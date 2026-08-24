"""
Uploads base64-encoded media to Cloudinary and returns a public URL —
the actual hosting step Meta's APIs need, since they require a real URL,
not raw bytes. Requires a free Cloudinary account: CLOUDINARY_CLOUD_NAME,
CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET in .env.
"""

import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

import cloudinary
import cloudinary.uploader

_configured = False


def _ensure_configured():
    global _configured
    if _configured:
        return
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
    api_key = os.getenv("CLOUDINARY_API_KEY")
    api_secret = os.getenv("CLOUDINARY_API_SECRET")
    if not all([cloud_name, api_key, api_secret]):
        raise ValueError(
            "Missing Cloudinary credentials in .env (CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET)"
        )
    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
    )
    _configured = True


def _upload_sync(base64_data: str, mime_type: str) -> str:
    _ensure_configured()
    # Strip existing data URI header if passed with one (e.g. data:image/png;base64,...)
    if "," in base64_data and base64_data.startswith("data:"):
        base64_data = base64_data.split(",", 1)[1]
    data_uri = f"data:{mime_type};base64,{base64_data}"
    resource_type = "video" if mime_type.startswith("video/") else "image"
    result = cloudinary.uploader.upload(data_uri, resource_type=resource_type)
    return result["secure_url"]


async def upload_base64(base64_data: str, mime_type: str) -> str:
    """base64_data: raw base64 string, no "data:...;base64," prefix
    needed — this builds it. mime_type: e.g. "image/jpeg", "video/mp4".
    Returns the public URL Cloudinary hosts it at. Cloudinary's SDK is
    synchronous, so this runs it off the event loop via asyncio.to_thread
    rather than blocking everything else while a large video uploads."""
    return await asyncio.to_thread(_upload_sync, base64_data, mime_type)