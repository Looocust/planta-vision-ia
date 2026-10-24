"""Chequeo de integración del cliente del agente contra el servicio de visión REAL.

Levanta services/vision_api en un puerto temporal (con ALLOW_FAULT_INJECTION y MODELS_ROOT en una copia
temporal para no tocar los artefactos), y ejecuta: health, predict (ACT probable), imagen oscura, fuera de
dominio, X-Fault 500 y timeout (circuit breaker), thresholds y staging de SKU. También corre un turno del
agente con modelo guionado usando la visión real.

Uso:
  uv run python eval/agent/integration_vision.py                         # instancia temporal (incluye mutaciones)
  uv run python eval/agent/integration_vision.py --live http://localhost:8001
      # SOLO LECTURA contra el servicio real en marcha (predict/health/images) + escenarios adversos a nivel AGENTE
      # (modelo guionado, sin LLM). Las fallas 5xx/timeout se prueban en una instancia temporal con
      # ALLOW_FAULT_INJECTION=true y copia de models/vision-1.0.1, porque el servicio de demo no la habilita.
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
    shutil.copytree(ROOT / "models" / "vision-1.0.1", tmp / "vision-1.0.1")
    port = free_port()
    env = {**os.environ, "ADMIN_TOKEN": "it-token", "ALLOW_FAULT_INJECTION": "true", "FAULT_TIMEOUT_S": "8",
           "MODELS_ROOT": str(tmp), "MODEL_DIR": str(tmp / "vision-1.0.1")}
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
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:  # una petición con X-Fault: timeout puede seguir durmiendo
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps(results, indent=1, ensure_ascii=False, default=str)[:6000])


def _agent_turn(vision_client, image_bytes: bytes, tmp: Path, extra_script=None) -> tuple[dict, object]:
    """Un turno del AGENTE real (grafo, guard, tools) con modelo guionado, usando el cliente de visión dado."""
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver

    from services.agent.llm import ScriptedChatModel, ai_tools, tool_call
    from services.agent.plant.db import init_db
    from services.agent.service import AgentService
    con = init_db(tmp / f"p_{time.time_ns()}.db", reseed=True)
    svc = AgentService(model=None, con=con, vision=vision_client, checkpointer=InMemorySaver(), model_name="fake",
                       upload_root=tmp / "up")
    img = svc.upload(image_bytes, "x.png", "P1", "P1-L1", "u_sup_p1", "supervisor")["image_id"]
    script = [ai_tools(tool_call("inspect_images", {"image_ids": [img]}))]
    script += [s(img) for s in (extra_script or [])] + [AIMessage(content="fin")]
    model = ScriptedChatModel(script=script)
    svc.deps.model = model
    r = svc.chat("it", "P1", "P1-L1", "u_sup_p1", "supervisor", "inspecciona", [img])
    from langchain_core.messages import ToolMessage
    first = json.loads([m for m in model.seen[1] if isinstance(m, ToolMessage)][0].content)
    n_cmd = con.execute("SELECT COUNT(*) FROM robot_commands").fetchone()[0]
    n_rev = con.execute("SELECT COUNT(*) FROM review_queue WHERE image_id IS NOT NULL").fetchone()[0]
    return {"inspect": first, "tools": [(t["tool"], t["status"]) for t in r["tool_calls"]],
            "robot_commands": n_cmd, "review_cases_created": n_rev}, svc


def live(url: str) -> dict:
    """Solo lectura contra el servicio en marcha + escenarios adversos del agente."""
    from services.agent.llm import ai_tools, tool_call
    tmp = Path(tempfile.mkdtemp(prefix="vision_live_"))
    out: dict = {"target": url}
    vc = VisionClient(base_url=url, timeout_s=10.0, retries=1, backoff_s=0.1)
    out["health"] = vc.health()
    ok, info = _agent_turn(vc, (COIL / "obj5__30.png").read_bytes(), tmp)
    out["agent_known_object"] = {"decision": ok["inspect"]["results"][0]["decision"],
                                 "label": ok["inspect"]["results"][0]["object"]["label"],
                                 "angle": ok["inspect"]["results"][0]["angle"]["degrees"],
                                 "evidence": [e["image_id"] for e in ok["inspect"]["results"][0]["evidence_similar_views"]]}
    ood, _ = _agent_turn(vc, synth_out_of_domain(), tmp)
    r = ood["inspect"]["results"][0]
    out["ADV1_out_of_domain"] = {"decision": r["decision"], "reasons": r["reasons"], "recommended": r["recommended_action"],
                                 "robot_commands": ood["robot_commands"]}
    rev, _ = _agent_turn(vc, (COIL / "obj47__90.png").read_bytes(), tmp,
                         [lambda img: ai_tools(tool_call("enqueue_for_review", {"image_id": img}))])
    r = rev["inspect"]["results"][0]
    out["ADV2_unreliable_symmetric"] = {"decision": r["decision"], "reasons": r["reasons"],
                                        "recommended": r["recommended_action"], "tools": rev["tools"],
                                        "review_cases_created": rev["review_cases_created"],
                                        "robot_commands": rev["robot_commands"]}
    dark, _ = _agent_turn(vc, synth_dark((COIL / "obj5__30.png").read_bytes()), tmp)
    r = dark["inspect"]["results"][0]
    out["low_quality_dark"] = {"decision": r["decision"], "reasons": r["reasons"], "robot_commands": dark["robot_commands"]}
    # 5xx / timeout: el servicio de demo no habilita X-Fault -> instancia temporal con la misma imagen de código/modelo
    mtmp = Path(tempfile.mkdtemp(prefix="vision_fault_"))
    shutil.copytree(ROOT / "models" / "vision-1.0.1", mtmp / "vision-1.0.1")
    port = free_port()
    env = {**os.environ, "ALLOW_FAULT_INJECTION": "true", "FAULT_TIMEOUT_S": "8", "MODELS_ROOT": str(mtmp),
           "MODEL_DIR": str(mtmp / "vision-1.0.1")}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "services.vision_api.app:app", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        furl = f"http://127.0.0.1:{port}"
        for _ in range(120):
            try:
                if httpx.get(furl + "/health", timeout=1).json().get("status") == "ok":
                    break
            except Exception:
                time.sleep(0.5)
        for fault in ("500", "timeout"):
            fvc = VisionClient(base_url=furl, timeout_s=2.0, retries=1, backoff_s=0.1)
            fvc.fault = fault
            f, _ = _agent_turn(fvc, (COIL / "obj5__30.png").read_bytes(), tmp)
            r = f["inspect"]["results"][0]
            out[f"ADV4_fault_{fault}"] = {"degraded": f["inspect"].get("degraded"), "status": r.get("status"),
                                          "error": r.get("error"), "robot_command": r.get("robot_command"),
                                          "robot_commands": f["robot_commands"]}
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:  # una petición con X-Fault: timeout puede seguir durmiendo
            proc.kill()
        shutil.rmtree(mtmp, ignore_errors=True)
    return out


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--live":
        res = live(sys.argv[2])
        from datetime import datetime
        dest = ROOT / "eval" / "agent" / "results" / f"integration_vision_live_{datetime.now():%Y%m%d-%H%M%S}.json"
        dest.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
        print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
        print(f"guardado en {dest.relative_to(ROOT)}")
    else:
        main()
