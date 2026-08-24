"""Social post drafts — generate, save, edit, publish.

Drafts are editable text captions that the frontend stores before publishing.
The generate endpoint uses the LLM to create draft text from user input.
Saving/editing persists drafts in the DB. Publishing converts a draft into a
LeadSocialPost via the existing social service.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from .. import activity
from ..activity import A
from ..db import get_leadai_db
from ..models import utcnow
from ..models_social import LeadSocialDraft, LeadSocialPost
from ..rbac import Principal, assert_owns, scoped
from ..schemas import Ok
from ..services import llm
from ..social import service as social_svc
from ..social.schemas import (
    DraftCreateRequest,
    DraftGenerateRequest,
    DraftListOut,
    DraftOut,
    DraftUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/social/drafts", tags=["LeadAI • Social Drafts"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _draft_out(row: LeadSocialDraft) -> DraftOut:
    return DraftOut(
        id=row.Id,
        caption=row.Caption or "",
        platforms=[p for p in (row.Platforms or "").split(",") if p],
        status=row.Status,
        post_id=row.PostId,
        created_at=row.CreatedAt,
        updated_at=row.UpdatedAt,
    )


def _get_draft(db: Session, draft_id: str, client_id: str) -> LeadSocialDraft:
    row = db.get(LeadSocialDraft, draft_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Draft not found")
    assert_owns(row.ClientId, client_id)
    return row


# ---------------------------------------------------------------------------
# AI draft generation (no persistence — returns text only)
# ---------------------------------------------------------------------------
_DRAFT_SYSTEM = """You are a professional social-media copywriter.
Create one polished, creative, publication-ready text post from the material below.

The material may contain a topic, raw content, specific instructions, or a mix of all
three, all together in a single block of text — read it carefully and work out what's
source material to draw from versus what's an instruction to follow.

Requirements:
- Preserve factual claims from the supplied material. Do not invent company facts.
- Follow any instructions embedded in the material when they do not conflict with the source content.
- Use a clear, engaging professional tone and a natural structure suitable for a social post.
- Include a concise call to action only when it fits the supplied material.
- Return only the draft text. Do not add a title, analysis, quotation marks, labels, or code fences.
- This request creates a draft only; never claim that it was posted or scheduled."""


@router.post("/generate", summary="Generate a text draft with AI (no save)")
async def generate_draft(
    body: DraftGenerateRequest,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
):
    """Generate one editable text draft from free-form content/instructions.
    Returns the draft text only — the frontend decides whether to save it."""
    alternate = ""
    if body.version > 1:
        alternate = (
            "This is an alternate version. Use a noticeably different hook, structure, "
            "and phrasing from a typical first draft while preserving the source material."
        )
    user_msg = f"Material:\n{body.content.strip()}\n\n{alternate}".strip()

    draft, meta = llm.complete(
        system=_DRAFT_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
        temperature=0.8,
        max_tokens=1000,
    )
    if not draft:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Unable to generate a draft.")
    return {"draft": draft, "version": body.version, "meta": meta}


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
@router.post(
    "",
    response_model=DraftOut,
    status_code=status.HTTP_201_CREATED,
    summary="Save a new draft",
)
def create_draft(
    body: DraftCreateRequest,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    row = LeadSocialDraft(
        ClientId=client_id,
        Caption=body.caption,
        Platforms=",".join(body.platforms),
        ChannelAccountId=body.account_id,
        CreatedBy=principal.email,
    )
    db.add(row)
    db.flush()
    activity.log_principal(
        db, principal, action=A.CHANNEL_UPDATED, client_id=client_id,
        entity_type="social_draft", entity_id=row.Id,
        message="Draft created", request=request,
    )
    db.commit()
    return _draft_out(row)


@router.get("", response_model=DraftListOut, summary="List drafts")
def list_drafts(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    status_filter: str | None = Query(None, alias="status"),
    scope: tuple[Principal, str] = Depends(scoped("social.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    query = db.query(LeadSocialDraft).filter(
        LeadSocialDraft.ClientId == client_id,
        LeadSocialDraft.IsDeleted == False,  # noqa: E712
    )
    if status_filter:
        query = query.filter(LeadSocialDraft.Status == status_filter)
    total = query.count()
    rows = (
        query.order_by(LeadSocialDraft.CreatedAt.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return DraftListOut(
        total_items=total, page=page, page_size=page_size,
        items=[_draft_out(r) for r in rows],
    )


@router.get("/{draft_id}", response_model=DraftOut, summary="Get one draft")
def get_draft(
    draft_id: str,
    scope: tuple[Principal, str] = Depends(scoped("social.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    return _draft_out(_get_draft(db, draft_id, client_id))


@router.patch("/{draft_id}", response_model=DraftOut, summary="Edit a draft")
def update_draft(
    draft_id: str,
    body: DraftUpdateRequest,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    row = _get_draft(db, draft_id, client_id)
    if body.caption is not None:
        row.Caption = body.caption
    if body.platforms is not None:
        row.Platforms = ",".join(body.platforms)
    if body.account_id is not None:
        row.ChannelAccountId = body.account_id
    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()
    activity.log_principal(
        db, principal, action=A.CHANNEL_UPDATED, client_id=client_id,
        entity_type="social_draft", entity_id=row.Id,
        message="Draft updated", request=request,
    )
    db.commit()
    return _draft_out(row)


@router.delete("/{draft_id}", response_model=Ok, summary="Delete a draft")
def delete_draft(
    draft_id: str,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    row = _get_draft(db, draft_id, client_id)
    row.IsDeleted = True
    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()
    activity.log_principal(
        db, principal, action=A.CHANNEL_UPDATED, client_id=client_id,
        entity_type="social_draft", entity_id=row.Id,
        message="Draft deleted", request=request,
    )
    db.commit()
    return Ok(message="Draft deleted.")


# ---------------------------------------------------------------------------
# Publish a draft
# ---------------------------------------------------------------------------
@router.post("/{draft_id}/publish", summary="Publish a saved draft")
async def publish_draft(
    draft_id: str,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("social.post")),
    db: Session = Depends(get_leadai_db),
):
    """Convert a draft into a published post via the existing social service."""
    principal, client_id = scope
    row = _get_draft(db, draft_id, client_id)
    if row.Status == "published":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Draft is already published.")

    platforms = [p for p in (row.Platforms or "").split(",") if p]
    if not platforms:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No platforms selected.")

    results, post_row = await social_svc.publish(
        db, client_id,
        caption=row.Caption or "",
        uploaded=[],
        platforms=platforms,
        actor=principal.email,
        account_id=row.ChannelAccountId,
        mode="direct",
    )

    succeeded = [p for p, r in results.items() if r.get("success")]
    row.Status = "published"
    row.PostId = post_row.Id if post_row else None
    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()

    activity.log_principal(
        db, principal, action=A.CHANNEL_UPDATED, client_id=client_id,
        entity_type="social_draft", entity_id=row.Id,
        message=f"Draft published to {', '.join(platforms)}",
        meta={"post_id": post_row.Id if post_row else None},
        request=request,
    )
    db.commit()
    return {
        "draft_id": row.Id,
        "post_id": post_row.Id if post_row else None,
        "status": post_row.Status if post_row else "failed",
        "results": results,
    }
