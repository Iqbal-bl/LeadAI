"""
Facebook Page comment reading and replying. Requires pages_read_engagement
(to read) and pages_manage_engagement (to reply) granted to the Page
Access Token in .env.

Note on scale: Meta doesn't expose a single "all comments across the
whole Page" endpoint — comments live under each individual post. For a
Page with heavy comment volume, the right long-term approach is Webhooks
(subscribe to the "feed" field, Meta pushes you new comment IDs in real
time) rather than polling every post repeatedly. This module is the
simpler polling-based version — good enough to start with, and the
functions here (reply_to_comment especially) are exactly what a webhook
handler would call too, so nothing here gets thrown away if you add
webhooks later.
"""

from .client import graph_get, graph_post
from .pages import _page_id, get_recent_posts


async def get_comments_on_post(post_id: str, limit: int = 25) -> dict:
    """Reads a single post's top-level comments."""
    return await graph_get(
        f"{post_id}/comments",
        params={"limit": limit, "fields": "id,message,from,created_time"},
    )


async def reply_to_comment(comment_id: str, message: str) -> dict:
    """Posts a reply to a specific comment, as the Page. Returns
    {"id": "<new_reply_comment_id>"}."""
    return await graph_post(f"{comment_id}/comments", data={"message": message})


async def _direct_replies(comment_id: str, limit: int = 25) -> list[dict]:
    """One level of direct replies to a single comment (every comment,
    including a reply itself, has its own /comments edge — this is what
    lets us walk arbitrarily deep, not just top-level -> first-level-reply)."""
    result = await graph_get(
        f"{comment_id}/comments",
        params={"limit": limit, "fields": "id,message,from,created_time"},
    )
    return result.get("data", [])


async def _collect_unreplied(comment: dict, post_id: str, page_id: str, depth: int, max_depth: int, out: list):
    replies = await _direct_replies(comment["id"])
    already_replied = any(r.get("from", {}).get("id") == page_id for r in replies)
    if not already_replied:
        out.append({
            "post_id": post_id,
            "comment_id": comment["id"],
            "message": comment.get("message", ""),
            "from_name": comment.get("from", {}).get("name", "(unknown)"),
            "depth": depth,
        })
    if depth < max_depth:
        for reply in replies:
            # Don't recurse into the Page's own replies — nothing to
            # answer there, and it'd just re-walk what we already posted.
            if reply.get("from", {}).get("id") != page_id:
                await _collect_unreplied(reply, post_id, page_id, depth + 1, max_depth, out)


async def list_unreplied_comments(recent_posts_limit: int = 50, comments_per_post: int = 25, max_depth: int = 5) -> list[dict]:
    """Convenience function: looks at the Page's most recent posts and
    returns EVERY comment the Page hasn't replied to yet — at ANY nesting
    depth, not just top-level comments. A comment that is itself a reply
    to another comment (a "reply to a reply") gets checked and included
    too, since every comment node has its own /comments edge for its own
    replies. max_depth bounds how far down a single thread this recurses
    (5 covers virtually any real conversation depth on Facebook).

    Returns a flat list of dicts: [{"post_id", "comment_id", "message",
    "from_name", "depth"}, ...] — "depth" is 0 for a top-level comment,
    1 for a reply to it, 2 for a reply to that reply, etc. Pass comment_id
    to reply_to_comment to answer any of them, regardless of depth.
    """
    page_id = _page_id()
    posts = await get_recent_posts(limit=recent_posts_limit)
    unreplied = []
    for post in posts.get("data", []):
        top_level = await get_comments_on_post(post["id"], limit=comments_per_post)
        for comment in top_level.get("data", []):
            await _collect_unreplied(comment, post["id"], page_id, depth=0, max_depth=max_depth, out=unreplied)
    return unreplied
