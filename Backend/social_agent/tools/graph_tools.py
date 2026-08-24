"""
Meta Graph API tools — official API calls, no browser involved. Prefer
these over browser automation whenever the target is your own Page/IG
Business account: officially supported, faster, UI-change-proof.
"""

import os

from langchain_core.tools import tool

from social_agent.graph_api import comments as graph_comments
from social_agent.graph_api import instagram as graph_instagram
from social_agent.graph_api import leads as graph_leads
from social_agent.graph_api import messaging as graph_messaging
from social_agent.graph_api import pages as graph_pages


@tool
async def graph_create_facebook_post(message: str, image_path: str | None = None) -> str:
    """Creates a post on the Facebook PAGE (not a personal profile — Graph
    API can't post to those). image_path can be a local file (uploaded
    directly) or a public URL."""
    try:
        if image_path:
            result = await graph_pages.create_photo_post(image_path, caption=message)
        else:
            result = await graph_pages.create_text_post(message)
        return f"Created Facebook Page post: {result}"
    except Exception as e:
        return f"Error creating Facebook Page post: {e}"


@tool
async def graph_publish_instagram_photo(image_url: str, caption: str = "") -> str:
    """Publishes a photo to the Instagram Business account. image_url
    MUST be a public URL — Instagram has no direct local-file upload."""
    try:
        result = await graph_instagram.publish_photo(image_url, caption)
        return f"Published Instagram photo: {result}"
    except Exception as e:
        return f"Error publishing Instagram photo: {e}"


@tool
async def graph_create_facebook_video_post(video_path_or_url: str, caption: str = "") -> str:
    """Posts a video to the Facebook Page. video_path_or_url can be a
    local file (uploaded directly) or a public URL."""
    try:
        result = await graph_pages.create_video_post(video_path_or_url, caption)
        return f"Created Facebook video post: {result}"
    except Exception as e:
        return f"Error creating Facebook video post: {e}"


@tool
async def graph_publish_instagram_reel(video_url: str, caption: str = "") -> str:
    """Publishes a Reel to the Instagram Business account. video_url
    MUST be a public URL. Video processing is slow — this call can take
    a couple minutes to return while it waits for Meta to finish
    processing before publishing."""
    try:
        result = await graph_instagram.publish_reel(video_url, caption)
        return f"Published Instagram Reel: {result}"
    except Exception as e:
        return f"Error publishing Instagram Reel: {e}"


@tool
async def graph_list_unreplied_comments(recent_posts_limit: int = 50) -> str:
    """Returns every unreplied comment across the Page's recent posts, at
    any nesting depth (including replies-to-replies). Each entry has a
    comment_id, commenter name, message, and depth. Use
    graph_reply_to_comment with a comment_id to respond."""
    try:
        result = await graph_comments.list_unreplied_comments(recent_posts_limit)
        if not result:
            return "No unreplied comments found."
        return str(result)
    except Exception as e:
        return f"Error listing unreplied comments: {e}"


@tool
async def graph_reply_to_comment(comment_id: str, message: str) -> str:
    """Posts a reply to a specific comment, as the Page."""
    try:
        result = await graph_comments.reply_to_comment(comment_id, message)
        return f"Replied to comment {comment_id}: {result}"
    except Exception as e:
        return f"Error replying to comment {comment_id}: {e}"


@tool
async def graph_list_unreplied_instagram_comments(recent_media_limit: int = 50) -> str:
    """Returns Instagram comments on recent media that haven't been
    replied to yet — each with media_id, comment_id, from_name, and
    message. Use graph_reply_to_instagram_comment with a comment_id to
    respond."""
    try:
        result = await graph_instagram.list_unreplied_comments(recent_media_limit)
        if not result:
            return "No unreplied Instagram comments found."
        return str(result)
    except Exception as e:
        return f"Error listing unreplied Instagram comments: {e}"


@tool
async def graph_reply_to_instagram_comment(comment_id: str, message: str) -> str:
    """Replies to a specific Instagram comment, as the account. Get
    comment_id from graph_list_unreplied_instagram_comments first."""
    try:
        result = await graph_instagram.reply_to_comment(comment_id, message)
        return f"Replied to Instagram comment {comment_id}: {result}"
    except Exception as e:
        return f"Error replying to Instagram comment {comment_id}: {e}"


@tool
async def graph_fetch_leads(form_id: str) -> str:
    """Fetches Lead Ads submissions for a form_id (use
    graph_list_lead_forms to find one)."""
    try:
        result = await graph_leads.get_leads(form_id)
        return str(result)
    except Exception as e:
        return f"Error fetching leads: {e}"


@tool
async def graph_list_lead_forms(page_id: str | None = None) -> str:
    """Lists Lead Ads forms on a Page. Defaults to .env's FB_PAGE_ID."""
    try:
        pid = page_id or os.environ["FB_PAGE_ID"]
        result = await graph_leads.list_lead_forms(pid)
        return str(result)
    except Exception as e:
        return f"Error listing lead forms: {e}"


@tool
async def graph_list_unreplied_messages(limit: int = 25) -> str:
    """Returns Page Messenger conversations with unread messages — each
    with conversation_id, sender_psid, sender_name, and their latest
    message. Use graph_reply_to_message with sender_psid to respond."""
    try:
        result = await graph_messaging.list_unreplied_conversations(limit)
        if not result:
            return "No unread Messenger conversations found."
        return str(result)
    except Exception as e:
        return f"Error listing unreplied messages: {e}"


@tool
async def graph_reply_to_message(sender_psid: str, message: str) -> str:
    """Sends a Messenger reply to a person, as the Page, using their
    sender_psid from graph_list_unreplied_messages."""
    try:
        result = await graph_messaging.send_message(sender_psid, message)
        return f"Sent message to {sender_psid}: {result}"
    except Exception as e:
        return f"Error sending message to {sender_psid}: {e}"


@tool
async def graph_list_unreplied_instagram_messages(limit: int = 25) -> str:
    """Returns Instagram DM conversations with unread messages — each
    with conversation_id, sender_igsid, sender_name, and their latest
    message. Use graph_reply_to_instagram_message to respond."""
    try:
        result = await graph_instagram.list_unreplied_conversations(limit)
        if not result:
            return "No unread Instagram DM conversations found."
        return str(result)
    except Exception as e:
        return f"Error listing unreplied Instagram messages: {e}"


@tool
async def graph_reply_to_instagram_message(sender_igsid: str, message: str) -> str:
    """Sends an Instagram DM reply to a person, using their sender_igsid
    from graph_list_unreplied_instagram_messages."""
    try:
        result = await graph_instagram.send_message(sender_igsid, message)
        return f"Sent Instagram message to {sender_igsid}: {result}"
    except Exception as e:
        return f"Error sending Instagram message to {sender_igsid}: {e}"


@tool
async def graph_create_facebook_multi_photo_post(image_urls: list[str], caption: str = "") -> str:
    """Creates ONE Facebook Page post with multiple photos attached (2+
    required — for a single photo use graph_create_facebook_post
    instead). Each URL can be a public URL or local file path."""
    try:
        result = await graph_pages.create_multi_photo_post(image_urls, caption)
        return f"Created Facebook multi-photo post: {result}"
    except Exception as e:
        return f"Error creating Facebook multi-photo post: {e}"


@tool
async def graph_publish_instagram_carousel(items: list[dict], caption: str = "") -> str:
    """Publishes an Instagram carousel — 2 to 10 photos/videos swiped
    through as one post. Each item in the list needs {"url": "...",
    "is_video": true/false}. All URLs must be public. Can take several
    minutes if the carousel includes video — wait for the result rather
    than assuming failure."""
    try:
        result = await graph_instagram.publish_carousel(items, caption)
        return f"Published Instagram carousel: {result}"
    except Exception as e:
        return f"Error publishing Instagram carousel: {e}"

@tool
async def graph_delete_facebook_post(post_id: str) -> str:
    """Deletes a Facebook Page post. post_id looks like
    "PAGEID_POSTID". Irreversible."""
    try:
        result = await graph_pages.delete_post(post_id)
        return f"Deleted Facebook post {post_id}: {result}"
    except Exception as e:
        return f"Error deleting Facebook post {post_id}: {e}"


@tool
async def graph_delete_instagram_media(media_id: str) -> str:
    """Deletes an Instagram post, Reel, Story, or entire carousel (pass
    the carousel's own container id, not a slide's — individual slides
    can't be deleted). Irreversible."""
    try:
        result = await graph_instagram.delete_media(media_id)
        return f"Deleted Instagram media {media_id}: {result}"
    except Exception as e:
        return f"Error deleting Instagram media {media_id}: {e}"


GRAPH_API_TOOLS = [
    graph_create_facebook_post,
    graph_create_facebook_video_post,
    graph_publish_instagram_photo,
    graph_publish_instagram_reel,
    graph_list_unreplied_comments,
    graph_reply_to_comment,
    graph_list_unreplied_instagram_comments,
    graph_reply_to_instagram_comment,
    graph_list_unreplied_messages,
    graph_reply_to_message,
    graph_list_unreplied_instagram_messages,
    graph_reply_to_instagram_message,
    graph_fetch_leads,
    graph_list_lead_forms,
    graph_create_facebook_multi_photo_post,
    graph_publish_instagram_carousel,
    graph_delete_facebook_post,
    graph_delete_instagram_media
]
