"""Company admins can now define their own named, typed fields ("data points")
for the AI to collect from every lead — e.g. Kestrel Homes wants "Preferred
Location" (text) and "Has a home loan pre-approval" (yes/no) captured for every
caller, not just the fixed budget/timeline/product fields the AI already tracks.

Four pieces, tested separately:
  1. CRUD — routers/data_points.py, company-scoped, script.read/script.manage.
  2. Extraction — ai_engine.qualify()'s existing analysis call also fills in
     whatever it can for the company's defined fields, validated by type.
  3. Proactive asking — memory.missing_data_points_note() surfaces any REQUIRED
     field still missing, independent of thread length (unlike the existing
     long-thread memory note, which only fires once a conversation is truncated
     — a brand-new conversation is exactly when a data point is most likely
     still missing).
  4. Reading them back — the collected values ride along on the SAME LeadOut
     the inbox conversation detail endpoint already returns (serializers.lead_out),
     rather than needing a new endpoint.

Run: python tests/test_data_points.py
"""
import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.rbac import ROLE_PERMISSIONS, Principal  # noqa: E402
from LeadAI.routers import data_points  # noqa: E402
from LeadAI.schemas import DataPointCreate, DataPointUpdate  # noqa: E402
from LeadAI.services import ai_engine, memory  # noqa: E402
from LeadAI.serializers import lead_out  # noqa: E402
from fastapi import HTTPException  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def _principal(client_id):
    return Principal(email="admin@kestrel.test", role="company_admin", client_id=client_id,
                     permissions=set(ROLE_PERMISSIONS["company_admin"]))


def _client(db, name="Kestrel Homes"):
    client = Client(Name=name)
    db.add(client)
    db.flush()
    return client


# =========================================================================== #
# 1. CRUD
# =========================================================================== #
def test_create_list_update_and_delete_a_data_point():
    db = SessionLocalAdmin()
    client = _client(db)
    principal = _principal(client.Id)

    created = data_points.create_data_point(
        DataPointCreate(key="preferred_location", label="Preferred Location", data_type="text"),
        request=None, principal=principal, db=db,
    )
    assert created.key == "preferred_location" and created.required is False

    listed = data_points.list_data_points(principal=principal, db=db)
    assert [d.id for d in listed] == [created.id]

    updated = data_points.update_data_point(
        created.id, DataPointUpdate(required=True, label="Preferred City"),
        request=None, principal=principal, db=db,
    )
    assert updated.required is True and updated.label == "Preferred City"

    out = data_points.delete_data_point(created.id, request=None, principal=principal, db=db)
    assert out.message == "Data point deleted"
    assert data_points.list_data_points(principal=principal, db=db) == []


def test_a_select_data_point_needs_at_least_two_options():
    db = SessionLocalAdmin()
    client = _client(db)
    principal = _principal(client.Id)
    try:
        data_points.create_data_point(
            DataPointCreate(key="property_type", label="Property Type", data_type="select",
                            options=["Apartment"]),
            request=None, principal=principal, db=db,
        )
        assert False, "should have rejected a select with only one option"
    except HTTPException as exc:
        assert exc.status_code == 422


def test_a_duplicate_key_for_the_same_company_is_rejected():
    db = SessionLocalAdmin()
    client = _client(db)
    principal = _principal(client.Id)
    data_points.create_data_point(
        DataPointCreate(key="budget_range", label="Budget Range", data_type="text"),
        request=None, principal=principal, db=db,
    )
    try:
        data_points.create_data_point(
            DataPointCreate(key="budget_range", label="Budget (again)", data_type="text"),
            request=None, principal=principal, db=db,
        )
        assert False, "should have rejected the duplicate key"
    except HTTPException as exc:
        assert exc.status_code == 409


def test_one_companys_data_points_are_invisible_to_another():
    db = SessionLocalAdmin()
    kestrel = _client(db, "Kestrel Homes")
    nexa = _client(db, "Nexa Finserv")
    data_points.create_data_point(
        DataPointCreate(key="preferred_location", label="Preferred Location", data_type="text"),
        request=None, principal=_principal(kestrel.Id), db=db,
    )
    assert data_points.list_data_points(principal=_principal(nexa.Id), db=db) == []


# =========================================================================== #
# 2. extraction (ai_engine)
# =========================================================================== #
def test_validate_data_point_value_by_type():
    dp_num = models.LeadCompanyDataPoint(ClientId="c", Key="k", Label="L", DataType="number")
    assert ai_engine._validate_data_point_value("45 lakh", dp_num) is None   # not a bare number
    assert ai_engine._validate_data_point_value("45", dp_num) == 45
    assert ai_engine._validate_data_point_value(None, dp_num) is None

    dp_bool = models.LeadCompanyDataPoint(ClientId="c", Key="k", Label="L", DataType="boolean")
    assert ai_engine._validate_data_point_value("yes", dp_bool) is True
    assert ai_engine._validate_data_point_value("no", dp_bool) is False

    dp_select = models.LeadCompanyDataPoint(ClientId="c", Key="k", Label="L", DataType="select",
                                            OptionsJson=["Apartment", "Villa"])
    assert ai_engine._validate_data_point_value("apartment", dp_select) == "Apartment"  # case-normalised
    assert ai_engine._validate_data_point_value("Plot", dp_select) is None              # not an option

    dp_email = models.LeadCompanyDataPoint(ClientId="c", Key="k", Label="L", DataType="email")
    assert ai_engine._validate_data_point_value("a@b.com", dp_email) == "a@b.com"
    assert ai_engine._validate_data_point_value("not-an-email", dp_email) is None


def test_a_date_data_point_tells_the_model_what_today_actually_is():
    # Production bug: a customer said "I'll visit the site today" and the date
    # data point came back "2023" — the model has no inherent sense of "now",
    # so "today"/"tomorrow" resolved against its training data instead of the
    # real date, unless the prompt states it explicitly.
    import datetime as _dt

    dp_date = models.LeadCompanyDataPoint(ClientId="c", Key="visit_date", Label="Site Visit Date",
                                          DataType="date")
    instruction = ai_engine._data_points_instruction([dp_date])
    today = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")
    assert f"Today's actual date is {today}" in instruction


def test_the_date_note_names_todays_weekday_so_a_bare_day_name_resolves_correctly():
    """Production bug: a customer said just "Sunday" (no date) for a site
    visit, and it came back as a Thursday. Giving only the ISO date forces
    the model to work out what weekday that is before counting forward to
    "next Sunday" — exactly the arithmetic it got wrong. Naming the weekday
    removes that step; the instruction also says explicitly to resolve a
    bare day name to its NEXT upcoming occurrence."""
    import datetime as _dt

    dp_date = models.LeadCompanyDataPoint(ClientId="c", Key="visit_date", Label="Site Visit Date",
                                          DataType="date")
    instruction = ai_engine._data_points_instruction([dp_date])
    today = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=5, minutes=30)))
    assert f"({today.strftime('%A')})" in instruction
    assert "NEXT upcoming occurrence" in instruction


def test_no_date_note_when_no_data_point_is_a_date():
    dp_text = models.LeadCompanyDataPoint(ClientId="c", Key="notes", Label="Notes", DataType="text")
    instruction = ai_engine._data_points_instruction([dp_text])
    assert "Today's actual date" not in instruction


def test_qualify_extracts_data_points_and_never_blanks_out_what_it_already_knew():
    db = SessionLocalAdmin()
    client = _client(db)
    dp = models.LeadCompanyDataPoint(ClientId=client.Id, Key="preferred_location",
                                     Label="Preferred Location", DataType="text", Required=True)
    db.add(dp)
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="chat")
    db.add(conv)
    db.flush()
    lead = models.Lead(ClientId=client.Id, ConversationId=conv.Id, DataPointsJson={"preferred_location": "Mohali"})
    msg = models.LeadMessage(ClientId=client.Id, ConversationId=conv.Id, Sender="customer",
                             Content="Actually let's talk about financing instead")
    db.add(msg)
    db.commit()

    from LeadAI.config import settings as real_settings

    class _Settings:
        llm_enabled = True
        llm_qualification = True
        def __getattr__(self, name):
            return getattr(real_settings, name)

    saved = (ai_engine.settings, ai_engine.llm.complete_json)
    ai_engine.settings = _Settings()
    # The model this turn does not repeat the location at all — it should survive untouched.
    ai_engine.llm.complete_json = lambda *a, **k: ({
        "intent": "evaluating", "timeline": "unknown", "budget": "unknown", "product": "unknown",
        "sentiment": "neutral", "summary": "s", "next_step": "n", "facts": [],
        "data_points": {"preferred_location": None},
    }, {})
    try:
        ai_engine.qualify(db, client.Id, lead, [msg])
    finally:
        ai_engine.settings, ai_engine.llm.complete_json = saved

    assert lead.DataPointsJson == {"preferred_location": "Mohali"}   # untouched, not wiped


def test_qualify_overwrites_a_data_point_when_the_customer_corrects_it():
    db = SessionLocalAdmin()
    client = _client(db)
    db.add(models.LeadCompanyDataPoint(ClientId=client.Id, Key="preferred_location",
                                       Label="Preferred Location", DataType="text"))
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="chat")
    db.add(conv)
    db.flush()
    lead = models.Lead(ClientId=client.Id, ConversationId=conv.Id, DataPointsJson={"preferred_location": "Mohali"})
    msg = models.LeadMessage(ClientId=client.Id, ConversationId=conv.Id, Sender="customer",
                             Content="Actually I'd prefer Zirakpur")
    db.add(msg)
    db.commit()

    from LeadAI.config import settings as real_settings

    class _Settings:
        llm_enabled = True
        llm_qualification = True
        def __getattr__(self, name):
            return getattr(real_settings, name)

    saved = (ai_engine.settings, ai_engine.llm.complete_json)
    ai_engine.settings = _Settings()
    ai_engine.llm.complete_json = lambda *a, **k: ({
        "intent": "evaluating", "timeline": "unknown", "budget": "unknown", "product": "unknown",
        "sentiment": "neutral", "summary": "s", "next_step": "n", "facts": [],
        "data_points": {"preferred_location": "Zirakpur"},
    }, {})
    try:
        ai_engine.qualify(db, client.Id, lead, [msg])
    finally:
        ai_engine.settings, ai_engine.llm.complete_json = saved

    assert lead.DataPointsJson == {"preferred_location": "Zirakpur"}


# =========================================================================== #
# 3. proactive asking (memory)
# =========================================================================== #
def test_a_missing_required_data_point_produces_a_note():
    db = SessionLocalAdmin()
    client = _client(db)
    db.add(models.LeadCompanyDataPoint(ClientId=client.Id, Key="preferred_location",
                                       Label="Preferred Location", DataType="text", Required=True))
    db.commit()
    note = memory.missing_data_points_note(db, client.Id, lead=None)
    assert "Preferred Location" in note


def test_an_already_collected_required_data_point_is_not_mentioned_again():
    db = SessionLocalAdmin()
    client = _client(db)
    db.add(models.LeadCompanyDataPoint(ClientId=client.Id, Key="preferred_location",
                                       Label="Preferred Location", DataType="text", Required=True))
    db.commit()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="chat")
    db.add(conv)
    db.flush()
    lead = models.Lead(ClientId=client.Id, ConversationId=conv.Id,
                       DataPointsJson={"preferred_location": "Mohali"})
    db.add(lead)
    db.commit()
    assert memory.missing_data_points_note(db, client.Id, lead) == ""


def test_no_note_at_all_when_the_company_has_no_required_data_points():
    db = SessionLocalAdmin()
    client = _client(db)
    # Optional (not required) data point — never proactively asked for.
    db.add(models.LeadCompanyDataPoint(ClientId=client.Id, Key="notes", Label="Notes",
                                       DataType="text", Required=False))
    db.commit()
    assert memory.missing_data_points_note(db, client.Id, lead=None) == ""


def test_this_is_not_gated_by_conversation_length_unlike_the_thread_note():
    # thread_state_note() only fires once the thread is truncated; a brand-new
    # conversation (the case that matters most for asking) must still get the note.
    db = SessionLocalAdmin()
    client = _client(db)
    db.add(models.LeadCompanyDataPoint(ClientId=client.Id, Key="preferred_location",
                                       Label="Preferred Location", DataType="text", Required=True))
    db.commit()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="chat")
    db.add(conv)
    db.flush()
    note = memory.missing_data_points_note(db, client.Id, lead=None)
    state_note = memory.thread_state_note(db, conv, history=[])
    assert note and not state_note


# =========================================================================== #
# 4. reading collected values back (inbox conversation detail)
# =========================================================================== #
def test_collected_values_ride_on_the_same_lead_out_the_inbox_endpoint_returns():
    db = SessionLocalAdmin()
    client = _client(db)
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="chat")
    db.add(conv)
    db.flush()
    lead = models.Lead(ClientId=client.Id, ConversationId=conv.Id,
                       DataPointsJson={"target_budget": 4500000})
    db.add(lead)
    db.commit()

    out = lead_out(lead)
    assert out.data_points == {"target_budget": 4500000}


def test_a_lead_with_nothing_collected_yet_returns_none_not_an_error():
    db = SessionLocalAdmin()
    client = _client(db)
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="chat")
    db.add(conv)
    db.flush()
    lead = models.Lead(ClientId=client.Id, ConversationId=conv.Id)
    db.add(lead)
    db.commit()

    assert lead_out(lead).data_points is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
