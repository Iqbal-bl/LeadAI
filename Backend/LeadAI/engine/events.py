"""
Turn events: the record other components (monitor agent, scorer, analytics) read.

The engine emits an event for every turn. Downstream components consume events; they
never reach into the live conversation. That one-way flow is what keeps the monitor
and the scorer OFF the reply's critical path.

This file is only the schema. Persistence (an outbox table written in the same
transaction as the turn, so an event exists if and only if the turn committed) comes
with the control-plane phase.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "turn.received",      # customer (or counterparty) message arrived
    "turn.replied",       # the AI answered
    "turn.skipped",       # the AI deliberately did not answer (paused, human took over)
    "handoff.requested",  # escalated to a human
    "grounding.blocked",  # verify step refused an unsupported reply
    "control.changed",    # conversation paused / terminated / resumed
    "lead.scored",        # lead score recomputed
    "lead.nullified",     # lead invalidated (e.g. bot-to-bot loop)
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TurnEvent(BaseModel):
    type: EventType
    client_id: str
    conversation_id: str
    channel: str
    turn_id: str | None = None
    # Who spoke: lets the monitor tell customer-to-bot from bot-to-bot.
    speaker: Literal["customer", "ai", "agent", "system"] = "customer"
    text: str | None = None
    confidence: float | None = None
    latency_ms: int | None = None
    # Free-form facts for consumers: model, sources count, verdict, reason...
    data: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=_now)
