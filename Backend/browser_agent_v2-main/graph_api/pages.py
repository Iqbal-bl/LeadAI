"""
Facebook Page actions. Requires pages_manage_posts (+ pages_read_engagement
for reading) granted to the Page Access Token in .env's FB_PAGE_ACCESS_TOKEN.
FB_PAGE_ID also needs to be set — find it via GET /me/accounts with your
user token, or on the Page's own "About" settings.
"""

import os
import json
from .client import graph_delete, graph_get, graph_post


def _page_id() -> str:
    page_id = os.environ.get("FB_PAGE_ID")
    if not page_id:
        raise ValueError("FB_PAGE_ID is not set in .env")
    return page_id


async def create_text_post(message: str) -> dict:
    """Creates a plain text post on the Page's feed. Returns {"id": "<page_id>_<post_id>"}."""
    return await graph_post(f"{_page_id()}/feed", data={"message": message})


async def create_photo_post(image_path: str, caption: str = "") -> dict:
    """Creates a single-photo post with an optional caption, in one call.
    image_path can be a local file path (uploaded directly as multipart
    form data — Facebook Pages support this, unlike Instagram which
    requires a public URL, see instagram.py) or a public image URL."""
    data = {"caption": caption, "published": "true"}
    if image_path.startswith("http://") or image_path.startswith("https://"):
        data["url"] = image_path
        return await graph_post(f"{_page_id()}/photos", data=data)
    with open(image_path, "rb") as f:
        return await graph_post(
            f"{_page_id()}/photos", data=data, files={"source": f}
        )


# async def create_video_post(video_path_or_url: str, caption: str = "") -> dict:
#     """Posts a video to the Page's feed. video_path_or_url can be a local
#     file path (uploaded directly) or a public video URL."""
#     data = {"description": caption}
#     if video_path_or_url.startswith("http://") or video_path_or_url.startswith("https://"):
#         data["file_url"] = video_path_or_url
#         return await graph_post(f"{_page_id()}/videos", data=data)
#     with open(video_path_or_url, "rb") as f:
#         return await graph_post(f"{_page_id()}/videos", data=data, files={"source": f})

async def create_video_post(video_path_or_url: str, caption: str = "") -> dict:
    """Posts a video to the Page's feed. video_path_or_url can be a local
    file path (uploaded directly) or a public video URL. Explicitly sets
    privacy to EVERYONE — videos have shown a pattern of not reliably
    reaching non-admin viewers via feed distribution otherwise, even
    when the video object itself is technically public."""
    data = {
        "description": caption,
        "privacy": json.dumps({"value": "Public"}),
    }
    if video_path_or_url.startswith("http://") or video_path_or_url.startswith("https://"):
        data["file_url"] = video_path_or_url
        return await graph_post(f"{_page_id()}/videos", data=data)
    with open(video_path_or_url, "rb") as f:
        return await graph_post(f"{_page_id()}/videos", data=data, files={"source": f})

async def create_multi_photo_post(image_paths_or_urls: list[str], caption: str = "") -> dict:
    """Creates ONE post with multiple photos attached (a Facebook album-
    style post, not separate posts). Each item can be a local file path
    or a public URL, mixed is fine. NOTE: Facebook Page posts don't
    support multiple videos in one native post the way photos work —
    this is photos only."""
    if len(image_paths_or_urls) < 2:
        raise ValueError("Use create_photo_post for a single image — this is for 2+ photos.")

    media_ids = []
    for item in image_paths_or_urls:
        data = {"published": "false"}
        if item.startswith("http://") or item.startswith("https://"):
            data["url"] = item
            result = await graph_post(f"{_page_id()}/photos", data=data)
        else:
            with open(item, "rb") as f:
                result = await graph_post(f"{_page_id()}/photos", data=data, files={"source": f})
        media_ids.append(result["id"])

    post_data = {"message": caption}
    for i, media_id in enumerate(media_ids):
        post_data[f"attached_media[{i}]"] = json.dumps({"media_fbid": media_id})
    return await graph_post(f"{_page_id()}/feed", data=post_data)

async def get_recent_posts(limit: int = 10) -> dict:
    """Reads the Page's most recent posts (id, message, created_time)."""
    return await graph_get(
        f"{_page_id()}/posts",
        params={"limit": limit, "fields": "id,message,created_time,permalink_url"},
    )


async def delete_post(post_id: str) -> dict:
    """Deletes a post by its id (as returned from create_text_post/create_photo_post)."""
    return await graph_delete(post_id)
