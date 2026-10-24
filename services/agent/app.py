"""API HTTP del agente (puerto 8002) + UI mínima.

Ejecutar: uv run uvicorn services.agent.app:app --port 8002

Identidad: TODAS las rutas de negocio exigen `Authorization: Bearer <token>` (services/agent/auth.py). El
usuario, rol y plantas salen del token verificado, nunca del cuerpo. Si el cuerpo trae user_id/role (contrato
original) y no coinciden con el token, se rechaza con 403 (intento de suplantación).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from services.agent.approvals import ApprovalError
from services.agent.auth import AuthFailure, Identity, bearer, demo_login_enabled, issue_token, verify_token
from services.agent.config import llm_model, llm_provider, real_now
from services.agent.plant.db import rows
from services.agent.rbac import AuthError
from services.agent.review import ReviewError
from services.agent.service import AgentService, UploadError

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("agent")
_LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}


@asynccontextmanager
async def lifespan(_app):
    if demo_login_enabled():
        log.warning("DEMO_LOGIN=true: /v1/auth/demo-login emite tokens de CUALQUIER usuario del directorio a clientes "
                    "locales. Solo para demo; en producción debe estar en false y usarse OIDC (Entra ID).")
    yield


app = FastAPI(title="Copiloto operativo de línea", version="1.1.0", lifespan=lifespan)


@lru_cache
def get_service() -> AgentService:
    from services.agent.llm import make_chat_model, make_fallback_models
    svc = AgentService(model=make_chat_model(), fallbacks=make_fallback_models())
    svc.purge_expired()  # retención al arrancar
    return svc


def identity(authorization: str | None = Header(None)) -> Identity:
    try:
        return verify_token(bearer(authorization))
    except AuthFailure as e:
        raise HTTPException(e.status, str(e))


def _check_claims(ident: Identity, user_id: str | None, role: str | None, plant_id: str | None = None) -> None:
    if (user_id and user_id != ident.user_id) or (role and role != ident.role):
        get_service().log_auth_failure(user_id, role, plant_id, None,
                                       f"suplantación: token={ident.user_id}/{ident.role}")
        raise HTTPException(403, "user_id/role del cuerpo no coinciden con la identidad verificada")
    if plant_id and plant_id not in ident.plants:
        raise HTTPException(403, f"{ident.user_id} no está autorizado para la planta {plant_id}")


class DemoLoginIn(BaseModel):
    user_id: str = Field(..., max_length=40)


class ChatIn(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    plant_id: str = Field(..., pattern=r"^P\d$")
    line_id: str = Field(..., pattern=r"^P\d-L\d$")
    message: str = Field(..., min_length=1, max_length=4000)
    image_ids: list[str] = Field(default_factory=list, max_length=10)
    user_id: str | None = None  # opcional (contrato previo): debe coincidir con el token
    role: str | None = None


class ApprovalIn(BaseModel):
    decision: str = Field(..., pattern=r"^(approve|reject)$")
    comment: str | None = Field(None, max_length=500)
    approver_id: str | None = None  # opcional: debe coincidir con el token
    role: str | None = None


class LabelConfirmIn(BaseModel):
    decision: str = Field(..., pattern=r"^(confirm|reject)$")
    label: str | None = Field(None, pattern=r"^obj([1-9]|[1-9][0-9]|100)$")
    angle_deg: float | None = Field(None, ge=0, lt=360)


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text()


@app.get("/health")
def health():
    return {"status": "ok", "llm_provider": llm_provider(), "llm_model": llm_model(),
            "demo_login": demo_login_enabled()}


@app.post("/v1/auth/demo-login")
def demo_login(body: DemoLoginIn, request: Request):
    """SOLO DEMO (DEMO_LOGIN=true, por defecto false). Por diseño permite elegir CUALQUIER identidad del directorio
    simulado, por eso solo responde a clientes locales. En producción: OIDC con Entra ID; este endpoint no existe."""
    if not demo_login_enabled():
        raise HTTPException(404, "demo-login deshabilitado")
    host = request.client.host if request.client else ""
    if host not in _LOCAL_CLIENTS:
        raise HTTPException(403, "demo-login solo acepta clientes locales")
    try:
        return issue_token(body.user_id)
    except AuthFailure as e:
        raise HTTPException(e.status, str(e))


@app.post("/v1/chat")
def chat(body: ChatIn, ident: Identity = Depends(identity)):
    _check_claims(ident, body.user_id, body.role, body.plant_id)
    svc = get_service()
    try:
        return svc.chat(body.session_id, body.plant_id, body.line_id, ident.user_id, ident.role, body.message,
                        body.image_ids)
    except AuthError as e:
        svc.log_auth_failure(ident.user_id, ident.role, body.plant_id, body.line_id, str(e))
        raise HTTPException(403, str(e))


@app.post("/v1/uploads")
async def upload(file: UploadFile = File(...), plant_id: str = Form(...), line_id: str = Form(...),
                 ident: Identity = Depends(identity)):
    _check_claims(ident, None, None, plant_id)
    svc = get_service()
    data = await file.read()
    try:
        return svc.upload(data, file.filename or "upload", plant_id, line_id, ident.user_id, ident.role)
    except AuthError as e:
        raise HTTPException(403, str(e))
    except UploadError as e:
        raise HTTPException(e.http_status, e.message)


@app.get("/v1/uploads/{image_id}")
def get_upload(image_id: str, plant_id: str, ident: Identity = Depends(identity)):
    _check_claims(ident, None, None, plant_id)
    svc = get_service()
    r = rows(svc.con, "SELECT path FROM uploads WHERE image_id=? AND plant_id=? AND expires_at>?",
             (image_id, plant_id, real_now().isoformat()))
    if not r:
        raise HTTPException(404, "no encontrada o expirada")
    return FileResponse(r[0]["path"])


@app.get("/v1/approvals")
def list_approvals(plant_id: str, ident: Identity = Depends(identity)):
    _check_claims(ident, None, None, plant_id)
    return {"pending": get_service().pending_approvals(plant_id)}


@app.post("/v1/approvals/{approval_id}")
def decide(approval_id: str, body: ApprovalIn, ident: Identity = Depends(identity)):
    _check_claims(ident, body.approver_id, body.role)
    try:  # el aprobador es SIEMPRE la identidad verificada del token
        return get_service().decide(approval_id, ident.user_id, ident.role, body.decision, body.comment)
    except ApprovalError as e:
        raise HTTPException(e.http_status, {"code": e.code, "message": e.message})


@app.get("/v1/review-labels")
def list_labels(plant_id: str, ident: Identity = Depends(identity)):
    _check_claims(ident, None, None, plant_id)
    return {"pending": get_service().pending_labels(plant_id)}


@app.post("/v1/review-labels/{case_id}")
def confirm_label(case_id: str, body: LabelConfirmIn, ident: Identity = Depends(identity)):
    try:
        return get_service().confirm_label(ident.user_id, ident.role, ident.plants, case_id, body.decision,
                                           body.label, body.angle_deg)
    except ReviewError as e:
        raise HTTPException(e.http_status, {"code": e.code, "message": e.message})


@app.post("/v1/admin/purge")
def purge(ident: Identity = Depends(identity)):
    if ident.role != "plant_manager":
        raise HTTPException(403, "solo plant_manager")
    return get_service().purge_expired()
