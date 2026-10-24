"""Entrena y exporta el modelo productivo de visión con TODAS las vistas de los 100 objetos.

Uso:  uv run python scripts/train_vision.py [--version vision-1.0.2] [--out models/] [--force]

1. Descarga/verifica el dataset si falta (SHA-256) y calcula el hash de versión de datos.
2. Aplica la RECETA CONGELADA (src/vision/recipe.py): k angular y umbrales elegidos en una validación
   (90 objetos con paso 10° + 10 pseudo-desconocidos, semilla 43).
3. Re-entrena con las 7.200 vistas y exporta models/<version>/ (versión por defecto: configs/vision.yaml;
   nunca sobrescribe una versión existente salvo --force) (model.joblib + metadata.json con SHA-256 del
   modelo, commit git, versiones de librerías) y lo registra en MLflow (incluido model.joblib).
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

from src.vision.data import (DEFAULT_DATA_DIR, SEED, data_version_hash, ensure_dataset,  # noqa: E402
                             production_validation_split, sha256_file)
from src.vision.experiments import load_all  # noqa: E402
from src.vision.features import FeatureConfig  # noqa: E402
from src.vision.inference import VisionPredictor  # noqa: E402
from src.vision.models import VisionModel  # noqa: E402
from src.vision.quality import calibrate_quality  # noqa: E402
from src.vision.recipe import RECIPE, calibrate_recipe  # noqa: E402


def _git_info() -> dict:
    import subprocess

    def run(*a):
        try:
            return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:  # noqa: BLE001
            return ""

    scope = ["src/vision", "scripts/train_vision.py", "scripts/build_vision_notebook.py",
             "scripts/build_vision_report.py", "configs/vision.yaml", "services/vision_api"]
    def paths(out):
        return [ln.split(maxsplit=1)[1] for ln in out.splitlines() if ln.strip()]

    vision_dirty = paths(run("status", "--porcelain", "--", *scope))
    other_dirty = [x for x in paths(run("status", "--porcelain")) if x not in vision_dirty]
    return {"commit": run("rev-parse", "HEAD") or "unknown", "dirty": bool(vision_dirty),
            "dirty_scope": scope, "dirty_vision_paths": vision_dirty, "n_unrelated_dirty_paths": len(other_dirty),
            "note": "dirty se evalúa solo sobre el código de visión; los demás cambios sin commit (agente) no afectan al artefacto"}


def _code_snapshot(path: Path) -> tuple[Path, str]:
    """Tarball determinista del código que produce el artefacto (src/vision, scripts de visión, configs)."""
    import hashlib
    import io
    import tarfile

    files = sorted(list((ROOT / "src" / "vision").glob("*.py")) + list((ROOT / "scripts").glob("*vision*.py")) +
                   [ROOT / "configs" / "vision.yaml"])
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=9) as tf:
        for f in files:
            data = f.read_bytes()
            ti = tarfile.TarInfo(str(f.relative_to(ROOT)))
            ti.size, ti.mtime, ti.mode = len(data), 0, 0o644
            tf.addfile(ti, io.BytesIO(data))
    path.write_bytes(buf.getvalue())
    content_hash = hashlib.sha256(b"".join(hashlib.sha256(f.read_bytes()).digest() + str(f.relative_to(ROOT)).encode()
                                           for f in files)).hexdigest()
    return path, content_hash


def _lib_versions() -> dict:
    import importlib.metadata as md
    import platform

    out = {"python": platform.python_version()}
    for p in ("numpy", "scikit-learn", "scikit-image", "opencv-python-headless", "joblib", "scipy", "mlflow"):
        try:
            out[p] = md.version(p)
        except md.PackageNotFoundError:
            pass
    return out


def main(argv=None) -> Path:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "configs" / "vision.yaml"))
    ap.add_argument("--version", default=None)
    ap.add_argument("--out", default=str(ROOT / "models"))
    ap.add_argument("--force", action="store_true",
                    help="permite sobrescribir una versión existente (solo para regenerar una versión NO publicada)")
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(args.config).read_text())
    version = args.version or cfg["version"]
    np.random.seed(SEED)

    ensure_dataset(DEFAULT_DATA_DIR)
    dhash = data_version_hash(DEFAULT_DATA_DIR)
    fcfg = FeatureConfig.from_dict(cfg.get("feature_config") or {}) if cfg.get("feature_config") else FeatureConfig()
    data = load_all(fcfg)
    ds, names, X = data["ds"], data["names"], data["classic"]

    recipe = RECIPE
    results_path = ROOT / "docs" / "report" / "results.json"
    recipe_metrics = {}
    if results_path.exists():
        recipe_metrics = json.loads(results_path.read_text()).get("recipe_summary", {})
    print(f"Receta congelada: {recipe['name']} ({recipe['clf']} {recipe['params']}, PCA {recipe['n_pca']})")

    # pasos 2–4 de la receta con la validación de producción (90 conocidos paso 10° + 10 pseudo-desconocidos)
    vs = production_validation_split(ds.labels, ds.angles)
    X_aug, X_pert = data["classic_aug"], data["classic_pert"]
    cal = calibrate_recipe(X, names, ds.angles, ds.image_ids, vs["fit"], vs["val"], vs["val_is_unknown"], recipe,
                           X_aug=X_aug, X_pert=X_pert)
    th, info = cal["thresholds"], {k: v for k, v in cal["threshold_info"].items() if k != "grid"}
    print("k (validación):", cal["k"], "| clases con ángulo poco fiable:", cal["flagged"])
    print("Umbrales (validación):", th.to_dict(), info)

    # paso 5: modelo final con todo
    t = time.time()
    final = VisionModel(recipe["clf"], recipe["params"], n_pca=recipe["n_pca"], k_angle_refine=cal["k"])
    allix = np.arange(len(X))
    final.fit(np.vstack([X, X_aug]), np.concatenate([names, names]), np.concatenate([ds.angles, ds.angles]),
              np.concatenate([ds.image_ids, ds.image_ids]))
    loo_symmetric = list(final.symmetric_classes)
    final.symmetric_classes = sorted(set(final.symmetric_classes) | set(cal["flagged"]))
    print(f"Entrenamiento final: {time.time() - t:.1f}s; clases simétricas: {final.symmetric_classes}")
    qth = calibrate_quality(ds.images[::7])
    git = _git_info()
    versions = _lib_versions()
    meta = {
        "version": version,
        "stage": "production",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data_version_hash": dhash,
        "dataset": "COIL-100 (7.200 vistas, 100 objetos, paso 5°)",
        "seed": SEED,
        "recipe": recipe,
        "model_family": "clásico: HSV+HOG+LBP+Hu → estandarización → PCA → " + recipe["clf"],
        "classifier": {"name": recipe["clf"], "params": recipe["params"],
                       "selection_source": "receta congelada (src/vision/recipe.py), elegida en la exploración por validación"},
        "n_pca": recipe["n_pca"],
        "k_angle_refine": cal["k"],
        "k_table_val": cal["k_table"],
        "feature_config": fcfg.to_dict(),
        "thresholds": th.to_dict(),
        "threshold_validation": {**info, "pseudo_unknown_objects": [f"obj{o}" for o in vs["pseudo_unknown"]]},
        "quality_thresholds": qth.to_dict(),
        "angle_tolerance_deg": cfg["angle_tolerance_deg"],
        "classes": [str(c) for c in final.classes_],
        "symmetric_classes": final.symmetric_classes,
        "symmetric_classes_sources": {"loo_twin_view": loo_symmetric, "validation_angle_unreliable": cal["flagged"]},
        "symmetry_scores": {str(k): (None if not np.isfinite(v) else round(float(v), 4))
                            for k, v in final.symmetry_scores_.items()},
        "metrics_note": ("El artefacto usa las 7.200 vistas (+1 copia aumentada) y no tiene test propio. Las cifras "
                         "independientes son las de la selección ANIDADA por protocolo (results.json → recipe.nested); "
                         "las de esta configuración desplegada (results.json → recipe.results.classic) tienen familia/C "
                         "elegidos con validación agregada que se solapa con tests de otros protocolos."),
        "changelog": ["1.0.0: receta inicial", "1.0.1: preprocesado robusto a ruido (mediana 3×3 + supresión de fondo), "
                      "aumentación en train, umbrales calibrados con validación perturbada, criterio de falsa "
                      "aceptación de desconocidos ≤5 %",
                      "1.0.2: 2ª compuerta de novedad (distancia a la clase / espaciado típico de la clase, percentil 99 "
                      "de conocidos en validación) contra desconocidos casi duplicados"],
        "metrics_summary": recipe_metrics,
        "git": git,
        "library_versions": versions,
    }
    target = Path(args.out) / version
    if target.exists() and not args.force:
        raise SystemExit(f"{target} ya existe: las versiones publicadas no se sobrescriben (use --force solo para "
                         "regenerar una versión no publicada, o cambie --version)")
    target.parent.mkdir(parents=True, exist_ok=True)
    snap, snap_sha = _code_snapshot(target.parent / f".{version}.code.tar.gz")
    meta["code_snapshot_sha256"] = snap_sha
    pred = VisionPredictor(final, meta)
    out_dir = pred.save(target)
    final_snap = out_dir / "code_snapshot.tar.gz"
    snap.replace(final_snap)
    model_sha = pred.metadata["model_sha256"]
    print(f"Artefacto exportado en {out_dir} ({pred.metadata['artifact_size_mb']} MB, sha256 {model_sha[:16]}…)")
    try:  # trazabilidad en MLflow (mismo store que los experimentos)
        import mlflow

        from src.vision.tracking import setup_mlflow

        setup_mlflow()
        with mlflow.start_run(run_name=f"train-{version}"):
            mlflow.set_tags({"stage": "production", "version": version, "git_commit": git["commit"],
                             "git_dirty": str(git["dirty"]), "model_sha256": model_sha, "recipe": recipe["name"]})
            mlflow.log_params({"classifier": recipe["clf"], "params": json.dumps(recipe["params"]), "n_pca": recipe["n_pca"],
                               "k_angle_refine": cal["k"], "data_version_hash": dhash, "seed": SEED,
                               "model_sha256": model_sha, "git_commit": git["commit"], "git_dirty": git["dirty"],
                               **{f"th.{k}": v for k, v in th.to_dict().items()},
                               **{f"lib.{k}": v for k, v in versions.items()}})
            mlflow.log_metrics({"val_coverage": info["val_coverage"], "val_n_act": info["val_n_act"],
                                "val_error_among_act": info["val_error_among_act"] or 0.0,
                                "artifact_size_mb": pred.metadata["artifact_size_mb"]})
            mlflow.log_artifact(str(out_dir / "metadata.json"))
            mlflow.log_artifact(str(out_dir / "model.joblib"))
            mlflow.log_artifact(str(final_snap))
            mlflow.set_tag("code_snapshot_sha256", snap_sha)
    except Exception as e:  # noqa: BLE001
        print("MLflow no disponible:", e)
    return out_dir


if __name__ == "__main__":
    main()
