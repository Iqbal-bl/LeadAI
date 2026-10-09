"""
The one conversation pipeline, shared by every inbound channel.

BEFORE: `routers/chat.py::_handle_customer_turn` held the pipeline, and the web
widget was the only caller.
NOW:    the pipeline lives here, and `chat.py` is one of four callers (web
        widget, WhatsApp, Messenger, Instagram). `chat.py` keeps its public
        contract unchanged, so no existing client breaks.

This is the single most important refactor in the release. If WhatsApp had got
its own copy of "persist -> RAG -> qualify -> summarise -> handoff", the two
copies would have diverged within a month and lead scores would silently differ
by channel. One function, four adapters.

WHAT THIS FILE ADDS BEYOND THE OLD CODE
---------------------------------------
1. `resolve_social_conversation()` — turns an inbound social event into
   (customer, conversation) with de-duplication, so the same WhatsApp number
   messaging you three weeks apart resumes rather than forking.
2. `apply_threshold()` — the lead-score threshold gate. A lead below the
   company's bar is handled by the AI but is NOT pushed at the sales team; the
   moment it crosses, exactly one notification fires and, optionally, the lead
   is promoted to a CRM account.
3. `deliver()` — sends the AI's reply back out on whatever channel the customer
   used. The widget polls, so it needs no delivery; WhatsApp does.
"""
from __future__ import annotations

import logging
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy.orm import Session

from domain.models import Client

from .. import activity
from ..activity import A
from core.observability import traceable
from core.usage_tracker import bind_usage_context
from ..config import settings

from ..db import session as new_session
from ..engine import bridge as engine_bridge
from ..engine import control as engine_control
from ..engine import outbox
from ..engine.events import TurnEvent
from ..engine.locks import conversation_lock
from ..engine.trace import TurnTrace
from ..engine.trace import step as trace_step
from ..models import (
    CHANNEL_WEB,
    Lead,
    LeadChannelAccount,
    LeadChannelIdentity,
    LeadCompanySettings,
    LeadConversation,
    LeadCustomer,
    LeadMessage,
    utcnow,
)
from ..models_ext import LeadCampaign
from ..security import decrypt_pii, encrypt_pii, phone_fingerprint
from . import ai_engine, contact_capture, memory, reply_cleanup, scoring_queue, script_engine

try:
    from core.websocket_manager import _fire_and_forget, manager as ws_manager
except Exception:  # noqa: BLE001
    ws_manager = None  # type: ignore[assignment]

    def _fire_and_forget(coro):  # type: ignore[misc]
        pass

logger = logging.getLogger(__name__)


@dataclass
class TurnResult:
    """What the pipeline decided. Routers map this onto their own response
    model, so the pipeline never depends on a particular API shape."""

    reply: str
    confidence: float
    needs_human: bool
    handed_off: bool
    sources: list = field(default_factory=list)
    lead_status: str = "cold"
    lead_score: int = 0
    above_threshold: bool = False
    crossed_threshold_now: bool = False
    conversation_id: str = ""
    ai_replied: bool = True


# =========================================================================== #
# settings + thresholds
# =========================================================================== #
def company_settings(db: Session, client_id: str) -> LeadCompanySettings | None:
    return (
        db.query(LeadCompanySettings)
        .filter(
            LeadCompanySettings.ClientId == client_id,
            LeadCompanySettings.IsDeleted == False,  # noqa: E712
        )
        .one_or_none()
    )


def lead_threshold(db: Session, client_id: str) -> int:
    """The score at which a lead becomes the sales team's problem.

    Per-company value wins; otherwise the platform default. Resolved through
    the shared cache because it is read on every single customer turn and it
    changes roughly never.
    """
    from . import cache

    key = f"leadai:threshold:{client_id}"
    cached = cache.get_value(key)
    if cached is not None:
        return int(cached)
    cfg = company_settings(db, client_id)
    value = (
        cfg.LeadScoreThreshold
        if cfg is not None and cfg.LeadScoreThreshold is not None
        else settings.lead_score_threshold
    )
    cache.set_value(key, int(value), ttl=300)
    return int(value)


def invalidate_threshold_cache(client_id: str) -> None:
    """Called when settings are saved, so a change takes effect immediately."""
    from . import cache

    cache.clear_prefix(f"leadai:threshold:{client_id}")


def apply_threshold(
    db: Session,
    client: Client,
    conversation: LeadConversation,
    lead: Lead,
    request: Request | None = None,
    trace: TurnTrace | None = None,
) -> tuple[bool, bool]:
    """Evaluate the lead against the company's threshold.

    Returns (is_above, crossed_just_now).

    Two flags rather than one because the DASHBOARD wants "is it above the bar
    right now" (a filter) while NOTIFICATIONS want "did it cross on this turn"
    (an event). Storing both on the row keeps the dashboard query a single
    indexed WHERE, instead of joining settings and recomputing per lead.

    Crossing is recorded once. A lead that drops back below the bar keeps its
    ThresholdCrossedAt, so it will not re-notify on every subsequent bounce
    across the boundary — sales teams stop trusting alerts that repeat.
    """
    threshold = lead_threshold(db, client.Id)
    score = int(lead.Score or 0)
    was_above = bool(lead.IsAboveThreshold)
    is_above = score >= threshold

    lead.IsAboveThreshold = is_above
    crossed = is_above and not was_above and lead.ThresholdCrossedAt is None
    if crossed:
        lead.ThresholdCrossedAt = utcnow()
    trace_step(
        trace, "threshold",
        "crossed the bar now: notify once" if crossed
        else ("above the bar" if is_above else "below the bar"),
        score=score, threshold=threshold, was_above=was_above, is_above=is_above,
        already_notified=lead.ThresholdCrossedAt is not None and not crossed,
    )

    if not crossed:
        return is_above, False

    cfg = company_settings(db, client.Id)
    activity.log(
        db,
        action=A.LEAD_THRESHOLD_CROSSED,
        client_id=client.Id,
        actor_email="ai",
        actor_role="ai",
        entity_type="lead",
        entity_id=lead.Id,
        message=f"Lead reached the dashboard threshold ({score} >= {threshold})",
        meta={"score": score, "threshold": threshold, "status": lead.Status},
        request=request,
    )

    if cfg is None or cfg.NotifyOnThreshold:
        conversation.ThresholdNotifiedAt = utcnow()
        _broadcast_inbox(
            client.Id,
            {
                "type": "lead_threshold_crossed",
                "conversation_id": conversation.Id,
                "lead_id": lead.Id,
                "score": score,
                "threshold": threshold,
                "status": lead.Status,
                "channel": conversation.Channel,
                "client_id": client.Id,
            },
        )

    # Optional automatic promotion into the CRM.
    auto_convert = (
        cfg.AutoConvertThreshold
        if cfg is not None and cfg.AutoConvertThreshold is not None
        else settings.auto_convert_threshold
    )
    if auto_convert and score >= int(auto_convert) and not lead.ConvertedAccountId:
        try:
            from . import crm

            account = crm.convert_lead(
                db, client.Id, conversation, lead, owner_email=None, actor="auto-threshold"
            )
            logger.info("[LeadAI] auto-converted lead %s -> account %s", lead.Id, account.Id)
        except Exception as exc:  # noqa: BLE001
            # Conversion is a bonus, never a reason to fail the customer's turn.
            logger.warning("[LeadAI] auto-convert failed for lead %s: %s", lead.Id, exc)

    return True, True


def get_or_create_lead(db: Session, client_id: str, conversation_id: str, actor: str = "ai") -> Lead:
    lead = db.query(Lead).filter(Lead.ConversationId == conversation_id).one_or_none()
    if lead is None:
        lead = Lead(ClientId=client_id, ConversationId=conversation_id, CreatedBy=actor)
        db.add(lead)
        db.flush()
    return lead


def score_turn(
    db: Session,
    client: Client,
    conversation: LeadConversation,
    lead: Lead,
    history: list,
    *,
    trace: TurnTrace | None = None,
    request: Request | None = None,
) -> tuple[bool, bool]:
    """The single place a lead is scored: qualify, summarise, log the first time it reaches
    "qualified", then evaluate the dashboard threshold. Returns apply_threshold's
    (is_above, crossed_just_now). Caller commits.

    Every channel goes through here (chat inline and deferred, voice, a human-assigned
    conversation), so a scoring rule or side effect added here applies everywhere.
    """
    previous_status = lead.Status
    before_data_points = dict(lead.DataPointsJson or {})
    ai_engine.qualify(db, client.Id, lead, history, trace=trace)
    conversation.Summary, conversation.NextStep = ai_engine.summarize(
        db, client.Id, client.Name, lead, history, trace=trace
    )
    conversation.MessageCount = len(history)

    # Which data points changed, for the lead's own timeline — never the values
    # themselves (same discipline as PHONE_CAPTURED: the log records THAT a fact
    # was collected, not the fact's content, since values can be PII).
    after_data_points = lead.DataPointsJson or {}
    changed_keys = [k for k, v in after_data_points.items() if before_data_points.get(k) != v]
    if changed_keys:
        activity.log(
            db,
            action=A.DATA_POINT_COLLECTED,
            client_id=client.Id,
            actor_email="ai",
            actor_role="ai",
            entity_type="lead",
            entity_id=lead.Id,
            message=(
                f"Collected {', '.join(changed_keys)}" if len(changed_keys) <= 3
                else f"Collected {len(changed_keys)} data point(s)"
            ),
            meta={"data_points_collected": changed_keys},
            request=request,
        )

    if lead.Status == "qualified" and previous_status != "qualified":
        activity.log(
            db,
            action=A.LEAD_QUALIFIED,
            client_id=client.Id,
            actor_email="ai",
            actor_role="ai",
            entity_type="lead",
            entity_id=lead.Id,
            message=f"Lead qualified at score {lead.Score}",
            meta={
                "score": lead.Score,
                "intent": lead.Intent,
                "timeline": lead.Timeline,
                "product": lead.Product,
            },
            request=request,
        )
    return apply_threshold(db, client, conversation, lead, request, trace=trace)


def score_after_reply(
    db: Session,
    client_id: str,
    conversation_id: str,
    message_id: str | None,
    *,
    channel: str | None = None,
    history_fn: Callable[[Session, str], list] | None = None,
    note: str = "scoring after the reply was delivered",
    touch_last_message: bool = False,
) -> bool:
    """Phase 2 of a turn, after the reply has gone out. The caller owns `db` and closes it.

    qualify and summarise are a second LLM round trip that changes nothing about what the
    customer sees, so they run after delivery instead of blocking it (chat push channels and
    live calls alike).

    Appends its steps to the AI message's trace, so "why did the AI say that" and "how was the
    lead scored" still read as one record. Never raises: called fire-and-forget.
    Returns False only when scoring failed (so a queue can retry), True otherwise.
    """
    try:
        client = db.get(Client, client_id)
        conversation = db.get(LeadConversation, conversation_id)
        if client is None or conversation is None:
            return True
        lead = get_or_create_lead(db, client_id, conversation_id)
        history = (history_fn or memory.thread_history)(db, conversation_id)
        trace = TurnTrace(
            conversation_id=conversation_id, client_id=client_id, channel=channel or conversation.Channel
        )
        trace_step(trace, "post_turn", note)

        score_turn(db, client, conversation, lead, history, trace=trace)
        if touch_last_message:
            conversation.LastMessageAt = utcnow()

        message = db.get(LeadMessage, message_id) if message_id else None
        if message is not None and message.TraceJson and trace.as_json():
            merged = dict(message.TraceJson)
            merged["steps"] = list(merged.get("steps", [])) + trace.as_json()["steps"]
            message.TraceJson = merged
        db.commit()
        return True
    except Exception:  # noqa: BLE001
        logger.warning("[LeadAI flow] deferred scoring failed for conv %s", conversation_id, exc_info=True)
        db.rollback()
        return False


def run_deferred_scoring(client_id: str, conversation_id: str, message_id: str | None) -> bool:
    """score_after_reply in its own session, for a push-delivered turn
    (WhatsApp/Instagram/Messenger). Runs on a scoring_queue worker thread."""
    db = new_session()
    try:
        return score_after_reply(db, client_id, conversation_id, message_id)
    finally:
        db.close()


# =========================================================================== #
# social conversation resolution
# =========================================================================== #
def resolve_social_conversation(
    db: Session,
    account: LeadChannelAccount,
    *,
    external_user_id: str,
    profile_name: str | None = None,
    phone: str | None = None,
) -> tuple[Client, LeadConversation, LeadChannelIdentity]:
    """Find or create the customer + conversation behind an inbound social id.

    Resolution order, most-specific first:
      1. An existing identity row for (account, external id) — the normal path.
      2. A customer in this company with the same phone fingerprint — the person
         chatted on the website last week and is now on WhatsApp. Merging here
         is what stops one human becoming three leads.
      3. A brand-new customer.

    A CLOSED conversation is not reused: a new message weeks later is a new
    sales opportunity and deserves its own lead row and its own score. An open
    or assigned one is resumed so the agent keeps their context.
    """
    client = db.get(Client, account.ClientId)
    if client is None:
        raise ValueError(f"Channel account {account.Id} points at a missing company")

    identity = (
        db.query(LeadChannelIdentity)
        .filter(
            LeadChannelIdentity.ChannelAccountId == account.Id,
            LeadChannelIdentity.ExternalUserId == str(external_user_id),
            LeadChannelIdentity.IsDeleted == False,  # noqa: E712
        )
        .one_or_none()
    )

    customer: LeadCustomer | None = None
    if identity is not None:
        customer = db.get(LeadCustomer, identity.CustomerId)

    # ---- resolve the sender's real name ---------------------------------- #
    # WhatsApp webhooks carry the profile name; Messenger and Instagram do not —
    # they send only an opaque PSID / IGSID. Without this lookup an agent
    # revealing a Messenger lead sees `7891234567890123` and nothing else.
    #
    # Fetched only when we do not already have a name, so this costs one Graph
    # call per NEW social contact, not one per message. A failure is silent and
    # returns {}: a missing display name must never stop us handling the message.
    social_handle: str | None = None
    already_known = bool(profile_name) or (identity is not None and bool(identity.ProfileName))
    if not already_known:
        from . import channels as ch

        profile = ch.fetch_profile(account, account.Channel, str(external_user_id))
        if profile:
            profile_name = profile_name or profile.get("name") or profile.get("username")
            social_handle = profile.get("handle")

    if customer is None and phone:
        fingerprint = phone_fingerprint(phone)
        if fingerprint:
            customer = (
                db.query(LeadCustomer)
                .filter(
                    LeadCustomer.ClientId == client.Id,
                    LeadCustomer.PhoneHash == fingerprint,
                    LeadCustomer.IsDeleted == False,  # noqa: E712
                )
                .order_by(LeadCustomer.CreatedAt.desc())
                .first()
            )

    if customer is None:
        customer = LeadCustomer(
            ClientId=client.Id,
            PublicRef=f"Customer #{random.randint(10000, 99999)}",
            DisplayName=profile_name,
            PhoneEnc=encrypt_pii(phone),
            WhatsAppEnc=encrypt_pii(phone if account.Channel == "whatsapp" else None),
            # Store the readable handle (@username) when we resolved one, and fall
            # back to the raw id only when we could not. Previously this always
            # stored the raw IGSID, which is why revealing a contact produced a
            # number instead of a username.
            InstagramEnc=encrypt_pii(
                (social_handle or external_user_id)
                if account.Channel == "instagram"
                else None
            ),
            PhoneHash=phone_fingerprint(phone),
            CreatedBy=account.Channel,
        )
        db.add(customer)
        db.flush()
    else:
        # Backfill: an existing customer created before the profile lookup, or
        # before the person set a display name on their account.
        if profile_name and not customer.DisplayName:
            customer.DisplayName = profile_name
        if (
            social_handle
            and account.Channel == "instagram"
            and not decrypt_pii(customer.InstagramEnc or "")
        ):
            customer.InstagramEnc = encrypt_pii(social_handle)

    if identity is None:
        identity = LeadChannelIdentity(
            ClientId=client.Id,
            ChannelAccountId=account.Id,
            Channel=account.Channel,
            ExternalUserId=str(external_user_id),
            CustomerId=customer.Id,
            ProfileName=profile_name,
            CreatedBy=account.Channel,
        )
        db.add(identity)
        db.flush()
    elif profile_name and not identity.ProfileName:
        identity.ProfileName = profile_name

    conversation = None
    if identity.ConversationId:
        candidate = db.get(LeadConversation, identity.ConversationId)
        if (
            candidate is not None
            and not candidate.IsDeleted
            and candidate.Status != "closed"
        ):
            conversation = candidate

    if conversation is None:
        script = script_engine.resolve_script(db, client.Id, channel="chat")
        cfg = company_settings(db, client.Id)
        conversation = LeadConversation(
            ClientId=client.Id,
            CustomerId=customer.Id,
            Channel=account.Channel,
            Status="open",
            ScriptId=account.ScriptId or getattr(script, "Id", None),
            Language=(
                account.DefaultLanguage
                or (cfg.DefaultLanguage if cfg else None)
                or settings.default_language
            ),
            ChannelAccountId=account.Id,
            ExternalThreadId=str(external_user_id),
            LastMessageAt=utcnow(),
            CreatedBy=account.Channel,
        )
        db.add(conversation)
        db.flush()
        db.add(
            Lead(ClientId=client.Id, ConversationId=conversation.Id, CreatedBy=account.Channel)
        )
        identity.ConversationId = conversation.Id
        activity.log(
            db,
            action=A.CHAT_STARTED,
            client_id=client.Id,
            actor_email=account.Channel,
            actor_role="customer",
            entity_type="conversation",
            entity_id=conversation.Id,
            message=f"New {account.Channel} conversation ({customer.PublicRef})",
            meta={"channel": account.Channel, "account": account.Name},
        )

    identity.LastUserMessageAt = utcnow()
    account.LastInboundAt = utcnow()
    return client, conversation, identity


# =========================================================================== #
# outbound delivery
# =========================================================================== #
@dataclass
class DeliveryResult:
    """What happened when we tried to push a message out on a social channel.

    `deliver()` used to return `str | None`, which conflated four different
    outcomes into one falsy value: nothing to deliver, no route configured,
    provider rejected it, and outside the messaging window. For the AI path that
    was tolerable — the AI is not waiting for an answer. For a HUMAN agent typing
    into the inbox it is not: they need to know whether the customer received
    what they just sent.
    """

    status: str            # "sent" | "failed" | "skipped" | "not_applicable"
    message_id: str | None = None
    error: str | None = None
    detail: str | None = None   # operator-facing explanation

    @property
    def delivered(self) -> bool:
        return self.status == "sent"

    @property
    def needs_attention(self) -> bool:
        """True when a human should be told. 'not_applicable' is silent — the web
        widget polls, so there is genuinely nothing to report."""
        return self.status in ("failed", "skipped")


def deliver(
    db: Session,
    conversation: LeadConversation,
    text: str,
    *,
    message: LeadMessage | None = None,
) -> DeliveryResult:
    """Push a message out on the conversation's own channel.

    The web widget is pull-based (it polls /public/chat/messages), so there is
    nothing to deliver. Social channels are push-based and must be told.

    When `message` is supplied, the outcome is recorded on that row
    (DeliveryStatus / ExternalMessageId / DeliveryError) so the dashboard can
    show an undelivered message as undelivered rather than silently pretending
    it went out. The caller owns the commit.

    Provider failures are still caught rather than raised: the message is already
    persisted, an agent can retry, and letting a provider outage 500 a Meta
    webhook would make Meta retry the whole payload and re-run the LLM. The
    difference from before is that the failure is now RECORDED and RETURNED
    instead of being swallowed into a bare None.
    """

    def _record(result: DeliveryResult) -> DeliveryResult:
        if message is not None and result.status != "not_applicable":
            message.DeliveryStatus = result.status
            message.ExternalMessageId = result.message_id
            message.DeliveryError = (result.error or result.detail or None)
            if message.DeliveryError:
                message.DeliveryError = message.DeliveryError[:500]
        return result

    if not text:
        return DeliveryResult("not_applicable", detail="Empty message")

    if conversation.Channel in (CHANNEL_WEB, "voice"):
        # Correct and expected: the widget polls, and a voice turn is spoken.
        return DeliveryResult(
            "not_applicable",
            detail=f"Channel '{conversation.Channel}' does not require push delivery",
        )

    from . import billing as billing_svc
    allowed, reason = billing_svc.check_channel_access(db, conversation.ClientId, conversation.Channel)
    if not allowed:
        return _record(
            DeliveryResult(
                "failed",
                error="Channel subscription required",
                detail=reason,
            )
        )

    if not conversation.ChannelAccountId or not conversation.ExternalThreadId:
        # This conversation is tagged with a social channel but has no route back
        # to the customer. Almost always a conversation created manually or by an
        # import rather than by an inbound webhook.
        return _record(
            DeliveryResult(
                "failed",
                error="No channel route on this conversation",
                detail=(
                    f"Conversation is marked '{conversation.Channel}' but has no "
                    "channel account or external thread id, so there is no address "
                    "to send to. This happens when a conversation was created "
                    "outside an inbound webhook."
                ),
            )
        )

    from . import channels as ch

    account = db.get(LeadChannelAccount, conversation.ChannelAccountId)
    if account is None:
        return _record(
            DeliveryResult(
                "failed",
                error="Channel account not found",
                detail="The connected account this conversation belongs to no longer exists.",
            )
        )
    if not account.IsActive:
        return _record(
            DeliveryResult(
                "failed",
                error="Channel account is disconnected",
                detail=(
                    f"The {conversation.Channel} account '{account.Name or account.ExternalId}' "
                    "is inactive. Reconnect it under Channels and send again."
                ),
            )
        )

    # Meta's 24-hour rule. Outside the window a free-form send is REJECTED by the
    # Graph API, so checking first turns a confusing provider error into a clear
    # instruction. Checked here rather than inside channels.send_text() because
    # campaigns legitimately send templates outside the window.
    last_inbound = _last_customer_message_at(db, conversation)
    if not ch.within_session_window(last_inbound):
        return _record(
            DeliveryResult(
                "skipped",
                error="Outside the 24-hour messaging window",
                detail=(
                    "Meta only allows free-form replies within 24 hours of the "
                    "customer's last message"
                    + (
                        f" (last received {last_inbound:%d %b %H:%M} UTC)."
                        if last_inbound
                        else " (this customer has never messaged in)."
                    )
                    + " Send an approved template instead, or wait for them to reply."
                ),
            )
        )

    try:
        message_id = ch.send_text(
            account, conversation.Channel, conversation.ExternalThreadId, text
        )
        account.LastOutboundAt = utcnow()
        logger.info(
            "[LeadAI] delivered on %s conv=%s provider_id=%s",
            conversation.Channel, conversation.Id, message_id,
        )
        return _record(DeliveryResult("sent", message_id=message_id))
    except Exception as exc:  # noqa: BLE001
        account.LastErrorAt = utcnow()
        account.LastError = str(exc)[:500]
        logger.warning(
            "[LeadAI] delivery failed on %s conv=%s: %s",
            conversation.Channel, conversation.Id, exc,
        )
        return _record(
            DeliveryResult(
                "failed",
                error=str(exc)[:500],
                detail=f"The {conversation.Channel} provider rejected the message.",
            )
        )


def _last_customer_message_at(db: Session, conversation: LeadConversation):
    """Timestamp of the most recent INBOUND message, for the 24h window check.

    Deliberately the customer's last message, not `conversation.LastMessageAt` —
    the latter is bumped by our own outbound turns, so using it would make the
    window look open forever and every send outside it would fail at the provider
    with an opaque error instead of being caught here.
    """
    row = (
        db.query(LeadMessage)
        .filter(
            LeadMessage.ConversationId == conversation.Id,
            LeadMessage.Sender == "customer",
            LeadMessage.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadMessage.CreatedAt.desc())
        .first()
    )
    return row.CreatedAt if row else None


def _event(db: Session, type_: str, conversation: LeadConversation, *, speaker: str, **fields) -> None:
    """Queue a turn event on this transaction (a no-op unless ENGINE_EVENTS is on).

    `text`, `confidence`, `latency_ms` and `turn_id` are first-class event fields; anything
    else lands in `data`. Queued, not written: it commits or rolls back with the turn.
    """
    known = {k: fields.pop(k) for k in ("text", "confidence", "latency_ms", "turn_id") if k in fields}
    outbox.emit(
        db,
        TurnEvent(
            type=type_,
            client_id=conversation.ClientId,
            conversation_id=conversation.Id,
            channel=conversation.Channel or "web",
            speaker=speaker,
            data=fields,
            **known,
        ),
    )


def _broadcast_inbox(client_id: str, payload: dict) -> None:
    if ws_manager is None:
        return
    _fire_and_forget(ws_manager.broadcast_to_leadai_inbox(client_id, payload))


def _broadcast_conversation(conversation_id: str, payload: dict) -> None:
    if ws_manager is None:
        return
    _fire_and_forget(
        ws_manager.broadcast_to_leadai_conversation(conversation_id, payload)
    )


# =========================================================================== #
# call escalation ("chat and call", with consent) — see models.LeadCampaign.
# CallEscalationEnabled and LeadConversation.CallConsentStatus.
# =========================================================================== #
CALL_CONSENT_YES = re.compile(
    r"\b(yes|yeah|yep|yup|sure|ok|okay|please call|call me|go ahead|call now)\b", re.IGNORECASE
)
CALL_CONSENT_NO = re.compile(
    r"\b(no|nope|not now|don'?t call|later|no thanks)\b", re.IGNORECASE
)


def _resolve_call_consent(db: Session, client: Client, conversation: LeadConversation, text: str) -> None:
    """If we're waiting on a yes/no to "should we call you", resolve it from
    THIS message before the normal chat reply is generated. A call is placed
    ONLY here, on an explicit yes — never anywhere else, and never silently.
    Best-effort: a call failing to start must not break the chat turn.
    """
    if conversation.CallConsentStatus != "asked":
        return
    if CALL_CONSENT_YES.search(text or ""):
        conversation.CallConsentStatus = "accepted"
        try:
            customer = db.get(LeadCustomer, conversation.CustomerId)
            phone = decrypt_pii(customer.PhoneEnc) if customer else None
            if not phone:
                logger.warning("[LeadAI] call consent accepted but conv %s has no phone on file",
                               conversation.Id)
                return
            campaign = db.get(LeadCampaign, conversation.CampaignId) if conversation.CampaignId else None
            from . import call_bridge

            call_bridge.start_call_for_conversation(
                db, client.Id, client.Name, conversation,
                initiated_by="call_consent", mode="ai_voice",
                script_id=campaign.ScriptId if campaign else None,
                override_number=phone,
            )
        except Exception:  # noqa: BLE001
            logger.exception("[LeadAI] consent-triggered call failed to start for conv %s", conversation.Id)
    elif CALL_CONSENT_NO.search(text or ""):
        conversation.CallConsentStatus = "declined"
    # Anything else (an unrelated reply) leaves status "asked" — the chat just
    # continues normally and the offer can still be accepted on a later turn.


def _maybe_offer_call_consent(db: Session, conversation: LeadConversation, result: dict) -> None:
    """Right before a normal handoff would fire, offer a call instead — only
    for a campaign that explicitly turned this on, and only once per
    conversation (CallConsentStatus starts null; "asked" guards re-asking).
    """
    if not (result.get("needs_human") and conversation.Status == "open"):
        return
    if conversation.CallConsentStatus is not None or not conversation.CampaignId:
        return
    campaign = db.get(LeadCampaign, conversation.CampaignId)
    if campaign is None or not campaign.CallEscalationEnabled:
        return
    result["reply"] = (
        (result.get("reply") or "").rstrip()
        + " Would you like us to call you about this instead?"
    ).strip()
    conversation.CallConsentStatus = "asked"
    conversation.CallConsentAskedAt = utcnow()


# =========================================================================== #
# the pipeline
# =========================================================================== #
def handle_customer_turn(
    db: Session,
    client: Client,
    conversation: LeadConversation,
    text: str,
    *,
    request: Request | None = None,
    source: str = "widget",
    deliver_reply: bool = False,
    external_message_id: str | None = None,
) -> TurnResult:
    """Process one inbound customer message, end to end, one turn at a time per
    conversation (when LEADAI_CONVERSATION_LOCK is on; see engine/locks.py).

    Two messages arriving together no longer race: the second waits, then runs with the
    first one's message and reply already in its history.
    """
    conversation_id = conversation.Id
    with conversation_lock(db, conversation_id), bind_usage_context(
        company_id=client.Id,
        process="chat_answer",
        channel=conversation.Channel,
        conversation_id=conversation.Id,
    ):
        return _run_customer_turn(
            db, client, conversation, text,
            request=request, source=source, deliver_reply=deliver_reply,
            external_message_id=external_message_id,
        )


@traceable(name="agent:leadai_customer_turn", run_type="chain")
def _run_customer_turn(
    db: Session,
    client: Client,
    conversation: LeadConversation,
    text: str,
    *,
    request: Request | None = None,
    source: str = "widget",
    deliver_reply: bool = False,
    external_message_id: str | None = None,
) -> TurnResult:
    """Process one inbound customer message, end to end.

    Sequence (unchanged from the original widget pipeline, plus the threshold
    gate and optional delivery):

        persist inbound
          -> if a human has taken over: qualify, broadcast, STOP (AI stays quiet)
          -> RAG answer
          -> persist outbound
          -> qualify + summarise
          -> threshold evaluation (+ notify once, + optional CRM promotion)
          -> handoff if confidence is low
          -> commit
          -> broadcast + deliver

    Commit happens ONCE, at the end, so a failure anywhere leaves no half-written
    conversation. Delivery and websocket broadcasts happen strictly AFTER the
    commit — sending a WhatsApp message for a turn that then rolled back would
    be unrecoverable.
    """
    client_id = client.Id
    trace = TurnTrace(
        conversation_id=conversation.Id, client_id=client_id,
        channel=conversation.Channel, turn_id=external_message_id,
    )
    trace_step(trace, "receive", f"customer message via {source}",
               msg_chars=len(text or ""), deliver_reply=deliver_reply,
               external_id_present=bool(external_message_id),
               conversation_status=conversation.Status,
               control_status=engine_control.get_control(conversation))

    _resolve_call_consent(db, client, conversation, text)

    # Centralised in memory.thread_history() so the voice path and the chat path
    # can never window history differently. Same query as before.
    history = memory.thread_history(db, conversation.Id)

    # Cross-channel carry-over. A customer who chatted on the website in March
    # and WhatsApps in April gets a NEW conversation row (correctly — different
    # channel, different thread), which previously meant the AI met them as a
    # stranger. The digest below is a read-only summary of what is already known
    # about them, injected as one system-role turn ahead of the thread. Empty
    # string when they are genuinely new, in which case nothing changes.
    carryover = ""
    if len(history) <= 2:
        # Only worth building at the start of a thread. Mid-conversation the
        # thread itself carries the context, and re-injecting the digest on every
        # turn wastes prompt budget and makes the bot repeat old facts.
        carryover = memory.customer_memory(db, conversation)
        if carryover:
            logger.info(
                "[LeadAI flow] carry-over memory applied to conv %s (%d chars)",
                conversation.Id,
                len(carryover),
            )

    trace_step(trace, "memory", "history loaded",
               history_msgs=len(history), window_turns=memory.LLM_WINDOW_TURNS,
               thread_truncated=memory.thread_is_truncated(history),
               carryover_applied=bool(carryover), carryover_chars=len(carryover))

    inbound = LeadMessage(
        ClientId=client_id,
        ConversationId=conversation.Id,
        Sender="customer",
        Content=text,
        CreatedBy=source,
    )
    db.add(inbound)
    db.flush()
    history.append(inbound)
    conversation.LastMessageAt = utcnow()
    _event(db, "turn.received", conversation, speaker="customer", text=text,
           turn_id=external_message_id, source=source)

    # A customer who types their number into the chat ("yes it's 98765 43210") has given
    # it to us: keep it on the customer record, encrypted, so staff see it masked and can
    # Reveal it. Runs before the human-takeover check, so it works while an agent is on it.
    captured = contact_capture.capture_phone(
        db, db.get(LeadCustomer, conversation.CustomerId), text,
        client_id=client_id, conversation_id=conversation.Id, actor=source,
    )
    # Whether a number was saved, never the number.
    trace_step(trace, "phone_capture", "phone number saved" if captured else "nothing to save")

    # Broadcast customer message immediately so staff sees it before AI processing
    _broadcast_conversation(
        conversation.Id,
        {
            "type": "message",
            "sender": "customer",
            "content": text,
            "conversation_id": conversation.Id,
            "channel": conversation.Channel,
        },
    )
    _broadcast_inbox(
        client_id,
        {
            "type": "new_message",
            "conversation_id": conversation.Id,
            "sender": "customer",
            "content": text,
            "channel": conversation.Channel,
            "client_id": client_id,
        },
    )

    lead = db.query(Lead).filter(Lead.ConversationId == conversation.Id).one_or_none()
    if lead is None:
        lead = Lead(ClientId=client_id, ConversationId=conversation.Id, CreatedBy=source)
        db.add(lead)
        db.flush()

    activity.log(
        db,
        action=A.CHAT_MESSAGE,
        client_id=client_id,
        actor_email=source,
        actor_role="customer",
        entity_type="conversation",
        entity_id=conversation.Id,
        message=f"Customer message ({conversation.Channel}): {text[:160]}",
        request=request,
    )

    # ---- stopped from outside (paused / terminated): the AI stays silent -----
    # Set by staff or the monitor agent (engine/control.py). The message is stored so
    # nothing the customer said is lost, but nothing else happens: no reply, and no
    # re-scoring, so a lead that was nullified stays nullified.
    if engine_control.is_stopped(conversation):
        conversation.MessageCount = len(history)
        _event(db, "turn.skipped", conversation, speaker="system",
               reason=f"conversation {engine_control.get_control(conversation)}")
        trace_step(trace, "control",
                   f"stopped: {engine_control.get_control(conversation)}; AI silent, no re-scoring",
                   reason=conversation.ControlReason, set_by=conversation.ControlBy)
        inbound.TraceJson = trace.as_json()
        db.commit()
        return TurnResult(
            reply="",
            confidence=1.0,
            needs_human=False,
            handed_off=conversation.Status in ("needs_human", "assigned"),
            lead_status=lead.Status,
            lead_score=lead.Score or 0,
            conversation_id=conversation.Id,
            ai_replied=False,
        )

    # ---- a human has taken over: the AI stays silent ----------------------
    if conversation.Status == "assigned":
        _event(db, "turn.skipped", conversation, speaker="system", reason="human took over")
        trace_step(trace, "takeover", "a human has taken over: AI silent, lead still scored",
                   assigned_to_set=bool(conversation.AssignedUserEmail))
        above, crossed = score_turn(db, client, conversation, lead, history, trace=trace, request=request)
        inbound.TraceJson = trace.as_json()
        db.commit()

        return TurnResult(
            reply="",
            confidence=1.0,
            needs_human=True,
            handed_off=True,
            lead_status=lead.Status,
            lead_score=lead.Score or 0,
            above_threshold=above,
            crossed_threshold_now=crossed,
            conversation_id=conversation.Id,
            ai_replied=False,
        )

    # ---- AI turn ----------------------------------------------------------
    # Once the AI has finished the conversation (told the customer an advisor will
    # follow up), a bare "ok" / "yes" / "thanks" must not restart the qualification
    # questions. It gets one short fixed line, with no retrieval and no LLM call.
    already_completed = conversation.AiCompletedAt is not None
    if already_completed and reply_cleanup.is_acknowledgement(text):
        trace_step(trace, "shortcut",
                   "conversation already complete and message is an acknowledgement: "
                   "fixed closing line, no retrieval, no model call")
        result = {
            "reply": reply_cleanup.closing_reply(client.Name, text),
            "confidence": 1.0,
            "needs_human": False,
            "ends_conversation": False,
            "handoff_reason": None,
            "sources": [],
            "model": "closing-template",
            "latency_ms": 0,
        }
    else:
        # Once the thread outgrows the model's window, re-supply what is already
        # established (facts, summary) so an early detail is not forgotten. "" for
        # short threads, where the model sees everything anyway.
        state_note = memory.thread_state_note(db, conversation, history)
        trace_step(trace, "state_note",
                   "added: thread outgrew the model window" if state_note else "not needed",
                   chars=len(state_note), already_completed=already_completed)
        datapoints_note = memory.missing_data_points_note(db, client_id, lead)
        result = ai_engine.answer(
            db, client_id, client.Name, text, history=history, channel="chat",
            carryover=carryover,
            # A customer who keeps talking after completion asked something real:
            # continue from where we stopped, do not start over.
            session_note="\n\n".join(
                n for n in (state_note, reply_cleanup.COMPLETED_NOTE if already_completed else "",
                            datapoints_note) if n
            ),
            trace=trace,
        )
        # Judge (observe) or decide (enforce) the reply; a no-op unless ENGINE_MODE is set.
        result = engine_bridge.apply(
            result, text=text, client_id=client_id, conversation_id=conversation.Id,
            channel=conversation.Channel, history=history,
        )
        note = result.get("engine")
        trace_step(
            trace, "engine",
            "off" if not note else f"{note['mode']}: verdict={note['verdict']}",
            **({} if not note else {
                "unsupported_figures": note["unsupported_count"], "declined_in_words": note["declined"],
                "escalation": note["escalation"],
            }),
        )
        if result.get("ends_conversation"):
            conversation.AiCompletedAt = utcnow()
        elif already_completed:
            conversation.AiCompletedAt = None  # they reopened it with a new question

    _maybe_offer_call_consent(db, conversation, result)

    outbound = LeadMessage(
        ClientId=client_id,
        ConversationId=conversation.Id,
        Sender="ai",
        Content=result["reply"],
        Confidence=result["confidence"],
        SourcesJson=result["sources"],
        ModelUsed=result["model"],
        LatencyMs=result["latency_ms"],
        CreatedBy="ai",
    )
    db.add(outbound)
    db.flush()
    history.append(outbound)
    _event(db, "turn.replied", conversation, speaker="ai", text=result["reply"],
           confidence=result["confidence"], latency_ms=result["latency_ms"],
           model=result["model"], sources=len(result["sources"]),
           needs_human=bool(result["needs_human"]), **engine_bridge.audit_meta(result))

    activity.log(
        db,
        action=A.AI_REPLIED,
        client_id=client_id,
        actor_email="ai",
        actor_role="ai",
        entity_type="conversation",
        entity_id=conversation.Id,
        message=f"AI replied (confidence {result['confidence']})",
        meta={
            "confidence": result["confidence"],
            "sources": len(result["sources"]),
            "model": result["model"],
            "latency_ms": result["latency_ms"],
            "channel": conversation.Channel,
            **engine_bridge.audit_meta(result),
        },
        request=request,
    )

    conversation.MessageCount = len(history)
    conversation.LastMessageAt = utcnow()

    handed_off = False
    if result.get("ends_conversation") and conversation.Status == "needs_human":
        # Already waiting for a human (e.g. auto-paused earlier): the reason shown to
        # staff should be the current one, not a stale one.
        conversation.HandoffReason = (result["handoff_reason"] or "")[:300]
    if result["needs_human"] and conversation.Status == "open":
        conversation.Status = "needs_human"
        conversation.HandoffReason = (result["handoff_reason"] or "")[:300]
        handed_off = True
        _event(db, "handoff.requested", conversation, speaker="ai",
               reason=conversation.HandoffReason, confidence=result["confidence"])
        activity.log(
            db,
            action=A.AI_HANDOFF,
            client_id=client_id,
            actor_email="ai",
            actor_role="ai",
            entity_type="conversation",
            entity_id=conversation.Id,
            message=f"Escalated to human: {conversation.HandoffReason}",
            meta={"confidence": result["confidence"]},
            log_type="Warning",
            request=request,
        )

    if result["needs_human"] and not handed_off:
        trace_step(trace, "handoff", "AI wanted a human, but no new handoff was raised",
                   conversation_status=conversation.Status, reason=result.get("handoff_reason"))
    else:
        trace_step(trace, "handoff",
                   "raised: conversation now needs a human" if handed_off else "none needed",
                   reason=conversation.HandoffReason if handed_off else None,
                   conversation_status=conversation.Status)

    # Lead scoring (qualify + summarise) is a second LLM round trip that changes nothing
    # about the reply already decided above, so on EVERY channel it runs after the reply
    # (scoring_queue, its own session) instead of adding its latency to the reply. The
    # widget's response therefore carries the lead_status/lead_score as of the previous turn:
    # only the staff-side lead gauge reads it, and it catches up on the next message.
    trace_step(trace, "commit", "turn committed as one transaction",
               reply_chars=len(result["reply"] or ""), messages_in_thread=len(history))
    outbound.TraceJson = trace.as_json()
    db.commit()

    # ---- post-commit side effects ----------------------------------------
    if deliver_reply and result["reply"]:
        # `outbound` is passed so the delivery outcome lands on the message row.
        # An AI reply that Meta rejected now shows as failed in the inbox rather
        # than sitting there looking delivered.
        delivery = deliver(db, conversation, result["reply"], message=outbound)
        trace_step(trace, "deliver", f"delivery {delivery.status}",
                   channel=conversation.Channel, error=getattr(delivery, "error", None))
        if delivery.status != "not_applicable":
            outbound.TraceJson = trace.as_json()
            db.commit()

    scoring_queue.submit(client_id, conversation.Id, outbound.Id)

    _broadcast_conversation(
        conversation.Id,
        {
            "type": "message",
            "sender": "ai",
            "content": result["reply"],
            "confidence": result["confidence"],
            "conversation_id": conversation.Id,
            "channel": conversation.Channel,
        },
    )
    _broadcast_inbox(
        client_id,
        {
            "type": "new_message",
            "conversation_id": conversation.Id,
            "sender": "ai",
            "content": result["reply"],
            "channel": conversation.Channel,
            "client_id": client_id,
        },
    )

    return TurnResult(
        reply=result["reply"],
        confidence=result["confidence"],
        needs_human=result["needs_human"],
        handed_off=handed_off or conversation.Status in ("needs_human", "assigned"),
        sources=result["sources"],
        lead_status=lead.Status,
        lead_score=lead.Score or 0,
        conversation_id=conversation.Id,
    )
