"""
MinIO object storage for Twilio call recordings.

MinIO is S3-compatible, so we reuse the already-installed `aioboto3` client and
just point it at the MinIO endpoint. The flow (see multiligual_call.py's
recording webhook) is:

    Twilio hosts the audio  ->  download (basic auth)  ->  put into MinIO
                            ->  delete from Twilio       ->  return MinIO URL

Configuration (env):
    MINIO_ENDPOINT        e.g. http://minio:9000  (or https://minio.example.com)
    MINIO_ACCESS_KEY      MinIO root / service account access key
    MINIO_SECRET_KEY      MinIO secret key
    MINIO_BUCKET          bucket for recordings        (default: call-recordings)
    MINIO_REGION          region label                 (default: us-east-1)
    MINIO_PUBLIC_ENDPOINT optional browser-facing base used to build the
                          returned URL (defaults to MINIO_ENDPOINT)
    MINIO_CA_BUNDLE       optional path to a CA cert/bundle for verifying a
                          self-signed MinIO TLS certificate
    MINIO_VERIFY_SSL      set to "false" to skip TLS verification entirely
                          (only for trusted networks with self-signed certs;
                          prefer MINIO_CA_BUNDLE when possible)
"""
import io
import os
import logging
from datetime import datetime, timezone

import aioboto3
from botocore.config import Config as BotoConfig

logger = logging.getLogger(__name__)


def minio_enabled() -> bool:
    """True only when the core MinIO settings are present."""
    return bool(
        os.getenv("MINIO_ENDPOINT")
        and os.getenv("MINIO_ACCESS_KEY")
        and os.getenv("MINIO_SECRET_KEY")
    )


def _bucket() -> str:
    return os.getenv("MINIO_BUCKET_RECORDINGS", os.getenv("MINIO_BUCKET", "call-recordings"))


def _public_url(key: str) -> str:
    base = (os.getenv("MINIO_PUBLIC_ENDPOINT") or os.getenv("MINIO_ENDPOINT") or "").rstrip("/")
    return f"{base}/{_bucket()}/{key}"


def _verify_setting():
    """
    TLS verification for the MinIO endpoint:
      - MINIO_CA_BUNDLE set  -> verify against that CA bundle (self-signed cert)
      - MINIO_VERIFY_SSL=false -> skip verification entirely
      - otherwise -> default botocore verification (True)
    """
    ca_bundle = os.getenv("MINIO_CA_BUNDLE")
    if ca_bundle:
        return ca_bundle
    if os.getenv("MINIO_VERIFY_SSL", "true").strip().lower() == "false":
        return False
    return True


def _session_and_kwargs():
    """Build an aioboto3 session + client kwargs targeting MinIO."""
    session = aioboto3.Session()
    kwargs = dict(
        service_name="s3",
        endpoint_url=os.getenv("MINIO_ENDPOINT"),
        aws_access_key_id=os.getenv("MINIO_ACCESS_KEY"),
        aws_secret_access_key=os.getenv("MINIO_SECRET_KEY"),
        region_name=os.getenv("MINIO_REGION", "us-east-1"),
        verify=_verify_setting(),
        # path-style addressing is required for MinIO (no virtual-host buckets)
        config=BotoConfig(s3={"addressing_style": "path"}),
    )
    return session, kwargs


async def _ensure_bucket(s3):
    """Create the bucket if it does not already exist (idempotent)."""
    try:
        await s3.head_bucket(Bucket=_bucket())
    except Exception:
        try:
            await s3.create_bucket(Bucket=_bucket())
            logger.info(f"[MINIO] created bucket '{_bucket()}'")
        except Exception as e:
            # Another worker may have created it between head and create.
            logger.warning(f"[MINIO] ensure bucket '{_bucket()}': {e}")


async def upload_bytes(data: bytes, key: str, content_type: str) -> str:
    """Upload raw bytes to MinIO under `key`; return the object URL."""
    session, kwargs = _session_and_kwargs()
    async with session.client(**kwargs) as s3:
        await _ensure_bucket(s3)
        await s3.upload_fileobj(
            Fileobj=io.BytesIO(data),
            Bucket=_bucket(),
            Key=key,
            ExtraArgs={"ContentType": content_type},
        )
    url = _public_url(key)
    logger.info(f"[MINIO] uploaded {len(data)} bytes -> {url}")
    return url


def build_key(call_sid: str, ext: str) -> str:
    """Date-partitioned object key: recordings/YYYY/MM/DD/<callsid>.<ext>."""
    now = datetime.now(timezone.utc)
    return f"recordings/{now:%Y/%m/%d}/{call_sid}.{ext}"
