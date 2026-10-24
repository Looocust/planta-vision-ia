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
from .reject import (ANGLE_TOLERANCE_DEG, auroc, decide_batch, decide_out, expected_calibration_error, risk_coverage,
                     select_thresholds, unreliable_angle_classes, wilson_interval)

RECIPE = {
    "name": "classic-logreg-v2",
    "features": "HSV(16x4x4)+HOG(64px,9or,8x8)+LBP(u8,2x2)+Hu → StandardScaler → PCA(150)",
    "clf": "logreg",
    "params": {"C": 1.0},
    "n_pca": 150,
    "k_candidates": [1, 2, 3],
    "target_error": 0.02,
    "min_act": 50,
    "unreliable_max_fail_rate": 0.25,
    "novelty_gate_2": "razón distancia-a-la-clase / espaciado típico de la clase ≤ percentil 99 de conocidos en validación",
    "preprocessing": "mediana 3×3 + máscara (Gauss 5×5, gris>40, apertura 3×3) + fondo a 0",
    "augment": True,
    "ratio_gate": True,
    "augmentation": "1 copia por vista de train: ruido σ~U(0,6), brillo ×U(0.8,1.2), blur σ~U(0.5,1) con p=0.3; "
                    "validación = vistas limpias + 1 copia perturbada con la misma distribución",
}


def _stack(X, idx, bank=None):
    return X[idx] if bank is None else np.vstack([X[idx], bank[idx]])


def _tile(a, idx, bank=None):
    return a[idx] if bank is None else np.concatenate([a[idx], a[idx]])


def calibrate_recipe(X, names, angles, ids, fit, val, val_unk=None, recipe: dict = RECIPE,
                     X_aug=None, X_pert=None) -> dict:
    """Pasos 2–4 de la receta usando solo (fit, val) ⊂ train. Devuelve k, clases marcadas y umbrales.

    X_aug: banco de características de copias aumentadas (solo se indexan vistas de `fit`).
    X_pert: banco de copias perturbadas (solo se indexan vistas de `val`): la validación usa vistas limpias +
    perturbadas, para que los umbrales reflejen condiciones realistas de sensor.
    """
    m = VisionModel(recipe["clf"], recipe["params"], n_pca=recipe["n_pca"])
    m.fit(_stack(X, fit, X_aug), _tile(names, fit, X_aug), _tile(angles, fit, X_aug), _tile(ids, fit, X_aug))
    Xv, nv, av = _stack(X, val, X_pert), _tile(names, val, X_pert), _tile(angles, val, X_pert)
    unk_v = None if val_unk is None else _tile(np.asarray(val_unk, bool), np.arange(len(val)), X_pert)
    kn = np.ones(len(nv), bool) if unk_v is None else ~unk_v
    k_table, outs = [], {}
    for k in recipe["k_candidates"]:
        m.k_angle_refine = k
        out = m.predict(Xv, top_k=0)
        ok = (out["label"] == nv) & kn
        err = circular_error(out["angle"], av)[ok]
        k_table.append({"k": k, "val_within_10": float(np.mean(err <= ANGLE_TOLERANCE_DEG)) if ok.any() else 0.0,
                        "val_mae_deg": float(err.mean()) if ok.any() else float("nan")})
        outs[k] = out
    best = max(k_table, key=lambda r: (round(r["val_within_10"], 4), -r["val_mae_deg"], -r["k"]))
    k = best["k"]
    out = outs[k]
    err = circular_error(out["angle"], av)
    flagged = unreliable_angle_classes(nv[kn], out["label"][kn], err[kn], recipe["unreliable_max_fail_rate"])
    amb = out["ambiguous"] | np.isin(out["label"], flagged)
    correct = (out["label"] == nv) & kn
    th, info = select_thresholds(correct, err, out["confidence"], out["angle_unc"], amb, out["nn_distance"],
                                 is_unknown=unk_v, target_error=recipe["target_error"], min_act=recipe["min_act"],
                                 novelty_ratio=out["novelty_ratio"], ratio_gate=recipe.get("ratio_gate", False))
    succ = correct & (err <= ANGLE_TOLERANCE_DEG)
    ece, _ = expected_calibration_error(out["confidence"][kn], correct[kn])
    return {"k": k, "k_table": k_table, "flagged": flagged, "thresholds": th, "threshold_info": info,
            "val_perturbed": X_pert is not None, "train_augmented": X_aug is not None,
            "val_class_accuracy": float(correct[kn].mean()), "val_end_to_end": float(succ[kn].mean()),
            "val_ece": float(ece)}


def _ci(k, n):
    lo, hi = wilson_interval(int(k), int(n))
    return {"rate": (k / n) if n else None, "k": int(k), "n": int(n), "wilson95": [lo, hi]}


def decision_report(out, true_names, true_angles, th, is_unknown) -> dict:
    unk = np.asarray(is_unknown, bool)
    correct = (out["label"] == true_names) & ~unk
    success = correct & (circular_error(out["angle"], true_angles) <= ANGLE_TOLERANCE_DEG)
    dec = decide_out(out, th)
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
        t = DecisionThresholds(th.min_class_confidence, u, th.unknown_distance, th.max_novelty_ratio)
        act = decide_out(out, t) == "ACT"
        c = _ci((act & ~success).sum(), act.sum())
        rows.append({"max_angle_uncertainty_deg": u, "coverage": float(act.mean()), **{f"risk_{k}": v for k, v in c.items()}})
    return rows


def evaluate_recipe(protocol: str, data: dict, feature_key: str = "classic", recipe: dict = RECIPE,
                    out_dir=None, robustness: bool = False, cal: dict | None = None, tag: str = "recipe") -> dict:
    ds = data["ds"]
    names, angles, ids, X = data["names"], ds.angles, ds.image_ids, data[feature_key]
    sp = protocol_split(ds.labels, ds.angles, protocol)
    vs = validation_split(ds.labels, ds.angles, protocol)
    tr, te = sp["train"], sp["test"]
    te_unk = sp.get("test_is_unknown", np.zeros(len(te), bool))
    t0 = time.time()
    X_aug = data.get(f"{feature_key}_aug") if recipe.get("augment", True) else None
    X_pert = data.get(f"{feature_key}_pert") if recipe.get("augment", True) else None
    if cal is None:
        cal = calibrate_recipe(X, names, angles, ids, vs["fit"], vs["val"], vs.get("val_is_unknown"), recipe,
                               X_aug=X_aug, X_pert=X_pert)
    m = VisionModel(recipe["clf"], recipe["params"], n_pca=recipe["n_pca"], k_angle_refine=cal["k"])
    m.fit(_stack(X, tr, X_aug), _tile(names, tr, X_aug), _tile(angles, tr, X_aug), _tile(ids, tr, X_aug))
    m.symmetric_classes = sorted(set(m.symmetric_classes) | set(cal["flagged"]))
    out = m.predict(X[te], top_k=0)
    th = cal["thresholds"]
    res = {"protocol": protocol, "feature": feature_key, "recipe": recipe["name"], "clf": recipe["clf"],
           "params": recipe["params"], "n_train": int(len(tr)),
           "n_test": int(len(te)), "n_fit": int(len(vs["fit"])), "n_val": int(len(vs["val"])),
           "k_selected_on_val": cal["k"], "k_table_val": cal["k_table"], "flagged_classes_val": cal["flagged"],
           "thresholds": th.to_dict(), "threshold_validation": {k: v for k, v in cal["threshold_info"].items() if k != "grid"},
           "val_uncertainty_grid": cal["threshold_info"]["grid"], "train_augmented": cal["train_augmented"],
           "val_perturbed": cal["val_perturbed"]}
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
    if robustness and feature_key == "classic":
        res["robustness"] = robustness_eval(m, th, protocol, te, te_unk, data)
    res["elapsed_s"] = time.time() - t0
    if out_dir is not None:
        np.savez(out_dir / f"{tag}_{feature_key}_{protocol}.npz", label=out["label"], confidence=out["confidence"],
                 nn_distance=out["nn_distance"], novelty_ratio=out["novelty_ratio"], angle=out["angle"], angle_unc=out["angle_unc"],
                 ambiguous=out["ambiguous"], decision=dec, true_label=names[te], true_angle=angles[te],
                 is_unknown=te_unk)
    return res


def robustness_eval(model, th, protocol, te, te_unk, data) -> dict:
    """Re-evalúa el modelo YA AJUSTADO (mismos umbrales) sobre el test perturbado. Semillas fijas; cache en disco."""
    import json

    from .augment import ROBUSTNESS_SUITE, apply
    from .data import PROJECT_ROOT
    from .features import extract_features

    ds, names = data["ds"], data["names"]
    cfg = data["feature_cfg"]
    cache_dir = PROJECT_ROOT / "data" / "runtime" / "robustness"
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_all = {}
    for i, (name, (kind, level)) in enumerate(ROBUSTNESS_SUITE.items()):
        if kind == "none":
            Xp = data["classic"][te]
        else:
            fp = cache_dir / f"{protocol}_{kind}_{level}.npz"
            if fp.exists() and json.loads(str(np.load(fp)["cfg"])) == cfg.to_dict():
                Xp = np.load(fp)["X"]
            else:
                rng = np.random.default_rng(7000 + i)
                imgs = [apply(ds.images[j], kind, level, rng) for j in te]
                from joblib import Parallel, delayed

                Xp = np.stack(Parallel(n_jobs=8)(delayed(extract_features)(im, cfg) for im in imgs))
                np.savez(fp, X=Xp, cfg=json.dumps(cfg.to_dict()))
        out = model.predict(Xp, top_k=0)
        dr, _ = decision_report(out, names[te], ds.angles[te], th, te_unk)
        row = {"kind": kind, "level": level, "coverage_act": dr["coverage_act"], "n_act": dr["n_act"],
               "error_among_act": dr["error_among_act"]}
        kn = ~te_unk
        if kn.any():
            ev = evaluate_predictions({k: out[k][kn] for k in ("label", "angle")}, names[te][kn], ds.angles[te][kn])
            row.update(class_accuracy=ev["class_accuracy"], angle_mae_deg=ev["angle_mae_deg"],
                       end_to_end_success=ev["end_to_end_success"],
                       reject_rate_known=float(np.mean(decide_out(out, th)[kn] == "REJECT")))
        if te_unk.any():
            row["unknown_reaches_act"] = dr["unknown_reaches_act"]
            row["unknown_rejected"] = dr["unknown_rejected"]
        out_all[name] = row
    return out_all


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


def _log_run(mlflow, name, r, p, feat, dhash, extra_tags=None):
    import json

    from .data import SEED

    with mlflow.start_run(run_name=name):
        mlflow.set_tags({"protocol": p, "feature": feat, **(extra_tags or {})})
        mlflow.log_params({"protocol": p, "feature": feat, "data_version_hash": dhash, "seed": SEED,
                           "clf": r["clf"], "params": json.dumps(r["params"]), "k_selected_on_val": r["k_selected_on_val"],
                           **{f"th.{k}": v for k, v in r["thresholds"].items()}})
        d = r["decision"]
        met = {"coverage_act": d["coverage_act"], "n_act": d["n_act"], "review_rate": d["review_rate"],
               "reject_rate": d["reject_rate"]}
        if d["error_among_act"]["rate"] is not None:
            met["error_among_act"] = d["error_among_act"]["rate"]
        for k in ("class_accuracy", "angle_mae_deg", "angle_within_10", "end_to_end_success", "ece",
                  "auroc_combined_rank", "auroc_nn_distance", "auroc_neg_confidence"):
            if r.get(k) is not None and np.isfinite(r[k]):
                met[k] = float(r[k])
        if "unknown_reaches_act" in d:
            met["unknown_reaches_act"] = d["unknown_reaches_act"]["rate"]
            met["unknown_rejected"] = d["unknown_rejected"]["rate"]
        for name_, row in r.get("robustness", {}).items():
            tag = "rob." + "".join(ch if ch.isalnum() else "_" for ch in name_)
            met[f"{tag}.coverage_act"] = row["coverage_act"]
            if row["error_among_act"]["rate"] is not None:
                met[f"{tag}.error_among_act"] = row["error_among_act"]["rate"]
        mlflow.log_metrics(met)


def run_recipe_all(data: dict, features=("classic", "resnet18"), protocols=("A", "B", "C", "D", "D_strict"),
                   out_dir=None, mlflow_uri: str | None = None, nested: bool = True) -> dict:
    """(1) Selección anidada por protocolo (cifras independientes); (2) configuración desplegada congelada en
    cada protocolo (+ robustez en A y D estricto); (3) línea base. Todo se registra en MLflow."""
    from pathlib import Path

    import mlflow

    from .data import PROJECT_ROOT, data_version_hash

    out_dir = Path(out_dir or PROJECT_ROOT / "data" / "runtime" / "experiments")
    out_dir.mkdir(parents=True, exist_ok=True)
    from .tracking import setup_mlflow

    setup_mlflow(mlflow_uri)
    dhash = data_version_hash()
    res = {"recipe": RECIPE, "results": {}, "baselines": {}, "nested": {}}
    for p in protocols:
        res["baselines"][p] = baseline_protocol(p, data)
    if nested:
        for p in protocols:
            r = evaluate_nested(p, data, out_dir=out_dir)
            res["nested"][p] = r
            _log_run(mlflow, f"nested-classic-{p}", r, p, "classic", dhash, {"kind": "nested_selection"})
    for feat in features:
        for p in protocols:
            r = evaluate_recipe(p, data, feat, out_dir=out_dir,
                                robustness=(feat == "classic" and p in ("A", "D_strict")))
            res["results"].setdefault(feat, {})[p] = r
            _log_run(mlflow, f"deployed-{feat}-{p}", r, p, feat, dhash, {"kind": "deployed_config", "recipe": RECIPE["name"]})
    return res


NESTED_CANDIDATES = [
    {"clf": "logreg", "params": {"C": 0.1}}, {"clf": "logreg", "params": {"C": 1.0}},
    {"clf": "logreg", "params": {"C": 10.0}}, {"clf": "svm_rbf", "params": {"C": 10.0}},
    {"clf": "knn", "params": {"n_neighbors": 1}}, {"clf": "random_forest", "params": {"n_estimators": 300}},
]


def evaluate_nested(protocol: str, data: dict, candidates=NESTED_CANDIDATES, out_dir=None) -> dict:
    """Selección ANIDADA: familia, hiperparámetros, k y umbrales se eligen SOLO con la validación interna del
    train de este protocolo; luego se re-ajusta con su train y se evalúa UNA vez en su test. Es la única cifra
    que se presenta como estimación independiente.

    Criterio de selección (validación): automatización habilitada > cobertura ACT de validación > exactitud
    de clase de validación.
    """
    ds = data["ds"]
    names, angles, ids, X = data["names"], ds.angles, ds.image_ids, data["classic"]
    vs = validation_split(ds.labels, ds.angles, protocol)
    rows, cals = [], {}
    for c in candidates:
        rec = {**RECIPE, "name": f"nested-{c['clf']}", "clf": c["clf"], "params": c["params"]}
        cal = calibrate_recipe(X, names, angles, ids, vs["fit"], vs["val"], vs.get("val_is_unknown"), rec,
                               X_aug=data.get("classic_aug"), X_pert=data.get("classic_pert"))
        key = f"{c['clf']}:{c['params']}"
        cals[key] = (rec, cal)
        ti = cal["threshold_info"]
        rows.append({"candidate": key, "val_class_accuracy": cal["val_class_accuracy"], "val_ece": cal["val_ece"],
                     "val_end_to_end": cal["val_end_to_end"], "automation_enabled": ti["automation_enabled"],
                     "val_coverage": ti["val_coverage"], "val_error_among_act": ti["val_error_among_act"], "k": cal["k"]})
    best = max(rows, key=lambda r: (r["automation_enabled"], r["val_coverage"] if r["automation_enabled"] else 0.0,
                                    r["val_class_accuracy"]))
    rec, cal = cals[best["candidate"]]
    res = evaluate_recipe(protocol, data, "classic", rec, out_dir=out_dir, cal=cal, tag="nested")
    res["selection_table_val"] = rows
    res["selected_candidate"] = best["candidate"]
    return res
