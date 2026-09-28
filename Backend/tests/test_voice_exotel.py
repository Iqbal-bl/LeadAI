"""Exotel calls on the Pipecat pipeline: routing (which flow to dial), authentication, and
carrier-specific transport wiring. Twilio's own tests (test_voice_pipecat_pipeline.py) are
untouched by any of this — this file only adds Exotel coverage alongside them.

WHY EXOTEL'S AUTH IS DIFFERENT FROM TWILIO'S
Twilio calls our own /outbound-twiml fresh, per call, so we can hand back a short-lived signed
token embedded in the stream URL. Exotel's Voicebot/Stream applet is configured once, in
Exotel's own dashboard (external to this repo), with a URL that does not change per call — so
there is no per-call token to check. Instead, the CallSid is looked up against our own database:
a LeadCall row we placed, for THIS provider, not yet terminal, placed recently. See
LeadAI/voice/pipeline.py's module docstring for the full reasoning, including why this is
weaker than Twilio's check and what would strengthen it later.

Run: python tests/test_voice_exotel.py
"""
import asyncio
import json
import sys
import types
import uuid
from datetime import timedelta

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from LeadAI import models  # noqa: E402
from LeadAI import integration  # noqa: E402
from LeadAI.config import settings as real_settings  # noqa: E402
from LeadAI.models import utcnow  # noqa: E402
from LeadAI.services import telephony  # noqa: E402
from LeadAI.voice import pipeline, routing  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def run(coro):
    return asyncio.run(coro)


# =================================================================== authenticate_exotel
def make_call(provider="exotel", status="in-progress", age_seconds=30, call_sid=None):
    call_sid = call_sid or f"CAexo-{uuid.uuid4().hex[:12]}"
    db = SessionLocalAdmin()
    call = models.LeadCall(
        ClientId="c1", ConversationId="conv-1", CallSid=call_sid, Provider=provider, Status=status,
    )
    db.add(call)
    db.flush()
    if age_seconds is not None:
        call.CreatedAt = utcnow() - timedelta(seconds=age_seconds)
    db.commit()
    return db


def make_call_with_sid(**kwargs):
    """make_call(), also handing back the CallSid it used (a fresh one unless given)."""
    sid = kwargs.get("call_sid") or f"CAexo-{uuid.uuid4().hex[:12]}"
    kwargs["call_sid"] = sid
    return make_call(**kwargs), sid


def test_a_recent_non_terminal_exotel_call_is_accepted():
    db, sid = make_call_with_sid()
    pipeline.authenticate_exotel(sid, db_session_factory=lambda: db)  # must not raise


def test_an_unknown_callsid_is_rejected():
    db, _sid = make_call_with_sid()
    try:
        pipeline.authenticate_exotel("CA-does-not-exist", db_session_factory=lambda: db)
    except pipeline.CallRejected:
        return
    raise AssertionError("an unknown CallSid was accepted")


def test_a_twilio_call_row_with_the_same_sid_does_not_authenticate_the_exotel_socket():
    db, sid = make_call_with_sid(provider="twilio")
    try:
        pipeline.authenticate_exotel(sid, db_session_factory=lambda: db)
    except pipeline.CallRejected:
        return
    raise AssertionError("a call placed on a different carrier was accepted")


def test_a_call_already_over_is_rejected():
    for status in ("completed", "failed", "busy", "no-answer", "canceled"):
        db, sid = make_call_with_sid(status=status)
        try:
            pipeline.authenticate_exotel(sid, db_session_factory=lambda: db)
        except pipeline.CallRejected:
            continue
        raise AssertionError(f"a call in terminal status {status!r} was accepted")


def test_a_stale_call_outside_the_time_window_is_rejected():
    db, sid = make_call_with_sid(age_seconds=pipeline.EXOTEL_CALL_MAX_AGE_SECONDS + 60)
    try:
        pipeline.authenticate_exotel(sid, db_session_factory=lambda: db)
    except pipeline.CallRejected:
        return
    raise AssertionError("a call far outside the connection window was accepted")


def test_a_call_from_the_future_is_rejected_too():
    # Guards against clock skew being exploited to extend the window indefinitely.
    db, sid = make_call_with_sid(age_seconds=-300)
    try:
        pipeline.authenticate_exotel(sid, db_session_factory=lambda: db)
    except pipeline.CallRejected:
        return
    raise AssertionError("a call timestamped in the future was accepted")


def test_no_call_sid_at_all_is_rejected():
    db, _sid = make_call_with_sid()
    try:
        pipeline.authenticate_exotel(None, db_session_factory=lambda: db)
    except pipeline.CallRejected:
        return
    raise AssertionError("a missing CallSid was accepted")


def test_the_db_session_is_always_closed():
    closed = []

    class TrackedSession:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def close(self):
            closed.append(True)

    real_db, sid = make_call_with_sid()
    tracked = TrackedSession(real_db)
    pipeline.authenticate_exotel(sid, db_session_factory=lambda: tracked)
    try:
        pipeline.authenticate_exotel("CA-missing", db_session_factory=lambda: tracked)
    except pipeline.CallRejected:
        pass
    assert closed == [True, True]              # closed on the accept path AND the reject path


# =================================================================== telephony routing
def with_settings(**overrides):
    class S:
        def __getattr__(self, name):
            return getattr(real_settings, name)

    s = S()
    for k, v in overrides.items():
        setattr(s.__class__, k, v)
    return s


def test_place_exotel_call_uses_the_pipecat_flow_when_routed_and_configured():
    seen = {}

    def fake_post(url, data=None, timeout=None):
        seen["url"] = data.get("Url")
        return _FakeResp({"Call": {"Sid": "CAnew1", "Status": "in-progress"}})

    saved = telephony.settings
    telephony.settings = with_settings(
        exotel_enabled=True, exotel_sid="SID1", exotel_api_key="k", exotel_api_token="t",
        exotel_subdomain="api.exotel.com", exotel_caller_id="+911111111111",
        exotel_flow_app_id="LEGACY_FLOW", exotel_pipecat_flow_app_id="PIPECAT_FLOW",
    )
    saved_post = _install_fake_httpx(fake_post)
    try:
        sid, status = telephony.place_exotel_call("+919999999999", "https://server", use_pipecat=True)
    finally:
        telephony.settings = saved
        _restore_httpx(saved_post)
    assert sid == "CAnew1" and "PIPECAT_FLOW" in seen["url"] and "LEGACY_FLOW" not in seen["url"]


def test_place_exotel_call_uses_the_legacy_flow_when_not_routed_to_pipecat():
    seen = {}

    def fake_post(url, data=None, timeout=None):
        seen["url"] = data.get("Url")
        return _FakeResp({"Call": {"Sid": "CAnew2", "Status": "in-progress"}})

    telephony.settings = with_settings(
        exotel_enabled=True, exotel_sid="SID1", exotel_api_key="k", exotel_api_token="t",
        exotel_subdomain="api.exotel.com", exotel_caller_id="+911111111111",
        exotel_flow_app_id="LEGACY_FLOW", exotel_pipecat_flow_app_id="PIPECAT_FLOW",
    )
    saved_post = _install_fake_httpx(fake_post)
    try:
        telephony.place_exotel_call("+919999999999", "https://server", use_pipecat=False)
    finally:
        telephony.settings = real_settings
        _restore_httpx(saved_post)
    assert "LEGACY_FLOW" in seen["url"] and "PIPECAT_FLOW" not in seen["url"]


def test_place_exotel_call_falls_back_to_legacy_flow_when_pipecat_flow_is_not_configured():
    """Routing asked for Pipecat, but nobody has created the second Exotel flow yet
    (EXOTEL_PIPECAT_FLOW_APP_ID unset). The call must still go through — on the flow that
    works today — rather than fail outright."""
    seen = {}

    def fake_post(url, data=None, timeout=None):
        seen["url"] = data.get("Url")
        return _FakeResp({"Call": {"Sid": "CAnew3", "Status": "in-progress"}})

    telephony.settings = with_settings(
        exotel_enabled=True, exotel_sid="SID1", exotel_api_key="k", exotel_api_token="t",
        exotel_subdomain="api.exotel.com", exotel_caller_id="+911111111111",
        exotel_flow_app_id="LEGACY_FLOW", exotel_pipecat_flow_app_id=None,
    )
    saved_post = _install_fake_httpx(fake_post)
    try:
        sid, _ = telephony.place_exotel_call("+919999999999", "https://server", use_pipecat=True)
    finally:
        telephony.settings = real_settings
        _restore_httpx(saved_post)
    assert sid == "CAnew3" and "LEGACY_FLOW" in seen["url"]              # the call still went out


def test_place_call_only_passes_use_pipecat_through_to_exotel_never_to_twilio():
    calls = {}

    def fake_place_exotel(to_number, server_url, use_pipecat=False):
        calls["exotel_use_pipecat"] = use_pipecat
        return "CAx", "in-progress"

    def fake_place_twilio(to_number, server_url):
        calls["twilio_called"] = True
        return "CAt", "initiated"

    telephony.settings = with_settings(effective_voice_provider="exotel")
    saved = (telephony.place_exotel_call, telephony.place_twilio_call)
    telephony.place_exotel_call, telephony.place_twilio_call = fake_place_exotel, fake_place_twilio
    try:
        sid, status, provider = telephony.place_call("+919999999999", "https://server", use_pipecat=True)
    finally:
        telephony.settings = real_settings
        telephony.place_exotel_call, telephony.place_twilio_call = saved
    assert provider == "exotel" and calls["exotel_use_pipecat"] is True and "twilio_called" not in calls


class _FakeResp:
    def __init__(self, body):
        self._body = body
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


class _FakeErrorResp:
    """A 4xx response whose body carries Exotel's actual reason for the rejection."""

    def __init__(self, status_code, body_text):
        self.status_code = status_code
        self.text = body_text

    def raise_for_status(self):
        import httpx

        raise httpx.HTTPStatusError(
            f"{self.status_code} error", request=httpx.Request("POST", "https://x"), response=self
        )

    def json(self):
        return {}


def _install_fake_httpx(fake_post):
    import httpx

    saved = httpx.post
    httpx.post = fake_post
    return saved


def _restore_httpx(saved):
    import httpx

    httpx.post = saved


# =========================================================== phone number normalisation
def test_a_caller_id_with_dashes_and_spaces_is_cleaned_before_it_reaches_exotel():
    # The real failure this reproduces: EXOTEL_CALLER_ID stored as "0987-654 3210" (a
    # human-readable paste) made Exotel's Calls/connect reject the call with 400.
    assert telephony._clean_exotel_number("0987-654 3210") == "09876543210"
    assert telephony._clean_exotel_number("+91 98765-43210") == "+919876543210"
    assert telephony._clean_exotel_number("9876543210") == "9876543210"          # already clean: unchanged
    assert telephony._clean_exotel_number(None) is None and telephony._clean_exotel_number("") == ""


def test_place_exotel_call_sends_cleaned_numbers_for_from_callerid_and_to():
    seen = {}

    def fake_post(url, data=None, timeout=None):
        seen.update(data)
        return _FakeResp({"Call": {"Sid": "CAclean1", "Status": "in-progress"}})

    telephony.settings = with_settings(
        exotel_enabled=True, exotel_sid="SID1", exotel_api_key="k", exotel_api_token="t",
        exotel_subdomain="api.exotel.com", exotel_caller_id="0987-654 3210",       # dirty, as in .env
        exotel_flow_app_id=None, exotel_pipecat_flow_app_id=None,                 # -> the "no flow" / To branch too
    )
    saved_post = _install_fake_httpx(fake_post)
    try:
        telephony.place_exotel_call("+91 98765-43210", "https://server")
    finally:
        telephony.settings = real_settings
        _restore_httpx(saved_post)
    assert seen["From"] == "+919876543210" and seen["CallerId"] == "09876543210" and seen["To"] == "09876543210"


def test_a_flow_based_call_does_not_send_statuscallback_params():
    # Exotel rejected a real call (400 "Invalid 'StatusCallbackEvents' specified") when these
    # were combined with a flow Url. Locks in that they stay out of that branch.
    seen = {}

    def fake_post(url, data=None, timeout=None):
        seen.update(data)
        return _FakeResp({"Call": {"Sid": "CAflow1", "Status": "in-progress"}})

    telephony.settings = with_settings(
        exotel_enabled=True, exotel_sid="SID1", exotel_api_key="k", exotel_api_token="t",
        exotel_subdomain="api.exotel.com", exotel_caller_id="+911111111111",
        exotel_flow_app_id="FLOW1", exotel_pipecat_flow_app_id=None,
    )
    saved_post = _install_fake_httpx(fake_post)
    try:
        telephony.place_exotel_call("+919999999999", "https://server", use_pipecat=False)
    finally:
        telephony.settings = real_settings
        _restore_httpx(saved_post)
    assert "Url" in seen
    assert "StatusCallback" not in seen and "StatusCallbackEvents[0]" not in seen


def test_a_plain_bridge_call_with_no_flow_still_sends_statuscallback_params():
    seen = {}

    def fake_post(url, data=None, timeout=None):
        seen.update(data)
        return _FakeResp({"Call": {"Sid": "CAbridge1", "Status": "in-progress"}})

    telephony.settings = with_settings(
        exotel_enabled=True, exotel_sid="SID1", exotel_api_key="k", exotel_api_token="t",
        exotel_subdomain="api.exotel.com", exotel_caller_id="+911111111111",
        exotel_flow_app_id=None, exotel_pipecat_flow_app_id=None,
    )
    saved_post = _install_fake_httpx(fake_post)
    try:
        telephony.place_exotel_call("+919999999999", "https://server")
    finally:
        telephony.settings = real_settings
        _restore_httpx(saved_post)
    assert "Url" not in seen and "To" in seen
    assert seen["StatusCallback"].endswith("/api/leadai/voice/exotel/status")
    assert seen["StatusCallbackEvents[0]"] == "terminal"


def test_a_rejected_call_surfaces_exotels_actual_error_message_not_just_the_status_code():
    def fake_post(url, data=None, timeout=None):
        return _FakeErrorResp(400, '{"RestException":{"Code":400,"Message":"CallerId is invalid"}}')

    telephony.settings = with_settings(
        exotel_enabled=True, exotel_sid="SID1", exotel_api_key="k", exotel_api_token="t",
        exotel_subdomain="api.exotel.com", exotel_caller_id="+911111111111",
        exotel_flow_app_id="FLOW1", exotel_pipecat_flow_app_id=None,
    )
    saved_post = _install_fake_httpx(fake_post)
    try:
        telephony.place_exotel_call("+919999999999", "https://server")
        raised = None
    except telephony.CallPlacementError as exc:
        raised = str(exc)
    finally:
        telephony.settings = real_settings
        _restore_httpx(saved_post)
    assert raised is not None and "CallerId is invalid" in raised          # not just "400 Bad Request"


# =================================================================== routing.use_pipecat_for
def test_use_pipecat_for_matches_use_pipecat_given_the_same_call():
    saved = routing.settings
    routing.settings = with_settings(voice_pipeline="pipecat", voice_pipecat_numbers="")
    try:
        assert routing.use_pipecat_for(client_id="c1", conversation_id="v1", phone_number="+919876543210") is True
        assert routing.use_pipecat_for(client_id="", conversation_id="v1", phone_number="+919876543210") is False
    finally:
        routing.settings = saved


def test_use_pipecat_for_respects_canary_numbers():
    saved = routing.settings
    routing.settings = with_settings(voice_pipeline="canary", voice_pipecat_numbers="9876543210")
    try:
        assert routing.use_pipecat_for(client_id="c1", conversation_id="v1", phone_number="+919876543210") is True
        assert routing.use_pipecat_for(client_id="c1", conversation_id="v1", phone_number="+911111111111") is False
    finally:
        routing.settings = saved


# =================================================================== the public websocket
def _exotel_messages(call_sid, stream_sid="MZexo1"):
    return [
        json.dumps({"event": "connected"}),
        json.dumps({"event": "start", "start": {
            "stream_sid": stream_sid, "call_sid": call_sid, "account_sid": "EXOACC1",
            "from": "+919876543210", "to": "+911111111111", "custom_parameters": {},
            "media_format": {"encoding": "audio/x-raw", "sample_rate": "8000", "bit_rate": "16"}}}),
    ]


def _connect_exotel(call_sid, active_calls):
    built = []
    app = FastAPI()
    integration._register_voice_pipecat(app)
    fake = types.ModuleType("outbound.app")
    fake.active_calls = active_calls
    saved = (sys.modules.get("outbound.app"), pipeline.build_services)
    sys.modules["outbound.app"] = fake
    pipeline.build_services = lambda ctx: built.append(ctx)
    code = None
    try:
        with TestClient(app).websocket_connect("/media-stream-pipecat") as ws:
            for message in _exotel_messages(call_sid):
                ws.send_text(message)
            try:
                ws.receive_text()
            except WebSocketDisconnect as exc:
                code = exc.code
    finally:
        pipeline.build_services = saved[1]
        if saved[0] is None:
            sys.modules.pop("outbound.app", None)
        else:
            sys.modules["outbound.app"] = saved[0]
    return code, built


def test_the_endpoint_accepts_exotel_too_and_rejects_a_callsid_with_no_matching_leadai_call():
    code, built = _connect_exotel("CA-not-in-our-db", {})
    assert code == 1008 and built == []


# An "accepted" case through the REAL public endpoint is deliberately not tested here: unlike
# authenticate()/authenticate_exotel() (tested directly, above), run_call()'s services_factory
# default is bound to the real build_services at import time, so patching pipeline.build_services
# does not intercept it through the endpoint — a call that clears authentication would reach the
# real Sarvam client. (The Twilio test suite has this same shape: it only drives REJECTIONS
# through the real endpoint too.) authenticate_exotel's own tests already cover every accept/
# reject branch; this section only adds the two rejection paths that are specific to going
# through the real endpoint (detection + call-context lookup), not duplicated by the unit tests.


def test_an_exotel_call_that_already_ended_cannot_open_the_socket_even_with_leadai_context():
    make_call(call_sid="CAexo-done", status="completed")
    code, built = _connect_exotel("CAexo-done", {
        "CAexo-done": {"leadai": True, "client_id": "c1", "conversation_id": "conv-1"},
    })
    assert code == 1008 and built == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
