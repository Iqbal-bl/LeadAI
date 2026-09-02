"""
Schemas for the social publishing API.

Two families, because there are two genuinely different ways to publish and
conflating them produced the standalone agent's most confusing behaviour
("why did my caption change?"):

  * DIRECT  — the caption is published verbatim. No model involved.
  * AI      — a topic plus optional style instructions; the agent writes the
              caption (and can generate the image), then publishes.

Every request is scoped to the caller's company by the RBAC dependency, so no
schema here carries a `client_id`: accepting one from the body would let a
caller name someone else's company, which is precisely the hole the tenant
resolver exists to close.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Platform = Literal["facebook", "instagram", "linkedin"]


class MediaItem(BaseModel):
    """Base64 media, uploaded to the object store then handed to Meta as a URL."""

    type: Literal["image", "video"]
    data: str = Field(description="Raw base64. A 'data:...;base64,' prefix is accepted too.")
    mime_type: str = Field(description="e.g. image/jpeg, video/mp4")


class MediaUrlItem(BaseModel):
    url: str = Field(description="Publicly reachable HTTPS URL. Meta fetches it itself.")
    is_video: bool = False


class _PlatformsMixin(BaseModel):
    platforms: list[Platform] = Field(
        default=["facebook", "instagram"],
        description="Which connected platforms to publish to. Each resolves to this "
        "company's own connected account; one platform failing does not stop the others.",
    )
    account_id: str | None = Field(
        default=None,
        description="Pin a specific connected channel account (for companies with more "
        "than one Page). Omit to use the company's first active account.",
    )
    schedule_time: datetime | None = Field(
        default=None,
        description="Optional UTC ISO 8601 timestamp for scheduled publishing. Must be in the future. If null, publishes immediately.",
    )


    @field_validator("platforms")
    @classmethod
    def _non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("Pick at least one platform.")
        return v


class DirectPostRequest(_PlatformsMixin):
    """Publish an exact caption with base64 media."""

    caption: str = ""
    media: list[MediaItem] = Field(default_factory=list)


class DirectUrlPostRequest(_PlatformsMixin):
    """Publish an exact caption with media that is already hosted publicly."""

    caption: str = ""
    media: list[MediaUrlItem] = Field(default_factory=list)


class AiPostRequest(_PlatformsMixin):
    """Let the agent write the caption from a topic."""

    topic: str = Field(min_length=1, max_length=500)
    instructions: str = Field(default="", max_length=2000)
    image_url: str = Field(
        default="", description="Leave empty to have an image generated for the topic."
    )
    video_url: str = ""


class SinglePlatformAiRequest(BaseModel):
    """AI post targeted at exactly one platform (the /facebook and /instagram routes)."""

    topic: str = Field(min_length=1, max_length=500)
    instructions: str = Field(default="", max_length=2000)
    image_url: str = ""
    video_url: str = ""
    account_id: str | None = None


class ReplyAllRequest(BaseModel):
    account_id: str | None = None
    limit: int = Field(default=25, ge=1, le=200, description="Max items to handle in one run.")


class TopicCreate(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    instructions: str = ""
    image_url: str = ""
    video_url: str = ""
    platforms: list[Platform] = ["facebook"]
    account_id: str | None = None
    scheduled_for: datetime | None = None


class TopicOut(BaseModel):
    id: str
    topic: str
    instructions: str | None = None
    image_url: str | None = None
    video_url: str | None = None
    platforms: list[str]
    status: str
    scheduled_for: datetime | None = None
    attempts: int = 0
    last_error: str | None = None
    post_id: str | None = None
    created_at: datetime | None = None


class PostOut(BaseModel):
    id: str
    platforms: list[str]
    mode: str
    status: str
    caption: str | None = None
    topic: str | None = None
    media_shape: str | None = None
    media_urls: list[str] = []
    results: dict[str, Any] | None = None
    facebook_post_id: str | None = None
    instagram_media_id: str | None = None
    duration_ms: int | None = None
    error: str | None = None
    published_at: datetime | None = None
    created_at: datetime | None = None


class PostListOut(BaseModel):
    total_items: int
    page: int
    page_size: int
    items: list[PostOut]


class PlatformStatusOut(BaseModel):
    platforms: dict[str, Any] = Field(
        description="Per platform: whether this company has a usable connected account, "
        "and which one will be used."
    )


# ---------------------------------------------------------------------------
# Social drafts
# ---------------------------------------------------------------------------
class DraftGenerateRequest(BaseModel):
    """Generate a text draft from free-form content/instructions."""

    content: str = Field(..., min_length=1, max_length=12_000)
    version: int = Field(default=1, ge=1, le=100, description="Request a different phrasing (2, 3, ...).")


class DraftCreateRequest(BaseModel):
    """Save a new draft."""

    caption: str = Field(default="", max_length=10_000)
    platforms: list[Platform] = Field(default=["facebook", "instagram"])
    account_id: str | None = None


class DraftUpdateRequest(BaseModel):
    """Edit an existing draft."""

    caption: str | None = Field(default=None, max_length=10_000)
    platforms: list[Platform] | None = None
    account_id: str | None = None


class DraftOut(BaseModel):
    id: str
    caption: str
    platforms: list[str]
    status: str
    post_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DraftListOut(BaseModel):
    total_items: int
    page: int
    page_size: int
    items: list[DraftOut]
