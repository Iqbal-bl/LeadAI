"""`LeadCampaign.Concurrency` ("simultaneous sends/calls" per its own column
comment) was accepted on create/update and returned on every campaign read,
but campaign_runner.py never actually read it — calls were placed one at a
time with only a RatePerMinute pacing delay between them, same as a message
send. The old VoiceAI outbound/batching.py system DID enforce a real
concurrent_limit (bounding how many calls are simultaneously ringing/live),
tracked in an in-memory asyncio Set — which only works for one long-lived
worker process. The job-queue design here has to survive worker restarts and
run on any worker, so the cap is enforced from a live DB count of non-terminal
`leadai_calls` rows instead of in-memory state.

Run: python tests/test_campaign_call_concurrency.py
"""
import uuid

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.rbac import ROLE_PERMISSIONS, Principal  # noqa: E402
from LeadAI.security import encrypt_pii  # noqa: E402
from LeadAI.services import call_bridge, campaign_runner as cr  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def _principal(client_id):
    return Principal(email="admin@kestrel.test", role="company_admin", client_id=client_id,
                     permissions=set(ROLE_PERMISSIONS["company_admin"]))


def _setup_call_campaign(client_name, n=3, concurrency=1):
    db = SessionLocalAdmin()
    client = Client(Name=client_name)
    db.add(client)
    db.flush()
    contact_list = models.LeadContactList(ClientId=client.Id, Name="l", SourceType="leads", Status="ready")
    db.add(contact_list)
    db.flush()
    for i in range(1, n + 1):
        phone = f"+9191100000{i:02d}"
        customer = models.LeadCustomer(ClientId=client.Id, PublicRef=f"C{i}", DisplayName=f"Lead {i}",
                                       PhoneEnc=encrypt_pii(phone))
        db.add(customer)
        db.flush()
        db.add(models.LeadContactListItem(ClientId=client.Id, ListId=contact_list.Id, RowNumber=i,
                                          Name=f"Lead {i}", CustomerId=customer.Id,
                                          PhoneEnc=encrypt_pii(phone), PhoneHash=f"h{i}", IsValid=True))
    campaign = models.LeadCampaign(ClientId=client.Id, Name="calls", Kind="call", Channel="voice",
                                   AudienceType="list", ListId=contact_list.Id, ScriptId="script-1",
                                   Purpose="transactional", Concurrency=concurrency, RatePerMinute=100000)
    db.add(campaign)
    db.commit()
    return db, client, campaign, _principal(client.Id)


def _mock_place_call(placed, db, client_id, company_name, conversation, initiated_by,
                     mode="ai_voice", script_id=None, override_number=None):
    """A real, persisted LeadCall (needed so recipient.CallId joins to a real
    row the concurrency count can see) left in a non-terminal state, as if
    the call is still ringing/in-progress."""
    call = models.LeadCall(
        ClientId=client_id, ConversationId=conversation.Id, Status="in-progress",
        CallSid=f"CA{uuid.uuid4().hex[:10]}",
    )
    db.add(call)
    db.flush()
    placed.append(call)
    return call


def test_the_cap_stops_placing_more_calls_once_it_is_reached():
    db, client, campaign, principal = _setup_call_campaign("Kestrel Calls", n=3, concurrency=1)
    placed = []
    call_bridge.start_call_for_conversation = lambda *a, **k: _mock_place_call(placed, *a, **k)

    cr.build_audience(db, campaign)
    result = cr.run_campaign_job(db, {"campaign_id": campaign.Id})

    # Only the first call was placed — the cap (1) was hit before a second one could be.
    assert len(placed) == 1
    assert result.get("sent") == 1
    assert result.get("waiting_for_concurrency_slot") is True

    statuses = {
        r.Name: r.Status
        for r in db.query(models.LeadCampaignRecipient).filter(
            models.LeadCampaignRecipient.CampaignId == campaign.Id
        ).all()
    }
    assert statuses["Lead 1"] == "sent"
    assert statuses["Lead 2"] == "queued"
    assert statuses["Lead 3"] == "queued"


def test_a_finished_call_frees_the_slot_for_the_next_recipient():
    db, client, campaign, principal = _setup_call_campaign("Nexa Calls", n=3, concurrency=1)
    placed = []
    call_bridge.start_call_for_conversation = lambda *a, **k: _mock_place_call(placed, *a, **k)

    cr.build_audience(db, campaign)
    cr.run_campaign_job(db, {"campaign_id": campaign.Id})
    assert len(placed) == 1

    # The first call ends.
    placed[0].Status = "completed"
    db.commit()

    result = cr.run_campaign_job(db, {"campaign_id": campaign.Id})
    assert len(placed) == 2  # the freed slot let exactly one more through
    assert result.get("sent") == 1

    statuses = {
        r.Name: r.Status
        for r in db.query(models.LeadCampaignRecipient).filter(
            models.LeadCampaignRecipient.CampaignId == campaign.Id
        ).all()
    }
    assert statuses["Lead 2"] == "sent"
    assert statuses["Lead 3"] == "queued"  # cap(1) hit again — still waiting


def test_a_higher_cap_lets_more_calls_through_in_one_pass():
    db, client, campaign, principal = _setup_call_campaign("Kestrel Wide", n=3, concurrency=3)
    placed = []
    call_bridge.start_call_for_conversation = lambda *a, **k: _mock_place_call(placed, *a, **k)

    cr.build_audience(db, campaign)
    result = cr.run_campaign_job(db, {"campaign_id": campaign.Id})

    assert len(placed) == 3
    assert result.get("sent") == 3
    assert "waiting_for_concurrency_slot" not in result


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
