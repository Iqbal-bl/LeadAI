"""
PII protection and customer-widget session tokens.

Two separate concerns, both deliberately independent of the outbound app's
identity-server auth (which stays the single source of truth for STAFF):

1. PII at rest. Customer phone/email/social handles are encrypted with Fernet
   before they touch the database. Agents are shown `PublicRef` only; a reveal
   is an explicit, audited admin action. `phone_fingerprint()` gives a
   non-reversible lookup key so a returning customer can be recognised without
   ever decrypting.

2. Chat widget sessions. An end customer on a public website has no identity-
   server account, so they get a short-lived HS256 token bound to one
   conversation id. It grants access to that conversation and nothing else —
   it is not a user token and carries no role.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt  # PyJWT, already a dependency of the outbound app

from .config import settings

logger = logging.getLogger(__name__)


# ===========================================================================
# PII encryption
# ===========================================================================

_fernet_instance = None


def _fernet():
    """Lazily build a Fernet. Import is local so a missing `cryptography`
    degrades to hashing-only rather than breaking app import."""
    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance
    try:
        from cryptography.fernet import Fernet
    except ImportError:  # pragma: no cover
        logger.warning("[LeadAI] cryptography not installed — PII will be stored masked-only")
        return None

    if settings.pii_key:
        key = settings.pii_key.encode()
    else:
        # Deterministic derivation so the demo boots without extra config, and
        # so restarts can still decrypt what they wrote.
        digest = hashlib.sha256(settings.chat_jwt_secret.encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    try:
        _fernet_instance = Fernet(key)
    except Exception as exc:  # noqa: BLE001
        logger.error("[LeadAI] invalid LEADAI_PII_KEY (%s) — PII encryption disabled", exc)
        return None
    return _fernet_instance


def encrypt_pii(value: str | None) -> str | None:
    if not value:
        return None
    box = _fernet()
    if box is None:
        return None
    return box.encrypt(value.strip().encode()).decode()


def decrypt_pii(value: str | None) -> str | None:
    if not value:
        return None
    box = _fernet()
    if box is None:
        return None
    try:
        return box.decrypt(value.encode()).decode()
    except Exception:  # noqa: BLE001  (InvalidToken and friends)
        return None


def phone_fingerprint(phone: str | None) -> str | None:
    """Keyed hash of a normalised phone number.

    Keyed (HMAC) rather than plain SHA so a leaked database can't be attacked by
    hashing every possible Indian mobile number.
    """
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return None
    digits = digits[-10:]  # last 10 digits: country-code agnostic
    return hmac.new(
        settings.chat_jwt_secret.encode(), digits.encode(), hashlib.sha256
    ).hexdigest()


def mask_phone(phone: str | None) -> str | None:
    """'+919876543210' -> '+9198*****210'. Safe to show any role."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 6:
        return "*" * len(digits)
    prefix = phone[: len(phone) - 8]
    return f"{prefix}{digits[-8:-6]}*****{digits[-3:]}"


def mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, domain = email.split("@", 1)
    head = local[:2] if len(local) > 2 else local[:1]
    return f"{head}{'*' * max(3, len(local) - len(head))}@{domain}"


# ===========================================================================
# Chat widget session tokens
# ===========================================================================

_CHAT_TOKEN_TYPE = "leadai_chat"


def create_chat_token(conversation_id: str, client_id: str, minutes: int | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": conversation_id,
        "cid": client_id,
        "typ": _CHAT_TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=minutes or settings.chat_session_minutes),
    }
    return jwt.encode(payload, settings.chat_jwt_secret, algorithm=settings.chat_jwt_algorithm)


def decode_chat_token(token: str) -> dict[str, Any] | None:
    """Return claims for a valid widget session token, else None."""
    try:
        claims = jwt.decode(
            token,
            settings.chat_jwt_secret,
            algorithms=[settings.chat_jwt_algorithm],
            leeway=settings.chat_jwt_leeway,
        )
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
    if claims.get("typ") != _CHAT_TOKEN_TYPE or not claims.get("sub"):
        return None
    return claims
