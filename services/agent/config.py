"""Carga de configuración (YAML en configs/ + variables de entorno). Sin secretos en código."""
from __future__ import annotations

import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIGS = ROOT / "configs"


def _load_dotenv() -> None:
    """Carga .env (si existe) sin sobreescribir variables ya definidas. Nunca imprime valores."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


@lru_cache
def load_yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONFIGS / name).read_text())


def agent_cfg() -> dict[str, Any]:
    return load_yaml("agent.yaml")


def policy_cfg() -> dict[str, Any]:
    return load_yaml("policy.yaml")


def roles_cfg() -> dict[str, Any]:
    return load_yaml("roles.yaml")


def pricing_cfg() -> dict[str, Any]:
    return load_yaml("llm_pricing.yaml")


def plant_db_path() -> Path:
    c = agent_cfg()["plant"]
    p = os.environ.get(c["db_path_env"]) or c["default_db_path"]
    p = Path(p)
    return p if p.is_absolute() else ROOT / p


def checkpoint_db_path() -> Path:
    c = agent_cfg()["plant"]
    p = Path(os.environ.get(c["checkpoint_db_env"]) or c["default_checkpoint_db"])
    return p if p.is_absolute() else ROOT / p


def uploads_dir() -> Path:
    p = Path(os.environ.get("AGENT_UPLOADS_DIR") or agent_cfg()["plant"]["uploads_dir"])
    return p if p.is_absolute() else ROOT / p


def sim_now() -> datetime:
    """Reloj simulado de planta: hace reproducibles las consultas ("turno 2", "3 a. m.")."""
    return datetime.fromisoformat(os.environ.get("PLANT_SIM_NOW") or agent_cfg()["plant"]["sim_now"])


def real_now() -> datetime:
    """Reloj de SEGURIDAD (tiempo real): expiración de cargas, aprobaciones, tokens y memoria."""
    return datetime.now().replace(microsecond=0)


def vision_bound_plants() -> list[str]:
    return list(agent_cfg()["vision"].get("bound_plants") or [])


def vision_base_url() -> str:
    c = agent_cfg()["vision"]
    return os.environ.get(c["base_url_env"]) or c["default_base_url"]


def vision_admin_token() -> str | None:
    # VISION_ADMIN_TOKEN (agente) o ADMIN_TOKEN (mismo .env que usa services/vision_api)
    return os.environ.get(agent_cfg()["vision"]["admin_token_env"]) or os.environ.get("ADMIN_TOKEN")


def llm_provider() -> str:
    c = agent_cfg()["llm"]
    return os.environ.get(c["provider_env"]) or c["default_provider"]


def llm_model() -> str:
    c = agent_cfg()["llm"]
    return os.environ.get(c["model_env"]) or c["default_model"]


def limits() -> dict[str, Any]:
    return agent_cfg()["limits"]
