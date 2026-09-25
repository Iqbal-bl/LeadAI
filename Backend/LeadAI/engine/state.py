"""
State that flows through the conversation graph.

Two rules shape this file:
  * The engine is STATELESS. Everything a turn needs arrives in TurnState and
    everything it decides leaves in it; nothing lives in a module global or an
    instance attribute. That is what lets you run N workers without one of them
    "owning" a call.
  * Only plain data (str, int, dict, list) goes in. State is persisted between
    turns and serialised by LangGraph checkpointers, so no ORM rows or clients.
"""
from __future__ import annotations

from typing import Any, TypedDict

# Conversation control, set from OUTSIDE the conversation (staff, or the monitor
# agent). The engine checks it before doing anything else on a turn.
CONTROL_ACTIVE = "active"
CONTROL_PAUSED = "paused"
CONTROL_TERMINATED = "terminated"
CONTROL_STOPPED = {CONTROL_PAUSED, CONTROL_TERMINATED}

# Verify outcomes.
VERDICT_SUPPORTED = "supported"
VERDICT_UNSUPPORTED = "unsupported"
VERDICT_UNCHECKED = "unchecked"   # no source text available to check against


class TurnState(TypedDict, total=False):
    # ---- input ------------------------------------------------------------
    turn_id: str               # idempotency key: a retried webhook must not double-reply
    client_id: str
    conversation_id: str
    channel: str               # web | whatsapp | instagram | voice
    text: str                  # what the customer said
    control_status: str        # CONTROL_*; defaults to active
    human_assigned: bool       # a staff member has taken over: the AI stays silent

    # ---- produced by nodes --------------------------------------------------
    skip_reason: str | None    # why the AI did not answer this turn, if it did not
    result: dict[str, Any]     # the answer: reply, confidence, needs_human, sources...
    verdict: str               # VERDICT_*
    unsupported_figures: list[str]

    # ---- final decision -----------------------------------------------------
    reply: str
    needs_human: bool
    handoff_reason: str | None
