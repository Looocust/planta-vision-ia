"""Capa de aplicación: sesiones, cargas, chat (grafo) y decisiones de aprobación."""
from __future__ import annotations

import hashlib
import io
import sqlite3
import threading
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from services.agent import approvals as approvals_mod
from services.agent.audit import audit
from services.agent.config import checkpoint_db_path, llm_model, policy_cfg, real_now, sim_now, uploads_dir
from services.agent.graph import Deps, make_graph, new_turn_input
from services.agent.llm import estimate_cost
from services.agent.plant.db import execute, init_db, rows
from services.agent.rbac import AuthError, authenticate
from services.agent.vision_client import VisionClient


class UploadError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code, self.message, self.http_status = code, message, http_status


class AgentService:
    def __init__(self, model, con: sqlite3.Connection | None = None, vision: VisionClient | None = None,
                 checkpointer=None, model_name: str | None = None, upload_root: Path | None = None,
                 fallbacks: list | None = None):
        self.con = con or init_db()
        self.vision = vision or VisionClient()
        self.model_name = model_name or llm_model()
        if checkpointer is None:
            from langgraph.checkpoint.sqlite import SqliteSaver
            p = checkpoint_db_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            checkpointer = SqliteSaver(sqlite3.connect(p, check_same_thread=False))
        self.checkpointer = checkpointer
        self.deps = Deps(model=model, con=self.con, vision=self.vision, model_name=self.model_name,
                         fallbacks=fallbacks or [])
        self.graph = make_graph(self.deps, checkpointer)
        self.upload_root = upload_root or uploads_dir()
        self._locks: dict[str, threading.Lock] = {}
        self._glock = threading.Lock()

    def _lock(self, sid: str) -> threading.Lock:
        with self._glock:
            return self._locks.setdefault(sid, threading.Lock())

    # ------------------------------------------------------------------ uploads
    def upload(self, data: bytes, filename: str, plant_id: str, line_id: str, user_id: str, role: str) -> dict:
        authenticate(user_id, role, plant_id)
        self._check_line(plant_id, line_id)
        pol = policy_cfg()["uploads"]
        if len(data) > pol["max_bytes"]:
            raise UploadError("too_large", "Archivo > 5 MB", 413)
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(data))
            img.verify()
            fmt = (img.format or "").upper()
        except Exception:
            raise UploadError("invalid_image", "El archivo no es una imagen válida", 400)
        if fmt not in ("PNG", "JPEG"):
            raise UploadError("unsupported_type", f"Formato {fmt} no soportado (PNG/JPG)", 415)
        image_id = "img_" + uuid.uuid4().hex[:12]
        d = self.upload_root / plant_id / line_id
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{image_id}.{ 'png' if fmt == 'PNG' else 'jpg'}"
        path.write_bytes(data)
        now = real_now()  # reloj de seguridad: la retención se mide en tiempo real
        execute(self.con, "INSERT INTO uploads VALUES(?,?,?,?,?,?,?,?,?)",
                (image_id, plant_id, line_id, user_id, str(path), hashlib.sha256(data).hexdigest(), len(data),
                 now.isoformat(), (now + timedelta(days=pol["retention_days"])).isoformat()))
        return {"image_id": image_id}

    def _check_line(self, plant_id: str, line_id: str) -> None:
        r = rows(self.con, "SELECT plant_id FROM lines WHERE line_id=?", (line_id,))
        if not r or r[0]["plant_id"] != plant_id:
            raise AuthError(f"La línea {line_id} no pertenece a la planta {plant_id}")

    # ------------------------------------------------------------------ chat
    def _config(self, session_id, plant_id, line_id, user_id, role) -> dict:
        return {"configurable": {"thread_id": session_id, "plant_id": plant_id, "line_id": line_id,
                                 "user_id": user_id, "role": role}, "recursion_limit": 60}

    def chat(self, session_id: str, plant_id: str, line_id: str, user_id: str, role: str, message: str,
             image_ids: list[str] | None = None) -> dict:
        authenticate(user_id, role, plant_id)
        self._check_line(plant_id, line_id)
        # aislamiento de sesión: una sesión pertenece a un único usuario/planta/línea
        sid = f"{plant_id}:{line_id}:{user_id}:{session_id}"
        cfg = self._config(sid, plant_id, line_id, user_id, role)
        with self._lock(sid):
            st = self.graph.get_state(cfg)
            if st and st.next:  # había una aprobación pendiente: se cierra la espera sin decidir
                self.graph.invoke(Command(resume={"status": "still_pending"}), cfg)
            prev = (self.graph.get_state(cfg).values or {}).get("image_ids") or []
            # solo imágenes de esta planta/línea (validadas contra el registro de cargas)
            valid = [i for i in (image_ids or []) if rows(self.con, "SELECT 1 FROM uploads WHERE image_id=? AND "
                                                          "plant_id=? AND line_id=? AND expires_at>?",
                                                          (i, plant_id, line_id, real_now().isoformat()))]
            imgs = list(dict.fromkeys(prev + valid))[-20:]
            text = message[:4000]
            if image_ids:
                text += f"\n[Imágenes adjuntas en este mensaje: {', '.join(valid) or 'ninguna válida'}]"
            out = self.graph.invoke(new_turn_input(text, imgs), cfg)
        return self._response(out, cfg)

    def _response(self, out: dict, cfg: dict) -> dict:
        msgs = out.get("messages", [])
        last_human = max((i for i, m in enumerate(msgs) if isinstance(m, HumanMessage)), default=-1)
        turn_ai = [m for m in msgs[last_human + 1:] if isinstance(m, AIMessage)]
        reply = ""
        for m in reversed(turn_ai):
            c = m.content if isinstance(m.content, str) else "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in m.content)
            if c.strip():
                reply = c
                break
        pending = None
        intr = out.get("__interrupt__")
        if intr:
            pending = intr[0].value if hasattr(intr[0], "value") else intr[0]
            note = (f"\n\n[Sistema] Solicitud {pending.get('approval_id')} ({pending.get('operation')}) PENDIENTE de "
                    f"aprobación por un rol autorizado ({', '.join(pending.get('approver_roles') or [])}) distinto del "
                    f"solicitante. No se ha ejecutado.")
            reply = (reply or "") + note
        tin, tout = out.get("tokens_in", 0), out.get("tokens_out", 0)
        by_model: dict[str, dict] = {}
        for u in out.get("llm_usage") or []:  # costo por modelo efectivamente usado (incluye respaldos)
            b = by_model.setdefault(u["model"], {"input_tokens": 0, "output_tokens": 0, "calls": 0})
            b["input_tokens"] += u["input_tokens"]
            b["output_tokens"] += u["output_tokens"]
            b["calls"] += 1
        for name, b in by_model.items():
            b["cost_usd"] = estimate_cost(name, b["input_tokens"], b["output_tokens"])
        labels = out.get("pending_labels") or []
        if labels:
            reply = (reply or "") + "".join(
                f"\n\n[Sistema] Etiqueta propuesta para {p_['case_id']}: {p_['proposed_label']} @ "
                f"{p_['proposed_angle_deg']}°. NO está en el dataset hasta que la confirmes en 'Etiquetas por confirmar'."
                for p_ in labels)
        return {"reply": reply or "(sin respuesta)", "tool_calls": out.get("tool_log", []),
                "pending_approval": pending, "pending_label_confirmations": labels,
                "stop_reason": out.get("stop_reason"),
                "usage": {"input_tokens": tin, "output_tokens": tout, "llm_calls": out.get("steps", 0),
                          "models_used": sorted(by_model), "by_model": by_model,
                          "cost_usd": round(sum(b["cost_usd"] for b in by_model.values()), 6)}}

    # ------------------------------------------------------------------ approvals
    def decide(self, approval_id: str, approver_id: str, role: str, decision: str, comment: str | None) -> dict:
        res = approvals_mod.decide(self.con, self.vision, approval_id, approver_id, role, decision, comment, sim_now(),
                                   real_now())
        if res.get("idempotent"):
            return res
        ap = rows(self.con, "SELECT * FROM approvals WHERE approval_id=?", (approval_id,))[0]
        sid = ap["session_id"]
        if sid and sid.count(":") >= 3:
            plant, line, user = sid.split(":")[:3]
            cfg = self._config(sid, plant, line, user, ap["requester_role"])
            with self._lock(sid):
                try:
                    st = self.graph.get_state(cfg)
                    if st and "await_approval" in (st.next or ()):
                        out = self.graph.invoke(Command(resume={**res, "approver_id": approver_id}), cfg)
                        msgs = out.get("messages", [])
                        res["session_message"] = msgs[-1].content if msgs else None
                    else:
                        self.graph.update_state(cfg, {"messages": [AIMessage(
                            content=f"[Sistema] La solicitud {approval_id} quedó en estado {res['status']} "
                                    f"(decidida por {approver_id}).")]})
                except Exception as e:  # noqa: BLE001 - la decisión ya quedó persistida y auditada
                    res["session_note"] = f"no se pudo notificar a la sesión: {type(e).__name__}"
        return res

    def pending_approvals(self, plant_id: str) -> list[dict]:
        return rows(self.con, "SELECT approval_id, line_id, operation, params, justification, requested_by, "
                              "requester_role, status, created_at, expires_at FROM approvals WHERE plant_id=? AND "
                              "status='pending' ORDER BY created_at", (plant_id,))

    def log_auth_failure(self, user_id, role, plant_id, line_id, detail):
        audit(self.con, sim_now(), session_id=None, request_id=None, user_id=user_id, role=role, plant_id=plant_id,
              line_id=line_id, action="auth_denied", tool=None, args={}, outcome="denied", detail=detail)

    # ------------------------------------------------------------------ etiquetas humanas
    def pending_labels(self, plant_id: str) -> list[dict]:
        return rows(self.con, "SELECT case_id, line_id, image_ref, image_id, predicted_label, predicted_confidence, "
                              "proposed_label, proposed_angle, proposed_by, proposed_at FROM review_queue WHERE "
                              "plant_id=? AND status='label_proposed' ORDER BY proposed_at", (plant_id,))

    def confirm_label(self, user_id: str, role: str, plants: tuple[str, ...], case_id: str, decision: str,
                      label: str | None, angle_deg: float | None) -> dict:
        from services.agent import review
        return review.confirm(self.con, user_id, role, plants, case_id, decision, label, angle_deg, real_now())

    # ------------------------------------------------------------------ retención
    def purge_expired(self) -> dict:
        from services.agent import review
        return review.purge_expired(self.con, real_now())
