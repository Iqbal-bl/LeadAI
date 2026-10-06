"""Before this, a frontend watching a scheduled/running campaign had to poll
GET /campaigns/{id} and guess when to check — no push existed at all for
campaigns (unlike leads/messages, which already have leadai_inbox/
leadai_conversation websocket channels). This adds the matching
leadai_campaign channel: campaign_runner.broadcast_campaign() fires a
best-effort, fire-and-forget push every time a campaign's status or counts
actually change (scheduled fire, batch progress, pause/resume/cancel,
completion) — see /ws/leadai/campaign/{id} in integration.py.

Run: python tests/test_campaign_websocket.py
"""
import asyncio

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from core import websocket_manager  # noqa: E402
from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.services import billing, campaign_runner as cr, channels  # noqa: E402
import test_campaign_scheduling as sched_helpers  # noqa: E402  (reuses its fixture helper)

billing.check_channel_access = lambda db, client_id, channel: (True, "")
channels.send_text = lambda account, channel, to, text: "msg-1"

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def _campaign(name):
    # Keeping `db` alive for as long as the caller holds `campaign` matters:
    # the session default-expires every attribute on commit, and a lazy
    # reload needs a still-attached session or it's a DetachedInstanceError.
    db = SessionLocalAdmin()
    client = Client(Name=name)
    db.add(client)
    db.flush()
    campaign = models.LeadCampaign(ClientId=client.Id, Name="c", Kind="message", Channel="sms",
                                   AudienceType="list", Purpose="transactional", Status="running",
                                   TotalCount=10, SentCount=3, FailedCount=1)
    db.add(campaign)
    db.commit()
    return db, campaign


async def _with_live_loop(fn):
    """Simulates the production setup (set_event_loop called at app startup)
    for the duration of one coroutine, restoring whatever was there before."""
    saved = websocket_manager._loop
    websocket_manager.set_event_loop(asyncio.get_event_loop())
    try:
        await fn()
    finally:
        websocket_manager._loop = saved


def test_broadcast_campaign_pushes_a_live_snapshot_to_subscribers():
    _db, campaign = _campaign("Kestrel WS")
    received = []

    async def _fake_broadcast(campaign_id, data):
        received.append((campaign_id, data))

    async def _run():
        saved_method = websocket_manager.manager.broadcast_to_leadai_campaign
        websocket_manager.manager.broadcast_to_leadai_campaign = _fake_broadcast
        try:
            cr.broadcast_campaign(campaign)
            await asyncio.sleep(0.05)   # let _fire_and_forget's scheduled coroutine actually run
        finally:
            websocket_manager.manager.broadcast_to_leadai_campaign = saved_method

    asyncio.run(_with_live_loop(_run))

    assert len(received) == 1
    campaign_id, data = received[0]
    assert campaign_id == campaign.Id
    assert data["type"] == "campaign_status"
    assert data["status"] == "running"
    assert data["total_count"] == 10
    assert data["sent_count"] == 3
    assert data["failed_count"] == 1


def test_broadcast_campaign_lists_in_flight_calls_for_call_campaigns_when_db_is_given():
    """Parity with the old VoiceAI batch system's running_calls_with_ids —
    a frontend watching a call campaign needs to know WHICH calls are live
    right now, not just a count. Only populated when a db session is passed
    (so a caller without one handy isn't forced into a query just to
    broadcast), and only for Kind == "call"."""
    db, client, contact_list, _principal = sched_helpers._company_with_one_contact("Kestrel WS Calls")
    campaign = models.LeadCampaign(ClientId=client.Id, Name="c", Kind="call", Channel="voice",
                                   AudienceType="list", ListId=contact_list.Id, Purpose="transactional",
                                   Status="running", TotalCount=1)
    db.add(campaign)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C_live", DisplayName="Lead One")
    db.add(customer)
    db.flush()
    conversation = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="voice")
    db.add(conversation)
    db.flush()
    call = models.LeadCall(ClientId=client.Id, ConversationId=conversation.Id, Status="in-progress",
                           CallSid="CA_LIVE_123")
    db.add(call)
    db.flush()
    recipient = models.LeadCampaignRecipient(ClientId=client.Id, CampaignId=campaign.Id, Name="Lead One",
                                             Status="sending", CallId=call.Id, DedupeKey="dk-live-1")
    db.add(recipient)
    db.commit()

    received = []

    async def _fake_broadcast(campaign_id, data):
        received.append(data)

    async def _run():
        saved_method = websocket_manager.manager.broadcast_to_leadai_campaign
        websocket_manager.manager.broadcast_to_leadai_campaign = _fake_broadcast
        try:
            cr.broadcast_campaign(campaign, db=db)
            await asyncio.sleep(0.05)
        finally:
            websocket_manager.manager.broadcast_to_leadai_campaign = saved_method

    asyncio.run(_with_live_loop(_run))

    assert len(received) == 1
    assert received[0]["running_calls"] == [{"callSid": "CA_LIVE_123", "recipientId": recipient.Id}]


def test_broadcast_campaign_never_raises_with_no_event_loop_captured():
    """Production safety net: if the event loop was never captured (or the
    app is mid-shutdown), a dropped broadcast must never take the campaign
    job down with it."""
    _db, campaign = _campaign("Nexa WS")
    saved = websocket_manager._loop
    websocket_manager._loop = None
    try:
        cr.broadcast_campaign(campaign)   # must not raise
    finally:
        websocket_manager._loop = saved


def test_a_full_scheduled_run_broadcasts_at_every_real_state_change():
    """Not just broadcast_campaign() in isolation — proves the actual wiring
    at the call sites fires: fire_scheduled_campaign (-> queued), the running
    transition, the per-batch progress update, and _finish (-> completed)."""
    db, client, contact_list, principal = sched_helpers._company_with_one_contact("Kestrel WS Run")
    campaign = models.LeadCampaign(ClientId=client.Id, Name="c", Kind="message", Channel="sms",
                                   AudienceType="list", ListId=contact_list.Id, MessageBody="hi",
                                   Purpose="transactional", Status="scheduled")
    db.add(campaign)
    db.commit()

    received = []

    async def _fake_broadcast(campaign_id, data):
        received.append(data["status"])

    async def _run():
        saved_method = websocket_manager.manager.broadcast_to_leadai_campaign
        websocket_manager.manager.broadcast_to_leadai_campaign = _fake_broadcast
        try:
            cr.fire_scheduled_campaign(db, {"campaign_id": campaign.Id})
            cr.run_campaign_job(db, {"campaign_id": campaign.Id})
            await asyncio.sleep(0.05)
        finally:
            websocket_manager.manager.broadcast_to_leadai_campaign = saved_method

    asyncio.run(_with_live_loop(_run))

    # queued (fire_scheduled_campaign) -> running -> running (batch progress) -> completed (_finish)
    assert received == ["queued", "running", "running", "completed"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
