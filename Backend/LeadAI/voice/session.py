"""
One phone call's connection to the shared brain.

The audio pipeline runs on an asyncio event loop, and the brain is ordinary blocking code
(database, LLM). Running it directly would freeze the audio for everyone on the process. So:

  * every brain call runs in a worker thread, in its OWN database session (a session is not
    safe to share across threads);
  * the slow part that does not change what the caller hears next (scoring the lead,
    refreshing the summary) is queued and run after the reply has been spoken, one at a
    time and in order, so a fast talker cannot make two scoring runs race each other.

This class holds only ids, never ORM objects, so nothing crosses a thread boundary that
should not.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable

from sqlalchemy.orm import Session

from domain.models import Client

from ..models import LeadCall, LeadConversation, utcnow
from ..services import voice_flow
from .brain import BrainReply

logger = logging.getLogger(__name__)

SCORING_DRAIN_SECONDS = 30.0

# A call already in one of these states was ended by something that already knows the
# real outcome (a manual dashboard hangup, a carrier status webhook, the simulated
# endpoint's transfer) — _finalize_call() must not relabel it, only backfill a missing
# duration.
_TERMINAL_CALL_STATUSES = frozenset(
    {"completed", "failed", "busy", "no-answer", "canceled", "transferred"}
)


def _default_session_factory() -> Session:
    from ..db import session

    return session()


class CallSession:
    def __init__(
        self,
        *,
        client_id: str,
        conversation_id: str,
        call_sid: str,
        session_factory: Callable[[], Session] | None = None,
        phone_number: str | None = None,
    ) -> None:
        self.client_id = client_id
        self.conversation_id = conversation_id
        self.call_sid = call_sid
        self.phone_number = phone_number
        self.language: str | None = None        # the caller's language, from speech-to-text
        self._token: threading.Event | None = None  # cancels the turn currently being prepared
        self._ai_end_reason: str | None = None      # set when the AI has decided to end the call
        self._broadcasts: set[asyncio.Task] = set()  # live-transcript pushes still in flight
        self._session_factory = session_factory or _default_session_factory
        self._scoring: asyncio.Queue = asyncio.Queue()
        self._scoring_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ loading
    def _load(self, db: Session):
        client = db.get(Client, self.client_id)
        conversation = db.get(LeadConversation, self.conversation_id)
        call = (
            db.query(LeadCall)
            .filter(LeadCall.CallSid == self.call_sid, LeadCall.ConversationId == self.conversation_id)
            .first()
        )
        if client is None or conversation is None or call is None:
            raise LookupError(f"call {self.call_sid} is not linked to a LeadAI conversation")
        return client, conversation, call

    # ------------------------------------------------------- legacy call screens
    def _save_legacy_transcript(self, role: str, text: str) -> None:
        """Also write the line to the existing `conversations` table.

        The call detail screens and the post-call sync read that table (role "user" is the
        customer, "agent" is the AI), and the legacy loop wrote it for every spoken line. A
        call served by this pipeline must show up there too. Best effort: never fails a turn.
        """
        if not text:
            return
        try:
            from outbound.call_store import save_transcript_message

            save_transcript_message(self.call_sid, role, text, self.phone_number)
        except Exception:  # noqa: BLE001
            logger.warning("[LeadAI voice] could not write the legacy transcript row", exc_info=True)

    # ----------------------------------------------------------- live transcript
    def broadcast(self, role: str, text: str) -> None:
        """Push one spoken line ("user" or "agent") to the live transcript websockets.

        The legacy loop did this for every line through outbound.app.broadcast_transcript, which
        streams it to the call's transcript socket AND the inbox conversation socket (same
        message id in both). The Pipecat path only saved lines to the database, so the UI stayed
        empty until the call was over. Reusing that function keeps the format identical.

        Fire-and-forget, like the legacy loop: it must never delay the audio. Ordering holds
        because the websocket manager delivers each call's messages in the order they were queued.
        """
        text = (text or "").strip()
        if not text:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._push_transcript(role, text))
        self._broadcasts.add(task)
        task.add_done_callback(self._broadcasts.discard)

    def broadcast_user(self, text: str) -> None:
        self.broadcast("user", text)

    async def _push_transcript(self, role: str, text: str) -> None:
        try:
            from outbound.app import broadcast_transcript

            await broadcast_transcript(self.call_sid, role, text)
        except Exception:  # noqa: BLE001
            logger.debug("[LeadAI voice] live transcript push failed", exc_info=True)

    # ------------------------------------------------ language and interruptions
    def set_language(self, code: str | None) -> None:
        """Called by the pipeline with each transcript's language (e.g. "hi-IN")."""
        if code:
            self.language = code

    def supersede(self) -> None:
        """The caller spoke again: the turn being prepared must be discarded, not saved."""
        token = self._token
        if token is not None:
            token.set()

    # -------------------------------------------------------------------- turns
    def _respond_sync(self, text: str, token: threading.Event | None = None) -> BrainReply:
        db = self._session_factory()
        language = self.language
        try:
            client, conversation, call = self._load(db)
            turn = voice_flow.handle_voice_turn(
                db, client, conversation, call, text, defer_scoring=True, commit=True, live_call=True,
                language=language, superseded=(token.is_set if token is not None else None),
            )
            if turn.superseded:
                return BrainReply(superseded=True)
            self._save_legacy_transcript("user", text)
            self._save_legacy_transcript("agent", turn.reply_text)
            return BrainReply(
                text=turn.reply_text, ends_call=turn.ends_call, skipped=turn.skipped,
                message_id=turn.outbound_id, language=turn.language,
            )
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    async def respond(self, text: str) -> BrainReply:
        # A fresh token per turn: the brain sets it if the caller speaks again before the
        # reply is ready, and the worker thread then rolls the turn back instead of saving it.
        token = threading.Event()
        self._token = token
        return await asyncio.to_thread(self._respond_sync, text, token)

    def _opening_sync(self) -> BrainReply:
        from . import warmup

        db = self._session_factory()
        try:
            client, conversation, call = self._load(db)
            precomposed = warmup.pop_precomputed_opening(self.call_sid)
            language = precomposed["language"] if precomposed else voice_flow.opening_language(db, conversation)
            text, message_id = voice_flow.opening_line(
                db, client, conversation, call, language=language, precomposed=precomposed,
            )
            self._save_legacy_transcript("agent", text)
            return BrainReply(text=text, skipped=not text, message_id=message_id, language=language)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    async def opening(self) -> BrainReply:
        reply = await asyncio.to_thread(self._opening_sync)
        self.broadcast("agent", reply.text)        # legacy showed the greeting as it started
        return reply

    # ------------------------------------------------------------------ scoring
    def start(self) -> None:
        if self._scoring_task is None:
            self._scoring_task = asyncio.create_task(self._scoring_worker())

    def schedule_scoring(self, reply: BrainReply) -> None:
        """Queue the post-reply work for a stored AI message. Safe to call from the brain."""
        if reply.message_id:
            self._scoring.put_nowait(reply.message_id)

    def after_reply(self, reply: BrainReply) -> None:
        """Runs once per spoken reply: queue scoring, and remember if the AI is ending the call."""
        self.schedule_scoring(reply)
        # The reply has just been handed to the voice: show it. Superseded replies never get here.
        self.broadcast("agent", reply.text)
        if reply.ends_call or reply.skipped:
            self._ai_end_reason = (
                "AI ended the call (conversation paused or terminated)" if reply.skipped
                else "AI ended the call (end of conversation)"
            )

    def note_ai_hangup(self) -> None:
        """Called at the moment WE hang up: record that the AI ended the call, and only then.

        The legacy bookkeeping says "Caller hung up" unless told otherwise. The first fix set
        the reason when the AI DECIDED to end the call, but on the second call the caller hung
        up first, and the log then blamed the AI. So the reason is written only if the AI has
        decided to end AND the call is still live, right before the hang-up request is sent.
        """
        if not self._ai_end_reason:
            return
        try:
            from outbound.app import active_calls
            from outbound.call_state import call_hangup_reasons

            status = (active_calls.get(self.call_sid) or {}).get("status")
            if status in ("completed", "failed", "busy", "no-answer", "canceled"):
                return                                   # the caller (or the carrier) ended it first
            call_hangup_reasons[self.call_sid] = self._ai_end_reason
        except Exception:  # noqa: BLE001
            logger.debug("could not record the hangup reason", exc_info=True)

    def _score_sync(self, message_id: str) -> None:
        db = self._session_factory()
        try:
            voice_flow.run_deferred_scoring(db, self.client_id, self.conversation_id, message_id)
        finally:
            db.close()

    async def _scoring_worker(self) -> None:
        while True:
            message_id = await self._scoring.get()
            try:
                if message_id is None:
                    return
                await asyncio.to_thread(self._score_sync, message_id)
            except Exception:  # noqa: BLE001 — never let scoring kill the worker
                logger.warning("[LeadAI voice] scoring failed for call %s", self.call_sid, exc_info=True)
            finally:
                self._scoring.task_done()

    def _finalize_call_sync(self) -> None:
        """Mark the LeadCall row ended and record how long it actually ran.

        This is the one place every real call reaches on its way out, whatever ended it
        (caller hung up, the AI ended it, an error, the idle timeout) — so it is where
        leadai_calls.Status/DurationSec get written at all. Before this, only a manual
        dashboard hangup or the simulated endpoint ever touched those columns, which is
        why 16 of 23 live calls sat at Status=initiated forever and every "completed" one
        still showed DurationSec=0.
        """
        db = self._session_factory()
        try:
            call = (
                db.query(LeadCall)
                .filter(LeadCall.CallSid == self.call_sid, LeadCall.ConversationId == self.conversation_id)
                .one_or_none()
            )
            if call is None:
                return
            if not call.DurationSec and call.CreatedAt:
                created = call.CreatedAt.replace(tzinfo=None)
                call.DurationSec = max(0, int((utcnow().replace(tzinfo=None) - created).total_seconds()))
            if (call.Status or "").lower() not in _TERMINAL_CALL_STATUSES:
                call.Status = "completed"
            call.UpdatedAt = utcnow()
            call.UpdatedBy = "pipecat-session"
            db.commit()
        except Exception:  # noqa: BLE001 — bookkeeping must never block call shutdown
            logger.warning("[LeadAI voice] could not finalize call %s", self.call_sid, exc_info=True)
            db.rollback()
        finally:
            db.close()

    async def close(self) -> None:
        """Flush the live transcript, finish outstanding scoring (bounded), then stop the worker.
        Call when the call ends."""
        if self._broadcasts:
            await asyncio.wait({*self._broadcasts}, timeout=2.0)     # let the last lines reach the UI
        try:
            await asyncio.to_thread(self._finalize_call_sync)
        except Exception:  # noqa: BLE001 — never let bookkeeping block shutdown
            logger.warning("[LeadAI voice] call finalize failed for %s", self.call_sid, exc_info=True)
        if self._scoring_task is None:
            return
        self._scoring.put_nowait(None)
        try:
            await asyncio.wait_for(self._scoring_task, timeout=SCORING_DRAIN_SECONDS)
        except asyncio.TimeoutError:
            logger.warning("[LeadAI voice] scoring did not finish within %ss for %s",
                           SCORING_DRAIN_SECONDS, self.call_sid)
            self._scoring_task.cancel()
        self._scoring_task = None
