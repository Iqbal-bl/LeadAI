"""A finished conversation stays finished, and the lead score reads the whole chat.

Reproduces the Instagram session where the AI ended with "[END_CALL]" and then
restarted the qualification questions on "Ok". Runs the real chat pipeline
(conversation_flow -> ai_engine -> qualify) on in-memory SQLite. Only the network is
faked: retrieval and the two LLM calls. Run: python tests/test_conversation_completion.py
"""
import types

import conftest_stub  # noqa: F401  — installs core.base/core.database/core.auth stubs first
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.config import settings as real_settings  # noqa: E402
from LeadAI.services import ai_engine, conversation_flow, reply_cleanup  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001 — the domain `batchinfo` table has a known duplicate index name
        pass

LLM_CALLS: list[dict] = []          # every conversation-model call the pipeline makes
CLOSING = ("An advisor from Nexa Finserv will take over this conversation. Expect a callback "
           "during business hours.\n\n[END_CALL]")


class _Settings:
    """The real settings, with the LLM switched on for this test.

    Both switches are forced so the result never depends on the developer's own .env.
    """
    llm_enabled = True
    llm_qualification = True
    loop_max_turns = 20

    def __getattr__(self, name):
        return getattr(real_settings, name)


def fake_complete(system, messages, **kwargs):
    LLM_CALLS.append({"system": system, "messages": messages})
    last = messages[-1]["content"]
    if "Customer question: Yes" in last:
        return CLOSING, {"model": "fake", "latency_ms": 1}
    return "Sure, happy to help with that.", {"model": "fake", "latency_ms": 1}


def fake_complete_json(system, messages, **kwargs):
    return {
        "intent": "ready_to_buy", "timeline": "unknown", "budget": "Rs 50 lakh loan",
        "product": "Business Loan", "sentiment": "positive",
        "summary": "Startup owner wants a Rs 50 lakh business loan; 3 years in business.",
        "next_step": "Call to collect documents.",
    }, {}


ai_engine.settings = _Settings()
ai_engine.llm.complete = fake_complete
ai_engine.llm.complete_json = fake_complete_json
ai_engine.vectorstore.search = lambda *a, **k: []
ai_engine.vectorstore.idf_map = lambda *a, **k: ({}, 1.0)
ai_engine._detect_product = lambda *a, **k: None
ai_engine.company_thresholds = lambda db, client_id: (0.0, 5)   # only END_CALL can hand off here


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


def say(db, client, conv, text):
    result = conversation_flow.handle_customer_turn(db, client, conv, text)
    db.refresh(conv)
    return result


def test_end_call_never_reaches_the_customer_and_completes_the_conversation():
    db, client, conv = setup()
    say(db, client, conv, "I will take the business loan, 50 lakhs")
    assert conv.AiCompletedAt is None and conv.Status == "open"

    done = say(db, client, conv, "Yes")
    assert "END_CALL" not in done.reply and done.reply.endswith("business hours.")
    stored = db.query(models.LeadMessage).filter_by(ConversationId=conv.Id, Sender="ai").all()
    assert all("END_CALL" not in m.Content for m in stored)          # not stored either
    assert conv.AiCompletedAt is not None
    assert conv.Status == "needs_human"                              # Option A: handed off
    assert conv.HandoffReason == "AI completed the conversation; advisor follow-up"


def test_acknowledgements_after_completion_get_a_fixed_line_not_a_restart():
    db, client, conv = setup()
    say(db, client, conv, "I will take the business loan, 50 lakhs")
    say(db, client, conv, "Yes")
    calls_before = len(LLM_CALLS)

    ok = say(db, client, conv, "Ok")
    assert ok.reply.startswith("Thank you! An advisor from Nexa Finserv will contact you shortly")
    hi = say(db, client, conv, "haan theek hai")
    assert hi.reply.startswith("Shukriya!")
    assert len(LLM_CALLS) == calls_before                            # no model call at all
    assert conv.AiCompletedAt is not None                            # still completed


def test_a_real_question_after_completion_continues_with_context_and_reopens():
    db, client, conv = setup()
    say(db, client, conv, "I will take the business loan, 50 lakhs")
    say(db, client, conv, "Yes")
    calls_before = len(LLM_CALLS)

    say(db, client, conv, "What documents do I need?")
    assert len(LLM_CALLS) == calls_before + 1
    system_turns = [m["content"] for m in LLM_CALLS[-1]["messages"] if m["role"] == "system"]
    assert reply_cleanup.COMPLETED_NOTE in system_turns              # told not to start over
    assert conv.AiCompletedAt is None                                # reopened


def test_lead_score_reflects_the_whole_conversation():
    db, client, conv = setup()
    say(db, client, conv, "Ok what type of loans you offer")
    say(db, client, conv, "I will take the business loan, 50 lakhs")
    say(db, client, conv, "I have a startup running from 3 years")
    lead = db.query(models.Lead).filter_by(ConversationId=conv.Id).one()
    assert lead.Intent == "ready_to_buy"
    assert lead.Product == "Business Loan" and lead.Budget == "RS 50 LAKH LOAN"
    assert lead.Status == "hot" and lead.Score >= 62                 # was warm / 48 / "browsing"
    assert "Startup owner" in (conv.Summary or "")                   # one call also wrote the brief


def test_keyword_rules_still_work_when_the_llm_is_off():
    # Same conversation, LLM analysis unavailable: the phrase list alone now sees the intent.
    ai_engine.llm.complete_json = lambda *a, **k: (None, {})
    try:
        db, client, conv = setup()
        say(db, client, conv, "Ok I will take business loan")
        lead = db.query(models.Lead).filter_by(ConversationId=conv.Id).one()
        assert lead.Intent == "ready_to_buy"
    finally:
        ai_engine.llm.complete_json = fake_complete_json


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
