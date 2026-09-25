"""
Outbox: write turn events to the database, in the turn's own transaction.

    emit(db, TurnEvent(...))     # adds a row; the caller's commit persists it

Because the row rides the same transaction as the turn, "the event exists" and "the turn
committed" are the same fact: a rolled-back turn leaves no phantom event, and a committed
one can never lose its event. That guarantee is the reason to use a table rather than a
fire-and-forget call to a queue.

Off unless ENGINE_EVENTS=true. Emitting never raises: an event is a side channel, and
losing one must not cost a customer their reply.
"""
from __future__ import annotations

import hashlib
import logging

from ..config import settings
from ..models_ext import LeadEvent
from .events import TurnEvent

logger = logging.getLogger(__name__)

MAX_TURN_ID_CHARS = 80   # width of leadai_events.TurnId
MAX_TEXT_CHARS = 2000   # the monitor needs the gist of a turn, not an unbounded blob


def column_turn_id(turn_id: str | None) -> str | None:
    """`turn_id` fitted to the TurnId column.

    Channel message ids can be far longer than the column (an Instagram message id runs to
    ~170 characters), and an oversize value makes MySQL reject the insert, which used to roll
    back the whole customer turn with it. A long id is stored as a stable hash instead; the
    full id is still in PayloadJson.
    """
    if not turn_id or len(turn_id) <= MAX_TURN_ID_CHARS:
        return turn_id
    return "h:" + hashlib.sha256(turn_id.encode("utf-8")).hexdigest()[:MAX_TURN_ID_CHARS - 2]


def enabled() -> bool:
    return bool(getattr(settings, "engine_events", False))


def emit(db, event: TurnEvent) -> bool:
    """Queue `event` on the session. Returns True if a row was added."""
    if not enabled():
        return False
    try:
        if event.text and len(event.text) > MAX_TEXT_CHARS:
            event = event.model_copy(update={"text": event.text[:MAX_TEXT_CHARS]})
        db.add(
            LeadEvent(
                ClientId=event.client_id,
                ConversationId=event.conversation_id,
                Type=event.type,
                Channel=event.channel,
                TurnId=column_turn_id(event.turn_id),
                Speaker=event.speaker,
                PayloadJson=event.model_dump(mode="json"),
                CreatedBy="engine",
            )
        )
        return True
    except Exception:  # noqa: BLE001
        logger.warning("[LeadAI outbox] could not queue %s event", event.type, exc_info=True)
        return False
