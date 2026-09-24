"""
Generator service executing the LangGraph blog workflow and styling the HTML output.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, List, Optional
import markdown

from .graph import blog_graph
from .schemas import GenerateBlogRequest, GenerateBlogResponse


class GeneratorService:

    @classmethod
    async def generate_blog_async(cls, req: GenerateBlogRequest) -> GenerateBlogResponse:
        """Executes LangGraph workflow in a background thread."""
        return await asyncio.to_thread(cls.generate_blog, req)

    @classmethod
    def _format_and_style_html(
        cls,
        raw_html: str,
        title: str,
        plan: Any,
        cta_text: Optional[str] = None,
        cta_url: Optional[str] = None
    ) -> str:
        """Applies responsive styling, Quick Summary card, typography, and CTA button."""
        # 1. Strip duplicate leading <h1> tag
        content = re.sub(r"^\s*<h1[^>]*>.*?</h1>", "", raw_html, flags=re.DOTALL | re.IGNORECASE).strip()

        # 2. Extract Quick Summary Bullets from plan or text
        summary_bullets = []
        if plan and hasattr(plan, "tasks") and plan.tasks:
            for t in plan.tasks[:4]:
                goal = getattr(t, "goal", "")
                if goal and len(goal) > 10:
                    summary_bullets.append(goal)

        if not summary_bullets:
            # Fallback: extract sentences from first paragraph
            first_p_match = re.search(r"<p>(.*?)</p>", content, flags=re.DOTALL)
            if first_p_match:
                sentences = re.split(r"\.\s+", re.sub(r"<[^>]+>", "", first_p_match.group(1)).strip())
                summary_bullets = [s.strip() for s in sentences if len(s.strip()) > 20][:3]

        if not summary_bullets:
            summary_bullets = [
                f"Strategic insights and practical implementation frameworks for {title}.",
                "Key tactical workflows, performance benchmarks, and decision-making clarity.",
                "Actionable recommendations and long-term business advantages."
            ]

        bullets_html = "".join(
            f'<li style="margin-bottom: 12px; line-height: 1.65; color: #1e293b;">{b}</li>' for b in summary_bullets
        )
        button_text = cta_text or "Book Free Strategy Session Today"
        target_cta_url = cta_url or "#strategy-session"

        summary_card = f"""
<div style="background: linear-gradient(135deg, #eef6ff 0%, #e0f0fe 100%); border: 1px solid #cde4fe; border-radius: 16px; padding: 28px 32px; margin: 20px 0 32px 0; box-shadow: 0 4px 16px rgba(0, 102, 255, 0.05);">
  <h3 style="font-size: 22px; font-weight: 700; color: #0f172a; margin-top: 0; margin-bottom: 16px; letter-spacing: -0.3px;">Quick Summary & Key Takeaways</h3>
  <ul style="padding-left: 20px; margin: 0 0 20px 0; font-size: 16px;">
    {bullets_html}
  </ul>
  <a href="{target_cta_url}" style="display: inline-block; background-color: #2563eb; color: #ffffff !important; font-size: 14.5px; font-weight: 700; text-decoration: none; padding: 12px 26px; border-radius: 9999px; box-shadow: 0 4px 14px rgba(37, 99, 235, 0.3); transition: all 0.2s ease;">
    {button_text} &rarr;
  </a>
</div>
"""

        # 3. Apply Inline CSS Styles to elements for clean display across email, CMS, and web
        content = re.sub(
            r"<h2([^>]*)>",
            r'<h2\1 style="font-size: 24px; font-weight: 700; color: #0f172a; margin: 34px 0 16px 0; letter-spacing: -0.3px;">',
            content
        )
        content = re.sub(
            r"<h3([^>]*)>",
            r'<h3\1 style="font-size: 20px; font-weight: 600; color: #1e293b; margin: 26px 0 12px 0;">',
            content
        )
        content = re.sub(
            r"<p([^>]*)>",
            r'<p\1 style="margin-bottom: 22px; color: #334155; font-size: 16.5px; line-height: 1.85;">',
            content
        )
        content = re.sub(
            r"<ul([^>]*)>",
            r'<ul\1 style="padding-left: 24px; margin-bottom: 24px; color: #334155; font-size: 16.5px;">',
            content
        )
        content = re.sub(
            r"<ol([^>]*)>",
            r'<ol\1 style="padding-left: 24px; margin-bottom: 24px; color: #334155; font-size: 16.5px;">',
            content
        )
        content = re.sub(
            r"<li([^>]*)>",
            r'<li\1 style="margin-bottom: 10px; line-height: 1.7;">',
            content
        )
        content = re.sub(
            r"<blockquote([^>]*)>",
            r'<blockquote\1 style="background: #f0f7ff; border-left: 4px solid #3b82f6; border-radius: 8px; padding: 16px 20px; margin: 24px 0; font-style: normal; color: #1e3a8a;">',
            content
        )
        content = re.sub(
            r"<pre([^>]*)>",
            r'<pre\1 style="background: #0f172a; color: #f8fafc; border-radius: 12px; padding: 20px 24px; overflow-x: auto; font-family: Consolas, monospace; font-size: 14.5px; line-height: 1.6; margin: 20px 0 28px 0;">',
            content
        )
        content = re.sub(
            r"<img([^>]*)>",
            r'<img\1 style="max-width: 100%; height: auto; border-radius: 12px; box-shadow: 0 8px 24px rgba(0,0,0,0.08); margin: 24px auto; display: block;">',
            content
        )

        styled_html = f"""<div class="leadai-blog-container" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; font-size: 16.5px; line-height: 1.85; color: #334155; max-width: 860px; margin: 0 auto;">
{summary_card}
{content}
</div>"""
        return styled_html

    @classmethod
    def generate_blog(cls, req: GenerateBlogRequest) -> GenerateBlogResponse:
        """Executes LangGraph blog workflow and returns styled HTML with images & tags."""
        blog_type = req.blog_type.lower() if req.blog_type else "text_and_image"
        include_images = req.include_images if req.include_images is not None else (blog_type != "text_only")
        effective_num_images = 0 if not include_images else (req.num_images or 1)

        state = {
            "topic": req.topic.strip(),
            "tone": req.tone or "professional",
            "target_audience": req.target_audience or "Business leaders, practitioners, and modern professionals",
            "keywords": req.keywords or [],
            "blog_type": "text_only" if not include_images else "text_and_image",
            "include_images": include_images,
            "num_images": effective_num_images,
            "target_words": req.target_words or 1000,
            "target_length": req.target_length or f"~{req.target_words or 1000} words",
            "language": req.language or "English",
            "cta_text": req.cta_text,
            "cta_url": req.cta_url,
            "mode": "closed_book",
            "needs_research": False,
            "queries": [],
            "evidence": [],
            "plan": None,
            "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "recency_days": 3650,
            "sections": [],
            "merged_md": "",
            "md_with_placeholders": "",
            "image_specs": [],
            "final": "",
        }

        # Invoke LangGraph
        result = blog_graph.invoke(state)

        final_md = result.get("final") or result.get("merged_md") or ""
        plan = result.get("plan")

        title = getattr(plan, "blog_title", None) or req.topic.title()

        raw_html = markdown.markdown(
            final_md,
            extensions=["extra", "nl2br"]
        )

        styled_html = cls._format_and_style_html(
            raw_html=raw_html,
            title=title,
            plan=plan,
            cta_text=req.cta_text,
            cta_url=req.cta_url,
        )

        # Extract images from markdown
        images = []
        cover_image = None
        if include_images:
            images = re.findall(r"!\[.*?\]\((https?://[^\s\)]+|data:image/[^\s\)]+)\)", final_md)
            if images:
                cover_image = images[0]

        # Generate short summary excerpt
        summary = ""
        first_p = re.search(r"<p[^>]*>(.*?)</p>", styled_html, flags=re.DOTALL)
        if first_p:
            clean_text = re.sub(r"<[^>]+>", "", first_p.group(1)).strip()
            summary = clean_text[:280] + ("..." if len(clean_text) > 280 else "")

        return GenerateBlogResponse(
            title=title,
            content=styled_html,
            summary=summary,
            cover_image=cover_image,
            images=images,
            tags=req.keywords or [],
        )
