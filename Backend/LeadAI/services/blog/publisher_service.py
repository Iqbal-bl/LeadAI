"""
Multi-Channel Publisher Service.
Publishes approved blog articles to WordPress Websites, LinkedIn, Facebook, and Instagram.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import requests
from requests.auth import HTTPBasicAuth
from sqlalchemy.orm import Session

from ...models_blog import LeadArticle, LeadBlogSettings
from ...social.service import publish as publish_social, urls_to_media

logger = logging.getLogger(__name__)


class PublisherService:

    @classmethod
    def publish_to_all_channels(
        cls,
        db: Session,
        article: LeadArticle,
        target_channels: Optional[List[str]] = None,
        actor: str = "system"
    ) -> Dict[str, Any]:
        """
        Publishes an article across all requested channels (WordPress, LinkedIn, Facebook, Instagram).
        Updates article.Results, post IDs, status, and published_at.
        """
        client_id = article.ClientId
        blog_settings: Optional[LeadBlogSettings] = db.query(LeadBlogSettings).filter(
            LeadBlogSettings.ClientId == client_id,
            LeadBlogSettings.IsDeleted == False
        ).first()

        # Determine channels to post to
        channels = target_channels or article.TargetChannels or (blog_settings.TargetChannels if blog_settings else []) or ["wordpress"]
        channels = [c.strip().lower() for c in channels if c]

        results: Dict[str, Any] = {}

        # 1. Publish to WordPress if selected
        if "wordpress" in channels or "website" in channels:
            wp_res = cls.publish_to_wordpress(db, client_id, article, blog_settings)
            results["wordpress"] = wp_res
            if wp_res.get("success") and wp_res.get("url"):
                article.WordPressPostUrl = wp_res.get("url")
                article.WordPressPostId = str(wp_res.get("post_id", ""))

        # 2. Publish to Social Channels (LinkedIn, Facebook, Instagram)
        social_channels = [c for c in channels if c in ("linkedin", "facebook", "instagram")]
        if social_channels:
            social_res = cls.publish_to_social(db, client_id, article, social_channels, actor=actor)
            results.update(social_res)
            if "linkedin" in results and results["linkedin"].get("id"):
                li_id = str(results["linkedin"]["id"])
                article.LinkedInPostId = li_id
                if not results["linkedin"].get("url"):
                    if li_id.startswith("http"):
                        results["linkedin"]["url"] = li_id
                    elif ":" in li_id:
                        results["linkedin"]["url"] = f"https://www.linkedin.com/feed/update/{li_id}/"
                    else:
                        results["linkedin"]["url"] = f"https://www.linkedin.com/feed/update/urn:li:share:{li_id}/"
            if "facebook" in results and results["facebook"].get("id"):
                article.FacebookPostId = str(results["facebook"]["id"])
                if not results["facebook"].get("url"):
                    results["facebook"]["url"] = f"https://www.facebook.com/{results['facebook']['id']}"
            if "instagram" in results and results["instagram"].get("id"):
                article.InstagramMediaId = str(results["instagram"]["id"])
                if not results["instagram"].get("url"):
                    results["instagram"]["url"] = f"https://www.instagram.com/p/{results['instagram']['id']}/"

        # Determine overall success
        any_success = any(r.get("success") for r in results.values())
        all_success = all(r.get("success") for r in results.values()) if results else False

        article.Results = results
        article.UpdatedAt = datetime.now(timezone.utc)

        if all_success or any_success:
            article.Status = "published"
            article.PublishedAt = datetime.now(timezone.utc)
        else:
            article.Status = "published" if not results else "failed"

        db.commit()
        db.refresh(article)
        return results

    @classmethod
    def publish_to_wordpress(
        cls,
        db: Session,
        client_id: str,
        article: LeadArticle,
        blog_settings: Optional[LeadBlogSettings] = None
    ) -> Dict[str, Any]:
        """Publishes article content to WordPress REST API."""
        if blog_settings is None:
            blog_settings = db.query(LeadBlogSettings).filter(
                LeadBlogSettings.ClientId == client_id,
                LeadBlogSettings.IsDeleted == False
            ).first()

        wp_url = (blog_settings.WordPressUrl if blog_settings and blog_settings.WordPressUrl else None) or os.getenv("WEBSITE_URL") or os.getenv("WP_URL")
        wp_user = (blog_settings.WordPressUsername if blog_settings and blog_settings.WordPressUsername else None) or os.getenv("WEBSITE_USERNAME") or os.getenv("WP_USERNAME")
        wp_pass = None

        if blog_settings and blog_settings.WordPressAppPasswordEnc:
            from ...security import decrypt_pii
            try:
                wp_pass = decrypt_pii(blog_settings.WordPressAppPasswordEnc)
            except Exception:
                wp_pass = blog_settings.WordPressAppPasswordEnc
        if not wp_pass:
            wp_pass = os.getenv("WEBSITE_PASSWORD") or os.getenv("WP_PASSWORD")

        if not (wp_url and wp_user and wp_pass):
            msg = "WordPress URL, Username, or Password not configured."
            logger.info(f"[PublisherService] {msg}")
            return {"success": False, "skipped": True, "error": msg}

        clean_url = wp_url.strip().rstrip("/")
        if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
            clean_url = f"https://{clean_url}"

        endpoint = f"{clean_url}/wp-json/wp/v2/posts"
        auth = HTTPBasicAuth(wp_user.strip(), wp_pass.strip())

        payload = {
            "title": article.Title,
            "content": article.Content or "",
            "status": "publish",
            "excerpt": article.Summary or "",
        }
        if article.Slug:
            payload["slug"] = article.Slug

        try:
            headers = {
                "User-Agent": "LeadAI-Publisher/2.0",
                "Content-Type": "application/json"
            }
            resp = requests.post(endpoint, json=payload, auth=auth, headers=headers, timeout=30)
            if resp.status_code in (200, 201):
                data = resp.json()
                live_link = data.get("link") or f"{clean_url}/?p={data.get('id')}"
                return {
                    "success": True,
                    "post_id": data.get("id"),
                    "url": live_link,
                }
            else:
                err = f"WordPress REST API error ({resp.status_code}): {resp.text[:300]}"
                logger.error(f"[PublisherService] {err}")
                return {"success": False, "error": err}
        except Exception as exc:
            err = f"WordPress connection error: {exc}"
            logger.error(f"[PublisherService] {err}")
            return {"success": False, "error": err}

    @classmethod
    def publish_to_social(
        cls,
        db: Session,
        client_id: str,
        article: LeadArticle,
        social_channels: List[str],
        actor: str = "system"
    ) -> Dict[str, Any]:
        """Publishes article summary, link, and cover images to LinkedIn, Facebook, and Instagram."""
        # Build rich long-form social caption
        caption = cls._build_rich_social_caption(article)

        # Build media list
        media_urls = []
        if article.CoverImage:
            media_urls.append(article.CoverImage)
        elif article.Images and len(article.Images) > 0:
            media_urls.append(article.Images[0])

        uploaded = urls_to_media(media_urls)

        # Run async social publisher
        loop = asyncio.new_event_loop()
        try:
            results, post_row = loop.run_until_complete(
                publish_social(
                    db=db,
                    client_id=client_id,
                    caption=caption.strip(),
                    uploaded=uploaded,
                    platforms=social_channels,
                    actor=actor,
                    mode="ai",
                    topic=article.Title,
                    record=True,
                )
            )
            return results
        except Exception as exc:
            logger.error(f"[PublisherService] Error publishing to social channels {social_channels}: {exc}")
            return {p: {"success": False, "error": str(exc)} for p in social_channels}
        finally:
            loop.close()

    @classmethod
    def _build_rich_social_caption(cls, article: LeadArticle, max_length: int = 2800) -> str:
        """Extracts and formats full article content into a high-impact long-form social post."""
        tags_str = " ".join([f"#{t.replace(' ', '').replace('-', '')}" for t in (article.Tags or []) if t])

        # Clean HTML to readable Markdown text
        html = article.Content or ""
        # Remove CTA buttons & banners
        html = re.sub(r'<a[^>]*class="[^"]*cta[^"]*"[^>]*>.*?</a>', '', html, flags=re.DOTALL)
        # Format headings & lists
        text = re.sub(r'<h[1-2][^>]*>(.*?)</h[1-2]>', r'\n\n📌 \1\n', html)
        text = re.sub(r'<h[3-6][^>]*>(.*?)</h[3-6]>', r'\n\n🔹 \1\n', html)
        text = re.sub(r'<li[^>]*>(.*?)</li>', r'\n• \1', text)
        text = re.sub(r'<p[^>]*>(.*?)</p>', r'\n\n\1', text)
        text = re.sub(r'<br\s*/?>', r'\n', text)
        # Strip all other HTML tags & entities
        text = re.sub(r'<[^>]+>', '', text)
        text = text.replace("&rarr;", "→").replace("&bull;", "•").replace("&amp;", "&").replace("&quot;", '"')
        text = re.sub(r'\n{3,}', '\n\n', text).strip()

        # Remove duplicate Quick Summary block if present in content
        if "Quick Summary" in text:
            parts = text.split("Quick Summary", 1)
            if len(parts) > 1 and "📌" in parts[1]:
                text = parts[1][parts[1].find("📌"):].strip()

        header = f"🚀 {article.Title}\n\n{article.Summary or ''}\n\n"
        footer = f"\n\n💬 What strategy has worked best for your team? Share your thoughts below!\n\n{tags_str}"
        if article.WordPressPostUrl:
            footer = f"\n\n📖 Read full publication: {article.WordPressPostUrl}" + footer

        available_len = max_length - len(header) - len(footer)
        if available_len > 300 and len(text) > 100:
            truncated_body = text[:available_len]
            last_break = max(truncated_body.rfind("."), truncated_body.rfind("\n"))
            if last_break > available_len * 0.7:
                truncated_body = truncated_body[:last_break + 1]
            caption = header + "Key Insights & Strategic Framework:\n" + truncated_body + footer
        else:
            caption = header + footer

        return caption.strip()
