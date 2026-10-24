"""Configuración única de MLflow: store sqlite en la raíz del proyecto y artefactos en una ruta ABSOLUTA.

Antes, el artifact root era relativo al cwd y los artefactos quedaban repartidos entre ./mlruns y
./notebooks/mlruns. Las corridas nuevas van siempre a <PROJECT_ROOT>/mlruns. El experimento histórico
"vision-coil100" (con raíz relativa) se conserva intacto; las corridas nuevas van a "vision-coil100-v2".
Ver con:  uv run mlflow ui --backend-store-uri sqlite:///mlflow.db
"""
from __future__ import annotations

from .data import PROJECT_ROOT

EXPERIMENT = "vision-coil100-v2"


def setup_mlflow(uri: str | None = None):
    import mlflow

    mlflow.set_tracking_uri(uri or f"sqlite:///{PROJECT_ROOT / 'mlflow.db'}")
    root = (PROJECT_ROOT / "mlruns").resolve()
    root.mkdir(exist_ok=True)
    exp = mlflow.get_experiment_by_name(EXPERIMENT)
    if exp is None:
        mlflow.create_experiment(EXPERIMENT, artifact_location=root.as_uri())
    mlflow.set_experiment(EXPERIMENT)
    return mlflow
