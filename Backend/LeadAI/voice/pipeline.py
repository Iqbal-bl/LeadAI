"""
The Pipecat phone pipeline: Twilio or Exotel websocket in, LeadAI brain in the middle, speech out.

    carrier audio -> [transport in] -> speech-to-text -> user turn detection (voice activity)
                  -> LeadAIBrainProcessor -> text-to-speech -> [transport out] -> carrier audio

Twilio and Exotel share this whole pipeline and the whole brain (services/voice_flow.py, which
uses OpenAI regardless of carrier). Only the transport in/out (this file's authenticate/build
serializer logic) is carrier-specific.

The websocket is public (a carrier cannot carry a user login), so the FIRST thing this does is
authenticate the connection — differently per carrier, because the two carriers hand us
different guarantees:

  * Twilio calls our own /outbound-twiml FRESH, per call, so we can hand back a short-lived
    signed token embedded in the stream URL and check it here. Strong: a stray connection
    cannot forge one.
  * Exotel's Voicebot/Stream applet is configured once, in Exotel's own dashboard (not in this
    codebase), with a WSS URL that does not change per call — so there is no per-call token to
    check. Instead, the CallSid the start event reports is looked up against our OWN database: a
    LeadCall row we created, for THIS provider, not yet in a terminal state, placed recently. That
    is weaker than Twilio's signed token (a correctly-guessed live CallSid within the time window
    would pass), but it is a real, testable check, and it is what Exotel's setup allows without a
    per-call dynamic URL. If Exotel later confirms the "HTTPS endpoint that returns a fresh WSS
    URL per call" contract, the same signed-token approach as Twilio could replace this.

Assembly (`assemble`) is separate from the network (`run_call`), so the wiring is tested with
stand-in services and no audio.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from pipecat.frames.frames import TranscriptionFrame, VADUserStoppedSpeakingFrame
from pipecat.processors.frame_processor import FrameProcessor

from ..config import settings
from ..services import telephony
from .brain import LeadAIBrainProcessor
from .session import CallSession

logger = logging.getLogger(__name__)

# A silent or dead call must not hold a pipeline (and its paid connections) open forever.
IDLE_TIMEOUT_SECONDS = 120
# A real incident: Sarvam's STT websocket connect hung mid-handshake and never raised —
# pipecat's own default setup timeout (20 s) is itself none too fast, and in that call it
# fired nearly 40 s late besides (something in the hang was blocking the event loop, not
# just taking a while). The caller heard total silence — the pre-composed opener never
# even got synthesized — until THEY gave up and hung up, ~57 s in. A bounded wait that
# fails fast into a clean hangup (see on_setup_timeout below) beats leaving a live person
# on a dead line for however long a wedged connection to a third party takes to give up.
PIPELINE_SETUP_TIMEOUT_SECONDS = 8.0
# Voice activity reports "silence" after this long, and Pipecat's Smart Turn model then
# decides whether the caller has really finished. 0.2 s is Pipecat's recommended value for
# that pairing; the first version used 0.6 s and added 0.4 s to EVERY turn for nothing (the
# log showed Smart Turn correctly waiting through mid-sentence pauses on its own).
VAD_STOP_SECONDS = 0.2
# Silero's own defaults (confidence 0.7, start_secs 0.2, min_volume 0.6) assume a
# clean microphone. Carrier-encoded phone audio (Twilio/Exotel, 8kHz) carries more
# line noise and encoding artifacts than that — background noise alone was enough
# to trip "user started speaking" mid-reply, broadcasting an interruption that cut
# the bot off (see brain.py's _call_ending guard for the other half of that fix).
# Raised the bar on all three: a higher confidence and a longer required run of
# speech-like audio before triggering, plus a higher volume floor, costs a little
# responsiveness on a genuinely quiet "yes" but stops noise alone from interrupting.
VAD_CONFIDENCE = 0.8
VAD_START_SECONDS = 0.35
VAD_MIN_VOLUME = 0.7
# Smart Turn decides whether a pause is the end of a thought. When it says "not finished" it
# still gives up after this much silence. Its default is 3 s: on the second live call "Okay,
# bye." was judged unfinished and the reply came 3 s later, after the caller had hung up. 1.5 s
# is enough for a natural breath. If the caller does carry on, the interrupted turn is dropped
# and its words merged into the next (see brain.py), so cutting in early is recoverable.
SMART_TURN_STOP_SECONDS = 1.5

WS_POLICY_VIOLATION = 1008
WS_INTERNAL_ERROR = 1011

# A CallSid presented over the Exotel websocket must belong to a call WE placed within this
# window. Bounds how long a correctly-guessed live CallSid could be replayed for.
EXOTEL_CALL_MAX_AGE_SECONDS = 600
# Statuses that mean the call is over: a connection claiming one of these CallSids is stale.
_TERMINAL_CALL_STATUSES = frozenset({"completed", "failed", "busy", "no-answer", "canceled"})


class CallRejected(Exception):
    """The call may not use this pipeline. The message is safe to log, not to send."""


async def handle_pipeline_setup_timeout(call_sid: str, provider: str) -> None:
    """A processor (almost always STT/TTS connecting to Sarvam) never finished
    setting up within PIPELINE_SETUP_TIMEOUT_SECONDS. The caller is on a silent
    line RIGHT NOW — hang up rather than let them sit there for however long the
    wedged connection takes to give up on its own (see the real incident recorded
    on PIPELINE_SETUP_TIMEOUT_SECONDS above).

    A plain function, not inlined in the event handler, so it's directly
    testable without constructing a full pipecat worker/transport.
    """
    logger.error("[LeadAI voice] call %s: pipeline setup never completed — hanging up", call_sid)
    await asyncio.to_thread(telephony.hangup, call_sid, provider)


# --------------------------------------------------------------------------- auth
def authenticate(call_sid: str | None, token: str | None, decode=None) -> None:
    """Twilio: accept only if `token` is a valid, unexpired stream token FOR this call."""
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


def authenticate_exotel(call_sid: str | None, *, db_session_factory=None) -> None:
    """Exotel: no per-call token exists (see the module docstring), so accept only a CallSid
    that matches a LeadCall WE placed via Exotel, recently, and that is not yet over."""
    from ..models import LeadCall, utcnow

    if not call_sid:
        raise CallRejected("no call sid in the start event")
    if db_session_factory is None:
        from ..db import session as db_session_factory

    db = db_session_factory()
    try:
        call = (
            db.query(LeadCall)
            .filter(LeadCall.CallSid == call_sid, LeadCall.Provider == "exotel", LeadCall.IsDeleted == False)  # noqa: E712
            .one_or_none()
        )
        if call is None:
            raise CallRejected("no LeadAI Exotel call record for this CallSid")
        if (call.Status or "").lower() in _TERMINAL_CALL_STATUSES:
            raise CallRejected(f"call {call_sid} already ended ({call.Status})")
        # DateTime columns round-trip as naive (no tzinfo) even though utcnow() is aware; compare
        # naive-to-naive so this does not depend on which DB driver preserves tzinfo.
        created = call.CreatedAt.replace(tzinfo=None) if call.CreatedAt else None
        now = utcnow().replace(tzinfo=None)
        age = (now - created).total_seconds() if created else None
        if age is None or age > EXOTEL_CALL_MAX_AGE_SECONDS or age < -60:
            raise CallRejected(f"call {call_sid} is outside the allowed connection window")
    finally:
        db.close()


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


def deepgram_language(code: str | None):
    """A Deepgram language for our short codes; None lets Nova auto-detect.

    Deepgram's codes are plain (no "-IN" region suffix the way Sarvam's are) —
    same short codes we already use, so this is the same table shape as
    sarvam_language() with the generic (non-regional) Language members.
    """
    from pipecat.transcriptions.language import Language

    table = {
        "hi": Language.HI, "hi-in": Language.HI, "raj": Language.HI,
        "en": Language.EN, "en-in": Language.EN,
        "bn": Language.BN, "gu": Language.GU, "kn": Language.KN, "ml": Language.ML,
        "mr": Language.MR, "od": Language.OR, "pa": Language.PA, "ta": Language.TA,
        "te": Language.TE,
    }
    return table.get((code or "").strip().lower())


@dataclass
class Services:
    """The speech services. Real ones talk to Sarvam or Deepgram; tests pass stand-ins."""

    stt: Any
    tts: Any
    # code ("hi-IN") -> a frame that switches the text-to-speech language, or None.
    language_frame: Any = None


def _build_sarvam_services(context: dict) -> Services:
    from pipecat.services.sarvam.stt import SarvamSTTService
    from pipecat.services.sarvam.tts import SarvamTTSService

    api_key = os.getenv("SARVAM_API_KEY")
    if not api_key:
        raise CallRejected("SARVAM_API_KEY is not set")
    language = sarvam_language(context.get("language")) if not context.get("multi_stt") else None
    stt_settings = SarvamSTTService.Settings(language=language) if language else SarvamSTTService.Settings()
    # 1.0 is Sarvam's normal speaking speed; valid range on bulbul:v3 is 0.5-2.0.
    # 1.1 (slightly faster) is the platform default; a super admin can override
    # it per company (see LeadCompanySettings.VoiceSpeed) — never a company admin.
    # "anushka" isn't in bulbul:v3's speaker roster (it's a leftover bulbul:v2
    # name) — only reached if company_voice_settings() itself somehow handed
    # back nothing, but kept valid for the same reason that one is "ritu".
    tts_kwargs = {"voice": context.get("speaker") or "ritu", "pace": context.get("pace") or 1.1}
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
        # mode="transcribe" is NOT the library's claimed default here — the SDK only sends a
        # `mode` at all when one is explicitly given (self._mode is None by default and the
        # connect payload skips the field entirely), so Sarvam's own server-side default
        # applied, which turned out to be translate-to-English: a Hindi caller's "kya hum
        # hindi mein baat kar sakte hain" came back as the English transcript "Can we talk
        # in Hindi?" with language_code en-IN. Explicit "transcribe" asks for the reply in
        # the language actually spoken, in its own script, instead of an English gloss.
        stt=SarvamSTTService(api_key=api_key, mode="transcribe", settings=stt_settings),
        tts=SarvamTTSService(api_key=api_key, settings=SarvamTTSService.Settings(**tts_kwargs)),
        language_frame=language_frame,
    )


def _build_deepgram_services(context: dict) -> Services:
    """Deepgram Nova (STT) + Aura (TTS). Switched to per company via
    LeadCompanySettings.SttTtsProvider — see routers/companies.py's
    update_voice_settings (super-admin only).

    Deliberately ignores context["speaker"]/["gender"]/["pace"]: those are
    Sarvam voice ids and a 0.5-2.0 pace range, neither valid for Deepgram's
    Aura voices (fixed voice-name-per-language-and-gender, 0.7-1.5 speed) —
    passing a Sarvam value through would 400 against Deepgram's API exactly
    like the old hardcoded "anushka" default 400'd against bulbul:v3.

    Aura's Indic-language coverage is far narrower than Sarvam bulbul's at
    time of writing — mainly English and Spanish voices. A company whose
    callers speak Hindi/other Indic languages should stay on Sarvam; this
    path exists for companies that specifically want Deepgram's English
    accuracy/latency, not as a drop-in replacement for every company.
    """
    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.deepgram.tts import DeepgramTTSService

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise CallRejected("DEEPGRAM_API_KEY is not set")
    language = deepgram_language(context.get("language")) if not context.get("multi_stt") else None
    stt_settings = DeepgramSTTService.Settings(language=language) if language else DeepgramSTTService.Settings()

    return Services(
        stt=DeepgramSTTService(api_key=api_key, settings=stt_settings),
        tts=DeepgramTTSService(api_key=api_key),   # fixed "aura-2-helena-en" default voice/speed
        language_frame=None,   # Aura's voice name bakes in the language; no per-reply switch to make
    )


def build_services(context: dict) -> Services:
    """Dispatches to whichever speech vendor this company is configured for
    (see LeadCompanySettings.SttTtsProvider) — "sarvam" unless a super admin
    explicitly switched it."""
    if (context.get("provider") or "sarvam") == "deepgram":
        return _build_deepgram_services(context)
    return _build_sarvam_services(context)


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
        on_user_text=session.broadcast_user,
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

    Also times the STT round trip (VAD says the caller stopped -> STT finally delivers that
    utterance's transcript) and logs it when it's unusually slow. A real incident: a caller's
    utterance took ~10s to come back as a transcript even though Sarvam's own self-reported
    processing_latency was 78ms — the delay was somewhere in transit/queueing, not in Sarvam's
    transcription itself, and nothing surfaced that gap on its own; it took manually diffing
    raw DEBUG timestamps after the fact to even see it. This makes that visible without having
    to do that again.
    """

    # Above this, the gap is worth a log line of its own rather than scrolling past unremarked.
    _SLOW_STT_ROUND_TRIP_SECONDS = 3.0

    def __init__(self, *, on_language, **kwargs):
        super().__init__(**kwargs)
        self._on_language = on_language
        self._stopped_speaking_at: float | None = None

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, VADUserStoppedSpeakingFrame):
            self._stopped_speaking_at = time.monotonic()
        elif isinstance(frame, TranscriptionFrame):
            if self._stopped_speaking_at is not None:
                elapsed = time.monotonic() - self._stopped_speaking_at
                self._stopped_speaking_at = None
                if elapsed >= self._SLOW_STT_ROUND_TRIP_SECONDS:
                    logger.warning(
                        "[LeadAI voice] STT took %.1fs to return a transcript after the "
                        "caller stopped speaking (text=%r)", elapsed, frame.text[:80],
                    )
            if frame.language:
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
        if transport_type not in ("twilio", "exotel"):
            raise CallRejected(f"unsupported carrier {transport_type!r}")
        call_sid = call_data.call_id
        if transport_type == "twilio":
            authenticate(call_sid, (call_data.body or {}).get("token"))
        else:
            authenticate_exotel(call_sid)
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
    if transport_type == "twilio":
        serializer = _hangup_aware_serializer()(
            stream_sid=call_data.stream_id,
            call_sid=call_sid,
            account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
            auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
            on_ai_hangup=session.note_ai_hangup,
        )
        sample_rate = 8000
    else:
        from pipecat.serializers.exotel import ExotelFrameSerializer

        sample_rate = getattr(settings, "exotel_pipecat_sample_rate", 8000)
        serializer = ExotelFrameSerializer(
            stream_sid=call_data.stream_id,
            call_sid=call_sid,
            params=ExotelFrameSerializer.InputParams(exotel_sample_rate=sample_rate),
        )
    transport = FastAPIWebsocketTransport(
        websocket,
        FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
        ),
    )
    pipeline, brain, _aggregators = assemble(
        transport_in=transport.input(),
        transport_out=transport.output(),
        services=services,
        session=session,
        vad=SileroVADAnalyzer(params=VADParams(
            stop_secs=VAD_STOP_SECONDS,
            confidence=VAD_CONFIDENCE,
            start_secs=VAD_START_SECONDS,
            min_volume=VAD_MIN_VOLUME,
        )),
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(audio_in_sample_rate=sample_rate, audio_out_sample_rate=sample_rate),
        idle_timeout_secs=IDLE_TIMEOUT_SECONDS,
        enable_rtvi=False,
        setup_timeout_secs=PIPELINE_SETUP_TIMEOUT_SECONDS,
    )

    @worker.event_handler("on_setup_timeout")
    async def _on_setup_timeout(_worker):
        await handle_pipeline_setup_timeout(call_sid, transport_type)

    # Compose the opening line WHILE the pipeline is starting, not after: the first live call
    # spent about 3 s of dead air generating the greeting only once the pipeline was ready.
    opening_task = asyncio.create_task(session.opening())

    @transport.event_handler("on_client_connected")
    async def _on_connected(_transport, _client):
        connected_at = time.monotonic()
        try:
            opening = await opening_task
        except Exception:  # noqa: BLE001 — no greeting is better than no call
            logger.exception("[LeadAI voice] could not compose the opening line")
            return
        frames = opening_frames(opening, services)
        if frames:
            # Tells the brain an opener was actually queued — only then is a
            # bare "Hello?" overlapping it treated as noise rather than a
            # real question (see brain.py's mark_opener_spoken()).
            brain.mark_opener_spoken()
        for frame in frames:
            await worker.queue_frame(frame)
        if frames:
            # A real incident: pipeline setup reported fully ready, but TTS
            # didn't start generating the (already-composed) opener until
            # ~4.3s later — with nothing logged in between to say why. This
            # timestamp pins down OUR side of that gap precisely, so the next
            # time it's slow, whatever's logged next (pipecat's own "Generating
            # TTS [...]" debug line) shows exactly how much of the delay is
            # on our side of queue_frame() versus pipecat's own worker.
            logger.info(
                "[LeadAI voice] call %s: opener queued %.2fs after client-connected",
                call_sid, time.monotonic() - connected_at,
            )

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
