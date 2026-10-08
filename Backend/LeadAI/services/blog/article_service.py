"""
Article Service handling CRUD, version snapshots, approval flow, regeneration, and dashboard statistics.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from ...models_blog import (
    LeadArticle,
    LeadArticleVersion,
    LeadBlogReviewNote,
    LeadBlogSettings,
)
from .email_service import EmailService
from .generator_service import GeneratorService
from .publisher_service import PublisherService
from .schemas import (
    ArticleCreate,
    ArticleListResponse,
    ArticleResponse,
    ArticleUpdate,
    ArticleVersionResponse,
    DashboardStatsResponse,
    GenerateBlogRequest,
    ReviewActionRequest,
    ReviewNoteResponse,
    ScheduleRequest,
)
from .token_service import TokenService

logger = logging.getLogger(__name__)


class ArticleService:

    @classmethod
    def _to_response(cls, db: Session, article: LeadArticle) -> ArticleResponse:
        """Converts LeadArticle model and its relationships to ArticleResponse."""
        versions = (
            db.query(LeadArticleVersion)
            .filter(
                LeadArticleVersion.ArticleId == article.Id,
                LeadArticleVersion.IsDeleted == False,
            )
            .order_by(LeadArticleVersion.VersionNumber.asc())
            .all()
        )
        review_notes = (
            db.query(LeadBlogReviewNote)
            .filter(
                LeadBlogReviewNote.ArticleId == article.Id,
                LeadBlogReviewNote.IsDeleted == False,
            )
            .order_by(LeadBlogReviewNote.CreatedAt.asc())
            .all()
        )

        return ArticleResponse(
            id=article.Id,
            client_id=article.ClientId,
            title=article.Title,
            slug=article.Slug,
            content=article.Content or "",
            summary=article.Summary or "",
            cover_image=article.CoverImage or "",
            images=article.Images or [],
            tags=article.Tags or [],
            status=article.Status,
            requires_approval=article.RequiresApproval,
            generation_mode=article.GenerationMode,
            current_version=article.CurrentVersion or 1,
            author_name=article.AuthorName,
            author_email=article.AuthorEmail,
            author_role=article.AuthorRole,
            scheduled_at=article.ScheduledAt,
            published_at=article.PublishedAt,
            submitted_at=article.SubmittedAt,
            reviewed_at=article.ReviewedAt,
            target_channels=article.TargetChannels or [],
            channel_account_id=getattr(article, "ChannelAccountId", None),
            channel_account_name=getattr(article, "ChannelAccountName", None),
            results=article.Results,
            linkedin_post_id=article.LinkedInPostId,
            facebook_post_id=article.FacebookPostId,
            instagram_media_id=article.InstagramMediaId,
            wordpress_post_url=article.WordPressPostUrl,
            review_token=article.ReviewToken,
            created_at=article.CreatedAt,
            updated_at=article.UpdatedAt,
            versions=[
                ArticleVersionResponse(
                    id=v.Id,
                    version_number=v.VersionNumber,
                    title=v.Title,
                    content=v.Content or "",
                    summary=v.Summary or "",
                    cover_image=v.CoverImage or "",
                    images=v.Images or [],
                    tags=v.Tags or [],
                    regeneration_prompt=v.RegenerationPrompt,
                    created_at=v.CreatedAt,
                )
                for v in versions
            ],
            review_notes=[
                ReviewNoteResponse(
                    id=rn.Id,
                    version_number=rn.VersionNumber,
                    author_name=rn.AuthorName,
                    author_role=rn.AuthorRole,
                    content=rn.Content,
                    action_taken=rn.ActionTaken,
                    created_at=rn.CreatedAt,
                )
                for rn in review_notes
            ],
        )

    @classmethod
    def get_stats(cls, db: Session, client_id: str) -> DashboardStatsResponse:
        """Computes counts for all article statuses for a company."""
        base_query = db.query(LeadArticle.Status, func.count(LeadArticle.Id)).filter(
            LeadArticle.ClientId == client_id,
            LeadArticle.IsDeleted == False,
        ).group_by(LeadArticle.Status)

        counts = dict(base_query.all())
        total = sum(counts.values())

        return DashboardStatsResponse(
            draft=counts.get("draft", 0),
            pending_approval=counts.get("pending_approval", 0),
            approved=counts.get("approved", 0),
            changes_requested=counts.get("changes_requested", 0),
            scheduled=counts.get("scheduled", 0),
            published=counts.get("published", 0),
            rejected=counts.get("rejected", 0),
            total=total,
        )

    @classmethod
    def list_articles(
        cls,
        db: Session,
        client_id: str,
        status: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> ArticleListResponse:
        """Lists articles with filters and pagination."""
        query = db.query(LeadArticle).filter(
            LeadArticle.ClientId == client_id,
            LeadArticle.IsDeleted == False,
        )

        if status and status.lower() != "all":
            query = query.filter(LeadArticle.Status == status.lower())

        if search:
            s = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    LeadArticle.Title.ilike(s),
                    LeadArticle.Summary.ilike(s),
                    LeadArticle.AuthorName.ilike(s),
                )
            )

        total = query.count()
        articles = query.order_by(LeadArticle.CreatedAt.desc()).offset(skip).limit(limit).all()

        return ArticleListResponse(
            total=total,
            items=[cls._to_response(db, a) for a in articles],
        )

    @classmethod
    def get_article(cls, db: Session, client_id: str, article_id: str) -> LeadArticle:
        """Retrieves single article verifying company scope."""
        article = db.query(LeadArticle).filter(
            LeadArticle.Id == article_id,
            LeadArticle.ClientId == client_id,
            LeadArticle.IsDeleted == False,
        ).first()
        return article

    @classmethod
    def get_article_by_token(cls, db: Session, token: str) -> Optional[ArticleResponse]:
        """Loads article using a signed review JWT token."""
        payload = TokenService.verify_review_token(token)
        if not payload:
            return None

        article_id = payload.get("article_id")
        client_id = payload.get("client_id")
        article = db.query(LeadArticle).filter(
            LeadArticle.Id == article_id,
            LeadArticle.ClientId == client_id,
            LeadArticle.IsDeleted == False,
        ).first()

        if not article:
            return None
        return cls._to_response(db, article)

    @classmethod
    def create_or_save_draft(cls, db: Session, client_id: str, data: ArticleCreate) -> ArticleResponse:
        """Creates an article draft and snapshot version 1."""
        article = LeadArticle(
            ClientId=client_id,
            Title=data.title,
            Slug=data.slug,
            Content=data.content or "",
            Summary=data.summary or "",
            CoverImage=data.cover_image or "",
            Images=data.images or [],
            Tags=data.tags or [],
            RequiresApproval=data.requires_approval,
            TargetChannels=data.target_channels or [],
            Status="draft",
            AuthorName=data.author_name or "LeadAI Editor",
            AuthorEmail=data.author_email,
            CurrentVersion=1,
        )
        db.add(article)
        db.flush()

        v1 = LeadArticleVersion(
            ClientId=client_id,
            ArticleId=article.Id,
            VersionNumber=1,
            Title=article.Title,
            Content=article.Content,
            Summary=article.Summary,
            CoverImage=article.CoverImage,
            Images=article.Images,
            Tags=article.Tags,
            RegenerationPrompt="Initial creation",
        )
        db.add(v1)
        db.commit()
        db.refresh(article)

        return cls._to_response(db, article)

    @classmethod
    def generate_and_save(
        cls,
        db: Session,
        client_id: str,
        req: GenerateBlogRequest,
        company_name: str = "Your Organization"
    ) -> ArticleResponse:
        """
        Executes AI Blog Generation, persists Article & Version 1, and routes
        to Auto-Publish or Dispatches Approval Email based on company settings.
        """
        # Fetch company blog settings
        blog_settings: Optional[LeadBlogSettings] = db.query(LeadBlogSettings).filter(
            LeadBlogSettings.ClientId == client_id,
            LeadBlogSettings.IsDeleted == False
        ).first()

        # Run Generator
        gen_res = GeneratorService.generate_blog(req)

        # Create Article
        requires_approval = req.requires_approval if req.requires_approval is not None else (
            blog_settings.Mode == "manual_confirmation" if blog_settings else True
        )
        channels = req.target_channels or (blog_settings.TargetChannels if blog_settings else []) or ["wordpress"]

        article = LeadArticle(
            ClientId=client_id,
            Title=gen_res.title,
            Content=gen_res.content,
            Summary=gen_res.summary or "",
            CoverImage=gen_res.cover_image or "",
            Images=gen_res.images or [],
            Tags=gen_res.tags or [],
            RequiresApproval=requires_approval,
            GenerationMode=req.generation_mode or "immediate",
            TargetChannels=channels,
            ChannelAccountId=req.channel_account_id,
            ChannelAccountName=req.channel_account_name,
            Status="draft",
            CurrentVersion=1,
            AuthorName=req.author_name or "LeadAI Content Studio",
            AuthorEmail=req.author_email,
            GenerationPrompt=req.model_dump(exclude={"scheduled_generation_at"}),
        )
        db.add(article)
        db.flush()

        # Save Version 1
        v1 = LeadArticleVersion(
            ClientId=client_id,
            ArticleId=article.Id,
            VersionNumber=1,
            Title=article.Title,
            Content=article.Content,
            Summary=article.Summary,
            CoverImage=article.CoverImage,
            Images=article.Images,
            Tags=article.Tags,
            RegenerationPrompt="Initial Automated Generation",
        )
        db.add(v1)

        # Route by approval requirement
        if not requires_approval:
            # Auto-Publish Mode
            article.Status = "published"
            article.PublishedAt = datetime.now(timezone.utc)
            db.commit()
            db.refresh(article)

            # Publish across target channels
            PublisherService.publish_to_all_channels(db, article, target_channels=channels, actor="system")

            cls.add_review_note(
                db, client_id, article.Id, 1,
                author_name="Automated Content Engine",
                author_role="system",
                content="Article generated and auto-published directly across configured channels.",
                action_taken="published"
            )
        else:
            # Manual Confirmation Mode
            article.Status = "pending_approval"
            article.SubmittedAt = datetime.now(timezone.utc)
            
            # Generate Review Token
            admin_email = req.admin_reviewer_email or (blog_settings.AdminNotificationEmail if blog_settings else None)
            review_url = TokenService.generate_review_url(article.Id, client_id, reviewer_email=admin_email)
            article.ReviewToken = TokenService.create_review_token(article.Id, client_id, reviewer_email=admin_email)
            db.commit()
            db.refresh(article)

            cls.add_review_note(
                db, client_id, article.Id, 1,
                author_name="Automated Content Engine",
                author_role="system",
                content="Article generated and sent to admin for editorial review & confirmation.",
                action_taken="submit_approval"
            )

            # Send Email
            if admin_email:
                EmailService.send_approval_request_email(
                    recipient_email=admin_email,
                    article_title=article.Title,
                    author_name=article.AuthorName,
                    author_email=article.AuthorEmail,
                    review_url=review_url,
                    summary=article.Summary,
                    version_number=1,
                    company_name=company_name,
                    has_multiple_versions=False,
                )

        db.commit()
        db.refresh(article)

        from ... import activity
        from ...activity import A
        activity.log(
            db,
            action=A.BLOG_ARTICLE_GENERATED,
            client_id=client_id,
            actor_email=req.author_email or "system",
            entity_type="blog",
            entity_id=article.Id,
            log_type="Info",
            message=f"Blog article generated: '{article.Title[:80]}' ({'Requires Approval' if requires_approval else 'Auto-Published'})",
            meta={"topic": req.topic, "mode": req.generation_mode, "requires_approval": requires_approval, "channels": channels},
            commit=True,
        )

        return cls._to_response(db, article)

    @classmethod
    def generate_multi_account_blogs(
        cls,
        db: Session,
        client_id: str,
        req: GenerateBlogRequest,
        company_name: str = "Your Organization"
    ) -> List[ArticleResponse]:
        """
        Generates uniquely tailored blog variants across all connected LinkedIn accounts
        (same core topic, distinct angle/perspective/style for each profile),
        then dispatches approval requests to admin or auto-publishes accordingly.
        """
        from ...models_ext import LeadChannelAccount

        # Check for active LinkedIn accounts for this company
        linkedin_accounts = (
            db.query(LeadChannelAccount)
            .filter(
                LeadChannelAccount.ClientId == client_id,
                LeadChannelAccount.Channel == "linkedin",
                LeadChannelAccount.IsDeleted == False,
            )
            .all()
        )

        target_channels = req.target_channels or []
        is_linkedin_target = (not target_channels) or any(c.lower() == "linkedin" for c in target_channels)

        # If only 1 account or non-LinkedIn target or specific account already chosen: generate standard article
        if len(linkedin_accounts) <= 1 or not is_linkedin_target or req.channel_account_id:
            return [cls.generate_and_save(db, client_id, req, company_name)]

        angles = [
            "Strategic Thought Leadership: Executive vision, industry paradigm shifts, and macroeconomic impact",
            "Practical Execution: Tactical workflows, practitioner frameworks, and step-by-step methodologies",
            "Technical Deep-Dive: Architecture, toolchains, implementation benchmarks, and edge cases",
            "Culture & Leadership: Team enablement, organizational change, and talent strategies",
        ]

        created_articles: List[ArticleResponse] = []
        for idx, acc in enumerate(linkedin_accounts):
            acc_name = acc.Name or f"LinkedIn Profile {idx + 1}"
            selected_angle = angles[idx % len(angles)] + f" (Tailored for {acc_name})"

            account_req = req.model_copy(deep=True)
            account_req.channel_account_id = acc.Id
            account_req.channel_account_name = acc_name
            account_req.author_name = acc_name
            account_req.variant_angle = selected_angle

            art_resp = cls.generate_and_save(
                db=db,
                client_id=client_id,
                req=account_req,
                company_name=company_name
            )
            created_articles.append(art_resp)

        return created_articles

    @classmethod
    def review_article(
        cls,
        db: Session,
        client_id: str,
        article_id: str,
        req: ReviewActionRequest,
        company_name: str = "Your Organization"
    ) -> ArticleResponse:
        """Handles Approve, Request Changes, Reject, or Regenerate review actions."""
        article = cls.get_article(db, client_id, article_id)
        if not article:
            raise ValueError(f"Article {article_id} not found.")

        # Locked state guard: once an article is published live, no further editorial actions are permitted
        if article.Status == "published":
            logger.info(
                "[ArticleService.review_article] Article %s is already published live. Action '%s' ignored.",
                article_id,
                req.action,
            )
            return cls._to_response(db, article)

        now = datetime.now(timezone.utc)

        if req.action == "approved":
            article.Status = "approved"
            article.ReviewedAt = now
            cls.add_review_note(
                db, client_id, article_id, article.CurrentVersion,
                author_name=req.reviewer_name or "Admin",
                author_role=req.reviewer_role or "manager",
                content=req.notes or "Article approved for publication.",
                action_taken="approved"
            )

            if req.publish_now:
                PublisherService.publish_to_all_channels(
                    db, article, target_channels=req.target_channels or article.TargetChannels, actor=req.reviewer_name or "Admin"
                )

        elif req.action == "changes_requested":
            article.Status = "changes_requested"
            article.ReviewedAt = now
            cls.add_review_note(
                db, client_id, article_id, article.CurrentVersion,
                author_name=req.reviewer_name or "Admin",
                author_role=req.reviewer_role or "manager",
                content=req.notes or "Editorial modifications requested.",
                action_taken="changes_requested"
            )

        elif req.action == "rejected":
            article.Status = "rejected"
            article.ReviewedAt = now
            cls.add_review_note(
                db, client_id, article_id, article.CurrentVersion,
                author_name=req.reviewer_name or "Admin",
                author_role=req.reviewer_role or "manager",
                content=req.notes or "Article rejected.",
                action_taken="rejected"
            )

        elif req.action == "regenerate":
            # Increment version and re-run generator
            new_version_num = (article.CurrentVersion or 1) + 1
            stored_prompt = article.GenerationPrompt or {}

            regen_topic = stored_prompt.get("topic") or article.Title
            if req.regeneration_prompt:
                regen_topic = f"{regen_topic} (Focus: {req.regeneration_prompt})"

            gen_req = GenerateBlogRequest(
                topic=regen_topic,
                tone=stored_prompt.get("tone", "professional"),
                target_audience=stored_prompt.get("target_audience"),
                keywords=stored_prompt.get("keywords") or article.Tags or [],
                target_words=stored_prompt.get("target_words", 1000),
                include_images=stored_prompt.get("include_images", True),
                num_images=stored_prompt.get("num_images", 1),
                cta_text=stored_prompt.get("cta_text"),
                cta_url=stored_prompt.get("cta_url"),
            )

            gen_res = GeneratorService.generate_blog(gen_req)

            article.Title = gen_res.title or article.Title
            article.Content = gen_res.content
            article.Summary = gen_res.summary or article.Summary
            article.CoverImage = gen_res.cover_image or article.CoverImage
            article.Images = gen_res.images or article.Images
            article.Tags = gen_res.tags or article.Tags
            article.CurrentVersion = new_version_num
            article.Status = "pending_approval"
            article.SubmittedAt = now

            # Add new version snapshot
            new_v = LeadArticleVersion(
                ClientId=client_id,
                ArticleId=article.Id,
                VersionNumber=new_version_num,
                Title=article.Title,
                Content=article.Content,
                Summary=article.Summary,
                CoverImage=article.CoverImage,
                Images=article.Images,
                Tags=article.Tags,
                RegenerationPrompt=req.regeneration_prompt or req.notes or "Admin requested regeneration",
            )
            db.add(new_v)

            cls.add_review_note(
                db, client_id, article_id, new_version_num,
                author_name=req.reviewer_name or "Admin",
                author_role=req.reviewer_role or "manager",
                content=f"Regenerated Version {new_version_num} created: {req.regeneration_prompt or req.notes or 'No specific feedback'}",
                action_taken="regenerated"
            )

            # Send Email for the new version
            blog_settings = db.query(LeadBlogSettings).filter(
                LeadBlogSettings.ClientId == client_id,
                LeadBlogSettings.IsDeleted == False
            ).first()
            admin_email = (blog_settings.AdminNotificationEmail if blog_settings else None)
            if admin_email:
                review_url = TokenService.generate_review_url(article.Id, client_id, reviewer_email=admin_email)
                EmailService.send_approval_request_email(
                    recipient_email=admin_email,
                    article_title=article.Title,
                    author_name="LeadAI Generator",
                    author_email=None,
                    review_url=review_url,
                    summary=article.Summary,
                    version_number=new_version_num,
                    company_name=company_name,
                    has_multiple_versions=True,
                )

        article.UpdatedAt = now
        db.commit()
        db.refresh(article)

        from ... import activity
        from ...activity import A
        action_map = {
            "approved": A.BLOG_ARTICLE_STATUS_CHANGED,
            "rejected": A.BLOG_ARTICLE_STATUS_CHANGED,
            "changes_requested": A.BLOG_ARTICLE_STATUS_CHANGED,
            "regenerate": A.BLOG_ARTICLE_GENERATING,
        }
        activity.log(
            db,
            action=action_map.get(req.action, A.BLOG_ARTICLE_STATUS_CHANGED),
            client_id=client_id,
            actor_email=req.reviewer_name or "admin",
            entity_type="blog",
            entity_id=article.Id,
            log_type="Info",
            message=f"Blog article '{article.Title[:80]}' editorial review action: {req.action}",
            meta={"action": req.action, "notes": req.notes, "version": article.CurrentVersion},
            commit=True,
        )

        return cls._to_response(db, article)

    @classmethod
    def add_review_note(
        cls,
        db: Session,
        client_id: str,
        article_id: str,
        version_number: Optional[int],
        author_name: str,
        author_role: str,
        content: str,
        action_taken: str
    ):
        """Appends a review note to the article history."""
        note = LeadBlogReviewNote(
            ClientId=client_id,
            ArticleId=article_id,
            VersionNumber=version_number,
            AuthorName=author_name,
            AuthorRole=author_role,
            Content=content,
            ActionTaken=action_taken,
        )
        db.add(note)
        db.commit()
