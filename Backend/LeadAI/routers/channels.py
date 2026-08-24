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

import logging

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
)
from ..schemas import Ok
from ..security import encrypt_pii
from ..serializers_ext import channel_account_out
from ..services import channels as ch

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
