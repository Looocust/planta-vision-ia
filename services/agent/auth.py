"""Identidad verificada (demo): tokens bearer firmados con HMAC-SHA256.

- El secreto viene de AUTH_SECRET (env/.env). Si falta, se genera uno efímero por proceso (los tokens dejan de
  valer al reiniciar) y se avisa en el log.
- POST /v1/auth/demo-login solo existe si DEMO_LOGIN=true (entorno de desarrollo/demo): emite un token para un
  usuario del directorio simulado (configs/roles.yaml).
- Producción: este módulo se reemplaza por validación de tokens OIDC de Entra ID (firma JWKS, audiencia, emisor,
  expiración) y el rol/plantas salen de los claims (grupos/app roles), nunca del cuerpo de la petición.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass

from services.agent.config import roles_cfg

log = logging.getLogger(__name__)
_EPHEMERAL = secrets.token_hex(32)
TOKEN_TTL_S = 8 * 3600


class AuthFailure(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Identity:
    user_id: str
    role: str
    plants: tuple[str, ...]


def _secret() -> bytes:
    s = os.environ.get("AUTH_SECRET")
    if not s:
        log.warning("AUTH_SECRET no definido: usando secreto efímero (solo demo)")
        s = _EPHEMERAL
    return s.encode()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def demo_login_enabled() -> bool:
    return os.environ.get("DEMO_LOGIN", "false").lower() in ("1", "true", "yes")


def issue_token(user_id: str, ttl_s: int = TOKEN_TTL_S, now: float | None = None) -> dict:
    u = roles_cfg()["users"].get(user_id)
    if u is None:
        raise AuthFailure("usuario desconocido", 404)
    exp = int((now or time.time()) + ttl_s)
    payload = {"sub": user_id, "role": u["role"], "plants": u["plants"], "exp": exp}
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return {"token": f"{body}.{sig}", "user_id": user_id, "role": u["role"], "plants": u["plants"], "expires_at": exp}


def verify_token(token: str | None, now: float | None = None) -> Identity:
    if not token:
        raise AuthFailure("falta el token (Authorization: Bearer ...)")
    try:
        body, sig = token.split(".")
        expected = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            raise AuthFailure("firma de token inválida")
        p = json.loads(_unb64(body))
    except AuthFailure:
        raise
    except Exception:
        raise AuthFailure("token mal formado")
    if p.get("exp", 0) < (now or time.time()):
        raise AuthFailure("token expirado")
    # el directorio manda: si el usuario fue dado de baja o cambió de rol, el token deja de valer
    u = roles_cfg()["users"].get(p.get("sub"))
    if u is None or u["role"] != p.get("role"):
        raise AuthFailure("identidad del token ya no es válida")
    return Identity(p["sub"], u["role"], tuple(u["plants"]))


def bearer(header: str | None) -> str | None:
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return None
