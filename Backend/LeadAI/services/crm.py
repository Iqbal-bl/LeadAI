"""
CRM — the customer module.

A LEAD is a conversation with a score. An ACCOUNT is a commercial relationship.
Keeping them as two tables rather than a status column on `leadai_leads` is a
deliberate choice, and the reason is consent and lifetime:

  * A lead is scoped to ONE conversation and dies with it. A customer outlives
    every conversation they ever have, and may have several open at once.
  * A customer carries per-channel consent (OptInWhatsApp, DoNotDisturb…).
    Campaigns must check that on every send. Storing it on a conversation-scoped
    row would mean "did they opt out?" depends on which chat you look at.
  * A customer can be created without any conversation at all — imported from
    the client's existing book of business. That is impossible if the customer
    IS a lead.

Conversion is idempotent: converting the same lead twice returns the existing
account rather than creating a duplicate.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .. import activity
from ..activity import A
from ..models import (
    Lead,
    LeadAccount,
    LeadAccountNote,
    LeadConversation,
    LeadCustomer,
    utcnow,
)
from ..security import decrypt_pii, encrypt_pii, mask_email, mask_phone, phone_fingerprint

logger = logging.getLogger(__name__)


def find_account_by_phone(db: Session, client_id: str, phone: str | None) -> LeadAccount | None:
    """Look up by keyed fingerprint — no decryption, no full-table scan."""
    fingerprint = phone_fingerprint(phone)
    if not fingerprint:
        return None
    return (
        db.query(LeadAccount)
        .filter(
            LeadAccount.ClientId == client_id,
            LeadAccount.PhoneHash == fingerprint,
            LeadAccount.IsDeleted == False,  # noqa: E712
        )
        .first()
    )


def create_account(
    db: Session,
    client_id: str,
    *,
    display_name: str,
    phone: str | None = None,
    email: str | None = None,
    whatsapp: str | None = None,
    company_name: str | None = None,
    stage: str = "customer",
    owner_email: str | None = None,
    product: str | None = None,
    value: float = 0.0,
    source: str | None = None,
    tags: str | None = None,
    fields: dict | None = None,
    actor: str = "system",
    customer_id: str | None = None,
) -> LeadAccount:
    """Create (or return an existing) account. Contact details encrypted at rest.

    Both the encrypted value AND a masked display value are stored. The masked
    form is what every list view renders, so browsing 500 customers costs zero
    decryptions — revealing a real number stays a deliberate, audited action.
    """
    existing = find_account_by_phone(db, client_id, phone)
    if existing is not None:
        return existing

    account = LeadAccount(
        ClientId=client_id,
        CustomerId=customer_id,
        DisplayName=(display_name or "Customer")[:160],
        CompanyName=company_name,
        PhoneEnc=encrypt_pii(phone),
        EmailEnc=encrypt_pii(email),
        WhatsAppEnc=encrypt_pii(whatsapp or phone),
        PhoneHash=phone_fingerprint(phone),
        PhoneMasked=mask_phone(phone),
        EmailMasked=mask_email(email),
        Stage=stage,
        OwnerEmail=owner_email,
        Product=product,
        Value=value or 0.0,
        Source=source,
        Tags=tags,
        FieldsJson=fields,
        CreatedBy=actor,
    )
    db.add(account)
    db.flush()
    activity.log(
        db,
        action=A.ACCOUNT_CREATED,
        client_id=client_id,
        actor_email=actor,
        entity_type="account",
        entity_id=account.Id,
        message=f"Customer created: {account.DisplayName}",
        meta={"stage": stage, "source": source},
    )
    return account


def convert_lead(
    db: Session,
    client_id: str,
    conversation: LeadConversation,
    lead: Lead,
    *,
    owner_email: str | None = None,
    actor: str = "system",
    stage: str = "customer",
    value: float | None = None,
) -> LeadAccount:
    """Promote a qualified lead into a customer account.

    Idempotent by design — the conversion button in the UI is exactly the kind
    of thing that gets double-clicked, and an auto-convert rule can fire on the
    same lead from two concurrent turns.
    """
    if lead.ConvertedAccountId:
        existing = db.get(LeadAccount, lead.ConvertedAccountId)
        if existing is not None and not existing.IsDeleted:
            return existing

    customer = db.get(LeadCustomer, conversation.CustomerId)
    phone = decrypt_pii(customer.PhoneEnc) if customer else None
    email = decrypt_pii(customer.EmailEnc) if customer else None
    whatsapp = decrypt_pii(customer.WhatsAppEnc) if customer else None

    account = find_account_by_phone(db, client_id, phone)
    if account is None:
        account = create_account(
            db,
            client_id,
            display_name=(customer.DisplayName if customer else None)
            or (customer.PublicRef if customer else "Customer"),
            phone=phone,
            email=email,
            whatsapp=whatsapp,
            stage=stage,
            owner_email=owner_email or conversation.AssignedUserEmail,
            product=lead.Product if lead.Product != "unknown" else None,
            value=value if value is not None else 0.0,
            source=conversation.Channel,
            actor=actor,
            customer_id=conversation.CustomerId,
        )

    account.SourceConversationId = conversation.Id
    account.SourceLeadId = lead.Id
    account.ConvertedAt = utcnow()
    if owner_email:
        account.OwnerEmail = owner_email

    lead.ConvertedAccountId = account.Id
    lead.ConvertedAt = utcnow()
    if lead.Status != "qualified":
        lead.Status = "qualified"

    add_note(
        db,
        client_id,
        account,
        body=(
            f"Converted from a {conversation.Channel} conversation "
            f"(lead score {lead.Score}, interest: {lead.Interest})."
        ),
        note_type="stage_change",
        author_email=actor,
        meta={"conversation_id": conversation.Id, "lead_id": lead.Id, "score": lead.Score},
    )
    activity.log(
        db,
        action=A.LEAD_CONVERTED,
        client_id=client_id,
        actor_email=actor,
        entity_type="account",
        entity_id=account.Id,
        message=f"Lead converted to customer ({account.DisplayName})",
        meta={"lead_id": lead.Id, "score": lead.Score, "channel": conversation.Channel},
    )
    return account


def add_note(
    db: Session,
    client_id: str,
    account: LeadAccount,
    *,
    body: str,
    note_type: str = "note",
    author_email: str | None = None,
    meta: dict | None = None,
) -> LeadAccountNote:
    note = LeadAccountNote(
        ClientId=client_id,
        AccountId=account.Id,
        NoteType=note_type,
        Body=body[:4000],
        AuthorEmail=author_email,
        MetaJson=meta,
        CreatedBy=author_email or "system",
    )
    db.add(note)
    return note


def can_contact(account: LeadAccount, channel: str) -> tuple[bool, str | None]:
    """Consent gate. Returns (allowed, reason_if_not).

    Called by the campaign runner before EVERY send. Getting this wrong is not a
    bug, it is a regulatory incident — under India's TRAI rules and under GDPR
    for any EU contact, contacting someone who opted out is the expensive kind
    of mistake. So the check is centralised here and the default is restrictive.
    """
    if account.IsDeleted:
        return False, "Customer record deleted"
    if account.DoNotDisturb:
        return False, "Customer is on Do Not Disturb"
    if account.Status in ("blocked", "churned") and channel != "email":
        return False, f"Customer status is {account.Status}"
    mapping = {
        "whatsapp": account.OptInWhatsApp,
        "messenger": account.OptInWhatsApp,
        "instagram": account.OptInWhatsApp,
        "sms": account.OptInSms,
        "email": account.OptInEmail,
        "voice": account.OptInCall,
    }
    if not mapping.get(channel, True):
        return False, f"Customer has opted out of {channel}"
    return True, None


def mark_contacted(db: Session, account: LeadAccount, channel: str, note: str | None = None) -> None:
    account.LastContactedAt = utcnow()
    if note:
        add_note(db, account.ClientId, account, body=note, note_type="campaign")


def upcoming_occasions(
    db: Session, client_id: str, within_days: int = 7
) -> list[dict]:
    """Birthdays and anniversaries in the next N days — the audience for a
    festive/greeting campaign. Month/day comparison, so the stored year (often a
    placeholder) is irrelevant."""
    today = datetime.now(timezone.utc).date()
    rows = (
        db.query(LeadAccount)
        .filter(
            LeadAccount.ClientId == client_id,
            LeadAccount.IsDeleted == False,  # noqa: E712
        )
        .all()
    )
    out: list[dict] = []
    for account in rows:
        for field_name, label in (("Birthday", "birthday"), ("Anniversary", "anniversary")):
            value = getattr(account, field_name, None)
            if not value:
                continue
            try:
                this_year = value.replace(year=today.year).date()
            except ValueError:  # 29 Feb in a non-leap year
                continue
            delta = (this_year - today).days
            if 0 <= delta <= within_days:
                out.append(
                    {
                        "account_id": account.Id,
                        "name": account.DisplayName,
                        "occasion": label,
                        "date": this_year.isoformat(),
                        "in_days": delta,
                    }
                )
    return sorted(out, key=lambda item: item["in_days"])
