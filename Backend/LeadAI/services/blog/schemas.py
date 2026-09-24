"""
Pydantic schemas for the LeadAI Blog & Content Generation Engine.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


# ===========================================================================
# 1. LangGraph Core Schemas
# ===========================================================================

class RouterDecision(BaseModel):
    needs_research: bool = Field(
        description="Whether the topic requires external web research."
    )
    mode: Literal["closed_book", "hybrid", "open_book"] = Field(
        description="closed_book: evergreen; hybrid: evergreen with recent examples; open_book: highly volatile news/roundups."
    )
    queries: List[str] = Field(
        default_factory=list,
        description="Search queries if research is needed."
    )


class EvidenceItem(BaseModel):
    title: str
    url: str
    snippet: str
    published_at: Optional[str] = None
    source: Optional[str] = None


class EvidencePack(BaseModel):
    evidence: List[EvidenceItem] = Field(
        default_factory=list,
        description="Deduplicated research evidence items."
    )


class Task(BaseModel):
    id: int
    title: str
    goal: str
    bullets: List[str] = Field(default_factory=list)
    target_words: int = 250
    tags: List[str] = Field(default_factory=list)
    requires_research: bool = False
    requires_citations: bool = False
    requires_code: bool = False


class Plan(BaseModel):
    blog_title: str
    target_words: int
    tone: str = "professional"
    audience: str = "Business Leaders and Professionals"
    blog_kind: str = "deep_dive"
    constraints: List[str] = Field(default_factory=list)
    tasks: List[Task] = Field(default_factory=list)


class ImageSpec(BaseModel):
    placeholder: str = Field(description="e.g. [[IMAGE_1]]")
    filename: str = Field(description="e.g. architecture_diagram.jpg")
    alt: str = Field(description="Alt text describing the image")
    caption: str = Field(description="Caption shown beneath the image")
    prompt: str = Field(description="Detailed image-generation prompt for AI model")
    size: str = Field(default="1024x1024")
    quality: str = Field(default="medium")


class GlobalImagePlan(BaseModel):
    images: List[ImageSpec] = Field(default_factory=list)
    md_with_placeholders: str = Field(
        description="The complete Markdown with [[IMAGE_X]] placeholders inserted."
    )


# ===========================================================================
# 2. Generation API Request & Response
# ===========================================================================

class GenerateBlogRequest(BaseModel):
    topic: str
    tone: Optional[str] = "professional"
    target_audience: Optional[str] = "Business Leaders and Professionals"
    keywords: Optional[List[str]] = Field(default_factory=list)
    blog_type: Optional[str] = "text_and_image"  # text_only | text_and_image
    include_images: Optional[bool] = True
    num_images: Optional[int] = 1
    target_words: Optional[int] = 1000
    target_length: Optional[str] = "~1000 words"
    language: Optional[str] = "English"
    cta_text: Optional[str] = "Book Free Strategy Session Today"
    cta_url: Optional[str] = "#strategy-session"
    
    # Scheduling & Workflow
    generation_mode: Optional[str] = "immediate"  # immediate | scheduled
    scheduled_generation_at: Optional[datetime] = None
    requires_approval: Optional[bool] = True
    target_channels: Optional[List[str]] = Field(default_factory=list)
    
    # Author & Notification
    author_name: Optional[str] = "LeadAI Content Studio"
    author_email: Optional[str] = None
    admin_reviewer_email: Optional[str] = None


class GenerateBlogResponse(BaseModel):
    title: str
    content: str  # HTML
    summary: Optional[str] = ""
    cover_image: Optional[str] = None
    images: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


# ===========================================================================
# 3. Article & Editorial Schemas
# ===========================================================================

class ArticleVersionResponse(BaseModel):
    id: str
    version_number: int
    title: str
    content: Optional[str] = ""
    summary: Optional[str] = ""
    cover_image: Optional[str] = ""
    images: Optional[List[str]] = []
    tags: Optional[List[str]] = []
    regeneration_prompt: Optional[str] = None
    created_at: Optional[datetime] = None


class ReviewNoteResponse(BaseModel):
    id: str
    version_number: Optional[int] = None
    author_name: str
    author_role: str
    content: str
    action_taken: str
    created_at: Optional[datetime] = None


class ArticleResponse(BaseModel):
    id: str
    client_id: str
    title: str
    slug: Optional[str] = None
    content: Optional[str] = ""
    summary: Optional[str] = ""
    cover_image: Optional[str] = ""
    images: Optional[List[str]] = []
    tags: Optional[List[str]] = []
    status: str
    requires_approval: bool
    generation_mode: str
    current_version: int
    author_name: Optional[str] = None
    author_email: Optional[str] = None
    author_role: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    target_channels: Optional[List[str]] = []
    results: Optional[Dict[str, Any]] = None
    linkedin_post_id: Optional[str] = None
    facebook_post_id: Optional[str] = None
    instagram_media_id: Optional[str] = None
    wordpress_post_url: Optional[str] = None
    review_token: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    versions: Optional[List[ArticleVersionResponse]] = []
    review_notes: Optional[List[ReviewNoteResponse]] = []


class ArticleCreate(BaseModel):
    title: str
    content: Optional[str] = ""
    summary: Optional[str] = ""
    slug: Optional[str] = None
    cover_image: Optional[str] = ""
    images: Optional[List[str]] = []
    tags: Optional[List[str]] = []
    requires_approval: bool = True
    target_channels: Optional[List[str]] = []
    author_name: Optional[str] = "LeadAI Editor"
    author_email: Optional[str] = None


class ArticleUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    summary: Optional[str] = None
    slug: Optional[str] = None
    cover_image: Optional[str] = None
    images: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    requires_approval: Optional[bool] = None
    target_channels: Optional[List[str]] = None


class ReviewActionRequest(BaseModel):
    action: Literal["approved", "changes_requested", "rejected", "regenerate"]
    notes: Optional[str] = ""
    reviewer_name: Optional[str] = "Admin"
    reviewer_role: Optional[str] = "manager"
    regeneration_prompt: Optional[str] = None
    publish_now: Optional[bool] = True
    target_channels: Optional[List[str]] = None


class PublishRequest(BaseModel):
    target_channels: Optional[List[str]] = None  # None = use article.TargetChannels or company defaults
    actor: Optional[str] = "Admin"


class ScheduleRequest(BaseModel):
    scheduled_at: datetime
    target_channels: Optional[List[str]] = None


class ArticleListResponse(BaseModel):
    total: int
    items: List[ArticleResponse]


class DashboardStatsResponse(BaseModel):
    draft: int = 0
    pending_approval: int = 0
    approved: int = 0
    changes_requested: int = 0
    scheduled: int = 0
    published: int = 0
    rejected: int = 0
    total: int = 0


# ===========================================================================
# 4. Blog Settings Schemas
# ===========================================================================

class BlogSettingsIn(BaseModel):
    is_auto_blog_enabled: Optional[bool] = None
    mode: Optional[Literal["automatic", "manual_confirmation"]] = None
    schedule_time: Optional[str] = None  # "09:00"
    topic_niche: Optional[str] = None
    keywords: Optional[List[str]] = None
    target_audience: Optional[str] = None
    tone: Optional[str] = None
    language: Optional[str] = None
    target_words: Optional[int] = None
    include_images: Optional[bool] = None
    num_images: Optional[int] = None
    cta_text: Optional[str] = None
    cta_url: Optional[str] = None
    target_channels: Optional[List[str]] = None
    admin_notification_email: Optional[str] = None
    wordpress_url: Optional[str] = None
    wordpress_username: Optional[str] = None
    wordpress_app_password: Optional[str] = None


class BlogSettingsOut(BaseModel):
    client_id: str
    is_auto_blog_enabled: bool
    mode: str
    schedule_time: str
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    topic_niche: Optional[str] = None
    keywords: List[str] = []
    target_audience: Optional[str] = None
    tone: Optional[str] = None
    language: Optional[str] = None
    target_words: int = 1000
    include_images: bool = True
    num_images: int = 1
    cta_text: Optional[str] = None
    cta_url: Optional[str] = None
    target_channels: List[str] = []
    admin_notification_email: Optional[str] = None
    wordpress_url: Optional[str] = None
    wordpress_username: Optional[str] = None
    has_wordpress_password: bool = False
