"""Cross-platform posting — one call posts the SAME generated content
(caption, and image/video) to both Facebook and Instagram. Content is
generated once inside a single agent run, not independently per
platform, so wording and image are guaranteed identical rather than two
separately-generated versions that happen to be similar."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent.orchestrator import run_agent_task
from api.auth import require_api_key
from api.request_logging import Timer, log_call

router = APIRouter(prefix="/posts", tags=["multi-platform"])


class PostRequest(BaseModel):
    topic: str
    instructions: str = ""
    image_url: str = ""  # leave empty to auto-generate one image, reused on both platforms


class VideoPostRequest(BaseModel):
    topic: str
    instructions: str = ""
    video_url: str  # same video used on both platforms


@router.post("")
async def create_post_both_platforms(body: PostRequest, client_id: str = Depends(require_api_key)):
    """Posts to Facebook and Instagram in one call, same caption/image."""
    with Timer() as t:
        result = await run_agent_task(
            "multi_platform_post_from_topic",
            {"topic": body.topic, "instructions": body.instructions, "image_url": body.image_url},
            use_browser=False,
            recursion_limit=40,
        )
    log_call(client_id, "both", "post", "/posts", body.model_dump(),
              result.get("success", False), t.duration_ms, str(result.get("result", "")))
    return result


@router.post("/video")
async def create_video_post_both_platforms(body: VideoPostRequest, client_id: str = Depends(require_api_key)):
    """Posts a video (Facebook) + Reel (Instagram) in one call, same caption."""
    with Timer() as t:
        result = await run_agent_task(
            "multi_platform_post_video",
            {"topic": body.topic, "instructions": body.instructions, "video_url": body.video_url},
            use_browser=False,
            recursion_limit=60,
        )
    log_call(client_id, "both", "post_video", "/posts/video", body.model_dump(),
              result.get("success", False), t.duration_ms, str(result.get("result", "")))
    return result