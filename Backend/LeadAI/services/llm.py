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

from ..engine import gateway

logger = logging.getLogger(__name__)


def provider() -> str:
    return gateway.provider()


def model_name() -> str:
    return gateway.model_name()


def complete(
    system: str,
    messages: list[dict],
    temperature: float = 0.25,
    max_tokens: int = 600,
    json_mode: bool = False,
    profile: str = "chat",
) -> tuple[str | None, dict]:
    """Return (text_or_None, meta).

    The HTTP call, retry policy and tracing live in engine/gateway.py so every
    channel shares them. This wrapper keeps the original signature, so existing
    callers (and test doubles) are untouched.

    meta always carries latency_ms and model, so the caller can persist how a
    given reply was produced — useful when debugging "why did the AI say that".
    """
    return gateway.complete(
        system,
        messages,
        profile=profile,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=json_mode,
    )


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
