"""
LeadAI — AI Contextual Comment & Reply Intelligence Service.

Extracts company profile context, knowledge base passages, and post context to
formulate highly relevant, brand-aligned responses to LinkedIn comments.
"""
from __future__ import annotations

import logging
import random
from typing import Optional, Tuple
from sqlalchemy.orm import Session

from core.observability import traceable
from .llm import complete_json
from ..models import (
    LeadCustomer,
    LeadChannelIdentity,
    LeadChannelAccount,
    LeadKbChunk,
    LeadActivityLog,
    utcnow,
)
from ..models_blog import (
    LeadSocialComment,
    LeadCommentSettings,
    LeadBlogSettings,
    LeadArticle,
)
from ..security import encrypt_pii
from . import crm as crm_service

logger = logging.getLogger("leadai.services.comment_reply_ai")


class CommentReplyAIService:
    """Orchestrates AI analysis, sentiment scoring, contextual reply generation, and CRM lead capture."""

    @classmethod
    def get_or_create_settings(cls, db: Session, client_id: str, channel: str = "linkedin") -> LeadCommentSettings:
        """Fetch or initialize default comment automation settings for a company."""
        settings = (
            db.query(LeadCommentSettings)
            .filter(
                LeadCommentSettings.ClientId == client_id,
                LeadCommentSettings.Channel == channel,
                LeadCommentSettings.IsDeleted == False,
            )
            .first()
        )
        if not settings:
            settings = LeadCommentSettings(
                ClientId=client_id,
                Channel=channel,
                IsAutoReplyEnabled=False,
                RequireApprovalForQuestions=True,
                ReplyTone="thought_leadership",
                AutoCaptureLeads=True,
                MinLeadIntentThreshold=0.6,
                ExcludeKeywords=["scam", "spam", "refund", "fake", "terrible", "complaint"],
                CreatedBy="system",
            )
            db.add(settings)
            db.commit()
            db.refresh(settings)
        return settings

    @classmethod
    def gather_company_context(cls, db: Session, client_id: str) -> dict:
        """Gather all available business context for the company."""
        try:
            from domain.models import Client
        except ImportError:
            pass
        
        client = db.query(Client).filter(Client.Id == client_id, Client.IsDeleted == False).first()
        company_name = client.Name if client else "Our Organization"
        company_desc = client.Description if client and client.Description else ""
        
        blog_settings = (
            db.query(LeadBlogSettings)
            .filter(LeadBlogSettings.ClientId == client_id, LeadBlogSettings.IsDeleted == False)
            .first()
        )
        
        niche = blog_settings.TopicNiche if blog_settings and blog_settings.TopicNiche else "B2B Solutions & Technology"
        target_audience = blog_settings.TargetAudience if blog_settings and blog_settings.TargetAudience else "Professionals and decision makers"
        cta_text = blog_settings.CtaText if blog_settings and blog_settings.CtaText else "Learn more on our website"
        cta_url = blog_settings.CtaUrl if blog_settings and blog_settings.CtaUrl else ""
        
        return {
            "company_name": company_name,
            "company_description": company_desc,
            "industry_niche": niche,
            "target_audience": target_audience,
            "cta_text": cta_text,
            "cta_url": cta_url,
        }

    @classmethod
    def search_kb_context(cls, db: Session, client_id: str, query_text: str, limit: int = 3) -> list[str]:
        """Search company knowledge base chunks for relevant context."""
        if not query_text:
            return []
        
        keywords = [w.lower() for w in query_text.split() if len(w) > 3]
        if not keywords:
            return []
            
        chunks = (
            db.query(LeadKbChunk)
            .filter(LeadKbChunk.ClientId == client_id, LeadKbChunk.IsDeleted == False)
            .limit(30)
            .all()
        )
        
        matches = []
        for ch in chunks:
            text_lower = ch.ChunkText.lower()
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > 0:
                matches.append((score, ch.ChunkText[:400]))
                
        matches.sort(key=lambda x: x[0], reverse=True)
        return [m[1] for m in matches[:limit]]

    @classmethod
    @traceable(name="tool:comment_reply_ai", run_type="tool")
    def generate_reply_for_comment(
        cls,
        db: Session,
        comment: LeadSocialComment,
        custom_instruction_override: Optional[str] = None,
    ) -> dict:
        """Use LLM with company, post, and comment context to draft an intelligent reply and classify intent."""
        client_id = comment.ClientId
        settings = cls.get_or_create_settings(db, client_id, comment.Channel)
        company_ctx = cls.gather_company_context(db, client_id)
        
        # Post Context
        post_title = comment.PostTitle or "Recent LinkedIn Post"
        post_snippet = comment.PostSnippet or ""
        
        # Knowledge Base excerpts
        kb_excerpts = cls.search_kb_context(db, client_id, comment.CommentText)
        kb_text = "\n---\n".join(kb_excerpts) if kb_excerpts else "No specific knowledge base notes found."

        tone = settings.ReplyTone or "thought_leadership"
        custom_instructions = custom_instruction_override or settings.CustomInstructions or ""

        system_prompt = f"""You are the official LinkedIn voice and community engagement specialist for {company_ctx['company_name']}.
Your goal is to write intelligent, thoughtful, human-sounding, and context-rich replies to comments on company LinkedIn posts.

COMPANY PROFILE:
- Name: {company_ctx['company_name']}
- Summary: {company_ctx['company_description'] or 'Leading industry innovators'}
- Niche & Expertise: {company_ctx['industry_niche']}
- Value Proposition / CTA: {company_ctx['cta_text']} ({company_ctx['cta_url']})

KNOWLEDGE BASE CONTEXT:
{kb_text}

REPLY GUIDELINES:
1. Tone: {tone.replace('_', ' ').title()}.
2. Be authentic, professional, and conversational. NEVER sound like a generic AI (Avoid: "Thank you for your valuable insight!", "Indeed!", "Great question!").
3. Do NOT prefix or greet with the commenter's name (e.g., do NOT start with "Hi John," or "John,") because LinkedIn automatically prefixes their @mention tag to the reply. Jump straight into the conversational response.
4. Connect their comment directly to the specific topic/insights of the post.
5. If the comment is a question: answer it clearly and accurately using the context provided.
6. If the comment expresses interest, inquiry, or partnership: provide a helpful answer and gently invite them to connect or send a DM.
7. If the comment is praise or agreement: acknowledge their specific point and build on it with a brief engaging follow-up thought or question.
8. Length: Concise (2 to 4 sentences max).

OUTPUT FORMAT:
Output strictly a JSON object with this structure:
{{
  "sentiment": "positive" | "question" | "lead_inquiry" | "praise" | "critical" | "neutral",
  "intent_score": <number between 0.0 and 1.0 indicating buying/lead interest>,
  "is_question": <true or false>,
  "is_lead_candidate": <true or false>,
  "suggested_reply": "<the exact reply message text>",
  "rationale": "<1 sentence explanation of why this reply was crafted>"
}}"""

        user_content = f"""POST TITLE: {post_title}
POST CONTENT / EXCERPT:
{post_snippet}

COMMENT DETAILS:
- Commenter Name: {comment.AuthorName}
- Commenter Headline: {comment.AuthorHeadline or 'Professional'}
- Comment Content: "{comment.CommentText}"
{f"- Special Instruction: {custom_instructions}" if custom_instructions else ""}"""

        messages = [{"role": "user", "content": user_content}]
        from core.usage_tracker import bind_usage_context

        with bind_usage_context(company_id=comment.ClientId, process="comment_reply_generation", channel="social"):
            result, meta = complete_json(system_prompt, messages)


        if not result or "suggested_reply" not in result:
            # Fallback reply
            first_name = comment.AuthorName.split()[0] if comment.AuthorName else "there"
            suggested = f"Hi {first_name}, thanks for sharing your thoughts on this! Really appreciate your perspective."
            sentiment = "positive"
            intent_score = 0.3
            is_question = "?" in comment.CommentText
            is_lead = False
            rationale = "Default polite acknowledgement."
        else:
            suggested = result.get("suggested_reply", "").strip()
            sentiment = result.get("sentiment", "neutral")
            intent_score = float(result.get("intent_score", 0.0))
            is_question = bool(result.get("is_question", False))
            is_lead = bool(result.get("is_lead_candidate", intent_score >= settings.MinLeadIntentThreshold))
            rationale = result.get("rationale", "")

        # Multi-turn engagement rule:
        # If the user comments on a post, we reply, and the user replies back (or user has >=2 comments on this post),
        # they are actively engaged and MUST be captured as a lead regardless of AI intent score cutoff.
        is_multi_turn_reply = False
        if comment.ParentCommentUrn:
            parent = (
                db.query(LeadSocialComment)
                .filter(
                    LeadSocialComment.CommentUrn == comment.ParentCommentUrn,
                    LeadSocialComment.IsDeleted == False,
                )
                .first()
            )
            if parent and (parent.RepliedBy or parent.Status in ("approved", "auto_replied", "replied")):
                is_multi_turn_reply = True

        if not is_multi_turn_reply and (comment.AuthorUrn or comment.AuthorName):
            author_filter = (
                LeadSocialComment.AuthorUrn == comment.AuthorUrn
                if comment.AuthorUrn
                else LeadSocialComment.AuthorName == comment.AuthorName
            )
            prior_count = (
                db.query(LeadSocialComment)
                .filter(
                    LeadSocialComment.ClientId == comment.ClientId,
                    LeadSocialComment.PostUrn == comment.PostUrn,
                    LeadSocialComment.Id != comment.Id,
                    author_filter,
                    LeadSocialComment.IsDeleted == False,
                )
                .count()
            )
            if prior_count >= 1:
                is_multi_turn_reply = True

        if is_multi_turn_reply:
            is_lead = True
            intent_score = max(intent_score, 0.75)
            rationale = (rationale + " [Multi-turn engagement: User actively replied in thread]").strip()

        # Save updates to comment row
        comment.Sentiment = sentiment
        comment.IntentScore = intent_score
        comment.IsQuestion = is_question
        comment.IsLeadCandidate = is_lead
        comment.SuggestedReply = suggested
        comment.SuggestedReplyRationale = rationale
        comment.ContextUsedJson = {
            "company_name": company_ctx["company_name"],
            "post_title": post_title,
            "kb_chunks_used": len(kb_excerpts),
            "model_meta": meta,
        }
        comment.UpdatedAt = utcnow()
        db.commit()

        # If lead auto-capture is enabled and high intent detected, sync to CRM
        if settings.AutoCaptureLeads and is_lead and not comment.CustomerId:
            cls.capture_commenter_as_lead(db, comment)

        return {
            "sentiment": sentiment,
            "intent_score": intent_score,
            "is_question": is_question,
            "is_lead_candidate": is_lead,
            "suggested_reply": suggested,
            "rationale": rationale,
        }

    @classmethod
    def capture_commenter_as_lead(cls, db: Session, comment: LeadSocialComment) -> Optional[LeadCustomer]:
        """Convert a social media commenter (Instagram, Facebook/Messenger, LinkedIn) into a LeadCustomer,
        LeadConversation, Lead, and LeadAccount in CRM with full source and origin attribution."""
        from ..models import (
            LeadConversation,
            Lead,
            LeadMessage,
            CHANNEL_MESSENGER,
            CHANNEL_INSTAGRAM,
        )

        channel_raw = (comment.Channel or "linkedin").lower()
        if channel_raw in ("messenger", "facebook"):
            channel_key = "messenger"
            channel_label = "Facebook"
            default_display = "Facebook User"
        elif channel_raw == "instagram":
            channel_key = "instagram"
            channel_label = "Instagram"
            default_display = "Instagram User"
        elif channel_raw == "linkedin":
            channel_key = "linkedin"
            channel_label = "LinkedIn"
            default_display = "LinkedIn Member"
        else:
            channel_key = channel_raw
            channel_label = channel_raw.title()
            default_display = f"{channel_label} User"

        customer: Optional[LeadCustomer] = None
        identity: Optional[LeadChannelIdentity] = None

        # ------------------------------------------------------------------ #
        # Step 1: Resolve or Link LeadCustomer & LeadChannelIdentity          #
        # ------------------------------------------------------------------ #
        if comment.CustomerId:
            customer = db.get(LeadCustomer, comment.CustomerId)
            if customer:
                if channel_key == "linkedin" and not customer.LinkedinProfileUrl and comment.AuthorProfileUrl:
                    customer.LinkedinProfileUrl = comment.AuthorProfileUrl
                    customer.UpdatedAt = utcnow()
                elif channel_key == "instagram" and comment.AuthorName and not customer.InstagramEnc:
                    customer.InstagramEnc = encrypt_pii(comment.AuthorName.lstrip("@"))
                    customer.UpdatedAt = utcnow()

        if not customer:
            if channel_key == "linkedin":
                chan_acct = (
                    db.query(LeadChannelAccount)
                    .filter(
                        LeadChannelAccount.ClientId == comment.ClientId,
                        LeadChannelAccount.Channel == "linkedin",
                        LeadChannelAccount.IsDeleted == False,
                    )
                    .first()
                )
                channel_account_id = chan_acct.Id if chan_acct else "linkedin-default"
                external_id = comment.AuthorUrn or comment.AuthorProfileUrl or f"linkedin_comment_{comment.AuthorName.replace(' ', '_')}"

                from ..social.linkedin_bot import find_or_link_linkedin_customer
                customer, identity = find_or_link_linkedin_customer(
                    db=db,
                    client_id=comment.ClientId,
                    channel_account_id=channel_account_id,
                    sender_urn=comment.AuthorUrn or external_id,
                    profile_url=comment.AuthorProfileUrl,
                    display_name=comment.AuthorName,
                    created_by="linkedin_comment_ai",
                )
            else:
                # Facebook / Messenger / Instagram
                chan_acct = None
                if comment.AccountId:
                    chan_acct = db.get(LeadChannelAccount, comment.AccountId)
                if not chan_acct:
                    chan_acct = (
                        db.query(LeadChannelAccount)
                        .filter(
                            LeadChannelAccount.ClientId == comment.ClientId,
                            LeadChannelAccount.Channel.in_([channel_key, "messenger", "facebook"] if channel_key == "messenger" else [channel_key]),
                            LeadChannelAccount.IsDeleted == False,
                        )
                        .first()
                    )
                channel_account_id = chan_acct.Id if chan_acct else None

                author_ext_id = str(comment.AuthorUrn or f"{channel_key}_{comment.Id}")
                # 1. Match identity by ExternalUserId
                identity = (
                    db.query(LeadChannelIdentity)
                    .filter(
                        LeadChannelIdentity.ClientId == comment.ClientId,
                        LeadChannelIdentity.Channel.in_([channel_key, "messenger", "facebook"] if channel_key == "messenger" else [channel_key]),
                        LeadChannelIdentity.ExternalUserId == author_ext_id,
                        LeadChannelIdentity.IsDeleted == False,
                    )
                    .first()
                )
                if identity:
                    customer = db.get(LeadCustomer, identity.CustomerId)

                # 2. Match customer by DisplayName / Username if not found by identity
                if not customer and comment.AuthorName and comment.AuthorName != default_display:
                    customer = (
                        db.query(LeadCustomer)
                        .filter(
                            LeadCustomer.ClientId == comment.ClientId,
                            LeadCustomer.DisplayName == comment.AuthorName,
                            LeadCustomer.IsDeleted == False,
                        )
                        .first()
                    )

                # 3. Create brand-new LeadCustomer if none found
                if not customer:
                    customer = LeadCustomer(
                        ClientId=comment.ClientId,
                        PublicRef=f"Lead #{random.randint(10000, 99999)}",
                        DisplayName=comment.AuthorName or default_display,
                        InstagramEnc=encrypt_pii(comment.AuthorName.lstrip("@") if comment.AuthorName else author_ext_id) if channel_key == "instagram" else None,
                        PhoneEnc=encrypt_pii(None),
                        CreatedBy=f"{channel_key}_comment",
                    )
                    db.add(customer)
                    db.flush()

                # Ensure LeadChannelIdentity exists
                if not identity:
                    identity = LeadChannelIdentity(
                        ClientId=comment.ClientId,
                        ChannelAccountId=channel_account_id,
                        Channel=channel_key,
                        ExternalUserId=author_ext_id,
                        CustomerId=customer.Id,
                        ProfileName=comment.AuthorName or customer.DisplayName,
                        CreatedBy=f"{channel_key}_comment",
                    )
                    db.add(identity)
                    db.flush()

        comment.CustomerId = customer.Id
        if identity:
            comment.IdentityId = identity.Id

        # ------------------------------------------------------------------ #
        # Step 2: Ensure LeadConversation + Lead exist in Inbox               #
        # ------------------------------------------------------------------ #
        conv = (
            db.query(LeadConversation)
            .filter(
                LeadConversation.ClientId == comment.ClientId,
                LeadConversation.CustomerId == customer.Id,
                LeadConversation.Channel.in_([channel_key, "messenger", "facebook"] if channel_key == "messenger" else [channel_key]),
                LeadConversation.IsDeleted == False,
                LeadConversation.Status != "closed",
            )
            .order_by(LeadConversation.CreatedAt.desc())
            .first()
        )

        post_heading = comment.PostTitle or comment.PostSnippet or f"{channel_label} Post"
        if len(post_heading) > 80:
            post_heading = post_heading[:77] + "..."

        if not conv:
            conv = LeadConversation(
                ClientId=comment.ClientId,
                CustomerId=customer.Id,
                Channel=channel_key,
                Status="open",
                Summary=f"Lead captured from {channel_label} post: \"{post_heading}\"",
                NextStep="Respond to commenter or initiate follow-up",
                ChannelAccountId=comment.AccountId,
                ExternalThreadId=str(comment.CommentUrn),
                MessageCount=1,
                LastMessageAt=comment.CommentCreatedAt or utcnow(),
                CreatedBy=f"{channel_key}_comment",
            )
            db.add(conv)
            db.flush()

        if identity and not identity.ConversationId:
            identity.ConversationId = conv.Id

        # Link/Update Lead qualification row
        lead_row = (
            db.query(Lead)
            .filter(Lead.ConversationId == conv.Id)
            .first()
        )
        intent_score_pct = int(min(1.0, max(0.0, float(comment.IntentScore or 0.7))) * 100)
        status_val = "qualified" if intent_score_pct >= 75 else ("warm" if intent_score_pct >= 45 else "cold")

        if not lead_row:
            lead_row = Lead(
                ClientId=comment.ClientId,
                ConversationId=conv.Id,
                Status=status_val,
                Score=intent_score_pct,
                Interest=(comment.PostTitle or "Social Post Inquiry")[:160],
                Intent="inquiry",
                Product=comment.PostTitle[:100] if comment.PostTitle else "unknown",
                Sentiment=comment.Sentiment or "positive",
                CreatedBy=f"{channel_key}_comment",
            )
            db.add(lead_row)
            db.flush()
        else:
            if intent_score_pct > (lead_row.Score or 0):
                lead_row.Score = intent_score_pct
                lead_row.Status = status_val
                lead_row.UpdatedAt = utcnow()

        # Add comment as first LeadMessage if not already present in conversation
        has_msg = db.query(LeadMessage).filter(LeadMessage.ConversationId == conv.Id).first()
        if not has_msg and comment.CommentText:
            msg = LeadMessage(
                ClientId=comment.ClientId,
                ConversationId=conv.Id,
                Sender="customer",
                Content=comment.CommentText,
                CreatedAt=comment.CommentCreatedAt or utcnow(),
                CreatedBy="meta_webhook" if channel_key in ("messenger", "instagram") else "system",
            )
            db.add(msg)
            db.flush()

        # ------------------------------------------------------------------ #
        # Step 3: Upsert CRM Account with origin fields & source attribution #
        # ------------------------------------------------------------------ #
        origin_fields = {
            "origin_type": "post_comment",
            "channel": channel_key,
            "post_id": comment.PostUrn,
            "post_title": comment.PostTitle,
            "post_snippet": comment.PostSnippet,
            "comment_id": comment.CommentUrn,
            "comment_text": comment.CommentText,
            "author_profile_url": comment.AuthorProfileUrl,
        }

        acct = crm_service.create_account(
            db, comment.ClientId,
            display_name=customer.DisplayName or comment.AuthorName or default_display,
            source=channel_key,
            stage="lead",
            customer_id=customer.Id,
            linkedin_profile_url=comment.AuthorProfileUrl if channel_key == "linkedin" else (getattr(customer, "LinkedinProfileUrl", None)),
            tags=f"{channel_key},post_comment,comment_lead",
            fields=origin_fields,
            actor=f"{channel_key}_comment_ai",
        )
        if acct and not acct.SourceConversationId and conv:
            acct.SourceConversationId = conv.Id

        db.commit()
        logger.info(
            f"Captured/linked {channel_label} commenter as CRM Lead: {customer.DisplayName} ({customer.Id}) "
            f"[Post: {comment.PostTitle[:40] if comment.PostTitle else 'N/A'}]"
        )
        return customer

