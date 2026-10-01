""""Switch if the user mentions both chat and call: ask the customer first, and
only call if they say yes." A campaign with CallEscalationEnabled offers a call
instead of a plain handoff; the actual call is placed ONLY after an explicit
yes on a later turn, never automatically.

Run: python tests/test_call_consent_escalation.py
"""
import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.models_ext import LeadCampaign  # noqa: E402
from LeadAI.security import encrypt_pii  # noqa: E402
from LeadAI.services import conversation_flow  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def _setup(call_escalation_enabled=True, with_campaign=True, with_phone=True):
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C1",
                                   PhoneEnc=encrypt_pii("+919000000001") if with_phone else None)
    db.add(customer)
    db.flush()
    campaign = None
    if with_campaign:
        campaign = LeadCampaign(ClientId=client.Id, Name="Home Loan batch", Kind="message",
                                Channel="whatsapp", AudienceType="list",
                                CallEscalationEnabled=call_escalation_enabled, Status="draft")
        db.add(campaign)
        db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="whatsapp",
                                   Status="open", CampaignId=campaign.Id if campaign else None)
    db.add(conv)
    db.commit()
    return db, client, conv, campaign


# =========================================================================== #
# offering
# =========================================================================== #
def test_offers_a_call_instead_of_a_plain_handoff_when_escalation_is_enabled():
    db, client, conv, campaign = _setup(call_escalation_enabled=True)
    result = {"needs_human": True, "reply": "I'll have someone follow up."}
    conversation_flow._maybe_offer_call_consent(db, conv, result)
    assert "call you" in result["reply"]
    assert conv.CallConsentStatus == "asked"
    assert conv.CallConsentAskedAt is not None


def test_never_offers_when_the_campaign_has_not_enabled_it():
    db, client, conv, campaign = _setup(call_escalation_enabled=False)
    result = {"needs_human": True, "reply": "I'll have someone follow up."}
    original = result["reply"]
    conversation_flow._maybe_offer_call_consent(db, conv, result)
    assert result["reply"] == original
    assert conv.CallConsentStatus is None


def test_never_offers_outside_a_campaign_at_all():
    db, client, conv, campaign = _setup(with_campaign=False)
    result = {"needs_human": True, "reply": "I'll have someone follow up."}
    original = result["reply"]
    conversation_flow._maybe_offer_call_consent(db, conv, result)
    assert result["reply"] == original


def test_never_asks_twice():
    db, client, conv, campaign = _setup(call_escalation_enabled=True)
    conv.CallConsentStatus = "declined"   # already resolved earlier in the thread
    result = {"needs_human": True, "reply": "I'll have someone follow up."}
    original = result["reply"]
    conversation_flow._maybe_offer_call_consent(db, conv, result)
    assert result["reply"] == original


def test_does_not_offer_when_handoff_isnt_actually_firing():
    db, client, conv, campaign = _setup(call_escalation_enabled=True)
    result = {"needs_human": False, "reply": "Here's the answer."}
    original = result["reply"]
    conversation_flow._maybe_offer_call_consent(db, conv, result)
    assert result["reply"] == original


# =========================================================================== #
# resolving (yes / no / ambiguous)
# =========================================================================== #
def test_a_yes_triggers_a_real_call_and_marks_accepted():
    db, client, conv, campaign = _setup(call_escalation_enabled=True)
    conv.CallConsentStatus = "asked"
    db.commit()

    calls = []
    from LeadAI.services import call_bridge
    saved = call_bridge.start_call_for_conversation
    call_bridge.start_call_for_conversation = lambda *a, **k: calls.append(k) or models.LeadCall(
        ClientId=client.Id, ConversationId=conv.Id, Status="initiated",
    )
    try:
        conversation_flow._resolve_call_consent(db, client, conv, "Yes please call me")
    finally:
        call_bridge.start_call_for_conversation = saved

    assert conv.CallConsentStatus == "accepted"
    assert len(calls) == 1
    assert calls[0]["override_number"] == "+919000000001"


def test_a_no_marks_declined_and_never_calls():
    db, client, conv, campaign = _setup(call_escalation_enabled=True)
    conv.CallConsentStatus = "asked"
    db.commit()

    calls = []
    from LeadAI.services import call_bridge
    saved = call_bridge.start_call_for_conversation
    call_bridge.start_call_for_conversation = lambda *a, **k: calls.append(1)
    try:
        conversation_flow._resolve_call_consent(db, client, conv, "No, not now thanks")
    finally:
        call_bridge.start_call_for_conversation = saved

    assert conv.CallConsentStatus == "declined"
    assert calls == []


def test_an_ambiguous_reply_leaves_the_offer_open_for_next_turn():
    db, client, conv, campaign = _setup(call_escalation_enabled=True)
    conv.CallConsentStatus = "asked"
    db.commit()
    conversation_flow._resolve_call_consent(db, client, conv, "What time do you close today?")
    assert conv.CallConsentStatus == "asked"   # still pending, not resolved either way


def test_resolve_is_a_no_op_when_nothing_was_ever_asked():
    db, client, conv, campaign = _setup(call_escalation_enabled=True)
    assert conv.CallConsentStatus is None
    conversation_flow._resolve_call_consent(db, client, conv, "Yes!")
    assert conv.CallConsentStatus is None   # never asked, so a stray "yes" does nothing


def test_a_yes_with_no_phone_on_file_does_not_crash_the_turn():
    db, client, conv, campaign = _setup(call_escalation_enabled=True, with_phone=False)
    conv.CallConsentStatus = "asked"
    db.commit()
    conversation_flow._resolve_call_consent(db, client, conv, "Yes please")
    assert conv.CallConsentStatus == "accepted"   # intent recorded even though no call could start


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
