"""Utilidades de seguridad deterministas: detección de inyección, enmascarado de PII, truncado."""
from __future__ import annotations

import json
import re
import unicodedata

_INJECTION_PATTERNS = [
    r"ignora\w* (todas )?(tus|las) (instrucciones|pol[ií]ticas|reglas)",
    r"ignore (all |your |previous )+instructions",
    r"system override",
    r"eres (el )?administrador",
    r"apru[eé]balo t[uú] mismo",
    r"sin pedir aprobaci[oó]n",
    r"you are now",
]
_INJ = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"\+?\d[\d\s-]{8,}\d")


def looks_like_injection(text: str | None) -> bool:
    return bool(text) and bool(_INJ.search(text))


def mask_person(name: str | None) -> str | None:
    """'Carlos Pérez' -> 'C. P***' (los nombres de operarios nunca llegan completos al LLM)."""
    if not name:
        return name
    if name.startswith("u_"):
        return name  # identificadores de usuario del sistema (no PII directa)
    parts = name.split()
    return " ".join([parts[0][0] + "."] + [p[0] + "***" for p in parts[1:]]) if parts else name


def redact(text: str) -> str:
    text = _EMAIL.sub("[email]", text)
    return _PHONE.sub("[teléfono]", text)


def wrap_untrusted(text: str) -> dict:
    """Marca texto libre de terceros (tickets, notas) como DATO, nunca instrucción."""
    return {"untrusted_text": redact(text), "possible_prompt_injection": looks_like_injection(text)}


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def truncate_json(obj, max_chars: int) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 60] + f'... [truncado: {len(s)} caracteres en total]"'
