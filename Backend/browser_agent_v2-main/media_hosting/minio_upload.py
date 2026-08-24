# --------------------------------------------------
# agentic_browser_v2 / media_hosting\minio_upload.py
# --------------------------------------------------
"""Upload base64 media to an S3-compatible MinIO bucket.

Meta fetches Instagram and Facebook media from the returned URL itself, so
MINIO_PUBLIC_BASE_URL must be an externally reachable HTTPS object endpoint.
The MinIO console URL and a private LAN address are not suitable for it.
"""

import asyncio
import base64
import binascii
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import quote
from uuid import uuid4

import boto3
from botocore.config import Config
from dotenv import load_dotenv


load_dotenv()


def _setting(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if not value:
        raise ValueError(f"{name} is not set in .env")
    return value


def _client():
    """Return a path-style S3 client, compatible with a standard MinIO setup."""
    verify_tls = os.getenv("MINIO_TLS_VERIFY", "true").lower() not in {"0", "false", "no"}
    return boto3.client(
        "s3",
        endpoint_url=_setting("MINIO_ENDPOINT").rstrip("/"),
        aws_access_key_id=_setting("MINIO_ACCESS_KEY"),
        aws_secret_access_key=_setting("MINIO_SECRET_KEY"),
        region_name=os.getenv("MINIO_REGION", "us-east-1"),
        verify=verify_tls,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _decode_base64(value: str) -> bytes:
    if value.startswith("data:"):
        _, separator, value = value.partition(",")
        if not separator:
            raise ValueError("Invalid base64 data URI")
    try:
        return base64.b64decode(value, validate=True)
    except binascii.Error as exc:
        raise ValueError("Media data must be valid base64") from exc


def _extension(mime_type: str) -> str:
    extension = mimetypes.guess_extension(mime_type, strict=False)
    if extension == ".jpe":
        return ".jpg"
    return extension or ".bin"


def _public_url(bucket: str, key: str) -> str:
    # This must be the externally reachable object URL origin, not the MinIO
    # Console endpoint. It normally has the form https://media.company.com.
    base_url = _setting("MINIO_PUBLIC_BASE_URL").rstrip("/")
    return f"{base_url}/{quote(bucket, safe='')}/{quote(key, safe='/')}"


def _upload_sync(base64_data: str, mime_type: str) -> str:
    body = _decode_base64(base64_data)
    if not body:
        raise ValueError("Media data is empty")

    bucket = _setting("MINIO_BUCKET")
    prefix = os.getenv("MINIO_PREFIX", "posts").strip("/")
    timestamp = datetime.now(timezone.utc)
    key = str(
        PurePosixPath(prefix)
        / f"{timestamp:%Y}"
        / f"{timestamp:%m}"
        / f"{uuid4().hex}{_extension(mime_type)}"
    )

    _client().put_object(Bucket=bucket, Key=key, Body=body, ContentType=mime_type)
    return _public_url(bucket, key)


async def upload_base64(base64_data: str, mime_type: str) -> str:
    """Upload media once and return its public direct-download URL.

    The function intentionally keeps Cloudinary's former interface so the
    direct multi-platform posting flow does not otherwise change.
    """
    return await asyncio.to_thread(_upload_sync, base64_data, mime_type)
