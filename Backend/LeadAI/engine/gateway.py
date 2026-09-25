"""
LLM gateway: the one place a model is actually called.

`services/llm.py` used to own the HTTP call. It now delegates here so that every
channel (chat, voice, analysis, and later the monitor agent) shares one client with
the same failure behaviour, and one place to hang tracing.

Contract, unchanged from the old llm.complete():
  * returns (text_or_None, meta); None means "no key configured or the call failed",
    and every caller already has a deterministic fallback for that;
  * meta always carries model and latency_ms.

What is new:
  * PROFILES: per-channel settings (temperature, token cap, timeout, retries), so
    voice can be tight without every call site remembering the numbers;
  * one bounded retry on failures that are genuinely transient (429 / 5xx / could
    not connect). Read timeouts are NOT retried: the provider may still be working
    on it, and a second attempt doubles both cost and dead air;
  * trace hooks: see add_trace_hook().
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..config import settings

logger = logging.getLogger(__name__)

# Statuses worth one more try. 4xx other than 429 are our bug (bad payload, bad key)
# and retrying them only hides it.
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}
_BACKOFF_SECONDS = 0.4


@dataclass(frozen=True)
class Profile:
    name: str
    temperature: float = 0.25
    max_tokens: int = 600
    timeout: float | None = None   # None -> settings.openai_timeout
    retries: int = 1


PROFILES: dict[str, Profile] = {
    # Factual Q&A: low temperature, because this is not copywriting.
    "chat": Profile("chat"),
    # A long reply is dead air on a call, and a retry is worse than a fallback line.
    "voice": Profile("voice", max_tokens=220, timeout=15.0, retries=0),
    # Structured extraction, lead scoring, monitor verdicts.
    "analysis": Profile("analysis", temperature=0.0, max_tokens=500),
}

# A trace hook receives (meta, system, messages, reply). It sees message content, so
# whoever registers one owns redaction. None are registered by default.
TraceHook = Callable[[dict, str, list, "str | None"], None]
_trace_hooks: list[TraceHook] = []


def add_trace_hook(hook: TraceHook) -> None:
    """Register an observer (Langfuse, LangSmith, a log file) for every model call."""
    _trace_hooks.append(hook)


def clear_trace_hooks() -> None:
    _trace_hooks.clear()


def _emit_trace(meta: dict, system: str, messages: list, reply: str | None) -> None:
    for hook in _trace_hooks:
        try:
            hook(meta, system, messages, reply)
        except Exception:  # noqa: BLE001 — tracing must never break a customer reply
            logger.warning("[LeadAI gateway] trace hook failed", exc_info=True)


def provider() -> str:
    return "openai" if settings.llm_enabled else "builtin-extractive"


def model_name() -> str:
    return settings.openai_model if settings.llm_enabled else "builtin-extractive"


def _is_transient(exc: Exception) -> bool:
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _TRANSIENT_STATUS
    return isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout)


def complete(
    system: str,
    messages: list[dict],
    *,
    profile: str = "chat",
    temperature: float | None = None,
    max_tokens: int | None = None,
    json_mode: bool = False,
) -> tuple[str | None, dict]:
    """Return (text_or_None, meta). Explicit arguments override the profile."""
    prof = PROFILES.get(profile, PROFILES["chat"])
    temperature = prof.temperature if temperature is None else temperature
    max_tokens = prof.max_tokens if max_tokens is None else max_tokens

    meta: dict = {
        "model": model_name(),
        "latency_ms": 0,
        "provider": provider(),
        "profile": prof.name,
        "attempts": 0,
    }
    if not settings.llm_enabled:
        return None, meta

    payload = {
        "model": settings.openai_model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, *messages],
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    started = time.perf_counter()
    reply: str | None = None
    for attempt in range(prof.retries + 1):
        meta["attempts"] = attempt + 1
        try:
            import httpx

            resp = httpx.post(
                f"{settings.openai_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json=payload,
                timeout=prof.timeout or settings.openai_timeout,
            )
            resp.raise_for_status()
            body = resp.json()
            usage = body.get("usage") or {}
            meta["prompt_tokens"] = usage.get("prompt_tokens")
            meta["completion_tokens"] = usage.get("completion_tokens")
            meta.pop("error", None)
            reply = body["choices"][0]["message"]["content"]
            break
        except Exception as exc:  # noqa: BLE001
            meta["error"] = str(exc)[:200]
            if attempt < prof.retries and _is_transient(exc):
                logger.info("[LeadAI gateway] transient failure (%s) — retrying", exc)
                time.sleep(_BACKOFF_SECONDS * (attempt + 1))
                continue
            logger.warning("[LeadAI gateway] completion failed (%s) — falling back", exc)
            break

    meta["latency_ms"] = int((time.perf_counter() - started) * 1000)
    _emit_trace(meta, system, messages, reply)
    return reply, meta
