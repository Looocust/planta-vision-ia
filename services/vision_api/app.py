"""Servicio de inferencia de visión (FastAPI, puerto 8001).

Ejecutar:  uv run uvicorn services.vision_api.app:app --port 8001
Config por entorno: MODEL_DIR, MODELS_ROOT, ADMIN_TOKEN, ALLOW_FAULT_INJECTION, DATA_DIR, MAX_UPLOAD_MB.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hmac
import json
import logging
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.vision.inference import IMAGE_ID_RE, VisionPredictor, decode_image, promote_candidate  # noqa: E402

ALLOWED_TYPES = {"image/png", "image/jpeg", "image/jpg"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", protected_namespaces=())
    model_dir: str = str(ROOT / "models" / "vision-1.0.1")
    models_root: str = str(ROOT / "models")
    admin_token: str = ""
    allow_fault_injection: bool = False
    data_dir: str = str(ROOT / "data" / "raw" / "coil-100")
    max_upload_mb: float = 5.0
    fault_timeout_s: float = 30.0


settings = Settings()


# ---------------------------------------------------------------- logging JSON estructurado
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)), "level": record.levelname,
             "logger": record.name, "msg": record.getMessage()}
        d.update(getattr(record, "extra_fields", {}))
        return json.dumps(d, ensure_ascii=False)


logger = logging.getLogger("vision_api")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def log(msg: str, **fields):
    logger.info(msg, extra={"extra_fields": fields})


# ---------------------------------------------------------------- estado del modelo (swap thread-safe)
class ModelHolder:
    def __init__(self):
        self._lock = threading.RLock()
        self._predictor: VisionPredictor | None = None
        self.error: str | None = None

    def load(self, model_dir: str):
        try:
            p = VisionPredictor.load(model_dir)
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            log("model_load_failed", model_dir=model_dir, error=str(e))
            return
        self.swap(p)

    def swap(self, p: VisionPredictor):
        with self._lock:
            self._predictor = p
            self.error = None
        log("model_active", model_version=p.version)

    def get(self) -> VisionPredictor:
        with self._lock:
            p = self._predictor
        if p is None:
            raise HTTPException(503, detail="modelo no cargado")
        return p


holder = ModelHolder()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    holder.load(settings.model_dir)
    yield


app = FastAPI(title="Vision API — COIL-100", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = rid
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as e:  # noqa: BLE001
        log("unhandled_error", request_id=rid, path=request.url.path, error=str(e))
        response = JSONResponse({"detail": "error interno", "request_id": rid}, status_code=500)
    response.headers["X-Request-ID"] = rid
    log("request", request_id=rid, method=request.method, path=request.url.path, status=response.status_code,
        latency_ms=round((time.perf_counter() - t0) * 1000, 2))
    return response


def _require_admin(token: str | None):
    if not settings.admin_token or not token or not hmac.compare_digest(token, settings.admin_token):
        raise HTTPException(401, detail="token de administración inválido")


async def _read_upload(f: UploadFile) -> bytes:
    if (f.content_type or "").lower() not in ALLOWED_TYPES:
        raise HTTPException(415, detail=f"tipo no soportado: {f.content_type}")
    limit = int(settings.max_upload_mb * 1024 * 1024)
    data = await f.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, detail=f"archivo > {settings.max_upload_mb:g} MB")
    if not data:
        raise HTTPException(400, detail="archivo vacío")
    return data


# ---------------------------------------------------------------- endpoints
@app.get("/health")
def health():
    p = holder._predictor
    return {"status": "ok" if p else "degraded", "model_version": p.version if p else None}


@app.get("/v1/model")
def model_info():
    p = holder.get()
    m = p.metadata
    keys = ("version", "stage", "created_at", "promoted_at", "parent_version", "data_version_hash", "dataset",
            "model_family", "classifier", "thresholds", "threshold_validation", "quality_thresholds",
            "angle_tolerance_deg", "symmetric_classes", "metrics_summary", "artifact_size_mb", "classes")
    out = {k: m.get(k) for k in keys if k in m}
    out["thresholds"] = p.thresholds.to_dict()
    out["n_classes"] = len(m.get("classes", []))
    return out


@app.post("/v1/predict")
async def predict(request: Request, file: UploadFile = File(...), top_k: int = Form(5),
                  station_id: str | None = Form(None), x_fault: str | None = Header(None)):
    rid = request.state.request_id
    if x_fault and settings.allow_fault_injection:
        log("fault_injected", request_id=rid, fault=x_fault)
        if x_fault == "500":
            raise HTTPException(500, detail="falla inyectada (X-Fault: 500)")
        if x_fault == "timeout":
            await asyncio.sleep(settings.fault_timeout_s)
    if not 1 <= top_k <= 20:
        raise HTTPException(422, detail="top_k debe estar en [1, 20]")
    data = await _read_upload(file)
    p = holder.get()
    try:
        img = decode_image(data)
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from e
    t0 = time.perf_counter()
    resp = await asyncio.to_thread(p.predict, img, top_k, rid)
    resp["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    if station_id:
        resp["station_id"] = station_id
    log("prediction", request_id=rid, station_id=station_id, model_version=resp["model_version"],
        decision=resp["decision"], label=resp["object"]["label"], confidence=resp["object"]["confidence"],
        angle=resp["angle"]["degrees"], reasons=resp["reasons"], latency_ms=resp["latency_ms"])
    return resp


@app.get("/v1/images/{image_id}")
def get_image(image_id: str):
    if not IMAGE_ID_RE.fullmatch(image_id):
        raise HTTPException(400, detail="image_id inválido")
    candidates = [Path(settings.data_dir) / f"{image_id}.png"]
    p = holder._predictor
    if p is not None and p.model_dir is not None:
        candidates.append(p.model_dir / "refs" / f"{image_id}.png")
    for c in candidates:
        if c.is_file():
            return FileResponse(c, media_type="image/png")
    raise HTTPException(404, detail="imagen no encontrada")


class ThresholdsIn(BaseModel):
    min_class_confidence: float | None = None
    max_angle_uncertainty_deg: float | None = None
    unknown_distance: float | None = None


@app.put("/v1/admin/thresholds")
def put_thresholds(body: ThresholdsIn, request: Request, x_admin_token: str | None = Header(None)):
    _require_admin(x_admin_token)
    p = holder.get()
    vals = body.model_dump()
    with holder._lock:
        try:
            new = p.set_thresholds(**vals)  # valida todo antes de mutar (atómico)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, detail=str(e)) from e
    log("thresholds_updated", request_id=request.state.request_id, thresholds=new)
    return new


@app.post("/v1/skus/stage")
async def stage_sku(request: Request, sku: str = Form(...), angles: str = Form(...),
                    files: list[UploadFile] = File(...), x_admin_token: str | None = Header(None)):
    _require_admin(x_admin_token)
    try:
        ang = json.loads(angles)
        assert isinstance(ang, list) and all(isinstance(a, (int, float)) for a in ang)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, detail="angles debe ser una lista JSON de números") from e
    if len(ang) != len(files):
        raise HTTPException(400, detail="files y angles deben tener la misma longitud")
    imgs = []
    for f in files:
        try:
            imgs.append(decode_image(await _read_upload(f)))
        except ValueError as e:
            raise HTTPException(400, detail=str(e)) from e
    p = holder.get()
    res = await asyncio.to_thread(p.stage_sku, sku, imgs, ang, Path(settings.models_root) / "staging")
    log("sku_staged", request_id=request.state.request_id, sku=sku, candidate=res["candidate_version"],
        sufficient=res["sufficient"], issues=res["issues"])
    res.pop("staging_dir", None)
    return res


class PromoteIn(BaseModel):
    candidate_version: str


@app.post("/v1/admin/promote")
def promote(body: PromoteIn, request: Request, x_admin_token: str | None = Header(None)):
    _require_admin(x_admin_token)
    try:
        meta = json.loads((Path(settings.models_root) / "staging" / body.candidate_version / "metadata.json").read_text())
    except (FileNotFoundError, OSError):
        raise HTTPException(404, detail="candidato no encontrado") from None
    staged = meta.get("staged_sku", {})
    if staged and not staged.get("sufficient", False):
        raise HTTPException(409, detail={"msg": "candidato insuficiente; no se promueve", "issues": staged.get("issues")})
    try:
        dst = promote_candidate(body.candidate_version, settings.models_root)
    except FileExistsError as e:
        raise HTTPException(409, detail=str(e)) from e
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(404, detail=str(e)) from e
    new = VisionPredictor.load(dst)
    previous = holder.get().version
    holder.swap(new)
    log("model_promoted", request_id=request.state.request_id, previous=previous, active=new.version)
    return {"active_version": new.version, "previous_version": previous}
