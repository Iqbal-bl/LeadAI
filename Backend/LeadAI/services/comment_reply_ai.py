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
        """Convert a LinkedIn commenter into a LeadCustomer and LeadChannelIdentity in CRM."""
        if comment.CustomerId:
            return db.get(LeadCustomer, comment.CustomerId)

        external_id = comment.AuthorUrn or comment.AuthorProfileUrl or f"linkedin_comment_{comment.AuthorName.replace(' ', '_')}"
        
        # Check if identity already exists
        identity = (
            db.query(LeadChannelIdentity)
            .filter(
                LeadChannelIdentity.ClientId == comment.ClientId,
                LeadChannelIdentity.ExternalUserId == str(external_id),
                LeadChannelIdentity.IsDeleted == False,
            )
            .first()
        )

        customer = None
        if identity:
            customer = db.get(LeadCustomer, identity.CustomerId)
            comment.CustomerId = identity.CustomerId
            comment.IdentityId = identity.Id
            db.commit()
            return customer

        # Resolve channel account ID
        chan_acct = (
            db.query(LeadChannelAccount)
            .filter(
                LeadChannelAccount.ClientId == comment.ClientId,
                LeadChannelAccount.Channel == (comment.Channel or "linkedin"),
                LeadChannelAccount.IsDeleted == False,
            )
            .first()
        )
        channel_account_id = chan_acct.Id if chan_acct else "linkedin-default"

        # Create new customer
        display_name = comment.AuthorName or "LinkedIn Member"
        customer = LeadCustomer(
            ClientId=comment.ClientId,
            PublicRef=f"Lead #{random.randint(10000, 99999)}",
            DisplayName=display_name,
            PhoneEnc=encrypt_pii(None),
            CreatedBy="linkedin_comment_ai",
        )
        db.add(customer)
        db.flush()

        identity = LeadChannelIdentity(
            ClientId=comment.ClientId,
            ChannelAccountId=channel_account_id,
            Channel=comment.Channel or "linkedin",
            ExternalUserId=str(external_id),
            CustomerId=customer.Id,
            ProfileName=display_name,
            CreatedBy="linkedin_comment_ai",
        )
        db.add(identity)
        db.flush()

        comment.CustomerId = customer.Id
        comment.IdentityId = identity.Id
        db.commit()
        logger.info(f"Captured new LinkedIn commenter as CRM Lead: {display_name} ({customer.Id})")
        return customer

