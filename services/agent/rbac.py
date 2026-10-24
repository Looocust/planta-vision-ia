"""Autorización determinista (RBAC + alcance de planta). El LLM nunca decide permisos."""
from __future__ import annotations

from services.agent.config import roles_cfg


class AuthError(Exception):
    pass


def authenticate(user_id: str, role: str, plant_id: str) -> None:
    """Simula el IdP: el usuario debe existir, el rol declarado coincidir y la planta estar autorizada."""
    users = roles_cfg()["users"]
    u = users.get(user_id)
    if u is None:
        raise AuthError(f"usuario desconocido: {user_id}")
    if u["role"] != role:
        raise AuthError(f"el rol declarado '{role}' no coincide con el rol registrado de {user_id}")
    if plant_id not in u["plants"]:
        raise AuthError(f"{user_id} no está autorizado para la planta {plant_id}")


def tool_allowed(role: str, tool: str) -> bool:
    r = roles_cfg()["roles"].get(role)
    return bool(r) and tool in r["tools"]


def can_request(role: str, operation: str) -> bool:
    r = roles_cfg()["roles"].get(role)
    return bool(r) and operation in r["can_request"]


def can_approve(role: str, operation: str) -> bool:
    r = roles_cfg()["roles"].get(role)
    return bool(r) and operation in r["can_approve"]


def approver_roles(operation: str) -> list[str]:
    return [name for name, r in roles_cfg()["roles"].items() if operation in r["can_approve"]]


def allowed_tools(role: str) -> list[str]:
    r = roles_cfg()["roles"].get(role)
    return list(r["tools"]) if r else []
