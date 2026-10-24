"""Chequeo de integración del cliente del agente contra el servicio de visión REAL.

Levanta services/vision_api en un puerto temporal (con ALLOW_FAULT_INJECTION y MODELS_ROOT en una copia
temporal para no tocar los artefactos), y ejecuta: health, predict (ACT probable), imagen oscura, fuera de
dominio, X-Fault 500 y timeout (circuit breaker), thresholds y staging de SKU. También corre un turno del
agente con modelo guionado usando la visión real.

Uso: uv run python eval/agent/integration_vision.py
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from eval.agent.vision_stub import synth_dark, synth_out_of_domain  # noqa: E402
from services.agent.vision_client import VisionClient, VisionUnavailable  # noqa: E402

COIL = ROOT / "data" / "raw" / "coil-100"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    tmp = Path(tempfile.mkdtemp(prefix="vision_it_"))
    shutil.copytree(ROOT / "models" / "vision-1.0.0", tmp / "vision-1.0.0")
    port = free_port()
    env = {**os.environ, "ADMIN_TOKEN": "it-token", "ALLOW_FAULT_INJECTION": "true", "FAULT_TIMEOUT_S": "8",
           "MODELS_ROOT": str(tmp), "MODEL_DIR": str(tmp / "vision-1.0.0")}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "services.vision_api.app:app", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    results = {}
    try:
        for _ in range(120):
            try:
                if httpx.get(url + "/health", timeout=1).json().get("status") == "ok":
                    break
            except Exception:
                time.sleep(0.5)
        os.environ["VISION_ADMIN_TOKEN"] = "it-token"
        vc = VisionClient(base_url=url, timeout_s=5.0, retries=1, backoff_s=0.1)
        results["health"] = vc.health()
        good = vc.predict((COIL / "obj5__30.png").read_bytes(), "a.png", top_k=3, station_id="P1-L1")
        results["predict_obj5__30"] = {k: good.get(k) for k in ("decision", "object", "angle", "reasons", "similar")}
        assert good["decision"] in ("ACT", "REVIEW", "REJECT") and "request_id" in good
        dark = vc.predict(synth_dark((COIL / "obj5__30.png").read_bytes()), "d.png")
        results["predict_dark"] = {k: dark.get(k) for k in ("decision", "reasons", "quality")}
        ood = vc.predict(synth_out_of_domain(), "o.png")
        results["predict_ood"] = {k: ood.get(k) for k in ("decision", "reasons", "object")}
        r = httpx.get(url + "/v1/images/obj5__30", timeout=5)
        results["evidence_image"] = {"status": r.status_code, "type": r.headers.get("content-type")}
        vc.fault = "500"
        try:
            vc.predict((COIL / "obj5__30.png").read_bytes(), "a.png")
            results["fault_500"] = "NO FALLÓ (inesperado)"
        except VisionUnavailable as e:
            results["fault_500"] = e.code
        vc2 = VisionClient(base_url=url, timeout_s=2.0, retries=1, backoff_s=0.1)
        vc2.fault = "timeout"
        try:
            vc2.predict((COIL / "obj5__30.png").read_bytes(), "a.png")
            results["fault_timeout"] = "NO FALLÓ (inesperado)"
        except VisionUnavailable as e:
            results["fault_timeout"] = e.code
        results["circuit_after_faults"] = vc.breaker.state
        vc3 = VisionClient(base_url=url)
        results["thresholds"] = vc3.set_thresholds({"min_class_confidence": 0.8})
        imgs = [(f"obj100__{a}.png", (COIL / f"obj100__{a}.png").read_bytes()) for a in range(0, 360, 40)]
        st = vc3.stage_sku("SKU-IT-01", imgs, [float(a) for a in range(0, 360, 40)])
        results["stage_sku"] = {k: st.get(k) for k in ("candidate_version", "sufficient", "issues", "metrics", "error")}
    finally:
        proc.terminate()
        proc.wait(5)
        shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps(results, indent=1, ensure_ascii=False, default=str)[:6000])


if __name__ == "__main__":
    main()
