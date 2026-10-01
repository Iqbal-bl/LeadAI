"""Per-run history mirroring the old VoiceAI outbound/batching.py structure
(Batch -> BatchExecution -> CallNumberExecution): LeadCampaign plays Batch's
role, LeadCampaignExecution is one row per Start/Restart, and
LeadCampaignRecipientAttempt is one row per recipient PER RUN. The point of
the attempt table: LeadCampaignRecipient only ever holds a recipient's
CURRENT state, so once a later run fixes what an earlier run got wrong, the
earlier run's own outcome would otherwise be gone — a report for "what
happened on run 1" would silently start describing run 2 instead.

Run: python tests/test_campaign_executions.py
"""
import asyncio
import csv
import io
import uuid

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.rbac import ROLE_PERMISSIONS, Principal  # noqa: E402
from LeadAI.routers import campaigns  # noqa: E402
from LeadAI.security import encrypt_pii  # noqa: E402
from LeadAI.services import billing, campaign_runner as cr  # noqa: E402
from LeadAI.services import channels  # noqa: E402

billing.check_channel_access = lambda db, client_id, channel: (True, "")

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def _principal(client_id):
    return Principal(email="admin@kestrel.test", role="company_admin", client_id=client_id,
                     permissions=set(ROLE_PERMISSIONS["company_admin"]))


def _setup_two_recipients(client_name):
    """One WhatsApp campaign, two recipients: 'good' always sends fine,
    'bad' fails on its first send and succeeds on a retry."""
    db = SessionLocalAdmin()
    client = Client(Name=client_name)
    db.add(client)
    db.flush()
    account = models.LeadChannelAccount(ClientId=client.Id, Channel="whatsapp", Name="wa",
                                        ExternalId=f"wa-{client_name}")
    db.add(account)
    db.flush()
    contact_list = models.LeadContactList(ClientId=client.Id, Name="l", SourceType="leads", Status="ready")
    db.add(contact_list)
    db.flush()

    good = models.LeadCustomer(ClientId=client.Id, PublicRef="C1", DisplayName="Good Lead",
                               PhoneEnc=encrypt_pii("+919111111111"))
    bad = models.LeadCustomer(ClientId=client.Id, PublicRef="C2", DisplayName="Bad Lead",
                              PhoneEnc=encrypt_pii("+919222222222"))
    db.add_all([good, bad])
    db.flush()
    db.add(models.LeadContactListItem(ClientId=client.Id, ListId=contact_list.Id, RowNumber=1,
                                      Name="Good Lead", CustomerId=good.Id,
                                      PhoneEnc=encrypt_pii("+919111111111"), PhoneHash="g", IsValid=True))
    db.add(models.LeadContactListItem(ClientId=client.Id, ListId=contact_list.Id, RowNumber=2,
                                      Name="Bad Lead", CustomerId=bad.Id,
                                      PhoneEnc=encrypt_pii("+919222222222"), PhoneHash="b", IsValid=True))
    campaign = models.LeadCampaign(ClientId=client.Id, Name="c", Kind="message", Channel="whatsapp",
                                   ChannelAccountId=account.Id, AudienceType="list",
                                   ListId=contact_list.Id, MessageBody="hi", Purpose="transactional")
    db.add(campaign)
    db.commit()
    return db, client, campaign, _principal(client.Id)


async def _drain(gen) -> bytes:
    return b"".join([chunk async for chunk in gen])


def _rows(response):
    body = asyncio.run(_drain(response.body_iterator)).decode("utf-8")
    return list(csv.reader(io.StringIO(body)))


def _send_fail_for_919222222222(account, channel, to, text):
    if to.lstrip("+") == "919222222222":
        raise channels.ChannelError("simulated provider rejection")
    return f"msg-{uuid.uuid4().hex[:6]}"


def test_a_completed_run_produces_one_execution_with_matching_counts():
    db, client, campaign, principal = _setup_two_recipients("Kestrel Exec")
    channels.send_text = lambda account, channel, to, text: "msg-1"

    cr.build_audience(db, campaign)
    cr.run_campaign_job(db, {"campaign_id": campaign.Id})

    out = campaigns.list_campaign_executions(
        campaign.Id, page=1, page_size=50, scope=(principal, client.Id), db=db
    )
    assert out.total_items == 1
    execution = out.items[0]
    assert execution.status == "completed"
    assert execution.restart_mode == "all"  # implicit — run_campaign_job called with no prior start_execution
    assert execution.total_count == 2
    assert execution.completed_count == 2
    assert execution.failed_count == 0

    attempts = (
        db.query(models.LeadCampaignRecipientAttempt)
        .filter(models.LeadCampaignRecipientAttempt.CampaignExecutionId == execution.id)
        .all()
    )
    assert len(attempts) == 2
    assert {a.Status for a in attempts} == {"sent"}


def test_retry_failed_opens_a_second_execution_scoped_to_only_the_failure():
    db, client, campaign, principal = _setup_two_recipients("Nexa Exec")
    channels.send_text = _send_fail_for_919222222222

    cr.build_audience(db, campaign)
    cr.run_campaign_job(db, {"campaign_id": campaign.Id})

    executions = campaigns.list_campaign_executions(
        campaign.Id, page=1, page_size=50, scope=(principal, client.Id), db=db
    ).items
    assert len(executions) == 1
    run1 = executions[0]
    assert run1.completed_count == 1 and run1.failed_count == 1

    # The provider would succeed this time — simulating "the issue was transient".
    channels.send_text = lambda account, channel, to, text: "msg-retry"
    campaigns.retry_failed(campaign.Id, request=None, scope=(principal, client.Id), db=db)
    cr.run_campaign_job(db, {"campaign_id": campaign.Id})

    executions = campaigns.list_campaign_executions(
        campaign.Id, page=1, page_size=50, scope=(principal, client.Id), db=db
    ).items
    assert len(executions) == 2
    run2 = next(e for e in executions if e.id != run1.id)
    assert run2.restart_mode == "failed_only"
    assert run2.total_count == 1  # only the one failed recipient, not both
    assert run2.completed_count == 1 and run2.failed_count == 0

    # run1's own row is untouched by run2 — its counts still show its own outcome.
    run1_fresh = db.get(models.LeadCampaignExecution, run1.id)
    assert run1_fresh.CompletedCount == 1 and run1_fresh.FailedCount == 1


def test_an_earlier_runs_export_still_shows_that_runs_real_outcome():
    """The whole reason the attempt table exists: exporting run 1 after run 2
    already fixed the failure must still show run 1 as FAILED for that
    recipient, not silently adopt the recipient's now-successful current state."""
    db, client, campaign, principal = _setup_two_recipients("Kestrel Frozen")
    channels.send_text = _send_fail_for_919222222222
    cr.build_audience(db, campaign)
    cr.run_campaign_job(db, {"campaign_id": campaign.Id})
    run1 = campaigns.list_campaign_executions(
        campaign.Id, page=1, page_size=50, scope=(principal, client.Id), db=db
    ).items[0]

    channels.send_text = lambda account, channel, to, text: "msg-retry"
    campaigns.retry_failed(campaign.Id, request=None, scope=(principal, client.Id), db=db)
    cr.run_campaign_job(db, {"campaign_id": campaign.Id})

    # The recipient's CURRENT state is now "sent" ...
    bad_recipient = (
        db.query(models.LeadCampaignRecipient)
        .filter(models.LeadCampaignRecipient.CampaignId == campaign.Id,
                models.LeadCampaignRecipient.Name == "Bad Lead")
        .one()
    )
    assert bad_recipient.Status == "sent"

    # ... but run 1's export must still say it failed on run 1.
    response = campaigns.export_campaign(
        campaign.Id, request=None, execution_id=run1.id, scope=(principal, client.Id), db=db
    )
    rows = _rows(response)
    header = rows[0]
    bad_row = next(r for r in rows[1:] if r[header.index("Name")] == "Bad Lead")
    assert bad_row[header.index("Status")] == "failed"
    # Run 1 only ever touched both recipients once — the export for it has exactly 2 rows.
    assert len(rows) - 1 == 2


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
