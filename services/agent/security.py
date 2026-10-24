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


def redact_deep(obj):
    """Redacta emails/teléfonos en TODOS los strings de una estructura (todo lo que llega al LLM)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [redact_deep(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_deep(v) for k, v in obj.items()}
    return obj


def _shrink(obj, max_str: int, max_list: int):
    if isinstance(obj, str):
        return obj if len(obj) <= max_str else obj[:max_str] + "...[recortado]"
    if isinstance(obj, list):
        out = [_shrink(x, max_str, max_list) for x in obj[:max_list]]
        if len(obj) > max_list:
            out.append(f"...[{len(obj) - max_list} elementos omitidos]")
        return out
    if isinstance(obj, dict):
        return {k: _shrink(v, max_str, max_list) for k, v in obj.items()}
    return obj


def truncate_json(obj, max_chars: int) -> str:
    """Serializa a JSON SIEMPRE válido dentro del límite: recorta strings y listas progresivamente."""
    s = json.dumps(obj, ensure_ascii=False, default=str)
    if len(s) <= max_chars:
        return s
    for max_str, max_list in [(600, 20), (300, 12), (160, 8), (80, 5), (40, 3)]:
        t = json.dumps(_shrink(obj, max_str, max_list), ensure_ascii=False, default=str)
        if len(t) <= max_chars:
            return t
    n = max_chars // 2
    while True:
        t = json.dumps({"truncated": True, "original_chars": len(s), "preview": s[:n]}, ensure_ascii=False)
        if len(t) <= max_chars or n == 0:
            return t
        n //= 2
