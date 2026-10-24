"""Receta productiva CONGELADA y su evaluación independiente por protocolo.

La receta se define una sola vez (abajo) y se aplica igual en cada protocolo y en producción:
1. características clásicas → estandarización → PCA(150) → regresión logística C=1;
2. k de interpolación angular elegido en VALIDACIÓN (dentro de train) entre {1, 2, 3};
3. clases con ángulo poco fiable en validación → `rotational_ambiguity`;
4. umbrales (confianza, incertidumbre angular, distancia de desconocido) con `select_thresholds` en validación
   (error entre ACT ≤ 2 %, ≥ 50 ACT en validación; si no, automatización desactivada);
5. re-ajuste con TODO el train del protocolo y evaluación ÚNICA en su test.
El artefacto productivo es esta misma receta ajustada con las 7.200 vistas: sus métricas esperadas son las de la
receta en los protocolos, no un test independiente del artefacto (no quedan datos sin usar).
"""
from __future__ import annotations

import time

import numpy as np

from .angle import circular_error
from .data import protocol_split, validation_split
from .models import VisionModel, evaluate_predictions
from .reject import (ANGLE_TOLERANCE_DEG, auroc, decide_batch, expected_calibration_error, risk_coverage,
                     select_thresholds, unreliable_angle_classes, wilson_interval)

RECIPE = {
    "name": "classic-logreg-v1",
    "features": "HSV(16x4x4)+HOG(64px,9or,8x8)+LBP(u8,2x2)+Hu → StandardScaler → PCA(150)",
    "clf": "logreg",
    "params": {"C": 1.0},
    "n_pca": 150,
    "k_candidates": [1, 2, 3],
    "target_error": 0.02,
    "min_act": 50,
    "unreliable_max_fail_rate": 0.25,
}


def calibrate_recipe(X, names, angles, ids, fit, val, val_unk=None, recipe: dict = RECIPE) -> dict:
    """Pasos 2–4 de la receta usando solo (fit, val) ⊂ train. Devuelve k, clases marcadas y umbrales."""
    m = VisionModel(recipe["clf"], recipe["params"], n_pca=recipe["n_pca"])
    m.fit(X[fit], names[fit], angles[fit], ids[fit])
    kn = np.ones(len(val), bool) if val_unk is None else ~np.asarray(val_unk, bool)
    k_table, outs = [], {}
    for k in recipe["k_candidates"]:
        m.k_angle_refine = k
        out = m.predict(X[val], top_k=0)
        ok = (out["label"] == names[val]) & kn
        err = circular_error(out["angle"], angles[val])[ok]
        k_table.append({"k": k, "val_within_10": float(np.mean(err <= ANGLE_TOLERANCE_DEG)) if ok.any() else 0.0,
                        "val_mae_deg": float(err.mean()) if ok.any() else float("nan")})
        outs[k] = out
    best = max(k_table, key=lambda r: (round(r["val_within_10"], 4), -r["val_mae_deg"], -r["k"]))
    k = best["k"]
    out = outs[k]
    err = circular_error(out["angle"], angles[val])
    flagged = unreliable_angle_classes(names[val][kn], out["label"][kn], err[kn], recipe["unreliable_max_fail_rate"])
    amb = out["ambiguous"] | np.isin(out["label"], flagged)
    correct = (out["label"] == names[val]) & kn
    th, info = select_thresholds(correct, err, out["confidence"], out["angle_unc"], amb, out["nn_distance"],
                                 is_unknown=val_unk, target_error=recipe["target_error"], min_act=recipe["min_act"])
    return {"k": k, "k_table": k_table, "flagged": flagged, "thresholds": th, "threshold_info": info}


def _ci(k, n):
    lo, hi = wilson_interval(int(k), int(n))
    return {"rate": (k / n) if n else None, "k": int(k), "n": int(n), "wilson95": [lo, hi]}


def decision_report(out, true_names, true_angles, th, is_unknown) -> dict:
    unk = np.asarray(is_unknown, bool)
    correct = (out["label"] == true_names) & ~unk
    success = correct & (circular_error(out["angle"], true_angles) <= ANGLE_TOLERANCE_DEG)
    dec = decide_batch(out["nn_distance"], out["confidence"], out["angle_unc"], out["ambiguous"], th)
    act = dec == "ACT"
    r = {
        "n": int(len(dec)), "n_act": int(act.sum()),
        "coverage_act": float(act.mean()), "review_rate": float((dec == "REVIEW").mean()),
        "reject_rate": float((dec == "REJECT").mean()),
        "error_among_act": _ci((act & ~success).sum(), act.sum()),
        "wrong_class_or_unknown_among_act": _ci((act & ~correct).sum(), act.sum()),
        "automated_success_rate": float((act & success).mean()),
    }
    if (~unk).any():
        r["known_coverage_act"] = float(act[~unk].mean())
        r["known_false_reject"] = float((dec[~unk] == "REJECT").mean())
    if unk.any():
        r["unknown_rejected"] = _ci((dec[unk] == "REJECT").sum(), unk.sum())
        r["unknown_reaches_act"] = _ci(act[unk].sum(), unk.sum())
        r["unknown_not_act"] = float((~act[unk]).mean())
    return r, dec


def uncertainty_sweep(out, true_names, true_angles, th, is_unknown, grid=(2.5, 5, 7.5, 10, 12.5, 15, 20, 30, 45, 60)):
    """Riesgo entre ACT vs umbral de incertidumbre (resto de umbrales fijos). Descriptivo: no elige nada."""
    from .reject import DecisionThresholds

    unk = np.asarray(is_unknown, bool)
    success = (out["label"] == true_names) & ~unk & (circular_error(out["angle"], true_angles) <= ANGLE_TOLERANCE_DEG)
    rows = []
    for u in grid:
        t = DecisionThresholds(th.min_class_confidence, u, th.unknown_distance)
        act = decide_batch(out["nn_distance"], out["confidence"], out["angle_unc"], out["ambiguous"], t) == "ACT"
        c = _ci((act & ~success).sum(), act.sum())
        rows.append({"max_angle_uncertainty_deg": u, "coverage": float(act.mean()), **{f"risk_{k}": v for k, v in c.items()}})
    return rows


def evaluate_recipe(protocol: str, data: dict, feature_key: str = "classic", recipe: dict = RECIPE,
                    out_dir=None) -> dict:
    ds = data["ds"]
    names, angles, ids, X = data["names"], ds.angles, ds.image_ids, data[feature_key]
    sp = protocol_split(ds.labels, ds.angles, protocol)
    vs = validation_split(ds.labels, ds.angles, protocol)
    tr, te = sp["train"], sp["test"]
    te_unk = sp.get("test_is_unknown", np.zeros(len(te), bool))
    t0 = time.time()
    cal = calibrate_recipe(X, names, angles, ids, vs["fit"], vs["val"], vs.get("val_is_unknown"), recipe)
    m = VisionModel(recipe["clf"], recipe["params"], n_pca=recipe["n_pca"], k_angle_refine=cal["k"])
    m.fit(X[tr], names[tr], angles[tr], ids[tr])
    m.symmetric_classes = sorted(set(m.symmetric_classes) | set(cal["flagged"]))
    out = m.predict(X[te], top_k=0)
    th = cal["thresholds"]
    res = {"protocol": protocol, "feature": feature_key, "recipe": recipe["name"], "n_train": int(len(tr)),
           "n_test": int(len(te)), "n_fit": int(len(vs["fit"])), "n_val": int(len(vs["val"])),
           "k_selected_on_val": cal["k"], "k_table_val": cal["k_table"], "flagged_classes_val": cal["flagged"],
           "thresholds": th.to_dict(), "threshold_validation": {k: v for k, v in cal["threshold_info"].items() if k != "grid"},
           "val_uncertainty_grid": cal["threshold_info"]["grid"]}
    kn = ~te_unk
    if kn.any():
        res.update(evaluate_predictions({k: out[k][kn] for k in ("label", "angle")}, names[te][kn], angles[te][kn]))
        corr = (out["label"] == names[te])[kn]
        res["ece"], _ = expected_calibration_error(out["confidence"][kn], corr)
        succ = corr & (circular_error(out["angle"], angles[te])[kn] <= ANGLE_TOLERANCE_DEG)
        res["aurc_confidence"] = risk_coverage(out["confidence"][kn], succ)["aurc"]
    dr, dec = decision_report(out, names[te], angles[te], th, te_unk)
    res["decision"] = dr
    res["automation_enabled"] = bool(cal["threshold_info"]["automation_enabled"])
    res["test_uncertainty_sweep_descriptive"] = uncertainty_sweep(out, names[te], angles[te], th, te_unk)
    if te_unk.any() and kn.any():
        from scipy.stats import rankdata

        res["auroc_nn_distance"] = auroc(out["nn_distance"], te_unk)
        res["auroc_neg_confidence"] = auroc(-out["confidence"], te_unk)
        res["auroc_combined_rank"] = auroc(rankdata(out["nn_distance"]) + rankdata(-out["confidence"]), te_unk)
    res["elapsed_s"] = time.time() - t0
    if out_dir is not None:
        np.savez(out_dir / f"recipe_{feature_key}_{protocol}.npz", label=out["label"], confidence=out["confidence"],
                 nn_distance=out["nn_distance"], angle=out["angle"], angle_unc=out["angle_unc"],
                 ambiguous=out["ambiguous"], decision=dec, true_label=names[te], true_angle=angles[te],
                 is_unknown=te_unk)
    return res


def baseline_protocol(protocol: str, data: dict) -> dict:
    """Línea base 1-NN en píxeles crudos: SIEMPRE actúa. Los desconocidos del test cuentan como error entre ACT."""
    from sklearn.neighbors import NearestNeighbors

    ds = data["ds"]
    names, angles, Xr = data["names"], ds.angles, data["raw"]
    sp = protocol_split(ds.labels, ds.angles, protocol)
    tr, te = sp["train"], sp["test"]
    unk = sp.get("test_is_unknown", np.zeros(len(te), bool))
    nn = NearestNeighbors(n_neighbors=1).fit(Xr[tr])
    t = time.perf_counter()
    d, j = nn.kneighbors(Xr[te])
    mean_ms = (time.perf_counter() - t) / len(te) * 1000
    j = tr[j[:, 0]]
    pred, pang = names[j], angles[j].astype(float)
    correct = (pred == names[te]) & ~unk
    success = correct & (circular_error(pang, angles[te]) <= ANGLE_TOLERANCE_DEG)
    r = {"protocol": protocol, "n_test": int(len(te)), "coverage_act": 1.0,
         "error_among_act": _ci((~success).sum(), len(te)),
         "mean_ms_per_image_in_batch": mean_ms}
    if (~unk).any():
        r.update(evaluate_predictions({"label": pred[~unk], "angle": pang[~unk]}, names[te][~unk], angles[te][~unk]))
    if unk.any():
        r["unknown_reaches_act"] = _ci(unk.sum(), unk.sum())
        if (~unk).any():
            r["auroc_nn_distance"] = auroc(d[:, 0], unk)
    return r
