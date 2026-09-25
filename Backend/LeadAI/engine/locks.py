"""
Per-conversation lock: one turn at a time for a given conversation.

THE PROBLEM
A customer on WhatsApp often sends two or three short messages in a row. Their webhooks
arrive milliseconds apart and are processed by different workers at the same time. Each
turn loads the history (without the other's message), each asks the model, each replies.
The customer gets two answers, out of order, and neither turn saw the other's context.

THE FIX
Take a MySQL advisory lock named after the conversation for the duration of the turn.
The second message waits for the first to finish, then runs with the full, fresh history.

WHY AN ADVISORY LOCK ON ITS OWN CONNECTION (not SELECT ... FOR UPDATE)
  * The app's sessions use MySQL's default REPEATABLE READ with autoflush off. A row lock
    taken mid-transaction does not refresh the read snapshot, so the second worker would
    win the lock and STILL read a stale history. This helper therefore commits after
    acquiring, which ends the old snapshot; the next read sees the other turn's messages.
  * An advisory lock is tied to a connection, and a Session returns its connection to the
    pool on commit, so it is held on a connection of its own and released there.

FAIL OPEN
If the lock cannot be taken (wrong database, pool exhausted, timeout), the turn runs
unlocked and a warning is logged. A rare duplicate reply is better than a dropped
customer message.

COST
One extra database connection per in-flight turn. Size the pool for it, which is why this
is off unless LEADAI_CONVERSATION_LOCK=true.

Commit semantics: acquiring the lock commits whatever the caller has pending. Both callers
(web widget, channel webhooks) have only committed-or-committable work at that point.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text

from ..config import settings

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(getattr(settings, "conversation_lock", False))


def _name(conversation_id: str) -> str:
    # MySQL lock names are limited to 64 characters; a UUID keeps this well under.
    return f"leadai:conv:{conversation_id}"[:64]


@contextmanager
def conversation_lock(db, conversation_id: str, timeout: int | None = None) -> Iterator[bool]:
    """Hold the conversation's lock for the body. Yields True if locked, False if not."""
    if not enabled():
        yield False
        return

    try:
        bind = db.get_bind()
        dialect = bind.dialect.name
    except Exception:  # noqa: BLE001
        yield False
        return
    if dialect != "mysql":          # SQLite in tests, or an engine without GET_LOCK
        yield False
        return

    timeout = int(timeout if timeout is not None else getattr(settings, "conversation_lock_timeout", 30))
    name = _name(conversation_id)
    conn = None
    got = None
    try:
        conn = bind.connect()
        got = conn.execute(text("SELECT GET_LOCK(:n, :t)"), {"n": name, "t": timeout}).scalar()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LeadAI lock] could not take lock for conv %s (%s) — running unlocked",
                       conversation_id, exc.__class__.__name__)

    if got != 1:
        if conn is not None:
            if got == 0:
                logger.warning("[LeadAI lock] timed out after %ss waiting for conv %s — running unlocked",
                               timeout, conversation_id)
            conn.close()
        yield False
        return

    try:
        # End the read snapshot taken before we owned the lock, so the next read sees
        # whatever the previous turn committed while we waited.
        db.commit()
        yield True
    finally:
        try:
            conn.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": name})
        except Exception:  # noqa: BLE001 — the lock dies with the connection anyway
            logger.warning("[LeadAI lock] release failed for conv %s", conversation_id, exc_info=True)
        finally:
            conn.close()
