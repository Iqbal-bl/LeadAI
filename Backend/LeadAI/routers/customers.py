"""
Customers — the CRM module for leads that converted.

This is where a lead stops being a conversation and becomes a relationship.
The endpoints fall into four groups:

    lifecycle   POST /customers/convert, PATCH /customers/{id}
    browsing    GET  /customers (filter by stage, owner, tag, follow-up due)
    outreach    POST /customers/{id}/message   — one person, one message
                POST /customers/greetings/preview — who has a birthday this week
    history     GET/POST /customers/{id}/notes

Bulk outreach is NOT here — that is a campaign with `audience_type=customers`,
because anything sent to more than one person needs throttling, quiet hours,
opt-out enforcement and a per-recipient audit trail. The single-message endpoint
below is for the genuine one-off, and it still checks consent.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import activity
from ..activity import A
from ..db import get_leadai_db
from ..models import (
    Lead,
    LeadAccount,
    LeadAccountNote,
    LeadChannelAccount,
    LeadConversation,
    utcnow,
)
from ..rbac import Principal, assert_owns, scoped
from ..schemas import Ok
from ..schemas_ext import (
    AccountCreate,
    AccountListOut,
    AccountNoteCreate,
    AccountNoteOut,
    AccountOut,
    AccountUpdate,
    ConvertLeadRequest,
    OccasionOut,
    QuickMessageRequest,
)
from ..security import decrypt_pii, encrypt_pii, mask_email, mask_phone, phone_fingerprint
from ..serializers_ext import account_note_out, account_out
from ..services import channels as ch, crm

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/customers", tags=["LeadAI • Customers (CRM)"])


def _account(db: Session, account_id: str, client_id: str) -> LeadAccount:
    row = db.get(LeadAccount, account_id)
    if row is None or row.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    assert_owns(row.ClientId, client_id)
    return row


@router.get("", response_model=AccountListOut, summary="List customers")
def list_customers(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    stage: str | None = None,
    account_status: str | None = Query(default=None, alias="status"),
    owner_email: str | None = None,
    tag: str | None = None,
    search: str | None = Query(default=None, description="Matches name or company"),
    follow_up_due: bool = False,
    scope: tuple[Principal, str] = Depends(scoped("customer.read", "customer.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Paginated, filtered list.

    No decryption happens here — the masked phone/email were computed once at
    write time. Listing 200 customers therefore costs zero crypto operations.
    """
    principal, client_id = scope
    query = db.query(LeadAccount).filter(
        LeadAccount.ClientId == client_id,
        LeadAccount.IsDeleted == False,  # noqa: E712
    )
    # An employee sees the customers they own, unless they can read everything.
    if not principal.can("customer.read.all") and not principal.can("lead.read.all"):
        query = query.filter(LeadAccount.OwnerEmail == principal.email)

    if stage:
        query = query.filter(LeadAccount.Stage == stage)
    if account_status:
        query = query.filter(LeadAccount.Status == account_status)
    if owner_email:
        query = query.filter(LeadAccount.OwnerEmail == owner_email)
    if tag:
        query = query.filter(LeadAccount.Tags.like(f"%{tag}%"))
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            or_(LeadAccount.DisplayName.like(pattern), LeadAccount.CompanyName.like(pattern))
        )
    if follow_up_due:
        query = query.filter(
            LeadAccount.NextFollowUpAt != None,  # noqa: E711
            LeadAccount.NextFollowUpAt <= datetime.now(timezone.utc).replace(tzinfo=None),
        )

    total = query.count()
    rows = (
        query.order_by(LeadAccount.CreatedAt.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return AccountListOut(
        total_items=total, page=page, page_size=page_size,
        items=[account_out(r) for r in rows],
    )


@router.post(
    "",
    response_model=AccountOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a customer manually",
)
def create_customer(
    payload: AccountCreate,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("customer.manage")),
    db: Session = Depends(get_leadai_db),
):
    """For importing an existing book of business that never went through a lead."""
    principal, client_id = scope
    row = crm.create_account(
        db,
        client_id,
        display_name=payload.display_name,
        phone=payload.phone,
        email=payload.email,
        whatsapp=payload.whatsapp,
        company_name=payload.company_name,
        stage=payload.stage,
        owner_email=payload.owner_email or principal.email,
        product=payload.product,
        value=payload.value,
        source=payload.source or "manual",
        tags=payload.tags,
        fields=payload.fields,
        actor=principal.email,
    )
    row.Currency = payload.currency
    row.Notes = payload.notes
    row.Birthday = payload.birthday
    row.Anniversary = payload.anniversary
    db.commit()
    return account_out(row)


@router.post("/convert", response_model=AccountOut, summary="Convert a lead into a customer")
def convert(
    payload: ConvertLeadRequest,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("customer.manage", "lead.status")),
    db: Session = Depends(get_leadai_db),
):
    """Promote a qualified conversation. Idempotent — safe to double-click."""
    principal, client_id = scope
    conversation = db.get(LeadConversation, payload.conversation_id)
    if conversation is None or conversation.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    assert_owns(conversation.ClientId, client_id)

    lead = db.query(Lead).filter(Lead.ConversationId == conversation.Id).one_or_none()
    if lead is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This conversation has no lead record.")

    account = crm.convert_lead(
        db, client_id, conversation, lead,
        owner_email=payload.owner_email or principal.email,
        actor=principal.email, stage=payload.stage, value=payload.value,
    )
    if payload.notes:
        crm.add_note(db, client_id, account, body=payload.notes, author_email=principal.email)
    db.commit()
    return account_out(account)


# ---------------------------------------------------------------------------
# NOTE ON ROUTE ORDER
# These literal paths MUST be declared before `/{account_id}`. FastAPI matches
# in declaration order, so if `/{account_id}` came first it would happily treat
# "greetings" as an account id and return 404 for a perfectly good endpoint.
# ---------------------------------------------------------------------------
@router.get(
    "/greetings/upcoming",
    response_model=list[OccasionOut],
    summary="Birthdays and anniversaries coming up",
)
def upcoming_greetings(
    within_days: int = Query(default=7, ge=0, le=90),
    scope: tuple[Principal, str] = Depends(scoped("customer.read", "customer.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Feeds the "send festive wishes" screen.

    Turn the result into a campaign by creating one with
    audience_type=customers and audience_filter={"account_ids": [...]}.
    """
    _, client_id = scope
    return [OccasionOut(**item) for item in crm.upcoming_occasions(db, client_id, within_days)]


@router.get("/stats/summary", summary="CRM dashboard tiles")
def customer_stats(
    scope: tuple[Principal, str] = Depends(scoped("customer.read", "analytics.read")),
    db: Session = Depends(get_leadai_db),
):
    from sqlalchemy import func

    _, client_id = scope
    base = db.query(LeadAccount).filter(
        LeadAccount.ClientId == client_id,
        LeadAccount.IsDeleted == False,  # noqa: E712
    )
    by_stage = dict(
        db.query(LeadAccount.Stage, func.count(LeadAccount.Id))
        .filter(LeadAccount.ClientId == client_id, LeadAccount.IsDeleted == False)  # noqa: E712
        .group_by(LeadAccount.Stage)
        .all()
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return {
        "total": base.count(),
        "by_stage": by_stage,
        "total_value": float(
            db.query(func.coalesce(func.sum(LeadAccount.Value), 0.0))
            .filter(LeadAccount.ClientId == client_id, LeadAccount.IsDeleted == False)  # noqa: E712
            .scalar()
            or 0
        ),
        "do_not_disturb": base.filter(LeadAccount.DoNotDisturb == True).count(),  # noqa: E712
        "follow_ups_due": base.filter(
            LeadAccount.NextFollowUpAt != None,  # noqa: E711
            LeadAccount.NextFollowUpAt <= now,
        ).count(),
        "new_this_month": base.filter(
            LeadAccount.CreatedAt >= now - timedelta(days=30)
        ).count(),
    }


@router.get("/{account_id}", response_model=AccountOut, summary="Customer detail")
def get_customer(
    account_id: str,
    scope: tuple[Principal, str] = Depends(scoped("customer.read", "customer.manage")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    return account_out(_account(db, account_id, client_id))


@router.patch("/{account_id}", response_model=AccountOut, summary="Update a customer")
def update_customer(
    account_id: str,
    payload: AccountUpdate,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("customer.manage")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    row = _account(db, account_id, client_id)
    previous_stage = row.Stage

    mapping = {
        "display_name": "DisplayName", "company_name": "CompanyName", "stage": "Stage",
        "status": "Status", "owner_email": "OwnerEmail", "product": "Product",
        "value": "Value", "tags": "Tags", "notes": "Notes",
        "next_follow_up_at": "NextFollowUpAt", "birthday": "Birthday",
        "anniversary": "Anniversary", "opt_in_whatsapp": "OptInWhatsApp",
        "opt_in_sms": "OptInSms", "opt_in_email": "OptInEmail",
        "opt_in_call": "OptInCall", "do_not_disturb": "DoNotDisturb",
        "fields": "FieldsJson",
    }
    data = payload.model_dump(exclude_unset=True)
    for key, column in mapping.items():
        if key in data and data[key] is not None:
            setattr(row, column, data[key])
    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()

    if "stage" in data and data["stage"] != previous_stage:
        crm.add_note(
            db, client_id, row,
            body=f"Stage changed from {previous_stage} to {row.Stage}",
            note_type="stage_change", author_email=principal.email,
        )
        activity.log_principal(
            db, principal, action=A.ACCOUNT_STAGE_CHANGED, client_id=client_id,
            entity_type="account", entity_id=row.Id,
            message=f"{row.DisplayName}: {previous_stage} -> {row.Stage}", request=request,
        )
    else:
        activity.log_principal(
            db, principal, action=A.ACCOUNT_UPDATED, client_id=client_id,
            entity_type="account", entity_id=row.Id,
            message=f"Updated customer {row.DisplayName}",
            meta={"fields": list(data)}, request=request,
        )
    db.commit()
    return account_out(row)


@router.delete("/{account_id}", response_model=Ok, summary="Delete a customer")
def delete_customer(
    account_id: str,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("customer.manage")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    row = _account(db, account_id, client_id)
    row.IsDeleted = True
    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()
    activity.log_principal(
        db, principal, action=A.ACCOUNT_DELETED, client_id=client_id,
        entity_type="account", entity_id=row.Id,
        message=f"Deleted customer {row.DisplayName}", log_type="Warning", request=request,
    )
    db.commit()
    return Ok(message="Customer deleted")


# --------------------------------------------------------------------------- #
# contact reveal
# --------------------------------------------------------------------------- #
@router.post("/{account_id}/reveal", summary="Reveal real contact details (audited)")
def reveal_contact(
    account_id: str,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("lead.reveal_pii")),
    db: Session = Depends(get_leadai_db),
):
    """The only path from encrypted storage to a readable phone number.

    Separate permission, separate endpoint, always logged — so "who looked up
    this customer's number and when" has an answer.
    """
    principal, client_id = scope
    row = _account(db, account_id, client_id)
    activity.log_principal(
        db, principal, action=A.PII_REVEALED, client_id=client_id,
        entity_type="account", entity_id=row.Id,
        message=f"Revealed contact details for {row.DisplayName}",
        log_type="Security", request=request,
    )
    db.commit()
    return {
        "id": row.Id,
        "display_name": row.DisplayName,
        "phone": decrypt_pii(row.PhoneEnc),
        "email": decrypt_pii(row.EmailEnc),
        "whatsapp": decrypt_pii(row.WhatsAppEnc),
    }


# --------------------------------------------------------------------------- #
# notes / timeline
# --------------------------------------------------------------------------- #
@router.get("/{account_id}/notes", response_model=list[AccountNoteOut], summary="Timeline")
def list_notes(
    account_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    scope: tuple[Principal, str] = Depends(scoped("customer.read", "customer.manage")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    _account(db, account_id, client_id)
    rows = (
        db.query(LeadAccountNote)
        .filter(
            LeadAccountNote.AccountId == account_id,
            LeadAccountNote.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadAccountNote.CreatedAt.desc())
        .limit(limit)
        .all()
    )
    return [account_note_out(r) for r in rows]


@router.post(
    "/{account_id}/notes",
    response_model=AccountNoteOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a note",
)
def add_note(
    account_id: str,
    payload: AccountNoteCreate,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("customer.manage", "customer.read")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    row = _account(db, account_id, client_id)
    note = crm.add_note(
        db, client_id, row,
        body=payload.body, note_type=payload.note_type, author_email=principal.email,
    )
    activity.log_principal(
        db, principal, action=A.ACCOUNT_NOTE_ADDED, client_id=client_id,
        entity_type="account", entity_id=row.Id,
        message=f"Note added to {row.DisplayName}", request=request,
    )
    db.commit()
    return account_note_out(note)


# --------------------------------------------------------------------------- #
# outreach
# --------------------------------------------------------------------------- #
@router.post("/{account_id}/message", response_model=Ok, summary="Send this customer one message")
def send_one(
    account_id: str,
    payload: QuickMessageRequest,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("campaign.send", "lead.reply")),
    db: Session = Depends(get_leadai_db),
):
    """Single-recipient outreach — a cold nudge, a festive wish, a follow-up.

    Consent is enforced here exactly as it is in a campaign. A one-off send is
    still a send, and "it was just one message" is not a defence to a DND
    complaint.
    """
    principal, client_id = scope
    row = _account(db, account_id, client_id)

    allowed, reason = crm.can_contact(row, payload.channel)
    if not allowed:
        raise HTTPException(status.HTTP_409_CONFLICT, reason or "Customer cannot be contacted")

    phone = decrypt_pii(row.WhatsAppEnc) or decrypt_pii(row.PhoneEnc)
    email = decrypt_pii(row.EmailEnc)

    account_row = None
    if payload.channel_account_id:
        account_row = db.get(LeadChannelAccount, payload.channel_account_id)
        if account_row is None or account_row.ClientId != client_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel account not found")
    elif payload.channel in ("whatsapp", "messenger", "instagram"):
        account_row = (
            db.query(LeadChannelAccount)
            .filter(
                LeadChannelAccount.ClientId == client_id,
                LeadChannelAccount.Channel == payload.channel,
                LeadChannelAccount.IsActive == True,   # noqa: E712
                LeadChannelAccount.IsDeleted == False,  # noqa: E712
            )
            .first()
        )
        if account_row is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"No {payload.channel} account is connected. Connect one first.",
            )

    body = payload.message or ""
    try:
        if payload.channel == "email":
            if not email:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "No email address on file")
            message_id = ch.send_email(email, "A note from us", body)
        elif payload.channel == "sms":
            if not phone:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "No phone number on file")
            message_id = ch.send_sms(phone, body)
        elif payload.template_name:
            if not phone:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "No phone number on file")
            message_id = ch.send_template(
                account_row, payload.channel, phone.lstrip("+"),
                payload.template_name, payload.template_language, payload.template_params,
            )
        else:
            if not phone:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "No phone number on file")
            message_id = ch.send_text(account_row, payload.channel, phone.lstrip("+"), body)
    except ch.ChannelError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    crm.mark_contacted(
        db, row, payload.channel,
        note=f"{payload.channel} message sent by {principal.email}: {body[:200]}",
    )
    activity.log_principal(
        db, principal, action=A.ACCOUNT_CONTACTED, client_id=client_id,
        entity_type="account", entity_id=row.Id,
        message=f"Sent a {payload.channel} message to {row.DisplayName}",
        meta={"channel": payload.channel, "template": payload.template_name},
        request=request,
    )
    db.commit()
    return Ok(message=f"Sent. Provider message id: {message_id}")
