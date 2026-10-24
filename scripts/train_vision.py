"""Entrena y exporta el modelo productivo de visión con TODAS las vistas de los 100 objetos.

Uso:  uv run python scripts/train_vision.py [--version vision-1.0.0] [--out models/]

1. Descarga/verifica el dataset si falta (SHA-256) y calcula el hash de versión de datos.
2. Lee el clasificador elegido en validación (docs/report/results.json → `production_selection`;
   si no existe, configs/vision.yaml).
3. Fija umbrales de decisión con una partición de validación SOLO de entrenamiento
   (90 objetos con paso 10° + 10 pseudo-desconocidos, semilla 43).
4. Re-entrena con las 7.200 vistas y exporta models/<version>/ (model.joblib + metadata.json).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.vision.angle import circular_error  # noqa: E402
from src.vision.data import DEFAULT_DATA_DIR, SEED, data_version_hash, ensure_dataset, production_validation_split  # noqa: E402
from src.vision.experiments import load_all  # noqa: E402
from src.vision.features import FeatureConfig  # noqa: E402
from src.vision.inference import VisionPredictor  # noqa: E402
from src.vision.models import VisionModel  # noqa: E402
from src.vision.quality import calibrate_quality  # noqa: E402
from src.vision.reject import select_thresholds, unreliable_angle_classes  # noqa: E402


def main(argv=None) -> Path:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "configs" / "vision.yaml"))
    ap.add_argument("--version", default=None)
    ap.add_argument("--out", default=str(ROOT / "models"))
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(args.config).read_text())
    version = args.version or cfg["version"]
    np.random.seed(SEED)

    ensure_dataset(DEFAULT_DATA_DIR)
    dhash = data_version_hash(DEFAULT_DATA_DIR)
    fcfg = FeatureConfig.from_dict(cfg.get("feature_config") or {}) if cfg.get("feature_config") else FeatureConfig()
    data = load_all(fcfg)
    ds, names, X = data["ds"], data["names"], data["classic"]

    clf_name, clf_params, source = cfg["classifier"]["name"], cfg["classifier"]["params"], "configs/vision.yaml"
    results_path = ROOT / "docs" / "report" / "results.json"
    metrics_summary = {}
    if results_path.exists():
        res = json.loads(results_path.read_text())
        if "production_selection" in res:
            clf_name = res["production_selection"]["clf"]
            clf_params = res["production_selection"]["params"]
            source = "docs/report/results.json (validación)"
        metrics_summary = res.get("summary", {})
    print(f"Clasificador: {clf_name} {clf_params} (fuente: {source})")

    # umbrales en validación de producción
    vs = production_validation_split(ds.labels, ds.angles)
    m = VisionModel(clf_name, clf_params, n_pca=cfg["n_pca"], k_angle_refine=cfg["k_angle_refine"])
    m.fit(X[vs["fit"]], names[vs["fit"]], ds.angles[vs["fit"]], ds.image_ids[vs["fit"]])
    out = m.predict(X[vs["val"]], top_k=0)
    unk = vs["val_is_unknown"]
    correct = (out["label"] == names[vs["val"]]) & ~unk
    err = circular_error(out["angle"], ds.angles[vs["val"]])
    flagged = unreliable_angle_classes(names[vs["val"]][~unk], out["label"][~unk], err[~unk])
    out["ambiguous"] = out["ambiguous"] | np.isin(out["label"], flagged)
    print("Clases con ángulo poco fiable en validación:", flagged)
    th, info = select_thresholds(correct, err, out["confidence"], out["angle_unc"], out["ambiguous"],
                                 out["nn_distance"], is_unknown=unk, target_error=cfg["target_error_among_act"])
    info.pop("grid", None)
    print("Umbrales (validación):", th.to_dict(), info)

    # modelo final con todo
    t = time.time()
    final = VisionModel(clf_name, clf_params, n_pca=cfg["n_pca"], k_angle_refine=cfg["k_angle_refine"])
    final.fit(X, names, ds.angles, ds.image_ids)
    loo_symmetric = list(final.symmetric_classes)
    final.symmetric_classes = sorted(set(final.symmetric_classes) | set(flagged))
    print(f"Entrenamiento final: {time.time() - t:.1f}s; clases simétricas: {final.symmetric_classes}")
    qth = calibrate_quality(ds.images[::7])
    meta = {
        "version": version,
        "stage": "production",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data_version_hash": dhash,
        "dataset": "COIL-100 (7.200 vistas, 100 objetos, paso 5°)",
        "seed": SEED,
        "model_family": "clásico: HSV+HOG+LBP+Hu → estandarización → PCA → " + clf_name,
        "classifier": {"name": clf_name, "params": clf_params, "selection_source": source},
        "n_pca": cfg["n_pca"],
        "feature_config": fcfg.to_dict(),
        "thresholds": th.to_dict(),
        "threshold_validation": {**info, "pseudo_unknown_objects": [f"obj{o}" for o in vs["pseudo_unknown"]]},
        "quality_thresholds": qth.to_dict(),
        "angle_tolerance_deg": cfg["angle_tolerance_deg"],
        "classes": [str(c) for c in final.classes_],
        "symmetric_classes": final.symmetric_classes,
        "symmetric_classes_sources": {"loo_twin_view": loo_symmetric, "validation_angle_unreliable": flagged},
        "symmetry_scores": {str(k): (None if not np.isfinite(v) else round(float(v), 4))
                            for k, v in final.symmetry_scores_.items()},
        "metrics_summary": metrics_summary,
    }
    pred = VisionPredictor(final, meta)
    out_dir = pred.save(Path(args.out) / version)
    print(f"Artefacto exportado en {out_dir} ({pred.metadata['artifact_size_mb']} MB)")
    try:  # trazabilidad en MLflow (mismo store que los experimentos)
        import mlflow

        mlflow.set_tracking_uri(f"sqlite:///{ROOT / 'mlflow.db'}")
        mlflow.set_experiment("vision-coil100")
        with mlflow.start_run(run_name=f"train-{version}"):
            mlflow.set_tags({"stage": "production", "version": version})
            mlflow.log_params({"classifier": clf_name, "params": json.dumps(clf_params), "n_pca": cfg["n_pca"],
                               "data_version_hash": dhash, "seed": SEED, **{f"th.{k}": v for k, v in th.to_dict().items()}})
            mlflow.log_metrics({"val_coverage": info["val_coverage"], "val_error_among_act": info["val_error_among_act"],
                                "artifact_size_mb": pred.metadata["artifact_size_mb"]})
            mlflow.log_artifact(str(out_dir / "metadata.json"))
    except Exception as e:  # noqa: BLE001
        print("MLflow no disponible:", e)
    return out_dir


if __name__ == "__main__":
    main()
