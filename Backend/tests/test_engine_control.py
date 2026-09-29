"""Control plane and event outbox, through the real chat pipeline.

  * A paused or terminated conversation gets no AI reply and no re-scoring, but the
    customer's message is still stored.
  * Resuming restores normal replies.
  * With ENGINE_EVENTS on, each turn writes events in its own transaction: a turn that
    fails and rolls back leaves no events behind.

Only the network is faked (retrieval and the LLM). Run: python tests/test_engine_control.py
"""
import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models, models_ext  # noqa: E402
from LeadAI.config import settings as real_settings  # noqa: E402
from LeadAI.engine import control, outbox  # noqa: E402
from LeadAI.services import ai_engine, conversation_flow  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass

LLM_CALLS: list = []


class _Settings:
    llm_enabled = True
    llm_qualification = False
    engine_mode = "off"
    engine_events = False

    def __getattr__(self, name):
        return getattr(real_settings, name)


class _Events(_Settings):
    engine_events = True


def fake_complete(system, messages, **kwargs):
    LLM_CALLS.append(messages)
    return "Sure, happy to help.", {"model": "fake", "latency_ms": 5}


ai_engine.settings = _Settings()
ai_engine.llm.complete = fake_complete
ai_engine.llm.complete_json = lambda *a, **k: (None, {})
ai_engine.vectorstore.search = lambda *a, **k: [
    {"chunk_id": "k", "document_id": "d", "score": 0.8, "text": "Home loans start at 8.5%."}]
ai_engine.vectorstore.idf_map = lambda *a, **k: ({}, 1.0)
ai_engine.company_thresholds = lambda db, client_id: (0.05, 5)


def setup():
    db = SessionLocalAdmin()
    client = Client(Name="Nexa Finserv")
    db.add(client)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="Customer #1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="web")
    db.add(conv)
    db.commit()
    return db, client, conv


def say(db, client, conv, text, **kw):
    result = conversation_flow.handle_customer_turn(db, client, conv, text, **kw)
    db.refresh(conv)
    return result


def events(db, conv):
    rows = (db.query(models_ext.LeadEvent).filter_by(ConversationId=conv.Id)
            .order_by(models_ext.LeadEvent.CreatedAt).all())
    return [r for r in rows]


def stored(db, conv):
    return db.query(models.LeadMessage).filter_by(ConversationId=conv.Id).count()


def with_events(fn):
    saved = outbox.settings
    outbox.settings = _Events()
    try:
        return fn()
    finally:
        outbox.settings = saved


# ---------------------------------------------------------------- control plane
def test_new_conversations_are_active_by_default():
    db, client, conv = setup()
    assert conv.ControlStatus is None and control.get_control(conv) == "active"
    assert not control.is_stopped(conv)


def test_paused_conversation_stores_the_message_but_the_ai_stays_silent():
    db, client, conv = setup()
    say(db, client, conv, "what is the rate")
    score_before = db.query(models.Lead).filter_by(ConversationId=conv.Id).one().Score
    calls_before, stored_before = len(LLM_CALLS), stored(db, conv)

    control.set_control(db, conv, "paused", reason="suspected bot loop", by="monitor")
    db.commit()
    result = say(db, client, conv, "hello? anyone there")

    assert result.reply == "" and result.ai_replied is False
    assert len(LLM_CALLS) == calls_before                       # no model call
    assert stored(db, conv) == stored_before + 1                # customer message kept, no AI row
    assert db.query(models.Lead).filter_by(ConversationId=conv.Id).one().Score == score_before


def test_terminated_conversation_is_silent_too_and_resume_restores_replies():
    db, client, conv = setup()
    control.set_control(db, conv, "terminated", reason="confirmed bot-to-bot loop", by="monitor")
    db.commit()
    assert say(db, client, conv, "hi again").reply == ""

    control.set_control(db, conv, "active", reason="reviewed by staff", by="agent@nexa.example")
    db.commit()
    result = say(db, client, conv, "what is the rate")
    assert result.ai_replied and result.reply


def test_set_control_records_who_and_why_and_is_idempotent():
    db, client, conv = setup()
    assert control.set_control(db, conv, "paused", reason="loop", by="monitor") == "active"
    db.commit()
    assert (conv.ControlStatus, conv.ControlReason, conv.ControlBy) == ("paused", "loop", "monitor")
    assert conv.ControlAt is not None
    rows = db.query(models.LeadActivityLog).filter_by(EntityId=conv.Id, Action="chat.control_changed").all()
    assert len(rows) == 1 and rows[0].MetaJson["to"] == "paused"

    assert control.set_control(db, conv, "paused", reason="again", by="monitor") == "paused"   # no-op
    db.commit()
    assert conv.ControlReason == "loop"
    assert db.query(models.LeadActivityLog).filter_by(EntityId=conv.Id, Action="chat.control_changed").count() == 1


def test_an_invalid_status_is_rejected():
    db, client, conv = setup()
    try:
        control.set_control(db, conv, "banana", reason="x", by="monitor")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


# ------------------------------------------------------------------ event outbox
def test_with_events_switched_off_nothing_is_written():
    # Forced off here so the test does not depend on the developer's .env (ENGINE_EVENTS).
    saved = outbox.settings
    outbox.settings = _Settings()
    try:
        db, client, conv = setup()
        say(db, client, conv, "what is the rate")
        assert events(db, conv) == []
    finally:
        outbox.settings = saved


def test_a_normal_turn_writes_received_and_replied_events():
    def run():
        db, client, conv = setup()
        say(db, client, conv, "what is the rate", external_message_id="wamid.42")
        return db, conv

    db, conv = with_events(run)
    rows = events(db, conv)
    assert [r.Type for r in rows] == ["turn.received", "turn.replied"]
    received, replied = rows
    assert received.Speaker == "customer" and received.TurnId == "wamid.42"
    assert received.PayloadJson["text"] == "what is the rate"
    assert replied.Speaker == "ai" and replied.PayloadJson["text"] == "Sure, happy to help."
    assert replied.PayloadJson["latency_ms"] == 5 and replied.PayloadJson["data"]["model"] == "fake"
    assert received.ProcessedAt is None and received.ClientId == conv.ClientId


def test_pausing_writes_a_control_event_and_later_turns_write_skipped():
    def run():
        db, client, conv = setup()
        control.set_control(db, conv, "paused", reason="loop", by="monitor")
        db.commit()
        say(db, client, conv, "hello?")
        return db, conv

    db, conv = with_events(run)
    types = [r.Type for r in events(db, conv)]
    assert types == ["control.changed", "turn.received", "turn.skipped"]


def test_a_failed_turn_leaves_no_events_behind():
    def run():
        db, client, conv = setup()
        saved = ai_engine.answer
        ai_engine.answer = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model exploded"))
        try:
            say(db, client, conv, "what is the rate")
        except RuntimeError:
            db.rollback()
        finally:
            ai_engine.answer = saved
        return db, conv

    db, conv = with_events(run)
    assert events(db, conv) == []               # the event rolled back with its turn
    assert stored(db, conv) == 0                # ...as did the message


def test_long_text_is_truncated_in_the_event_not_in_the_message():
    def run():
        db, client, conv = setup()
        say(db, client, conv, "x" * 5000)
        return db, conv

    db, conv = with_events(run)
    received = events(db, conv)[0]
    assert len(received.PayloadJson["text"]) == outbox.MAX_TEXT_CHARS


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
