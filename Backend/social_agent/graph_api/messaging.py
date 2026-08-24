"""
Facebook Page Messenger — reading conversations and sending replies as
the Page. Requires pages_messaging granted to the token. Subject to
Meta's messaging policy: you can generally only message someone who
messaged the Page first, within its response window.
"""

import json

from .client import graph_get, graph_post
from .pages import _page_id


async def list_conversations(limit: int = 25) -> dict:
    """Lists recent Messenger conversations on the Page, each with its
    latest message snippet and unread count."""
    return await graph_get(
        f"{_page_id()}/conversations",
        params={"limit": limit, "fields": "id,updated_time,unread_count,snippet,participants"},
    )


async def get_conversation_messages(conversation_id: str, limit: int = 10) -> dict:
    """Reads recent messages in one conversation, newest first."""
    return await graph_get(
        f"{conversation_id}/messages",
        params={"limit": limit, "fields": "id,message,from,created_time"},
    )


async def send_message(recipient_id: str, message: str) -> dict:
    """Sends a message to a person, as the Page. recipient_id is that
    person's PSID (Page-Scoped ID) — get it from a conversation's
    participants list, not their regular Facebook user ID."""
    return await graph_post(
        "me/messages",
        data={
            "recipient": json.dumps({"id": recipient_id}),
            "message": json.dumps({"text": message}),
            "messaging_type": "RESPONSE",
        },
    )


async def list_unreplied_conversations(limit: int = 25) -> list[dict]:
    """Convenience function: returns conversations with unread_count > 0,
    each with the latest incoming message and the sender's PSID — enough
    to decide a reply and call send_message directly."""
    conversations = await list_conversations(limit)
    unreplied = []
    for convo in conversations.get("data", []):
        if convo.get("unread_count", 0) <= 0:
            continue
        messages = await get_conversation_messages(convo["id"], limit=1)
        latest = messages.get("data", [{}])[0]
        participants = convo.get("participants", {}).get("data", [])
        sender = next((p for p in participants if p.get("id") != _page_id()), {})
        unreplied.append({
            "conversation_id": convo["id"],
            "sender_psid": sender.get("id", ""),
            "sender_name": sender.get("name", "(unknown)"),
            "message": latest.get("message", ""),
        })
    return unreplied