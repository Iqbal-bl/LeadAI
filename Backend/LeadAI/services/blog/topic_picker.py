"""
Automated Topic Picker Service.
Selects high-performing, trending, and company-tailored blog topics while avoiding recent duplicates.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from ...config import settings
from ...models_blog import LeadArticle, LeadBlogSettings

logger = logging.getLogger(__name__)


class SuggestedTopic(BaseModel):
    topic: str = Field(description="Catchy, high-value, SEO-optimized topic title.")
    keywords: List[str] = Field(description="3 to 5 focus keywords/tags relevant to this topic.")
    angle: str = Field(description="Brief explanation of why this topic is timely, valuable, or trending.")


class TopicPickerService:

    @classmethod
    def _get_llm(cls):
        api_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY")
        return ChatOpenAI(
            model=settings.openai_model or "gpt-4o-mini",
            api_key=api_key,
            temperature=0.7,
        )

    @classmethod
    def pick_daily_topic(
        cls,
        db: Session,
        client_id: str,
        blog_settings: Optional[LeadBlogSettings] = None
    ) -> Tuple[str, List[str]]:
        """
        Picks the next trending, high-converting blog topic for the company.
        Avoids topics created in the last 60 days.
        """
        if blog_settings is None:
            blog_settings = db.query(LeadBlogSettings).filter(
                LeadBlogSettings.ClientId == client_id,
                LeadBlogSettings.IsDeleted == False
            ).first()

        # 1. Fetch recent article titles for this company to prevent duplicates
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=60)
        recent_articles = (
            db.query(LeadArticle.Title)
            .filter(
                LeadArticle.ClientId == client_id,
                LeadArticle.CreatedAt >= cutoff_date,
                LeadArticle.IsDeleted == False,
            )
            .order_by(LeadArticle.CreatedAt.desc())
            .limit(30)
            .all()
        )
        recent_titles = [r[0] for r in recent_articles if r[0]]

        # 2. Gather company context
        niche = (blog_settings.TopicNiche if blog_settings and blog_settings.TopicNiche else None) or "Modern AI Automation, B2B Lead Generation & Intelligent Sales"
        default_keywords = (blog_settings.Keywords if blog_settings and blog_settings.Keywords else []) or ["AI Sales", "Lead Generation", "Automation"]
        audience = (blog_settings.TargetAudience if blog_settings and blog_settings.TargetAudience else "B2B Executives, Founders, and Sales Leaders")
        tone = (blog_settings.Tone if blog_settings and blog_settings.Tone else "thought_leadership")

        # 3. Prompt LLM to choose an exciting trending topic
        llm = cls._get_llm()
        structured_llm = llm.with_structured_output(SuggestedTopic)

        system_prompt = """
You are an elite B2B Content Strategist and Growth Editor.
Your job is to select ONE high-impact, timely, and engaging blog topic for a company.

Guidelines:
1. Topic must be deeply relevant to the company's niche and audience.
2. Must address a current industry trend, strategic challenge, actionable framework, or breakthrough insight.
3. Must NOT duplicate or closely rehash any of the recently published topics provided.
4. Output must include a strong title and 3-5 focus keywords.
"""

        user_content = f"""
Company Niche: {niche}
Target Audience: {audience}
Tone: {tone}
Preferred Seed Keywords: {', '.join(default_keywords)}
Current Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}

Recently Published Topics (DO NOT DUPLICATE OR CLOSELY REPEAT):
{json.dumps(recent_titles, indent=2) if recent_titles else 'None yet'}

Select the best new topic for today's article:
"""

        try:
            result: SuggestedTopic = structured_llm.invoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_content),
                ]
            )
            return result.topic, result.keywords or default_keywords
        except Exception as exc:
            logger.error(f"[TopicPickerService] Error picking topic for {client_id}: {exc}")
            # Fallback
            fallback_topic = f"{niche.split(',')[0].strip()} in {datetime.now().year}: Strategic Guide & Best Practices"
            return fallback_topic, default_keywords
