"""
Blog Automation & Settings Router for LeadAI.
Manages automated daily settings, channel configuration, and on-demand AI blog creation.
Supports standard scoped (/blog-settings, /blog/...) and explicit (/companies/{company_id}/...) endpoints.
"""
from __future__ import annotations

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from Domain.models import Client

from ..db import get_leadai_db
from ..models_blog import LeadBlogSettings
from ..rbac import Principal, assert_owns, require, resolve_scope
from ..security import encrypt_pii
from ..services.blog.article_service import ArticleService
from ..services.blog.generator_service import GeneratorService
from ..services.blog.topic_picker import TopicPickerService
from ..services.blog.schemas import (
    ArticleResponse,
    BlogSettingsIn,
    BlogSettingsOut,
    GenerateBlogRequest,
    GenerateBlogResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["LeadAI • Blog Settings & AI Generator"])


def _resolve_company(principal: Principal, explicit_company_id: Optional[str] = None) -> str:
    if explicit_company_id:
        if principal.is_platform_admin:
            return explicit_company_id
        user_scope = resolve_scope(principal)
        if explicit_company_id != user_scope:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Access denied to this company.")
        return user_scope
    return resolve_scope(principal)


# ===========================================================================
# 1. Company Blog Settings Endpoints
# ===========================================================================

@router.get(
    "/blog-settings",
    response_model=BlogSettingsOut,
    summary="Get automated blog creation settings for a company",
)
@router.get(
    "/companies/{company_id}/blog-settings",
    response_model=BlogSettingsOut,
    include_in_schema=False,
)
def get_blog_settings(
    company_id: Optional[str] = None,
    client_id: Optional[str] = Query(None),
    principal: Principal = Depends(require("settings.manage", "campaign.read", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    target_client = _resolve_company(principal, company_id or client_id)
    bs = db.query(LeadBlogSettings).filter(
        LeadBlogSettings.ClientId == target_client,
        LeadBlogSettings.IsDeleted == False,
    ).first()

    if not bs:
        bs = LeadBlogSettings(
            ClientId=target_client,
            IsAutoBlogEnabled=False,
            Mode="manual_confirmation",
            ScheduleTime="09:00",
            TargetWords=1000,
            IncludeImages=True,
            NumImages=1,
            TargetChannels=["linkedin", "wordpress"],
            AdminNotificationEmail=principal.email,
        )
        db.add(bs)
        db.commit()
        db.refresh(bs)

    return BlogSettingsOut(
        client_id=bs.ClientId,
        is_auto_blog_enabled=bs.IsAutoBlogEnabled,
        mode=bs.Mode,
        schedule_time=bs.ScheduleTime,
        last_run_at=bs.LastRunAt,
        next_run_at=bs.NextRunAt,
        topic_niche=bs.TopicNiche,
        keywords=bs.Keywords or [],
        target_audience=bs.TargetAudience,
        tone=bs.Tone,
        language=bs.Language or "English",
        target_words=bs.TargetWords or 1000,
        include_images=bs.IncludeImages,
        num_images=bs.NumImages or 1,
        cta_text=bs.CtaText,
        cta_url=bs.CtaUrl,
        target_channels=bs.TargetChannels or [],
        admin_notification_email=bs.AdminNotificationEmail,
        wordpress_url=bs.WordPressUrl,
        wordpress_username=bs.WordPressUsername,
        has_wordpress_password=bool(bs.WordPressAppPasswordEnc),
    )


@router.put(
    "/blog-settings",
    response_model=BlogSettingsOut,
    summary="Update automated blog creation settings and target channels for a company",
)
@router.put(
    "/companies/{company_id}/blog-settings",
    response_model=BlogSettingsOut,
    include_in_schema=False,
)
def update_blog_settings(
    payload: BlogSettingsIn,
    company_id: Optional[str] = None,
    client_id: Optional[str] = Query(None),
    principal: Principal = Depends(require("settings.manage")),
    db: Session = Depends(get_leadai_db),
):
    target_client = _resolve_company(principal, company_id or client_id)
    bs = db.query(LeadBlogSettings).filter(
        LeadBlogSettings.ClientId == target_client,
        LeadBlogSettings.IsDeleted == False,
    ).first()

    if not bs:
        bs = LeadBlogSettings(ClientId=target_client)
        db.add(bs)

    if payload.is_auto_blog_enabled is not None:
        bs.IsAutoBlogEnabled = payload.is_auto_blog_enabled
    if payload.mode is not None:
        bs.Mode = payload.mode
    if payload.schedule_time is not None:
        if bs.ScheduleTime != payload.schedule_time:
            bs.LastRunAt = None
        bs.ScheduleTime = payload.schedule_time
    if payload.topic_niche is not None:
        bs.TopicNiche = payload.topic_niche
    if payload.keywords is not None:
        bs.Keywords = payload.keywords
    if payload.target_audience is not None:
        bs.TargetAudience = payload.target_audience
    if payload.tone is not None:
        bs.Tone = payload.tone
    if payload.language is not None:
        bs.Language = payload.language
    if payload.target_words is not None:
        bs.TargetWords = payload.target_words
    if payload.include_images is not None:
        bs.IncludeImages = payload.include_images
    if payload.num_images is not None:
        bs.NumImages = payload.num_images
    if payload.cta_text is not None:
        bs.CtaText = payload.cta_text
    if payload.cta_url is not None:
        bs.CtaUrl = payload.cta_url
    if payload.target_channels is not None:
        bs.TargetChannels = payload.target_channels
    if payload.admin_notification_email is not None:
        bs.AdminNotificationEmail = payload.admin_notification_email
    if payload.wordpress_url is not None:
        bs.WordPressUrl = payload.wordpress_url
    if payload.wordpress_username is not None:
        bs.WordPressUsername = payload.wordpress_username
    if payload.wordpress_app_password is not None:
        if payload.wordpress_app_password.strip():
            bs.WordPressAppPasswordEnc = encrypt_pii(payload.wordpress_app_password.strip())
        else:
            bs.WordPressAppPasswordEnc = None

    db.commit()
    db.refresh(bs)

    if bs.IsAutoBlogEnabled:
        from ..services.jobs import bootstrap_blog_job
        bootstrap_blog_job(db)

    return BlogSettingsOut(
        client_id=bs.ClientId,
        is_auto_blog_enabled=bs.IsAutoBlogEnabled,
        mode=bs.Mode,
        schedule_time=bs.ScheduleTime,
        last_run_at=bs.LastRunAt,
        next_run_at=bs.NextRunAt,
        topic_niche=bs.TopicNiche,
        keywords=bs.Keywords or [],
        target_audience=bs.TargetAudience,
        tone=bs.Tone,
        language=bs.Language or "English",
        target_words=bs.TargetWords or 1000,
        include_images=bs.IncludeImages,
        num_images=bs.NumImages or 1,
        cta_text=bs.CtaText,
        cta_url=bs.CtaUrl,
        target_channels=bs.TargetChannels or [],
        admin_notification_email=bs.AdminNotificationEmail,
        wordpress_url=bs.WordPressUrl,
        wordpress_username=bs.WordPressUsername,
        has_wordpress_password=bool(bs.WordPressAppPasswordEnc),
    )


@router.post(
    "/blog-settings/trigger-run",
    summary="Trigger the automated blog creation pipeline immediately for testing",
)
@router.post(
    "/companies/{company_id}/blog-settings/trigger-run",
    include_in_schema=False,
)
def trigger_automated_blog_run(
    company_id: Optional[str] = None,
    client_id: Optional[str] = Query(None),
    principal: Principal = Depends(require("settings.manage", "campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    target_client = _resolve_company(principal, company_id or client_id)
    bs = db.query(LeadBlogSettings).filter(
        LeadBlogSettings.ClientId == target_client,
        LeadBlogSettings.IsDeleted == False,
    ).first()

    if not bs:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Blog settings not found.")

    from ..services.blog.topic_picker import TopicPickerService
    from ..services.jobs import enqueue
    from ..models import Client, utcnow

    client = db.query(Client).filter(Client.Id == target_client, Client.IsDeleted == False).first()
    company_name = client.Name if client else "Your Organization"

    topic, keywords = TopicPickerService.pick_daily_topic(db, target_client, bs)
    now = utcnow()

    job = enqueue(
        db,
        "blog.generate",
        payload={
            "client_id": target_client,
            "company_name": company_name,
            "topic": topic,
            "keywords": keywords,
            "mode": bs.Mode,
            "tone": bs.Tone,
            "target_audience": bs.TargetAudience,
            "target_words": bs.TargetWords,
            "include_images": bs.IncludeImages,
            "num_images": bs.NumImages,
            "cta_text": bs.CtaText,
            "cta_url": bs.CtaUrl,
            "target_channels": bs.TargetChannels or ["wordpress"],
            "admin_email": bs.AdminNotificationEmail,
        },
        client_id=target_client,
        commit=True,
    )

    bs.LastRunAt = now
    db.commit()

    return {
        "status": "queued",
        "job_id": job.Id,
        "topic": topic,
        "keywords": keywords,
        "message": f"Automated generation queued for topic '{topic}'.",
    }


# ===========================================================================
# 2. AI Blog Generation Endpoints
# ===========================================================================

@router.post(
    "/blog/generate",
    response_model=GenerateBlogResponse,
    summary="Instant AI generation (returns content preview without saving to database)",
)
@router.post(
    "/companies/{company_id}/blog/generate",
    response_model=GenerateBlogResponse,
    include_in_schema=False,
)
def generate_blog_preview(
    payload: GenerateBlogRequest,
    company_id: Optional[str] = None,
    client_id: Optional[str] = Query(None),
    principal: Principal = Depends(require("campaign.manage")),
):
    target_client = _resolve_company(principal, company_id or client_id)
    if not payload.topic.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Topic is required.")
    return GeneratorService.generate_blog(payload)


@router.post(
    "/blog/generate-and-save",
    response_model=ArticleResponse,
    summary="Generate blog, save Article & Version 1, and auto-publish or dispatch review email",
)
@router.post(
    "/companies/{company_id}/blog/generate-and-save",
    response_model=ArticleResponse,
    include_in_schema=False,
)
def generate_and_save_article(
    payload: GenerateBlogRequest,
    company_id: Optional[str] = None,
    client_id: Optional[str] = Query(None),
    principal: Principal = Depends(require("campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    target_client = _resolve_company(principal, company_id or client_id)
    if not payload.topic.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Topic is required.")

    client = db.query(Client).filter(Client.Id == target_client, Client.IsDeleted == False).first()
    company_name = client.Name if client else "Your Organization"

    return ArticleService.generate_and_save(
        db=db,
        client_id=target_client,
        req=payload,
        company_name=company_name,
    )


@router.post(
    "/blog/generate-daily",
    response_model=ArticleResponse,
    summary="1-Click Daily Run: picks trending topic automatically and executes complete workflow",
)
@router.post(
    "/companies/{company_id}/blog/generate-daily",
    response_model=ArticleResponse,
    include_in_schema=False,
)
def trigger_daily_blog_run(
    company_id: Optional[str] = None,
    client_id: Optional[str] = Query(None),
    principal: Principal = Depends(require("campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Picks a trending niche topic for the company and executes generation."""
    target_client = _resolve_company(principal, company_id or client_id)
    bs = db.query(LeadBlogSettings).filter(
        LeadBlogSettings.ClientId == target_client,
        LeadBlogSettings.IsDeleted == False,
    ).first()

    topic, keywords = TopicPickerService.pick_daily_topic(db, target_client, bs)
    client = db.query(Client).filter(Client.Id == target_client, Client.IsDeleted == False).first()
    company_name = client.Name if client else "Your Organization"

    req = GenerateBlogRequest(
        topic=topic,
        keywords=keywords,
        tone=bs.Tone if bs else "thought_leadership",
        target_audience=bs.TargetAudience if bs else "Business Leaders",
        target_words=bs.TargetWords if bs else 1000,
        include_images=bs.IncludeImages if bs else True,
        num_images=bs.NumImages if bs else 1,
        cta_text=bs.CtaText if bs else "Book Free Strategy Session Today",
        cta_url=bs.CtaUrl if bs else "#strategy-session",
        generation_mode="daily_scheduler",
        admin_reviewer_email=bs.AdminNotificationEmail if bs else principal.email,
        target_channels=bs.TargetChannels if bs else ["wordpress"],
    )

    return ArticleService.generate_and_save(
        db=db,
        client_id=target_client,
        req=req,
        company_name=company_name,
    )
