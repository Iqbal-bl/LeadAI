"""Routing, authentication and assembly of the Pipecat phone pipeline, with no phone audio.

  * routing: legacy stays the default; canary sends only chosen numbers; pipecat sends every
    LeadAI call; a call without LeadAI context never leaves the legacy pipeline;
  * authentication: the public websocket refuses anyone without a valid stream token for THIS
    call, using the platform's real token code (core.auth), before any paid service starts;
  * assembly: the real user-turn aggregator + the LeadAI brain, wired between stand-in
    speech services, turn a caller's finished sentence into a reply for the speech stage.

What this cannot cover is real audio through Twilio and Sarvam; that needs a live test call.
Run: python tests/test_voice_pipecat_pipeline.py
"""
import asyncio
import sys
import types
import warnings

warnings.filterwarnings("ignore")

# Real token code first: conftest_stub replaces core.auth with a stub that has no token functions.
from core.auth import _decode_token, create_access_token, create_stream_token  # noqa: E402

import conftest_stub  # noqa: E402,F401

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from pipecat.frames.frames import (  # noqa: E402
    LLMTextFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameProcessor  # noqa: E402
from pipecat_harness import SleepFrame, run_stage  # noqa: E402

from LeadAI import integration  # noqa: E402
from LeadAI.config import settings as real_settings  # noqa: E402
from LeadAI.voice import pipeline, routing  # noqa: E402
from LeadAI.voice.brain import BrainReply  # noqa: E402


class _S:
    def __init__(self, mode="legacy", numbers=""):
        self.voice_pipeline, self.voice_pipecat_numbers = mode, numbers

    def __getattr__(self, name):
        return getattr(real_settings, name)


def route(mode, call, numbers=""):
    saved = routing.settings
    routing.settings = _S(mode, numbers)
    try:
        return routing.stream_path(call)
    finally:
        routing.settings = saved


LEADAI_CALL = {"leadai": True, "client_id": "c1", "conversation_id": "v1", "phone_number": "+919876543210"}


# ------------------------------------------------------------------------ routing
def test_legacy_mode_and_unknown_modes_never_move_a_call():
    assert route("legacy", LEADAI_CALL) == "/media-stream"
    assert route("", LEADAI_CALL) == "/media-stream" and route("banana", LEADAI_CALL) == "/media-stream"


def test_canary_moves_only_the_chosen_numbers_in_any_format():
    for allowed in ("9876543210", "+91 98765-43210", "919876543210", "+1 555 000 1111, 9876543210"):
        assert route("canary", LEADAI_CALL, allowed) == "/media-stream-pipecat", allowed
    other = dict(LEADAI_CALL, phone_number="+919999999999")
    assert route("canary", other, "9876543210") == "/media-stream"
    assert route("canary", LEADAI_CALL, "") == "/media-stream"          # empty list: nobody


def test_pipecat_mode_moves_every_leadai_call():
    assert route("pipecat", LEADAI_CALL) == "/media-stream-pipecat"


def test_a_call_without_leadai_context_never_leaves_the_legacy_pipeline():
    for call in (None, {}, {"phone_number": "+919876543210"}, dict(LEADAI_CALL, leadai=False),
                 {k: v for k, v in LEADAI_CALL.items() if k != "conversation_id"}):
        assert route("pipecat", call) == "/media-stream", call


# ------------------------------------------------------------------- authentication
def test_a_valid_token_for_this_call_is_accepted():
    pipeline.authenticate("CA1", create_stream_token("CA1"), decode=_decode_token)


def test_missing_forged_and_wrong_call_tokens_are_refused():
    good = create_stream_token("CA1")
    for call_sid, token, why in (
        ("CA1", None, "missing token"),
        ("CA1", "", "empty token"),
        ("CA1", good + "x", "forged"),
        ("CA2", good, "token belongs to another call"),
        (None, good, "no call sid"),
    ):
        try:
            pipeline.authenticate(call_sid, token, decode=_decode_token)
        except pipeline.CallRejected:
            continue
        raise AssertionError(f"accepted: {why}")


def test_an_access_token_cannot_open_a_media_stream():
    try:
        pipeline.authenticate("CA1", create_access_token("CA1"), decode=_decode_token)
    except pipeline.CallRejected:
        return
    raise AssertionError("an access token was accepted as a stream token")


def test_call_context_requires_leadai_company_and_conversation():
    fake = types.ModuleType("outbound.app")
    fake.active_calls = {"CA1": dict(LEADAI_CALL), "CA2": {"phone_number": "+91"}}
    saved = sys.modules.get("outbound.app")
    sys.modules["outbound.app"] = fake
    try:
        assert pipeline.load_call_context("CA1")["client_id"] == "c1"
        for sid in ("CA2", "CA-UNKNOWN"):
            try:
                pipeline.load_call_context(sid)
            except pipeline.CallRejected:
                continue
            raise AssertionError("accepted a call with no LeadAI context: " + sid)
    finally:
        if saved is None:
            sys.modules.pop("outbound.app", None)
        else:
            sys.modules["outbound.app"] = saved


# --------------------------------------------------- the public websocket endpoint
def _twilio_messages(call_sid, token, stream_sid="MZ1"):
    import json

    return [
        json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}),
        json.dumps({"event": "start", "start": {
            "streamSid": stream_sid, "callSid": call_sid, "accountSid": "AC1", "tracks": ["inbound"],
            "customParameters": ({"token": token} if token is not None else {}),
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}}),
    ]


def _connect(call_sid, token, active_calls):
    """Open the endpoint like Twilio does; return (close_code, services_built)."""
    built = []
    app = FastAPI()
    integration._register_voice_pipecat(app)
    fake = types.ModuleType("outbound.app")
    fake.active_calls = active_calls
    saved = (sys.modules.get("outbound.app"), pipeline.build_services, pipeline.authenticate)
    sys.modules["outbound.app"] = fake
    pipeline.build_services = lambda ctx: built.append(ctx)
    # The real token check (core.auth is stubbed inside this test process, so hand it over).
    real_authenticate = saved[2]
    pipeline.authenticate = lambda sid, tok: real_authenticate(sid, tok, decode=_decode_token)
    code = None
    try:
        with TestClient(app).websocket_connect("/media-stream-pipecat") as ws:
            for message in _twilio_messages(call_sid, token):
                ws.send_text(message)
            try:
                ws.receive_text()
            except WebSocketDisconnect as exc:
                code = exc.code
    finally:
        pipeline.build_services, pipeline.authenticate = saved[1], saved[2]
        if saved[0] is None:
            sys.modules.pop("outbound.app", None)
        else:
            sys.modules["outbound.app"] = saved[0]
    return code, built


def test_the_endpoint_closes_unauthenticated_connections_before_any_paid_service_starts():
    calls = {"CA1": dict(LEADAI_CALL)}
    good = create_stream_token("CA1")
    for label, sid, token in (
        ("no token", "CA1", None),
        ("forged token", "CA1", good + "x"),
        ("token for another call", "CA1", create_stream_token("CA9")),
    ):
        code, built = _connect(sid, token, calls)
        assert code == 1008, (label, code)
        assert built == [], label + ": a speech service was started for an unauthenticated call"


def test_a_valid_token_for_a_call_with_no_leadai_context_is_still_refused():
    code, built = _connect("CA5", create_stream_token("CA5"), {"CA5": {"phone_number": "+91"}})
    assert code == 1008 and built == []


# --------------------------------------------------------------------------- assembly
class _Pass(FrameProcessor):
    """A stage that does nothing but forward every frame."""

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class _SpeechCollector(FrameProcessor):
    """Stands in for text-to-speech: records what it would have spoken."""

    def __init__(self):
        super().__init__()
        self.spoken = []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            self.spoken.append(frame.text)
        await self.push_frame(frame, direction)


class _FakeSession:
    def __init__(self):
        self.heard, self.scored = [], []

    async def respond(self, text):
        self.heard.append(text)
        return BrainReply(text="Rates start at 8.5 percent.", message_id="m1")

    def after_reply(self, reply):
        self.scored.append(reply.message_id)

    def set_language(self, code):
        self.language = code

    def supersede(self):
        pass


def test_a_finished_sentence_reaches_the_brain_and_the_reply_reaches_the_speech_stage():
    tts, session = _SpeechCollector(), _FakeSession()
    pipe, _brain, _aggs = pipeline.assemble(
        transport_in=_Pass(), transport_out=_Pass(),
        services=pipeline.Services(stt=_Pass(), tts=tts), session=session)
    frames = [
        VADUserStartedSpeakingFrame(),
        TranscriptionFrame(text="what is the interest rate", user_id="caller", timestamp="2026-01-01T00:00:00Z"),
        VADUserStoppedSpeakingFrame(),
    ]
    asyncio.run(run_stage(pipe, frames + [SleepFrame(sleep=1.5)]))
    assert session.heard == ["what is the interest rate"]
    assert tts.spoken == ["Rates start at 8.5 percent."]
    assert session.scored == ["m1"]                 # the after-reply hook (scoring) ran for the reply


def test_speech_language_mapping_and_auto_detect():
    from pipecat.transcriptions.language import Language

    assert pipeline.sarvam_language("hi") == Language.HI_IN
    assert pipeline.sarvam_language("EN") == Language.EN_IN
    assert pipeline.sarvam_language("raj") == Language.HI_IN         # same fallback as the legacy loop
    assert pipeline.sarvam_language("multi") is None and pipeline.sarvam_language(None) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
