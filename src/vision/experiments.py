"""Orquestación de experimentos por protocolo (validación → selección → test una sola vez).

El notebook y los scripts llaman a estas funciones; no duplican lógica.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from .angle import circular_error
from .data import PROJECT_ROOT, SEED, Dataset, load_dataset, protocol_split, validation_split
from .features import FeatureConfig, extract_batch, raw_pixels
from .models import VisionModel, evaluate_predictions
from .reject import (ANGLE_TOLERANCE_DEG, DecisionThresholds, auroc, decide_batch, expected_calibration_error,
                     risk_coverage, select_thresholds, unreliable_angle_classes)

RUNTIME = PROJECT_ROOT / "data" / "runtime"
PROTOCOLS = ("A", "B", "C", "D")

# Rejilla pequeña y explícita (todo se elige en validación)
GRID = {
    "knn": [{"n_neighbors": 1}, {"n_neighbors": 5}],
    "logreg": [{"C": 0.1}, {"C": 1.0}, {"C": 10.0}],
    "svm_rbf": [{"C": 1.0}, {"C": 10.0}],
    "random_forest": [{"n_estimators": 300}],
}


def label_names(labels: np.ndarray) -> np.ndarray:
    return np.array([f"obj{int(x)}" for x in labels])


def load_all(feature_cfg: FeatureConfig = FeatureConfig(), use_cache: bool = True) -> dict:
    """Carga imágenes y características clásicas, píxeles crudos y embeddings (cacheados en data/runtime)."""
    RUNTIME.mkdir(parents=True, exist_ok=True)
    ds = load_dataset(cache=RUNTIME / "coil100_images.npz" if use_cache else None)
    fc = RUNTIME / "classic_features.npz"
    if use_cache and fc.exists() and json.loads(str(np.load(fc)["cfg"])) == feature_cfg.to_dict():
        X = np.load(fc)["X"]
    else:
        X = extract_batch(ds.images, feature_cfg, n_jobs=8)
        np.savez(fc, X=X, cfg=json.dumps(feature_cfg.to_dict()))
    rp = raw_pixels(ds.images, 32)
    emb_path = RUNTIME / "resnet18_embeddings.npz"
    if use_cache and emb_path.exists():
        E = np.load(emb_path)["emb"]
    else:
        from .modern import resnet_embeddings

        E = resnet_embeddings(ds.images)
        np.savez(emb_path, emb=E, image_ids=ds.image_ids)
    return {"ds": ds, "names": label_names(ds.labels), "classic": X, "raw": rp, "resnet18": E}


# ---------------------------------------------------------------- utilidades de evaluación

def _decision_metrics(out, true_names, true_angles, th: DecisionThresholds, is_unknown=None) -> dict:
    n = len(true_names)
    unk = np.zeros(n, bool) if is_unknown is None else np.asarray(is_unknown, bool)
    correct = (out["label"] == true_names) & ~unk
    err = circular_error(out["angle"], true_angles)
    success = correct & (err <= ANGLE_TOLERANCE_DEG)
    dec = decide_batch(out["nn_distance"], out["confidence"], out["angle_unc"], out["ambiguous"], th)
    act = dec == "ACT"
    res = {
        "coverage_act": float(act.mean()),
        "review_rate": float((dec == "REVIEW").mean()),
        "reject_rate": float((dec == "REJECT").mean()),
        "error_among_act": float(1 - success[act].mean()) if act.any() else float("nan"),
        "wrong_class_among_act": float(1 - correct[act].mean()) if act.any() else float("nan"),
        "n_act": int(act.sum()),
        "automated_success_rate": float((act & success).mean()),
    }
    k = ~unk
    if k.any():
        res["known_coverage_act"] = float(act[k].mean())
        res["known_false_reject"] = float((dec[k] == "REJECT").mean())
    if unk.any():
        res["unknown_rejected"] = float((dec[unk] == "REJECT").mean())
        res["unknown_false_accept_act"] = float(act[unk].mean())
        res["unknown_not_act"] = float((~act[unk]).mean())
    return res, dec


def _fit_predict(Xtr, ntr, atr, idtr, Xte, cfg: dict, top_k: int = 0, extra_ambiguous=()):
    m = VisionModel(clf_name=cfg["clf"], clf_params=cfg["params"], n_pca=cfg.get("n_pca", 150),
                    k_angle_refine=cfg.get("k_angle_refine", 2))
    t = time.time()
    m.fit(Xtr, ntr, atr, idtr)
    m.symmetric_classes = sorted(set(m.symmetric_classes) | set(extra_ambiguous))
    fit_s = time.time() - t
    t = time.time()
    out = m.predict(Xte, top_k=top_k)
    pred_ms = (time.time() - t) / max(len(Xte), 1) * 1000
    return m, out, fit_s, pred_ms


def baseline_raw_1nn(Xr: np.ndarray, names, angles, tr, te) -> dict:
    """Línea base: 1-NN en píxeles crudos 32×32; ángulo = ángulo del vecino; sin rechazo (siempre ACT)."""
    from sklearn.neighbors import NearestNeighbors

    nn = NearestNeighbors(n_neighbors=1).fit(Xr[tr])
    t = time.time()
    d, j = nn.kneighbors(Xr[te])
    ms = (time.time() - t) / len(te) * 1000
    j = tr[j[:, 0]]
    out = {"label": names[j], "angle": angles[j].astype(float)}
    m = evaluate_predictions(out, names[te], angles[te])
    m["predict_ms_per_image_batch"] = ms
    return m, d[:, 0]


# ---------------------------------------------------------------- protocolo completo

def run_protocol(protocol: str, data: dict, feature_key: str = "classic", grid: dict | None = None,
                 target_error: float = 0.02, log=print) -> dict:
    """Validación (sobre train) → mejor hiperparámetro por clasificador → mejor clasificador → umbrales →
    re-entrena en train completo → evaluación ÚNICA en test de cada clasificador (selección ya fijada)."""
    grid = grid or GRID
    ds: Dataset = data["ds"]
    names, angles, ids = data["names"], ds.angles, ds.image_ids
    X = data[feature_key]
    sp = protocol_split(ds.labels, ds.angles, protocol)
    vs = validation_split(ds.labels, ds.angles, protocol)
    tr, te, fit, val = sp["train"], sp["test"], vs["fit"], vs["val"]
    val_unk = vs.get("val_is_unknown")
    te_unk = sp.get("test_is_unknown")
    res = {"protocol": protocol, "feature": feature_key, "n_train": int(len(tr)), "n_test": int(len(te)),
           "n_fit": int(len(fit)), "n_val": int(len(val)), "validation": {}, "test": {}}
    if protocol == "D":
        res["unknown_objects"] = [f"obj{o}" for o in sp["unknown_objects"]]
        res["pseudo_unknown_val"] = [f"obj{o}" for o in vs["pseudo_unknown"]]
    if protocol == "B":
        res["arc_starts"] = {f"obj{k}": v for k, v in sp["arc_starts"].items()}

    # 1) validación: mejor configuración por clasificador
    best_cfg, val_outs = {}, {}
    for clf, plist in grid.items():
        best = None
        for p in plist:
            cfg = {"clf": clf, "params": p}
            _, out, fit_s, _ = _fit_predict(X[fit], names[fit], angles[fit], ids[fit], X[val], cfg)
            known = ~val_unk if val_unk is not None else np.ones(len(val), bool)
            m = evaluate_predictions({k: out[k][known] for k in ("label", "angle")}, names[val][known], angles[val][known])
            m["fit_s"] = fit_s
            res["validation"][f"{clf}:{json.dumps(p)}"] = m
            log(f"[{protocol}/{feature_key}] val {clf} {p}: acc={m['class_accuracy']:.4f} e2e={m['end_to_end_success']:.4f}")
            key = (m["class_accuracy"], m["end_to_end_success"])
            if best is None or key > best[0]:
                best = (key, cfg, out)
        best_cfg[clf] = best[1]
        val_outs[clf] = best[2]
    # 2) mejor clasificador por exactitud de validación (desempate: éxito extremo a extremo)
    sel = max(best_cfg, key=lambda c: (res["validation"][f"{c}:{json.dumps(best_cfg[c]['params'])}"]["class_accuracy"],
                                       res["validation"][f"{c}:{json.dumps(best_cfg[c]['params'])}"]["end_to_end_success"]))
    res["selected"] = best_cfg[sel]
    res["best_per_classifier"] = best_cfg

    # 3) umbrales en validación (para cada clasificador, con su mejor config)
    thresholds, flagged = {}, {}
    for clf, out in val_outs.items():
        kn = ~val_unk if val_unk is not None else np.ones(len(val), bool)
        correct = (out["label"] == names[val]) & kn
        err = circular_error(out["angle"], angles[val])
        # clases con ángulo poco fiable en validación → ambigüedad rotacional a nivel de clase
        flagged[clf] = unreliable_angle_classes(names[val][kn], out["label"][kn], err[kn])
        out["ambiguous"] = out["ambiguous"] | np.isin(out["label"], flagged[clf])
        th, info = select_thresholds(correct, err, out["confidence"], out["angle_unc"], out["ambiguous"],
                                     out["nn_distance"], is_unknown=val_unk, target_error=target_error)
        info.pop("grid")
        thresholds[clf] = (th, info)
    res["thresholds"] = {c: {**t.to_dict(), **i} for c, (t, i) in thresholds.items()}
    res["angle_unreliable_classes"] = flagged

    # 4) test: re-entrena con todo train y evalúa una vez
    for clf, cfg in best_cfg.items():
        model, out, fit_s, pred_ms = _fit_predict(X[tr], names[tr], angles[tr], ids[tr], X[te], cfg,
                                                  extra_ambiguous=flagged[clf])
        kn = ~te_unk if te_unk is not None else np.ones(len(te), bool)
        m = evaluate_predictions({k: out[k][kn] for k in ("label", "angle")}, names[te][kn], angles[te][kn])
        th = thresholds[clf][0]
        dm, dec = _decision_metrics(out, names[te], angles[te], th, te_unk)
        m.update(dm)
        correct = (out["label"] == names[te]) & kn
        ece, rel = expected_calibration_error(out["confidence"][kn], correct[kn])
        m["ece"] = ece
        success = correct & (circular_error(out["angle"], angles[te]) <= ANGLE_TOLERANCE_DEG)
        rc = risk_coverage(out["confidence"][kn], success[kn])
        m["aurc_confidence"] = rc["aurc"]
        m["fit_s"] = fit_s
        m["predict_ms_per_image_batch"] = pred_ms
        if te_unk is not None:
            m["auroc_nn_distance"] = auroc(out["nn_distance"], te_unk)
            m["auroc_neg_confidence"] = auroc(-out["confidence"], te_unk)
            # combinación por rangos (sin parámetros)
            from scipy.stats import rankdata

            comb = rankdata(out["nn_distance"]) + rankdata(-out["confidence"])
            m["auroc_combined_rank"] = auroc(comb, te_unk)
        res["test"][clf] = m
        log(f"[{protocol}/{feature_key}] TEST {clf}: acc={m['class_accuracy']:.4f} mae={m['angle_mae_deg']:.2f} "
            f"e2e={m['end_to_end_success']:.4f} act={m['coverage_act']:.3f} errACT={m['error_among_act']:.4f}")
        if clf == sel:
            res["_selected_out"] = {k: v for k, v in out.items() if k not in ("proba", "similar")}
            res["_selected_out"]["decision"] = dec
            res["_selected_out"]["true_label"] = names[te]
            res["_selected_out"]["true_angle"] = angles[te]
            res["_selected_out"]["is_unknown"] = te_unk if te_unk is not None else np.zeros(len(te), bool)
            res["_selected_reliability"] = rel
    # 5) línea base (solo en clásicas para no repetir): siempre actúa; los desconocidos cuentan como error entre ACT
    if feature_key == "classic":
        from .recipe import baseline_protocol

        bm = baseline_protocol(protocol, data)
        bm["error_among_act_ci"] = bm["error_among_act"]
        bm["error_among_act"] = bm["error_among_act"]["rate"]
        res["baseline_raw_1nn"] = bm
        log(f"[{protocol}] BASELINE raw 1-NN: {bm}")
    return res


def strip_private(res: dict) -> dict:
    return {k: v for k, v in res.items() if not k.startswith("_")}


def to_jsonable(o):
    if isinstance(o, dict):
        return {str(k): to_jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [to_jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, DecisionThresholds):
        return o.to_dict()
    return o


# ---------------------------------------------------------------- corrida completa + MLflow

def _flat_metrics(prefix: str, d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, (int, float, np.integer, np.floating)) and np.isfinite(v):
            out[f"{prefix}{k}"] = float(v)
    return out


def run_all(data: dict | None = None, features=("classic", "resnet18"), protocols=PROTOCOLS,
            mlflow_uri: str | None = None, out_dir: Path | None = None, log=print) -> dict:
    """Corre todos los protocolos para cada representación, registra en MLflow y guarda salidas."""
    import mlflow

    from .data import data_version_hash

    data = data or load_all()
    out_dir = Path(out_dir or RUNTIME / "experiments")
    out_dir.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(mlflow_uri or f"sqlite:///{PROJECT_ROOT / 'mlflow.db'}")
    mlflow.set_experiment("vision-coil100")
    dhash = data_version_hash()
    results = {"data_version_hash": dhash, "seed": SEED, "feature_config": FeatureConfig().to_dict(),
               "grid": GRID, "protocols": {}}
    for feat in features:
        for p in protocols:
            t0 = time.time()
            r = run_protocol(p, data, feature_key=feat, log=log)
            r["elapsed_s"] = time.time() - t0
            so = r.pop("_selected_out")
            rel = r.pop("_selected_reliability")
            np.savez(out_dir / f"{feat}_{p}.npz", **{k: np.asarray(v) for k, v in so.items()})
            r["selected_reliability"] = rel
            results["protocols"].setdefault(p, {})[feat] = to_jsonable(r)
            with mlflow.start_run(run_name=f"{feat}-{p}"):
                mlflow.set_tags({"protocol": p, "feature": feat, "selected_clf": r["selected"]["clf"]})
                mlflow.log_params({"protocol": p, "feature": feat, "data_version_hash": dhash, "seed": SEED,
                                   "selected": json.dumps(r["selected"]), "n_train": r["n_train"], "n_test": r["n_test"]})
                for clf, m in r["test"].items():
                    mlflow.log_metrics(_flat_metrics(f"test.{clf}.", m))
                for k, m in r["validation"].items():
                    mlflow.log_metrics(_flat_metrics(f"val.{k.split(':')[0]}.{k.split(':')[1].replace(' ', '').replace(chr(34), '').replace('{', '').replace('}', '').replace(':', '=')}.", m))
                if "baseline_raw_1nn" in r:
                    mlflow.log_metrics(_flat_metrics("test.baseline_raw_1nn.", r["baseline_raw_1nn"]))
                tmp = out_dir / f"result_{feat}_{p}.json"
                tmp.write_text(json.dumps(to_jsonable(r), indent=1))
                mlflow.log_artifact(str(tmp))
    return results


def select_production_classifier(results: dict, feature: str = "classic") -> dict:
    """Clasificador con mejor exactitud media de VALIDACIÓN en los 4 protocolos (desempate: éxito e2e)."""
    scores = {}
    for p, rr in results["protocols"].items():
        r = rr[feature]
        for clf, cfg in r["best_per_classifier"].items():
            m = r["validation"][f"{clf}:{json.dumps(cfg['params'])}"]
            scores.setdefault(clf, []).append((m["class_accuracy"], m["end_to_end_success"], cfg["params"]))
    agg = {c: (float(np.mean([s[0] for s in v])), float(np.mean([s[1] for s in v]))) for c, v in scores.items()}
    best = max(agg, key=lambda c: agg[c])
    # hiperparámetro: el de mayor exactitud media de validación entre protocolos para ese clasificador
    per_param = {}
    for p, rr in results["protocols"].items():
        for k, m in rr[feature]["validation"].items():
            clf, params = k.split(":", 1)
            if clf == best:
                per_param.setdefault(params, []).append(m["class_accuracy"])
    params = max(per_param, key=lambda k: float(np.mean(per_param[k])))
    agg = {c: {"mean_val_class_accuracy": a, "mean_val_end_to_end": e} for c, (a, e) in agg.items()}
    agg["_per_param_mean_val_acc"] = {k: float(np.mean(v)) for k, v in per_param.items()}
    return {"clf": best, "params": json.loads(params), "mean_val": agg}
