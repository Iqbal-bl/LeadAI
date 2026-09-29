"""Keep contact details a customer types into the chat.

A customer on Instagram or Messenger has no phone number on file: those channels never
give us one. When staff or the AI ask for it and the customer replies "Yes it's
7696086310", that number used to sit only in the message text. This module reads it out
and saves it on the customer record (encrypted, like every other contact field), so the
inbox shows it masked and the audited Reveal returns it.

Only real phone numbers are accepted: candidates are validated with the `phonenumbers`
library, so "1.2 crore", "10800000" or "1180 sq ft" are never mistaken for a phone.
"""
from __future__ import annotations

import logging

import phonenumbers
from phonenumbers import Leniency, PhoneNumberFormat, PhoneNumberMatcher
from sqlalchemy.orm import Session

from .. import activity
from ..activity import A
from ..config import settings
from ..models import LeadCustomer
from ..security import decrypt_pii, encrypt_pii, phone_fingerprint

logger = logging.getLogger(__name__)


def extract_phone(text: str | None, region: str | None = None) -> str | None:
    """The first valid phone number in `text`, as E.164 (+917696086310), or None.

    `region` is the country assumed for numbers written without a country code.
    """
    if not text:
        return None
    region = (region or settings.default_phone_region or "IN").upper()
    try:
        for match in PhoneNumberMatcher(text, region, leniency=Leniency.VALID):
            return phonenumbers.format_number(match.number, PhoneNumberFormat.E164)
    except Exception:  # noqa: BLE001 — never let parsing break a live customer turn
        logger.debug("[LeadAI capture] phone parsing failed", exc_info=True)
    return None


def capture_phone(
    db: Session,
    customer: LeadCustomer | None,
    text: str | None,
    *,
    client_id: str,
    conversation_id: str,
    actor: str = "customer",
) -> str | None:
    """Save a phone number found in a customer's message. Caller commits.

    Fills an EMPTY phone only. A customer who already has a number keeps it: silently
    replacing it because a later message contains a different number (a friend's, an
    order id) would be worse than missing an update. Returns the number saved, or None.
    """
    if customer is None or not text:
        return None
    if customer.PhoneEnc and decrypt_pii(customer.PhoneEnc):
        return None
    phone = extract_phone(text)
    if not phone:
        return None

    customer.PhoneEnc = encrypt_pii(phone)
    customer.PhoneHash = phone_fingerprint(phone)
    # The audit row names the customer and the conversation, never the number itself.
    activity.log(
        db,
        action=A.PHONE_CAPTURED,
        client_id=client_id,
        actor_email=actor,
        actor_role="customer",
        entity_type="customer",
        entity_id=customer.Id,
        message=f"Phone number captured from chat for {customer.PublicRef}",
        meta={"customer_ref": customer.PublicRef, "conversation_id": conversation_id},
    )
    logger.info("[LeadAI capture] phone saved for %s (conv %s)", customer.PublicRef, conversation_id)
    return phone
