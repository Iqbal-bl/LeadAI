# --------------------------------------------------
# agentic_browser_v2 / api\routers\multi_platform_direct.py
# --------------------------------------------------
"""
Direct multi-platform posting — exact caption, no LLM. Media comes in as
base64 (image and/or video, any mix, any count) — each item gets
uploaded to MinIO once, then the resulting URL is reused across
every platform, so a video isn't re-uploaded per platform.

Each platform is attempted independently based on what it can actually
handle: Facebook has no native multi-video or mixed-media post (only
multi-photo), so a request that includes video alongside other items,
or multiple videos, gets SKIPPED for Facebook specifically — with a
clear reason in the response — while Instagram's carousel (which
supports mixed image+video, up to 10 items) still goes through. One
platform's limitation never blocks another platform's post.

TO ADD A NEW PLATFORM: implement a _post_<platform>(caption, uploaded,
media_shape) function below, add its media-shape support set to
PLATFORM_CAPABILITIES, and add both to PLATFORM_POSTERS. Nothing else
in this file needs to change.
"""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import require_api_key
from api.request_logging import Timer, log_call
from graph_api import instagram as graph_instagram
from graph_api import pages as graph_pages
from media_hosting import minio_upload

router = APIRouter(prefix="/direct", tags=["multi-platform-direct"])


class MediaItem(BaseModel):
    type: Literal["image", "video"]
    data: str        # raw base64 string, no "data:...;base64," prefix
    mime_type: str    # e.g. "image/jpeg", "video/mp4"


class DirectPostRequest(BaseModel):
    caption: str = ""
    media: list[MediaItem] = []   # empty = text-only (Facebook only)
    platforms: list[str] = ["facebook", "instagram"]


# --- Media shape classification -------------------------------------
# One request's media list gets classified ONCE into a shape; each
# platform then just checks "can I handle this shape" — no per-platform
# special-casing scattered through the endpoint logic.

def _classify_media(uploaded: list[dict]) -> str:
    if not uploaded:
        return "none"
    has_video = any(m["is_video"] for m in uploaded)
    has_image = any(not m["is_video"] for m in uploaded)
    if len(uploaded) == 1:
        return "single_video" if has_video else "single_image"
    if has_video and has_image:
        return "mixed_carousel"
    return "multi_video" if has_video else "multi_image"


PLATFORM_CAPABILITIES = {
    "facebook": {"none", "single_image", "single_video", "multi_image"},
    "instagram": {"single_image", "single_video", "multi_image", "multi_video", "mixed_carousel"},
}


# --- Per-platform posting functions -----------------------------------

async def _post_facebook(caption: str, uploaded: list[dict], media_shape: str) -> dict:
    if media_shape == "none":
        return await graph_pages.create_text_post(caption)
    if media_shape == "single_image":
        return await graph_pages.create_photo_post(uploaded[0]["url"], caption=caption)
    if media_shape == "single_video":
        return await graph_pages.create_video_post(uploaded[0]["url"], caption)
    if media_shape == "multi_image":
        return await graph_pages.create_multi_photo_post([m["url"] for m in uploaded], caption)
    raise ValueError(f"Facebook does not support: {media_shape}")


async def _post_instagram(caption: str, uploaded: list[dict], media_shape: str) -> dict:
    if media_shape == "none":
        raise ValueError("Instagram requires at least one image or video.")
    if media_shape == "single_image":
        return await graph_instagram.publish_photo(uploaded[0]["url"], caption=caption)
    if media_shape == "single_video":
        return await graph_instagram.publish_reel(uploaded[0]["url"], caption)
    # multi_image, multi_video, and mixed_carousel all go through the
    # same carousel call — Instagram's carousel handles all three shapes.
    return await graph_instagram.publish_carousel(uploaded, caption)


PLATFORM_POSTERS = {
    "facebook": _post_facebook,
    "instagram": _post_instagram,
}


@router.post("/posts")
async def direct_post_multi_platform(body: DirectPostRequest, client_id: str = Depends(require_api_key)):
    with Timer() as t:
        # Upload every media item ONCE, reused across all platforms.
        uploaded = []
        for item in body.media:
            url = await minio_upload.upload_base64(item.data, item.mime_type)
            uploaded.append({"url": url, "is_video": item.type == "video"})

        media_shape = _classify_media(uploaded)

        results = {}
        for platform in body.platforms:
            capabilities = PLATFORM_CAPABILITIES.get(platform)
            poster = PLATFORM_POSTERS.get(platform)
            if capabilities is None or poster is None:
                results[platform] = {"success": False, "error": f"Unsupported platform: {platform}"}
                continue
            if media_shape not in capabilities:
                results[platform] = {
                    "success": False,
                    "skipped": True,
                    "error": f"{platform} does not support this media combination ({media_shape}).",
                }
                continue
            try:
                result = await poster(body.caption, uploaded, media_shape)
                results[platform] = {"success": True, "result": result}
            except Exception as e:
                results[platform] = {"success": False, "error": str(e)}

    overall_success = any(r["success"] for r in results.values())
    log_call(
        client_id, ",".join(body.platforms), "multi_post_direct", "/direct/posts",
        {"caption": body.caption, "media_count": len(body.media), "media_shape": media_shape, "platforms": body.platforms},
        overall_success, t.duration_ms, str(results),
    )
    return results
