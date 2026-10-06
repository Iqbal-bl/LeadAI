"""
Background scoring for chat turns: one conversation at a time, newest turn wins, one retry.

Scoring (qualify + summarise) runs after the reply has been delivered, so nothing here is
on the customer's path: submit() only records a job and returns.

WHAT THIS FIXES (it replaced one fire-and-forget thread per turn)
  * Two quick messages used to start two scorers at once. Each read the history and wrote
    the lead row, so whichever finished last won, even if it had seen less history.
    Now a conversation has at most one scorer running.
  * A scorer for a conversation that is already being scored does not queue a second LLM
    round trip per message: the job waiting behind the running one is replaced, because a
    score is recomputed from the whole conversation and only the latest one matters.
  * A failed run is retried once instead of being silently lost.
  * The number of concurrent scorers is bounded, so a burst cannot open unbounded LLM calls
    and database sessions.

WHAT IT DOES NOT FIX
  In-process only. A restart loses jobs that have not run, and several app workers each have
  their own queue, so two workers can still score the same conversation at once. Both are
  fine for now (the next turn re-scores everything), and both go away when scoring moves to
  a consumer of the turn events.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ..config import settings

logger = logging.getLogger(__name__)

RETRY_DELAY_SECONDS = 2.0

_lock = threading.Lock()
_waiting: dict[str, tuple[str, str | None]] = {}   # conversation_id -> (client_id, message_id)
_running: set[str] = set()
_pool: ThreadPoolExecutor | None = None


def _get_pool() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(
            max_workers=max(1, int(getattr(settings, "scoring_workers", 4))),
            thread_name_prefix="leadai-score",
        )
    return _pool


def submit(client_id: str, conversation_id: str, message_id: str | None) -> None:
    """Queue scoring for a conversation. Never blocks, never raises."""
    try:
        with _lock:
            _waiting[conversation_id] = (client_id, message_id)   # newest turn replaces an older waiter
            if conversation_id in _running:
                return                                             # the running worker will pick it up
            _running.add(conversation_id)
        _get_pool().submit(_drain, conversation_id)
    except Exception:  # noqa: BLE001 — scoring must never break a turn
        logger.warning("[LeadAI scoring] could not queue conv %s", conversation_id, exc_info=True)
        with _lock:
            _running.discard(conversation_id)
            _waiting.pop(conversation_id, None)


def _drain(conversation_id: str) -> None:
    while True:
        with _lock:
            job = _waiting.pop(conversation_id, None)
            if job is None:
                _running.discard(conversation_id)
                return
        _run(conversation_id, *job)


def _run(conversation_id: str, client_id: str, message_id: str | None) -> None:
    from . import conversation_flow   # late: conversation_flow imports this module

    for attempt in (1, 2):
        if conversation_flow.run_deferred_scoring(client_id, conversation_id, message_id):
            return
        if attempt == 1:
            time.sleep(RETRY_DELAY_SECONDS)
    logger.error("[LeadAI scoring] gave up on conv %s after a retry", conversation_id)


def wait_idle(timeout: float = 5.0) -> bool:
    """Block until nothing is queued or running (True), or the timeout passes (False).
    For tests and graceful shutdown; request handlers never call it."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with _lock:
            if not _running and not _waiting:
                return True
        time.sleep(0.01)
    return False
