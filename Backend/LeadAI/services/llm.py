"""
Single entry point for text generation (OpenAI chat completions).

`complete()` returns None when no key is configured OR when the call fails, and
every caller in ai_engine has a deterministic fallback for that case. That is a
deliberate design choice, not a convenience: the sales assistant must keep
answering during an OpenAI outage, and an extractive answer built from retrieved
chunks is a safe degradation — it can only quote the company's own knowledge
base, so it cannot hallucinate.

Grounding rules enforced here rather than trusted to the prompt:
  * the retrieved context is passed as a separate system-level block;
  * temperature defaults low (0.25) because this is factual Q&A, not copywriting;
  * max_tokens is capped so a runaway generation can't blow the TTS budget on a
    voice call, where every extra sentence is dead air.
"""
from __future__ import annotations

import json
import logging
import time

from ..config import settings

logger = logging.getLogger(__name__)


def provider() -> str:
    return "openai" if settings.llm_enabled else "builtin-extractive"


def model_name() -> str:
    return settings.openai_model if settings.llm_enabled else "builtin-extractive"


def complete(
    system: str,
    messages: list[dict],
    temperature: float = 0.25,
    max_tokens: int = 600,
    json_mode: bool = False,
) -> tuple[str | None, dict]:
    """Return (text_or_None, meta).

    meta always carries latency_ms and model, so the caller can persist how a
    given reply was produced — useful when debugging "why did the AI say that".
    """
    meta: dict = {"model": model_name(), "latency_ms": 0, "provider": provider()}
    if not settings.llm_enabled:
        return None, meta

    started = time.perf_counter()
    try:
        import httpx

        payload = {
            "model": settings.openai_model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, *messages],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        resp = httpx.post(
            f"{settings.openai_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json=payload,
            timeout=settings.openai_timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        meta["latency_ms"] = int((time.perf_counter() - started) * 1000)
        usage = body.get("usage") or {}
        meta["prompt_tokens"] = usage.get("prompt_tokens")
        meta["completion_tokens"] = usage.get("completion_tokens")
        return body["choices"][0]["message"]["content"], meta
    except Exception as exc:  # noqa: BLE001
        meta["latency_ms"] = int((time.perf_counter() - started) * 1000)
        meta["error"] = str(exc)[:200]
        logger.warning("[LeadAI llm] completion failed (%s) — falling back", exc)
        return None, meta


def complete_json(system: str, messages: list[dict]) -> tuple[dict | None, dict]:
    """JSON-mode completion, used for structured lead extraction."""
    raw, meta = complete(system, messages, temperature=0.0, json_mode=True, max_tokens=500)
    if not raw:
        return None, meta
    try:
        return json.loads(raw), meta
    except json.JSONDecodeError:
        # Models occasionally wrap JSON in prose or a code fence.
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        try:
            return json.loads(cleaned.strip()), meta
        except json.JSONDecodeError:
            meta["error"] = "unparseable json"
            return None, meta
