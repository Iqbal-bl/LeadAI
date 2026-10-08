"""Campaign runs happened, and an operator asking "what happened, in detail" had
nowhere to look: the activity log already recorded created/built/started/paused/
resumed/cancelled/completed, but nothing was logged PER BATCH (so a paused-and-
resumed campaign, or one spanning many job runs, showed only the final tally) and
quiet-hours deferrals were never logged at all (only overwritten into a StatusMessage
that the next batch run erases). There was also no campaign-scoped endpoint to read
any of it back — a caller had to already know to hit GET /activity with the right
entity_type/entity_id.

Run: python tests/test_campaign_history.py
"""
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


def _setup_whatsapp_campaign(client_name, purpose="transactional"):
    db = SessionLocalAdmin()
    client = Client(Name=client_name)
    db.add(client)
    db.flush()
    account = models.LeadChannelAccount(ClientId=client.Id, Channel="whatsapp", Name="wa",
                                        ExternalId=f"wa-{client_name}")
    db.add(account)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C1", DisplayName="Contact",
                                   PhoneEnc=encrypt_pii("+919111111111"))
    db.add(customer)
    db.flush()
    contact_list = models.LeadContactList(ClientId=client.Id, Name="l", SourceType="leads", Status="ready")
    db.add(contact_list)
    db.flush()
    db.add(models.LeadContactListItem(ClientId=client.Id, ListId=contact_list.Id, RowNumber=1,
                                      Name="Contact", CustomerId=customer.Id,
                                      PhoneEnc=encrypt_pii("+919111111111"), PhoneHash="z", IsValid=True))
    campaign = models.LeadCampaign(ClientId=client.Id, Name="c", Kind="message", Channel="whatsapp",
                                   ChannelAccountId=account.Id, AudienceType="list",
                                   ListId=contact_list.Id, MessageBody="hi", Purpose=purpose)
    db.add(campaign)
    db.commit()

    principal = Principal(email=f"admin@{client_name}.test", role="company_admin", client_id=client.Id,
                          permissions=set(ROLE_PERMISSIONS["company_admin"]))
    return db, client, campaign, principal


def test_a_completed_run_is_recorded_batch_by_batch():
    db, client, campaign, principal = _setup_whatsapp_campaign("Kestrel History")
    channels.send_text = lambda account, channel, to, text: "msg-1"

    cr.build_audience(db, campaign)
    cr.run_campaign_job(db, {"campaign_id": campaign.Id})

    out = campaigns.list_campaign_history(
        campaign.Id, page=1, page_size=50, scope=(principal, client.Id), db=db
    )
    actions = [item.action for item in out.items]  # newest first
    assert "campaign.completed" in actions
    assert "campaign.batch_processed" in actions
    assert "campaign.audience_built" in actions
    assert actions.index("campaign.completed") < actions.index("campaign.batch_processed") < actions.index(
        "campaign.audience_built"
    )

    batch_entry = next(i for i in out.items if i.action == "campaign.batch_processed")
    execution_id = batch_entry.meta.pop("execution_id", None)
    assert execution_id  # ties this log line back to the specific run that produced it
    assert batch_entry.meta == {"sent": 1, "failed": 0, "skipped": 0, "remaining": 0}

    completed_entry = next(i for i in out.items if i.action == "campaign.completed")
    assert completed_entry.meta.get("execution_id") == execution_id
    assert campaign.Name in completed_entry.message


def test_quiet_hours_deferral_is_recorded_and_nothing_is_sent():
    db, client, campaign, principal = _setup_whatsapp_campaign("Nexa History", purpose="promotional")
    sent = []
    channels.send_text = lambda account, channel, to, text: sent.append(to) or "msg-1"
    # 2 AM local — outside the default 09:00-21:00 quiet-hours window, deterministically.
    cr._local_now = lambda tz_name: __import__("datetime").datetime(2026, 9, 29, 2, 0, 0)

    cr.build_audience(db, campaign)
    result = cr.run_campaign_job(db, {"campaign_id": campaign.Id})

    assert "deferred_until" in result
    assert sent == []  # nothing was actually sent

    out = campaigns.list_campaign_history(
        campaign.Id, page=1, page_size=50, scope=(principal, client.Id), db=db
    )
    actions = [item.action for item in out.items]
    assert "campaign.deferred" in actions
    assert "campaign.batch_processed" not in actions  # no batch ran


def test_history_does_not_leak_across_campaigns_or_companies():
    db1, client1, campaign1, principal1 = _setup_whatsapp_campaign("Kestrel Isolation")
    channels.send_text = lambda account, channel, to, text: "msg-1"
    cr.build_audience(db1, campaign1)
    cr.run_campaign_job(db1, {"campaign_id": campaign1.Id})

    db2, client2, campaign2, principal2 = _setup_whatsapp_campaign("Nexa Isolation")
    cr.build_audience(db2, campaign2)
    cr.run_campaign_job(db2, {"campaign_id": campaign2.Id})

    out = campaigns.list_campaign_history(
        campaign1.Id, page=1, page_size=50, scope=(principal1, client1.Id), db=db1
    )
    # campaign1's own run produces exactly 3 entries (built, batch_processed,
    # completed). If campaign2's identically-shaped run (different campaign,
    # different client) leaked in too, this would be 6.
    assert len(out.items) == out.total_items == 3


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
