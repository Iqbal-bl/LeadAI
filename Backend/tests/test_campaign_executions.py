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
from fastapi import HTTPException  # noqa: E402

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.rbac import ROLE_PERMISSIONS, Principal  # noqa: E402
from LeadAI.routers import campaigns  # noqa: E402
from LeadAI.security import encrypt_pii, mask_email  # noqa: E402
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


def test_a_completed_campaign_can_be_restarted_via_start():
    """The whole point of restart_mode is restarting a campaign that already
    finished — a plain POST /start?restart_mode=all on a 'completed' campaign
    must succeed, not 409, and must open a second, separately-tracked run."""
    db, client, campaign, principal = _setup_two_recipients("Kestrel Restart")
    channels.send_text = lambda account, channel, to, text: "msg-1"
    cr.build_audience(db, campaign)
    cr.run_campaign_job(db, {"campaign_id": campaign.Id})
    db.refresh(campaign)
    assert campaign.Status == "completed"

    campaigns.start_campaign(
        campaign.Id, request=None, restart_mode="all", scope=(principal, client.Id), db=db
    )
    db.refresh(campaign)
    assert campaign.Status == "queued"
    assert campaign.CompletedAt is None

    executions = campaigns.list_campaign_executions(
        campaign.Id, page=1, page_size=50, scope=(principal, client.Id), db=db
    ).items
    assert len(executions) == 2
    newest = executions[0]
    assert newest.restart_mode == "all"
    assert newest.total_count == 2  # "all" touches both recipients again, not just failures


def test_execution_detail_and_attempts_endpoints():
    """The list endpoint alone isn't enough — an operator drilling into one
    run needs its own summary (GET .../executions/{id}) and the per-recipient
    outcomes it actually produced (GET .../executions/{id}/attempts)."""
    db, client, campaign, principal = _setup_two_recipients("Kestrel Detail")
    channels.send_text = _send_fail_for_919222222222
    cr.build_audience(db, campaign)
    cr.run_campaign_job(db, {"campaign_id": campaign.Id})

    execution_id = campaigns.list_campaign_executions(
        campaign.Id, page=1, page_size=50, scope=(principal, client.Id), db=db
    ).items[0].id

    detail = campaigns.get_campaign_execution(
        campaign.Id, execution_id, scope=(principal, client.Id), db=db
    )
    assert detail.id == execution_id
    assert detail.completed_count == 1 and detail.failed_count == 1

    attempts = campaigns.list_campaign_execution_attempts(
        campaign.Id, execution_id, page=1, page_size=50, status_filter=None,
        scope=(principal, client.Id), db=db,
    )
    assert attempts.total_items == 2
    names_by_status = {a.status: a.name for a in attempts.items}
    assert names_by_status["sent"] == "Good Lead"
    assert names_by_status["failed"] == "Bad Lead"

    # Wrong campaign, right execution id -> 404, not a cross-campaign leak.
    other_db, other_client, other_campaign, other_principal = _setup_two_recipients("Nexa Detail")
    try:
        campaigns.get_campaign_execution(
            other_campaign.Id, execution_id, scope=(other_principal, other_client.Id), db=other_db
        )
        assert False, "expected a 404"
    except HTTPException as exc:
        assert exc.status_code == 404


def test_attempt_detail_carries_email_lead_score_and_call_outcome():
    """The whole reason the attempt endpoint joins Lead and LeadCall: an
    operator should see name, phone, email, what the AI determined about the
    lead, AND what happened on the call in one place — the same "input +
    outcome" shape the old VoiceAI batch CSV used — not stitched together
    from three different endpoints."""
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Enriched")
    db.add(client)
    db.flush()
    contact_list = models.LeadContactList(ClientId=client.Id, Name="l", SourceType="leads", Status="ready")
    db.add(contact_list)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C1", DisplayName="Rani Shah",
                                   PhoneEnc=encrypt_pii("+919333333333"))
    db.add(customer)
    db.flush()
    db.add(models.LeadContactListItem(
        ClientId=client.Id, ListId=contact_list.Id, RowNumber=1, Name="Rani Shah",
        CustomerId=customer.Id, PhoneEnc=encrypt_pii("+919333333333"),
        EmailEnc=encrypt_pii("rani.shah@example.com"), PhoneHash="r1", IsValid=True,
    ))
    campaign = models.LeadCampaign(ClientId=client.Id, Name="calls", Kind="call", Channel="voice",
                                   AudienceType="list", ListId=contact_list.Id, ScriptId="s1",
                                   Purpose="transactional", Concurrency=5, RatePerMinute=100000)
    db.add(campaign)
    db.commit()
    principal = _principal(client.Id)

    from LeadAI.services import call_bridge

    def _fake_place_call(db, client_id, company_name, conversation, initiated_by, mode="ai_voice",
                         script_id=None, override_number=None):
        call = models.LeadCall(ClientId=client_id, ConversationId=conversation.Id, Status="completed",
                               CallSid="CA123", DurationSec=97)
        db.add(call)
        db.flush()
        return call

    call_bridge.start_call_for_conversation = _fake_place_call

    cr.build_audience(db, campaign)
    cr.run_campaign_job(db, {"campaign_id": campaign.Id})

    recipient = db.query(models.LeadCampaignRecipient).filter(
        models.LeadCampaignRecipient.CampaignId == campaign.Id
    ).one()
    assert recipient.EmailMasked == mask_email("rani.shah@example.com")

    # _place_call already created a Lead row for the fresh conversation — set
    # its score/status/data points the way the real scoring pipeline would
    # once the AI has actually talked to them.
    lead = db.query(models.Lead).filter(models.Lead.ConversationId == recipient.ConversationId).one()
    lead.Score = 88
    lead.Status = "hot"
    lead.Product = "Home Loan"
    lead.DataPointsJson = {"budget": 4500000, "preferred_city": "Pune"}
    db.commit()

    execution_id = campaigns.list_campaign_executions(
        campaign.Id, page=1, page_size=50, scope=(principal, client.Id), db=db
    ).items[0].id
    attempts = campaigns.list_campaign_execution_attempts(
        campaign.Id, execution_id, page=1, page_size=50, status_filter=None,
        scope=(principal, client.Id), db=db,
    )
    attempt = attempts.items[0]
    assert attempt.name == "Rani Shah"
    assert attempt.email_masked == mask_email("rani.shah@example.com")
    assert attempt.lead_score == 88
    assert attempt.lead_status == "hot"
    assert attempt.product == "Home Loan"
    assert attempt.data_points == {"budget": 4500000, "preferred_city": "Pune"}
    assert attempt.call_status == "completed"
    assert attempt.call_duration_sec == 97

    # The live /recipients view (used while a campaign is still running)
    # carries the same product the historical attempts view does.
    recipients_page = campaigns.list_recipients(
        campaign.Id, page=1, page_size=50, status_filter=None,
        scope=(principal, client.Id), db=db,
    )
    assert recipients_page.items[0].product == "Home Loan"


def test_list_campaigns_can_be_filtered_into_broadcast_vs_lead_campaign():
    """A frontend wanting separate "broadcaster" and "batches" lists should
    be able to get them from the one /campaigns endpoint via campaign_type,
    rather than needing two different URLs."""
    db, client, _campaign, principal = _setup_two_recipients("Kestrel Split")
    db.add(models.LeadCampaign(ClientId=client.Id, Name="manual one", Kind="message", Channel="whatsapp",
                               AudienceType="customers", CreatedVia="manual"))
    db.add(models.LeadCampaign(ClientId=client.Id, Name="imported one", Kind="call", Channel="voice",
                               AudienceType="list", CreatedVia="import"))
    db.commit()

    broadcasts = campaigns.list_campaigns(
        page=1, page_size=50, status_filter=None, kind=None, campaign_type="broadcast",
        scope=(principal, client.Id), db=db,
    )
    lead_campaigns = campaigns.list_campaigns(
        page=1, page_size=50, status_filter=None, kind=None, campaign_type="lead_campaign",
        scope=(principal, client.Id), db=db,
    )
    broadcast_names = {c.name for c in broadcasts.items}
    lead_campaign_names = {c.name for c in lead_campaigns.items}
    assert broadcast_names == {"c", "manual one"}
    assert lead_campaign_names == {"imported one"}


def test_a_cancelled_campaign_still_cannot_be_started():
    db, client, campaign, principal = _setup_two_recipients("Nexa Restart")
    campaign.Status = "cancelled"
    db.commit()
    try:
        campaigns.start_campaign(
            campaign.Id, request=None, restart_mode="all", scope=(principal, client.Id), db=db
        )
        assert False, "expected a 409"
    except HTTPException as exc:
        assert exc.status_code == 409


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
