"""
The Pipecat stage where an LLM service normally sits.

Pipecat's user aggregator decides when the caller has finished speaking (voice activity plus
transcription) and hands the conversation on as an `LLMContextFrame`. A normal bot passes
that to an LLM service. Here it goes to LeadAI's own brain instead, which retrieves from the
company's knowledge, applies the confidence and handoff rules, records a decision trace and
honours pause/terminate. The reply comes back out as the same frames an LLM service would
emit, so the text-to-speech stage and the transport need to know nothing about LeadAI.

Rules this stage keeps:
  * it never lets a failure end the call: any error becomes a short spoken apology;
  * it never answers a provisional (speculative) turn;
  * it never forwards the context frame, exactly like an LLM service;
  * PEOPLE TALK IN BURSTS. If the caller speaks again while a reply is being prepared, that
    reply is never spoken, the brain is told to discard the turn (nothing saved), and the
    words are carried into the next turn so the model answers the whole thought once. The
    second live call fired three turns in four seconds for one thought, saved two replies
    the caller never heard, and answered only the last fragment.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pipecat.frames.frames import (
    EndWorkerFrame,
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)

APOLOGY = "Sorry, I had a technical problem. Could you please say that again?"


@dataclass
class BrainReply:
    text: str = ""
    ends_call: bool = False        # hang up once this has been spoken
    skipped: bool = False          # paused/terminated: nothing to say
    superseded: bool = False       # the caller spoke again first: nothing was saved, say nothing
    message_id: str | None = None  # the stored AI message, for the later scoring phase
    language: str | None = None    # the language this reply is in (the caller's), e.g. "hi-IN"


Respond = Callable[[str], Awaitable[BrainReply]]
AfterReply = Callable[[BrainReply], None]
OnSupersede = Callable[[], None]
LanguageFrame = Callable[[str], "Frame | None"]


def last_user_text(context) -> str:
    """The caller's latest words, from the context the aggregator built."""
    try:
        messages = context.get_messages()
    except Exception:  # noqa: BLE001
        return ""
    for message in reversed(messages or []):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            return " ".join(parts).strip()
    return ""


class LeadAIBrainProcessor(FrameProcessor):
    def __init__(
        self,
        *,
        respond: Respond,
        after_reply: AfterReply | None = None,
        on_supersede: OnSupersede | None = None,
        language_frame: LanguageFrame | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._respond = respond
        self._after_reply = after_reply
        self._on_supersede = on_supersede
        self._language_frame = language_frame
        # Bumped whenever the caller (re)starts speaking; a reply prepared under an older
        # generation is stale and is not spoken.
        self._generation = 0
        self._inflight: str | None = None   # what is being answered right now
        self._carry = ""                    # words from a turn that was superseded
        self._tts_language: str | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, (InterruptionFrame, UserStartedSpeakingFrame)):
            self._generation += 1
            self._supersede_inflight()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMContextFrame):
            # A provisional inference, run before the caller has really finished. Answering
            # it would store a reply to half a sentence.
            if getattr(frame, "speculation", None):
                return
            await self._answer(last_user_text(frame.context))
            return

        await self.push_frame(frame, direction)

    def _supersede_inflight(self) -> None:
        """The caller spoke again: whatever is being prepared will not be spoken."""
        if self._inflight is None:
            return
        self._carry = self._inflight          # their words must not be lost
        self._inflight = None
        if self._on_supersede is not None:
            try:
                self._on_supersede()
            except Exception:  # noqa: BLE001
                logger.warning("[LeadAI voice] supersede hook failed", exc_info=True)

    async def _answer(self, text: str) -> None:
        if not text:
            return
        if self._carry:
            # Earlier words of the same thought, from a turn that was superseded.
            text = f"{self._carry} {text}".strip()
            self._carry = ""
        generation = self._generation
        self._inflight = text
        try:
            reply = await self._respond(text)
        except asyncio.CancelledError:
            # Pipecat cancels this stage when the caller interrupts.
            self._supersede_inflight()
            raise
        except Exception:  # noqa: BLE001 — a bug in the brain must not drop the call
            logger.exception("[LeadAI voice] brain failed; speaking an apology")
            reply = BrainReply(text=APOLOGY)
            self._inflight = None
        else:
            if generation == self._generation:
                self._inflight = None

        if generation != self._generation or reply.superseded:
            logger.info("[LeadAI voice] dropped a reply: the caller spoke again first")
            return

        if reply.text:
            if reply.language and self._language_frame is not None and reply.language != self._tts_language:
                # Speak in the language the reply is written in, before the text arrives.
                switch = self._language_frame(reply.language)
                if switch is not None:
                    await self.push_frame(switch)
                self._tts_language = reply.language
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMTextFrame(text=reply.text))
            await self.push_frame(LLMFullResponseEndFrame())
        if self._after_reply is not None:
            try:
                self._after_reply(reply)
            except Exception:  # noqa: BLE001
                logger.warning("[LeadAI voice] after-reply hook failed", exc_info=True)
        if reply.ends_call or reply.skipped:
            # Pushed downstream so the frames queued ahead of it (the goodbye or the transfer
            # line) are spoken in full before the call ends.
            await self.push_frame(EndWorkerFrame(reason="handoff or end of conversation"))
