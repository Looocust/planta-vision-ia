"""Análisis de datos, latencia, resúmenes y figuras (usados por el notebook y el reporte)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy.spatial.distance import cdist  # noqa: E402

from .angle import circular_error, symmetry_profile  # noqa: E402
from .data import PROJECT_ROOT  # noqa: E402
from .models import rotational_symmetry_score  # noqa: E402
from .reject import ANGLE_TOLERANCE_DEG, expected_calibration_error, risk_coverage  # noqa: E402

FIG_DIR = PROJECT_ROOT / "docs" / "report" / "figures"
PALETTE = {"classic": "#2a6f97", "resnet18": "#c8553d", "baseline": "#8d8d8d"}


def savefig(fig, name: str) -> Path:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    p = FIG_DIR / f"{name}.png"
    fig.savefig(p, dpi=130, bbox_inches="tight")
    return p


# ---------------------------------------------------------------- EDA

def sample_grid(ds, objects=None, angles=(0, 45, 90, 135, 180, 225, 270, 315)):
    objects = objects or [1, 7, 25, 42, 47, 67, 84, 95, 98, 100]
    fig, axes = plt.subplots(len(objects), len(angles), figsize=(len(angles) * 1.1, len(objects) * 1.1))
    for i, o in enumerate(objects):
        for j, a in enumerate(angles):
            ax = axes[i, j]
            ax.imshow(ds.images[(ds.labels == o) & (ds.angles == a)][0])
            ax.set_xticks([]); ax.set_yticks([])
            if j == 0:
                ax.set_ylabel(f"obj{o}", fontsize=7)
            if i == 0:
                ax.set_title(f"{a}°", fontsize=7)
    fig.suptitle("COIL-100: vistas de ejemplo (filas = objetos, columnas = ángulo)", fontsize=9)
    return fig


def image_stats(ds) -> dict:
    import cv2

    g = np.stack([cv2.cvtColor(im, cv2.COLOR_RGB2GRAY) for im in ds.images])
    border = np.concatenate([g[:, :4].reshape(len(g), -1), g[:, -4:].reshape(len(g), -1)], 1)
    lap = np.array([cv2.Laplacian(x, cv2.CV_64F).var() for x in g])
    return {"brightness": g.mean((1, 2)) / 255, "object_fraction": (g > 40).mean((1, 2)),
            "background_median": np.median(border, 1), "sharpness": lap}


def symmetry_analysis(Z: np.ndarray, ds) -> dict:
    """Por objeto: perfil distancia-vs-separación, índice de perfil y índice LOO (todas las vistas)."""
    out = {"profile": {}, "profile_score": {}, "loo_score": {}, "ratio_180_90": {}}
    for o in np.unique(ds.labels):
        m = ds.labels == o
        a = ds.angles[m].astype(float)
        p = symmetry_profile(Z[m], a)
        out["profile"][int(o)] = p["profile"]
        out["profile_score"][int(o)] = float(1 / p["profile"][p["separations"] >= 45].mean())
        out["loo_score"][int(o)] = rotational_symmetry_score(Z[m], a)
        prof = dict(zip(p["separations"], p["profile"]))
        out["ratio_180_90"][int(o)] = float(prof[180] / prof[90])
    out["separations"] = p["separations"]
    return out


def symmetry_figure(sym: dict, highlight=(95, 94, 47, 25, 67, 98, 1, 42)):
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.6))
    for o, prof in sym["profile"].items():
        axes[0].plot(sym["separations"], prof, color="#cccccc", lw=0.6)
    for o in highlight:
        axes[0].plot(sym["separations"], sym["profile"][o], lw=1.6, label=f"obj{o}")
    axes[0].set_xlabel("separación angular entre vistas (°)")
    axes[0].set_ylabel("distancia media / distancia a 5°")
    axes[0].set_title("Perfil de auto-similitud por objeto")
    axes[0].legend(fontsize=6, ncol=2)
    ps = np.array(list(sym["profile_score"].values()))
    axes[1].hist(ps, bins=25, color=PALETTE["classic"])
    axes[1].set_xlabel("índice de perfil (d@5° / d@≥45°); alto = simétrico")
    axes[1].set_title("Ambigüedad rotacional (perfil)")
    r = np.array(list(sym["ratio_180_90"].values()))
    axes[2].hist(r, bins=25, color=PALETTE["resnet18"])
    axes[2].axvline(1, color="k", ls="--", lw=0.8)
    axes[2].set_xlabel("d(θ,θ+180°) / d(θ,θ+90°); <1 = vistas opuestas parecidas")
    axes[2].set_title("Similitud a 180°")
    fig.tight_layout()
    return fig


def confusable_pairs(Z: np.ndarray, ds, top: int = 10) -> list[dict]:
    """Pares de objetos distintos más cercanos (distancia mínima entre cualquiera de sus vistas)."""
    objs = np.unique(ds.labels)
    D = cdist(Z, Z)
    L = ds.labels
    res = []
    M = np.full((len(objs), len(objs)), np.inf)
    for i, a in enumerate(objs):
        ma = L == a
        Da = D[ma]
        for j, b in enumerate(objs):
            if b <= a:
                continue
            M[i, j] = Da[:, L == b].min()
    intra = np.array([np.sort(D[L == o][:, L == o], 1)[:, 1].mean() for o in objs])
    iu = np.dstack(np.unravel_index(np.argsort(M, axis=None)[:top], M.shape))[0]
    for i, j in iu:
        res.append({"a": f"obj{objs[i]}", "b": f"obj{objs[j]}", "min_inter_distance": float(M[i, j]),
                    "mean_intra_nn_distance": float((intra[i] + intra[j]) / 2)})
    return res


# ---------------------------------------------------------------- resultados

def per_protocol_bars(results: dict):
    prots = list(results["protocols"])
    metrics = [("class_accuracy", "Exactitud de clase"), ("angle_within_10", "Ángulo ≤10° (clase correcta)"),
               ("end_to_end_success", "Éxito extremo a extremo"), ("coverage_act", "Fracción automatizada (ACT)"),
               ("error_among_act", "Error entre ACT")]
    fig, axes = plt.subplots(1, len(metrics), figsize=(17, 3.4))
    w = 0.27
    for k, (m, title) in enumerate(metrics):
        ax = axes[k]
        for s, (name, getter) in enumerate([
            ("Clásico (prod.)", lambda r: r["classic"]["test"][r["classic"]["selected"]["clf"]]),
            ("Base píxeles 1-NN", lambda r: r["classic"]["baseline_raw_1nn"]),
            ("ResNet18 congelada", lambda r: r["resnet18"]["test"][r["resnet18"]["selected"]["clf"]]),
        ]):
            vals = [getter(results["protocols"][p]).get(m, np.nan) for p in prots]
            ax.bar(np.arange(len(prots)) + (s - 1) * w, vals, w, label=name,
                   color=[PALETTE["classic"], PALETTE["baseline"], PALETTE["resnet18"]][s])
        ax.set_xticks(range(len(prots)), prots)
        ax.set_title(title, fontsize=9)
        ax.set_ylim(0, 1.05)
    axes[0].legend(fontsize=7, loc="lower left")
    fig.tight_layout()
    return fig


def load_outputs(feature: str, protocol: str, out_dir: Path) -> dict:
    z = np.load(out_dir / f"{feature}_{protocol}.npz", allow_pickle=True)
    return {k: z[k] for k in z.files}


def angle_error_hist(outs: dict):
    fig, axes = plt.subplots(1, len(outs), figsize=(4 * len(outs), 3))
    for ax, (p, o) in zip(np.atleast_1d(axes), outs.items()):
        ok = (o["label"] == o["true_label"]) & ~o["is_unknown"]
        err = circular_error(o["angle"][ok], o["true_angle"][ok])
        ax.hist(err, bins=np.arange(0, 185, 5), color=PALETTE["classic"])
        ax.axvline(ANGLE_TOLERANCE_DEG, color="r", ls="--", lw=1, label="±10°")
        ax.set_yscale("log")
        ax.set_title(f"Protocolo {p}: MAE={err.mean():.1f}°, ≤10°={np.mean(err <= 10):.1%}", fontsize=9)
        ax.set_xlabel("error angular circular (°)")
    np.atleast_1d(axes)[0].legend()
    fig.tight_layout()
    return fig


def reliability_and_risk(outs: dict):
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    ax = axes[0]
    ax.plot([0, 1], [0, 1], "k--", lw=0.8)
    for p, o in outs.items():
        k = ~o["is_unknown"]
        corr = (o["label"] == o["true_label"])[k]
        ece, rel = expected_calibration_error(o["confidence"][k], corr)
        ax.plot(rel["bin_conf"], rel["bin_acc"], "o-", ms=3, label=f"{p} (ECE={ece:.3f})")
    ax.set_xlabel("confianza predicha"); ax.set_ylabel("exactitud observada")
    ax.set_title("Diagrama de confiabilidad (clase)")
    ax.legend(fontsize=7)
    ax = axes[1]
    for p, o in outs.items():
        k = ~o["is_unknown"]
        succ = ((o["label"] == o["true_label"]) & (circular_error(o["angle"], o["true_angle"]) <= 10))[k]
        rc = risk_coverage(o["confidence"][k], succ)
        ax.plot(rc["coverage"], rc["risk"], label=f"{p} conf. clase (AURC={rc['aurc']:.3f})")
        rc2 = risk_coverage(-o["angle_unc"][k] - 1e-6 * (1 - o["confidence"][k]), succ)
        ax.plot(rc2["coverage"], rc2["risk"], ls="--", label=f"{p} −incert. ángulo (AURC={rc2['aurc']:.3f})")
    ax.set_xlabel("cobertura (fracción automatizada)"); ax.set_ylabel("riesgo (1 − éxito e2e)")
    ax.set_title("Riesgo–cobertura (éxito extremo a extremo)")
    ax.legend(fontsize=6)
    ax = axes[2]
    for p, o in outs.items():
        k = ~o["is_unknown"]
        ok = (o["label"] == o["true_label"])[k]
        err = circular_error(o["angle"], o["true_angle"])[k][ok]
        unc = o["angle_unc"][k][ok]
        bins = np.array([0, 2.5, 5, 7.5, 10, 15, 20, 30, 60, 181])
        idx = np.digitize(unc, bins) - 1
        frac = [np.mean(err[idx == b] <= 10) if (idx == b).sum() >= 10 else np.nan for b in range(len(bins) - 1)]
        ax.plot((bins[:-1] + bins[1:]) / 2, frac, "o-", ms=3, label=p)
    ax.axvline(10, color="r", ls=":", lw=0.8)
    ax.set_xscale("symlog", linthresh=10)
    ax.set_xlabel("incertidumbre angular reportada (°)"); ax.set_ylabel("P(|error| ≤ 10°)")
    ax.set_title("Incertidumbre angular vs. acierto ±10°")
    ax.legend(fontsize=7)
    fig.tight_layout()
    return fig


def unknown_roc(o: dict):
    from sklearn.metrics import roc_curve

    fig, ax = plt.subplots(figsize=(4.5, 4))
    y = o["is_unknown"].astype(int)
    from scipy.stats import rankdata

    for name, s in [("distancia al vecino", o["nn_distance"]), ("1 − confianza", -o["confidence"]),
                    ("combinado (rangos)", rankdata(o["nn_distance"]) + rankdata(-o["confidence"]))]:
        fpr, tpr, _ = roc_curve(y, s)
        from sklearn.metrics import auc

        ax.plot(fpr, tpr, label=f"{name} (AUROC={auc(fpr, tpr):.3f})")
    ax.plot([0, 1], [0, 1], "k--", lw=0.6)
    ax.set_xlabel("FPR (conocidos rechazados)"); ax.set_ylabel("TPR (desconocidos detectados)")
    ax.set_title("Protocolo D: conocido vs. desconocido")
    ax.legend(fontsize=7)
    return fig


def per_object_failures(outs: dict, top: int = 12) -> dict:
    rows = {}
    for p, o in outs.items():
        k = ~o["is_unknown"]
        fail = ~((o["label"] == o["true_label"]) & (circular_error(o["angle"], o["true_angle"]) <= 10))
        cls_fail = o["label"] != o["true_label"]
        objs, cnt = np.unique(o["true_label"][k & fail], return_counts=True)
        order = np.argsort(-cnt)[:top]
        tot = {lab: int((o["true_label"][k] == lab).sum()) for lab in objs[order]}
        rows[p] = [{"object": str(objs[i]), "failures": int(cnt[i]), "n": tot[objs[i]],
                    "class_errors": int((cls_fail & k & (o["true_label"] == objs[i])).sum()),
                    "confused_with": _most_common(o["label"][k & cls_fail & (o["true_label"] == objs[i])])}
                   for i in order]
    return rows


def _most_common(arr):
    if len(arr) == 0:
        return None
    v, c = np.unique(arr, return_counts=True)
    return str(v[np.argmax(c)])


def failure_examples_figure(ds, outs: dict, protocol: str, n: int = 8):
    o = outs[protocol]
    k = ~o["is_unknown"]
    cls_fail = np.where(k & (o["label"] != o["true_label"]))[0]
    ang_fail = np.where(k & (o["label"] == o["true_label"]) & (circular_error(o["angle"], o["true_angle"]) > 10))[0]
    rng = np.random.default_rng(42)
    pick = list(rng.choice(cls_fail, min(n // 2, len(cls_fail)), replace=False)) + \
        list(rng.choice(ang_fail, min(n - min(n // 2, len(cls_fail)), len(ang_fail)), replace=False))
    id2idx = {iid: i for i, iid in enumerate(ds.image_ids)}
    fig, axes = plt.subplots(2, len(pick), figsize=(1.6 * len(pick), 3.6))
    for j, i in enumerate(pick):
        tl, ta = o["true_label"][i], int(o["true_angle"][i])
        axes[0, j].imshow(ds.images[id2idx[f"{tl}__{ta}"]])
        axes[0, j].set_title(f"real {tl}@{ta}°", fontsize=6)
        pl, pa = o["label"][i], int(round(float(o["angle"][i]) / 5) * 5) % 360
        key = f"{pl}__{pa}"
        if key in id2idx:
            axes[1, j].imshow(ds.images[id2idx[key]])
        axes[1, j].set_title(f"pred {pl}@{float(o['angle'][i]):.0f}°", fontsize=6)
        for r in (0, 1):
            axes[r, j].set_xticks([]); axes[r, j].set_yticks([])
    fig.suptitle(f"Protocolo {protocol}: errores (arriba: consulta; abajo: vista de referencia de la predicción)", fontsize=8)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------- latencia y tamaño

def measure_latency(predictor, images: np.ndarray, n: int = 100) -> dict:
    """Latencia por imagen (ms) del pipeline productivo completo (calidad + features + modelo + decisión)."""
    for im in images[:5]:
        predictor.predict(im)
    ts = []
    for im in images[:n]:
        t = time.perf_counter()
        predictor.predict(im)
        ts.append((time.perf_counter() - t) * 1000)
    return {"p50_ms": float(np.percentile(ts, 50)), "p95_ms": float(np.percentile(ts, 95)), "mean_ms": float(np.mean(ts))}


def measure_resnet_latency(images: np.ndarray, n: int = 50) -> dict:
    import torch

    from .modern import load_resnet18, resnet_embeddings

    out = {}
    for dev in ["cpu"] + (["mps"] if torch.backends.mps.is_available() else []):
        m = load_resnet18(dev)
        resnet_embeddings(images[:4], m, batch_size=1, device=dev)
        ts = []
        for im in images[:n]:
            t = time.perf_counter()
            resnet_embeddings(im[None], m, batch_size=1, device=dev)
            if dev == "mps":
                torch.mps.synchronize()
            ts.append((time.perf_counter() - t) * 1000)
        out[dev] = {"p50_ms": float(np.percentile(ts, 50)), "p95_ms": float(np.percentile(ts, 95))}
    n_params = sum(p.numel() for p in load_resnet18("cpu").parameters())
    out["weights_mb_fp32"] = round(n_params * 4 / 1e6, 1)
    return out


def latency_figure(lat: dict):
    names = list(lat)
    vals = [lat[k].get("p50_ms", lat[k].get("mean_ms")) for k in names]
    fig, ax = plt.subplots(figsize=(6, 3))
    ax.barh(names, vals, color=[PALETTE.get(k.split()[0], "#555") for k in names])
    for i, v in enumerate(vals):
        ax.text(v, i, f" {v:.1f} ms", va="center", fontsize=8)
    ax.set_xlabel("ms por imagen (p50 de 1 imagen; la base es la media por imagen en lote)")
    ax.set_title("Latencia de inferencia")
    fig.tight_layout()
    return fig


def build_summary(results: dict) -> dict:
    s = {}
    for p, rr in results["protocols"].items():
        c = rr["classic"]
        cm = c["test"][c["selected"]["clf"]]
        row = {"classic_selected": c["selected"], "classic": _pick(cm), "baseline_raw_1nn": _pick(c["baseline_raw_1nn"])}
        if "resnet18" in rr:
            r = rr["resnet18"]
            row["resnet18_selected"] = r["selected"]
            row["resnet18"] = _pick(r["test"][r["selected"]["clf"]])
        s[p] = row
    return s


def _pick(m: dict) -> dict:
    keys = ("class_accuracy", "angle_mae_deg", "angle_median_deg", "angle_within_10", "end_to_end_success",
            "coverage_act", "review_rate", "reject_rate", "error_among_act", "ece", "auroc_nn_distance",
            "auroc_neg_confidence", "auroc_combined_rank", "unknown_rejected", "unknown_false_accept_act",
            "unknown_not_act", "known_false_reject", "predict_ms_per_image_batch")
    return {k: m[k] for k in keys if k in m}


def strict_json(o):
    """NaN/inf → null (JSON estándar); numpy → tipos nativos."""
    if isinstance(o, dict):
        return {str(k): strict_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [strict_json(v) for v in o]
    if isinstance(o, np.ndarray):
        return strict_json(o.tolist())
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return float(o) if np.isfinite(o) else None
    return o


def write_results(results: dict, path: Path | None = None) -> Path:
    path = Path(path or PROJECT_ROOT / "docs" / "report" / "results.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(strict_json(results), indent=1, ensure_ascii=False, allow_nan=False))
    return path


# ---------------------------------------------------------------- receta congelada

RECIPE_PROTOCOLS = ("A", "B", "C", "D", "D_strict")


def _rate(x):
    return x["rate"] if isinstance(x, dict) else x


def recipe_row(r: dict) -> dict:
    d = r["decision"]
    e = d["error_among_act"]
    row = {"class_accuracy": r.get("class_accuracy"), "angle_mae_deg": r.get("angle_mae_deg"),
           "angle_within_10": r.get("angle_within_10"), "end_to_end_success": r.get("end_to_end_success"),
           "coverage_act": d["coverage_act"], "review_rate": d["review_rate"], "reject_rate": d["reject_rate"],
           "n_act": d["n_act"], "error_among_act": e["rate"], "error_among_act_wilson95": e["wilson95"],
           "automation_enabled": r["automation_enabled"], "k": r["k_selected_on_val"], "thresholds": r["thresholds"],
           "ece": r.get("ece")}
    for k in ("auroc_nn_distance", "auroc_neg_confidence", "auroc_combined_rank"):
        if k in r:
            row[k] = r[k]
    if "unknown_rejected" in d:
        row["unknown_rejected"] = d["unknown_rejected"]["rate"]
        row["unknown_rejected_wilson95"] = d["unknown_rejected"]["wilson95"]
        row["unknown_reaches_act"] = d["unknown_reaches_act"]["rate"]
        row["unknown_reaches_act_wilson95"] = d["unknown_reaches_act"]["wilson95"]
    if "known_false_reject" in d:
        row["known_false_reject"] = d["known_false_reject"]
    return row


def build_recipe_summary(recipe_results: dict, baselines: dict, nested: dict | None = None) -> dict:
    out = {}
    for p in RECIPE_PROTOCOLS:
        out[p] = {"recipe_classic": recipe_row(recipe_results["classic"][p])}
        if nested and p in nested:
            out[p]["nested_classic"] = {**recipe_row(nested[p]), "selected_candidate": nested[p]["selected_candidate"]}
        if "resnet18" in recipe_results and p in recipe_results["resnet18"]:
            out[p]["recipe_resnet18"] = recipe_row(recipe_results["resnet18"][p])
        b = baselines[p]
        out[p]["baseline_raw_1nn"] = {k: b.get(k) for k in ("class_accuracy", "angle_mae_deg", "angle_within_10",
                                                             "end_to_end_success", "coverage_act")}
        out[p]["baseline_raw_1nn"]["error_among_act"] = b["error_among_act"]["rate"]
        out[p]["baseline_raw_1nn"]["error_among_act_wilson95"] = b["error_among_act"]["wilson95"]
    return out


def recipe_bars(summary: dict):
    prots = list(summary)
    metrics = [("class_accuracy", "Exactitud de clase (conocidos)"), ("end_to_end_success", "Éxito e2e (sin rechazo)"),
               ("coverage_act", "Fracción automatizada (ACT)"), ("error_among_act", "Error entre ACT")]
    fig, axes = plt.subplots(1, len(metrics), figsize=(16, 3.4))
    w = 0.27
    w = 0.2
    series = [("Selección anidada (independiente)", "nested_classic", "#1b4965"),
              ("Config. desplegada (clásica)", "recipe_classic", PALETTE["classic"]),
              ("Base píxeles 1-NN", "baseline_raw_1nn", PALETTE["baseline"]),
              ("Config. desplegada con ResNet18", "recipe_resnet18", PALETTE["resnet18"])]
    for k, (m, title) in enumerate(metrics):
        ax = axes[k]
        for s, (name, key, col) in enumerate(series):
            vals = [summary[p].get(key, {}).get(m) for p in prots]
            vals = [np.nan if v is None else v for v in vals]
            ax.bar(np.arange(len(prots)) + (s - 1.5) * w, vals, w, label=name, color=col)
        ax.set_xticks(range(len(prots)), [p.replace("_strict", "\nestricto") for p in prots], fontsize=8)
        ax.set_title(title, fontsize=9)
        ax.set_ylim(0, 1.05)
    axes[0].legend(fontsize=7, loc="lower left")
    fig.suptitle("Resultados en test por protocolo (barra ausente = N/A, sin ACT)", fontsize=9)
    fig.tight_layout()
    return fig


def sweep_figure(recipe_res: dict, protocols=("A", "D")):
    """Riesgo entre ACT (IC Wilson 95 %) vs umbral de incertidumbre: validación (selección) y test (descriptivo)."""
    fig, axes = plt.subplots(1, len(protocols), figsize=(6 * len(protocols), 3.6))
    for ax, p in zip(np.atleast_1d(axes), protocols):
        r = recipe_res[p]
        for rows, lab, key_n, col in ((r["val_uncertainty_grid"], "validación", "val", PALETTE["classic"]),
                                      (r["test_uncertainty_sweep_descriptive"], "test (descriptivo)", "test", PALETTE["resnet18"])):
            u = np.array([x["max_angle_uncertainty_deg"] for x in rows], float)
            if key_n == "val":
                rate = np.array([np.nan if x["n_act"] == 0 else x["error_among_act"] for x in rows], float)
                lo = np.array([np.nan if x["wilson95_low"] is None else x["wilson95_low"] for x in rows], float)
                hi = np.array([np.nan if x["wilson95_high"] is None else x["wilson95_high"] for x in rows], float)
            else:
                rate = np.array([np.nan if x["risk_rate"] is None else x["risk_rate"] for x in rows], float)
                lo = np.array([np.nan if x["risk_wilson95"][0] is None else x["risk_wilson95"][0] for x in rows], float)
                hi = np.array([np.nan if x["risk_wilson95"][1] is None else x["risk_wilson95"][1] for x in rows], float)
            ax.plot(u, rate, "o-", ms=3, color=col, label=lab)
            ax.fill_between(u, lo, hi, color=col, alpha=0.2)
        ax.axhline(r["threshold_validation"]["target_error"], color="k", ls="--", lw=0.8, label="objetivo 2 %")
        ax.axvline(r["thresholds"]["max_angle_uncertainty_deg"], color="g", ls=":", lw=1, label="umbral elegido")
        ax.set_xlabel("umbral de incertidumbre angular (score en °)")
        ax.set_ylabel("P(clase incorrecta o |error|>10° | ACT)")
        ax.set_title(f"Protocolo {p}")
        ax.legend(fontsize=7)
    fig.tight_layout()
    return fig


def robustness_table(rob: dict) -> list[dict]:
    rows = []
    for name, r in rob.items():
        e = r["error_among_act"]
        row = {"perturbación": name, "exactitud": r.get("class_accuracy"), "MAE°": r.get("angle_mae_deg"),
               "éxito e2e": r.get("end_to_end_success"), "% ACT": r["coverage_act"], "n ACT": r["n_act"],
               "error|ACT": e["rate"], "IC95": e["wilson95"], "conocidos rechazados": r.get("reject_rate_known")}
        if "unknown_reaches_act" in r:
            row["desconocidos a ACT"] = r["unknown_reaches_act"]["rate"]
            row["IC95 desconocidos a ACT"] = r["unknown_reaches_act"]["wilson95"]
            row["desconocidos rechazados"] = r["unknown_rejected"]["rate"]
        rows.append(row)
    return rows
