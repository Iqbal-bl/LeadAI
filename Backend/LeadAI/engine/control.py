"""
Control plane: stop, pause or resume a conversation from OUTSIDE it.

Who uses this: staff (a "pause the bot" button), and the monitor agent (two company bots
talking to each other in a loop: terminate it). The conversation never decides this for
itself; the pipeline only READS the status, at the start of every turn
(`is_stopped`), and stops answering.

    active       normal
    paused       the AI stays silent; staff can resume
    terminated   the AI stays silent for good (e.g. a confirmed bot-to-bot loop)

Kept separate from `LeadConversation.Status` (the sales workflow) on purpose: a
conversation can be `assigned` to a human and still be `active`, or `needs_human` and
`terminated`. NULL in the column means active, so existing rows need no backfill.

set_control() records the change three ways: on the row, in the audit log (who and why),
and as a `control.changed` event for the monitor and analytics.
"""
from __future__ import annotations

from ..activity import A
from ..activity import log as audit_log
from ..models import utcnow
from . import outbox
from .events import TurnEvent
from .state import CONTROL_ACTIVE, CONTROL_PAUSED, CONTROL_STOPPED, CONTROL_TERMINATED

VALID = (CONTROL_ACTIVE, CONTROL_PAUSED, CONTROL_TERMINATED)


def get_control(conversation) -> str:
    return getattr(conversation, "ControlStatus", None) or CONTROL_ACTIVE


def is_stopped(conversation) -> bool:
    return get_control(conversation) in CONTROL_STOPPED


def set_control(
    db,
    conversation,
    status: str,
    *,
    reason: str,
    by: str,
    request=None,
) -> str:
    """Change a conversation's control status. Returns the previous status.

    Idempotent: setting the status it already has changes nothing and records nothing.
    The caller commits.
    """
    if status not in VALID:
        raise ValueError(f"control status must be one of {VALID}, got {status!r}")
    previous = get_control(conversation)
    if previous == status:
        return previous

    conversation.ControlStatus = status
    conversation.ControlReason = (reason or "")[:300]
    conversation.ControlAt = utcnow()
    conversation.ControlBy = (by or "system")[:200]

    audit_log(
        db,
        action=A.CONTROL_CHANGED,
        client_id=conversation.ClientId,
        actor_email=by or "system",
        actor_role="system" if "@" not in (by or "") else "staff",
        entity_type="conversation",
        entity_id=conversation.Id,
        message=f"Conversation {previous} -> {status}: {(reason or '')[:160]}",
        meta={"from": previous, "to": status, "reason": (reason or "")[:300]},
        log_type="Warning" if status != CONTROL_ACTIVE else "Info",
        request=request,
    )
    outbox.emit(
        db,
        TurnEvent(
            type="control.changed",
            client_id=conversation.ClientId,
            conversation_id=conversation.Id,
            channel=conversation.Channel or "web",
            speaker="system",
            data={"from": previous, "to": status, "reason": reason, "by": by},
        ),
    )
    return previous
