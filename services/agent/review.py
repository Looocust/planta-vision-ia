"""Confirmación humana de etiquetas (fuera del LLM) y purga por retención.

El LLM solo puede PROPONER una etiqueta (submit_review_label -> status 'label_proposed'). Un humano con identidad
verificada (token) y rol de etiquetado confirma o rechaza vía POST /v1/review-labels/{case_id}, reenviando la
etiqueta y el ángulo que vio; solo entonces, en UNA transacción, el caso pasa a 'labeled' y se inserta en
retraining_dataset (case_id UNIQUE).
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from services.agent import rbac
from services.agent.audit import audit
from services.agent.plant.db import _lock, rows


class ReviewError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code, self.message, self.http_status = code, message, http_status


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S")


def confirm(con: sqlite3.Connection, user_id: str, role: str, plants: tuple[str, ...], case_id: str, decision: str,
            label: str | None, angle_deg: float | None, now: datetime) -> dict:
    if not rbac.tool_allowed(role, "submit_review_label"):
        raise ReviewError("forbidden_role", f"El rol '{role}' no puede confirmar etiquetas", 403)
    with _lock:
        c = rows(con, "SELECT * FROM review_queue WHERE case_id=?", (case_id,))
        if not c or c[0]["plant_id"] not in plants:
            raise ReviewError("not_found", "Caso inexistente o fuera de tus plantas", 404)
        c = c[0]
        base = dict(session_id=None, request_id=case_id, user_id=user_id, role=role, plant_id=c["plant_id"],
                    line_id=c["line_id"])
        if c["status"] == "labeled":
            return {"case_id": case_id, "status": "labeled", "idempotent": True}
        if c["status"] != "label_proposed":
            raise ReviewError("no_proposal", "El caso no tiene una etiqueta propuesta pendiente", 409)
        if decision == "reject":
            con.execute("UPDATE review_queue SET status='pending', proposed_label=NULL, proposed_angle=NULL, "
                        "proposed_by=NULL, proposed_at=NULL WHERE case_id=?", (case_id,))
            con.commit()
            audit(con, now, action="label_rejected", tool="submit_review_label", args={"case_id": case_id},
                  outcome="rejected", **base)
            return {"case_id": case_id, "status": "pending", "message": "Propuesta descartada; el caso sigue en cola."}
        if decision != "confirm":
            raise ReviewError("invalid_decision", "decision debe ser confirm o reject")
        if label != c["proposed_label"] or angle_deg is None or abs(float(angle_deg) - c["proposed_angle"]) > 1e-6:
            raise ReviewError("mismatch", "La etiqueta/ángulo confirmados no coinciden con la propuesta mostrada", 409)
        sid = f"DS-{uuid.uuid4().hex[:8].upper()}"
        try:  # una sola transacción: caso etiquetado + muestra de reentrenamiento
            con.execute("UPDATE review_queue SET status='labeled', labeled_by=?, label=?, angle_deg=?, labeled_at=? "
                        "WHERE case_id=? AND status='label_proposed'",
                        (user_id, c["proposed_label"], c["proposed_angle"], _iso(now), case_id))
            con.execute("INSERT INTO retraining_dataset VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (sid, case_id, c["plant_id"], c["line_id"], c["image_ref"] or c["image_id"],
                         c["proposed_label"], c["proposed_angle"], "human_review_confirmed", user_id, _iso(now)))
            con.commit()
        except Exception:
            con.rollback()
            raise
    audit(con, now, action="label_confirmed", tool="submit_review_label",
          args={"case_id": case_id, "label": label, "angle_deg": angle_deg}, outcome="labeled", **base)
    return {"case_id": case_id, "status": "labeled", "retraining_sample_id": sid, "label": c["proposed_label"],
            "angle_deg": c["proposed_angle"], "confirmed_by": user_id}


def purge_expired(con: sqlite3.Connection, now: datetime) -> dict:
    """Retención (reloj real): borra cargas expiradas (archivo + fila), memoria vencida y expira aprobaciones."""
    t = _iso(now)
    with _lock:
        ups = rows(con, "SELECT image_id, path FROM uploads WHERE expires_at<=?", (t,))
        for u in ups:
            try:
                Path(u["path"]).unlink(missing_ok=True)
            except OSError:
                pass
        con.execute("DELETE FROM uploads WHERE expires_at<=?", (t,))
        mem = con.execute("DELETE FROM user_memory WHERE expires_at<=?", (t,)).rowcount
        apr = con.execute("UPDATE approvals SET status='expired', idempotency_key=idempotency_key||':'||approval_id "
                          "WHERE status='pending' AND expires_at<=?", (t,)).rowcount
        con.commit()
    return {"uploads_deleted": len(ups), "memory_deleted": mem, "approvals_expired": apr}


if __name__ == "__main__":
    from services.agent.plant.db import init_db
    print(purge_expired(init_db(), datetime.now()))
