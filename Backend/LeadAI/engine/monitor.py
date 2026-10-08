"""
Monitor agent: a lightweight triage classifier that watches every chat/voice turn,
concurrently with retrieval, and decides whether it is a real knowledge question or a
general/non-substantive remark (small talk, an acknowledgement, a request unrelated to
the company's products) that should never reach KB retrieval.

Generalises the shape of ai_engine.py's `_is_greeting`/`_is_language_switch_request`
short-circuits — those are free (no LLM call) and stay as the first, zero-cost check;
this is the catch-all behind them for every way a customer can say something that isn't
a product question that nobody has written a regex for yet. A real incident: a caller's
entire turn was "please speak in Hindi" — not recognised as non-substantive by anything
at the time, so it ran through retrieval, scored low confidence against a knowledge base
that has nothing to do with language, and the voice pipeline ended the call.

    TRIAGE_MODE=off       (default) classify_turn() is never called.
    TRIAGE_MODE=observe   every eligible turn is classified and the verdict is traced,
                          but the reply is untouched. Use this to measure accuracy on
                          real traffic before trusting it.
    TRIAGE_MODE=enforce   a confident "general" verdict skips KB retrieval.

submit() is called BEFORE retrieval and resolve() AFTER it, so the classification
genuinely overlaps with the (non-LLM) retrieval work on a background thread instead of
adding a serial round trip to every turn.

Any failure (timeout, bad JSON, no API key) resolves to None: the monitor may fail to
help, but it must never be the reason a customer gets no reply, or a slow one.
"""
from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor

from ..config import settings
from ..models import LeadMessage
from . import gateway

logger = logging.getLogger(__name__)

HISTORY_TURNS = 2   # just enough that a bare "yes" answering the AI's own question has context

MODES = ("off", "observe", "enforce")

SYSTEM_PROMPT = (
    "You triage one customer message in a sales conversation. Decide whether it is a "
    "real question about the company's products/services that needs looking up in "
    "company knowledge, or a general remark that does not (a greeting, small talk, an "
    "acknowledgement like 'yes'/'ok', a request to change language, asking if anyone is "
    "there, a test message, anything off-topic). "
    "A question asking to recall, confirm or summarise something already said or agreed "
    "EARLIER IN THIS CONVERSATION — their own budget, the product or plot they already "
    "chose, a date already discussed, 'what did we finalize' — is ALSO general, even "
    "though it names a product-sounding word: it needs the conversation so far, not a "
    "lookup in company knowledge, and the company knowledge has no way to know what THIS "
    "customer personally said or agreed to. "
    'Reply with JSON only: {"category": "knowledge_question" or "general", '
    '"confidence": 0 to 1, "reason": "a few words"}.'
)

_executor: ThreadPoolExecutor | None = None


def current_mode() -> str:
    mode = (getattr(settings, "triage_mode", "off") or "off").strip().lower()
    if mode not in MODES:
        logger.warning("[LeadAI monitor] unknown TRIAGE_MODE %r — treating as off", mode)
        return "off"
    return mode


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="leadai-monitor")
    return _executor


def _history_window(history: list[LeadMessage]) -> list[dict]:
    """The last few turns as {"role", "content"} dicts — same shape as
    services/memory.py's llm_window(), not imported from here: the engine package
    does not depend on the services layer (services/ depends on engine/, not the
    other way round)."""
    usable = [m for m in (history or []) if (m.Sender or "") in ("customer", "ai", "agent")]
    return [
        {"role": "user" if m.Sender == "customer" else "assistant", "content": m.Content or ""}
        for m in usable[-HISTORY_TURNS:]
        if (m.Content or "").strip()
    ]


def classify_turn(
    question: str,
    query_override: str | None,
    history: list[LeadMessage],
    channel: str,
) -> dict | None:
    """{"category", "confidence", "reason"}, or None on any failure. Never raises."""
    try:
        messages = _history_window(history)
        messages.append({"role": "user", "content": query_override or question or ""})
        verdict, _meta = gateway.complete_json(SYSTEM_PROMPT, messages, profile="triage")
        if not verdict:
            return None
        category = verdict.get("category")
        if category not in ("knowledge_question", "general"):
            return None
        return {
            "category": category,
            "confidence": float(verdict.get("confidence") or 0.0),
            "reason": str(verdict.get("reason") or "")[:200],
        }
    except Exception:  # noqa: BLE001 — the monitor must never break or slow a reply
        logger.warning("[LeadAI monitor] classify_turn failed", exc_info=True)
        return None


def submit(
    question: str, query_override: str | None, history: list[LeadMessage], channel: str
) -> Future | None:
    """Kick off classification in the background. None when triage is off."""
    if current_mode() == "off":
        return None
    try:
        return _get_executor().submit(classify_turn, question, query_override, history, channel)
    except Exception:  # noqa: BLE001
        logger.warning("[LeadAI monitor] could not submit classification", exc_info=True)
        return None


def resolve(future: Future | None) -> dict | None:
    """Block (bounded) for the verdict. None on timeout/failure/no-future — fail open."""
    if future is None:
        return None
    try:
        return future.result(timeout=settings.triage_timeout_seconds)
    except Exception:  # noqa: BLE001 — a slow/failed classifier must never block a reply
        logger.warning("[LeadAI monitor] classification did not resolve in time", exc_info=True)
        return None
