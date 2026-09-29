"""The simulated voice turn (POST /voice/calls/{id}/turn): behaviour must not change.

Characterization test: written against the endpoint BEFORE its logic moved into the shared
voice service (services/voice_flow.py), and kept as the proof that the move changed nothing
observable. It calls the endpoint function directly with a fake principal, on in-memory
SQLite; only retrieval and the LLM are faked. Run: python tests/test_voice_turn.py
"""
import types

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.config import settings as real_settings  # noqa: E402
from LeadAI.routers import voice  # noqa: E402
from LeadAI.schemas import VoiceTurnIn  # noqa: E402
from LeadAI.services import ai_engine  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass

TRANSFER = "Let me bring in a specialist who can help with that — connecting you now."


class _Settings:
    llm_enabled = True
    llm_qualification = False
    engine_mode = "off"
    engine_events = False

    def __getattr__(self, name):
        return getattr(real_settings, name)


def wire(score=0.8):
    ai_engine.settings = _Settings()
    ai_engine.llm.complete = lambda *a, **k: (
        "Rates start at 8.5 percent.", {"model": "fake-model", "latency_ms": 9})
    ai_engine.llm.complete_json = lambda *a, **k: (None, {})
    ai_engine.vectorstore.search = lambda *a, **k: (
        [{"chunk_id": "k1", "document_id": "d", "score": score,
          "text": "Home loan interest rates start at 8.5% per annum."}] if score else [])
    ai_engine.vectorstore.idf_map = lambda *a, **k: ({}, 1.0)
    ai_engine._detect_product = lambda *a, **k: None
    ai_engine.company_thresholds = lambda db, client_id: (0.4, 5)


def setup():
    db = SessionLocalAdmin()
    client = Client(Name="Nexa Finserv")
    db.add(client)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="Customer #1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="voice")
    db.add(conv)
    db.flush()
    call = models.LeadCall(ClientId=client.Id, ConversationId=conv.Id, CallSid="CA123",
                           Provider="simulated", Status="in_progress", Language="hi")
    db.add(call)
    db.commit()
    principal = types.SimpleNamespace(
        client_id=client.Id, sees_only_assigned=False, email="agent@nexa.example",
        role="employee", is_platform_admin=False)
    return db, client, conv, call, principal


def turn(utterance, score=0.8):
    wire(score)
    db, client, conv, call, principal = setup()
    out = voice.voice_turn(call.Id, VoiceTurnIn(utterance=utterance), None, principal, db)
    for obj in (conv, call):
        db.refresh(obj)
    msgs = db.query(models.LeadMessage).filter_by(ConversationId=conv.Id).order_by(
        models.LeadMessage.CreatedAt).all()
    return db, conv, call, msgs, out


def test_a_normal_turn_replies_stores_both_sides_and_scores_the_lead():
    db, conv, call, msgs, out = turn("what is the interest rate")
    assert out.reply == "Rates start at 8.5 percent." and out.handed_off is False
    assert out.tts["text"] == out.reply and out.tts["language"] == "hi"
    assert [m.Sender for m in msgs] == ["customer", "ai"]
    assert all(m.CallSid == "CA123" and m.CreatedBy == "voice" for m in msgs)
    assert msgs[1].Content == out.reply and msgs[1].ModelUsed == "fake-model"
    assert msgs[1].LatencyMs == 9 and msgs[1].SourcesJson
    assert call.DurationSec == 18 and call.HandedOff in (False, None)
    lead = db.query(models.Lead).filter_by(ConversationId=conv.Id).one()
    assert out.lead_status == lead.Status and out.lead_score == lead.Score > 0
    assert conv.MessageCount == 2 and conv.Summary
    logs = db.query(models.LeadActivityLog).filter_by(EntityId=call.Id, Action="chat.ai_replied").all()
    assert len(logs) == 1 and "Voice turn" in logs[0].LogMessage


def test_a_handoff_speaks_the_transfer_line_and_marks_the_call_transferred():
    db, conv, call, msgs, out = turn("do you finance a private island", score=0.0)
    assert out.reply == TRANSFER and out.handed_off is True
    assert call.HandedOff is True and call.Status == "transferred"
    assert conv.Status == "needs_human" and "below threshold" in conv.HandoffReason
    assert msgs[1].Content == TRANSFER                        # the spoken line is what is stored


def test_the_ai_message_carries_a_voice_decision_trace():
    db, conv, call, msgs, out = turn("what is the interest rate")
    steps = [s["step"] for s in msgs[1].TraceJson["steps"]]
    assert msgs[1].TraceJson["channel"] == "voice"
    for expected in ("receive", "retrieve", "confidence", "generate", "answer_decision", "qualify", "commit"):
        assert expected in steps, steps


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
