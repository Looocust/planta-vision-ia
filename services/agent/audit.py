"""Auditoría append-only de toda acción (tool ejecutada, bloqueada, aprobación)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from services.agent.plant.db import execute
from services.agent.security import redact


def audit(con: sqlite3.Connection, now: datetime, *, session_id: str | None, request_id: str | None,
          user_id: str | None, role: str | None, plant_id: str | None, line_id: str | None, action: str,
          tool: str | None, args, outcome: str, detail: str | None = None) -> None:
    execute(con, "INSERT INTO audit_log(ts, session_id, request_id, user_id, role, plant_id, line_id, action, tool, "
                 "args, outcome, detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (now.strftime("%Y-%m-%dT%H:%M:%S"), session_id, request_id, user_id, role, plant_id, line_id, action,
             tool, redact(json.dumps(args, ensure_ascii=False, default=str))[:1000], outcome,
             redact(detail)[:1000] if detail else None))
