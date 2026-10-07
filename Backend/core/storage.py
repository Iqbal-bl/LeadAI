"""
MinIO object storage for Twilio call recordings.

MinIO is S3-compatible, so we reuse the already-installed `aioboto3` client and
just point it at the MinIO endpoint. The flow (see outbound/app.py's
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


def _env(name: str, default=None):
    """The value of an env var, with a trailing inline comment removed.

    A real incident: `.env` files only treat `#` as a comment, and that's
    fine for python-dotenv (used by LeadAI/config.py's own `_env()`, which
    this mirrors) — but Docker Compose's `env_file:` directive does NOT
    strip an inline `# comment` the same way, so a container saw the raw
    value `"false     # set true once MinIO has a certificate signed by a
    real CA"` for MINIO_VERIFY_SSL. `== "false"` never matched, verification
    silently stayed on, and every recording archive failed with a
    self-signed-cert SSLCertVerificationError. Stripping here makes this
    module correct regardless of which tool parsed the .env file.
    """
    value = os.getenv(name)
    if value is None:
        return default
    for marker in (" //", "\t//", " #", "\t#"):
        cut = value.find(marker)
        if cut != -1:
            value = value[:cut]
    return value.strip()


def minio_enabled() -> bool:
    """True only when the core MinIO settings are present."""
    return bool(_env("MINIO_ENDPOINT") and _env("MINIO_ACCESS_KEY") and _env("MINIO_SECRET_KEY"))


def _bucket() -> str:
    return _env("MINIO_BUCKET_RECORDINGS", _env("MINIO_BUCKET", "call-recordings"))


def _public_url(key: str) -> str:
    base = (_env("MINIO_PUBLIC_ENDPOINT") or _env("MINIO_ENDPOINT") or "").rstrip("/")
    return f"{base}/{_bucket()}/{key}"


def _verify_setting():
    """
    TLS verification for the MinIO endpoint:
      - MINIO_CA_BUNDLE set  -> verify against that CA bundle (self-signed cert)
      - MINIO_VERIFY_SSL=false -> skip verification entirely
      - otherwise -> default botocore verification (True)
    """
    ca_bundle = _env("MINIO_CA_BUNDLE")
    if ca_bundle:
        return ca_bundle
    if (_env("MINIO_VERIFY_SSL", "true") or "true").lower() == "false":
        return False
    return True


def _session_and_kwargs():
    """Build an aioboto3 session + client kwargs targeting MinIO."""
    session = aioboto3.Session()
    kwargs = dict(
        service_name="s3",
        endpoint_url=_env("MINIO_ENDPOINT"),
        aws_access_key_id=_env("MINIO_ACCESS_KEY"),
        aws_secret_access_key=_env("MINIO_SECRET_KEY"),
        region_name=_env("MINIO_REGION", "us-east-1"),
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
