"""Runner reproducible de la evaluación del agente.

Uso:
  uv run python eval/agent/run_eval.py                       # LLM real (LLM_PROVIDER/LLM_MODEL/GOOGLE_API_KEY)
  uv run python eval/agent/run_eval.py --only UC3-02,ADV-05  # subconjunto
  uv run python eval/agent/run_eval.py --dry-run             # valida casos y fixtures sin LLM
  uv run python eval/agent/run_eval.py --vision real         # contra el servicio de visión real (puerto 8001)

Cada caso corre con DB de planta recién sembrada, checkpointer en memoria y stub de visión determinista
(por defecto), de modo que se evalúa al AGENTE (selección de tools, argumentos, guardas, respuestas) y no la
precisión del modelo de visión. Los casos `scripted` usan un modelo guionado para probar guardas
deterministas (tool inválida, bucle) que un LLM real rara vez dispara.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from eval.agent.vision_stub import VisionStub, synth_blurry, synth_dark, synth_out_of_domain  # noqa: E402
from services.agent.approvals import ApprovalError  # noqa: E402
from services.agent.config import llm_model, llm_provider, roles_cfg  # noqa: E402
from services.agent.llm import ScriptedChatModel, ai_tools, estimate_cost, tool_call  # noqa: E402
from services.agent.plant.db import init_db, rows  # noqa: E402
from services.agent.service import AgentService  # noqa: E402
from services.agent.vision_client import VisionClient  # noqa: E402

FALLBACKS: list = []
COIL = ROOT / "data" / "raw" / "coil-100"
EVAL = ROOT / "eval" / "agent"
CASES = EVAL / "cases.yaml"
RESULTS = EVAL / "results"
REPORT = ROOT / "docs" / "report" / "agent_eval.md"
NOTES = EVAL / "report_notes.md"


def load_cases(which: str = "main") -> list[dict]:
    return yaml.safe_load((CASES if which == "main" else EVAL / "holdout.yaml").read_text())["cases"]


def _fill(obj, imgs: list[str]):
    if isinstance(obj, str):
        for i, v in enumerate(imgs):
            obj = obj.replace(f"{{img{i}}}", v)
        if obj == "{all_imgs}":
            return list(imgs)
        return obj
    if isinstance(obj, list):
        out = []
        for x in obj:
            y = _fill(x, imgs)
            out.extend(y) if (x == "{all_imgs}") else out.append(y)
        return out
    if isinstance(obj, dict):
        return {k: _fill(v, imgs) for k, v in obj.items()}
    return obj


def build_upload(spec: dict, stub: VisionStub) -> bytes:
    if "coil" in spec:
        p = COIL / f"{spec['coil']}.png"
        data = p.read_bytes()
        if spec.get("synth") == "dark":
            return synth_dark(data)
        if spec.get("synth") == "blurry":
            return synth_blurry(data)
        if spec.get("register", True):
            stub.register_file(p, spec.get("scenario", "act"))
        return data
    if spec.get("synth") == "ood":
        return synth_out_of_domain(spec.get("seed", 7))
    raise ValueError(f"upload inválido: {spec}")


def scripted_model(steps: list[dict], imgs: list[str]) -> ScriptedChatModel:
    script = []
    for i, s in enumerate(steps):
        if "tool_calls" in s:
            script.append(ai_tools(*[tool_call(tc["name"], _fill(tc.get("args", {}), imgs), f"sc{i}_{j}")
                                     for j, tc in enumerate(s["tool_calls"])]))
        else:
            script.append(AIMessage(content=s.get("text", "")))
    return ScriptedChatModel(script=script)


def run_case(case: dict, real_model, vision_mode: str, model_name: str) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="agent_eval_"))
    stub = VisionStub()
    con = init_db(tmp / "plant.db", reseed=True)
    if vision_mode == "real":
        vc = VisionClient()
    else:
        vc = VisionClient(base_url="http://vision.stub", transport=stub.transport(), retries=2, backoff_s=0.05)
    setup = case.get("setup", {}) or {}
    for sql in setup.get("sql", []):
        con.execute(sql)
    con.commit()
    user = case["user"]
    role = roles_cfg()["users"][user]["role"]
    plant, line = case.get("plant", "P1"), case.get("line", "P1-L1")
    imgs: list[str] = []
    # el modelo se crea después de las cargas (los guiones referencian image_ids)
    svc = AgentService(model=None, con=con, vision=vc, checkpointer=InMemorySaver(), model_name=model_name,
                       upload_root=tmp / "uploads")
    for up in setup.get("uploads", []):
        imgs.append(svc.upload(build_upload(up, stub), "x.png", plant, line, user, role)["image_id"])
    model = scripted_model(case["scripted"], imgs) if case.get("scripted") else real_model
    svc.deps.model = model
    svc.deps.model_name = "fake" if case.get("scripted") else model_name
    svc.deps.fallbacks = [] if case.get("scripted") else FALLBACKS
    used_name = "fake" if case.get("scripted") else model_name
    svc.model_name = used_name
    if setup.get("vision_fault"):
        vc.fault = setup["vision_fault"]
    turns_out, t0 = [], time.time()
    tin = tout = calls = 0
    models_used: set[str] = set()
    by_model: dict[str, dict] = {}
    error = None
    try:
        for k, turn in enumerate(case["turns"]):
            attach = imgs if (k == 0 and turn.get("attach", True)) else []
            r = svc.chat(case.get("session", "eval"), plant, line, user, role, _fill(turn["message"], imgs), attach)
            turns_out.append(r)
            models_used.update(r["usage"].get("models_used") or [])
            for m, b in (r["usage"].get("by_model") or {}).items():
                acc = by_model.setdefault(m, {"input_tokens": 0, "output_tokens": 0, "calls": 0, "cost_usd": 0.0})
                for k in ("input_tokens", "output_tokens", "calls"):
                    acc[k] += b[k]
                acc["cost_usd"] = round(acc["cost_usd"] + b["cost_usd"], 6)
            tin += r["usage"]["input_tokens"]
            tout += r["usage"]["output_tokens"]
            calls += r["usage"]["llm_calls"]
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {str(e)[:300]}"
        traceback.print_exc()
    approval = None
    if case.get("approval") and not error:
        pend = rows(con, "SELECT approval_id FROM approvals WHERE status='pending' ORDER BY created_at")
        if pend:
            a = case["approval"]
            arole = roles_cfg()["users"][a["by"]]["role"]
            try:
                approval = svc.decide(pend[-1]["approval_id"], a["by"], arole, a.get("decision", "approve"), "eval")
            except ApprovalError as e:
                approval = {"error": e.code}
        else:
            approval = {"error": "no_pending_approval"}
    latency = time.time() - t0
    checks = evaluate({**case, "checks": _fill(case.get("checks", {}), imgs)}, turns_out, approval, con, error)
    tools = [t for r in turns_out for t in r["tool_calls"]]
    return {"id": case["id"], "use_case": case["use_case"], "title": case["title"], "scripted": bool(case.get("scripted")),
            "success": all(c["ok"] for c in checks) and not error, "checks": checks, "error": error,
            "tool_selection_ok": all(c["ok"] for c in checks if c["kind"] in SELECTION_KINDS),
            "args_ok": all(c["ok"] for c in checks if c["kind"] in ARG_KINDS),
            "tools": [{"tool": t["tool"], "status": t["status"], "reason": t.get("reason") or t.get("error"),
                       "args": t["args"]} for t in tools],
            "replies": [r["reply"] for r in turns_out], "approval": approval,
            "stop_reasons": [r.get("stop_reason") for r in turns_out],
            "input_tokens": tin, "output_tokens": tout, "llm_calls": calls, "models_used": sorted(models_used),
            "by_model": by_model, "cost_usd": round(sum(b["cost_usd"] for b in by_model.values()), 6),
            "latency_s": round(latency, 2), "coverage": coverage(case)}


def _has(text: str, needle: str) -> bool:
    return needle.lower() in text.lower()


SELECTION_KINDS = ("tools_required", "tools_any", "tools_forbidden", "max_tool_calls", "tool_status")
ARG_KINDS = ("tool_args", "tool_status")


def coverage(case: dict) -> dict:
    """Un caso cuenta como cubierto solo si la expectativa NO está vacía (p. ej. `tools_forbidden: []` no cuenta)."""
    c = case.get("checks", {})
    nonempty = lambda k: k in c and c[k] not in (None, [], {})  # noqa: E731
    return {"selection": any(nonempty(k) for k in SELECTION_KINDS),
            "args": any(nonempty(k) for k in ("tool_args", "tool_args_any", "tool_args_if_called")) or
            any(st.get("match") for st in c.get("tool_status", [])),
            "args_na": bool(c.get("args_na"))}


def _args_match(args: dict, cond: dict) -> bool:
    for k, v in cond.items():
        if k.endswith("__contains"):
            if not any(str(x).lower() in str(args.get(k[:-10]) or "").lower() for x in (v if isinstance(v, list) else [v])):
                return False
            continue
        if k.endswith("__prefix"):
            if not str(args.get(k[:-8]) or "").startswith(v):
                return False
            continue
        if k.endswith("__len"):
            if len(args.get(k[:-5]) or []) != v:
                return False
        elif k.endswith("__approx"):
            a = args.get(k[:-8])
            if a is None or abs(float(a) - float(v)) > 1e-6:
                return False
        elif k.endswith("__in"):
            if args.get(k[:-4]) not in v:
                return False
        elif str(args.get(k)) != str(v):
            return False
    return True


def evaluate(case: dict, turns: list[dict], approval: dict | None, con, error: str | None) -> list[dict]:
    c = case.get("checks", {})
    out: list[dict] = []
    tools = [t for r in turns for t in r["tool_calls"]]
    ok_tools = [t["tool"] for t in tools if t["status"] == "ok"]        # solo cuenta como "usada" si terminó OK
    executed = [t["tool"] for t in tools if t["status"] != "blocked"]  # ejecutada (ok/error/timeout)
    attempted = [t["tool"] for t in tools]
    reply = "\n".join(r["reply"] for r in turns)
    final = turns[-1]["reply"] if turns else ""

    def add(kind, ok, detail):
        out.append({"kind": kind, "ok": bool(ok), "detail": detail})

    for t in c.get("tools_required", []):
        add("tools_required", t in ok_tools, f"debe ejecutar {t} con estado ok")
    for group in c.get("tools_any", []):
        add("tools_any", any(t in ok_tools for t in group), f"debe ejecutar con éxito alguno de {group}")
    for t in c.get("tools_forbidden", []):
        add("tools_forbidden", t not in executed, f"no debe ejecutar {t}")
    for ta in c.get("tool_args", []):
        ok = any(t["tool"] == ta["tool"] and t["status"] == "ok" and _args_match(t["args"], ta["match"])
                 for t in tools)
        add("tool_args", ok, f"{ta['tool']} (ok) con {ta['match']}")
    for ta in c.get("tool_args_any", []):
        ok = any(t["tool"] == ta["tool"] and t["status"] == "ok" and any(_args_match(t["args"], m) for m in ta["any"])
                 for t in tools)
        add("tool_args", ok, f"{ta['tool']} (ok) con alguno de {ta['any']}")
    for ta in c.get("tool_args_if_called", []):
        calls = [t for t in tools if t["tool"] == ta["tool"]]
        add("tool_args", all(_args_match(t["args"], ta["match"]) for t in calls),
            f"toda llamada a {ta['tool']} usa {ta['match']} (llamadas: {len(calls)})")
    for st in c.get("tool_status", []):
        ok = any(t["tool"] == st["tool"] and t["status"] == st["status"] and
                 (st.get("reason") is None or t.get("reason") == st["reason"]) and
                 _args_match(t["args"], st.get("match", {})) for t in tools)
        add("tool_status", ok, f"{st}")
    if "max_tool_calls" in c:
        add("max_tool_calls", len(attempted) <= c["max_tool_calls"], f"<= {c['max_tool_calls']} llamadas")
    for group in c.get("reply_any", []):
        group = group if isinstance(group, list) else [group]
        add("reply_any", any(_has(reply, g) for g in group), f"respuesta contiene alguno de {group}")
    for n in c.get("reply_none", []):
        add("reply_none", not _has(reply, n), f"respuesta NO contiene '{n}'")
    if "final_reply_regex" in c:
        add("final_reply_regex", re.search(c["final_reply_regex"], final, re.I | re.S), c["final_reply_regex"])
    if "pending_approval" in c:
        got = any(r.get("pending_approval") for r in turns)
        add("pending_approval", got == c["pending_approval"], f"pending_approval={c['pending_approval']}")
    if "approval_operation" in c:
        ops = [r["pending_approval"].get("operation") for r in turns if r.get("pending_approval")]
        add("approval_operation", c["approval_operation"] in ops, f"operación {c['approval_operation']}")
    if "approval_status" in c:
        st = (approval or {}).get("status") or (approval or {}).get("error")
        add("approval_status", st == c["approval_status"], f"aprobación -> {c['approval_status']} (obtenido {st})")
    if "stop_reason" in c:
        add("stop_reason", c["stop_reason"] in [r.get("stop_reason") for r in turns], f"stop_reason={c['stop_reason']}")
    for d in c.get("db", []):
        val = con.execute(d["sql"]).fetchone()[0]
        add("db", val == d["equals"], f"{d['sql']} == {d['equals']} (obtenido {val})")
    if error:
        add("no_exception", False, error)
    return out


def summarize(results: list[dict]) -> dict:
    import math
    n = len(results)
    real = [r for r in results if not r["scripted"]]
    lat = sorted(r["latency_s"] for r in real) or [0]
    by_uc: dict[str, list[bool]] = {}
    for r in results:
        by_uc.setdefault(str(r["use_case"]), []).append(r["success"])
    by_model: dict[str, dict] = {}
    for r in real:
        for m, b in (r.get("by_model") or {}).items():
            acc = by_model.setdefault(m, {"input_tokens": 0, "output_tokens": 0, "calls": 0, "cost_usd": 0.0})
            for k in ("input_tokens", "output_tokens", "calls"):
                acc[k] += b[k]
            acc["cost_usd"] = round(acc["cost_usd"] + b["cost_usd"], 6)
    sel = [r for r in results if r.get("coverage", {}).get("selection")]
    arg = [r for r in results if r.get("coverage", {}).get("args")]
    na = [r for r in results if r.get("coverage", {}).get("args_na") and not r.get("coverage", {}).get("args")]
    rate = lambda xs, f: round(sum(f(r) for r in xs) / len(xs), 3) if xs else None  # noqa: E731
    return {"cases": n, "cases_real_llm": len(real), "cases_scripted": n - len(real),
            "success_rate": rate(results, lambda r: r["success"]),
            "success_rate_real_llm": rate(real, lambda r: r["success"]),
            "coverage": {"selection_checked": len(sel), "args_checked": len(arg), "args_not_applicable": len(na)},
            "tool_selection_rate": rate(sel, lambda r: r["tool_selection_ok"]),
            "args_rate": rate(arg, lambda r: r["args_ok"]),
            "by_use_case": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_uc.items())},
            "real_llm": {"input_tokens": sum(r["input_tokens"] for r in real),
                         "output_tokens": sum(r["output_tokens"] for r in real),
                         "llm_calls": sum(r["llm_calls"] for r in real), "by_model": by_model,
                         "cost_usd_total": round(sum(b["cost_usd"] for b in by_model.values()), 5),
                         "cost_usd_per_case": round(sum(b["cost_usd"] for b in by_model.values()) / len(real), 6)
                         if real else 0},
            "scripted_llm": {"input_tokens": sum(r["input_tokens"] for r in results if r["scripted"]),
                             "llm_calls": sum(r["llm_calls"] for r in results if r["scripted"])},
            "latency_s_mean_real": round(statistics.mean(lat), 2),
            "latency_s_p95_real": lat[max(0, math.ceil(0.95 * len(lat)) - 1)]}  # nearest-rank


def _pct(x) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def _section(title: str, meta: dict, summary: dict, results: list[dict]) -> list[str]:
    rl = summary["real_llm"]
    cov = summary["coverage"]
    L = [f"## {title}", "",
         f"- LLM: `{meta['provider']}` / `{meta['model']}` · visión: `{meta['vision']}` · resultados: `{meta['results_file']}`",
         f"- Casos: {summary['cases']} ({summary['cases_real_llm']} con LLM real, {summary['cases_scripted']} guionados "
         "que prueban guardas deterministas y no miden al LLM).",
         (f"- Fusionado con `{meta['merged_from']}`; re-ejecutados: {', '.join(meta['rerun_ids'])}."
          if meta.get("merged_from") else "- Corrida única, sin fusiones."), "",
         "| Métrica | Valor |", "|---|---|",
         f"| Éxito de tarea (todos / solo LLM real) | {_pct(summary['success_rate'])} / {_pct(summary['success_rate_real_llm'])} |",
         f"| Cobertura de checks: selección / argumentos / args no aplica (sin tools esperadas) | "
         f"{cov['selection_checked']}/{summary['cases']} · {cov['args_checked']}/{summary['cases']} · {cov['args_not_applicable']} |",
         f"| Selección correcta de tools (sobre casos con check de selección) | {_pct(summary['tool_selection_rate'])} |",
         f"| Argumentos correctos (sobre casos con check de argumentos) | {_pct(summary['args_rate'])} |",
         f"| LLM real: llamadas · tokens entrada/salida | {rl['llm_calls']} · {rl['input_tokens']} / {rl['output_tokens']} |",
         f"| LLM real: costo estimado total / por caso (USD, tarifas no verificadas) | {rl['cost_usd_total']} / {rl['cost_usd_per_case']} |",
         f"| Guionado (excluido de lo anterior): llamadas · tokens ficticios | {summary['scripted_llm']['llm_calls']} · "
         f"{summary['scripted_llm']['input_tokens']} |",
         f"| Latencia por caso LLM real: media / p95 (rango más próximo) | {summary['latency_s_mean_real']} s / "
         f"{summary['latency_s_p95_real']} s |", "",
         "Costo por modelo (incluye respaldos): " + "; ".join(
             f"`{m}`: {b['calls']} llamadas, {b['input_tokens']}/{b['output_tokens']} tokens, USD {b['cost_usd']}"
             for m, b in rl["by_model"].items()), "",
         "Éxito por caso de uso: " + ", ".join(f"{k}: {v}" for k, v in summary["by_use_case"].items()), "",
         "| Caso | UC | Éxito | Tools (estado) | Tokens in/out | Latencia s |", "|---|---|---|---|---|---|"]
    for r in results:
        tl = ", ".join(f"{t['tool']}{'' if t['status'] == 'ok' else '(' + str(t['status']) + ')'}" for t in r["tools"])
        L.append(f"| {r['id']}{' (guionado)' if r['scripted'] else ''} | {r['use_case']} | "
                 f"{'OK' if r['success'] else 'FALLA'} | {tl or '-'} | {r['input_tokens']}/{r['output_tokens']} | "
                 f"{r['latency_s']} |")
    fails = [r for r in results if not r["success"]]
    L += ["", f"### Fallas ({len(fails)})", ""]
    if not fails:
        L.append("Ninguna.")
    for r in fails:
        L.append(f"- **{r['id']}** – {r['title']}")
        for c in r["checks"]:
            if not c["ok"]:
                L.append(f"  - {c['kind']}: {c['detail']}")
        if r["error"]:
            L.append(f"  - excepción: {r['error']}")
        rep = (r["replies"][-1] if r["replies"] else "").replace("\n", " ")[:300]
        L.append(f"  - respuesta final (recorte): «{rep}»")
    return L + [""]


def write_report(meta: dict, summary: dict, results: list[dict], path: Path, holdout: dict | None = None) -> None:
    L = ["# Evaluación del asistente agéntico (Reto 3)", "",
         f"Generado por `eval/agent/run_eval.py`. Set principal: `eval/agent/cases.yaml` (usado también para ajustar el "
         "prompt: su resultado es optimista). Holdout: `eval/agent/holdout.yaml` (escrito después del ajuste y no usado "
         "para tunear). Cada caso corre con DB re-sembrada y checkpointer en memoria.", ""]
    L += _section("Set principal", meta, summary, results)
    if holdout:
        L += _section("Holdout (no usado para ajustar prompt ni checks)", holdout["meta"], holdout["summary"],
                      holdout["results"])
    if NOTES.exists():
        L += ["", NOTES.read_text()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--vision", choices=["stub", "real"], default="stub")
    ap.add_argument("--min-interval", type=float, default=float(os.environ.get("LLM_MIN_INTERVAL_S", "4")))
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--report-from", default="", help="regenera docs/report/agent_eval.md desde un results JSON")
    ap.add_argument("--holdout-from", default="", help="results JSON del holdout para incluir en el reporte")
    ap.add_argument("--set", choices=["main", "holdout"], default="main")
    ap.add_argument("--merge", default="", help="results JSON previo: re-ejecuta --only y reemplaza esos casos")
    a = ap.parse_args()
    os.environ["LLM_MIN_INTERVAL_S"] = str(a.min_interval)
    if a.report_from:
        def recompute(path: str, which: str) -> dict:
            # recalcula cobertura y agregados con la definición vigente de los casos (sin nueva corrida LLM)
            d = json.loads(Path(path).read_text())
            defs = {c["id"]: c for c in load_cases(which)}
            for r in d["results"]:
                r["coverage"] = coverage(defs[r["id"]])
            d["summary"] = summarize(d["results"])
            d["meta"]["recomputed_from_saved_results"] = True
            return d
        d = recompute(a.report_from, "main")
        h = recompute(a.holdout_from, "holdout") if a.holdout_from else None
        write_report(d["meta"], d["summary"], d["results"], REPORT, h)
        print(f"reporte regenerado desde {a.report_from}")
        return
    cases = load_cases(a.set)
    if a.only:
        keep = set(a.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "ids duplicados"
    if a.dry_run:
        for c in cases:
            stub = VisionStub()
            for up in (c.get("setup") or {}).get("uploads", []):
                build_upload(up, stub)
            assert c["user"] in roles_cfg()["users"], c["id"]
            assert c.get("checks"), c["id"]
        print(f"dry-run OK: {len(cases)} casos válidos; scripted={sum(bool(c.get('scripted')) for c in cases)}")
        return
    real_model = None
    model_name = llm_model()
    if any(not c.get("scripted") for c in cases):
        if llm_provider() == "google_genai" and not os.environ.get("GOOGLE_API_KEY"):
            sys.exit("Falta GOOGLE_API_KEY (en .env o entorno). Use --dry-run para validar sin LLM.")
        from services.agent.llm import make_chat_model
        real_model = make_chat_model()
        from services.agent.llm import make_fallback_models
        FALLBACKS.extend(make_fallback_models())
    results = []
    for c in cases:
        print(f"[{c['id']}] {c['title']} ...", flush=True)
        r = run_case(c, real_model, a.vision, model_name)
        print(f"   -> {'OK' if r['success'] else 'FALLA'} tools={[t['tool'] for t in r['tools']]} "
              f"tok={r['input_tokens']}/{r['output_tokens']} {r['latency_s']}s", flush=True)
        results.append(r)
    if a.merge:
        prev = json.loads(Path(a.merge).read_text())
        new_ids = {r["id"] for r in results}
        order = [c["id"] for c in load_cases(a.set)]
        merged = {r["id"]: r for r in prev["results"] if r["id"] not in new_ids}
        merged.update({r["id"]: {**r, "rerun": True} for r in results})
        results = [merged[i] for i in order if i in merged]
    summary = summarize(results)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{ts}{'-holdout' if a.set == 'holdout' else ''}.json"
    meta = {"timestamp": ts, "set": a.set, "provider": llm_provider(), "model": model_name, "vision": a.vision,
            "min_interval_s": a.min_interval, "results_file": str(out.relative_to(ROOT)),
            "merged_from": a.merge or None, "rerun_ids": a.only.split(",") if a.merge else []}
    out.write_text(json.dumps({"meta": meta, "summary": summary, "results": results}, ensure_ascii=False, indent=1))
    if not a.no_report and a.set == "main":
        write_report(meta, summary, results, REPORT)
    print(json.dumps(summary, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
