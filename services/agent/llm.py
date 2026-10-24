"""Fábrica del modelo de chat (proveedor/modelo por entorno), reintentos con backoff, rate limit,
y modelos falsos deterministas para pruebas offline."""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Callable, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from services.agent.config import agent_cfg, llm_model, llm_provider, pricing_cfg

_rate_lock = threading.Lock()
_last_call = [0.0]


def _min_interval() -> float:
    c = agent_cfg()["llm"]
    return float(os.environ.get(c["min_interval_s_env"], "0") or 0)


def _throttle() -> None:
    gap = _min_interval()
    if gap <= 0:
        return
    with _rate_lock:
        wait = _last_call[0] + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


_RETRYABLE = re.compile(r"429|RESOURCE_EXHAUSTED|ResourceExhausted|503|UNAVAILABLE|500 |INTERNAL|overloaded|"
                        r"Deadline|timed out", re.I)


class QuotaExhausted(Exception):
    """Cuota diaria agotada (reintentar en horas): no se reintenta; se pasa al modelo de respaldo si existe."""


def invoke_with_retry(model, messages: Sequence[BaseMessage], max_retries: int | None = None,
                      base: float | None = None) -> AIMessage:
    c = agent_cfg()["llm"]
    max_retries = c["max_retries"] if max_retries is None else max_retries
    base = c["backoff_base_s"] if base is None else base
    fake = getattr(model, "_llm_type", "") == "scripted-fake"
    for attempt in range(max_retries + 1):
        if not fake:
            _throttle()
        try:
            return model.invoke(list(messages))
        except Exception as e:  # noqa: BLE001 - se re-lanza si no es reintentable
            msg = str(e)
            if not _RETRYABLE.search(msg):
                raise
            if attempt >= max_retries and "PerDay" not in msg:
                raise
            if "PerDay" in msg or "per day" in msg:
                raise QuotaExhausted(msg[:300]) from e
            m = re.search(r"retry in ([\d.]+)s|retryDelay['\"]?: ?['\"]?(\d+)", msg)
            delay = float(m.group(1) or m.group(2)) + 1 if m else base * (2 ** attempt)
            if delay > 120:
                raise QuotaExhausted(msg[:300]) from e
            time.sleep(min(delay, 60))
    raise RuntimeError("unreachable")


def make_chat_model(provider: str | None = None, model: str | None = None) -> BaseChatModel:
    provider = provider or llm_provider()
    model = model or llm_model()
    if provider == "fake":
        raise ValueError("Use ScriptedChatModel directamente en pruebas")
    from langchain.chat_models import init_chat_model

    kwargs: dict[str, Any] = {"temperature": agent_cfg()["llm"]["temperature"]}
    if provider == "google_genai":
        kwargs["max_retries"] = 0  # reintentos propios (invoke_with_retry) para controlar backoff y medir
        level = os.environ.get("LLM_THINKING_LEVEL", agent_cfg()["llm"].get("thinking_level"))
        if level and model.startswith("gemini-3"):
            kwargs["thinking_level"] = level  # menos razonamiento interno => menor latencia/costo
    return init_chat_model(model, model_provider=provider, **kwargs)


def make_fallback_models() -> list[tuple[str, BaseChatModel]]:
    """Modelos de respaldo (env LLM_FALLBACK_MODELS, coma-separado) ante cuota agotada del principal."""
    names = [m.strip() for m in os.environ.get("LLM_FALLBACK_MODELS", "").split(",") if m.strip()]
    return [(n, make_chat_model(model=n)) for n in names]


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    p = pricing_cfg()
    price = p["models"].get(model) or p["default"]
    return round(input_tokens / 1e6 * price["input_per_mtok"] + output_tokens / 1e6 * price["output_per_mtok"], 6)


class ScriptedChatModel(BaseChatModel):
    """Modelo determinista para tests: cada llamada devuelve el siguiente elemento del guion.

    Un elemento puede ser un AIMessage o una función (messages) -> AIMessage. Si el guion se agota, responde
    con un texto fijo. bind_tools() devuelve el mismo objeto (las tools se registran para inspección).
    """

    script: list[Any] = []
    calls: int = 0
    bound_tools: list[str] = []
    seen: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        self.bound_tools = [t["function"]["name"] if isinstance(t, dict) else getattr(t, "name", str(t))
                            for t in tools]
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager: CallbackManagerForLLMRun | None = None,
                  **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        item: Any = self.script[self.calls] if self.calls < len(self.script) else AIMessage(content="Listo.")
        self.calls += 1
        msg = item(messages) if callable(item) else item
        msg = msg.model_copy()
        if not msg.usage_metadata:
            msg.usage_metadata = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
        return ChatResult(generations=[ChatGeneration(message=msg)])


def tool_call(name: str, args: dict, call_id: str | None = None) -> dict:
    return {"name": name, "args": args, "id": call_id or f"call_{name}_{abs(hash(str(args))) % 10**6}",
            "type": "tool_call"}


def ai_tools(*calls: dict, content: str = "") -> AIMessage:
    return AIMessage(content=content, tool_calls=list(calls))


ScriptItem = AIMessage | Callable[[list[BaseMessage]], AIMessage]
