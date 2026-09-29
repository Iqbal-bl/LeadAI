"""The engine inside the real chat pipeline (conversation_flow), in all three modes.

Reproduces the golden-set finding: the model says "I don't have information on car loan
rates" but retrieval confidence is above the threshold, so production leaves the
conversation open and no human is ever told. Only the network is faked.
Run: python tests/test_engine_flow.py
"""
import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.config import settings as real_settings  # noqa: E402
from LeadAI.engine import bridge  # noqa: E402
from LeadAI.engine.graph import DECLINED_HANDOFF  # noqa: E402
from LeadAI.services import ai_engine, conversation_flow  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass

DECLINE = "I'm sorry, but I don't have information on car loan interest rates."
KB = "Home loan interest rates start at 8.5% per annum."


class _Settings:
    llm_enabled = True
    llm_qualification = False
    engine_mode = "off"

    def __getattr__(self, name):
        return getattr(real_settings, name)


class _Bridge(_Settings):
    def __init__(self, mode):
        self.engine_mode = mode


ai_engine.settings = _Settings()
ai_engine.llm.complete = lambda *a, **k: (DECLINE, {"model": "fake", "latency_ms": 1})
ai_engine.llm.complete_json = lambda *a, **k: (None, {})
# Retrieval matches the unrelated home-loan text well: confidence is high, so the
# confidence-based handoff does NOT fire, exactly as in the live run.
ai_engine.vectorstore.search = lambda *a, **k: [
    {"chunk_id": "k1", "document_id": "d", "score": 0.8, "text": KB}]
ai_engine.vectorstore.idf_map = lambda *a, **k: ({}, 1.0)
ai_engine.company_thresholds = lambda db, client_id: (0.05, 5)


def run(mode):
    bridge.settings = _Bridge(mode)
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
    result = conversation_flow.handle_customer_turn(
        db, client, conv, "What is the interest rate on a car loan interest rate?")
    db.refresh(conv)
    logs = db.query(models.LeadActivityLog).filter_by(EntityId=conv.Id).all()
    return result, conv, [row for row in logs if "AI replied" in (row.LogMessage or "")]


def test_off_reproduces_the_gap_declined_but_nobody_is_told():
    result, conv, logs = run("off")
    assert result.confidence >= 0.05 and conv.Status == "open" and not result.handed_off
    assert logs and all("engine_mode" not in (row.MetaJson or {}) for row in logs)


def test_observe_changes_nothing_but_records_what_it_saw():
    result, conv, logs = run("observe")
    assert conv.Status == "open" and not result.handed_off
    meta = logs[0].MetaJson
    assert meta["engine_mode"] == "observe" and meta["engine_declined"] is True
    assert meta["engine_escalation"] is True       # it would have handed this to a human


def test_enforce_hands_the_conversation_to_a_human():
    result, conv, logs = run("enforce")
    assert conv.Status == "needs_human" and result.handed_off and result.needs_human
    assert conv.HandoffReason == DECLINED_HANDOFF


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
