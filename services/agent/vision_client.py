"""Cliente HTTP del servicio de visión (contrato docs/CONTRACTS.md).

Robustez: timeout por llamada, reintentos con backoff exponencial SOLO en 5xx/timeout/conexión,
circuit breaker (abre tras N fallos consecutivos, fail-fast durante `reset_s`) y degradación elegante:
nunca lanza al agente; devuelve un error estructurado. Las decisiones ACT/REVIEW/REJECT vienen del
servicio de visión; el agente no las recalcula.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

import httpx

from services.agent.config import agent_cfg, vision_admin_token, vision_base_url


class VisionUnavailable(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


class CircuitBreaker:
    def __init__(self, threshold: int, reset_s: float):
        self.threshold, self.reset_s = threshold, reset_s
        self.failures, self.opened_at = 0, None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if time.monotonic() - self.opened_at >= self.reset_s else "open"

    def allow(self) -> bool:
        return self.state != "open"

    def success(self) -> None:
        with self._lock:
            self.failures, self.opened_at = 0, None

    def failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.threshold or self.state == "half_open":
                self.opened_at = time.monotonic()


class VisionClient:
    def __init__(self, base_url: str | None = None, transport: httpx.BaseTransport | None = None,
                 timeout_s: float | None = None, retries: int | None = None, backoff_s: float | None = None):
        c = agent_cfg()["vision"]
        self.base_url = base_url or vision_base_url()
        self.timeout_s = timeout_s if timeout_s is not None else c["timeout_s"]
        self.retries = retries if retries is not None else c["retries"]
        self.backoff_s = backoff_s if backoff_s is not None else c["backoff_s"]
        self.breaker = CircuitBreaker(c["circuit_failure_threshold"], c["circuit_reset_s"])
        self.http = httpx.Client(base_url=self.base_url, timeout=self.timeout_s, transport=transport)
        self.fault: str | None = None  # inyección de fallas para demo/eval (cabecera X-Fault)

    # --- núcleo con reintentos + breaker
    def _request(self, method: str, url: str, admin: bool = False, **kw) -> httpx.Response:
        if not self.breaker.allow():
            raise VisionUnavailable("circuit_open", "servicio de visión con fallas repetidas; circuito abierto")
        headers = kw.pop("headers", {}) or {}
        if self.fault and url.endswith("/predict"):
            headers["X-Fault"] = self.fault
        if admin:
            tok = vision_admin_token()
            if tok:
                headers["X-Admin-Token"] = tok
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                r = self.http.request(method, url, headers=headers, **kw)
                if r.status_code >= 500:
                    last = VisionUnavailable(f"http_{r.status_code}", r.text[:200])
                else:
                    self.breaker.success()
                    return r
            except httpx.TimeoutException as e:
                last = VisionUnavailable("timeout", str(e)[:200] or "timeout")
            except httpx.TransportError as e:
                last = VisionUnavailable("connection_error", str(e)[:200])
            self.breaker.failure()
            if not self.breaker.allow():
                break
            if attempt < self.retries:
                time.sleep(self.backoff_s * (2 ** attempt))
        assert last is not None
        raise last if isinstance(last, VisionUnavailable) else VisionUnavailable("error", str(last))

    @staticmethod
    def _json_or_error(r: httpx.Response) -> dict[str, Any]:
        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:
                detail = r.text[:200]
            return {"error": f"http_{r.status_code}", "detail": detail}
        return r.json()

    # --- endpoints
    def health(self) -> dict:
        return self._json_or_error(self._request("GET", "/health"))

    def model_info(self) -> dict:
        return self._json_or_error(self._request("GET", "/v1/model"))

    def predict(self, image_bytes: bytes, filename: str, top_k: int = 5, station_id: str | None = None) -> dict:
        data = {"top_k": str(top_k)}
        if station_id:
            data["station_id"] = station_id
        files = {"file": (filename, image_bytes, "image/png")}
        return self._json_or_error(self._request("POST", "/v1/predict", files=files, data=data))

    def set_thresholds(self, values: dict) -> dict:
        return self._json_or_error(self._request("PUT", "/v1/admin/thresholds", admin=True, json=values))

    def stage_sku(self, sku: str, images: list[tuple[str, bytes]], angles: list[float]) -> dict:
        files = [("files", (name, b, "image/png")) for name, b in images]
        data = {"sku": sku, "angles": json.dumps(angles)}
        return self._json_or_error(self._request("POST", "/v1/skus/stage", admin=True, files=files, data=data,
                                                 timeout=max(self.timeout_s, 60)))

    def promote(self, candidate_version: str) -> dict:
        return self._json_or_error(self._request("POST", "/v1/admin/promote", admin=True,
                                                 json={"candidate_version": candidate_version}))

    def image_url(self, image_id: str) -> str:
        return f"{self.base_url}/v1/images/{image_id}"
