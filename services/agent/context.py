"""Contexto de ejecución inyectado desde la SESIÓN (nunca desde argumentos del LLM)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from services.agent.vision_client import VisionClient


@dataclass
class ToolContext:
    session_id: str
    plant_id: str
    line_id: str
    user_id: str
    role: str
    request_id: str
    con: sqlite3.Connection
    vision: VisionClient
    now: datetime
    session_image_ids: list[str] = field(default_factory=list)
    real_now: datetime | None = None  # reloj de seguridad (expiraciones); now = reloj simulado de planta

    @property
    def sec_now(self) -> datetime:
        return self.real_now or datetime.now().replace(microsecond=0)
