"""leadai_calls.Status/DurationSec were only ever written by
voice/session.py's CallSession._finalize_call_sync(), which has no access to
what actually happened on the line: it marked EVERY call "completed" (even a
busy, no-answer or failed one) and measured duration from when the row was
CREATED — i.e. dial time, including however long the phone rang — instead of
from when the call was actually answered.

The carrier (Twilio's /call-status webhook, Exotel's /voice/exotel/status)
already reports the real terminal status and the real answered-to-hangup
duration; it just never reached this table. call_bridge.sync_call_outcome()
is the one place that now writes the carrier's own truth, and it must win
regardless of whether the webhook or the pipecat session's own shutdown
happens to run first.

Run: python tests/test_call_outcome_from_carrier.py
"""
import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.services import call_bridge  # noqa: E402
from LeadAI.voice.session import CallSession  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def _make_call(db, client_id, conv_id, call_sid):
    call = models.LeadCall(
        ClientId=client_id, ConversationId=conv_id, CallSid=call_sid,
        Status="in-progress", DurationSec=0,
    )
    db.add(call)
    db.commit()
    return call


def test_sync_call_outcome_writes_the_real_status_and_duration():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Carrier")
    db.add(client)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="voice")
    db.add(conv)
    db.flush()
    call = _make_call(db, client.Id, conv.Id, "CA_busy_1")

    found = call_bridge.sync_call_outcome(db, "CA_busy_1", "busy", 0)
    db.commit()
    db.refresh(call)
    assert found is True
    assert call.Status == "busy"
    assert call.DurationSec == 0


def test_an_unknown_callsid_is_a_no_op_not_an_error():
    db = SessionLocalAdmin()
    found = call_bridge.sync_call_outcome(db, "CA_does_not_exist", "completed", 42)
    assert found is False


def test_the_carrier_report_wins_even_when_it_arrives_before_the_pipecat_session_closes():
    """Webhook-first ordering: the real status must survive _finalize_call_sync
    running afterwards (its guard only fills gaps, never overwrites)."""
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Carrier Order A")
    db.add(client)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="voice")
    db.add(conv)
    db.flush()
    call = _make_call(db, client.Id, conv.Id, "CA_order_a")

    # The carrier's webhook arrives first: real outcome is "no-answer", 0 duration.
    call_bridge.sync_call_outcome(db, "CA_order_a", "no-answer", 0)
    db.commit()

    # The pipecat session then shuts down and runs its own fallback finalize.
    session = CallSession(
        client_id=client.Id, conversation_id=conv.Id, call_sid="CA_order_a",
        session_factory=SessionLocalAdmin,
    )
    session._finalize_call_sync()

    db.refresh(call)
    assert call.Status == "no-answer"   # NOT overwritten to "completed"
    assert call.DurationSec == 0         # NOT overwritten with dial-time elapsed


def test_the_carrier_report_wins_even_when_it_arrives_after_the_pipecat_session_closes():
    """Pipecat-first ordering: the fallback's guess must be corrected once the
    real carrier report arrives — sync_call_outcome is deliberately unconditional."""
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Carrier Order B")
    db.add(client)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="voice")
    db.add(conv)
    db.flush()
    call = _make_call(db, client.Id, conv.Id, "CA_order_b")

    # The pipecat session shuts down first, before the webhook has arrived.
    session = CallSession(
        client_id=client.Id, conversation_id=conv.Id, call_sid="CA_order_b",
        session_factory=SessionLocalAdmin,
    )
    session._finalize_call_sync()
    db.refresh(call)
    assert call.Status == "completed"   # the fallback guess
    assert call.DurationSec >= 0

    # The carrier's webhook arrives afterwards with the real outcome: busy.
    call_bridge.sync_call_outcome(db, "CA_order_b", "busy", 0)
    db.commit()
    db.refresh(call)
    assert call.Status == "busy"        # corrected, not stuck on the guess
    assert call.DurationSec == 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
