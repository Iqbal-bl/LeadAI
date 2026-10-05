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
import re
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

# Real call: the opener ("Hi Priya, this is Kabir from Kestrel Homes...") is queued
# the instant the transport connects, but Twilio audio is already flowing in before
# that — a caller's reflexive "Hello?" on picking up, said while the opener is still
# being generated/spoken, was transcribed as a real first turn. The brain, with
# nothing substantive to answer, replied with another greeting-shaped line ("Hi
# Priya, I'm here to help...") right after the opener — sounding like two greetings
# back to back. Matched only against the CALL'S FIRST turn (see _greeted_once below):
# a bare "hello?" later in the call (e.g. checking the line is still live) still gets
# a real answer.
_BARE_GREETING = re.compile(r"^(hello+|he+llo+|hi+|hey+a*|helo+)[\s.,!?]*$", re.IGNORECASE)


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
OnUserText = Callable[[str], None]
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
        on_user_text: OnUserText | None = None,
        language_frame: LanguageFrame | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._respond = respond
        self._after_reply = after_reply
        self._on_supersede = on_supersede
        self._on_user_text = on_user_text
        self._language_frame = language_frame
        # Bumped whenever the caller (re)starts speaking; a reply prepared under an older
        # generation is stale and is not spoken.
        self._generation = 0
        self._inflight: str | None = None   # what is being answered right now
        self._carry = ""                    # words from a turn that was superseded
        self._tts_language: str | None = None
        # Set once a reply that ends the call is on its way out. A VAD false
        # positive (background noise, mic bleed-through from the bot's own
        # voice) during that farewell was broadcasting an interruption that
        # cut the TTS off mid-sentence — "Of course." then silence, instead
        # of the whole goodbye _answer() below already queues in full. There
        # is no next turn to prepare for once the call is ending, so nothing
        # downstream needs the interruption; it is swallowed here instead.
        self._call_ending = False
        # True once the call's first customer turn has been handled, however it
        # was handled — used only to decide whether a BARE "hello" deserves the
        # bare-greeting-overlap suppression below, not to gate anything else.
        self._greeted_once = False
        # False until the pipeline confirms an opener was actually queued to be
        # spoken (see mark_opener_spoken() / voice/pipeline.py's _on_connected).
        # Deliberately NOT inferred from "is this the first turn" alone: a
        # company with no opener text at all (opening.text empty) means the
        # caller heard nothing, so their "Hello?" is the only prompt they get —
        # suppressing it would leave them talking to silence, a worse bug than
        # the one being fixed here.
        self._opener_spoken = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, (InterruptionFrame, UserStartedSpeakingFrame)):
            if self._call_ending:
                return
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

    def mark_opener_spoken(self) -> None:
        """Called once the pipeline has actually queued the opener to be
        spoken (non-empty opening.text) — see voice/pipeline.py."""
        self._opener_spoken = True

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
        if self._on_user_text is not None:
            # What the caller JUST said, the moment it is recognised (the live transcript), and
            # before any thinking. Only the new words: a superseded turn's words were already
            # announced when that turn began, so merging them below must not announce them twice.
            try:
                self._on_user_text(text)
            except Exception:  # noqa: BLE001 — the live view must never disturb the call
                logger.debug("user-text hook failed", exc_info=True)

        if not self._greeted_once:
            self._greeted_once = True
            if self._opener_spoken and not self._carry and _BARE_GREETING.match(text.strip()):
                logger.info(
                    "[LeadAI voice] caller's first words were just a bare greeting (%r) "
                    "overlapping the opener — absorbed, not answered again", text,
                )
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

        if reply.ends_call or reply.skipped:
            # Set BEFORE the text frames go out: TTS for this farewell starts
            # the moment they're pushed, and a VAD blip can fire within a few
            # hundred ms of the bot starting to speak.
            self._call_ending = True

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
