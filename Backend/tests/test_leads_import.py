"""Importing a file of leads now auto-classifies them into one draft batch per
company-defined product, instead of leaving an operator to build each campaign
by hand. Phase 2 of "we already have batches... create an import leads API
with field name/type/required, then classify by product, one batch per
product (and a separate one for unknown)."

Run: python tests/test_leads_import.py
"""
import asyncio
import io
import uuid

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from fastapi import HTTPException, UploadFile  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.models_ext import LeadCampaign  # noqa: E402
from LeadAI.rbac import ROLE_PERMISSIONS, Principal  # noqa: E402
from LeadAI.routers import leads_import  # noqa: E402
from LeadAI.serializers_ext import campaign_out  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def _principal(client_id):
    return Principal(email="admin@kestrel.test", role="company_admin", client_id=client_id,
                     permissions=set(ROLE_PERMISSIONS["company_admin"]))


def _csv_file(text: str, name: str = "leads.csv") -> UploadFile:
    return UploadFile(file=io.BytesIO(text.encode("utf-8")), filename=name,
                      headers={"content-type": "text/csv"})


def _run(coro):
    return asyncio.run(coro)


def _setup(with_products=True):
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    account = models.LeadChannelAccount(ClientId=client.Id, Channel="whatsapp", Name="wa",
                                        ExternalId=uuid.uuid4().hex)
    db.add(account)
    db.flush()
    if with_products:
        db.add(models.LeadProduct(ClientId=client.Id, ProductName="Home Loan", ProductDescription="loan"))
        db.add(models.LeadProduct(ClientId=client.Id, ProductName="Personal Loan", ProductDescription="loan"))
    db.commit()
    return db, client, account


# =========================================================================== #
# schema
# =========================================================================== #
def test_schema_lists_fixed_fields_and_the_companys_own_data_points():
    db, client, account = _setup(with_products=False)
    db.add(models.LeadCompanyDataPoint(ClientId=client.Id, Key="budget", Label="Budget",
                                       DataType="number", Required=True))
    db.commit()
    out = leads_import.import_schema(principal=_principal(client.Id), db=db)
    keys = {f.key for f in out.fields}
    assert {"name", "phone", "email", "instagram_id", "facebook_id", "product"} <= keys
    assert "budget" in keys
    budget_field = next(f for f in out.fields if f.key == "budget")
    assert budget_field.source == "data_point" and budget_field.required is True


# =========================================================================== #
# import + classification
# =========================================================================== #
def test_import_classifies_leads_into_one_draft_campaign_per_product():
    db, client, account = _setup()
    csv_text = (
        "name,phone,product\n"
        "Amit Singh,+919000000001,Home Loan\n"
        "Rita Shah,+919000000002,home loan\n"   # different casing, same product
        "Karan Mehta,+919000000003,Personal Loan\n"
        "No Product Lead,+919000000004,Scooter Insurance\n"   # not in catalog
    )
    result = _run(leads_import.import_leads(
        request=None, file=_csv_file(csv_text),
        channel="chat", chat_channel="whatsapp", chat_channel_account_id=account.Id,
        instagram_account_id=None, facebook_account_id=None, voice_script_id=None,
        call_escalation=False, principal=_principal(client.Id), db=db,
    ))
    assert result.total == 4 and result.valid == 4 and result.invalid == 0

    by_product = {b.product: b for b in result.batches}
    assert by_product["Home Loan"].lead_count == 2
    assert by_product["Personal Loan"].lead_count == 1
    assert by_product["Unknown Product"].lead_count == 1

    campaigns = db.query(LeadCampaign).filter(LeadCampaign.ClientId == client.Id).all()
    assert len(campaigns) == 3
    assert all(c.Status == "draft" for c in campaigns)        # never auto-started
    assert all(c.CreatedVia == "import" for c in campaigns)
    home_loan_campaign = next(c for c in campaigns if c.Name.startswith("Home Loan"))
    assert home_loan_campaign.ProductId is not None
    unknown_campaign = next(c for c in campaigns if c.Name.startswith("Unknown Product"))
    assert unknown_campaign.ProductId is None


def test_a_real_name_never_becomes_the_masked_public_ref():
    # Real bug: an imported lead's actual name showed up in the inbox
    # unmasked, with no reveal step — PublicRef (what the inbox shows BEFORE
    # an operator explicitly reveals PII) was set to the real name instead
    # of a safe placeholder.
    db, client, account = _setup()
    csv_text = "name,phone,product\nPriya Verma,+919000000009,Home Loan\n"
    _run(leads_import.import_leads(
        request=None, file=_csv_file(csv_text),
        channel="chat", chat_channel="whatsapp", chat_channel_account_id=account.Id,
        instagram_account_id=None, facebook_account_id=None, voice_script_id=None,
        call_escalation=False, principal=_principal(client.Id), db=db,
    ))
    customer = db.query(models.LeadCustomer).filter(models.LeadCustomer.ClientId == client.Id).one()
    assert customer.DisplayName == "Priya Verma"      # kept, but gated behind reveal elsewhere
    assert "Priya" not in customer.PublicRef           # never leaks into the pre-reveal placeholder
    assert customer.PublicRef.startswith("Lead #")


def test_reimporting_the_same_phone_continues_the_existing_lead_not_a_duplicate():
    # Real bug: a frontend error led the operator to retry the same import,
    # and because every row unconditionally created a fresh conversation +
    # lead, the same real person ended up with two separate lead threads.
    db, client, account = _setup()
    csv_text = "name,phone,product\nAmit Singh,+919000000001,Home Loan\n"
    first = _run(leads_import.import_leads(
        request=None, file=_csv_file(csv_text),
        channel="chat", chat_channel="whatsapp", chat_channel_account_id=account.Id,
        instagram_account_id=None, facebook_account_id=None, voice_script_id=None,
        call_escalation=False, principal=_principal(client.Id), db=db,
    ))
    second = _run(leads_import.import_leads(
        request=None, file=_csv_file(csv_text),
        channel="chat", chat_channel="whatsapp", chat_channel_account_id=account.Id,
        instagram_account_id=None, facebook_account_id=None, voice_script_id=None,
        call_escalation=False, principal=_principal(client.Id), db=db,
    ))
    assert first.valid == 1 and second.valid == 1

    customers = db.query(models.LeadCustomer).filter(
        models.LeadCustomer.ClientId == client.Id, models.LeadCustomer.IsDeleted == False,
    ).all()
    assert len(customers) == 1                        # same phone, one customer

    conversations = db.query(models.LeadConversation).filter(
        models.LeadConversation.CustomerId == customers[0].Id, models.LeadConversation.IsDeleted == False,
    ).all()
    assert len(conversations) == 1                     # not forked into a second thread

    leads = db.query(models.Lead).filter(
        models.Lead.ConversationId == conversations[0].Id, models.Lead.IsDeleted == False,
    ).all()
    assert len(leads) == 1                              # one lead, continued — not duplicated


def test_a_closed_conversation_does_get_a_fresh_one_not_reopened_silently():
    db, client, account = _setup()
    csv_text = "name,phone,product\nAmit Singh,+919000000001,Home Loan\n"
    _run(leads_import.import_leads(
        request=None, file=_csv_file(csv_text),
        channel="chat", chat_channel="whatsapp", chat_channel_account_id=account.Id,
        instagram_account_id=None, facebook_account_id=None, voice_script_id=None,
        call_escalation=False, principal=_principal(client.Id), db=db,
    ))
    customer = db.query(models.LeadCustomer).filter(models.LeadCustomer.ClientId == client.Id).one()
    db.query(models.LeadConversation).filter(models.LeadConversation.CustomerId == customer.Id).update(
        {"Status": "closed"}
    )
    db.commit()

    _run(leads_import.import_leads(
        request=None, file=_csv_file(csv_text),
        channel="chat", chat_channel="whatsapp", chat_channel_account_id=account.Id,
        instagram_account_id=None, facebook_account_id=None, voice_script_id=None,
        call_escalation=False, principal=_principal(client.Id), db=db,
    ))
    conversations = db.query(models.LeadConversation).filter(
        models.LeadConversation.CustomerId == customer.Id, models.LeadConversation.IsDeleted == False,
    ).all()
    assert len(conversations) == 2    # the closed one stays closed; a new thread opens rather than reusing it


def test_a_company_with_no_catalog_puts_everything_in_one_unknown_batch():
    db, client, account = _setup(with_products=False)
    csv_text = "name,phone,product\nA,+919000000001,Anything\nB,+919000000002,Something Else\n"
    result = _run(leads_import.import_leads(
        request=None, file=_csv_file(csv_text),
        channel="chat", chat_channel="whatsapp", chat_channel_account_id=account.Id,
        instagram_account_id=None, facebook_account_id=None, voice_script_id=None,
        call_escalation=False, principal=_principal(client.Id), db=db,
    ))
    assert len(result.batches) == 1
    assert result.batches[0].product == "Unknown Product"
    assert result.batches[0].lead_count == 2


def test_invalid_rows_are_reported_not_silently_dropped():
    db, client, account = _setup()
    csv_text = "name,phone,product\nGood Row,+919000000001,Home Loan\nBad Row,,Home Loan\n"
    result = _run(leads_import.import_leads(
        request=None, file=_csv_file(csv_text),
        channel="chat", chat_channel="whatsapp", chat_channel_account_id=account.Id,
        instagram_account_id=None, facebook_account_id=None, voice_script_id=None,
        call_escalation=False, principal=_principal(client.Id), db=db,
    ))
    assert result.total == 2 and result.valid == 1 and result.invalid == 1
    assert result.invalid_rows[0].row_number == 2


def test_company_data_points_are_prefilled_from_matching_columns():
    db, client, account = _setup()
    db.add(models.LeadCompanyDataPoint(ClientId=client.Id, Key="budget", Label="Budget", DataType="number"))
    db.commit()
    csv_text = "name,phone,product,Budget\nAmit,+919000000001,Home Loan,4500000\n"
    _run(leads_import.import_leads(
        request=None, file=_csv_file(csv_text),
        channel="chat", chat_channel="whatsapp", chat_channel_account_id=account.Id,
        instagram_account_id=None, facebook_account_id=None, voice_script_id=None,
        call_escalation=False, principal=_principal(client.Id), db=db,
    ))
    lead = db.query(models.Lead).filter(models.Lead.ClientId == client.Id).one()
    assert lead.DataPointsJson == {"budget": 4500000}


# =========================================================================== #
# broadcast vs lead_campaign — derived from created_via, never stored separately
# =========================================================================== #
def test_an_imported_batch_is_labelled_a_lead_campaign():
    db, client, account = _setup()
    csv_text = "name,phone,product\nA,+919000000001,Home Loan\n"
    result = _run(leads_import.import_leads(
        request=None, file=_csv_file(csv_text),
        channel="chat", chat_channel="whatsapp", chat_channel_account_id=account.Id,
        instagram_account_id=None, facebook_account_id=None, voice_script_id=None,
        call_escalation=False, principal=_principal(client.Id), db=db,
    ))
    campaign = db.get(LeadCampaign, result.batches[0].campaign_id)
    out = campaign_out(campaign)
    assert out.created_via == "import"
    assert out.campaign_type == "lead_campaign"


def test_a_manually_created_campaign_is_labelled_a_broadcast():
    db, client, account = _setup()
    campaign = LeadCampaign(ClientId=client.Id, Name="Diwali promo", Kind="message",
                            Channel="whatsapp", AudienceType="customers", Status="draft")
    db.add(campaign)
    db.commit()
    out = campaign_out(campaign)
    assert out.created_via == "manual"
    assert out.campaign_type == "broadcast"


# =========================================================================== #
# validation
# =========================================================================== #
def test_instagram_column_without_an_account_id_is_rejected_when_chat_channel_is_instagram():
    db, client, account = _setup()
    csv_text = "name,instagram_id,product\nSomeone,igsid123,Home Loan\n"
    try:
        _run(leads_import.import_leads(
            request=None, file=_csv_file(csv_text),
            channel="chat", chat_channel="instagram", chat_channel_account_id=account.Id,
            instagram_account_id=None, facebook_account_id=None, voice_script_id=None,
            call_escalation=False, principal=_principal(client.Id), db=db,
        ))
        assert False, "should have required instagram_account_id"
    except HTTPException as exc:
        assert exc.status_code == 422


def test_an_instagram_id_column_never_blocks_an_unrelated_call_campaign():
    # Real bug: the import template includes every possible column, so a row
    # can carry an instagram_id even when this particular batch is a pure
    # voice-call campaign that never sends to it. The column being present
    # must not force specifying an instagram_account_id that has nothing to
    # do with this campaign's actual channel.
    db, client, account = _setup()
    csv_text = "name,phone,instagram_id,product\nSomeone,+919000000001,igsid123,Home Loan\n"
    result = _run(leads_import.import_leads(
        request=None, file=_csv_file(csv_text),
        channel="call", chat_channel=None, chat_channel_account_id=None,
        instagram_account_id=None, facebook_account_id=None, voice_script_id=None,
        call_escalation=False, principal=_principal(client.Id), db=db,
    ))
    assert result.valid == 1
    # No identity is recorded either, since no account id was ever given.
    assert db.query(models.LeadChannelIdentity).filter(
        models.LeadChannelIdentity.ClientId == client.Id
    ).count() == 0


def test_an_instagram_id_column_never_blocks_a_whatsapp_only_chat_campaign():
    db, client, account = _setup()
    csv_text = "name,phone,instagram_id,product\nSomeone,+919000000001,igsid123,Home Loan\n"
    result = _run(leads_import.import_leads(
        request=None, file=_csv_file(csv_text),
        channel="chat", chat_channel="whatsapp", chat_channel_account_id=account.Id,
        instagram_account_id=None, facebook_account_id=None, voice_script_id=None,
        call_escalation=False, principal=_principal(client.Id), db=db,
    ))
    assert result.valid == 1


def test_the_identity_is_still_recorded_when_an_account_id_is_given_even_on_a_call_campaign():
    db, client, account = _setup()
    ig_account = models.LeadChannelAccount(ClientId=client.Id, Channel="instagram", Name="ig",
                                           ExternalId=uuid.uuid4().hex)
    db.add(ig_account)
    db.commit()
    csv_text = "name,phone,instagram_id,product\nSomeone,+919000000001,igsid123,Home Loan\n"
    _run(leads_import.import_leads(
        request=None, file=_csv_file(csv_text),
        channel="call", chat_channel=None, chat_channel_account_id=None,
        instagram_account_id=ig_account.Id, facebook_account_id=None, voice_script_id=None,
        call_escalation=False, principal=_principal(client.Id), db=db,
    ))
    identity = db.query(models.LeadChannelIdentity).filter(
        models.LeadChannelIdentity.ClientId == client.Id
    ).one()
    assert identity.ExternalUserId == "igsid123"


def test_call_escalation_flag_rejected_unless_channel_is_both():
    db, client, account = _setup()
    csv_text = "name,phone,product\nA,+919000000001,Home Loan\n"
    try:
        _run(leads_import.import_leads(
            request=None, file=_csv_file(csv_text),
            channel="chat", chat_channel="whatsapp", chat_channel_account_id=account.Id,
            instagram_account_id=None, facebook_account_id=None, voice_script_id=None,
            call_escalation=True, principal=_principal(client.Id), db=db,
        ))
        assert False, "should have rejected call_escalation with channel='chat'"
    except HTTPException as exc:
        assert exc.status_code == 422


def test_channel_both_with_escalation_marks_the_campaign():
    db, client, account = _setup()
    csv_text = "name,phone,product\nA,+919000000001,Home Loan\n"
    result = _run(leads_import.import_leads(
        request=None, file=_csv_file(csv_text),
        channel="both", chat_channel="whatsapp", chat_channel_account_id=account.Id,
        instagram_account_id=None, facebook_account_id=None, voice_script_id=None,
        call_escalation=True, principal=_principal(client.Id), db=db,
    ))
    campaign = db.get(LeadCampaign, result.batches[0].campaign_id)
    assert campaign.CallEscalationEnabled is True


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
