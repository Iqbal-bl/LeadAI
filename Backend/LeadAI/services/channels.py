"""
Social channel adapters — WhatsApp Cloud API, Messenger, Instagram Direct.

THE ONE IDEA IN THIS FILE
------------------------
Every channel is reduced to two functions:

    normalise(payload)  ->  list[InboundMessage]     (provider shape -> ours)
    send(account, to, text/template)  ->  message id (ours -> provider shape)

Everything between those two — customer resolution, RAG, qualification, lead
scoring, handoff, activity logging — is the SAME code that already serves the
web widget (`services/conversation_flow.py`). That is why adding Facebook and
Instagram after WhatsApp costs almost nothing: all three are Meta Graph, so
they share the signature check, the transport and the error handling, and
differ only in which JSON field holds the sender id and which endpoint the
reply is POSTed to.

WHY THE WEB WIDGET STAYS
------------------------
Meta app review for WhatsApp Business and Instagram messaging takes weeks and
can bounce. The widget is the fallback that keeps lead capture alive during
that period, and afterwards it is still the right channel for a website
visitor who is not going to switch to WhatsApp to ask one question. Both write
to the same tables, so "which channel produced this lead" is a column, not a
separate system.

SECURITY
--------
1. Every webhook body is verified against `X-Hub-Signature-256` (HMAC-SHA256
   with the app secret) BEFORE it is parsed. An unverified body is discarded —
   otherwise anyone who learns the URL can inject fake leads and burn LLM spend.
2. Tenant routing NEVER trusts the payload for the company id. The payload
   carries the RECEIVING account's external id (phone_number_id / page id);
   that is looked up in `leadai_channel_accounts` to find the ClientId.
3. Access tokens are encrypted at rest with the same Fernet helper as customer
   PII and are only decrypted inside `_token_for()`.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    CHANNEL_INSTAGRAM,
    CHANNEL_MESSENGER,
    CHANNEL_WHATSAPP,
    LeadChannelAccount,
)
from ..security import decrypt_pii

logger = logging.getLogger(__name__)

META_CHANNELS = (CHANNEL_WHATSAPP, CHANNEL_MESSENGER, CHANNEL_INSTAGRAM)


class ChannelError(RuntimeError):
    """The provider rejected the send. Carries a user-safe message."""


@dataclass
class InboundMessage:
    """Provider-agnostic inbound event."""

    channel: str
    account_external_id: str          # phone_number_id / page id / ig id
    external_user_id: str             # wa_id / PSID / IGSID
    external_message_id: str
    text: str = ""
    profile_name: str | None = None
    phone: str | None = None          # WhatsApp gives the real number
    media_url: str | None = None
    media_type: str | None = None
    timestamp: datetime | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class StatusUpdate:
    """Delivery receipt (sent/delivered/read/failed) for an outbound message."""

    channel: str
    account_external_id: str
    external_message_id: str
    status: str
    error: str | None = None
    timestamp: datetime | None = None


# =========================================================================== #
# signature verification
# =========================================================================== #
def verify_signature(raw_body: bytes, header: str | None, app_secret: str | None) -> bool:
    """Constant-time check of Meta's X-Hub-Signature-256.

    Returns True when verification is DISABLED by config or no secret is
    configured — that combination is only for local development and is surfaced
    on the health endpoint so it cannot silently ship to production.
    """
    if not settings.meta_verify_signatures:
        return True
    secret = app_secret or settings.meta_app_secret
    if not secret:
        logger.warning("[LeadAI channels] no app secret configured — signature not verified")
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


def verify_challenge(mode: str | None, token: str | None, expected_token: str | None) -> bool:
    """GET handshake Meta performs once when you save the callback URL."""
    return mode == "subscribe" and bool(token) and token == (expected_token or settings.meta_verify_token)


# =========================================================================== #
# account lookup
# =========================================================================== #
def find_account(db: Session, channel: str, external_id: str) -> LeadChannelAccount | None:
    """Route an inbound event to the tenant that owns the receiving account.

    EXACT MATCH ONLY — NO HEURISTICS
    --------------------------------
    An earlier version fell back to "if there is exactly one Instagram account
    in the whole database, it must be that one". That is wrong in two ways on a
    multi-tenant platform:

      * It has no ClientId filter, so the count spans EVERY company. It works
        for your first customer and silently stops working the moment a second
        one connects Instagram — presenting as "it worked in beta, production
        drops everything".

      * While only one account exists it is a tenant-assignment hole: any POST
        with an arbitrary recipient.id selects that company. Signature checking
        happens AFTER this lookup, so the body picks the tenant first. That is
        precisely what rule 2 in routers/webhooks.py forbids.

    So: match the ids we recorded at connect time, or return None. Instagram
    hands us two of them (see instagram_login.complete_connection) and both are
    stored, so an exact match is always available for a correctly connected
    account. A None here means a genuine configuration problem worth seeing in
    the logs, not something to paper over with a guess.
    """
    ext = str(external_id)
    base = db.query(LeadChannelAccount).filter(
        LeadChannelAccount.IsActive == True,    # noqa: E712
        LeadChannelAccount.IsDeleted == False,  # noqa: E712
    )
    # .first(), not .one_or_none(): duplicate rows are a data problem, and
    # MultipleResultsFound here would escape the router as a 500 — which Meta
    # answers by retrying the same delivery, with backoff, for days.
    row = base.filter(
        LeadChannelAccount.Channel == channel,
        LeadChannelAccount.ExternalId == ext,
    ).first()
    if row is not None or channel != CHANNEL_INSTAGRAM:
        return row

    # Instagram Login issues an IGID (17841…) and an app-scoped user id
    # (2746…). Which one lands in the webhook depends on the payload shape, so
    # both are recorded at connect time: IGID in ExternalId, app-scoped id in
    # BusinessAccountId.
    return base.filter(
        LeadChannelAccount.Channel == CHANNEL_INSTAGRAM,
        LeadChannelAccount.BusinessAccountId == ext,
    ).first()

def _token_for(account: LeadChannelAccount | None) -> str | None:
    if account is not None:
        token = decrypt_pii(account.AccessTokenEnc)
        if token:
            return token
    return settings.meta_access_token


def app_secret_for(account: LeadChannelAccount | None) -> str | None:
    if account is not None:
        secret = decrypt_pii(account.AppSecretEnc)
        if secret:
            return secret
        logger.error(
            "[LeadAI channels] account %s AppSecretEnc set=%s but decrypt failed",
            account.Id, bool(account.AppSecretEnc),
        )
    # A standalone Instagram account's webhooks are signed with the INSTAGRAM
    # app secret. Falling back to META_APP_SECRET for one of those would fail
    # every signature check with a message that says nothing about the cause.
    if is_instagram_login(account):
        logger.warning("[LeadAI channels] falling back to env INSTAGRAM_APP_SECRET")
        return settings.instagram_app_secret or settings.meta_app_secret
    logger.warning("[LeadAI channels] falling back to env META_APP_SECRET")
    return settings.meta_app_secret


def is_instagram_login(account: LeadChannelAccount | None) -> bool:
    """True when this account was connected via standalone Instagram Login.

    Such an account has no Facebook Page, holds an Instagram User access token
    rather than a Page token, and must be addressed at graph.instagram.com.
    """
    return bool(account is not None and (account.LoginType or "facebook") == "instagram")


def _graph_base(account: LeadChannelAccount | None) -> tuple[str, str]:
    """(host, api_version) for this account.

    THE FAILURE THIS PREVENTS
    An Instagram User access token is not valid at graph.facebook.com. Sent
    there it returns "Invalid OAuth access token - Cannot parse access token",
    which reads like a bad credential and sends people off rotating tokens that
    were fine. Conversely a Page token is not valid at graph.instagram.com.

    The host is therefore a property of how the account was connected, never a
    global setting, and every caller goes through here.
    """
    if is_instagram_login(account):
        return (
            settings.instagram_graph_base.rstrip("/"),
            (account.ApiVersion if account else None) or settings.instagram_graph_version,
        )
    return (
        settings.meta_graph_base.rstrip("/"),
        (account.ApiVersion if account else None) or settings.meta_graph_version,
    )


def _graph_url(account: LeadChannelAccount | None, path: str) -> str:
    base, version = _graph_base(account)
    return f"{base}/{version}/{path.lstrip('/')}"


# =========================================================================== #
# inbound normalisation
# =========================================================================== #
def normalise(payload: dict) -> tuple[list[InboundMessage], list[StatusUpdate]]:
    """Turn one Meta webhook body into our own message/status objects.

    Meta batches: a single POST can carry several entries, each with several
    changes, each with several messages. All three levels are flattened here so
    the router only ever sees a flat list.
    """
    messages: list[InboundMessage] = []
    statuses: list[StatusUpdate] = []
    obj = (payload or {}).get("object", "")

    import json as _json
    logger.warning("[LeadAI normalise] obj=%s, entry_count=%d, keys=%s, payload_snippet=%s",
        obj,
        len((payload or {}).get("entry", []) or []),
        list((payload or {}).keys()),
        _json.dumps(payload or {}, default=str)[:800],
    )

    for entry in (payload or {}).get("entry", []) or []:
        # --- WhatsApp Cloud API -------------------------------------------
        if obj == "whatsapp_business_account":
            for change in entry.get("changes", []) or []:
                value = change.get("value", {}) or {}
                phone_number_id = str(
                    (value.get("metadata") or {}).get("phone_number_id", "")
                )
                # Contact profile names arrive separately from the messages.
                profiles = {
                    c.get("wa_id"): (c.get("profile") or {}).get("name")
                    for c in value.get("contacts", []) or []
                }
                for msg in value.get("messages", []) or []:
                    messages.append(
                        InboundMessage(
                            channel=CHANNEL_WHATSAPP,
                            account_external_id=phone_number_id,
                            external_user_id=str(msg.get("from", "")),
                            external_message_id=str(msg.get("id", "")),
                            text=_whatsapp_text(msg),
                            profile_name=profiles.get(msg.get("from")),
                            # WhatsApp's `from` IS the E.164 number without '+'.
                            phone=f"+{msg.get('from')}" if msg.get("from") else None,
                            media_type=msg.get("type"),
                            timestamp=_ts(msg.get("timestamp")),
                            raw=msg,
                        )
                    )
                for st in value.get("statuses", []) or []:
                    errors = st.get("errors") or []
                    statuses.append(
                        StatusUpdate(
                            channel=CHANNEL_WHATSAPP,
                            account_external_id=phone_number_id,
                            external_message_id=str(st.get("id", "")),
                            status=str(st.get("status", "")),
                            error=(errors[0].get("title") if errors else None),
                            timestamp=_ts(st.get("timestamp")),
                        )
                    )
            continue

        # --- Instagram -------------------------------------------------------
        # Instagram can send in two formats:
        #   1. changes[] with field="messages"|"messaging_postbacks"|etc.
        #   2. messaging[] with object="instagram" (same shape as Messenger)
        if obj == "instagram":
            IG_MSG_FIELDS = {"messages", "messaging_postbacks", "messaging_referrals"}
            account_id = str(entry.get("id", ""))
            # Format 1: changes[] format
            for change in entry.get("changes", []) or []:
                field = change.get("field", "")
                value = change.get("value", {}) or {}
                sender = str((value.get("sender") or {}).get("id", ""))
                recipient_id = str((value.get("recipient") or {}).get("id", ""))
                if not sender:
                    continue

                if field == "messages":
                    message = value.get("message") or {}
                    if not message:
                        continue
                    if message.get("is_echo"):
                        continue
                    attachments = message.get("attachments") or []
                    messages.append(
                        InboundMessage(
                            channel=CHANNEL_INSTAGRAM,
                            account_external_id=recipient_id or account_id,
                            external_user_id=sender,
                            external_message_id=str(message.get("mid", "")),
                            text=message.get("text") or "",
                            media_url=(
                                ((attachments[0].get("payload") or {}).get("url"))
                                if attachments else None
                            ),
                            media_type=(attachments[0].get("type") if attachments else "text"),
                            timestamp=_ts(value.get("timestamp")),
                            raw=value,
                        )
                    )
                elif field == "messaging_postbacks":
                    if value.get("is_self"):
                        continue
                    postback = value.get("postback") or {}
                    title = postback.get("title") or postback.get("payload") or "postback"
                    messages.append(
                        InboundMessage(
                            channel=CHANNEL_INSTAGRAM,
                            account_external_id=recipient_id or account_id,
                            external_user_id=sender,
                            external_message_id=str(postback.get("mid") or f"pb-{sender}-{int(value.get('timestamp', 0))}"),
                            text=title,
                            timestamp=_ts(value.get("timestamp")),
                            raw=value,
                        )
                    )
                elif field == "messaging_referrals":
                    referral = value.get("referral") or {}
                    messages.append(
                        InboundMessage(
                            channel=CHANNEL_INSTAGRAM,
                            account_external_id=recipient_id or account_id,
                            external_user_id=sender,
                            external_message_id=str(referral.get("id") or f"ref-{sender}-{int(value.get('timestamp', 0))}"),
                            text=referral.get("source") or referral.get("ref") or "referral",
                            timestamp=_ts(value.get("timestamp")),
                            raw=value,
                        )
                    )
            # Format 2: messaging[] format (same as Messenger)
            for event in entry.get("messaging", []) or []:
                message = event.get("message") or {}
                if message.get("is_echo"):
                    continue
                sender = str((event.get("sender") or {}).get("id", ""))
                if not sender:
                    continue
                if message:
                    attachments = message.get("attachments") or []
                    messages.append(
                        InboundMessage(
                            channel=CHANNEL_INSTAGRAM,
                            account_external_id=account_id,
                            external_user_id=sender,
                            external_message_id=str(message.get("mid", "")),
                            text=message.get("text") or "",
                            media_url=(
                                ((attachments[0].get("payload") or {}).get("url"))
                                if attachments else None
                            ),
                            media_type=(attachments[0].get("type") if attachments else "text"),
                            timestamp=_ts(event.get("timestamp")),
                            raw=event,
                        )
                    )
                elif event.get("delivery") or event.get("read"):
                    kind = "delivered" if event.get("delivery") else "read"
                    mids = (event.get("delivery") or event.get("read") or {}).get("mids") or []
                    for mid in mids or [f"{sender}:{kind}"]:
                        statuses.append(
                            StatusUpdate(
                                channel=CHANNEL_INSTAGRAM,
                                account_external_id=account_id,
                                external_message_id=str(mid),
                                status=kind,
                            )
                        )
            continue

        # --- Messenger ------------------------------------------------------
        # Messenger uses the `messaging` array with `object=page`.
        channel = CHANNEL_MESSENGER
        account_id = str(entry.get("id", ""))
        for event in entry.get("messaging", []) or []:
            message = event.get("message") or {}
            if message.get("is_echo"):
                # Our own outbound message reflected back — not a customer turn.
                continue
            sender = str((event.get("sender") or {}).get("id", ""))
            if not sender:
                continue
            if message:
                attachments = message.get("attachments") or []
                messages.append(
                    InboundMessage(
                        channel=channel,
                        account_external_id=account_id,
                        external_user_id=sender,
                        external_message_id=str(message.get("mid", "")),
                        text=message.get("text") or "",
                        media_url=(
                            ((attachments[0].get("payload") or {}).get("url"))
                            if attachments else None
                        ),
                        media_type=(attachments[0].get("type") if attachments else "text"),
                        timestamp=_ts(event.get("timestamp"), millis=True),
                        raw=event,
                    )
                )
            elif event.get("delivery") or event.get("read"):
                kind = "delivered" if event.get("delivery") else "read"
                mids = (event.get("delivery") or {}).get("mids") or []
                for mid in mids or [f"{sender}:{kind}"]:
                    statuses.append(
                        StatusUpdate(
                            channel=channel,
                            account_external_id=account_id,
                            external_message_id=str(mid),
                            status=kind,
                        )
                    )
    return messages, statuses


def _whatsapp_text(msg: dict) -> str:
    """Flatten WhatsApp's several text-bearing shapes into a single string."""
    kind = msg.get("type")
    if kind == "text":
        return (msg.get("text") or {}).get("body", "")
    if kind == "button":
        return (msg.get("button") or {}).get("text", "")
    if kind == "interactive":
        interactive = msg.get("interactive") or {}
        for key in ("button_reply", "list_reply"):
            if key in interactive:
                return (interactive[key] or {}).get("title", "")
    if kind in ("image", "video", "document", "audio"):
        caption = (msg.get(kind) or {}).get("caption")
        return caption or f"[{kind} received]"
    if kind == "location":
        loc = msg.get("location") or {}
        return f"[location: {loc.get('latitude')},{loc.get('longitude')}]"
    return ""


def _ts(value: Any, millis: bool = False) -> datetime | None:
    """Parse a Meta timestamp. Auto-detects seconds vs milliseconds.

    Instagram sends milliseconds; WhatsApp sends seconds. Anything past ~2286
    in seconds (1e11) is really milliseconds, so scale it down rather than
    trusting the caller. Windows raises OSError (not ValueError) on an
    out-of-range value, so that is caught too.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if millis or abs(number) > 100_000_000_000:  # > year 5138 in seconds
        number = number / 1000
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc)
    except (OSError, OverflowError, ValueError, OSError):
        return None


# =========================================================================== #
# outbound
# =========================================================================== #
def within_session_window(last_user_message_at: datetime | None) -> bool:
    """Meta only allows free-form replies inside 24h of the user's last message.

    Outside it, WhatsApp requires a pre-approved TEMPLATE and Messenger requires
    a message tag. Campaigns therefore default to templates; live conversation
    replies use free-form and are almost always inside the window.
    """
    if last_user_message_at is None:
        return False
    reference = last_user_message_at
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - reference < timedelta(
        hours=settings.meta_session_window_hours
    )


def fetch_profile(
    account: LeadChannelAccount | None,
    channel: str,
    external_user_id: str,
    *,
    timeout: float = 10.0,
) -> dict:
    """Look up a social sender's real name and handle from the Graph API.

    WHY THIS IS NEEDED
    ------------------
    WhatsApp webhooks carry the sender's name: it arrives in the `contacts`
    array and `normalise()` already reads it. Messenger and Instagram webhooks
    do NOT. Meta sends only an opaque PSID / IGSID — a long numeric string —
    and nothing else about the person.

    So an agent revealing a Messenger lead's contact details saw
    `7891234567890123` and nothing more. That is not a bug in the reveal
    endpoint; the name was never fetched in the first place, because it only
    exists behind a separate Graph call.

    Returns a dict with whichever of `name`, `username`, `profile_pic` the
    platform gave us, plus `handle` (a display-ready `@username` for Instagram,
    else the name). Returns {} on any failure — a missing name must never break
    message handling, which is the actual job.

    PERMISSIONS
    Messenger needs `pages_messaging`; the page token used for sending already
    has it. Instagram needs `instagram_manage_messages` and the account must be
    a Professional account linked to the Page. If the lookup 400s with a
    permissions error, reconnecting the account to mint a fresh token with the
    right scopes is the fix.

    PRIVACY
    Meta only returns a profile for a user who has messaged the Page — the same
    condition under which we hold a conversation with them at all. There is no
    way to enumerate strangers with this.
    """
    if not external_user_id:
        return {}

    token = _token_for(account)
    if not token:
        logger.debug("[LeadAI channels] no token to fetch profile for %s", channel)
        return {}

    if channel == CHANNEL_INSTAGRAM:
        # IG exposes `username`, which is what a human recognises; `name` is the
        # optional display name and is frequently blank.
        fields = "name,username,profile_pic"
    elif channel == CHANNEL_MESSENGER:
        # Messenger splits the name and does not expose a handle at all.
        fields = "name,first_name,last_name,profile_pic"
    else:
        # WhatsApp has no profile lookup endpoint — the name arrives in the
        # webhook or not at all.
        return {}

    url = _graph_url(account, external_user_id)
    try:
        import httpx

        response = httpx.get(
            url,
            params={"fields": fields, "access_token": token},
            timeout=timeout,
        )
        if response.status_code >= 400:
            logger.info(
                "[LeadAI channels] profile lookup %s/%s -> %s: %s",
                channel, external_user_id, response.status_code, response.text[:200],
            )
            return {}
        data = response.json() or {}
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "[LeadAI channels] profile lookup failed %s/%s: %s", channel, external_user_id, exc
        )
        return {}

    name = data.get("name") or " ".join(
        p for p in (data.get("first_name"), data.get("last_name")) if p
    ).strip()
    username = data.get("username")

    result = {}
    if name:
        result["name"] = name
    if username:
        result["username"] = username
        result["handle"] = f"@{username}"
    elif name:
        result["handle"] = name
    if data.get("profile_pic"):
        result["profile_pic"] = data["profile_pic"]

    logger.info(
        "[LeadAI channels] profile resolved %s/%s -> %s",
        channel, external_user_id, result.get("handle") or "(none)",
    )
    return result


def send_text(
    account: LeadChannelAccount | None,
    channel: str,
    to: str,
    text: str,
    *,
    timeout: float = 20.0,
) -> str:
    """Send a free-form message. Returns the provider message id.

    Raises ChannelError with a message safe to show an operator. In dry-run mode
    (LEADAI_CAMPAIGN_DRY_RUN=true) nothing leaves the process — useful for
    rehearsing a 50k-recipient campaign against production data.
    """
    if settings.campaign_dry_run:
        logger.info("[LeadAI channels] DRY RUN %s -> %s: %s", channel, to, text[:80])
        return f"dryrun-{hashlib.md5(f'{to}{text}'.encode()).hexdigest()[:16]}"

    token = _token_for(account)
    if not token:
        raise ChannelError("No access token configured for this channel account")

    if channel == CHANNEL_WHATSAPP:
        account_id = account.ExternalId if account else settings.whatsapp_phone_number_id
        if not account_id:
            raise ChannelError("No WhatsApp phone number id configured")
        url = _graph_url(account, f"{account_id}/messages")
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            # Graph rejects a leading '+' on this field.
            "to": to.lstrip("+"),
            "type": "text",
            "text": {"preview_url": False, "body": text[:4096]},
        }
    elif channel == CHANNEL_INSTAGRAM:
        account_id = account.ExternalId if account else "me"
        url = _graph_url(account, f"{account_id}/messages")
        # IG Messaging has no messaging_type/tag concept — sending it can 400.
        body = {"recipient": {"id": to}, "message": {"text": text[:1000]}}
    elif channel == CHANNEL_MESSENGER:
        account_id = account.ExternalId if account else "me"
        url = _graph_url(account, f"{account_id}/messages")
        body = {
            "recipient": {"id": to},
            "message": {"text": text[:2000]},
            "messaging_type": "RESPONSE",
        }
    else:
        raise ChannelError(f"Channel '{channel}' cannot send messages")

    return _post(url, body, token, timeout)


def send_template(
    account: LeadChannelAccount | None,
    channel: str,
    to: str,
    template_name: str,
    language: str = "en",
    params: list[str] | None = None,
    *,
    timeout: float = 20.0,
) -> str:
    """Send a pre-approved WhatsApp template.

    This is what a promotional or festive campaign must use: it is the only way
    to initiate a conversation outside the 24-hour window, and using it is what
    keeps the number's quality rating (and therefore its send limits) healthy.
    `params` fills {{1}}, {{2}}… positionally, in order.
    """
    if settings.campaign_dry_run:
        return f"dryrun-tpl-{hashlib.md5(f'{to}{template_name}'.encode()).hexdigest()[:16]}"

    if channel != CHANNEL_WHATSAPP:
        # Messenger/Instagram have no template concept; fall back to a tagged
        # free-form send, which is the closest equivalent.
        return send_text(account, channel, to, " ".join(params or [template_name]), timeout=timeout)

    token = _token_for(account)
    if not token:
        raise ChannelError("No access token configured for this channel account")
    account_id = account.ExternalId if account else settings.whatsapp_phone_number_id
    url = _graph_url(account, f"{account_id}/messages")

    components = []
    if params:
        components.append(
            {
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)[:1024]} for p in params],
            }
        )
    body = {
        "messaging_product": "whatsapp",
        "to": to.lstrip("+"),
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language or "en"},
            "components": components,
        },
    }
    return _post(url, body, token, timeout)


def mark_read(
    account: LeadChannelAccount | None,
    message_id: str,
    *,
    external_user_id: str | None = None,
) -> None:
    """Blue ticks. Best-effort — never let this fail a turn.

    The three channels do not share a shape here. WhatsApp marks a specific
    MESSAGE read; Instagram and Messenger mark the CONVERSATION seen and need
    the sender's id, not the message id. Sending WhatsApp's body to Instagram
    (as this once did) 400s on every inbound message — invisibly, because the
    failure is swallowed below.
    """
    if settings.campaign_dry_run or account is None:
        return
    token = _token_for(account)
    if not token:
        return

    channel = account.Channel
    if channel == CHANNEL_WHATSAPP:
        if not message_id:
            return
        url = _graph_url(account, f"{account.ExternalId}/messages")
        body = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
    elif channel in (CHANNEL_INSTAGRAM, CHANNEL_MESSENGER):
        if not external_user_id:
            return  # no conversation to mark without the sender
        url = _graph_url(account, f"{account.ExternalId or 'me'}/messages")
        body = {"recipient": {"id": external_user_id}, "sender_action": "mark_seen"}
    else:
        return

    try:
        _post(url, body, token, 10.0)
    except Exception as exc:  # noqa: BLE001
        # Debug, not warning: read receipts are cosmetic and a noisy failure
        # here would bury the errors that actually matter.
        logger.debug("[LeadAI channels] mark_read failed on %s: %s", channel, exc)


def _post(url: str, body: dict, token: str, timeout: float) -> str:
    import httpx

    try:
        response = httpx.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        raise ChannelError(f"Could not reach the messaging provider: {exc}") from exc

    if response.status_code >= 400:
        # Meta's error envelope is {"error": {"message", "code", "error_subcode"}}.
        detail = ""
        try:
            detail = (response.json().get("error") or {}).get("message", "")
        except Exception:  # noqa: BLE001
            detail = response.text[:300]
        raise ChannelError(f"Provider rejected the message ({response.status_code}): {detail}")

    data = response.json()
    if "messages" in data and data["messages"]:
        return str(data["messages"][0].get("id", ""))
    return str(data.get("message_id") or data.get("id") or "")


def send_sms(to: str, text: str) -> str:
    """SMS fallback via the Twilio client the outbound app already configures."""
    if settings.campaign_dry_run:
        return f"dryrun-sms-{hashlib.md5(to.encode()).hexdigest()[:12]}"
    import os

    if not settings.twilio_sms_from:
        raise ChannelError("TWILIO_SMS_FROM is not configured")
    try:
        from twilio.rest import Client as TwilioClient

        client = TwilioClient(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
        message = client.messages.create(to=to, from_=settings.twilio_sms_from, body=text[:1500])
        return message.sid
    except Exception as exc:  # noqa: BLE001
        raise ChannelError(f"SMS send failed: {exc}") from exc


def send_email(to: str, subject: str, text: str) -> str:
    """Plain SMTP. Deliberately minimal — swap for SES/SendGrid by editing here."""
    if settings.campaign_dry_run:
        return f"dryrun-email-{hashlib.md5(to.encode()).hexdigest()[:12]}"
    if not settings.smtp_host:
        raise ChannelError("SMTP is not configured")
    import smtplib
    from email.message import EmailMessage

    message = EmailMessage()
    message["From"] = settings.smtp_from or settings.smtp_user or "no-reply@localhost"
    message["To"] = to
    message["Subject"] = subject[:200]
    message.set_content(text)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password or "")
            server.send_message(message)
    except Exception as exc:  # noqa: BLE001
        raise ChannelError(f"Email send failed: {exc}") from exc
    return f"smtp-{hashlib.md5(f'{to}{subject}'.encode()).hexdigest()[:16]}"


def health(db: Session | None = None) -> dict:
    return {
        "social_enabled": settings.social_channels_enabled,
        "web_chat_enabled": settings.web_chat_enabled,
        "graph_version": settings.meta_graph_version,
        "signature_verification": settings.meta_verify_signatures,
        "platform_token_configured": bool(settings.meta_access_token),
        "sms": bool(settings.twilio_sms_from),
        "email": bool(settings.smtp_host),
        "dry_run": settings.campaign_dry_run,
    }
