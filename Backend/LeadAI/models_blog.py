"""
LeadAI — Blog & Content Automation tables.

Additive and multi-tenant: every table carries `ClientId` and inherits from `LeadAIBase`
with the audit quintet (CreatedAt, UpdatedAt, CreatedBy, UpdatedBy, IsDeleted).
Registered into `ALL_LEADAI_TABLES` in `LeadAI/models.py`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
try:
    from core.base import Base
except ImportError:
    from base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LeadAIBase(Base):
    """Same audit shape as Domain.models.BaseDocument."""

    __abstract__ = True

    Id = Column(String(36), primary_key=True, default=_uuid, unique=True, nullable=False)
    CreatedBy = Column(String(100), nullable=False, default="system")
    CreatedAt = Column(DateTime, default=utcnow, index=True)
    UpdatedBy = Column(String(100), nullable=True)
    UpdatedAt = Column(DateTime, nullable=True)
    IsDeleted = Column(Boolean, default=False, index=True)



# ===========================================================================
# 1. Company-level Automated Blog Settings
# ===========================================================================

class LeadBlogSettings(LeadAIBase):
    """Configuration for a company's automated daily blog creation & publishing."""

    __tablename__ = "leadai_blog_settings"
    __table_args__ = (
        UniqueConstraint("ClientId", name="uq_leadai_blog_settings_client"),
        Index("ix_leadai_blog_settings_client", "ClientId"),
    )

    ClientId = Column(String(36), nullable=False)
    IsAutoBlogEnabled = Column(Boolean, default=False, nullable=False)
    Mode = Column(String(30), default="manual_confirmation", nullable=False)  # "automatic" | "manual_confirmation"
    
    # Schedule timing (UTC daily run time, e.g., "09:00")
    ScheduleTime = Column(String(10), default="09:00", nullable=False)
    LastRunAt = Column(DateTime, nullable=True)
    NextRunAt = Column(DateTime, nullable=True)

    # Content generation preferences
    TopicNiche = Column(String(500), nullable=True)          # e.g., "AI Automation, B2B Lead Generation, Tech Sales"
    Keywords = Column(JSON, nullable=True, default=list)       # list of target keywords / hashtags
    TargetAudience = Column(String(250), nullable=True, default="Business leaders, practitioners, and modern professionals")
    Tone = Column(String(50), nullable=True, default="professional")  # professional, thought_leadership, conversational, persuasive
    Language = Column(String(30), nullable=True, default="English")
    TargetWords = Column(Integer, default=1000, nullable=False)
    IncludeImages = Column(Boolean, default=True, nullable=False)
    NumImages = Column(Integer, default=1, nullable=False)
    CtaText = Column(String(200), nullable=True, default="Book Free Strategy Session Today")
    CtaUrl = Column(String(500), nullable=True, default="#strategy-session")

    # Target publish channels: e.g. ["linkedin", "facebook", "instagram", "wordpress"]
    TargetChannels = Column(JSON, nullable=True, default=list)

    # Admin notification & approval recipient
    AdminNotificationEmail = Column(String(200), nullable=True)

    # Optional WordPress configuration per company
    WordPressUrl = Column(String(500), nullable=True)
    WordPressUsername = Column(String(120), nullable=True)
    WordPressAppPasswordEnc = Column(Text, nullable=True)  # Fernet encrypted


# ===========================================================================
# 2. Multi-tenant Article & Editorial Workflow
# ===========================================================================

class LeadArticle(LeadAIBase):
    """Article draft, approved post, or published blog entry for a company."""

    __tablename__ = "leadai_articles"
    __table_args__ = (
        Index("ix_leadai_article_client_status", "ClientId", "Status"),
        Index("ix_leadai_article_client_created", "ClientId", "CreatedAt"),
    )

    ClientId = Column(String(36), nullable=False, index=True)
    Title = Column(String(500), nullable=False, default="Untitled Draft")
    Slug = Column(String(500), nullable=True, index=True)
    Content = Column(Text, nullable=True, default="")       # Styled HTML Content
    Summary = Column(Text, nullable=True, default="")
    
    # Media & Tags
    CoverImage = Column(String(1000), nullable=True, default="")
    Images = Column(JSON, nullable=True, default=list)        # list of image URLs
    Tags = Column(JSON, nullable=True, default=list)          # list of tags / keywords

    # Status: draft, pending_approval, approved, changes_requested, scheduled, published, rejected, generating, generation_failed
    Status = Column(String(50), nullable=False, default="draft", index=True)
    RequiresApproval = Column(Boolean, nullable=False, default=True)
    GenerationMode = Column(String(50), nullable=False, default="immediate")  # immediate | scheduled | daily_scheduler
    ScheduledGenerationAt = Column(DateTime, nullable=True)
    GenerationPrompt = Column(JSON, nullable=True, default=dict)
    CurrentVersion = Column(Integer, nullable=False, default=1)

    # Author
    AuthorName = Column(String(200), nullable=True, default="LeadAI Content Studio")
    AuthorEmail = Column(String(200), nullable=True)
    AuthorRole = Column(String(50), nullable=True, default="system")

    # Publishing & Approvals
    ScheduledAt = Column(DateTime, nullable=True)             # Target publish time
    PublishedAt = Column(DateTime, nullable=True)
    SubmittedAt = Column(DateTime, nullable=True)
    ReviewedAt = Column(DateTime, nullable=True)

    # Target Channels & Outcomes
    TargetChannels = Column(JSON, nullable=True, default=list)  # ["linkedin", "facebook", "instagram", "wordpress"]
    Results = Column(JSON, nullable=True)                     # {"linkedin": {"success": true, "post_id": "..."}, ...}
    LinkedInPostId = Column(String(120), nullable=True)
    FacebookPostId = Column(String(120), nullable=True)
    InstagramMediaId = Column(String(120), nullable=True)
    WordPressPostUrl = Column(String(500), nullable=True)
    WordPressPostId = Column(String(120), nullable=True)

    ReviewToken = Column(String(1000), nullable=True)
    ErrorMessage = Column(Text, nullable=True)


class LeadArticleVersion(LeadAIBase):
    """Snapshot of an article version for history, comparison, and rollback."""

    __tablename__ = "leadai_article_versions"
    __table_args__ = (
        Index("ix_leadai_artver_article_ver", "ArticleId", "VersionNumber"),
        Index("ix_leadai_artver_client", "ClientId"),
    )

    ClientId = Column(String(36), nullable=False)
    ArticleId = Column(String(36), nullable=False, index=True)
    VersionNumber = Column(Integer, nullable=False, default=1)

    Title = Column(String(500), nullable=False)
    Content = Column(Text, nullable=True, default="")
    Summary = Column(Text, nullable=True, default="")
    CoverImage = Column(String(1000), nullable=True, default="")
    Images = Column(JSON, nullable=True, default=list)
    Tags = Column(JSON, nullable=True, default=list)

    RegenerationPrompt = Column(Text, nullable=True)


class LeadBlogReviewNote(LeadAIBase):
    """Editorial note, approval action, or change request log for an article."""

    __tablename__ = "leadai_blog_review_notes"
    __table_args__ = (
        Index("ix_leadai_revnote_article", "ArticleId"),
        Index("ix_leadai_revnote_client", "ClientId"),
    )

    ClientId = Column(String(36), nullable=False)
    ArticleId = Column(String(36), nullable=False, index=True)
    VersionNumber = Column(Integer, nullable=True)

    AuthorName = Column(String(200), nullable=False, default="Reviewer")
    AuthorRole = Column(String(50), nullable=False, default="manager")
    Content = Column(Text, nullable=False)
    ActionTaken = Column(String(50), nullable=False, default="draft")  # submit_approval, approved, changes_requested, rejected, regenerated, published


ALL_LEADAI_BLOG_TABLES = (
    LeadBlogSettings,
    LeadArticle,
    LeadArticleVersion,
    LeadBlogReviewNote,
)
