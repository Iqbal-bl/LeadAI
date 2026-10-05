"""leadai_calls.Status/DurationSec were only ever written by a manual dashboard hangup or
the simulated test endpoint. Every REAL live call (Twilio or Exotel, through the Pipecat
pipeline) never had either column touched at all — confirmed against production data: 16
of 23 real calls sat at Status="initiated" forever, and every one of the 7 that did reach
a terminal status (via one of those two paths) still showed DurationSec=0, because neither
path ever computed it.

CallSession.close() is the one place every real call reaches on its way out, regardless of
what ended it — that's where _finalize_call_sync() now runs. hangup_call() (the manual
dashboard action) is fixed the same way for immediacy.

Run: python tests/test_voice_call_finalize.py
"""
import asyncio
from datetime import timedelta

import conftest_stub  # noqa: F401
import test_voice_live_call as live  # noqa: E402  (setup(), SessionLocalAdmin, wire())

from LeadAI.models import utcnow  # noqa: E402
from LeadAI.services import call_bridge  # noqa: E402
from LeadAI.voice.session import CallSession  # noqa: E402


def backdate(db, call, seconds):
    call.CreatedAt = utcnow().replace(tzinfo=None) - timedelta(seconds=seconds)
    db.commit()


# ------------------------------------------------------------------- CallSession.close()
def test_a_call_that_never_got_a_terminal_status_gets_one_with_a_real_duration():
    live.wire()
    db, client, conv, call = live.setup()
    backdate(db, call, 42)
    assert call.Status == "in-progress" and not call.DurationSec

    session = CallSession(client_id=client.Id, conversation_id=conv.Id, call_sid=call.CallSid,
                          session_factory=live.SessionLocalAdmin)
    asyncio.run(session.close())

    db.refresh(call)
    assert call.Status == "completed"
    assert 41 <= call.DurationSec <= 44  # small slack for test execution time


def test_a_call_already_ended_some_other_way_is_not_relabelled():
    # A carrier webhook or the simulated endpoint already recorded the real outcome —
    # finalize must not stomp a real "failed"/"transferred" with a generic "completed".
    live.wire()
    db, client, conv, call = live.setup()
    call.Status = "failed"
    backdate(db, call, 10)

    session = CallSession(client_id=client.Id, conversation_id=conv.Id, call_sid=call.CallSid,
                          session_factory=live.SessionLocalAdmin)
    asyncio.run(session.close())

    db.refresh(call)
    assert call.Status == "failed"          # untouched
    assert 9 <= call.DurationSec <= 12       # duration still backfilled


def test_a_call_that_already_has_a_duration_is_left_alone():
    live.wire()
    db, client, conv, call = live.setup()
    call.DurationSec = 999
    backdate(db, call, 5)

    session = CallSession(client_id=client.Id, conversation_id=conv.Id, call_sid=call.CallSid,
                          session_factory=live.SessionLocalAdmin)
    asyncio.run(session.close())

    db.refresh(call)
    assert call.DurationSec == 999


def test_an_unknown_call_sid_does_not_raise():
    live.wire()
    session = CallSession(client_id="c", conversation_id="conv", call_sid="does-not-exist",
                          session_factory=live.SessionLocalAdmin)
    asyncio.run(session.close())  # must not raise


# ------------------------------------------------------------- manual dashboard hangup
def test_manually_hanging_up_also_records_duration_immediately():
    live.wire()
    db, client, conv, call = live.setup()
    backdate(db, call, 30)
    real_hangup = call_bridge.telephony.hangup
    call_bridge.telephony.hangup = lambda sid, provider: True
    try:
        ok = call_bridge.hangup_call(db, call)
        db.commit()  # hangup_call() mutates the row; committing is the caller's job
    finally:
        call_bridge.telephony.hangup = real_hangup

    assert ok is True
    db.refresh(call)
    assert call.Status == "completed"
    assert 29 <= call.DurationSec <= 32


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
