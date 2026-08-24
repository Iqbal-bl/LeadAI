"""
Tenant-aware bridge to the LangGraph agent.

WHY THIS IS A SEPARATE MODULE WITH LAZY IMPORTS
-----------------------------------------------
The AI path pulls in langchain, langgraph, the CRAG stack (FAISS, embeddings)
and, for browser tasks, Playwright. The direct publishing path needs none of
them. Importing the agent at module load would therefore make the whole LeadAI
API — including the Channels and Campaigns routes that have nothing to do with
this — fail to start on a deployment that only wants direct posting.

So the import happens inside `run_task()`, and an ImportError becomes a clean
503 explaining which extra to install, rather than a stack trace at boot.

CREDENTIAL PROPAGATION THROUGH THE AGENT
----------------------------------------
The agent's tools ultimately call the same `social_agent.graph_api` functions
as the direct path, which read the credential ContextVar. Because asyncio
copies the context into every task it spawns, binding the credentials here —
around the `await` — covers every tool call the graph makes downstream,
including ones running concurrently inside the graph. There is no need for the
agent itself to know anything about tenancy.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from social_agent.context import use_credentials

from .credentials import ChannelNotConnected, resolve

logger = logging.getLogger(__name__)

# Task name in the agent registry for each (platform, kind) pair.
TASK_NAMES = {
    ("facebook", "post"): "facebook_post_from_topic",
    ("facebook", "video"): "facebook_post_video",
    ("facebook", "reply_comments"): "facebook_reply_to_comments",
    ("facebook", "reply_messages"): "facebook_reply_to_messages",
    ("instagram", "post"): "instagram_post_from_topic",
    ("instagram", "video"): "instagram_post_reel",
    ("instagram", "reply_comments"): "instagram_reply_to_comments",
    ("instagram", "reply_messages"): "instagram_reply_to_messages",
}

# Reels and videos need far more polling turns than a photo post before the
# container reports FINISHED, so they get a bigger recursion budget.
RECURSION_LIMITS = {"video": 60}


def _load_runner():
    try:
        from social_agent.agent.orchestrator import run_agent_task
    except ImportError as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The AI content agent is not installed in this deployment. Install the "
            "agent extras (langchain, langgraph and the CRAG dependencies) to use "
            "topic-based posting, or use the direct endpoints, which publish an "
            f"exact caption and need none of it. ({exc})",
        ) from exc
    return run_agent_task


async def run_task(
    db: Session,
    client_id: str,
    platform: str,
    kind: str,
    params: dict | None = None,
    account_id: str | None = None,
) -> dict:
    """Run one named agent task as one company.

    `use_browser=False` throughout: every task exposed here is a Graph API task,
    and launching Playwright for them would add a browser process per request
    for no benefit. The browser-driven tasks remain available to the standalone
    scheduler, which is where they belong.
    """
    task_name = TASK_NAMES.get((platform, kind))
    if task_name is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"No agent task for {platform}/{kind}."
        )

    try:
        creds = resolve(db, client_id, platform, account_id=account_id)
    except ChannelNotConnected as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    run_agent_task = _load_runner()

    kwargs = {}
    if kind in RECURSION_LIMITS:
        kwargs["recursion_limit"] = RECURSION_LIMITS[kind]

    with use_credentials(creds):
        result = await run_agent_task(
            task_name, params or {}, use_browser=False, **kwargs
        )

    if isinstance(result, dict):
        result.setdefault("account_id", creds.account_id)
        result.setdefault("account_name", creds.account_name)
    return result
