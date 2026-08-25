"""
Channel management — connect a WhatsApp number, a Facebook Page or an
Instagram account to a company.

The connect flow the frontend implements against this router:

    1. POST /channels                  -> create the account row, get back the
                                          webhook URL and the verify token
    2. (in Meta's dashboard)           -> paste that URL + token, subscribe to
                                          the `messages` field
    3. GET  /meta                      -> Meta calls the public handshake
    4. POST /channels/{id}/test        -> prove the token works both ways
    5. the number is live; inbound messages become leads

Credentials are write-only over the API: you can set an access token, you can
never read one back. `has_access_token` tells the UI whether to show "Connected"
or "Add token", which is all it needs.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import activity
from ..activity import A
from ..config import settings
from ..db import get_leadai_db
from ..models import LeadChannelAccount, LeadChannelIdentity, utcnow
from ..rbac import Principal, assert_owns, scoped
from ..schemas_ext import (
    ChannelAccountCreate,
    ChannelAccountOut,
    ChannelAccountUpdate,
    ChannelStatusOut,
    ChannelTestSend,
    FacebookCallbackIn,
    InstagramCallbackIn,
)
from ..schemas import Ok
from ..security import encrypt_pii
from ..serializers_ext import channel_account_out
from ..services import cache, channels as ch
from ..services import instagram_login as ig_login
from ..services import facebook_login as fb_login

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/channels", tags=["LeadAI • Channels"])


def _public_base(request: Request) -> str:
    """Best-effort external base URL, for showing the operator the webhook URL.

    Honours the reverse-proxy headers because behind nginx/ALB `request.base_url`
    reports the internal scheme and host, which would print an unusable URL.
    """
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if forwarded_host:
        return f"{forwarded_proto or request.url.scheme}://{forwarded_host}"
    return str(request.base_url).rstrip("/")


def _get(db: Session, account_id: str, client_id: str) -> LeadChannelAccount:
    row = db.get(LeadChannelAccount, account_id)
    if row is None or row.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel account not found")
    assert_owns(row.ClientId, client_id)
    return row


@router.get("", response_model=list[ChannelAccountOut], summary="List connected channels")
def list_accounts(
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("channel.read", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    rows = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.ClientId == client_id,
            LeadChannelAccount.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadChannelAccount.CreatedAt.desc())
        .all()
    )
    base = _public_base(request)
    return [channel_account_out(row, base) for row in rows]


@router.get("/status", response_model=ChannelStatusOut, summary="Channel health for the dashboard")
def channel_status(
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("channel.read", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    rows = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.ClientId == client_id,
            LeadChannelAccount.IsDeleted == False,  # noqa: E712
        )
        .all()
    )
    base = _public_base(request)
    return ChannelStatusOut(
        **ch.health(db), accounts=[channel_account_out(r, base) for r in rows]
    )


@router.post(
    "",
    response_model=ChannelAccountOut,
    status_code=status.HTTP_201_CREATED,
    summary="Connect a WhatsApp number / Facebook Page / Instagram account",
)
def create_account(
    payload: ChannelAccountCreate,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("channel.manage")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope

    # The (channel, external_id) pair is globally unique — the same WhatsApp
    # number cannot be claimed by two companies, or inbound routing would be
    # ambiguous and one tenant would read another's leads.
    clash = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.Channel == payload.channel,
            LeadChannelAccount.ExternalId == payload.external_id,
            LeadChannelAccount.IsDeleted == False,  # noqa: E712
        )
        .first()
    )
    if clash is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That account is already connected"
            + (" to this company." if clash.ClientId == client_id else " to another company."),
        )

    row = LeadChannelAccount(
        ClientId=client_id,
        Channel=payload.channel,
        Provider="meta",
        Name=payload.name,
        ExternalId=payload.external_id,
        BusinessAccountId=payload.business_account_id,
        DisplayNumber=payload.display_number,
        AccessTokenEnc=encrypt_pii(payload.access_token),
        AppSecretEnc=encrypt_pii(payload.app_secret),
        VerifyToken=payload.verify_token or settings.meta_verify_token,
        ApiVersion=payload.api_version,
        AutoReply=payload.auto_reply,
        ScriptId=payload.script_id,
        DefaultLanguage=payload.default_language,
        CreatedBy=principal.email,
    )
    db.add(row)
    db.flush()
    activity.log_principal(
        db,
        principal,
        action=A.CHANNEL_CONNECTED,
        client_id=client_id,
        entity_type="channel_account",
        entity_id=row.Id,
        message=f"Connected {payload.channel} account '{payload.name}'",
        meta={"channel": payload.channel, "external_id": payload.external_id},
        request=request,
    )
    db.commit()
    return channel_account_out(row, _public_base(request))


@router.patch("/{account_id}", response_model=ChannelAccountOut, summary="Update a channel")
def update_account(
    account_id: str,
    payload: ChannelAccountUpdate,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("channel.manage")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    row = _get(db, account_id, client_id)

    data = payload.model_dump(exclude_unset=True)
    if "access_token" in data and data["access_token"]:
        row.AccessTokenEnc = encrypt_pii(data.pop("access_token"))
    if "app_secret" in data and data["app_secret"]:
        row.AppSecretEnc = encrypt_pii(data.pop("app_secret"))
    mapping = {
        "name": "Name",
        "verify_token": "VerifyToken",
        "display_number": "DisplayNumber",
        "api_version": "ApiVersion",
        "is_active": "IsActive",
        "auto_reply": "AutoReply",
        "script_id": "ScriptId",
        "default_language": "DefaultLanguage",
    }
    for key, column in mapping.items():
        if key in data and data[key] is not None:
            setattr(row, column, data[key])
    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()

    activity.log_principal(
        db, principal, action=A.CHANNEL_UPDATED, client_id=client_id,
        entity_type="channel_account", entity_id=row.Id,
        message=f"Updated channel '{row.Name}'",
        meta={"fields": list(data.keys())}, request=request,
    )
    db.commit()
    return channel_account_out(row, _public_base(request))


@router.delete("/{account_id}", response_model=Ok, summary="Disconnect a channel")
def delete_account(
    account_id: str,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("channel.manage")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    row = _get(db, account_id, client_id)
    # Soft delete: existing conversations reference this account, and the audit
    # trail must still resolve the name.
    row.IsDeleted = True
    row.IsActive = False
    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()
    activity.log_principal(
        db, principal, action=A.CHANNEL_DISCONNECTED, client_id=client_id,
        entity_type="channel_account", entity_id=row.Id,
        message=f"Disconnected channel '{row.Name}'", request=request,
    )
    db.commit()
    return Ok(message="Channel disconnected. Existing conversations are preserved.")


@router.post("/{account_id}/test", response_model=Ok, summary="Send a test message")
def test_send(
    account_id: str,
    payload: ChannelTestSend,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("channel.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Prove the credentials work before a campaign depends on them.

    Note the WhatsApp 24-hour rule: a free-form test only reaches a number that
    has messaged you in the last day. Use `template_name` otherwise, or the send
    will be rejected by Meta with a 131047.
    """
    principal, client_id = scope
    row = _get(db, account_id, client_id)
    try:
        if payload.template_name:
            message_id = ch.send_template(
                row, row.Channel, payload.to, payload.template_name,
                payload.template_language, payload.template_params,
            )
        else:
            message_id = ch.send_text(row, row.Channel, payload.to, payload.message)
        row.LastOutboundAt = utcnow()
        row.LastError = None
        db.commit()
        return Ok(message=f"Sent. Provider message id: {message_id}")
    except ch.ChannelError as exc:
        row.LastError = str(exc)[:500]
        row.LastErrorAt = utcnow()
        activity.log_principal(
            db, principal, action=A.CHANNEL_SEND_FAILED, client_id=client_id,
            entity_type="channel_account", entity_id=row.Id,
            message=f"Test send failed: {exc}", log_type="Error", request=request,
        )
        db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


# =========================================================================== #
# Standalone Instagram (Instagram API with Instagram Login)
# =========================================================================== #
# Connects an Instagram professional account that has NO linked Facebook Page.
# See LeadAI/services/instagram_login.py for why this is a separate flow rather
# than an option on the existing one.

@router.get(
    "/instagram/connect",
    summary="Start connecting a standalone Instagram account (no Facebook Page)",
)
def instagram_connect(
    request: Request,
    publishing: bool = False,
    scope: tuple[Principal, str] = Depends(scoped("channel.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Returns the URL to send the company to.

    The caller redirects the browser there; Instagram sends the person back to
    INSTAGRAM_REDIRECT_URI with `code` and `state`.

    `state` binds the callback to this company. It is returned here and stored
    server-side; the callback rejects any state it did not issue. Without that
    check, anyone who can reach the callback URL could attach an Instagram
    account of their choosing to a tenant.
    """
    principal, client_id = scope
    try:
        url, state = ig_login.authorize_url(publishing=publishing)
    except ig_login.InstagramLoginError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # Short TTL: this is a browser round-trip, not a session. Ten minutes is
    # generous for a consent screen and short enough that a leaked state is
    # useless by the time anyone finds it.
    cache.set_value(f"leadai:iglogin:{state}", client_id, ttl=600)

    activity.log_principal(
        db,
        principal,
        action=A.CHANNEL_CONNECTED,
        client_id=client_id,
        entity_type="channel",
        message="Started standalone Instagram connection",
        request=request,
    )
    db.commit()

    return {
        "authorize_url": url,
        "state": state,
        "expires_in": 600,
        "note": (
            "Open this URL in a browser. The Instagram account must be a "
            "professional (Business or Creator) account."
        ),
    }


@router.get(
    "/facebook/connect",
    summary="Start connecting a standalone Facebook account (no Instagram Page)",
)
def facebook_connect(
    request: Request,
    publishing: bool = False,
    scope: tuple[Principal, str] = Depends(scoped("channel.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Returns the URL to send the company to.

    The caller redirects the browser there; Facebook sends the person back to
    FACEBOOK_REDIRECT_URI with `code` and `state`.

    `state` binds the callback to this company. It is returned here and stored
    server-side; the callback rejects any state it did not issue. Without that
    check, anyone who can reach the callback URL could attach a Facebook
    account of their choosing to a tenant.
    """
    principal, client_id = scope
    try:
        url, state = fb_login.authorize_url(publishing=publishing)
    except fb_login.FacebookLoginError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # Short TTL: this is a browser round-trip, not a session. Ten minutes is
    # generous for a consent screen and short enough that a leaked state is
    # useless by the time anyone finds it.
    cache.set_value(f"leadai:fblogin:{state}", client_id, ttl=600)

    activity.log_principal(
        db,
        principal,
        action=A.CHANNEL_CONNECTED,
        client_id=client_id,
        entity_type="channel",
        message="Started standalone Facebook connection",
        request=request,
    )
    db.commit()

    return {
        "authorize_url": url,
        "state": state,
        "expires_in": 600,
        "note": (
            "Open this URL in a browser. The Instagram account must be a "
            "professional (Business or Creator) account."
        ),
    }

@router.post("/facebook/callback", summary="Exchange the code and list Pages")
def facebook_callback(payload: FacebookCallbackIn, db: Session = Depends(get_leadai_db)):
    client_id = cache.get_value(f"leadai:fblogin:{payload.state}")
    if not client_id:
        raise HTTPException(400, "That connection link has expired or was already used.")
    cache.set_value(f"leadai:fblogin:{payload.state}", "", ttl=1)   # single-use

    result = fb_login.complete_connection(payload.code)

    # Page tokens are credentials. Hold them server-side under a short-lived
    # selection token and return only display fields — never send a Page token
    # to the browser so it can be posted back.
    selection = secrets.token_urlsafe(24)
    cache.set_value(
        f"leadai:fbselect:{selection}",
        json.dumps({"client_id": client_id, "pages": result["pages"]}),
        ttl=900,
    )
    return {
        "selection": selection,
        "pages": [
            {"page_id": p["page_id"], "name": p["name"], "category": p["category"],
             "instagram_username": (p["instagram"] or {}).get("username"),
             "instagram_id": (p["instagram"] or {}).get("id")}
            for p in result["pages"]
        ],
    }

@router.post(
    "/instagram/callback",
    response_model=ChannelAccountOut,
    summary="Finish connecting a standalone Instagram account",
)
def instagram_callback(
    payload: InstagramCallbackIn,
    request: Request,
    db: Session = Depends(get_leadai_db),
):
    """Exchange the authorization code and store the connected account.

    Deliberately NOT scoped through `scoped(...)`: the company is recovered from
    the `state` issued by /instagram/connect, because the browser returning from
    Instagram may not be carrying a staff token. The state IS the authorisation,
    which is why it is single-use and deleted the moment it is consumed.
    """
    client_id = cache.get_value(f"leadai:iglogin:{payload.state}")
    if not client_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That connection link has expired or was already used. Start again from "
            "Channels > Connect Instagram.",
        )
    # Single-use: a replayed callback must not be able to rebind an account.
    # The cache exposes no delete, so the key is overwritten with a 1-second TTL
    # — the same effect, and it works identically on the Redis and in-process
    # backends rather than depending on which one is configured.
    cache.set_value(f"leadai:iglogin:{payload.state}", "", ttl=1)

    try:
        result = ig_login.complete_connection(payload.code)
    except ig_login.InstagramLoginError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    external_id = result["external_id"]
    username = result.get("username") or external_id

    existing = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.Channel == "instagram",
            LeadChannelAccount.ExternalId == external_id,
            LeadChannelAccount.IsDeleted == False,  # noqa: E712
        )
        .one_or_none()
    )

    if existing is not None and existing.ClientId != client_id:
        # One Instagram account cannot serve two tenants: inbound webhooks are
        # routed by ExternalId, so allowing this would send one company's DMs to
        # another company's inbox.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"@{username} is already connected to a different company.",
        )

    account = existing or LeadChannelAccount(
        ClientId=client_id,
        Channel="instagram",
        Provider="meta",
        ExternalId=external_id,
        CreatedBy="instagram-login",
    )

    account.LoginType = ig_login.LOGIN_TYPE_INSTAGRAM
    account.AppId = settings.instagram_app_id
    # Instagram webhooks may carry the IGID (stored as ExternalId above) OR the
    # app-scoped user id from the OAuth step. Recording the second one here is
    # what lets channels.find_account match exactly rather than guess — and a
    # guess is unusable once more than one company has connected Instagram.
    scoped_user_id = result.get("scoped_user_id")
    if scoped_user_id and scoped_user_id != external_id:
        account.BusinessAccountId = scoped_user_id
    account.Name = f"@{username}"
    account.AccessTokenEnc = encrypt_pii(result["access_token"])
    account.AppSecretEnc = encrypt_pii(settings.instagram_app_secret)
    account.VerifyToken = settings.instagram_verify_token
    account.ApiVersion = settings.instagram_graph_version
    account.TokenExpiresAt = result.get("expires_at")
    account.TokenRefreshedAt = utcnow()
    account.IsActive = True
    account.LastError = None
    account.LastErrorAt = None
    account.MetaJson = {
        "username": username,
        "account_type": result.get("account_type"),
        "profile_picture_url": result.get("profile_picture_url"),
        "permissions": result.get("permissions"),
        "login": "instagram",
    }
    if existing is None:
        db.add(account)
    db.flush()

    activity.log(
        db,
        action=A.CHANNEL_CONNECTED,
        client_id=client_id,
        actor_email="instagram-login",
        entity_type="channel",
        entity_id=account.Id,
        message=f"Connected standalone Instagram @{username}",
        meta={"external_id": external_id, "login_type": "instagram"},
        request=request,
    )
    db.commit()
    db.refresh(account)

    logger.info(
        "[LeadAI ig-login] connected @%s (%s) to company %s", username, external_id, client_id
    )
    return channel_account_out(account, _public_base(request))


@router.post(
    "/{account_id}/refresh-token",
    response_model=ChannelAccountOut,
    summary="Refresh a standalone Instagram token",
)
def instagram_refresh_token(
    account_id: str,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("channel.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Extend the 60-day token by another 60 days.

    Normally the background job handles this. Exposed manually because the one
    failure mode that cannot be automated away is a token that already expired,
    and an operator needs to be able to try before concluding they must
    re-authorise.
    """
    principal, client_id = scope
    account = _get(db, account_id, client_id)

    if not ch.is_instagram_login(account):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This account uses Facebook Login. Page tokens do not refresh this way — "
            "reconnect it instead.",
        )

    token = ch._token_for(account)
    if not token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No token stored for this account")

    try:
        refreshed = ig_login.refresh_long_lived(token)
    except ig_login.InstagramLoginError as exc:
        account.LastError = str(exc)[:500]
        account.LastErrorAt = utcnow()
        db.commit()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{exc} If the token has already expired there is no way to refresh it — "
            "reconnect the account from Channels.",
        ) from exc

    expires_in = int(refreshed.get("expires_in") or 0)
    account.AccessTokenEnc = encrypt_pii(refreshed["access_token"])
    account.TokenExpiresAt = (
        utcnow() + timedelta(seconds=expires_in) if expires_in else None
    )
    account.TokenRefreshedAt = utcnow()
    account.LastError = None
    account.LastErrorAt = None

    activity.log_principal(
        db,
        principal,
        action=A.CHANNEL_CONNECTED,
        client_id=client_id,
        entity_type="channel",
        entity_id=account.Id,
        message=f"Refreshed Instagram token for {account.Name}",
        request=request,
    )
    db.commit()
    db.refresh(account)
    return channel_account_out(account, _public_base(request))


@router.get("/{account_id}/contacts", summary="Known contacts on this channel")
def list_identities(
    account_id: str,
    page: int = 1,
    page_size: int = 50,
    scope: tuple[Principal, str] = Depends(scoped("channel.read", "lead.read.all")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    _get(db, account_id, client_id)
    page = max(1, page)
    page_size = min(200, max(1, page_size))
    query = db.query(LeadChannelIdentity).filter(
        LeadChannelIdentity.ChannelAccountId == account_id,
        LeadChannelIdentity.IsDeleted == False,  # noqa: E712
    )
    total = query.count()
    rows = (
        query.order_by(LeadChannelIdentity.LastUserMessageAt.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total_items": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": r.Id,
                "profile_name": r.ProfileName,
                # The external id IS the phone number on WhatsApp, so it is
                # masked exactly like any other contact detail.
                "external_user_id": f"***{r.ExternalUserId[-4:]}" if r.ExternalUserId else None,
                "conversation_id": r.ConversationId,
                "customer_id": r.CustomerId,
                "opted_out": bool(r.OptedOut),
                "last_user_message_at": r.LastUserMessageAt,
                "in_session_window": ch.within_session_window(r.LastUserMessageAt),
            }
            for r in rows
        ],
    }
