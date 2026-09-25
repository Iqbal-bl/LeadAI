"""
The Pipecat phone pipeline: Twilio websocket in, LeadAI brain in the middle, speech out.

    Twilio audio -> [transport in] -> speech-to-text -> user turn detection (voice activity)
                 -> LeadAIBrainProcessor -> text-to-speech -> [transport out] -> Twilio audio

The websocket is public (Twilio cannot carry a user login), so the FIRST thing this does is
verify the short-lived stream token that /outbound-twiml put in the call's start event, and
that it belongs to THIS call. An unauthenticated caller would otherwise get a free pipe into
paid speech and language services. The same rule the legacy /media-stream loop applies.

Assembly (`assemble`) is separate from the network (`run_call`), so the wiring is tested with
stand-in services and no audio.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

from pipecat.frames.frames import TranscriptionFrame
from pipecat.processors.frame_processor import FrameProcessor

from ..config import settings
from .brain import LeadAIBrainProcessor
from .session import CallSession

logger = logging.getLogger(__name__)

# A silent or dead call must not hold a pipeline (and its paid connections) open forever.
IDLE_TIMEOUT_SECONDS = 120
# Voice activity reports "silence" after this long, and Pipecat's Smart Turn model then
# decides whether the caller has really finished. 0.2 s is Pipecat's recommended value for
# that pairing; the first version used 0.6 s and added 0.4 s to EVERY turn for nothing (the
# log showed Smart Turn correctly waiting through mid-sentence pauses on its own).
VAD_STOP_SECONDS = 0.2
# Smart Turn decides whether a pause is the end of a thought. When it says "not finished" it
# still gives up after this much silence. Its default is 3 s: on the second live call "Okay,
# bye." was judged unfinished and the reply came 3 s later, after the caller had hung up. 1.5 s
# is enough for a natural breath. If the caller does carry on, the interrupted turn is dropped
# and its words merged into the next (see brain.py), so cutting in early is recoverable.
SMART_TURN_STOP_SECONDS = 1.5

WS_POLICY_VIOLATION = 1008
WS_INTERNAL_ERROR = 1011


class CallRejected(Exception):
    """The call may not use this pipeline. The message is safe to log, not to send."""


# --------------------------------------------------------------------------- auth
def authenticate(call_sid: str | None, token: str | None, decode=None) -> None:
    """Accept the call only if `token` is a valid, unexpired stream token FOR this call."""
    if not call_sid:
        raise CallRejected("no call sid in the start event")
    if not token:
        raise CallRejected("missing stream token")
    if decode is None:
        from core.auth import _decode_token as decode  # the same check the legacy loop uses
    try:
        payload = decode(token, "stream")
    except Exception as exc:  # noqa: BLE001 — HTTPException from the auth module
        raise CallRejected(f"invalid stream token ({exc.__class__.__name__})") from exc
    if payload.get("sub") != call_sid:
        raise CallRejected("stream token was issued for a different call")


def load_call_context(call_sid: str) -> dict:
    """The per-call config the call-placing code registered (company, conversation, voice)."""
    from outbound.app import active_calls

    data = active_calls.get(call_sid) or {}
    if not (data.get("leadai") and data.get("client_id") and data.get("conversation_id")):
        raise CallRejected("call has no LeadAI company/conversation; it belongs on the legacy pipeline")
    return data


# ------------------------------------------------------------------------ language
def sarvam_language(code: str | None):
    """A Sarvam language for our short codes; None lets Sarvam detect the language."""
    from pipecat.transcriptions.language import Language

    table = {
        "hi": Language.HI_IN, "hi-in": Language.HI_IN, "raj": Language.HI_IN,   # Rajasthani: Hindi, as legacy
        "en": Language.EN_IN, "en-in": Language.EN_IN,
        "bn": Language.BN_IN, "gu": Language.GU_IN, "kn": Language.KN_IN, "ml": Language.ML_IN,
        "mr": Language.MR_IN, "od": Language.OR_IN, "pa": Language.PA_IN, "ta": Language.TA_IN,
        "te": Language.TE_IN,
    }
    return table.get((code or "").strip().lower())        # "multi" / unknown -> auto-detect


@dataclass
class Services:
    """The speech services. Real ones talk to Sarvam; tests pass stand-ins."""

    stt: Any
    tts: Any
    # code ("hi-IN") -> a frame that switches the text-to-speech language, or None.
    language_frame: Any = None


def build_services(context: dict) -> Services:
    """Sarvam speech-to-text and text-to-speech, the same vendor the legacy loop uses."""
    from pipecat.services.sarvam.stt import SarvamSTTService
    from pipecat.services.sarvam.tts import SarvamTTSService

    api_key = os.getenv("SARVAM_API_KEY")
    if not api_key:
        raise CallRejected("SARVAM_API_KEY is not set")
    language = sarvam_language(context.get("language")) if not context.get("multi_stt") else None
    stt_settings = SarvamSTTService.Settings(language=language) if language else SarvamSTTService.Settings()
    tts_kwargs = {"voice": context.get("speaker") or "anushka"}
    if language:
        tts_kwargs["language"] = language
    def language_frame(code: str):
        """Speak in the language the reply is written in (Sarvam re-sends its config on the
        open connection, so this is cheap). Without it the voice stayed on the call's default
        language while the text was in another."""
        from pipecat.frames.frames import TTSUpdateSettingsFrame

        lang = sarvam_language(code)
        return TTSUpdateSettingsFrame(delta=SarvamTTSService.Settings(language=lang)) if lang else None

    return Services(
        stt=SarvamSTTService(api_key=api_key, settings=stt_settings),
        tts=SarvamTTSService(api_key=api_key, settings=SarvamTTSService.Settings(**tts_kwargs)),
        language_frame=language_frame,
    )


# ----------------------------------------------------------------------- assembly
def assemble(*, transport_in, transport_out, services: Services, session: CallSession, vad=None):
    """Wire the processors together. Returns (pipeline, brain, context_aggregators)."""
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )

    from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
    from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
        TurnAnalyzerUserTurnStopStrategy,
    )
    from pipecat.turns.user_turn_strategies import UserTurnStrategies

    turn_strategies = UserTurnStrategies(
        stop=[
            TurnAnalyzerUserTurnStopStrategy(
                turn_analyzer=LocalSmartTurnAnalyzerV3(params=SmartTurnParams(stop_secs=SMART_TURN_STOP_SECONDS))
            )
        ]
    )
    aggregators = LLMContextAggregatorPair(
        LLMContext(), user_params=LLMUserAggregatorParams(vad_analyzer=vad, user_turn_strategies=turn_strategies)
    )
    brain = LeadAIBrainProcessor(
        respond=session.respond,
        after_reply=session.after_reply,
        on_supersede=session.supersede,
        language_frame=services.language_frame,
    )
    pipeline = Pipeline(
        [
            transport_in,
            services.stt,
            LanguageTracker(on_language=session.set_language),
            aggregators.user(),
            brain,
            services.tts,
            transport_out,
            aggregators.assistant(),
        ]
    )
    return pipeline, brain, aggregators


def _hangup_aware_serializer():
    """TwilioFrameSerializer that tells us the moment it hangs up the call itself."""
    from pipecat.serializers.twilio import TwilioFrameSerializer

    class LeadAITwilioSerializer(TwilioFrameSerializer):
        def __init__(self, *args, on_ai_hangup=None, **kwargs):
            super().__init__(*args, **kwargs)
            self._on_ai_hangup = on_ai_hangup

        async def _hang_up_call(self):
            if self._on_ai_hangup is not None:
                try:
                    self._on_ai_hangup()
                except Exception:  # noqa: BLE001 — bookkeeping must never stop the hang-up
                    logger.debug("hangup hook failed", exc_info=True)
            await super()._hang_up_call()

    return LeadAITwilioSerializer


def opening_frames(opening, services) -> list:
    """What to queue when the call connects: switch the voice to the opener's language if it
    is not the default, then speak it. Empty when there is nothing to say."""
    from pipecat.frames.frames import TTSSpeakFrame

    if not opening.text:
        return []
    frames = []
    if opening.language and services.language_frame is not None:
        switch = services.language_frame(opening.language)
        if switch is not None:
            frames.append(switch)
    frames.append(TTSSpeakFrame(opening.text))
    return frames


class LanguageTracker(FrameProcessor):
    """Remembers the language speech-to-text reports for each utterance.

    Sits between speech-to-text and the turn aggregator, which consumes transcripts, so the
    language would otherwise be lost before the brain runs. Forwards every frame untouched.
    """

    def __init__(self, *, on_language, **kwargs):
        super().__init__(**kwargs)
        self._on_language = on_language

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and frame.language:
            try:
                self._on_language(getattr(frame.language, "value", str(frame.language)))
            except Exception:  # noqa: BLE001 — tracking must never disturb the audio path
                logger.debug("language tracker hook failed", exc_info=True)
        await self.push_frame(frame, direction)


# --------------------------------------------------------------------- the call
async def run_call(websocket, *, services_factory=build_services, session_factory=None) -> None:
    """Serve one Twilio media-stream connection from start to hang-up."""
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.runner.utils import parse_telephony_websocket
    from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
    from pipecat.workers.runner import WorkerRunner

    await websocket.accept()
    session: CallSession | None = None
    try:
        transport_type, call_data = await parse_telephony_websocket(websocket)
        if transport_type != "twilio":
            raise CallRejected(f"unsupported carrier {transport_type!r}")
        call_sid = call_data.call_id
        authenticate(call_sid, (call_data.body or {}).get("token"))
        context = load_call_context(call_sid)
        services = services_factory(context)
    except CallRejected as exc:
        logger.warning("[LeadAI voice] pipecat call rejected: %s", exc)
        await websocket.close(code=WS_POLICY_VIOLATION)
        return
    except Exception:  # noqa: BLE001
        logger.exception("[LeadAI voice] pipecat call could not start")
        await websocket.close(code=WS_INTERNAL_ERROR)
        return

    logger.info("[LeadAI voice] pipecat pipeline starting for call %s", call_sid)
    session = CallSession(
        client_id=context["client_id"], conversation_id=context["conversation_id"], call_sid=call_sid,
        phone_number=context.get("phone_number"),
        **({"session_factory": session_factory} if session_factory else {}),
    )
    transport = FastAPIWebsocketTransport(
        websocket,
        FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=_hangup_aware_serializer()(
                stream_sid=call_data.stream_id,
                call_sid=call_sid,
                account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
                auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
                on_ai_hangup=session.note_ai_hangup,
            ),
        ),
    )
    pipeline, _brain, _aggregators = assemble(
        transport_in=transport.input(),
        transport_out=transport.output(),
        services=services,
        session=session,
        vad=SileroVADAnalyzer(params=VADParams(stop_secs=VAD_STOP_SECONDS)),
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(audio_in_sample_rate=8000, audio_out_sample_rate=8000),
        idle_timeout_secs=IDLE_TIMEOUT_SECONDS,
        enable_rtvi=False,
    )

    # Compose the opening line WHILE the pipeline is starting, not after: the first live call
    # spent about 3 s of dead air generating the greeting only once the pipeline was ready.
    opening_task = asyncio.create_task(session.opening())

    @transport.event_handler("on_client_connected")
    async def _on_connected(_transport, _client):
        try:
            opening = await opening_task
        except Exception:  # noqa: BLE001 — no greeting is better than no call
            logger.exception("[LeadAI voice] could not compose the opening line")
            return
        for frame in opening_frames(opening, services):
            await worker.queue_frame(frame)

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnected(_transport, _client):
        await worker.cancel()

    session.start()
    try:
        # No signal handlers: this pipeline lives inside a web server that owns them (and
        # asyncio cannot install them on Windows).
        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(worker)
        await runner.run()
    finally:
        if not opening_task.done():
            opening_task.cancel()
        await session.close()          # finish scoring the last turns before letting go
        logger.info("[LeadAI voice] pipecat pipeline finished for call %s", call_sid)
