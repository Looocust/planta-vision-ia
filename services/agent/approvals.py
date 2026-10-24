"""Ejecutor DETERMINISTA de operaciones riesgosas (fuera del LLM).

Flujo: el agente solo crea la solicitud (request_risky_operation -> approvals.status='pending').
Un humano distinto, con rol aprobador y de la misma planta, decide vía POST /v1/approvals/{id}.
Aquí se re-validan TODAS las precondiciones (rol, planta, no-autoaprobación, expiración, estado de línea,
límites) y solo entonces se ejecuta. Cada ejecución es idempotente (una aprobación => a lo sumo una ejecución).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from services.agent import rbac
from services.agent.audit import audit
from services.agent.config import policy_cfg, roles_cfg
from services.agent.context import ToolContext
from services.agent.plant.db import _lock, execute, rows
from services.agent.tools import ToolError, _iso, _uid, validate_risky
from services.agent.vision_client import VisionClient, VisionUnavailable


class ApprovalError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code, self.message, self.http_status = code, message, http_status


def decide(con: sqlite3.Connection, vision: VisionClient, approval_id: str, approver_id: str, role: str,
           decision: str, comment: str | None, now: datetime) -> dict:
    with _lock:  # serializa decisiones concurrentes sobre la misma aprobación
        ap = rows(con, "SELECT * FROM approvals WHERE approval_id=?", (approval_id,))
        if not ap:
            raise ApprovalError("not_found", f"Aprobación {approval_id} no existe", 404)
        ap = ap[0]
        base = dict(session_id=ap["session_id"], request_id=approval_id, user_id=approver_id, role=role,
                    plant_id=ap["plant_id"], line_id=ap["line_id"])
        try:
            _check_approver(ap, approver_id, role, decision)
        except ApprovalError as e:
            audit(con, now, action="approval_denied", tool=ap["operation"], args={"decision": decision},
                  outcome=e.code, detail=e.message, **base)
            raise
        if ap["status"] != "pending":  # idempotencia: no se re-ejecuta
            return {"approval_id": approval_id, "status": ap["status"], "idempotent": True,
                    "result": json.loads(ap["result"] or "null")}
        if ap["expires_at"] and ap["expires_at"] < _iso(now):
            execute(con, "UPDATE approvals SET status='expired', idempotency_key=idempotency_key||':'||approval_id "
                         "WHERE approval_id=?", (approval_id,))
            audit(con, now, action="approval_expired", tool=ap["operation"], args={}, outcome="expired", **base)
            raise ApprovalError("expired", "La solicitud expiró; debe crearse de nuevo.", 409)
        if decision == "reject":
            execute(con, "UPDATE approvals SET status='rejected', approver_id=?, approver_role=?, decided_at=?, "
                         "comment=?, idempotency_key=idempotency_key||':'||approval_id WHERE approval_id=?",
                    (approver_id, role, _iso(now), comment, approval_id))
            audit(con, now, action="approval_rejected", tool=ap["operation"], args=json.loads(ap["params"]),
                  outcome="rejected", detail=comment, **base)
            return {"approval_id": approval_id, "status": "rejected", "operation": ap["operation"],
                    "message": "Operación rechazada por el aprobador. No se ejecutó nada."}
        # aprobar => re-validar precondiciones y ejecutar
        params = json.loads(ap["params"])
        ctx = ToolContext(session_id=ap["session_id"], plant_id=ap["plant_id"], line_id=ap["line_id"],
                          user_id=ap["requested_by"], role=ap["requester_role"], request_id=approval_id, con=con,
                          vision=vision, now=now)
        try:
            validate_risky(ctx, ap["operation"], ap["line_id"], params)
            result = _execute(ctx, ap["operation"], ap["line_id"], params, approval_id)
            status = "executed"
        except ToolError as e:
            result, status = {"error": e.code, "message": e.message}, "failed"
        except VisionUnavailable as e:
            result, status = {"error": "vision_unavailable", "message": f"Servicio de visión no disponible ({e.code})"}, "failed"
        execute(con, "UPDATE approvals SET status=?, approver_id=?, approver_role=?, decided_at=?, comment=?, result=?, "
                     "idempotency_key=idempotency_key||':'||approval_id WHERE approval_id=?",
                (status, approver_id, role, _iso(now), comment, json.dumps(result), approval_id))
        audit(con, now, action=f"approval_{status}", tool=ap["operation"], args=params, outcome=status,
              detail=json.dumps(result)[:500], **base)
        return {"approval_id": approval_id, "status": status, "operation": ap["operation"], "params": params,
                "result": result}


def _check_approver(ap: dict, approver_id: str, role: str, decision: str) -> None:
    if decision not in ("approve", "reject"):
        raise ApprovalError("invalid_decision", "decision debe ser approve o reject")
    u = roles_cfg()["users"].get(approver_id)
    if u is None or u["role"] != role:
        raise ApprovalError("unauthenticated", "Aprobador desconocido o rol no coincide con el registrado", 403)
    if policy_cfg()["approvals"]["same_plant_required"] and ap["plant_id"] not in u["plants"]:
        raise ApprovalError("forbidden_plant", "El aprobador no pertenece a la planta de la solicitud", 403)
    if policy_cfg()["approvals"]["approver_must_differ_from_requester"] and approver_id == ap["requested_by"]:
        raise ApprovalError("self_approval", "El solicitante no puede aprobar su propia solicitud", 403)
    if not rbac.can_approve(role, ap["operation"]):
        raise ApprovalError("forbidden_role", f"El rol '{role}' no puede aprobar '{ap['operation']}'", 403)


def _execute(ctx: ToolContext, op: str, line: str, params: dict, approval_id: str) -> dict:
    if op == "pause_line":
        execute(ctx.con, "UPDATE lines SET state='paused', updated_at=? WHERE line_id=?", (_iso(ctx.now), line))
        return {"line_id": line, "state": "paused"}
    if op == "resume_line":
        execute(ctx.con, "UPDATE lines SET state='running', updated_at=? WHERE line_id=?", (_iso(ctx.now), line))
        return {"line_id": line, "state": "running"}
    if op == "set_threshold":
        r = ctx.vision.set_thresholds(params)
        if "error" in r:
            raise ToolError("vision_error", f"El servicio de visión rechazó el cambio: {r['error']}")
        sets = ", ".join(f"{k}=?" for k in params)
        execute(ctx.con, f"UPDATE lines SET {sets}, updated_at=? WHERE line_id=?",
                (*params.values(), _iso(ctx.now), line))
        return {"line_id": line, "applied": params, "service_thresholds": r}
    if op == "manual_angle":
        cmd = _uid("CMD")
        execute(ctx.con, "INSERT OR IGNORE INTO robot_commands VALUES(?,?,?,?,?,?)",
                (cmd, line, approval_id, "PICK_MANUAL", json.dumps(params), _iso(ctx.now)))
        return {"line_id": line, "command_id": cmd, "action": "PICK_MANUAL", **params}
    if op == "promote_model":
        v = params["candidate_version"]
        r = ctx.vision.promote(v)
        if "error" in r:
            raise ToolError("vision_error", f"El servicio de visión rechazó la promoción: {r['error']} {r.get('detail')}")
        execute(ctx.con, "UPDATE model_registry SET status='archived' WHERE status='production'")
        execute(ctx.con, "UPDATE model_registry SET status='production', deployed_at=? WHERE version=?",
                (_iso(ctx.now), v))
        execute(ctx.con, "INSERT INTO model_deployments VALUES(?,?,?,?,?)", (line, v, _iso(ctx.now), "production",
                                                                            ctx.user_id))
        return {"promoted": v, "service": r}
    raise ToolError("unknown_operation", op)
