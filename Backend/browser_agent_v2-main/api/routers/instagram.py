"""Instagram endpoints — same pattern as facebook.py."""

from fastapi import HTTPException
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent.orchestrator import run_agent_task
from api.auth import require_api_key
from api.request_logging import Timer, log_call
from graph_api import instagram as graph_instagram

router = APIRouter(prefix="/instagram", tags=["instagram"])


class PostRequest(BaseModel):
    topic: str
    instructions: str = ""
    image_url: str = ""  # leave empty to auto-generate an image


class ReelRequest(BaseModel):
    topic: str
    instructions: str = ""
    video_url: str


@router.post("/posts")
async def create_post(body: PostRequest, client_id: str = Depends(require_api_key)):
    with Timer() as t:
        result = await run_agent_task(
            "instagram_post_from_topic",
            {"topic": body.topic, "instructions": body.instructions, "image_url": body.image_url},
            use_browser=False,
        )
    log_call(client_id, "instagram", "post", "/instagram/posts", body.model_dump(),
              result.get("success", False), t.duration_ms, str(result.get("result", "")))
    return result


@router.post("/posts/reel")
async def create_reel(body: ReelRequest, client_id: str = Depends(require_api_key)):
    with Timer() as t:
        result = await run_agent_task(
            "instagram_post_reel",
            {"topic": body.topic, "instructions": body.instructions, "video_url": body.video_url},
            use_browser=False,
            recursion_limit=60,  # video processing needs more polling turns
        )
    log_call(client_id, "instagram", "post_reel", "/instagram/posts/reel", body.model_dump(),
              result.get("success", False), t.duration_ms, str(result.get("result", "")))
    return result


@router.post("/comments/reply-all")
async def reply_to_all_comments(client_id: str = Depends(require_api_key)):
    with Timer() as t:
        result = await run_agent_task("instagram_reply_to_comments", {}, use_browser=False)
    log_call(client_id, "instagram", "reply_comments", "/instagram/comments/reply-all", {},
              result.get("success", False), t.duration_ms, str(result.get("result", "")))
    return result


@router.post("/messages/reply-all")
async def reply_to_all_messages(client_id: str = Depends(require_api_key)):
    with Timer() as t:
        result = await run_agent_task("instagram_reply_to_messages", {}, use_browser=False)
    log_call(client_id, "instagram", "reply_messages", "/instagram/messages/reply-all", {},
              result.get("success", False), t.duration_ms, str(result.get("result", "")))
    return result


@router.delete("/posts/{media_id}")
async def delete_media(media_id: str, client_id: str = Depends(require_api_key)):
    with Timer() as t:
        try:
            result = await graph_instagram.delete_media(media_id)
            success, error = True, None
        except Exception as e:
            result, success, error = None, False, str(e)
    log_call(client_id, "instagram", "delete_media", f"/instagram/posts/{media_id}", {"media_id": media_id},
              success, t.duration_ms, str(result), error)
    if not success:
        raise HTTPException(status_code=502, detail=error)
    return result