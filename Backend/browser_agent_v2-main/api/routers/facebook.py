"""Facebook Page endpoints — posts, comments, messages. Every action
routes through the LLM agent (run_agent_task, use_browser=False), never
a raw direct graph_api call — keeps the same LLM+CRAG reasoning/policy
whether triggered by a person, a script, or a CRM."""

from fastapi import HTTPException
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent.orchestrator import run_agent_task
from api.auth import require_api_key
from api.request_logging import Timer, log_call
from graph_api import pages as graph_pages

router = APIRouter(prefix="/facebook", tags=["facebook"])


class PostRequest(BaseModel):
    topic: str
    instructions: str = ""
    image_url: str = ""  # leave empty to auto-generate an image


class VideoPostRequest(BaseModel):
    topic: str
    instructions: str = ""
    video_url: str


@router.post("/posts")
async def create_post(body: PostRequest, client_id: str = Depends(require_api_key)):
    with Timer() as t:
        result = await run_agent_task(
            "facebook_post_from_topic",
            {"topic": body.topic, "instructions": body.instructions, "image_url": body.image_url},
            use_browser=False,
        )
    log_call(client_id, "facebook", "post", "/facebook/posts", body.model_dump(),
              result.get("success", False), t.duration_ms, str(result.get("result", "")))
    return result


@router.post("/posts/video")
async def create_video_post(body: VideoPostRequest, client_id: str = Depends(require_api_key)):
    with Timer() as t:
        result = await run_agent_task(
            "facebook_post_video",
            {"topic": body.topic, "instructions": body.instructions, "video_url": body.video_url},
            use_browser=False,
        )
    log_call(client_id, "facebook", "post_video", "/facebook/posts/video", body.model_dump(),
              result.get("success", False), t.duration_ms, str(result.get("result", "")))
    return result


@router.post("/comments/reply-all")
async def reply_to_all_comments(client_id: str = Depends(require_api_key)):
    with Timer() as t:
        result = await run_agent_task("facebook_reply_to_comments", {}, use_browser=False)
    log_call(client_id, "facebook", "reply_comments", "/facebook/comments/reply-all", {},
              result.get("success", False), t.duration_ms, str(result.get("result", "")))
    return result


@router.post("/messages/reply-all")
async def reply_to_all_messages(client_id: str = Depends(require_api_key)):
    with Timer() as t:
        result = await run_agent_task("facebook_reply_to_messages", {}, use_browser=False)
    log_call(client_id, "facebook", "reply_messages", "/facebook/messages/reply-all", {},
              result.get("success", False), t.duration_ms, str(result.get("result", "")))
    return result

@router.delete("/posts/{post_id}")
async def delete_post(post_id: str, client_id: str = Depends(require_api_key)):
    with Timer() as t:
        try:
            result = await graph_pages.delete_post(post_id)
            success, error = True, None
        except Exception as e:
            result, success, error = None, False, str(e)
    log_call(client_id, "facebook", "delete_post", f"/facebook/posts/{post_id}", {"post_id": post_id},
              success, t.duration_ms, str(result), error)
    if not success:
        raise HTTPException(status_code=502, detail=error)
    return result