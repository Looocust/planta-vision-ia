"""Stub determinista del servicio de visión (contrato docs/CONTRACTS.md) para pruebas offline y para la
evaluación cuando el servicio real no está disponible. Se usa vía httpx.MockTransport (en proceso).

Comportamiento:
- calidad: brillo medio < 0.12 o > 0.92, o nitidez (var. Laplaciano) < 15 -> REJECT image_quality
- imagen registrada (sha256) con escenario: act | low_conf | ambiguous
- imagen no registrada y de buena calidad -> REJECT unknown_object (fuera de dominio)
- cabecera X-Fault: 500 -> HTTP 500 ; timeout -> httpx.ReadTimeout
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid
from pathlib import Path

import httpx
import numpy as np
from PIL import Image


def image_stats(data: bytes) -> tuple[float, float, int, int]:
    img = Image.open(io.BytesIO(data)).convert("L")
    a = np.asarray(img, dtype=np.float32)
    lap = (-4 * a[1:-1, 1:-1] + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:])
    return float(a.mean() / 255.0), float(lap.var()), img.width, img.height


class VisionStub:
    def __init__(self):
        self.registry: dict[str, tuple[str, float, str]] = {}
        self.thresholds = {"min_class_confidence": 0.8, "max_angle_uncertainty_deg": 15.0, "unknown_distance": 0.6}
        self.active = "vision-1.0.0"
        self.calls: list[str] = []

    def register(self, data: bytes, label: str, angle: float, scenario: str = "act") -> None:
        self.registry[hashlib.sha256(data).hexdigest()] = (label, angle, scenario)

    def register_file(self, path: str | Path, scenario: str = "act") -> bytes:
        p = Path(path)
        data = p.read_bytes()
        label, ang = p.stem.split("__")
        self.register(data, label, float(ang), scenario)
        return data

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        fault = request.headers.get("X-Fault")
        if path.endswith("/predict") and fault == "500":
            return httpx.Response(500, json={"detail": "fault injected"})
        if path.endswith("/predict") and fault == "timeout":
            raise httpx.ReadTimeout("fault injected timeout", request=request)
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "model_version": self.active})
        if path == "/v1/predict":
            return self._predict(request)
        if path == "/v1/admin/thresholds":
            self.thresholds.update(json.loads(request.content or b"{}"))
            return httpx.Response(200, json=self.thresholds)
        if path == "/v1/skus/stage":
            body = request.content.decode("latin-1")
            sku = body.split('name="sku"')[1].split("\r\n\r\n")[1].split("\r\n")[0]
            n = body.count('name="files"')
            return httpx.Response(200, json={"candidate_version": f"{self.active}+{sku}", "sku": sku, "n_views": n,
                                             "sufficient": n >= 12, "issues": [],
                                             "metrics": {"accuracy": 0.953, "new_sku_accuracy": 0.97,
                                                         "angle_mae_deg": 6.9}})
        if path == "/v1/admin/promote":
            v = json.loads(request.content)["candidate_version"]
            self.active = v
            return httpx.Response(200, json={"active_version": v})
        return httpx.Response(404, json={"detail": "not found"})

    def _predict(self, request: httpx.Request) -> httpx.Response:
        body = request.content
        # extraer el archivo del multipart (stub simple: buscar firma PNG/JPG)
        start = body.find(b"\x89PNG")
        if start < 0:
            start = body.find(b"\xff\xd8")
        if start < 0:
            return httpx.Response(400, json={"detail": "imagen inválida"})
        end = body.rfind(b"\r\n--")
        data = body[start:end]
        bright, sharp, w, h = image_stats(data)
        rid = str(uuid.uuid4())
        quality = {"ok": True, "issues": [], "brightness": round(bright, 3), "sharpness": round(sharp, 1),
                   "width": w, "height": h}
        if bright < 0.12:
            quality["issues"].append("too_dark")
        if bright > 0.92:
            quality["issues"].append("overexposed")
        if sharp < 15:
            quality["issues"].append("blurry")
        quality["ok"] = not quality["issues"]
        base = {"request_id": rid, "model_version": self.active, "quality": quality, "latency_ms": 3.1}
        if not quality["ok"]:
            return httpx.Response(200, json={**base, "object": {"label": None, "confidence": 0.0, "known": False},
                                             "angle": {"degrees": None, "uncertainty_deg": None, "ambiguous": False},
                                             "decision": "REJECT", "reasons": ["image_quality"],
                                             "robot_command": {"action": "HOLD", "reason": "image_quality"},
                                             "similar": []})
        reg = self.registry.get(hashlib.sha256(data).hexdigest())
        if not reg:
            return httpx.Response(200, json={**base, "object": {"label": None, "confidence": 0.12, "known": False},
                                             "angle": {"degrees": None, "uncertainty_deg": None, "ambiguous": False},
                                             "decision": "REJECT", "reasons": ["unknown_object"],
                                             "robot_command": {"action": "HOLD", "reason": "unknown_object"},
                                             "similar": [{"label": "obj7", "angle_deg": 0, "similarity": 0.31,
                                                          "image_id": "obj7__0"}]})
        label, ang, scen = reg
        sim = [{"label": label, "angle_deg": (ang + d) % 360, "similarity": s, "image_id": f"{label}__{int((ang + d) % 360)}"}
               for d, s in [(0, 0.97), (5, 0.93), (-5, 0.92)]]
        if scen == "act":
            return httpx.Response(200, json={**base, "object": {"label": label, "confidence": 0.96, "known": True},
                                             "angle": {"degrees": ang, "uncertainty_deg": 3.5, "ambiguous": False},
                                             "decision": "ACT", "reasons": [],
                                             "robot_command": {"action": "PICK", "object": label, "rotate_deg": -ang},
                                             "similar": sim})
        if scen == "ambiguous":
            return httpx.Response(200, json={**base, "object": {"label": label, "confidence": 0.9, "known": True},
                                             "angle": {"degrees": None, "uncertainty_deg": 90.0, "ambiguous": True},
                                             "decision": "REVIEW", "reasons": ["rotational_ambiguity"],
                                             "robot_command": {"action": "HOLD", "reason": "rotational_ambiguity"},
                                             "similar": sim})
        return httpx.Response(200, json={**base, "object": {"label": label, "confidence": 0.52, "known": True},
                                         "angle": {"degrees": ang, "uncertainty_deg": 22.0, "ambiguous": False},
                                         "decision": "REVIEW", "reasons": ["low_class_confidence", "high_angle_uncertainty"],
                                         "robot_command": {"action": "HOLD", "reason": "low_class_confidence"},
                                         "similar": [{**sim[0], "label": "obj3", "image_id": "obj3__90", "similarity": 0.81},
                                                     *sim[:2]]})


def synth_dark(src: bytes) -> bytes:
    img = Image.open(io.BytesIO(src)).convert("RGB")
    a = (np.asarray(img, dtype=np.float32) * 0.08).astype(np.uint8)
    b = io.BytesIO()
    Image.fromarray(a).save(b, format="PNG")
    return b.getvalue()


def synth_blurry(src: bytes) -> bytes:
    from PIL import ImageFilter
    img = Image.open(io.BytesIO(src)).convert("RGB").filter(ImageFilter.GaussianBlur(6))
    b = io.BytesIO()
    img.save(b, format="PNG")
    return b.getvalue()


def synth_out_of_domain(seed: int = 7) -> bytes:
    """Imagen fuera de dominio: patrón de tablero + ruido (no es un objeto COIL)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:128, 0:128]
    board = (((yy // 16) + (xx // 16)) % 2 * 200 + 30).astype(np.float32)
    a = np.clip(board[..., None] + rng.normal(0, 25, (128, 128, 3)), 0, 255).astype(np.uint8)
    b = io.BytesIO()
    Image.fromarray(a).save(b, format="PNG")
    return b.getvalue()
