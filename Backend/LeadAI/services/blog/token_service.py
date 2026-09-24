"""
Token Service for generating and verifying temporary signed review tokens.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import jwt

from ...config import settings

JWT_SECRET_KEY = getattr(settings, "jwt_secret", None) or os.getenv("JWT_SECRET_KEY") or "leadai_blog_review_secret_2026"
JWT_ALGORITHM = "HS256"
DEFAULT_EXPIRY_DAYS = 7



class TokenService:

    @staticmethod
    def create_review_token(
        article_id: str,
        client_id: str,
        reviewer_email: Optional[str] = None,
        expires_days: int = DEFAULT_EXPIRY_DAYS
    ) -> str:
        """Generates a signed JWT review token valid for 7 days."""
        now = datetime.now(timezone.utc)
        payload = {
            "sub": article_id,
            "article_id": article_id,
            "client_id": client_id,
            "reviewer_email": reviewer_email,
            "scope": "article_review",
            "exp": now + timedelta(days=expires_days),
            "iat": now,
        }
        return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

    @staticmethod
    def verify_review_token(token: str) -> Optional[Dict[str, Any]]:
        """Verifies review token and returns payload or None."""
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            if payload.get("scope") != "article_review":
                return None
            return payload
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            return None

    @classmethod
    def generate_review_url(
        cls,
        article_id: str,
        client_id: str,
        reviewer_email: Optional[str] = None
    ) -> str:
        """Constructs frontend review link with token."""
        frontend_url = (os.getenv("FRONTEND_APP_URL") or "http://localhost:4200").rstrip("/")
        token = cls.create_review_token(article_id=article_id, client_id=client_id, reviewer_email=reviewer_email)
        return f"{frontend_url}/review/{article_id}?token={token}"
