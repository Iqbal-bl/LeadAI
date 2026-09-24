"""
Email Service for sending editorial approval notifications to Organization Admins.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from ...config import settings

logger = logging.getLogger(__name__)


class EmailService:

    @classmethod
    def send_approval_request_email(
        cls,
        recipient_email: str,
        article_title: str,
        author_name: str,
        author_email: Optional[str],
        review_url: str,
        summary: Optional[str] = None,
        version_number: int = 1,
        company_name: Optional[str] = "Your Organization",
        has_multiple_versions: bool = False
    ) -> bool:
        """Sends an HTML review request email with link to the Organization Admin."""
        if not recipient_email:
            logger.warning("[EmailService] No recipient email specified for approval request.")
            return False

        smtp_host = settings.smtp_host or os.getenv("MAIL_SERVER") or os.getenv("SMTP_HOST")
        smtp_port = settings.smtp_port or int(os.getenv("MAIL_PORT") or os.getenv("SMTP_PORT") or 587)
        smtp_user = settings.smtp_user or os.getenv("MAIL_USERNAME") or os.getenv("SMTP_USER")
        smtp_password = settings.smtp_password or os.getenv("MAIL_PASSWORD") or os.getenv("SMTP_PASSWORD")
        from_email = settings.smtp_from or os.getenv("MAIL_FROM") or smtp_user or "no-reply@leadai.io"
        from_name = os.getenv("SMTP_FROM_NAME") or f"{company_name} • LeadAI Publishing"

        if not (smtp_host and smtp_user and smtp_password):
            logger.info(
                f"[EmailService] SMTP not fully configured. Review URL for '{article_title}': {review_url}"
            )
            return False

        try:
            is_regen = version_number > 1
            subject_prefix = f"🔄 [Version {version_number} Regenerated]" if is_regen else "📝 Approval Required:"
            header_title = f"Version {version_number} Ready for Review" if is_regen else "New Blog Draft Ready for Review"
            header_subtitle = "A newly regenerated version is waiting for your review." if is_regen else "The daily automated content engine generated a new blog post."

            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"{subject_prefix} \"{article_title}\""
            msg["From"] = f"{from_name} <{from_email}>"
            msg["To"] = recipient_email

            if author_email:
                msg["Reply-To"] = f"{author_name} <{author_email}>"

            safe_summary = summary or "Comprehensive strategic article drafted and ready for review."
            version_badge = f'<span style="background: #e0e7ff; color: #3730a3; padding: 4px 10px; border-radius: 6px; font-weight: 700; font-size: 13px;">Version {version_number}</span>'
            compare_hint = '<p style="color: #4f46e5; font-size: 13.5px; font-weight: 600; margin-top: 15px;">💡 Multiple versions exist for this draft. You can compare changes side-by-side on the review page.</p>' if has_multiple_versions else ''

            html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; margin: 0; padding: 24px; color: #1e293b; }}
        .container {{ max-width: 620px; margin: 0 auto; background: #ffffff; border-radius: 14px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.06); }}
        .header {{ background: linear-gradient(135deg, #1e40af, #2563eb); color: #ffffff; padding: 28px 32px; text-align: left; }}
        .header h1 {{ margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -0.4px; }}
        .header p {{ margin: 6px 0 0 0; font-size: 14px; opacity: 0.92; }}
        .content {{ padding: 32px; font-size: 15.5px; line-height: 1.65; color: #334155; }}
        .info-card {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 20px; margin: 24px 0; }}
        .info-row {{ margin-bottom: 12px; }}
        .info-label {{ font-size: 13px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }}
        .info-val {{ font-size: 16px; color: #0f172a; font-weight: 600; }}
        .summary-box {{ background: #ffffff; border-left: 4px solid #3b82f6; padding: 12px 16px; border-radius: 4px; font-size: 14.5px; color: #475569; font-style: italic; }}
        .btn-wrapper {{ text-align: center; margin: 34px 0 20px 0; }}
        .btn {{ display: inline-block; background-color: #2563eb; color: #ffffff !important; padding: 15px 36px; font-size: 15.5px; font-weight: 700; text-decoration: none; border-radius: 8px; box-shadow: 0 4px 14px rgba(37,99,235,0.35); }}
        .footer {{ background: #f8fafc; border-top: 1px solid #e2e8f0; padding: 18px 32px; text-align: center; font-size: 12.5px; color: #94a3b8; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{header_title}</h1>
            <p>{header_subtitle}</p>
        </div>
        <div class="content">
            <p>Hello Admin / Editor,</p>
            <p>A new blog article has been prepared for <strong>{company_name}</strong> and is awaiting your review before publication.</p>
            
            <div class="info-card">
                <div class="info-row">
                    <div class="info-label">Title</div>
                    <div class="info-val">{article_title}</div>
                </div>
                <div class="info-row">
                    <div class="info-label">Version</div>
                    <div style="margin-top: 4px;">{version_badge}</div>
                </div>
                <div class="info-row" style="margin-bottom: 0;">
                    <div class="info-label">Summary Excerpt</div>
                    <div class="summary-box">{safe_summary}</div>
                </div>
            </div>

            {compare_hint}

            <p style="margin-top: 24px;">Click the button below to review the post, make edits, approve publication to connected channels, or request AI regeneration:</p>

            <div class="btn-wrapper">
                <a href="{review_url}" class="btn" target="_blank">Review & Confirm Article &rarr;</a>
            </div>
        </div>
        <div class="footer">
            LeadAI Automated Publishing &bull; Multi-Channel Content Engine<br>
            This secure review link is valid for 7 days.
        </div>
    </div>
</body>
</html>
"""
            msg.attach(MIMEText(html_content, "html"))

            use_ssl = str(os.getenv("MAIL_SSL_TLS") or os.getenv("SMTP_USE_SSL", "false")).lower() in ("true", "1", "yes")
            if use_ssl:
                with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as server:
                    server.login(smtp_user, smtp_password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
                    if settings.smtp_use_tls or str(os.getenv("SMTP_STARTTLS", "true")).lower() in ("true", "1", "yes"):
                        server.starttls()
                    server.login(smtp_user, smtp_password)
                    server.send_message(msg)

            logger.info(f"[EmailService] Review email dispatched to {recipient_email} for \"{article_title}\"")
            return True

        except Exception as exc:
            logger.error(f"[EmailService] Failed to send email to {recipient_email}: {exc}")
            return False
