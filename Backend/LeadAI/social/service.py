"""
Publishing service — runs the vendored agent's Graph calls inside one company's
credential context and records the outcome.

WHY THE PER-PLATFORM LOOP LIVES HERE
------------------------------------
The standalone agent's `/direct/posts` looped over platforms while a single set
of environment credentials stayed constant. Here each platform resolves to a
DIFFERENT `LeadChannelAccount` (a company's Page row and its Instagram row are
separate records, with different ids and possibly different tokens), so the
credential context has to be re-bound inside the loop, per platform. That is
also why one platform failing never aborts the others: they are independent
accounts, and a company whose Instagram token expired should still get its
Facebook post.

The media-shape capability table is carried over from the original
`multi_platform_direct.py` unchanged in meaning: Facebook Pages have no native
mixed-media or multi-video post, so such a request is *skipped* for Facebook
with a stated reason rather than failed, while Instagram's carousel still runs.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from social_agent.context import MissingCredentialsError, use_credentials
from social_agent.graph_api import instagram as graph_instagram
from social_agent.graph_api import pages as graph_pages

from ..models import utcnow
from ..models_social import (
    POST_STATUS_FAILED,
    POST_STATUS_PARTIAL,
    POST_STATUS_PUBLISHED,
    POST_STATUS_PUBLISHING,
    LeadSocialPost,
)
from .credentials import ChannelNotConnected, resolve

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Media shape classification (carried over from the standalone agent)
# --------------------------------------------------------------------------
def classify_media(uploaded: list[dict]) -> str:
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
    return await graph_instagram.publish_carousel(uploaded, caption)


PLATFORM_POSTERS = {
    "facebook": _post_facebook,
    "instagram": _post_instagram,
}


def _extract_id(platform: str, result: dict | None) -> str | None:
    if not isinstance(result, dict):
        return None
    return result.get("id") or result.get("post_id") or result.get("media_id")


# --------------------------------------------------------------------------
# The publish entry point
# --------------------------------------------------------------------------
async def publish(
    db: Session,
    client_id: str,
    caption: str,
    uploaded: list[dict],
    platforms: list[str],
    *,
    actor: str = "system",
    account_id: str | None = None,
    mode: str = "direct",
    topic: str | None = None,
    instructions: str | None = None,
    campaign_id: str | None = None,
    record: bool = True,
) -> tuple[dict, LeadSocialPost | None]:
    """Publish one caption + media set to one company's connected platforms.

    Returns `(results_by_platform, post_row)`. The row is committed by the
    caller's session so the publish and its audit record share a transaction
    boundary with whatever else the request is doing.
    """
    media_shape = classify_media(uploaded)
    started = time.perf_counter()

    row: LeadSocialPost | None = None
    if record:
        row = LeadSocialPost(
            ClientId=client_id,
            ChannelAccountId=account_id,
            Platforms=",".join(platforms),
            Mode=mode,
            MediaShape=media_shape,
            Topic=(topic or None),
            Instructions=(instructions or None),
            Caption=caption,
            MediaUrls=[m["url"] for m in uploaded],
            Status=POST_STATUS_PUBLISHING,
            CampaignId=campaign_id,
            CreatedBy=actor,
        )
        db.add(row)
        db.flush()  # gives the row an Id for logging before the network calls

    results: dict[str, dict] = {}

    for platform in platforms:
        platform = platform.strip().lower()
        capabilities = PLATFORM_CAPABILITIES.get(platform)
        poster = PLATFORM_POSTERS.get(platform)

        if capabilities is None or poster is None:
            results[platform] = {"success": False, "error": f"Unsupported platform: {platform}"}
            continue

        if media_shape not in capabilities:
            # Skipped, not failed — a documented platform limitation, and the
            # other platforms in this request must still go through.
            results[platform] = {
                "success": False,
                "skipped": True,
                "error": f"{platform} does not support this media combination ({media_shape}).",
            }
            continue

        # Credentials are resolved and bound PER PLATFORM: Facebook and
        # Instagram are different connected accounts for the same company.
        try:
            creds = resolve(db, client_id, platform, account_id=account_id)
        except ChannelNotConnected as exc:
            results[platform] = {"success": False, "not_connected": True, "error": str(exc)}
            continue

        try:
            with use_credentials(creds):
                result = await poster(caption, uploaded, media_shape)
            results[platform] = {
                "success": True,
                "account_id": creds.account_id,
                "account_name": creds.account_name,
                "result": result,
                "id": _extract_id(platform, result),
            }
        except MissingCredentialsError as exc:
            results[platform] = {"success": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[social] %s publish failed for client %s: %s", platform, client_id, exc
            )
            results[platform] = {"success": False, "error": str(exc)}

    duration_ms = int((time.perf_counter() - started) * 1000)

    succeeded = [p for p, r in results.items() if r.get("success")]
    failed = [p for p, r in results.items() if not r.get("success")]

    if row is not None:
        row.Results = results
        row.DurationMs = duration_ms
        if succeeded and not failed:
            row.Status = POST_STATUS_PUBLISHED
        elif succeeded:
            row.Status = POST_STATUS_PARTIAL
        else:
            row.Status = POST_STATUS_FAILED
            row.Error = "; ".join(
                f"{p}: {results[p].get('error')}" for p in failed
            )[:2000]
        if succeeded:
            row.PublishedAt = utcnow()
        fb = results.get("facebook") or {}
        ig = results.get("instagram") or {}
        row.FacebookPostId = fb.get("id")
        row.InstagramMediaId = ig.get("id")
        row.UpdatedAt = utcnow()

    return results, row


async def upload_media_items(items: list) -> list[dict]:
    """Host base64 media once, reuse the URL across every platform.

    Meta fetches media from a public URL itself, so an inline upload is not an
    option for Instagram at all. Uploading once (rather than per platform) also
    means a 40 MB video crosses the wire to the object store a single time.
    """
    from social_agent.media_hosting import minio_upload

    uploaded: list[dict] = []
    for item in items:
        url = await minio_upload.upload_base64(item.data, item.mime_type)
        uploaded.append({"url": url, "is_video": item.type == "video"})
    return uploaded


def urls_to_media(urls: list[str], video_flags: list[bool] | None = None) -> list[dict]:
    """Adapt already-public URLs into the shape the posters expect."""
    flags = video_flags or [False] * len(urls)
    return [{"url": u, "is_video": bool(f)} for u, f in zip(urls, flags)]
