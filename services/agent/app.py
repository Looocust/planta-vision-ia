"""API HTTP del agente (puerto 8002) + UI mínima.

Ejecutar: uv run uvicorn services.agent.app:app --port 8002
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from services.agent.approvals import ApprovalError
from services.agent.config import llm_model, llm_provider
from services.agent.plant.db import rows
from services.agent.rbac import AuthError, authenticate
from services.agent.service import AgentService, UploadError

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Copiloto operativo de línea", version="1.0.0")


@lru_cache
def get_service() -> AgentService:
    from services.agent.llm import make_chat_model, make_fallback_models
    return AgentService(model=make_chat_model(), fallbacks=make_fallback_models())


class ChatIn(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    plant_id: str = Field(..., pattern=r"^P\d$")
    line_id: str = Field(..., pattern=r"^P\d-L\d$")
    user_id: str = Field(..., max_length=40)
    role: str
    message: str = Field(..., min_length=1, max_length=4000)
    image_ids: list[str] = Field(default_factory=list, max_length=10)


class ApprovalIn(BaseModel):
    approver_id: str
    role: str
    decision: str = Field(..., pattern=r"^(approve|reject)$")
    comment: str | None = Field(None, max_length=500)


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text()


@app.get("/health")
def health():
    return {"status": "ok", "llm_provider": llm_provider(), "llm_model": llm_model()}


@app.post("/v1/chat")
def chat(body: ChatIn):
    svc = get_service()
    try:
        return svc.chat(body.session_id, body.plant_id, body.line_id, body.user_id, body.role, body.message,
                        body.image_ids)
    except AuthError as e:
        svc.log_auth_failure(body.user_id, body.role, body.plant_id, body.line_id, str(e))
        raise HTTPException(403, str(e))


@app.post("/v1/uploads")
async def upload(file: UploadFile = File(...), plant_id: str = Form("P1"), line_id: str = Form("P1-L1"),
                 user_id: str = Form("u_sup_p1"), role: str = Form("supervisor")):
    svc = get_service()
    data = await file.read()
    try:
        return svc.upload(data, file.filename or "upload", plant_id, line_id, user_id, role)
    except AuthError as e:
        raise HTTPException(403, str(e))
    except UploadError as e:
        raise HTTPException(e.http_status, e.message)


@app.get("/v1/uploads/{image_id}")
def get_upload(image_id: str, plant_id: str, user_id: str, role: str):
    svc = get_service()
    try:
        authenticate(user_id, role, plant_id)
    except AuthError as e:
        raise HTTPException(403, str(e))
    r = rows(svc.con, "SELECT path FROM uploads WHERE image_id=? AND plant_id=?", (image_id, plant_id))
    if not r:
        raise HTTPException(404, "no encontrada")
    return FileResponse(r[0]["path"])


@app.get("/v1/approvals")
def list_approvals(plant_id: str, user_id: str, role: str):
    svc = get_service()
    try:
        authenticate(user_id, role, plant_id)
    except AuthError as e:
        raise HTTPException(403, str(e))
    return {"pending": svc.pending_approvals(plant_id)}


@app.post("/v1/approvals/{approval_id}")
def decide(approval_id: str, body: ApprovalIn):
    svc = get_service()
    try:
        return svc.decide(approval_id, body.approver_id, body.role, body.decision, body.comment)
    except ApprovalError as e:
        raise HTTPException(e.http_status, {"code": e.code, "message": e.message})
