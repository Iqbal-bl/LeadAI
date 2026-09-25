"""
Articles & Editorial Approval Workflow Router for LeadAI.
Provides both standard scoped (/articles) and company-explicit (/companies/{id}/articles) endpoints,
plus token-authenticated email review endpoints.
"""
from __future__ import annotations

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session
from domain.models import Client

from ..db import get_leadai_db
from ..models_blog import LeadArticle
from ..rbac import Principal, assert_owns, require, resolve_scope
from ..security import decrypt_pii
from ..services.blog.article_service import ArticleService
from ..services.blog.publisher_service import PublisherService
from ..services.blog.schemas import (
    ArticleCreate,
    ArticleListResponse,
    ArticleResponse,
    ArticleUpdate,
    DashboardStatsResponse,
    PublishRequest,
    ReviewActionRequest,
)
from ..services.blog.token_service import TokenService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["LeadAI • Articles & Publishing"])


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
# 1. Scoped Article Endpoints (with resolve_scope)
# ===========================================================================

@router.get(
    "/articles/stats",
    response_model=DashboardStatsResponse,
    summary="Get article counts by status for company dashboard",
)
@router.get(
    "/companies/{company_id}/articles/stats",
    response_model=DashboardStatsResponse,
    include_in_schema=False,
)
def get_article_stats(
    company_id: Optional[str] = None,
    client_id: Optional[str] = Query(None),
    principal: Principal = Depends(require("campaign.read")),
    db: Session = Depends(get_leadai_db),
):
    target_client = _resolve_company(principal, company_id or client_id)
    return ArticleService.get_stats(db, target_client)


@router.get(
    "/articles",
    response_model=ArticleListResponse,
    summary="List articles for a company with filters and pagination",
)
@router.get(
    "/companies/{company_id}/articles",
    response_model=ArticleListResponse,
    include_in_schema=False,
)
def list_articles(
    company_id: Optional[str] = None,
    client_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="Filter: draft, pending_approval, approved, changes_requested, scheduled, published, all"),
    search: Optional[str] = Query(None, description="Search in title or summary"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    principal: Principal = Depends(require("campaign.read")),
    db: Session = Depends(get_leadai_db),
):
    target_client = _resolve_company(principal, company_id or client_id)
    return ArticleService.list_articles(db, target_client, status=status, search=search, skip=skip, limit=limit)


@router.get(
    "/articles/{article_id}",
    response_model=ArticleResponse,
    summary="Get single article with version history and review notes",
)
@router.get(
    "/companies/{company_id}/articles/{article_id}",
    response_model=ArticleResponse,
    include_in_schema=False,
)
def get_article(
    article_id: str,
    company_id: Optional[str] = None,
    client_id: Optional[str] = Query(None),
    principal: Principal = Depends(require("campaign.read")),
    db: Session = Depends(get_leadai_db),
):
    target_client = _resolve_company(principal, company_id or client_id)
    article = ArticleService.get_article(db, target_client, article_id)
    if not article:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Article {article_id} not found.")
    return ArticleService._to_response(db, article)


@router.post(
    "/articles",
    response_model=ArticleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new article draft",
)
@router.post(
    "/companies/{company_id}/articles",
    response_model=ArticleResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
def create_article_draft(
    payload: ArticleCreate,
    company_id: Optional[str] = None,
    client_id: Optional[str] = Query(None),
    principal: Principal = Depends(require("campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    target_client = _resolve_company(principal, company_id or client_id)
    return ArticleService.create_or_save_draft(db, target_client, payload)


@router.put(
    "/articles/{article_id}",
    response_model=ArticleResponse,
    summary="Update article content or metadata",
)
@router.put(
    "/companies/{company_id}/articles/{article_id}",
    response_model=ArticleResponse,
    include_in_schema=False,
)
def update_article(
    article_id: str,
    payload: ArticleUpdate,
    company_id: Optional[str] = None,
    client_id: Optional[str] = Query(None),
    principal: Principal = Depends(require("campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    target_client = _resolve_company(principal, company_id or client_id)
    article = ArticleService.get_article(db, target_client, article_id)
    if not article:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Article {article_id} not found.")

    if payload.title is not None:
        article.Title = payload.title
    if payload.content is not None:
        article.Content = payload.content
    if payload.summary is not None:
        article.Summary = payload.summary
    if payload.slug is not None:
        article.Slug = payload.slug
    if payload.cover_image is not None:
        article.CoverImage = payload.cover_image
    if payload.images is not None:
        article.Images = payload.images
    if payload.tags is not None:
        article.Tags = payload.tags
    if payload.requires_approval is not None:
        article.RequiresApproval = payload.requires_approval
    if payload.target_channels is not None:
        article.TargetChannels = payload.target_channels

    db.commit()
    db.refresh(article)
    return ArticleService._to_response(db, article)


@router.delete(
    "/articles/{article_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete article and its version history",
)
@router.delete(
    "/companies/{company_id}/articles/{article_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    include_in_schema=False,
)
def delete_article(
    article_id: str,
    company_id: Optional[str] = None,
    client_id: Optional[str] = Query(None),
    principal: Principal = Depends(require("campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    target_client = _resolve_company(principal, company_id or client_id)
    article = ArticleService.get_article(db, target_client, article_id)
    if not article:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Article {article_id} not found.")
    article.IsDeleted = True
    db.commit()


@router.post(
    "/articles/{article_id}/review",
    response_model=ArticleResponse,
    summary="Review action: approve, request changes, reject, or regenerate",
)
@router.post(
    "/companies/{company_id}/articles/{article_id}/review",
    response_model=ArticleResponse,
    include_in_schema=False,
)
def review_article(
    article_id: str,
    payload: ReviewActionRequest,
    company_id: Optional[str] = None,
    client_id: Optional[str] = Query(None),
    principal: Principal = Depends(require("campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    target_client = _resolve_company(principal, company_id or client_id)
    client = db.query(Client).filter(Client.Id == target_client, Client.IsDeleted == False).first()
    company_name = client.Name if client else "Your Organization"
    return ArticleService.review_article(
        db=db,
        client_id=target_client,
        article_id=article_id,
        req=payload,
        company_name=company_name,
    )


@router.post(
    "/articles/{article_id}/publish",
    response_model=ArticleResponse,
    summary="Publish article immediately to live channels (WordPress, LinkedIn, Facebook, Instagram)",
)
@router.post(
    "/companies/{company_id}/articles/{article_id}/publish",
    response_model=ArticleResponse,
    include_in_schema=False,
)
def publish_article(
    article_id: str,
    payload: PublishRequest = PublishRequest(),
    company_id: Optional[str] = None,
    client_id: Optional[str] = Query(None),
    principal: Principal = Depends(require("campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    target_client = _resolve_company(principal, company_id or client_id)
    article = ArticleService.get_article(db, target_client, article_id)
    if not article:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Article {article_id} not found.")

    PublisherService.publish_to_all_channels(
        db=db,
        article=article,
        target_channels=payload.target_channels or article.TargetChannels,
        actor=payload.actor or principal.email,
    )
    return ArticleService._to_response(db, article)


# ===========================================================================
# 2. Token-Authenticated Public Review Endpoints (For Email Links)
# ===========================================================================

@router.get(
    "/articles/review/preview",
    response_model=ArticleResponse,
    summary="Load article preview securely using signed review token from email",
)
def get_article_preview_by_token(
    token: str = Query(..., description="Signed JWT review token"),
    db: Session = Depends(get_leadai_db),
):
    article_resp = ArticleService.get_article_by_token(db, token)
    if not article_resp:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired review link token.")
    return article_resp


@router.post(
    "/articles/review/action",
    response_model=ArticleResponse,
    summary="Perform review action using signed review token from email",
)
def review_article_by_token(
    token: str = Query(..., description="Signed JWT review token"),
    payload: ReviewActionRequest = ...,
    db: Session = Depends(get_leadai_db),
):
    token_data = TokenService.verify_review_token(token)
    if not token_data:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired review link token.")

    article_id = token_data.get("article_id")
    client_id = token_data.get("client_id")
    reviewer_email = token_data.get("reviewer_email") or "Organization Admin"

    payload.reviewer_name = payload.reviewer_name or reviewer_email
    client = db.query(Client).filter(Client.Id == client_id, Client.IsDeleted == False).first()
    company_name = client.Name if client else "Your Organization"

    return ArticleService.review_article(
        db=db,
        client_id=client_id,
        article_id=article_id,
        req=payload,
        company_name=company_name,
    )
