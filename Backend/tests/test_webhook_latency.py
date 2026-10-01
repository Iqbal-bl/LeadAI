"""Three real customer-facing seconds, found in a live Instagram log, that had nothing to do
with the AI actually thinking:

  * mark_read() (the Instagram/Messenger "seen" tick) ran BEFORE the AI turn and blocked it
    for ~1.1s, even though it already treats its own failure as inconsequential.
  * resolve_social_conversation() re-fetched the customer's Instagram profile on EVERY
    message, not just the first one, because the guard compared the wrong value (the
    webhook's own payload, which IG/Messenger never carry a name in) instead of what was
    already stored on the identity — another ~1.1-1.2s on every single message.
  * qualify()/summarize() (lead scoring) ran before the reply was delivered, blocking it by
    a second full LLM round trip (~2-3s) that changes nothing about what the customer sees.

Run: python tests/test_webhook_latency.py
"""
import threading
import time
import uuid

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.config import settings as real_settings  # noqa: E402
from LeadAI.routers import webhooks  # noqa: E402
from LeadAI.services import ai_engine, billing, channels, conversation_flow, scoring_queue  # noqa: E402

billing.check_channel_access = lambda db, client_id, channel: (True, "")

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


class _Settings:
    llm_enabled = False

    def __getattr__(self, name):
        return getattr(real_settings, name)


ai_engine.settings = _Settings()
ai_engine.vectorstore.search = lambda *a, **k: []
ai_engine.vectorstore.idf_map = lambda *a, **k: ({}, 1.0)
ai_engine._detect_product = lambda *a, **k: None


def make_account(db, channel="instagram"):
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    account = models.LeadChannelAccount(ClientId=client.Id, Channel=channel, Name="kestrel",
                                        ExternalId=uuid.uuid4().hex, IsActive=True)
    db.add(account)
    db.commit()
    return client, account


# --------------------------------------------------------------- mark_read is non-blocking
def test_mark_read_runs_in_the_background_not_before_the_ai_turn():
    db = SessionLocalAdmin()
    client, account = make_account(db)
    order = []
    release = threading.Event()

    def slow_mark_read(*a, **k):
        release.wait(timeout=2)
        order.append("mark_read")

    def fake_turn(*a, **k):
        order.append("turn")
        return None

    real_mark_read, real_turn = channels.mark_read, conversation_flow.handle_customer_turn
    channels.mark_read = slow_mark_read
    conversation_flow.handle_customer_turn = fake_turn
    try:
        webhooks._process_one(db, {"account_id": account.Id, "external_user_id": "1036903189236884",
                                   "text": "Hi", "external_message_id": "m1"})
        # The AI turn must already be recorded — mark_read is still blocked on `release`.
        assert order == ["turn"], order
    finally:
        release.set()
        time.sleep(0.1)  # let the background thread finish before restoring the real function
        channels.mark_read = real_mark_read
        conversation_flow.handle_customer_turn = real_turn
    assert "mark_read" in order


# ------------------------------------------------------- profile fetched once, not per message
def test_profile_is_fetched_once_for_a_returning_contact_not_every_message():
    db = SessionLocalAdmin()
    client, account = make_account(db)
    calls = []

    def fake_fetch_profile(account, channel, external_user_id):
        calls.append(external_user_id)
        return {"name": "Ak Sharma", "username": "ak_sharma717", "handle": "ak_sharma717"}

    real_fetch = channels.fetch_profile
    channels.fetch_profile = fake_fetch_profile
    try:
        # First message from this contact: the profile is not known yet -> one fetch.
        c1, conv1, identity1 = conversation_flow.resolve_social_conversation(
            db, account, external_user_id="1036903189236884", profile_name=None,
        )
        db.commit()
        assert len(calls) == 1
        assert identity1.ProfileName == "Ak Sharma"

        # A second, later message from the SAME contact: IG/Messenger webhooks never carry a
        # profile_name, so this reproduces the live bug where the guard re-fetched every time.
        c2, conv2, identity2 = conversation_flow.resolve_social_conversation(
            db, account, external_user_id="1036903189236884", profile_name=None,
        )
        assert len(calls) == 1, "profile was re-fetched on a returning contact"
        assert conv2.Id == conv1.Id and identity2.Id == identity1.Id
    finally:
        channels.fetch_profile = real_fetch


# --------------------------------------------------------- scoring deferred on a push channel
def wire_scoring_fakes(reply="Sure, happy to help."):
    ai_engine.settings = type("S", (), {"llm_enabled": True, "llm_qualification": True,
                                        "engine_mode": "off",
                                        "__getattr__": lambda self, n: getattr(real_settings, n)})()

    def fake_complete(system, messages, **kw):
        return reply, {"model": "fake", "latency_ms": 1}

    def fake_complete_json(system, messages, **kw):
        return {"intent": "browsing", "timeline": "unknown", "budget": "unknown", "product": "unknown",
                "sentiment": "neutral", "summary": "s", "next_step": "n", "facts": []}, {}

    ai_engine.llm.complete = fake_complete
    ai_engine.llm.complete_json = fake_complete_json
    ai_engine.vectorstore.search = lambda *a, **k: []
    ai_engine.vectorstore.idf_map = lambda *a, **k: ({}, 1.0)
    ai_engine._detect_product = lambda *a, **k: None
    ai_engine.company_thresholds = lambda db, client_id: (0.0, 5)


def setup_conv(channel):
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="Customer #1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel=channel)
    db.add(conv)
    db.commit()
    return db, client, conv


def test_a_push_channel_reply_is_committed_before_scoring_runs():
    wire_scoring_fakes()
    db, client, conv = setup_conv("instagram")
    conversation_flow.handle_customer_turn(db, client, conv, "Hi", deliver_reply=True)
    ai = db.query(models.LeadMessage).filter_by(ConversationId=conv.Id, Sender="ai").one()
    steps = [s["step"] for s in ai.TraceJson["steps"]]
    assert "commit" in steps
    assert "qualify" not in steps, "scoring must not run inline for a push channel"

    lead = db.query(models.Lead).filter_by(ConversationId=conv.Id).one()
    assert lead.Score in (None, 0) and (lead.Status or "cold") == "cold"    # not scored yet

    time.sleep(0.3)   # the deferred background thread runs its own session
    db.expire_all()
    ai = db.query(models.LeadMessage).filter_by(ConversationId=conv.Id, Sender="ai").one()
    steps_after = [s["step"] for s in ai.TraceJson["steps"]]
    assert "post_turn" in steps_after            # scoring's trace got merged onto the reply
    conv2 = db.query(models.LeadConversation).filter_by(Id=conv.Id).one()
    assert conv2.Summary == "s"                   # qualify/summarize actually ran in the background


def test_the_widget_reply_is_not_held_up_by_scoring_either():
    wire_scoring_fakes()
    db, client, conv = setup_conv("web")
    result = conversation_flow.handle_customer_turn(db, client, conv, "Hi", deliver_reply=False)
    ai = db.query(models.LeadMessage).filter_by(ConversationId=conv.Id, Sender="ai").one()
    steps = [s["step"] for s in ai.TraceJson["steps"]]
    assert "commit" in steps and "qualify" not in steps, "scoring must not run inline for the widget"
    assert result.lead_status is not None and result.lead_score == 0     # as of the previous turn

    assert scoring_queue.wait_idle()
    db.expire_all()
    ai = db.query(models.LeadMessage).filter_by(ConversationId=conv.Id, Sender="ai").one()
    steps_after = [s["step"] for s in ai.TraceJson["steps"]]
    assert "qualify" in steps_after and "threshold" in steps_after      # scored in the background
    assert db.query(models.Lead).filter_by(ConversationId=conv.Id).one().Score > 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
