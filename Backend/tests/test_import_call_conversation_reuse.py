"""Real bug found while adding an inbox filter for "came from a lead-import
batch" vs "came in on its own": leads_import.py creates one LeadConversation
per imported lead (holding the Lead, Product, data points collected at
import time) — but when that lead's auto-classified campaign later actually
runs a CALL, `_iter_targets`'s "list" audience branch never told
build_audience that conversation existed, so `_place_call` saw no
ConversationId on the recipient and forked a SECOND, brand-new conversation
for the same customer. The call transcript and its own fresh (unscored) Lead
landed on that second conversation; the import-time Lead with its real data
points was left orphaned on the first one with no CampaignId ever set on
it — which is also exactly why there was no reliable way to tag "this
conversation belongs to batch X" for an inbox filter.

Run: python tests/test_import_call_conversation_reuse.py
"""
import asyncio
import io

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from fastapi import UploadFile  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.rbac import ROLE_PERMISSIONS, Principal  # noqa: E402
from LeadAI.routers import leads_import  # noqa: E402
from LeadAI.services import call_bridge, campaign_runner as cr  # noqa: E402

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


def _mock_place_call(db, client_id, company_name, conversation, initiated_by,
                     mode="ai_voice", script_id=None, override_number=None):
    call = models.LeadCall(ClientId=client_id, ConversationId=conversation.Id, Status="completed",
                           CallSid="CA-test", DurationSec=42)
    db.add(call)
    db.flush()
    return call


def test_a_call_campaign_reuses_the_imports_conversation_not_a_second_one():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Reuse")
    db.add(client)
    db.flush()
    db.add(models.LeadProduct(ClientId=client.Id, ProductName="Home Loan", ProductDescription="loan"))
    db.add(models.LeadCompanyDataPoint(ClientId=client.Id, Key="budget", Label="Budget", DataType="number"))
    db.commit()
    principal = _principal(client.Id)

    csv_text = "name,phone,product,Budget\nAmit Singh,+919000000055,Home Loan,4500000\n"
    result = _run(leads_import.import_leads(
        request=None, file=_csv_file(csv_text),
        channel="call", chat_channel=None, chat_channel_account_id=None,
        instagram_account_id=None, facebook_account_id=None, voice_script_id="script-1",
        call_escalation=False, principal=principal, db=db,
    ))
    campaign_id = result.batches[0].campaign_id
    campaign = db.get(models.LeadCampaign, campaign_id)

    customer = db.query(models.LeadCustomer).filter(models.LeadCustomer.ClientId == client.Id).one()
    import_conversation = (
        db.query(models.LeadConversation)
        .filter(models.LeadConversation.CustomerId == customer.Id)
        .one()
    )
    import_lead = db.query(models.Lead).filter(
        models.Lead.ConversationId == import_conversation.Id
    ).one()
    assert import_lead.DataPointsJson == {"budget": 4500000}
    assert import_conversation.CampaignId is None  # not yet tagged — the campaign hasn't run

    call_bridge.start_call_for_conversation = _mock_place_call
    cr.build_audience(db, campaign)
    cr.run_campaign_job(db, {"campaign_id": campaign.Id})

    # Still exactly one conversation for this customer — the call did NOT fork a second one.
    conversations = db.query(models.LeadConversation).filter(
        models.LeadConversation.CustomerId == customer.Id
    ).all()
    assert len(conversations) == 1
    assert conversations[0].Id == import_conversation.Id
    assert conversations[0].CampaignId == campaign.Id

    # The recipient was wired to the SAME conversation, and the SAME Lead that
    # holds the import's data points is the one the campaign actually used.
    recipient = db.query(models.LeadCampaignRecipient).filter(
        models.LeadCampaignRecipient.CampaignId == campaign.Id
    ).one()
    assert recipient.ConversationId == import_conversation.Id
    assert db.query(models.Lead).filter(
        models.Lead.ConversationId == import_conversation.Id
    ).count() == 1
    db.refresh(import_lead)
    assert import_lead.DataPointsJson == {"budget": 4500000}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
