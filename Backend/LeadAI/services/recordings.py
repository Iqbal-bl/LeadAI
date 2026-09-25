"""Playable links for call recordings.

The voice pipeline archives each call's audio in MinIO and saves its plain URL in the
`recordings` table (CallSid -> RecordingUrl). That URL is only playable if the bucket is
public. This module turns it into a time-limited signed link, which works for a public
or a private bucket alike, so the inbox can show a working audio player.

Signing is local arithmetic: no request is made to MinIO. The link is signed for the PUBLIC
address, because the host is part of an S3 signature: signing for the internal address and
then swapping the host (as the generic document helper does) would be rejected by MinIO.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from domain.models import Recordings

from ..config import settings

logger = logging.getLogger(__name__)

_client = None


def _hosts() -> set[str]:
    """Hostnames that mean "our MinIO": the public address and the internal one."""
    return {
        urlparse(u).netloc.lower()
        for u in (settings.minio_public_endpoint, settings.minio_endpoint)
        if u
    }


def _signer():
    """A boto3 client that signs for the address a browser will actually call."""
    global _client
    if _client is not None:
        return _client
    import boto3
    from botocore.config import Config

    _client = boto3.client(
        "s3",
        endpoint_url=(settings.minio_public_endpoint or settings.minio_endpoint),
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
        region_name=settings.minio_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return _client


def playable_url(stored_url: str | None) -> str | None:
    """A working link for a stored recording URL.

    Our own MinIO objects get a signed, expiring link (MINIO_PRESIGN_SECONDS). Any other
    URL (for example a Twilio one, when archiving was off) is returned unchanged, and so is
    the stored URL if MinIO is not configured or signing fails, so this never returns less
    than what was stored.
    """
    if not stored_url:
        return None
    if not settings.minio_enabled:
        return stored_url
    parsed = urlparse(stored_url)
    if parsed.netloc.lower() not in _hosts():
        return stored_url
    bucket, _, key = parsed.path.lstrip("/").partition("/")
    if not bucket or not key:
        return stored_url
    try:
        return _signer().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=settings.minio_presign_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — a signing problem must not break the call list
        logger.warning("[LeadAI recordings] could not sign %s: %s", parsed.path, exc)
        return stored_url


def stored_urls(db: Session, call_sids: list[str]) -> dict[str, str]:
    """The newest stored recording URL for each call id that has one (one query)."""
    sids = [s for s in dict.fromkeys(call_sids) if s]
    if not sids:
        return {}
    rows = (
        db.query(Recordings)
        .filter(Recordings.CallSid.in_(sids), Recordings.IsDeleted == False)  # noqa: E712
        .order_by(Recordings.CreatedAt.asc())
        .all()
    )
    return {r.CallSid: r.RecordingUrl for r in rows}       # a later row replaces an earlier one


def playable_urls(db: Session, call_sids: list[str]) -> dict[str, str]:
    """{call_sid: playable link} for every call in `call_sids` that has a recording."""
    return {sid: playable_url(url) for sid, url in stored_urls(db, call_sids).items()}
