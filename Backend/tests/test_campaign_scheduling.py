"""ScheduledAt used to be a field you could set and see echoed back in
CampaignOut, but nothing ever read it — no job anywhere scanned for a
due campaign and started it. An operator picking "run this at 9am tomorrow"
got silent no-op: the campaign just sat in "draft" forever.

Fix: creating/editing a campaign with scheduled_at now (1) moves it to
Status="scheduled" and (2) enqueues a "campaign.scheduled_start" job via the
EXISTING durable job queue's own run_at delay (LeadAI/services/jobs.py already
supports this — claim() only picks up a job once RunAt <= now). When that job
fires, campaign_runner.fire_scheduled_campaign() does exactly what a human
clicking Start does: build the audience fresh, open an execution, and hand off
to the normal "campaign.run" machinery.

Run: python tests/test_campaign_scheduling.py
"""
import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.rbac import ROLE_PERMISSIONS, Principal  # noqa: E402
from LeadAI.routers import campaigns  # noqa: E402
from LeadAI.schemas_ext import CampaignCreate, CampaignUpdate  # noqa: E402
from LeadAI.security import encrypt_pii  # noqa: E402
from LeadAI.services import billing, campaign_runner as cr, jobs  # noqa: E402
from LeadAI.services import channels  # noqa: E402

billing.check_channel_access = lambda db, client_id, channel: (True, "")
channels.send_text = lambda account, channel, to, text: "msg-1"

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def _principal(client_id):
    return Principal(email="admin@kestrel.test", role="company_admin", client_id=client_id,
                     permissions=set(ROLE_PERMISSIONS["company_admin"]))


def _company_with_one_contact(name):
    db = SessionLocalAdmin()
    client = Client(Name=name)
    db.add(client)
    db.flush()
    contact_list = models.LeadContactList(ClientId=client.Id, Name="l", SourceType="leads", Status="ready")
    db.add(contact_list)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C1", DisplayName="Lead One",
                                   PhoneEnc=encrypt_pii("+919111111111"))
    db.add(customer)
    db.flush()
    db.add(models.LeadContactListItem(ClientId=client.Id, ListId=contact_list.Id, RowNumber=1,
                                      Name="Lead One", CustomerId=customer.Id,
                                      PhoneEnc=encrypt_pii("+919111111111"), PhoneHash="l1", IsValid=True))
    db.commit()
    return db, client, contact_list, _principal(client.Id)


def _scheduled_jobs(db, campaign_id):
    # Filtering on the JSON payload in Python rather than via a JSON path
    # operator — those aren't portable across the SQLite versions this test
    # suite runs under.
    all_jobs = db.query(models.LeadJob).filter(models.LeadJob.Kind == "campaign.scheduled_start").all()
    return [j for j in all_jobs if (j.PayloadJson or {}).get("campaign_id") == campaign_id]


def test_creating_with_scheduled_at_marks_the_campaign_scheduled_and_queues_the_fire_job():
    db, client, contact_list, principal = _company_with_one_contact("Kestrel Sched")
    from datetime import datetime, timedelta
    when = datetime.utcnow() + timedelta(hours=2)

    out = campaigns.create_campaign(
        CampaignCreate(name="9am blast", kind="message", channel="sms", purpose="transactional",
                       audience_type="list", list_id=contact_list.Id, message_body="hi",
                       scheduled_at=when, timezone="UTC"),
        request=None, scope=(principal, client.Id), db=db,
    )
    assert out.status == "scheduled"

    pending = _scheduled_jobs(db, out.id)
    assert len(pending) == 1
    assert pending[0].Status == "queued"
    assert pending[0].RunAt == when


def test_firing_the_job_builds_audience_and_hands_off_to_campaign_run():
    db, client, contact_list, principal = _company_with_one_contact("Nexa Sched")
    campaign = models.LeadCampaign(ClientId=client.Id, Name="c", Kind="message", Channel="sms",
                                   AudienceType="list", ListId=contact_list.Id, MessageBody="hi",
                                   Purpose="transactional", Status="scheduled")
    db.add(campaign)
    db.commit()

    result = cr.fire_scheduled_campaign(db, {"campaign_id": campaign.Id})
    assert result == {"started": 1}
    db.refresh(campaign)
    assert campaign.Status == "queued"
    assert campaign.TotalCount == 1

    run_jobs = [j for j in db.query(models.LeadJob).filter(models.LeadJob.Kind == "campaign.run").all()
                if (j.PayloadJson or {}).get("campaign_id") == campaign.Id]
    assert len(run_jobs) == 1


def test_a_manual_start_before_the_scheduled_time_prevents_the_later_job_from_also_firing():
    """The real failure mode this guards against: an operator gets impatient
    and clicks Start at 8am for a 9am-scheduled campaign. At 9am the original
    job still fires (nothing cancels it in time in a slow test) — it must see
    the campaign is no longer "scheduled" and do nothing, not start a second,
    duplicate execution."""
    db, client, contact_list, principal = _company_with_one_contact("Kestrel Double")
    campaign = models.LeadCampaign(ClientId=client.Id, Name="c", Kind="message", Channel="sms",
                                   AudienceType="list", ListId=contact_list.Id, MessageBody="hi",
                                   Purpose="transactional", Status="scheduled")
    db.add(campaign)
    db.commit()

    campaigns.start_campaign(campaign.Id, request=None, restart_mode="all",
                             scope=(principal, client.Id), db=db)
    db.refresh(campaign)
    assert campaign.Status == "queued"

    result = cr.fire_scheduled_campaign(db, {"campaign_id": campaign.Id})
    assert result == {"stopped": "queued"}

    executions = (
        db.query(models.LeadCampaignExecution)
        .filter(models.LeadCampaignExecution.CampaignId == campaign.Id)
        .all()
    )
    assert len(executions) == 1  # not two


def test_editing_the_scheduled_time_cancels_the_old_job_and_queues_a_new_one():
    db, client, contact_list, principal = _company_with_one_contact("Nexa Resched")
    from datetime import datetime, timedelta
    first = datetime.utcnow() + timedelta(hours=2)
    second = datetime.utcnow() + timedelta(hours=5)

    out = campaigns.create_campaign(
        CampaignCreate(name="c", kind="message", channel="sms", purpose="transactional",
                       audience_type="list", list_id=contact_list.Id, message_body="hi",
                       scheduled_at=first, timezone="UTC"),
        request=None, scope=(principal, client.Id), db=db,
    )
    campaigns.update_campaign(
        out.id, CampaignUpdate(scheduled_at=second),
        request=None, scope=(principal, client.Id), db=db,
    )

    pending = [j for j in _scheduled_jobs(db, out.id) if j.Status == "queued"]
    assert len(pending) == 1
    assert pending[0].RunAt == second


def test_a_job_that_finds_zero_recipients_reverts_to_draft_instead_of_silently_stalling():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Empty")
    db.add(client)
    db.flush()
    empty_list = models.LeadContactList(ClientId=client.Id, Name="empty", SourceType="leads", Status="ready")
    db.add(empty_list)
    db.flush()
    campaign = models.LeadCampaign(ClientId=client.Id, Name="c", Kind="message", Channel="sms",
                                   AudienceType="list", ListId=empty_list.Id, MessageBody="hi",
                                   Purpose="transactional", Status="scheduled")
    db.add(campaign)
    db.commit()

    result = cr.fire_scheduled_campaign(db, {"campaign_id": campaign.Id})
    assert result == {"stopped": "no_recipients"}
    db.refresh(campaign)
    assert campaign.Status == "draft"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
