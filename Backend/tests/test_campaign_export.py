"""Phase 5/6: the campaign output CSV must carry lead score and every data
point collected for that company, not just send status — and the generated
file is archived (leadai_files) so "what was the output" stays answerable
later, same as the input file an import produced it from.

Run: python tests/test_campaign_export.py
"""
import asyncio
import csv
import io
import uuid

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.models_ext import LeadCampaign  # noqa: E402
from LeadAI.rbac import ROLE_PERMISSIONS, Principal  # noqa: E402
from LeadAI.routers import campaigns  # noqa: E402
from LeadAI.security import encrypt_pii  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def _principal(client_id):
    return Principal(email="admin@kestrel.test", role="company_admin", client_id=client_id,
                     permissions=set(ROLE_PERMISSIONS["company_admin"]))


def _setup():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    db.add(models.LeadCompanyDataPoint(ClientId=client.Id, Key="budget", Label="Budget", DataType="number"))
    db.commit()

    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C1", DisplayName="Amit Singh",
                                   PhoneEnc=encrypt_pii("+919000000001"))
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="whatsapp")
    db.add(conv)
    db.flush()
    lead = models.Lead(ClientId=client.Id, ConversationId=conv.Id, Score=82, Status="hot",
                       DataPointsJson={"budget": 4500000})
    db.add(lead)

    campaign = LeadCampaign(ClientId=client.Id, Name="Home Loan — Import 2026-10-01", Kind="message",
                            Channel="whatsapp", AudienceType="list", Status="completed")
    db.add(campaign)
    db.flush()
    db.add(models.LeadCampaignRecipient(
        ClientId=client.Id, CampaignId=campaign.Id, ConversationId=conv.Id, CustomerId=customer.Id,
        Name="Amit Singh", PhoneMasked="+9190*****01", Status="sent", DedupeKey=uuid.uuid4().hex,
    ))
    db.commit()
    return db, client, campaign, _principal(client.Id)


async def _drain(gen) -> bytes:
    return b"".join([chunk async for chunk in gen])


def _rows(response):
    body = asyncio.run(_drain(response.body_iterator)).decode("utf-8")
    return list(csv.reader(io.StringIO(body)))


def test_export_includes_lead_score_status_and_every_data_point():
    db, client, campaign, principal = _setup()
    response = campaigns.export_campaign(campaign.Id, request=None, scope=(principal, client.Id), db=db)
    rows = _rows(response)
    header, data_row = rows[0], rows[1]
    assert "Lead Score" in header and "Budget" in header
    assert data_row[header.index("Lead Score")] == "82"
    assert data_row[header.index("Lead Status")] == "hot"
    assert data_row[header.index("Budget")] == "4500000"
    assert data_row[header.index("Status")] == "sent"


def test_export_is_archived_as_a_file_and_linked_on_the_campaign():
    db, client, campaign, principal = _setup()
    assert campaign.OutputFileId is None
    campaigns.export_campaign(campaign.Id, request=None, scope=(principal, client.Id), db=db)
    db.refresh(campaign)
    assert campaign.OutputFileId is not None
    assert campaign.OutputGeneratedAt is not None
    file_row = db.get(models.LeadFile, campaign.OutputFileId)
    assert file_row is not None and file_row.Purpose == "export"


def test_a_recipient_with_no_lead_still_exports_with_blank_score_not_an_error():
    db, client, campaign, principal = _setup()
    db.add(models.LeadCampaignRecipient(
        ClientId=client.Id, CampaignId=campaign.Id, ConversationId=None, CustomerId=None,
        Name="No Lead Row", PhoneMasked="+9190*****02", Status="failed", DedupeKey=uuid.uuid4().hex,
    ))
    db.commit()
    response = campaigns.export_campaign(campaign.Id, request=None, scope=(principal, client.Id), db=db)
    rows = _rows(response)
    header = rows[0]
    no_lead_row = next(r for r in rows[1:] if r[header.index("Name")] == "No Lead Row")
    assert no_lead_row[header.index("Lead Score")] == ""


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
