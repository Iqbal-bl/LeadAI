"""
Instagram Business/Creator account actions via Graph API. Requires
instagram_content_publish granted to the token, and IG_BUSINESS_ACCOUNT_ID
set in .env (the IG account's own numeric id, not its @username — find it
via GET /{page-id}?fields=instagram_business_account with your Page token).

IMPORTANT, easy to get bitten by: unlike Facebook Pages (pages.py), which
accept a direct local-file upload, Instagram's media container endpoint
only accepts a PUBLICLY REACHABLE image_url — Meta's servers fetch the
image themselves from that URL. A local file path on your machine won't
work at all. For local files, you need to host them somewhere reachable
first (a real bucket/CDN in production; something like ngrok pointed at a
local static file server works fine for testing).

Publishing is always two calls, not one:
    1. Create a "container" — Meta processes the image/video async
    2. Publish that container once it's done processing
"""

import asyncio
import json
import os

from .client import graph_get, graph_post,graph_delete
from .pages import _page_id


def _ig_user_id() -> str:
    """The Instagram professional account id for the company bound to this
    request, resolved from the credentials context (see social_agent/context.py)."""
    from social_agent.context import resolve_ig_user_id

    return resolve_ig_user_id()


async def create_media_container(
    image_url: str | None = None,
    caption: str = "",
    video_url: str | None = None,
    media_type: str | None = None,
) -> str:
    """Step 1: tells Meta to fetch and process the media. Pass image_url
    for a photo, or video_url + media_type="REELS" for a Reel. Returns a
    creation_id — pass this to publish_container() once processing is
    done (see wait_until_ready)."""
    data = {"caption": caption}
    if video_url:
        data["video_url"] = video_url
        data["media_type"] = media_type or "REELS"
    elif image_url:
        data["image_url"] = image_url
    else:
        raise ValueError("create_media_container needs image_url or video_url")
    result = await graph_post(f"{_ig_user_id()}/media", data=data)
    return result["id"]


async def get_container_status(creation_id: str) -> dict:
    """Returns {"status_code": ..., "status": ...} — status_code is the
    coarse state (EXPIRED/ERROR/FINISHED/IN_PROGRESS/PUBLISHED), status
    is a more detailed string that often explains WHY on error."""
    return await graph_get(creation_id, params={"fields": "status_code,status"})


async def wait_until_ready(creation_id: str, timeout_seconds: int = 60) -> None:
    """Photos usually finish in a couple seconds; videos/Reels can take
    much longer (use a bigger timeout_seconds for those — see
    publish_reel's default). Polls status_code until FINISHED, or raises
    on ERROR/EXPIRED/timeout, including Meta's detailed status message
    if there is one."""
    elapsed = 0
    interval = 2
    while elapsed < timeout_seconds:
        result = await get_container_status(creation_id)
        status_code = result.get("status_code")
        if status_code == "FINISHED":
            return
        if status_code in ("ERROR", "EXPIRED"):
            detail = result.get("status", "(no additional detail returned)")
            raise RuntimeError(f"Media container {creation_id} failed: {status_code} — {detail}")
        await asyncio.sleep(interval)
        elapsed += interval
    raise TimeoutError(f"Media container {creation_id} did not finish within {timeout_seconds}s")


async def publish_container(creation_id: str) -> dict:
    """Step 2: actually posts the finished container to the feed."""
    return await graph_post(
        f"{_ig_user_id()}/media_publish", data={"creation_id": creation_id}
    )


async def publish_photo(image_url: str, caption: str = "") -> dict:
    """Convenience wrapper: create container -> wait -> publish, in one call."""
    creation_id = await create_media_container(image_url=image_url, caption=caption)
    await wait_until_ready(creation_id)
    return await publish_container(creation_id)


async def publish_reel(video_url: str, caption: str = "", timeout_seconds: int = 300) -> dict:
    """Convenience wrapper for a Reel: create container -> wait -> publish.
    Video processing is much slower than photos — default timeout is 5
    minutes, bump it for longer videos."""
    creation_id = await create_media_container(video_url=video_url, caption=caption, media_type="REELS")
    await wait_until_ready(creation_id, timeout_seconds=timeout_seconds)
    return await publish_container(creation_id)

async def create_carousel_item_container(url: str, is_video: bool = False) -> str:
    """Creates ONE child container for a carousel — a photo or video that
    will become one slide. is_carousel_item=true is what marks it as a
    carousel child rather than a standalone post."""
    data = {"is_carousel_item": "true"}
    if is_video:
        data["video_url"] = url
        data["media_type"] = "VIDEO"
    else:
        data["image_url"] = url
    result = await graph_post(f"{_ig_user_id()}/media", data=data)
    return result["id"]


async def publish_carousel(items: list[dict], caption: str = "") -> dict:
    """items: [{"url": "...", "is_video": False}, ...] — 2 to 10 items,
    mixed photos/videos is fine (Meta's own limit, enforced here too).
    Creates each child container, waits for each to finish processing,
    creates the parent CAROUSEL container, waits for it, then publishes.
    Video children take much longer than photo children — this can take
    several minutes total for a mixed/video-heavy carousel."""
    if not 2 <= len(items) <= 10:
        raise ValueError("Carousels need 2-10 items (Instagram's own limit).")

    child_ids = []
    for item in items:
        child_id = await create_carousel_item_container(item["url"], item.get("is_video", False))
        # Videos need real processing time; photos are near-instant —
        # generous timeout covers either without slowing photo-only
        # carousels down in practice (returns as soon as FINISHED).
        await wait_until_ready(child_id, timeout_seconds=300)
        child_ids.append(child_id)

    parent_result = await graph_post(
        f"{_ig_user_id()}/media",
        data={"media_type": "CAROUSEL", "children": ",".join(child_ids), "caption": caption},
    )
    parent_id = parent_result["id"]
    await wait_until_ready(parent_id, timeout_seconds=60)
    return await publish_container(parent_id)

async def get_recent_media(limit: int = 10) -> dict:
    """Reads the IG account's recent media (id, caption, timestamp, permalink)."""
    return await graph_get(
        f"{_ig_user_id()}/media",
        params={"limit": limit, "fields": "id,caption,timestamp,permalink"},
    )


async def list_conversations(limit: int = 25) -> dict:
    """Lists recent Instagram DM conversations. Uses the PAGE's id, not
    the IG Business Account id — for the Facebook Login (Page-linked)
    flow this project uses, Instagram messaging rides on the Messenger
    Platform, scoped to the Page, same as messaging.py's Facebook
    conversations. Filter to Instagram-platform conversations via the
    platform param."""
    return await graph_get(
        f"{_page_id()}/conversations",
        params={"limit": limit, "platform": "instagram", "fields": "id,updated_time,unread_count,participants"},
    )


async def get_conversation_messages(conversation_id: str, limit: int = 10) -> dict:
    """Reads recent messages in one Instagram DM conversation."""
    return await graph_get(
        f"{conversation_id}/messages",
        params={"limit": limit, "fields": "id,message,from,created_time"},
    )


async def send_message(recipient_igsid: str, message: str) -> dict:
    """Sends an Instagram DM, as the account — via the Page's /messages
    edge (Messenger Platform), not the IG Business Account's. Meta only
    allows messaging someone who messaged you first, within a 24-hour
    window (7 days for support-only replies after that)."""
    return await graph_post(
        f"{_page_id()}/messages",
        data={
            "recipient": json.dumps({"id": recipient_igsid}),
            "message": json.dumps({"text": message}),
        },
    )


async def list_unreplied_conversations(limit: int = 25) -> list[dict]:
    """Convenience function: returns Instagram DM conversations whose
    latest message was NOT sent by us — i.e. still needs a reply.

    Deliberately does NOT use unread_count: Meta frequently omits that
    field entirely for Instagram conversations, which silently made
    every conversation look "already read" and get skipped. Checking who
    actually sent the latest message is reliable regardless.

    IMPORTANT: "us" here must be compared against _ig_user_id() (the
    Instagram Business Account ID), NOT _page_id() — the conversation's
    participants and each message's from.id are all Instagram-scoped
    IDs. Comparing against the Facebook Page ID never matches either
    participant, and next() on a filter that matches "everyone" just
    silently returns whichever participant happens to be first — which
    was our own account, causing replies to be sent to ourselves."""
    conversations = await list_conversations(limit)
    my_id = _ig_user_id()
    unreplied = []
    for convo in conversations.get("data", []):
        messages = await get_conversation_messages(convo["id"], limit=1)
        latest = messages.get("data", [{}])[0]
        if not latest:
            continue
        latest_sender_id = latest.get("from", {}).get("id", "")
        if latest_sender_id == my_id:
            continue  # we sent the last message — nothing to reply to

        participants = convo.get("participants", {}).get("data", [])
        sender = next((p for p in participants if p.get("id") != my_id), {})
        sender_igsid = sender.get("id", "") or latest_sender_id

        unreplied.append({
            "conversation_id": convo["id"],
            "sender_igsid": sender_igsid,
            "sender_name": sender.get("username", sender.get("name", "(unknown)")),
            "message": latest.get("message", ""),
        })
    return unreplied


async def _own_username() -> str:
    result = await graph_get(_ig_user_id(), params={"fields": "username"})
    return result.get("username", "")


async def get_comments_on_media(media_id: str, limit: int = 25) -> dict:
    """Reads a media's comments, including each comment's direct replies
    (IG only exposes one reply level via this API — no reply-to-reply
    chains like Facebook's comments.py handles)."""
    return await graph_get(
        f"{media_id}/comments",
        params={"limit": limit, "fields": "id,text,username,timestamp,replies{username,text}"},
    )


async def reply_to_comment(comment_id: str, message: str) -> dict:
    """Replies to a specific comment, as the account. Requires
    instagram_manage_comments. Returns {"id": "<new_reply_comment_id>"}."""
    return await graph_post(f"{comment_id}/replies", data={"message": message})


async def list_unreplied_comments(recent_media_limit: int = 50, comments_per_media: int = 25) -> list[dict]:
    """Convenience function: looks at the account's recent media, returns
    comments the account hasn't replied to yet — checked via each
    comment's own replies list against the account's own username.

    Returns [{"media_id", "comment_id", "message", "from_name"}, ...].
    """
    own_username = await _own_username()
    media = await get_recent_media(limit=recent_media_limit)
    unreplied = []
    for item in media.get("data", []):
        comments = await get_comments_on_media(item["id"], limit=comments_per_media)
        for comment in comments.get("data", []):
            replies = comment.get("replies", {}).get("data", [])
            already_replied = any(r.get("username") == own_username for r in replies)
            if not already_replied:
                unreplied.append({
                    "media_id": item["id"],
                    "comment_id": comment["id"],
                    "message": comment.get("text", ""),
                    "from_name": comment.get("username", "(unknown)"),
                })
    return unreplied


async def delete_media(media_id: str) -> dict:
    """Deletes an Instagram post, Reel, Story, or an entire carousel
    (pass the carousel's own container media_id, not a slide's id —
    Meta does not support deleting individual carousel slides, only the
    whole album). Requires instagram_manage_contents permission.
    Irreversible."""
    return await graph_delete(media_id)