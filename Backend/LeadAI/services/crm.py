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
import re
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


def extract_linkedin_token(raw_id: str | None) -> str | None:
    """Extract the core LinkedIn member token or vanity slug from any URN or profile URL."""
    if not raw_id:
        return None
    raw_str = str(raw_id).strip()
    # Check for profile URL: https://www.linkedin.com/in/{slug}/
    url_match = re.search(r"linkedin\.com/in/([^/?#]+)", raw_str, re.IGNORECASE)
    if url_match:
        return url_match.group(1).strip().rstrip("/")
    # Check for URN: urn:li:fsd_profile:{token}, urn:li:fs_miniProfile:{token}, urn:li:member:{token}
    if raw_str.startswith("urn:li:"):
        parts = raw_str.split(":")
        if len(parts) >= 4:
            return parts[-1].strip()
    return raw_str.strip().rstrip("/")


def extract_linkedin_slug(url: str | None) -> str | None:
    """Extract vanity slug or token from a LinkedIn profile URL."""
    if not url:
        return None
    match = re.search(r"linkedin\.com/in/([^/?#]+)", str(url).strip(), re.IGNORECASE)
    if match:
        return match.group(1).strip().rstrip("/")
    return str(url).strip().rstrip("/")


def find_account_by_linkedin(
    db: Session, client_id: str, linkedin_profile_url: str | None, member_token: str | None = None
) -> LeadAccount | None:
    """Find an existing active CRM account by LinkedIn vanity slug, profile URL, or member token."""
    token = extract_linkedin_slug(linkedin_profile_url) or extract_linkedin_token(member_token)
    if not token or len(token) < 3:
        return None
    return (
        db.query(LeadAccount)
        .filter(
            LeadAccount.ClientId == client_id,
            LeadAccount.LinkedinProfileUrl.like(f"%{token}%"),
            LeadAccount.IsDeleted == False,
        )
        .first()
    )


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
    linkedin_profile_url: str | None = None,
) -> LeadAccount:
    """Create (or return an existing) account. Contact details encrypted at rest.

    Both the encrypted value AND a masked display value are stored. The masked
    form is what every list view renders, so browsing 500 customers costs zero
    decryptions — revealing a real number stays a deliberate, audited action.
    """
    existing = find_account_by_phone(db, client_id, phone)
    if existing is None and customer_id:
        existing = (
            db.query(LeadAccount)
            .filter(
                LeadAccount.ClientId == client_id,
                LeadAccount.CustomerId == customer_id,
                LeadAccount.IsDeleted == False,
            )
            .first()
        )
    # Check by LinkedIn profile URL or member token if not matched by customer_id or phone
    if existing is None and linkedin_profile_url:
        existing = find_account_by_linkedin(db, client_id, linkedin_profile_url)

    if existing is not None:
        # Deduplication & enrichment: Update LinkedIn profile URL if new one is verified/better
        url_updated = False
        if linkedin_profile_url:
            current_url = existing.LinkedinProfileUrl or ""
            # If current URL is missing, or is a raw token, and incoming is a vanity slug, upgrade it!
            incoming_is_vanity = bool(re.search(r"linkedin\.com/in/[a-zA-Z0-9_-]+", linkedin_profile_url))
            current_is_raw_token = "ACoAA" in current_url
            if not current_url or (current_is_raw_token and incoming_is_vanity) or current_url != linkedin_profile_url:
                existing.LinkedinProfileUrl = linkedin_profile_url
                url_updated = True

        # Append source tag to existing account if coming from another channel touchpoint
        if source:
            current_tags = set((existing.Tags or "").split(",")) if existing.Tags else set()
            new_tags = [source.strip()]
            if tags:
                new_tags.extend(t.strip() for t in tags.split(",") if t.strip())
            added = False
            for t in new_tags:
                if t and t not in current_tags:
                    current_tags.add(t)
                    added = True
            if added:
                existing.Tags = ",".join(sorted(filter(None, current_tags)))

        if url_updated:
            existing.UpdatedAt = utcnow()
            logger.info(
                "[CRM Lead LinkedIn URL] Stored profile URL '%s' for existing customer '%s' (AccountId: %s, CustomerId: %s, Source: %s)",
                linkedin_profile_url,
                existing.DisplayName,
                existing.Id,
                existing.CustomerId,
                source or existing.Source or "unknown",
            )
        db.commit()
        logger.info(
            "[CRM Lead Deduplication] Merged incoming event (Source: %s) into existing customer '%s' (AccountId: %s, CustomerId: %s)",
            source or "unknown",
            existing.DisplayName,
            existing.Id,
            existing.CustomerId,
        )
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
        LinkedinProfileUrl=linkedin_profile_url,
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
    if linkedin_profile_url:
        logger.info(
            "[CRM Lead LinkedIn URL] Stored profile URL '%s' for new customer '%s' (AccountId: %s, CustomerId: %s, Source: %s)",
            linkedin_profile_url,
            account.DisplayName,
            account.Id,
            customer_id,
            source or "unknown",
        )
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
    linkedin_profile_url = getattr(customer, "LinkedinProfileUrl", None) if customer else None

    account = None
    if phone:
        account = find_account_by_phone(db, client_id, phone)

    if account is None and conversation.CustomerId:
        account = db.query(LeadAccount).filter(
            LeadAccount.ClientId == client_id,
            LeadAccount.CustomerId == conversation.CustomerId,
            LeadAccount.IsDeleted == False,
        ).first()

    if account is None and linkedin_profile_url and len(linkedin_profile_url) > 20:
        clean_url = linkedin_profile_url.split("?")[0].rstrip("/")
        account = db.query(LeadAccount).filter(
            LeadAccount.ClientId == client_id,
            LeadAccount.LinkedinProfileUrl.like(f"%{clean_url}%"),
            LeadAccount.IsDeleted == False,
        ).first()

    if account is None and customer and customer.DisplayName:
        from ..social.linkedin_bot import normalize_contact_name
        clean_target_name = normalize_contact_name(customer.DisplayName)
        if clean_target_name and clean_target_name.lower() not in ("customer", "unknown lead", "linkedin member", ""):
            cand_accounts = db.query(LeadAccount).filter(
                LeadAccount.ClientId == client_id,
                LeadAccount.IsDeleted == False,
            ).all()
            for cand in cand_accounts:
                if normalize_contact_name(cand.DisplayName).lower() == clean_target_name.lower():
                    account = cand
                    break

    if account is None:
        # Carry over what the AI already learned during the conversation (city,
        # budget, family size, ...) instead of handing the sales rep an account
        # that remembers nothing. create_account() supports `fields`; nothing
        # was ever passing it, so every converted customer started blank.
        stated_facts = [str(f) for f in (lead.FactsJson or []) if str(f).strip()]
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
            fields={"lead_facts": stated_facts} if stated_facts else None,
            actor=actor,
            customer_id=conversation.CustomerId,
            linkedin_profile_url=linkedin_profile_url,
        )
    else:
        # Existing account matched — backfill or upgrade profile URL and customer_id
        if linkedin_profile_url:
            current_acc_url = account.LinkedinProfileUrl or ""
            if not current_acc_url or ("ACoAA" in current_acc_url and "ACoAA" not in linkedin_profile_url):
                account.LinkedinProfileUrl = linkedin_profile_url
        if not account.CustomerId and conversation.CustomerId:
            account.CustomerId = conversation.CustomerId

    account.SourceConversationId = conversation.Id
    account.SourceLeadId = lead.Id
    account.ConvertedAt = utcnow()
    # The caller explicitly asked to move this lead to `stage` — true whether an
    # account was just created (already got it via create_account's own default)
    # or an existing one was found by phone. Skipping this for the existing-account
    # path meant converting a lead whose phone matched a prior account silently did
    # nothing: the request said stage="customer", the response echoed it back, but
    # the stored account kept whatever stage it already had.
    account.Stage = stage
    # Same gap for the name: a different person's lead (or the same person giving
    # a different name) sharing a phone with a PRIOR account converted and the
    # account kept the old name forever, with no trace that anyone named
    # differently had ever come through on that number. The new name wins (this
    # is an explicit, operator-initiated conversion) but the old one is kept as a
    # note rather than silently discarded — a shared number can genuinely belong
    # to more than one person.
    new_name = (customer.DisplayName if customer else None) or (customer.PublicRef if customer else None)
    if new_name and new_name != account.DisplayName:
        if account.DisplayName:
            add_note(
                db, client_id, account,
                body=f"Name on file changed from \"{account.DisplayName}\" to \"{new_name}\" "
                     f"(another lead on the same phone number converted).",
                note_type="stage_change", author_email=actor,
            )
        account.DisplayName = new_name
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
