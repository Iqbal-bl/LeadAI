"""
Campaign engine — bulk messages and bulk AI calls.

TWO PHASES, DELIBERATELY SEPARATE
---------------------------------
    BUILD    resolve the audience into `leadai_campaign_recipients` rows.
    EXECUTE  walk those rows in throttled batches, sending one at a time.

Splitting them is what makes a campaign safe to pause, resume, restart and
audit. After BUILD the operator sees exactly who will be contacted and can
cancel; during EXECUTE every recipient row records its own status, so a crash
resumes from the first `queued` row rather than from the beginning. Nobody is
messaged twice, which is the failure mode that costs real money and real trust.

THROTTLING
----------
Two independent limits, because they protect different things:
  * Concurrency — how many sends are in flight. Protects our own worker.
  * Rate per minute — how many sends per minute. Protects the NUMBER. WhatsApp
    rates a business number on user blocks and complaints; blasting 10k
    messages in 60 seconds is the fastest way to get a number's quality rating
    downgraded and its daily limit cut.

QUIET HOURS
-----------
Promotional contact outside 09:00–21:00 local time breaches India's TRAI
regulations. When the current time is outside the window, execution reschedules
itself for the next opening rather than sending. Transactional campaigns can
opt out by setting Purpose='transactional'.

BULK CALLS
----------
Voice campaigns do NOT reimplement dialling. They reuse
`services/call_bridge.start_call_for_conversation`, which is the same path the
inbox "call this lead" button uses, which is in turn the existing Twilio/Exotel
+ Sarvam pipeline. A campaign call therefore produces a normal `leadai_calls`
row, a normal transcript and a normal recording.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from Domain.models import Client

from .. import activity
from ..activity import A
from ..config import settings
from ..models import (
    CHANNEL_EMAIL,
    CHANNEL_SMS,
    CHANNEL_VOICE,
    Lead,
    LeadAccount,
    LeadCampaign,
    LeadCampaignRecipient,
    LeadChannelAccount,
    LeadChannelIdentity,
    LeadContactListItem,
    LeadConversation,
    LeadCustomer,
    utcnow,
)
from ..security import decrypt_pii, encrypt_pii, mask_phone, phone_fingerprint
from . import audience, channels, crm, jobs

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = ("completed", "cancelled", "failed")


# =========================================================================== #
# quiet hours
# =========================================================================== #
def _local_now(tz_name: str | None) -> datetime:
    name = tz_name or settings.default_timezone
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(name))
    except Exception:  # noqa: BLE001
        # Python without tzdata: approximate IST rather than fail the campaign.
        return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def quiet_hours_check(campaign: LeadCampaign) -> tuple[bool, datetime | None]:
    """Return (may_send_now, resume_at).

    Transactional sends bypass the window — an OTP or an appointment reminder is
    not a promotion and is explicitly permitted.
    """
    if campaign.Purpose == "transactional":
        return True, None

    start = campaign.QuietHoursStart
    end = campaign.QuietHoursEnd
    if start is None or end is None:
        start, end = settings.quiet_hours_start, settings.quiet_hours_end
    if start == end:
        return True, None

    now = _local_now(campaign.TimeZone)
    hour = now.hour
    inside = start <= hour < end if start < end else (hour >= start or hour < end)
    if inside:
        return True, None

    resume = now.replace(hour=start, minute=0, second=0, microsecond=0)
    if resume <= now:
        resume += timedelta(days=1)
    return False, resume.astimezone(timezone.utc).replace(tzinfo=None)


# =========================================================================== #
# BUILD
# =========================================================================== #
def build_audience(db: Session, campaign: LeadCampaign, actor: str = "system") -> dict:
    """Materialise recipient rows. Idempotent: existing rows are left alone.

    De-duplication uses `DedupeKey` plus a unique constraint on
    (CampaignId, DedupeKey). The constraint is the real guard — application-side
    checking alone loses to two workers building the same campaign at once.
    """
    existing = {
        key
        for (key,) in db.query(LeadCampaignRecipient.DedupeKey)
        .filter(LeadCampaignRecipient.CampaignId == campaign.Id)
        .all()
    }
    added = skipped = 0

    for candidate in _iter_targets(db, campaign):
        phone = candidate.get("phone")
        email = candidate.get("email")
        if not phone and not email:
            skipped += 1
            continue

        key = phone_fingerprint(phone) or f"e:{(email or '').strip().lower()}"
        if campaign.DedupeByPhone and key in existing:
            skipped += 1
            continue
        existing.add(key)

        db.add(
            LeadCampaignRecipient(
                ClientId=campaign.ClientId,
                CampaignId=campaign.Id,
                ListItemId=candidate.get("list_item_id"),
                CustomerId=candidate.get("customer_id"),
                AccountId=candidate.get("account_id"),
                ConversationId=candidate.get("conversation_id"),
                Name=candidate.get("name"),
                PhoneEnc=encrypt_pii(phone),
                PhoneMasked=mask_phone(phone),
                DedupeKey=key[:80],
                FieldsJson=candidate.get("fields"),
                Status="queued",
                CreatedBy=actor,
            )
        )
        added += 1
        if added % 500 == 0:
            db.flush()

    campaign.TotalCount = (campaign.TotalCount or 0) + added
    campaign.QueuedCount = (
        db.query(func.count(LeadCampaignRecipient.Id))
        .filter(
            LeadCampaignRecipient.CampaignId == campaign.Id,
            LeadCampaignRecipient.Status == "queued",
        )
        .scalar()
        or 0
    )
    campaign.SkippedCount = (campaign.SkippedCount or 0) + skipped
    activity.log(
        db,
        action=A.CAMPAIGN_BUILT,
        client_id=campaign.ClientId,
        actor_email=actor,
        entity_type="campaign",
        entity_id=campaign.Id,
        message=f"Audience built: {added} recipients ({skipped} skipped)",
        meta={"added": added, "skipped": skipped, "audience": campaign.AudienceType},
    )
    db.commit()
    return {"added": added, "skipped": skipped, "total": campaign.TotalCount}


def _iter_targets(db: Session, campaign: LeadCampaign):
    """Yield candidate dicts from whichever audience the campaign names."""
    if campaign.AudienceType == "list" and campaign.ListId:
        rows = (
            db.query(LeadContactListItem)
            .filter(
                LeadContactListItem.ListId == campaign.ListId,
                LeadContactListItem.IsValid == True,   # noqa: E712
                LeadContactListItem.IsDeleted == False,  # noqa: E712
            )
            .yield_per(500)
        )
        for item in rows:
            yield {
                "list_item_id": item.Id,
                "customer_id": item.CustomerId,
                "account_id": item.AccountId,
                "name": item.Name,
                "phone": decrypt_pii(item.PhoneEnc),
                "email": decrypt_pii(item.EmailEnc),
                "fields": item.FieldsJson,
            }
        return

    if campaign.AudienceType == "customers":
        query = db.query(LeadAccount).filter(
            LeadAccount.ClientId == campaign.ClientId,
            LeadAccount.IsDeleted == False,  # noqa: E712
        )
        filters = campaign.AudienceFilterJson or {}
        if filters.get("stage"):
            query = query.filter(LeadAccount.Stage == filters["stage"])
        if filters.get("owner_email"):
            query = query.filter(LeadAccount.OwnerEmail == filters["owner_email"])
        if filters.get("tag"):
            query = query.filter(LeadAccount.Tags.like(f"%{filters['tag']}%"))
        if filters.get("account_ids"):
            query = query.filter(LeadAccount.Id.in_(filters["account_ids"]))
        for account in query.yield_per(500):
            yield {
                "account_id": account.Id,
                "customer_id": account.CustomerId,
                "name": account.DisplayName,
                "phone": decrypt_pii(account.PhoneEnc),
                "email": decrypt_pii(account.EmailEnc),
                "fields": {
                    "name": account.DisplayName,
                    "company": account.CompanyName,
                    "product": account.Product,
                    **(account.FieldsJson or {}),
                },
            }
        return

    # AudienceType == "leads": every conversation matching a filter.
    filters = campaign.AudienceFilterJson or {}
    query = (
        db.query(Lead, LeadConversation)
        .join(LeadConversation, LeadConversation.Id == Lead.ConversationId)
        .filter(
            Lead.ClientId == campaign.ClientId,
            Lead.IsDeleted == False,  # noqa: E712
        )
    )
    if filters.get("status"):
        query = query.filter(Lead.Status.in_(list(filters["status"])))
    if filters.get("min_score") is not None:
        query = query.filter(Lead.Score >= int(filters["min_score"]))
    if filters.get("above_threshold"):
        query = query.filter(Lead.IsAboveThreshold == True)  # noqa: E712
    if filters.get("channel"):
        query = query.filter(LeadConversation.Channel == filters["channel"])
    if filters.get("created_after"):
        query = query.filter(Lead.CreatedAt >= filters["created_after"])

    for lead, conversation in query.yield_per(500):
        customer = db.get(LeadCustomer, conversation.CustomerId)
        if customer is None:
            continue
        yield {
            "customer_id": customer.Id,
            "conversation_id": conversation.Id,
            "name": customer.DisplayName or customer.PublicRef,
            "phone": decrypt_pii(customer.PhoneEnc),
            "email": decrypt_pii(customer.EmailEnc),
            "fields": {
                "name": customer.DisplayName or "there",
                "product": lead.Product,
                "interest": lead.Interest,
            },
        }


# =========================================================================== #
# EXECUTE
# =========================================================================== #
@jobs.register("campaign.run")
def run_campaign_job(db: Session, payload: dict) -> dict:
    """Job handler. Processes one BATCH, then re-queues itself if work remains.

    Re-queueing rather than looping until done keeps any single job short, which
    means a deploy interrupts at most `campaign_batch_size` sends and the stale-
    job reclaimer has a small blast radius.
    """
    campaign_id = payload.get("campaign_id")
    campaign = db.get(LeadCampaign, campaign_id)
    if campaign is None or campaign.IsDeleted:
        return {"stopped": "campaign missing"}
    if campaign.Status in TERMINAL_STATUSES or campaign.Status == "paused":
        return {"stopped": campaign.Status}

    may_send, resume_at = quiet_hours_check(campaign)
    if not may_send:
        campaign.StatusMessage = f"Waiting for quiet hours to end (resumes {resume_at:%H:%M})"
        jobs.enqueue(
            db, "campaign.run", {"campaign_id": campaign.Id},
            client_id=campaign.ClientId, run_at=resume_at,
        )
        db.commit()
        return {"deferred_until": str(resume_at)}

    if campaign.Status != "running":
        campaign.Status = "running"
        campaign.StartedAt = campaign.StartedAt or utcnow()
        db.commit()

    client = db.get(Client, campaign.ClientId)
    batch = (
        db.query(LeadCampaignRecipient)
        .filter(
            LeadCampaignRecipient.CampaignId == campaign.Id,
            LeadCampaignRecipient.Status == "queued",
            LeadCampaignRecipient.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadCampaignRecipient.CreatedAt.asc())
        .limit(settings.campaign_batch_size)
        .all()
    )

    if not batch:
        return _finish(db, campaign)

    # Rate limiting: a fixed inter-send delay is simpler and gentler on the
    # carrier than a token bucket that fires in bursts.
    rate = max(1, campaign.RatePerMinute or settings.campaign_default_rate_per_minute)
    delay = 60.0 / rate

    sent = failed = skipped = 0
    for recipient in batch:
        fresh = db.get(LeadCampaign, campaign.Id)
        if fresh is None or fresh.Status in TERMINAL_STATUSES or fresh.Status == "paused":
            db.commit()
            return {"stopped": fresh.Status if fresh else "missing", "sent": sent}

        outcome = _send_one(db, campaign, recipient, client)
        if outcome == "sent":
            sent += 1
        elif outcome == "skipped":
            skipped += 1
        else:
            failed += 1
        db.commit()
        if delay > 0.01:
            time.sleep(delay)

    _refresh_counters(db, campaign)
    db.commit()

    remaining = (
        db.query(func.count(LeadCampaignRecipient.Id))
        .filter(
            LeadCampaignRecipient.CampaignId == campaign.Id,
            LeadCampaignRecipient.Status == "queued",
        )
        .scalar()
        or 0
    )
    if remaining:
        jobs.enqueue(
            db, "campaign.run", {"campaign_id": campaign.Id}, client_id=campaign.ClientId
        )
        db.commit()
        return {"sent": sent, "failed": failed, "skipped": skipped, "remaining": remaining}

    return _finish(db, campaign, {"sent": sent, "failed": failed, "skipped": skipped})


def _send_one(
    db: Session,
    campaign: LeadCampaign,
    recipient: LeadCampaignRecipient,
    client: Client | None,
) -> str:
    """Deliver to one recipient. Returns 'sent' | 'failed' | 'skipped'."""
    recipient.Status = "sending"
    recipient.Attempts = (recipient.Attempts or 0) + 1
    phone = decrypt_pii(recipient.PhoneEnc)

    # ---- consent -------------------------------------------------------
    if campaign.RespectOptOut:
        account = None
        if recipient.AccountId:
            account = db.get(LeadAccount, recipient.AccountId)
        elif phone:
            account = crm.find_account_by_phone(db, campaign.ClientId, phone)
        if account is not None:
            allowed, reason = crm.can_contact(account, campaign.Channel)
            if not allowed:
                recipient.Status = "opted_out"
                recipient.FailureReason = reason
                return "skipped"
        if _identity_opted_out(db, campaign, phone):
            recipient.Status = "opted_out"
            recipient.FailureReason = "Contact previously replied STOP"
            return "skipped"

    context = {
        "name": recipient.Name or "there",
        "first_name": (recipient.Name or "there").split(" ")[0],
        "company": client.Name if client else "",
        **(recipient.FieldsJson or {}),
    }

    try:
        if campaign.Kind == "call":
            return _place_call(db, campaign, recipient, client, phone, context)
        return _send_message(db, campaign, recipient, phone, context)
    except channels.ChannelError as exc:
        recipient.Status = "failed"
        recipient.FailureReason = str(exc)[:400]
        return "failed"
    except Exception as exc:  # noqa: BLE001
        logger.exception("[LeadAI campaign] unexpected send failure")
        recipient.Status = "failed"
        recipient.FailureReason = f"{exc.__class__.__name__}: {exc}"[:400]
        return "failed"


def _identity_opted_out(db: Session, campaign: LeadCampaign, phone: str | None) -> bool:
    """Honour a STOP sent on the social channel itself, even with no CRM row."""
    if not phone or not campaign.ChannelAccountId:
        return False
    row = (
        db.query(LeadChannelIdentity)
        .filter(
            LeadChannelIdentity.ChannelAccountId == campaign.ChannelAccountId,
            LeadChannelIdentity.ExternalUserId == phone.lstrip("+"),
            LeadChannelIdentity.OptedOut == True,  # noqa: E712
        )
        .first()
    )
    return row is not None


def _send_message(
    db: Session,
    campaign: LeadCampaign,
    recipient: LeadCampaignRecipient,
    phone: str | None,
    context: dict,
) -> str:
    account = (
        db.get(LeadChannelAccount, campaign.ChannelAccountId)
        if campaign.ChannelAccountId
        else None
    )
    body = audience.render_template(campaign.MessageBody or "", context)
    recipient.RenderedBody = body[:2000]

    if campaign.Channel == CHANNEL_EMAIL:
        email = decrypt_pii(recipient.PhoneEnc) if not phone else None
        target = email or (recipient.FieldsJson or {}).get("email")
        if not target:
            recipient.Status = "skipped"
            recipient.FailureReason = "No email address"
            return "skipped"
        message_id = channels.send_email(target, campaign.Name, body)
    elif campaign.Channel == CHANNEL_SMS:
        if not phone:
            recipient.Status = "skipped"
            recipient.FailureReason = "No phone number"
            return "skipped"
        message_id = channels.send_sms(phone, body)
    else:
        if not phone:
            recipient.Status = "skipped"
            recipient.FailureReason = "No phone number"
            return "skipped"
        target = phone.lstrip("+")
        if campaign.TemplateName:
            # Templates are required outside the 24h window and are the correct
            # default for any promotional or festive send.
            params = [
                audience.render_template(str(p), context)
                for p in (campaign.TemplateParamsJson or [])
            ] or [context.get("first_name", "there")]
            message_id = channels.send_template(
                account,
                campaign.Channel,
                target,
                campaign.TemplateName,
                campaign.TemplateLanguage or "en",
                params,
            )
        else:
            message_id = channels.send_text(account, campaign.Channel, target, body)

    recipient.Status = "sent"
    recipient.ExternalMessageId = message_id
    recipient.SentAt = utcnow()
    recipient.FailureReason = None
    if account is not None:
        account.LastOutboundAt = utcnow()
    return "sent"


def _place_call(
    db: Session,
    campaign: LeadCampaign,
    recipient: LeadCampaignRecipient,
    client: Client | None,
    phone: str | None,
    context: dict,
) -> str:
    """Bulk AI voice call — routed through the EXISTING outbound pipeline."""
    from . import call_bridge

    if not phone:
        recipient.Status = "skipped"
        recipient.FailureReason = "No phone number"
        return "skipped"

    conversation = None
    if recipient.ConversationId:
        conversation = db.get(LeadConversation, recipient.ConversationId)

    if conversation is None:
        # A campaign call to a cold list still needs a conversation to attach the
        # transcript, lead score and follow-ups to.
        customer = (
            db.get(LeadCustomer, recipient.CustomerId) if recipient.CustomerId else None
        )
        if customer is None:
            customer = LeadCustomer(
                ClientId=campaign.ClientId,
                PublicRef=recipient.Name or (recipient.PhoneMasked or "Campaign contact"),
                DisplayName=recipient.Name,
                PhoneEnc=recipient.PhoneEnc,
                PhoneHash=phone_fingerprint(phone),
                CreatedBy="campaign",
            )
            db.add(customer)
            db.flush()
            recipient.CustomerId = customer.Id

        conversation = LeadConversation(
            ClientId=campaign.ClientId,
            CustomerId=customer.Id,
            Channel=CHANNEL_VOICE,
            Status="open",
            ScriptId=campaign.ScriptId,
            Language=campaign.Language,
            CampaignId=campaign.Id,
            LastMessageAt=utcnow(),
            CreatedBy="campaign",
        )
        db.add(conversation)
        db.flush()
        db.add(
            Lead(
                ClientId=campaign.ClientId,
                ConversationId=conversation.Id,
                CreatedBy="campaign",
            )
        )
        recipient.ConversationId = conversation.Id
        campaign.LeadsCreated = (campaign.LeadsCreated or 0) + 1

    call = call_bridge.start_call_for_conversation(
        db,
        campaign.ClientId,
        client.Name if client else "",
        conversation,
        initiated_by=f"campaign:{campaign.Id}",
        mode="ai_voice",
        script_id=campaign.ScriptId,
        override_number=phone,
    )
    recipient.CallId = call.Id
    recipient.ExternalMessageId = call.CallSid

    if call.Status == "failed":
        recipient.Status = "failed"
        recipient.FailureReason = call.FailureReason
        return "failed"

    recipient.Status = "sent"
    recipient.SentAt = utcnow()
    return "sent"


def _refresh_counters(db: Session, campaign: LeadCampaign) -> None:
    """Recompute the denormalised counters from the recipient rows."""
    rows = dict(
        db.query(LeadCampaignRecipient.Status, func.count(LeadCampaignRecipient.Id))
        .filter(LeadCampaignRecipient.CampaignId == campaign.Id)
        .group_by(LeadCampaignRecipient.Status)
        .all()
    )
    campaign.QueuedCount = rows.get("queued", 0)
    campaign.SentCount = rows.get("sent", 0) + rows.get("delivered", 0) + rows.get("read", 0) + rows.get("replied", 0)
    campaign.DeliveredCount = rows.get("delivered", 0) + rows.get("read", 0) + rows.get("replied", 0)
    campaign.ReadCount = rows.get("read", 0) + rows.get("replied", 0)
    campaign.RepliedCount = rows.get("replied", 0)
    campaign.FailedCount = rows.get("failed", 0)
    campaign.SkippedCount = rows.get("skipped", 0) + rows.get("opted_out", 0)


def _finish(db: Session, campaign: LeadCampaign, extra: dict | None = None) -> dict:
    _refresh_counters(db, campaign)
    campaign.Status = "completed"
    campaign.CompletedAt = utcnow()
    campaign.StatusMessage = (
        f"Completed — {campaign.SentCount} sent, "
        f"{campaign.FailedCount} failed, {campaign.SkippedCount} skipped"
    )
    activity.log(
        db,
        action=A.CAMPAIGN_COMPLETED,
        client_id=campaign.ClientId,
        actor_email="system",
        entity_type="campaign",
        entity_id=campaign.Id,
        message=campaign.StatusMessage,
        meta={
            "sent": campaign.SentCount,
            "failed": campaign.FailedCount,
            "skipped": campaign.SkippedCount,
        },
    )
    db.commit()
    return {"status": "completed", **(extra or {})}


# =========================================================================== #
# delivery receipts (called from the webhook router)
# =========================================================================== #
def apply_status_update(db: Session, external_message_id: str, status: str, error: str | None) -> bool:
    """Fold a provider delivery receipt into the recipient row.

    Statuses only ever move FORWARD (sent -> delivered -> read -> replied);
    providers deliver receipts out of order surprisingly often, and without this
    guard a late 'sent' would overwrite a 'read'.
    """
    recipient = (
        db.query(LeadCampaignRecipient)
        .filter(LeadCampaignRecipient.ExternalMessageId == external_message_id)
        .first()
    )
    if recipient is None:
        return False

    rank = {"queued": 0, "sending": 1, "sent": 2, "delivered": 3, "read": 4, "replied": 5}
    if status == "failed":
        recipient.Status = "failed"
        recipient.FailureReason = (error or "Provider reported a failure")[:400]
    elif rank.get(status, -1) > rank.get(recipient.Status, 0):
        recipient.Status = status
        if status == "delivered":
            recipient.DeliveredAt = utcnow()
        elif status == "read":
            recipient.ReadAt = utcnow()
        elif status == "replied":
            recipient.RepliedAt = utcnow()

    campaign = db.get(LeadCampaign, recipient.CampaignId)
    if campaign is not None:
        _refresh_counters(db, campaign)
    return True


def note_reply(db: Session, conversation: LeadConversation) -> None:
    """A campaign recipient answered — the single most valuable campaign metric."""
    if not conversation.CampaignId:
        return
    recipient = (
        db.query(LeadCampaignRecipient)
        .filter(
            LeadCampaignRecipient.CampaignId == conversation.CampaignId,
            LeadCampaignRecipient.ConversationId == conversation.Id,
        )
        .first()
    )
    if recipient is None or recipient.Status == "replied":
        return
    recipient.Status = "replied"
    recipient.RepliedAt = utcnow()
    campaign = db.get(LeadCampaign, conversation.CampaignId)
    if campaign is not None:
        _refresh_counters(db, campaign)
