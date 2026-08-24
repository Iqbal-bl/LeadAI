"""Direct/verbatim posting — caption is used EXACTLY as given, no LLM
involved at all. Use these when you already have the exact caption
text. Use /facebook/posts, /instagram/posts (and their /video variants)
instead when you want the LLM to write the caption from a topic."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import require_api_key
from api.request_logging import Timer, log_call
from graph_api import instagram as graph_instagram
from graph_api import pages as graph_pages

router = APIRouter(tags=["direct-post"])


class FacebookDirectPostRequest(BaseModel):
    caption: str = ""
    image_url: str = ""  # optional — omit for a text-only post

class FacebookDirectVideoRequest(BaseModel):
    caption: str = ""
    video_url: str = ""

class InstagramDirectPostRequest(BaseModel):
    caption: str = ""
    image_url: str  # required — Instagram has no text-only posts

class InstagramDirectReelRequest(BaseModel):
    caption: str = ""
    video_url: str  

class MultiPhotoRequest(BaseModel):
    caption: str = ""
    image_urls: list[str]


class CarouselItem(BaseModel):
    url: str
    is_video: bool = False


class CarouselRequest(BaseModel):
    caption: str = ""
    items: list[CarouselItem]


@router.post("/facebook/posts/direct")
async def facebook_post_direct(body: FacebookDirectPostRequest, client_id: str = Depends(require_api_key)):
    with Timer() as t:
        try:
            if body.image_url:
                result = await graph_pages.create_photo_post(body.image_url, caption=body.caption)
            else:
                result = await graph_pages.create_text_post(body.caption)
            success, error = True, None
        except Exception as e:
            result, success, error = None, False, str(e)
    log_call(client_id, "facebook", "post_direct", "/facebook/posts/direct", body.model_dump(),
              success, t.duration_ms, str(result), error)
    if not success:
        raise HTTPException(status_code=502, detail=error)
    return result


@router.post("/instagram/posts/direct")
async def instagram_post_direct(body: InstagramDirectPostRequest, client_id: str = Depends(require_api_key)):
    with Timer() as t:
        try:
            result = await graph_instagram.publish_photo(body.image_url, caption=body.caption)
            success, error = True, None
        except Exception as e:
            result, success, error = None, False, str(e)
    log_call(client_id, "instagram", "post_direct", "/instagram/posts/direct", body.model_dump(),
              success, t.duration_ms, str(result), error)
    if not success:
        raise HTTPException(status_code=502, detail=error)
    return result

@router.post("/facebook/posts/direct_video")
async def facebook_post_direct_video(body: FacebookDirectVideoRequest, client_id: str = Depends(require_api_key)):
    with Timer() as t:
        try:
            result = await graph_pages.create_video_post(body.video_url,caption= body.caption)
            success, error = True, None
        except Exception as e:
            result,success,error = None,False,str(e)
    log_call(client_id,"facebook", "video_direct","/facebook/posts/direct_video",body.model_dump(),
            success, t.duration_ms, str(result),error)
    if not success:
        raise HTTPException(status_code=502,detail=error)
    return result

@router.post("/instagram/posts/direct_reel")
async def instagram_reel_direct(body: InstagramDirectReelRequest, client_id: str = Depends(require_api_key)):
    with Timer() as t:
        try:
            result = await graph_instagram.publish_reel(body.video_url, caption=body.caption)
            success, error = True, None
        except Exception as e:
            result, success, error = None, False, str(e)
    log_call(client_id, "instagram", "post_direct_reel", "/instagram/posts/direct_reel", body.model_dump(),
              success, t.duration_ms, str(result), error)
    if not success:
        raise HTTPException(status_code=502, detail=error)
    return result

@router.post("/facebook/posts/multi-photo")
async def facebook_multi_photo_direct(body: MultiPhotoRequest, client_id: str = Depends(require_api_key)):
    if len(body.image_urls) < 2:
        raise HTTPException(
            status_code=400,
            detail="image_urls needs at least 2 items — use /facebook/posts/direct for a single photo.",
        )
    with Timer() as t:
        try:
            result = await graph_pages.create_multi_photo_post(body.image_urls, caption=body.caption)
            success, error = True, None
        except Exception as e:
            result, success, error = None, False, str(e)
    log_call(client_id, "facebook", "multi_photo_direct", "/facebook/posts/multi-photo", body.model_dump(),
              success, t.duration_ms, str(result), error)
    if not success:
        raise HTTPException(status_code=502, detail=error)
    return result


@router.post("/instagram/posts/carousel")
async def instagram_carousel_direct(body: CarouselRequest, client_id: str = Depends(require_api_key)):
    if not 2 <= len(body.items) <= 10:
        raise HTTPException(status_code=400, detail="items needs 2-10 entries (Instagram's carousel limit).")
    items = [{"url": i.url, "is_video": i.is_video} for i in body.items]
    with Timer() as t:
        try:
            result = await graph_instagram.publish_carousel(items, caption=body.caption)
            success, error = True, None
        except Exception as e:
            result, success, error = None, False, str(e)
    log_call(client_id, "instagram", "carousel_direct", "/instagram/posts/carousel", body.model_dump(),
              success, t.duration_ms, str(result), error)
    if not success:
        raise HTTPException(status_code=502, detail=error)
    return result