"""
Inbound social webhooks — the replacement source of leads.

ONE URL FOR ALL THREE CHANNELS
------------------------------
    GET  /api/leadai/public/webhooks/meta   -> subscription handshake
    POST /api/leadai/public/webhooks/meta   -> messages + delivery receipts

WhatsApp, Messenger and Instagram all deliver to the same Meta webhook
infrastructure, so one endpoint handles all three and dispatches on the payload's
`object` field. Paste this single URL into all three products in the Meta app
dashboard.

THE THREE RULES THIS ENDPOINT LIVES BY
--------------------------------------
1. ALWAYS RETURN 200 QUICKLY. Meta retries any non-2xx, with backoff, for days.
   A retry re-runs the LLM and re-sends the reply. So: verify, persist, ACK —
   and do the expensive work behind the acknowledgement. Genuine errors are
   logged and stored on `leadai_channel_events`, not signalled by a 500.

2. NEVER TRUST THE BODY FOR TENANCY. The company is resolved by looking up the
   RECEIVING account's external id in `leadai_channel_accounts`. Nothing the
   sender controls can select a tenant.

3. IDEMPOTENCY BY MESSAGE ID. `leadai_channel_events.ExternalMessageId` carries
   a unique constraint. A replayed delivery hits it, is recognised as a
   duplicate, and is dropped before it reaches the pipeline.

WHY IT IS PUBLIC
----------------
Meta cannot present a user token. Authentication is instead cryptographic: the
`X-Hub-Signature-256` HMAC over the raw body, checked against the app secret in
`services/channels.verify_signature` before the body is parsed.
"""
from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import activity
from ..activity import A
from ..config import settings
from ..db import get_leadai_db, session as new_session
from ..models import (
    LeadChannelAccount,
    LeadChannelEvent,
    LeadChannelIdentity,
    utcnow,
)
from ..services import campaign_runner, channels, conversation_flow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public/webhooks", tags=["LeadAI • Inbound webhooks"])

# Replies to these (case-insensitive, whole message) opt the contact out. Meta
# requires an honoured opt-out path; this is it.
OPT_OUT_WORDS = {"stop", "unsubscribe", "opt out", "optout", "band karo", "cancel"}
OPT_IN_WORDS = {"start", "subscribe", "unstop", "resume"}


@router.get("/meta", summary="Meta webhook verification handshake")
def verify_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    db: Session = Depends(get_leadai_db),
):
    """Echo `hub.challenge` as plain text when the verify token matches."""
    accepted = channels.verify_challenge(hub_mode, hub_verify_token, settings.meta_verify_token)
    if not accepted and hub_verify_token:
        # Per-tenant verify tokens: any active account may own this token.
        accepted = (
            db.query(LeadChannelAccount)
            .filter(
                LeadChannelAccount.VerifyToken == hub_verify_token,
                LeadChannelAccount.IsActive == True,  # noqa: E712
                LeadChannelAccount.IsDeleted == False,  # noqa: E712
            )
            .first()
            is not None
        ) and hub_mode == "subscribe"

    if not accepted:
        logger.warning("[LeadAI webhook] verification rejected (mode=%s)", hub_mode)
        return Response(content="Verification failed", status_code=403)
    return Response(content=hub_challenge or "", media_type="text/plain")


@router.post("/meta", summary="Inbound WhatsApp / Messenger / Instagram events")
async def receive_webhook(
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_leadai_db),
):
    """Verify, de-duplicate, persist, acknowledge — then process in the background.

    The response is a 200 in every path except a failed signature check. That is
    intentional: see rule 1 in the module docstring.
    """
    raw = await request.body()
    logger.warning("[LeadAI webhook] raw len=%d sha=%s sig=%s",
                   len(raw),
                   hashlib.sha256(raw).hexdigest()[:12],
                   (request.headers.get("x-hub-signature-256") or "")[:20])
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return {"received": True, "ignored": "unparseable body"}

    try:
        messages, statuses = channels.normalise(payload)
    except Exception:  # noqa: BLE001
        logger.exception("[LeadAI webhook] normalise failed — acknowledging anyway")
        return {"received": True, "ignored": "normalise error"}
    if not messages and not statuses:
        return {"received": True, "ignored": "no actionable events"}

    signature = request.headers.get("x-hub-signature-256")
    accepted: list[dict] = []

    for message in messages:
        account = channels.find_account(db, message.channel, message.account_external_id)
        if account is None:
            # Log the ids we DO hold for this channel. Without them the next
            # id mismatch is another hour of guessing; with them it names
            # itself. Note that Meta's "Send to server" test button always
            # sends recipient.id=23245, which matches nothing by design — a
            # drop of that id is expected and proves only that the URL is
            # reachable.
            known = [
                (a.ExternalId, a.BusinessAccountId)
                for a in db.query(LeadChannelAccount)
                .filter(
                    LeadChannelAccount.Channel == message.channel,
                    LeadChannelAccount.IsDeleted == False,  # noqa: E712
                )
                .limit(20)
                .all()
            ]
            logger.warning(
                "[LeadAI webhook] no account for %s/%s — event dropped; known ids=%s",
                message.channel, message.account_external_id, known,
            )
            continue

        # Per-account app secret, so one tenant's leaked secret cannot be used to
        # forge events for another.
        if not channels.verify_signature(raw, signature, channels.app_secret_for(account)):
            logger.error("[LeadAI webhook] SIGNATURE MISMATCH for account %s", account.Id)
            return Response(content="Invalid signature", status_code=403)

        if not _record_event(db, account, message.external_message_id, "message", payload):
            continue  # duplicate delivery

        accepted.append(
            {
                "account_id": account.Id,
                "channel": message.channel,
                "external_user_id": message.external_user_id,
                "external_message_id": message.external_message_id,
                "text": message.text,
                "profile_name": message.profile_name,
                "phone": message.phone,
                "media_type": message.media_type,
            }
        )

    # Delivery receipts are cheap; handle them inline.
    for status in statuses:
        try:
            if campaign_runner.apply_status_update(
                db, status.external_message_id, status.status, status.error
            ):
                db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.warning("[LeadAI webhook] status update failed: %s", exc)

    if accepted:
        # Detached from the request so Meta gets its 200 immediately, even if the
        # LLM takes four seconds.
        background.add_task(_process_messages, accepted)

    return {"received": True, "queued": len(accepted)}


def _record_event(
    db: Session,
    account: LeadChannelAccount,
    external_message_id: str,
    event_type: str,
    payload: dict,
) -> bool:
    """Insert the idempotency row. False means we have seen this id before."""
    if not external_message_id:
        return True  # nothing to de-duplicate on; let it through
    event = LeadChannelEvent(
        ClientId=account.ClientId,
        ChannelAccountId=account.Id,
        Channel=account.Channel,
        Direction="inbound",
        ExternalMessageId=external_message_id[:160],
        EventType=event_type,
        Status="accepted",
        PayloadJson=payload if len(str(payload)) < 60_000 else {"truncated": True},
        CreatedBy=account.Channel,
    )
    try:
        db.add(event)
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        logger.info("[LeadAI webhook] duplicate delivery %s ignored", external_message_id)
        return False
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("[LeadAI webhook] could not record event: %s", exc)
        return True


def _process_messages(items: list[dict]) -> None:
    """Background pass: run the shared pipeline for each accepted message.

    Owns its own session — the request's session is closed by the time this
    runs. Every item is independent, so one failure cannot stop the rest.
    """
    for item in items:
        db = new_session()
        try:
            _process_one(db, item)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.exception("[LeadAI webhook] processing failed: %s", exc)
        finally:
            db.close()


def _process_one(db: Session, item: dict) -> None:
    account = db.get(LeadChannelAccount, item["account_id"])
    if account is None or not account.IsActive:
        return

    text = (item.get("text") or "").strip()
    client, conversation, identity = conversation_flow.resolve_social_conversation(
        db,
        account,
        external_user_id=item["external_user_id"],
        profile_name=item.get("profile_name"),
        phone=item.get("phone"),
    )

    # ---- opt-out / opt-in keywords, handled BEFORE the AI sees the message --
    lowered = text.lower().strip(" .!")
    if lowered in OPT_OUT_WORDS:
        identity.OptedOut = True
        identity.OptedOutAt = utcnow()
        _sync_account_consent(db, client.Id, item.get("phone"), opted_out=True)
        activity.log(
            db,
            action=A.CHANNEL_OPTED_OUT,
            client_id=client.Id,
            actor_email=account.Channel,
            entity_type="conversation",
            entity_id=conversation.Id,
            message="Contact opted out via keyword",
            log_type="Warning",
        )
        db.commit()
        try:
            channels.send_text(
                account, account.Channel, item["external_user_id"],
                "You have been unsubscribed. Reply START at any time to resume.",
            )
        except Exception:  # noqa: BLE001
            pass
        return

    if lowered in OPT_IN_WORDS and identity.OptedOut:
        identity.OptedOut = False
        identity.OptedOutAt = None
        _sync_account_consent(db, client.Id, item.get("phone"), opted_out=False)
        db.commit()

    if not text:
        # Media with no caption. Persisted by the pipeline as a placeholder so
        # the agent sees that something arrived.
        text = f"[{item.get('media_type') or 'attachment'} received]"

    # A reply to a campaign is the metric that matters most; record it before
    # the pipeline mutates the conversation.
    campaign_runner.note_reply(db, conversation)

    activity.log(
        db,
        action=A.CHANNEL_INBOUND,
        client_id=client.Id,
        actor_email=account.Channel,
        entity_type="conversation",
        entity_id=conversation.Id,
        message=f"Inbound {account.Channel} message received",
        meta={"account": account.Name, "channel": account.Channel},
    )
    db.commit()

    if not account.AutoReply:
        # Human-only channel: persist the customer's message and notify the
        # inbox, but do not let the AI answer.
        from ..models import LeadMessage

        db.add(
            LeadMessage(
                ClientId=client.Id,
                ConversationId=conversation.Id,
                Sender="customer",
                Content=text,
                CreatedBy=account.Channel,
            )
        )
        conversation.LastMessageAt = utcnow()
        conversation.MessageCount = (conversation.MessageCount or 0) + 1
        if conversation.Status == "open":
            conversation.Status = "needs_human"
        db.commit()
        conversation_flow._broadcast_inbox(
            client.Id,
            {
                "type": "new_message",
                "conversation_id": conversation.Id,
                "sender": "customer",
                "content": text,
                "channel": account.Channel,
                "client_id": client.Id,
            },
        )
        return

    channels.mark_read(
        account,
        item.get("external_message_id") or "",
        external_user_id=item.get("external_user_id"),
    )

    conversation_flow.handle_customer_turn(
        db,
        client,
        conversation,
        text,
        source=account.Channel,
        deliver_reply=True,
        external_message_id=item.get("external_message_id"),
    )


def _sync_account_consent(db: Session, client_id: str, phone: str | None, opted_out: bool) -> None:
    """Mirror a channel-level opt-out onto the CRM record.

    Without this, someone who replies STOP on WhatsApp would still be included
    in a campaign built from the customers audience.
    """
    if not phone:
        return
    from ..services import crm

    account = crm.find_account_by_phone(db, client_id, phone)
    if account is None:
        return
    account.OptInWhatsApp = not opted_out
    account.OptInSms = not opted_out
    if opted_out:
        account.DoNotDisturb = True


@router.post("/{channel}/generic", summary="Generic inbound adapter (non-Meta providers)")
async def receive_generic(
    channel: str,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_leadai_db),
):
    """Escape hatch for providers that are not Meta (Twilio WhatsApp, Gupshup,
    360dialog…).

    They all send some variant of {from, to, text}. Map that shape here and the
    rest of the platform is unchanged — which is the whole point of having one
    pipeline behind the adapters.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        form = await request.form()
        body = dict(form)

    to = str(body.get("To") or body.get("to") or body.get("account_id") or "")
    sender = str(body.get("From") or body.get("from") or body.get("sender") or "")
    text = str(body.get("Body") or body.get("text") or body.get("message") or "")
    message_id = str(body.get("MessageSid") or body.get("id") or f"{sender}:{len(text)}")

    account = channels.find_account(db, channel, to.replace("whatsapp:", "").lstrip("+"))
    if account is None:
        return {"received": True, "ignored": "unknown account"}
    if not _record_event(db, account, message_id, "message", body):
        return {"received": True, "ignored": "duplicate"}

    background.add_task(
        _process_messages,
        [
            {
                "account_id": account.Id,
                "channel": channel,
                "external_user_id": sender.replace("whatsapp:", "").lstrip("+"),
                "external_message_id": message_id,
                "text": text,
                "profile_name": body.get("ProfileName"),
                "phone": sender.replace("whatsapp:", ""),
                "media_type": "text",
            }
        ],
    )
    return {"received": True}
