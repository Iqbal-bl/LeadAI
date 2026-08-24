"""
Social publishing — post to the Facebook Page and Instagram account that THIS
company connected under Channels.

WHAT CHANGED VERSUS THE STANDALONE AGENT
----------------------------------------
The agent shipped as its own FastAPI app on port 8080, authenticated by a
shared `X-API-Key` and posting to whichever Page was named in the process's
environment variables. Two consequences made it unusable as a product surface:
every customer would have needed their own deployment, and its endpoints did
not appear in this application's Swagger at all.

Here the same operations are ordinary LeadAI routes:

  * auth is the identity-server token every other route already uses, so the
    frontend needs no second credential and the key sprawl disappears;
  * `scoped(...)` resolves the caller's company, and the target Page/Instagram
    account is looked up from that company's own `LeadChannelAccount` rows —
    a caller cannot name a Page, so a caller cannot post to a Page they do not
    own;
  * every publish is recorded against the company in `leadai_social_posts`.

ENDPOINT LAYOUT
---------------
  /social/platforms            what this company can publish to right now
  /social/posts                publish (direct, multi-platform) + history
  /social/facebook/*           Facebook-specific operations
  /social/instagram/*          Instagram-specific operations
  /social/topics               the content queue

The `/posts` route is the one to build a UI on: it takes a caption plus media
and fans out to every connected platform in one call. The per-platform routes
exist for the cases that genuinely differ (a Facebook multi-photo album, an
Instagram carousel) and for parity with the standalone API, so an existing
integration can be repointed by changing the base URL and the auth header.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from .. import activity
from ..activity import A
from ..db import get_leadai_db
from ..models import utcnow
from ..models_social import (
    POST_STATUS_PENDING,
    POST_STATUS_PUBLISHED,
    POST_STATUS_SCHEDULED,
    LeadSocialPost,
    LeadSocialTopic,
)
from ..rbac import Principal, assert_owns, scoped
from ..schemas import Ok
from ..services import jobs
from ..social import agent_bridge, service

from ..social.credentials import ChannelNotConnected, connected_platforms, resolve
from ..social.schemas import (
    AiPostRequest,
    DirectPostRequest,
    DirectUrlPostRequest,
    PlatformStatusOut,
    PostListOut,
    PostOut,
    ReplyAllRequest,
    SinglePlatformAiRequest,
    TopicCreate,
    TopicOut,
)
from social_agent.context import MissingCredentialsError, use_credentials
from social_agent.graph_api import instagram as graph_instagram
from social_agent.graph_api import leads as graph_leads
from social_agent.graph_api import pages as graph_pages

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/social", tags=["LeadAI • Social Publishing"])


# ===========================================================================
# Serialization
# ===========================================================================
def _post_out(row: LeadSocialPost) -> PostOut:
    return PostOut(
        id=row.Id,
        platforms=[p for p in (row.Platforms or "").split(",") if p],
        mode=row.Mode,
        status=row.Status,
        caption=row.Caption,
        topic=row.Topic,
        media_shape=row.MediaShape,
        media_urls=row.MediaUrls or [],
        results=row.Results,
        facebook_post_id=row.FacebookPostId,
        instagram_media_id=row.InstagramMediaId,
        duration_ms=row.DurationMs,
        error=row.Error,
        published_at=row.PublishedAt,
        created_at=row.CreatedAt,
    )


def _topic_out(row: LeadSocialTopic) -> TopicOut:
    return TopicOut(
        id=row.Id,
        topic=row.Topic,
        instructions=row.Instructions,
        image_url=row.ImageUrl,
        video_url=row.VideoUrl,
        platforms=[p for p in (row.Platforms or "").split(",") if p],
        status=row.Status,
        scheduled_for=row.ScheduledFor,
        attempts=row.Attempts or 0,
        last_error=row.LastError,
        post_id=row.PostId,
        created_at=row.CreatedAt,
    )


async def _run_single_platform(
    db: Session,
    client_id: str,
    platform: str,
    account_id: str | None,
    coro_factory,
):
    """Bind one company's credentials, run one Graph call, translate failures.

    Every per-platform endpoint below funnels through this so the mapping from
    "not connected" to 409 and "Meta rejected it" to 502 is written once.
    """
    try:
        creds = resolve(db, client_id, platform, account_id=account_id)
    except ChannelNotConnected as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    try:
        with use_credentials(creds):
            return await coro_factory()
    except MissingCredentialsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[social] %s call failed for %s: %s", platform, client_id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


# ===========================================================================
# Status
# ===========================================================================
@router.get(
    "/platforms",
    response_model=PlatformStatusOut,
    summary="Which platforms this company can publish to",
)
def platform_status(
    scope: tuple[Principal, str] = Depends(scoped("social.read", "channel.read")),
    db: Session = Depends(get_leadai_db),
):
    """Drives the publish screen's platform picker.

    A platform reports `connected: false` with the reason when the company has
    no active account for it, or has one with no access token — the two cases
    an operator can actually fix.
    """
    _, client_id = scope
    return PlatformStatusOut(platforms=connected_platforms(db, client_id))


def _validate_schedule_time(schedule_time: datetime | None) -> datetime | None:
    if schedule_time is None:
        return None
    if schedule_time.tzinfo is None:
        st = schedule_time.replace(tzinfo=timezone.utc)
    else:
        st = schedule_time.astimezone(timezone.utc)

    now = utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if st <= now:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"schedule_time must be in the future (UTC). Received {st.isoformat()}, current UTC time is {now.isoformat()}",
        )
    return st


# ===========================================================================
# Multi-platform publishing — the primary endpoint
# ===========================================================================
@router.post(
    "/posts",
    summary="Publish or schedule an exact caption + media to every connected platform",
    status_code=status.HTTP_201_CREATED,
)
async def create_direct_post(
    body: DirectPostRequest,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    """Media arrives as base64, is hosted once, and the resulting URL is reused
    for every platform — a 40 MB video is not re-uploaded per network.

    Platforms are attempted independently. Facebook has no native mixed-media
    or multi-video post, so such a request is reported as `skipped` for Facebook
    while Instagram's carousel still publishes; the response body carries a
    per-platform result either way, and the HTTP status is 201 as long as the
    request itself was well-formed.

    If `schedule_time` is provided (in UTC), the post is queued to publish automatically
    at that future time. If `schedule_time` is null, the post publishes immediately.
    """
    principal, client_id = scope
    st = _validate_schedule_time(body.schedule_time)

    try:
        uploaded = await service.upload_media_items(body.media)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Media upload failed: {exc}"
        ) from exc

    if st is not None:
        media_shape = service.classify_media(uploaded)
        row = LeadSocialPost(
            ClientId=client_id,
            ChannelAccountId=body.account_id,
            Platforms=",".join(body.platforms),
            Mode="direct",
            Caption=body.caption,
            MediaUrls=[m["url"] for m in uploaded],
            MediaShape=media_shape,
            Status=POST_STATUS_SCHEDULED,
            ScheduledFor=st,
            CreatedBy=principal.email,
        )
        db.add(row)
        db.flush()

        jobs.enqueue(
            db,
            kind="social.publish",
            payload={"post_id": row.Id},
            client_id=client_id,
            run_at=st,
            commit=True,
        )

        activity.log_principal(
            db, principal, action=A.CHANNEL_UPDATED, client_id=client_id,
            entity_type="social_post", entity_id=row.Id,
            message=f"Scheduled post for {st.isoformat()} on {', '.join(body.platforms)}",
            meta={"status": row.Status, "scheduled_for": st.isoformat(), "media_count": len(body.media)},
            request=request,
        )
        db.commit()
        return {
            "post_id": row.Id,
            "status": row.Status,
            "scheduled_for": st.isoformat(),
            "results": {p: {"scheduled": True, "scheduled_for": st.isoformat()} for p in body.platforms},
        }

    results, row = await service.publish(
        db,
        client_id,
        caption=body.caption,
        uploaded=uploaded,
        platforms=list(body.platforms),
        actor=principal.email,
        account_id=body.account_id,
        mode="direct",
    )

    activity.log_principal(
        db, principal, action=A.CHANNEL_UPDATED, client_id=client_id,
        entity_type="social_post", entity_id=row.Id if row else None,
        message=f"Published to {', '.join(body.platforms)}",
        meta={"status": row.Status if row else None, "media_count": len(body.media)},
        request=request,
    )
    db.commit()
    return {"post_id": row.Id if row else None, "status": row.Status if row else None, "results": results}


@router.post(
    "/posts/from-urls",
    summary="Publish or schedule using media that is already hosted",
    status_code=status.HTTP_201_CREATED,
)
async def create_direct_post_from_urls(
    body: DirectUrlPostRequest,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    """Same as POST /social/posts, but skips the upload step.

    Use this when the media already lives somewhere Meta can reach — a CDN, or
    a file previously uploaded through the documents API. The URLs must be
    publicly reachable over HTTPS: Meta's servers fetch them directly, so a
    presigned URL that expires in minutes, or anything behind a login, will
    fail on Meta's side rather than here.
    """
    principal, client_id = scope
    st = _validate_schedule_time(body.schedule_time)
    uploaded = [{"url": m.url, "is_video": m.is_video} for m in body.media]

    if st is not None:
        media_shape = service.classify_media(uploaded)
        row = LeadSocialPost(
            ClientId=client_id,
            ChannelAccountId=body.account_id,
            Platforms=",".join(body.platforms),
            Mode="direct",
            Caption=body.caption,
            MediaUrls=[m["url"] for m in uploaded],
            MediaShape=media_shape,
            Status=POST_STATUS_SCHEDULED,
            ScheduledFor=st,
            CreatedBy=principal.email,
        )
        db.add(row)
        db.flush()

        jobs.enqueue(
            db,
            kind="social.publish",
            payload={"post_id": row.Id},
            client_id=client_id,
            run_at=st,
            commit=True,
        )
        db.commit()
        return {
            "post_id": row.Id,
            "status": row.Status,
            "scheduled_for": st.isoformat(),
            "results": {p: {"scheduled": True, "scheduled_for": st.isoformat()} for p in body.platforms},
        }

    results, row = await service.publish(
        db, client_id,
        caption=body.caption,
        uploaded=uploaded,
        platforms=list(body.platforms),
        actor=principal.email,
        account_id=body.account_id,
        mode="direct",
    )
    db.commit()
    return {"post_id": row.Id if row else None, "status": row.Status if row else None, "results": results}



@router.post(
    "/posts/ai",
    summary="Let the AI agent write the caption, then publish",
    status_code=status.HTTP_201_CREATED,
)
async def create_ai_post(
    body: AiPostRequest,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    """Give a topic; the agent writes the caption (grounded in this company's
    knowledge base via CRAG) and generates an image when `image_url` is empty.

    Each platform is run as its own agent task, because the model writes
    differently for a Facebook post than for an Instagram caption, and because
    each platform publishes with a different connected account.
    """
    principal, client_id = scope

    row = LeadSocialPost(
        ClientId=client_id,
        ChannelAccountId=body.account_id,
        Platforms=",".join(body.platforms),
        Mode="ai",
        Topic=body.topic,
        Instructions=body.instructions or None,
        Status=POST_STATUS_PENDING,
        CreatedBy=principal.email,
    )
    db.add(row)
    db.flush()

    results: dict[str, dict] = {}
    for platform in body.platforms:
        kind = "video" if body.video_url else "post"
        params = {"topic": body.topic, "instructions": body.instructions}
        if body.video_url:
            params["video_url"] = body.video_url
        else:
            params["image_url"] = body.image_url
        try:
            results[platform] = await agent_bridge.run_task(
                db, client_id, platform, kind, params, account_id=body.account_id
            )
        except HTTPException as exc:
            results[platform] = {"success": False, "error": exc.detail}
        except Exception as exc:  # noqa: BLE001
            results[platform] = {"success": False, "error": str(exc)}

    succeeded = [p for p, r in results.items() if r.get("success")]
    row.Results = results
    row.Status = (
        POST_STATUS_PUBLISHED if len(succeeded) == len(body.platforms)
        else ("partial" if succeeded else "failed")
    )
    if succeeded:
        row.PublishedAt = utcnow()
    row.UpdatedAt = utcnow()
    db.commit()
    return {"post_id": row.Id, "status": row.Status, "results": results}


# ===========================================================================
# History
# ===========================================================================
@router.get("/posts", response_model=PostListOut, summary="Publishing history")
def list_posts(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    status_filter: str | None = Query(None, alias="status"),
    platform: str | None = Query(None),
    scope: tuple[Principal, str] = Depends(scoped("social.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    query = db.query(LeadSocialPost).filter(
        LeadSocialPost.ClientId == client_id,
        LeadSocialPost.IsDeleted == False,  # noqa: E712
    )
    if status_filter:
        query = query.filter(LeadSocialPost.Status == status_filter)
    if platform:
        query = query.filter(LeadSocialPost.Platforms.like(f"%{platform}%"))

    total = query.count()
    rows = (
        query.order_by(LeadSocialPost.CreatedAt.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return PostListOut(
        total_items=total, page=page, page_size=page_size,
        items=[_post_out(r) for r in rows],
    )


@router.get("/posts/{post_id}", response_model=PostOut, summary="One post's detail")
def get_post(
    post_id: str,
    scope: tuple[Principal, str] = Depends(scoped("social.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    row = db.get(LeadSocialPost, post_id)
    if row is None or row.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post not found")
    assert_owns(row.ClientId, client_id)
    return _post_out(row)


# ===========================================================================
# Facebook
# ===========================================================================
@router.post("/facebook/posts", summary="Facebook: AI-written post from a topic")
async def facebook_ai_post(
    body: SinglePlatformAiRequest,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    kind = "video" if body.video_url else "post"
    params = {"topic": body.topic, "instructions": body.instructions}
    params["video_url" if body.video_url else "image_url"] = body.video_url or body.image_url
    return await agent_bridge.run_task(
        db, client_id, "facebook", kind, params, account_id=body.account_id
    )


@router.post("/facebook/posts/direct", summary="Facebook: publish an exact caption")
async def facebook_direct(
    body: DirectUrlPostRequest,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    """Text-only when `media` is empty; single photo or video when it has one item."""
    principal, client_id = scope
    uploaded = [{"url": m.url, "is_video": m.is_video} for m in body.media]
    results, row = await service.publish(
        db, client_id, caption=body.caption, uploaded=uploaded,
        platforms=["facebook"], actor=principal.email, account_id=body.account_id,
    )
    db.commit()
    result = results.get("facebook", {})
    if not result.get("success"):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, result.get("error", "Publish failed"))
    return {"post_id": row.Id if row else None, **result}


@router.post("/facebook/posts/multi-photo", summary="Facebook: multi-photo album post")
async def facebook_multi_photo(
    body: DirectUrlPostRequest,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    if len(body.media) < 2:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "media needs at least 2 items — use /social/facebook/posts/direct for one photo.",
        )
    if any(m.is_video for m in body.media):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Facebook album posts are photos only. Post videos individually, or use "
            "Instagram's carousel for mixed media.",
        )
    _, client_id = scope
    urls = [m.url for m in body.media]
    return await _run_single_platform(
        db, client_id, "facebook", body.account_id,
        lambda: graph_pages.create_multi_photo_post(urls, caption=body.caption),
    )


@router.get("/facebook/posts", summary="Facebook: recent posts on the Page")
async def facebook_recent_posts(
    limit: int = Query(10, ge=1, le=100),
    account_id: str | None = Query(None),
    scope: tuple[Principal, str] = Depends(scoped("social.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    return await _run_single_platform(
        db, client_id, "facebook", account_id,
        lambda: graph_pages.get_recent_posts(limit),
    )


@router.delete("/facebook/posts/{post_id}", summary="Facebook: delete a post")
async def facebook_delete_post(
    post_id: str,
    account_id: str | None = Query(None),
    scope: tuple[Principal, str] = Depends(scoped("social.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Deletes on the company's own Page.

    Meta itself enforces the final check: a post id belonging to another
    company's Page is not addressable by this company's token, so the call
    fails at Meta even if an id is guessed.
    """
    _, client_id = scope
    return await _run_single_platform(
        db, client_id, "facebook", account_id,
        lambda: graph_pages.delete_post(post_id),
    )


@router.post("/facebook/comments/reply-all", summary="Facebook: AI-reply to new comments")
async def facebook_reply_comments(
    body: ReplyAllRequest,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    """Replies are grounded in this company's knowledge base, so two companies
    sharing this deployment get answers from their own documents."""
    _, client_id = scope
    return await agent_bridge.run_task(
        db, client_id, "facebook", "reply_comments",
        {"limit": body.limit}, account_id=body.account_id,
    )


@router.post("/facebook/messages/reply-all", summary="Facebook: AI-reply to new messages")
async def facebook_reply_messages(
    body: ReplyAllRequest,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    return await agent_bridge.run_task(
        db, client_id, "facebook", "reply_messages",
        {"limit": body.limit}, account_id=body.account_id,
    )


@router.get("/facebook/leads", summary="Facebook: retrieve Lead Ads submissions")
async def facebook_leads(
    form_id: str = Query(..., description="Lead form id on the company's Page."),
    limit: int = Query(50, ge=1, le=500),
    account_id: str | None = Query(None),
    scope: tuple[Principal, str] = Depends(scoped("social.read", "lead.read.all")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    return await _run_single_platform(
        db, client_id, "facebook", account_id,
        lambda: graph_leads.get_leads(form_id, limit),
    )


# ===========================================================================
# Instagram
# ===========================================================================
@router.post("/instagram/posts", summary="Instagram: AI-written post from a topic")
async def instagram_ai_post(
    body: SinglePlatformAiRequest,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    kind = "video" if body.video_url else "post"
    params = {"topic": body.topic, "instructions": body.instructions}
    params["video_url" if body.video_url else "image_url"] = body.video_url or body.image_url
    return await agent_bridge.run_task(
        db, client_id, "instagram", kind, params, account_id=body.account_id
    )


@router.post("/instagram/posts/direct", summary="Instagram: publish an exact caption")
async def instagram_direct(
    body: DirectUrlPostRequest,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    """Instagram has no text-only post, so `media` must contain at least one item."""
    if not body.media:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Instagram requires at least one image or video.",
        )
    principal, client_id = scope
    uploaded = [{"url": m.url, "is_video": m.is_video} for m in body.media]
    results, row = await service.publish(
        db, client_id, caption=body.caption, uploaded=uploaded,
        platforms=["instagram"], actor=principal.email, account_id=body.account_id,
    )
    db.commit()
    result = results.get("instagram", {})
    if not result.get("success"):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, result.get("error", "Publish failed"))
    return {"post_id": row.Id if row else None, **result}


@router.post("/instagram/posts/carousel", summary="Instagram: carousel (2–10 items)")
async def instagram_carousel(
    body: DirectUrlPostRequest,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    """Mixed images and videos are allowed — this is the only surface either
    platform offers that accepts both in a single post."""
    if not 2 <= len(body.media) <= 10:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "media needs 2–10 items (Instagram's carousel limit)."
        )
    _, client_id = scope
    items = [{"url": m.url, "is_video": m.is_video} for m in body.media]
    return await _run_single_platform(
        db, client_id, "instagram", body.account_id,
        lambda: graph_instagram.publish_carousel(items, caption=body.caption),
    )


@router.delete("/instagram/posts/{media_id}", summary="Instagram: delete a media item")
async def instagram_delete(
    media_id: str,
    account_id: str | None = Query(None),
    scope: tuple[Principal, str] = Depends(scoped("social.manage")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    return await _run_single_platform(
        db, client_id, "instagram", account_id,
        lambda: graph_instagram.delete_media(media_id),
    )


@router.post("/instagram/comments/reply-all", summary="Instagram: AI-reply to new comments")
async def instagram_reply_comments(
    body: ReplyAllRequest,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    return await agent_bridge.run_task(
        db, client_id, "instagram", "reply_comments",
        {"limit": body.limit}, account_id=body.account_id,
    )


@router.post("/instagram/messages/reply-all", summary="Instagram: AI-reply to new messages")
async def instagram_reply_messages(
    body: ReplyAllRequest,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    return await agent_bridge.run_task(
        db, client_id, "instagram", "reply_messages",
        {"limit": body.limit}, account_id=body.account_id,
    )


# ===========================================================================
# Content queue
# ===========================================================================
@router.get("/topics", response_model=list[TopicOut], summary="List queued content topics")
def list_topics(
    status_filter: str | None = Query(None, alias="status"),
    scope: tuple[Principal, str] = Depends(scoped("social.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    query = db.query(LeadSocialTopic).filter(
        LeadSocialTopic.ClientId == client_id,
        LeadSocialTopic.IsDeleted == False,  # noqa: E712
    )
    if status_filter:
        query = query.filter(LeadSocialTopic.Status == status_filter)
    return [_topic_out(r) for r in query.order_by(LeadSocialTopic.CreatedAt.desc()).all()]


@router.post(
    "/topics",
    response_model=TopicOut,
    status_code=status.HTTP_201_CREATED,
    summary="Queue a topic for later publishing",
)
def create_topic(
    body: TopicCreate,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    row = LeadSocialTopic(
        ClientId=client_id,
        Topic=body.topic,
        Instructions=body.instructions or None,
        ImageUrl=body.image_url or None,
        VideoUrl=body.video_url or None,
        Platforms=",".join(body.platforms),
        ChannelAccountId=body.account_id,
        ScheduledFor=body.scheduled_for,
        Status=POST_STATUS_PENDING,
        CreatedBy=principal.email,
    )
    db.add(row)
    db.commit()
    return _topic_out(row)


@router.post("/topics/{topic_id}/publish", summary="Publish a queued topic now")
async def publish_topic(
    topic_id: str,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    row = db.get(LeadSocialTopic, topic_id)
    if row is None or row.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    assert_owns(row.ClientId, client_id)

    platforms = [p for p in (row.Platforms or "").split(",") if p]
    results: dict[str, dict] = {}
    for platform in platforms:
        kind = "video" if row.VideoUrl else "post"
        params = {"topic": row.Topic, "instructions": row.Instructions or ""}
        params["video_url" if row.VideoUrl else "image_url"] = row.VideoUrl or row.ImageUrl or ""
        try:
            results[platform] = await agent_bridge.run_task(
                db, client_id, platform, kind, params, account_id=row.ChannelAccountId
            )
        except HTTPException as exc:
            results[platform] = {"success": False, "error": exc.detail}

    row.Attempts = (row.Attempts or 0) + 1
    succeeded = [p for p, r in results.items() if r.get("success")]
    if succeeded:
        row.Status = POST_STATUS_PUBLISHED
        row.LastError = None
    else:
        row.Status = "failed"
        row.LastError = "; ".join(str(r.get("error")) for r in results.values())[:2000]
    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()
    db.commit()
    return {"topic_id": row.Id, "status": row.Status, "results": results}


@router.delete("/topics/{topic_id}", response_model=Ok, summary="Remove a queued topic")
def delete_topic(
    topic_id: str,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    row = db.get(LeadSocialTopic, topic_id)
    if row is None or row.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    assert_owns(row.ClientId, client_id)
    row.IsDeleted = True
    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()
    db.commit()
    return Ok(message="Topic removed from the queue.")
