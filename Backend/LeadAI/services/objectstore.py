"""
Document store — MinIO (S3-compatible) with a local-disk fallback.

WHY A SECOND MODULE WHEN storage.py ALREADY EXISTS
--------------------------------------------------
`storage.py` at the repo root is purpose-built for ONE job: archive a Twilio
recording under `call-recordings/`. It is async-only (aioboto3), has no
listing, no deletion, no presigned URLs and no tenant prefixing. Rather than
bend it — and risk the recording pipeline that is already in production — this
module is the general-purpose store for everything else: knowledge-base
sources, uploaded campaign lists, campaign media, exports.

Both point at the SAME MinIO server. They just use different buckets, so the
recording archive's lifecycle rules (which you probably want long and
write-once) stay independent of, say, an export that should expire in a week.

DESIGN NOTES
------------
* Synchronous. Uploads happen inside ordinary request handlers and background
  jobs that already own a blocking DB session; adding an event loop there buys
  nothing and complicates error handling. boto3 is already installed.
* Tenant prefixing is mandatory and is done HERE, not by callers:
  `{purpose}/{client_id}/{yyyy}/{mm}/{uuid}-{safe_name}`. A caller cannot
  accidentally write into another company's prefix.
* Presigned URLs, not public buckets. A KB source document may contain a
  company's pricing; it must not be world-readable because someone guessed a
  key. Links are time-limited and minted per request.
* Degrades to local disk when MinIO is unconfigured, so `docker compose up`
  with nothing set still works end to end.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterable

from ..config import settings

logger = logging.getLogger(__name__)

# Purpose -> bucket. Keeping this a mapping (rather than one bucket with a
# prefix) means retention policy, versioning and quotas can be set per class of
# object directly in MinIO.
BUCKET_FOR_PURPOSE = {
    "kb": settings.minio_bucket_documents,
    "general": settings.minio_bucket_documents,
    "avatar": settings.minio_bucket_documents,
    "campaign_list": settings.minio_bucket_campaigns,
    "campaign_media": settings.minio_bucket_campaigns,
    "export": settings.minio_bucket_exports,
    "recording": os.getenv("MINIO_BUCKET_RECORDINGS", os.getenv("MINIO_BUCKET", "call-recordings")),
}

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_client = None
_checked_buckets: set[str] = set()


class StorageError(RuntimeError):
    """Upload/download failed. Callers turn this into a 502, never a 500."""


@dataclass
class StoredObject:
    bucket: str
    key: str
    size: int
    content_type: str
    checksum: str
    backend: str
    url: str | None = None


# --------------------------------------------------------------------------- #
# client
# --------------------------------------------------------------------------- #
def backend() -> str:
    return "minio" if settings.minio_enabled else "local"


def _s3():
    """Lazily build (and memoise) a boto3 S3 client pointed at MinIO."""
    global _client
    if _client is not None:
        return _client
    if not settings.minio_enabled:
        return None
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError as exc:  # pragma: no cover
        raise StorageError("boto3 is not installed") from exc

    # TLS verification: MINIO_CA_BUNDLE path, or MINIO_VERIFY_SSL=false to skip
    import os
    _ca_bundle = os.getenv("MINIO_CA_BUNDLE")
    if _ca_bundle:
        _verify = _ca_bundle
    elif os.getenv("MINIO_VERIFY_SSL", "true").strip().lower() == "false":
        _verify = False
    else:
        _verify = True

    _client = boto3.client(
        "s3",
        endpoint_url=settings.minio_endpoint,
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
        region_name=settings.minio_region,
        verify=_verify,
        # MinIO requires path-style addressing; virtual-host style would try to
        # resolve `bucket.minio:9000` and fail.
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )
    return _client


def _ensure_bucket(bucket: str) -> None:
    """Create the bucket on first use. Cached so it costs one call per process."""
    if bucket in _checked_buckets:
        return
    client = _s3()
    if client is None:
        return
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:  # noqa: BLE001  (404 / NoSuchBucket / 403)
        try:
            client.create_bucket(Bucket=bucket)
            logger.info("[LeadAI storage] created bucket %s", bucket)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[LeadAI storage] bucket %s unavailable: %s", bucket, exc)
    _checked_buckets.add(bucket)


# --------------------------------------------------------------------------- #
# keys
# --------------------------------------------------------------------------- #
def safe_name(name: str) -> str:
    cleaned = _SAFE.sub("_", (name or "file").strip())[-120:]
    return cleaned or "file"


def build_key(client_id: str, purpose: str, filename: str) -> str:
    """Tenant- and time-partitioned key.

    Time partitioning matters at scale: listing `kb/<client>/2026/08/` is cheap,
    listing a flat prefix with a million siblings is not.
    """
    now = datetime.now(timezone.utc)
    return (
        f"{purpose}/{client_id}/{now:%Y}/{now:%m}/"
        f"{uuid.uuid4().hex[:12]}-{safe_name(filename)}"
    )


def bucket_for(purpose: str) -> str:
    return BUCKET_FOR_PURPOSE.get(purpose, settings.minio_bucket_documents)


def _local_path(bucket: str, key: str) -> Path:
    return Path(settings.local_storage_dir) / bucket / key


# --------------------------------------------------------------------------- #
# operations
# --------------------------------------------------------------------------- #
def put_bytes(
    data: bytes,
    *,
    client_id: str,
    purpose: str = "general",
    filename: str = "file",
    content_type: str = "application/octet-stream",
    metadata: dict[str, str] | None = None,
) -> StoredObject:
    """Store bytes and return where they landed. Raises StorageError on failure."""
    bucket = bucket_for(purpose)
    key = build_key(client_id, purpose, filename)
    checksum = hashlib.sha256(data).hexdigest()

    client = _s3()
    if client is None:
        # Local fallback — same return shape, so no caller branches on backend.
        path = _local_path(bucket, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return StoredObject(bucket, key, len(data), content_type, checksum, "local")

    _ensure_bucket(bucket)
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            # S3 metadata values must be ASCII; the original filename can be
            # anything, so it is sanitised rather than dropped.
            Metadata={
                "client-id": client_id,
                "purpose": purpose,
                "original-name": safe_name(filename),
                "sha256": checksum,
                **{k: str(v)[:200] for k, v in (metadata or {}).items()},
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[LeadAI storage] put failed for %s/%s: %s", bucket, key, exc)
        raise StorageError(f"Could not store the file: {exc}") from exc

    return StoredObject(bucket, key, len(data), content_type, checksum, "minio")


def put_stream(
    stream: BinaryIO,
    *,
    client_id: str,
    purpose: str = "general",
    filename: str = "file",
    content_type: str = "application/octet-stream",
) -> StoredObject:
    """Upload without loading the whole object into memory."""
    client = _s3()
    if client is None:
        return put_bytes(
            stream.read(),
            client_id=client_id,
            purpose=purpose,
            filename=filename,
            content_type=content_type,
        )
    bucket = bucket_for(purpose)
    key = build_key(client_id, purpose, filename)
    _ensure_bucket(bucket)
    try:
        client.upload_fileobj(
            stream, bucket, key, ExtraArgs={"ContentType": content_type}
        )
        head = client.head_object(Bucket=bucket, Key=key)
        size = int(head.get("ContentLength", 0))
    except Exception as exc:  # noqa: BLE001
        raise StorageError(f"Could not store the file: {exc}") from exc
    return StoredObject(bucket, key, size, content_type, "", "minio")


def get_bytes(bucket: str, key: str) -> bytes:
    client = _s3()
    if client is None:
        path = _local_path(bucket, key)
        if not path.exists():
            raise StorageError("File not found")
        return path.read_bytes()
    try:
        return client.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception as exc:  # noqa: BLE001
        raise StorageError(f"Could not read the file: {exc}") from exc


def presigned_url(bucket: str, key: str, seconds: int | None = None, download_name: str | None = None) -> str | None:
    """Time-limited GET link.

    Returns None on the local backend — the caller falls back to streaming the
    bytes through the API, which is what /files/{id}/download does anyway.
    """
    client = _s3()
    if client is None:
        return None
    params: dict = {"Bucket": bucket, "Key": key}
    if download_name:
        params["ResponseContentDisposition"] = (
            f'attachment; filename="{safe_name(download_name)}"'
        )
    try:
        url = client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=seconds or settings.minio_presign_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LeadAI storage] presign failed: %s", exc)
        return None
    # The signing endpoint is usually the internal one (http://minio:9000);
    # rewrite the host so a browser can actually open the link.
    if settings.minio_public_endpoint and settings.minio_endpoint:
        url = url.replace(
            settings.minio_endpoint.rstrip("/"),
            settings.minio_public_endpoint.rstrip("/"),
            1,
        )
    return url


def delete(bucket: str, key: str) -> bool:
    client = _s3()
    if client is None:
        path = _local_path(bucket, key)
        if path.exists():
            path.unlink()
            return True
        return False
    try:
        client.delete_object(Bucket=bucket, Key=key)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LeadAI storage] delete failed %s/%s: %s", bucket, key, exc)
        return False


def list_keys(bucket: str, prefix: str, limit: int = 200) -> Iterable[str]:
    client = _s3()
    if client is None:
        root = _local_path(bucket, prefix)
        if not root.exists():
            return []
        return [str(p.relative_to(_local_path(bucket, ""))) for p in root.rglob("*") if p.is_file()][:limit]
    try:
        resp = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=limit)
        return [o["Key"] for o in resp.get("Contents", [])]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LeadAI storage] list failed: %s", exc)
        return []


def health() -> dict:
    """Reported by /api/leadai/health so a misconfigured store is visible."""
    if not settings.minio_enabled:
        return {"backend": "local", "path": settings.local_storage_dir, "ok": True}
    client = _s3()
    try:
        client.list_buckets()
        ok, error = True, None
    except Exception as exc:  # noqa: BLE001
        ok, error = False, str(exc)[:200]
    return {
        "backend": "minio",
        "endpoint": settings.minio_endpoint,
        "buckets": sorted(set(BUCKET_FOR_PURPOSE.values())),
        "ok": ok,
        "error": error,
    }


def purge_local() -> None:
    """Test helper — wipes the local fallback directory."""
    shutil.rmtree(settings.local_storage_dir, ignore_errors=True)
