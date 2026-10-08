"""A lead's own timeline: where it came from, who it's assigned to, calls
placed, and which data points were collected and when — unioned from every
entity an event about a lead can be filed under (lead/conversation/call/
customer), plus a source classification mirroring the inbox's own
inbound/import/broadcast split (test_inbox_lead_source_filter.py).

Two pieces:
  1. GET /inbox/{conversation_id}/history (inbox.get_lead_history) — the union
     query, scoping, and source classification.
  2. A new discrete activity-log event (A.DATA_POINT_COLLECTED) emitted from
     conversation_flow.score_turn() whenever qualify() changes a lead's
     DataPointsJson — previously this only lived in the ephemeral per-message
     TraceJson, never as a durable, queryable event.

Run: python tests/test_lead_history.py
"""
import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import activity, models  # noqa: E402
from LeadAI.activity import A  # noqa: E402
from LeadAI.config import settings as real_settings  # noqa: E402
from LeadAI.rbac import ROLE_PERMISSIONS, Principal  # noqa: E402
from LeadAI.routers import inbox  # noqa: E402
from LeadAI.services import ai_engine, conversation_flow, scoring_queue  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def _principal(client_id):
    return Principal(email="admin@kestrel.test", role="company_admin", client_id=client_id,
                     permissions=set(ROLE_PERMISSIONS["company_admin"]))


# =========================================================================== #
# 1. GET /inbox/{conversation_id}/history
# =========================================================================== #
def _setup_conversation(campaign_id=None, channel="whatsapp"):
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C1", DisplayName="Priya")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel=channel,
                                   Status="open", MessageCount=3, CampaignId=campaign_id)
    db.add(conv)
    db.flush()
    lead = models.Lead(ClientId=client.Id, ConversationId=conv.Id)
    db.add(lead)
    call = models.LeadCall(ClientId=client.Id, ConversationId=conv.Id, CallSid="CA123")
    db.add(call)
    db.commit()
    return db, client, conv, lead, call, customer


def test_the_timeline_includes_events_filed_on_the_lead_the_conversation_the_call_and_the_customer():
    db, client, conv, lead, call, customer = _setup_conversation()

    activity.log(db, action=A.LEAD_QUALIFIED, client_id=client.Id, entity_type="lead",
                 entity_id=lead.Id, message="Lead qualified")
    activity.log(db, action=A.LEAD_ASSIGNED, client_id=client.Id, entity_type="conversation",
                 entity_id=conv.Id, message="Assigned to agent@kestrel.test")
    activity.log(db, action=A.CALL_INITIATED, client_id=client.Id, entity_type="call",
                 entity_id=call.Id, message="Outbound call placed")
    activity.log(db, action=A.PHONE_CAPTURED, client_id=client.Id, entity_type="customer",
                 entity_id=customer.Id, message="Phone number saved")
    db.commit()

    out = inbox.get_lead_history(conversation_id=conv.Id, page=1, page_size=50,
                                 principal=_principal(client.Id), db=db)
    actions = {i.action for i in out.items}
    assert actions == {A.LEAD_QUALIFIED, A.LEAD_ASSIGNED, A.CALL_INITIATED, A.PHONE_CAPTURED}
    assert out.total_items == 4


def test_another_conversations_events_never_leak_in():
    db, client, conv, lead, call, customer = _setup_conversation()
    other_customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C2")
    db.add(other_customer)
    db.flush()
    other_conv = models.LeadConversation(ClientId=client.Id, CustomerId=other_customer.Id, Channel="whatsapp")
    db.add(other_conv)
    db.commit()

    activity.log(db, action=A.LEAD_ASSIGNED, client_id=client.Id, entity_type="conversation",
                 entity_id=other_conv.Id, message="Assigned (someone else's lead)")
    db.commit()

    out = inbox.get_lead_history(conversation_id=conv.Id, page=1, page_size=50,
                                 principal=_principal(client.Id), db=db)
    assert out.total_items == 0


def test_source_is_inbound_when_there_is_no_campaign():
    db, client, conv, lead, call, customer = _setup_conversation(campaign_id=None, channel="voice")
    out = inbox.get_lead_history(conversation_id=conv.Id, page=1, page_size=50,
                                 principal=_principal(client.Id), db=db)
    assert out.source.kind == "inbound" and out.source.channel == "voice"
    assert out.source.campaign_name is None


def test_source_is_import_or_broadcast_matching_the_campaigns_created_via():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    import_campaign = models.LeadCampaign(ClientId=client.Id, Name="Home Loan — Import", Kind="call",
                                          Channel="voice", AudienceType="list", CreatedVia="import")
    db.add(import_campaign)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="voice",
                                   CampaignId=import_campaign.Id)
    db.add(conv)
    db.commit()

    out = inbox.get_lead_history(conversation_id=conv.Id, page=1, page_size=50,
                                 principal=_principal(client.Id), db=db)
    assert out.source.kind == "import"
    assert out.source.campaign_name == "Home Loan — Import"


def test_pagination_caps_items_but_reports_the_true_total():
    db, client, conv, lead, call, customer = _setup_conversation()
    for i in range(5):
        activity.log(db, action=A.LEAD_STATUS_CHANGED, client_id=client.Id, entity_type="conversation",
                     entity_id=conv.Id, message=f"status change {i}")
    db.commit()

    out = inbox.get_lead_history(conversation_id=conv.Id, page=1, page_size=2,
                                 principal=_principal(client.Id), db=db)
    assert out.total_items == 5 and len(out.items) == 2


# =========================================================================== #
# 2. DATA_POINT_COLLECTED — a discrete, durable event (not just the trace)
# =========================================================================== #
class _Settings:
    llm_enabled = True
    llm_qualification = True
    engine_mode = "off"

    def __getattr__(self, name):
        return getattr(real_settings, name)


def _wire(data_point_values):
    ai_engine.settings = _Settings()
    ai_engine.llm.complete = lambda *a, **k: ("Sure, happy to help.", {"model": "fake", "latency_ms": 1})
    ai_engine.llm.complete_json = lambda *a, **k: ({
        "intent": "evaluating", "timeline": "unknown", "budget": "unknown", "product": "unknown",
        "sentiment": "neutral", "summary": "s", "next_step": "n", "facts": [],
        "data_points": data_point_values,
    }, {})
    ai_engine.vectorstore.search = lambda *a, **k: []
    ai_engine.vectorstore.idf_map = lambda *a, **k: ({}, 1.0)
    ai_engine._detect_product = lambda *a, **k: None
    ai_engine.company_thresholds = lambda db, client_id: (0.4, 5)


def _setup_with_data_point():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    db.add(models.LeadCompanyDataPoint(ClientId=client.Id, Key="preferred_location",
                                       Label="Preferred Location", DataType="text"))
    db.add(models.LeadCompanyDataPoint(ClientId=client.Id, Key="email", Label="Email", DataType="email"))
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="web")
    db.add(conv)
    db.commit()
    return db, client, conv


def test_a_newly_collected_data_point_is_logged_as_its_own_event():
    db, client, conv = _setup_with_data_point()
    _wire({"preferred_location": "Mohali", "email": "priya@example.com"})

    conversation_flow.handle_customer_turn(db, client, conv, "I'm looking in Mohali, my email is priya@example.com")
    scoring_queue.wait_idle()
    db.expire_all()

    lead = db.query(models.Lead).filter_by(ConversationId=conv.Id).one()
    rows = (
        db.query(models.LeadActivityLog)
        .filter_by(EntityType="lead", EntityId=lead.Id, Action=A.DATA_POINT_COLLECTED)
        .all()
    )
    assert len(rows) == 1
    collected = set(rows[0].MetaJson["data_points_collected"])
    assert collected == {"preferred_location", "email"}
    # The fact that these were collected is logged — never the actual values.
    assert "Mohali" not in str(rows[0].MetaJson) and "priya@example.com" not in str(rows[0].MetaJson)
    assert "Mohali" not in rows[0].LogMessage


def test_a_turn_that_collects_nothing_new_logs_no_event():
    db, client, conv = _setup_with_data_point()
    _wire({"preferred_location": None, "email": None})

    conversation_flow.handle_customer_turn(db, client, conv, "just browsing, thanks")
    scoring_queue.wait_idle()
    db.expire_all()

    lead = db.query(models.Lead).filter_by(ConversationId=conv.Id).one()
    rows = (
        db.query(models.LeadActivityLog)
        .filter_by(EntityType="lead", EntityId=lead.Id, Action=A.DATA_POINT_COLLECTED)
        .all()
    )
    assert rows == []


def test_a_repeated_value_already_known_is_not_logged_again():
    db, client, conv = _setup_with_data_point()
    _wire({"preferred_location": "Mohali", "email": None})
    conversation_flow.handle_customer_turn(db, client, conv, "Mohali please")
    scoring_queue.wait_idle()
    db.expire_all()

    # Second turn: the model repeats the SAME value — nothing actually changed.
    _wire({"preferred_location": "Mohali", "email": None})
    conv2 = db.query(models.LeadConversation).filter_by(Id=conv.Id).one()
    conversation_flow.handle_customer_turn(db, client, conv2, "yes, Mohali")
    scoring_queue.wait_idle()
    db.expire_all()

    lead = db.query(models.Lead).filter_by(ConversationId=conv.Id).one()
    rows = (
        db.query(models.LeadActivityLog)
        .filter_by(EntityType="lead", EntityId=lead.Id, Action=A.DATA_POINT_COLLECTED)
        .all()
    )
    assert len(rows) == 1   # only the FIRST turn actually changed anything


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
