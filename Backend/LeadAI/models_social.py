"""
Tables for social publishing.

These replace the standalone agent's SQLite tables (`api_request_log`,
`content_topic` in the old `api/db.py`). Those were single-tenant by
construction: neither carried a company id, so two companies' posts would sit
in one undifferentiated list and any "show me my posts" query would return
everyone's. Both tables gain `ClientId` here and ride on the same MySQL engine
and audit shape as every other LeadAI table, so tenant filtering, soft deletes
and the activity log all work the same way they do elsewhere.

Registered into ALL_LEADAI_TABLES via LeadAI/models.py, so `ensure_tables()`
creates them on startup like the rest.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, JSON, String, Text, Float, UniqueConstraint

from .models import LeadAIBase

# Lifecycle of a queued/published post.
POST_STATUS_PENDING = "pending"
POST_STATUS_SCHEDULED = "scheduled"
POST_STATUS_PUBLISHING = "publishing"
POST_STATUS_PUBLISHED = "published"
POST_STATUS_FAILED = "failed"
POST_STATUS_PARTIAL = "partial"  # multi-platform: some succeeded, some did not


class LeadSocialPost(LeadAIBase):
    """One publish attempt by one company.

    Stored per REQUEST, not per platform: a single "post to Facebook and
    Instagram" action is one row whose `Results` records the outcome for each
    platform separately. That keeps the operator's mental model ("I made one
    post") aligned with the UI, while still recording that Instagram succeeded
    and Facebook was skipped.
    """

    __tablename__ = "leadai_social_posts"
    __table_args__ = (
        Index("ix_leadai_social_post_client_created", "ClientId", "CreatedAt"),
        Index("ix_leadai_social_post_status", "ClientId", "Status"),
    )

    ClientId = Column(String(36), nullable=False)
    ChannelAccountId = Column(String(36), nullable=True)  # which connected account

    Platforms = Column(String(120), nullable=False, default="")  # "facebook,instagram"
    Mode = Column(String(20), nullable=False, default="direct")  # direct | ai
    MediaShape = Column(String(30), nullable=True)  # single_image | mixed_carousel | ...

    Topic = Column(String(500), nullable=True)       # AI mode: what to write about
    Instructions = Column(Text, nullable=True)        # AI mode: tone/style guidance
    Caption = Column(Text, nullable=True)             # direct mode: the exact text,
    # or, in AI mode, the caption the model actually produced — so the record
    # always shows what was really published.
    MediaUrls = Column(JSON, nullable=True)           # hosted URLs, post-upload

    Status = Column(String(20), nullable=False, default=POST_STATUS_PENDING, index=True)
    ScheduledFor = Column(DateTime, nullable=True)
    PublishedAt = Column(DateTime, nullable=True)

    # Per-platform outcome: {"facebook": {"success": true, "post_id": "..."}, ...}
    Results = Column(JSON, nullable=True)
    # Convenience copies of the first successful ids, so the common "open the
    # post" link does not require parsing Results in the frontend.
    FacebookPostId = Column(String(120), nullable=True)
    InstagramMediaId = Column(String(120), nullable=True)
    LinkedInPostId = Column(String(120), nullable=True)

    DurationMs = Column(Integer, nullable=True)
    Error = Column(Text, nullable=True)
    CampaignId = Column(String(36), nullable=True)  # attribution, when driven by a campaign


class LeadSocialTopic(LeadAIBase):
    """The content queue — topics awaiting AI caption generation and publishing.

    The multi-tenant successor to `content_queue/topics.csv` and the old
    `content_topic` table. A worker (or an operator clicking "publish next")
    drains this per company.
    """

    __tablename__ = "leadai_social_topics"
    __table_args__ = (
        Index("ix_leadai_social_topic_client_status", "ClientId", "Status"),
    )

    ClientId = Column(String(36), nullable=False)
    Topic = Column(String(500), nullable=False)
    Instructions = Column(Text, nullable=True)
    ImageUrl = Column(String(1000), nullable=True)
    VideoUrl = Column(String(1000), nullable=True)
    Platforms = Column(String(120), nullable=False, default="facebook")
    ChannelAccountId = Column(String(36), nullable=True)

    Status = Column(String(20), nullable=False, default=POST_STATUS_PENDING, index=True)
    ScheduledFor = Column(DateTime, nullable=True)
    PostId = Column(String(36), nullable=True)   # -> LeadSocialPost.Id once published
    Attempts = Column(Integer, nullable=False, default=0)
    LastError = Column(Text, nullable=True)
    IsActive = Column(Boolean, default=True)


class LeadSocialDraft(LeadAIBase):
    """Saved social post drafts — editable text before publishing.

    The frontend creates, edits, and eventually publishes a draft. Publishing
    converts it into a LeadSocialPost and marks the draft as published.
    """

    __tablename__ = "leadai_social_drafts"
    __table_args__ = (
        Index("ix_leadai_social_draft_client_status", "ClientId", "Status"),
    )

    ClientId = Column(String(36), nullable=False)
    Caption = Column(Text, nullable=False, default="")
    Platforms = Column(String(120), nullable=False, default="facebook,instagram")
    ChannelAccountId = Column(String(36), nullable=True)
    Status = Column(String(20), nullable=False, default="draft", index=True)  # draft | published | archived
    PostId = Column(String(36), nullable=True)   # -> LeadSocialPost.Id once published


ALL_LEADAI_SOCIAL_TABLES = (
    LeadSocialPost,
    LeadSocialTopic,
    LeadSocialDraft,
)
