"""
Bridge between the live chat flow and the engine graph.

The engine runs on the reply production has ALREADY produced, so it costs no extra LLM
call and cannot change what the customer would have been told unless you opt in:

    ENGINE_MODE=off       (default) nothing happens.
    ENGINE_MODE=observe   the graph judges every reply (figures supported by the
                          sources? did it decline in words?) and the verdict is logged
                          and stored in the activity log. The reply is untouched. Use
                          this on staging/production first to measure how often it
                          WOULD have intervened.
    ENGINE_MODE=enforce   the graph's decision replaces the original: an invented
                          figure or an in-words "I can't answer" now hands the
                          conversation to a human.

Any failure inside the engine is logged and swallowed: the engine may fail to help,
but it must never be the reason a customer gets no reply.
"""
from __future__ import annotations

import logging

from ..config import settings
from . import graph
from .state import VERDICT_UNSUPPORTED

logger = logging.getLogger(__name__)

MODES = ("off", "observe", "enforce")


def current_mode() -> str:
    mode = (getattr(settings, "engine_mode", "off") or "off").strip().lower()
    if mode not in MODES:
        logger.warning("[LeadAI engine] unknown ENGINE_MODE %r — treating as off", mode)
        return "off"
    return mode


def audit_meta(result: dict) -> dict:
    """The engine's note as flat, typed keys for the activity log.

    Flat because the audit log stringifies nested values, which would turn `declined:
    true` into `"True"` and make the verdicts awkward to query.
    """
    note = result.get("engine")
    if not note:
        return {}
    return {
        "engine_mode": note["mode"],
        "engine_verdict": note["verdict"],
        "engine_unsupported": note["unsupported_count"],
        "engine_declined": note["declined"],
        "engine_escalation": note["escalation"],
    }


def apply(
    result: dict,
    *,
    text: str,
    client_id: str,
    conversation_id: str,
    channel: str,
    mode: str | None = None,
) -> dict:
    """Return `result`, judged (observe) or decided (enforce) by the engine graph."""
    mode = mode or current_mode()
    if mode == "off":
        return result

    try:
        final = graph.run_turn(
            {
                "text": text,
                "client_id": client_id,
                "conversation_id": conversation_id,
                "channel": channel,
            },
            lambda _state: result,
            enforce=(mode == "enforce"),
        )
    except Exception:  # noqa: BLE001 — never let the engine break a customer reply
        logger.exception("[LeadAI engine] graph failed; using the original reply")
        return result

    unsupported = final.get("unsupported_figures") or []
    declined = bool(final.get("declined"))
    # Computed from the verdicts, not from the final decision: in observe mode the
    # decision is deliberately unchanged, and the point is to count how often the engine
    # WOULD have stepped in. In enforce mode the same condition means it did.
    changed = (declined or final.get("verdict") == VERDICT_UNSUPPORTED) and not result.get(
        "needs_human"
    )
    note = {
        "mode": mode,
        "verdict": final.get("verdict"),
        "unsupported_count": len(unsupported),
        "declined": declined,
        # In observe mode this reads "would have escalated"; in enforce, "did".
        "escalation": changed,
    }

    # Counts only: replies and figures can contain customer or company data, and logs
    # are not the place for it. The verdict travels in the activity log instead.
    if final.get("verdict") == VERDICT_UNSUPPORTED or declined:
        logger.info(
            "[LeadAI engine] conv=%s channel=%s mode=%s verdict=%s unsupported=%d "
            "declined=%s escalation=%s",
            conversation_id, channel, mode, note["verdict"], len(unsupported), declined, changed,
        )

    out = dict(result)
    out["engine"] = note
    if mode == "enforce":
        out["needs_human"] = final.get("needs_human", out.get("needs_human"))
        out["handoff_reason"] = final.get("handoff_reason", out.get("handoff_reason"))
    return out
