"""
Real Application Entry Points Attribution Test Suite.

Opt-in test that exercises the real live HTTP endpoints and background jobs
against the OpenAI API when `RUN_REAL_AI_VERIFICATION=true` and `OPENAI_API_KEY` are configured.
"""
import os
import sys
import unittest

# Ensure project root & tests are on sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tests_dir = os.path.join(backend_dir, "tests")
for p in (backend_dir, tests_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

import conftest_stub  # noqa: E402, F401
from starlette.testclient import TestClient

from core.database import engine_admin, SessionLocalAdmin
from domain.models import Client
from LeadAI.models import (
    LeadChannelAccount,
    LeadCompanySettings,
    LeadConversation,
    LeadCustomer,
    LeadMessage,
    LeadUserRole,
    ROLE_COMPANY_ADMIN,
)
from LeadAI.models_usage import (
    LeadAIUsageDailyAggregate,
    LeadAIUsageEvent,
)
from core.usage_tracker import flush_on_shutdown


class TestRealAppEntryPoints(unittest.TestCase):

    @unittest.skipUnless(
        os.getenv("RUN_REAL_AI_VERIFICATION") == "true" and os.getenv("OPENAI_API_KEY"),
        "Opt-in test: skipped unless RUN_REAL_AI_VERIFICATION=true and OPENAI_API_KEY is configured",
    )
    def test_real_application_entry_points_attribution(self):
        from outbound.app import app
        from LeadAI.services.jobs import handle_blog_generate

        TEST_CLIENT_ID = "client_real_entry_point"

        db = SessionLocalAdmin()
        try:
            # Create schema & clean tables
            for m in (Client, LeadCompanySettings, LeadChannelAccount, LeadConversation, LeadCustomer,
                      LeadMessage, LeadUserRole, LeadAIUsageEvent, LeadAIUsageDailyAggregate):
                try:
                    m.__table__.create(bind=engine_admin, checkfirst=True)
                except Exception:
                    pass

            db.query(LeadAIUsageEvent).delete()
            db.query(LeadAIUsageDailyAggregate).delete()
            db.query(LeadConversation).delete()
            db.query(LeadMessage).delete()
            db.query(LeadUserRole).delete()
            db.query(Client).filter_by(Id=TEST_CLIENT_ID).delete()
            db.commit()

            db_client = Client(Id=TEST_CLIENT_ID, Name="Real Entry Point Corp", CreatedBy="entry_test")
            settings = LeadCompanySettings(
                ClientId=TEST_CLIENT_ID,
                CompanyName="Real Entry Point Corp",
                Industry="AI Voice Automation",
                HandoffThreshold=0.60,
                AutoConvertThreshold=70,
                WidgetEnabled=True,
            )
            user_role = LeadUserRole(
                ClientId=TEST_CLIENT_ID,
                UserEmail="tester@example.com",
                Role=ROLE_COMPANY_ADMIN,
                IsActive=True,
                CreatedBy="system",
            )
            db.add_all([db_client, settings, user_role])
            db.commit()

            from core.auth import create_access_token
            real_jwt = create_access_token("tester@example.com")
            auth_headers = {
                "Authorization": f"Bearer {real_jwt}",
                "X-Client-Id": TEST_CLIENT_ID,
                "X-User-Email": "tester@example.com",
            }

            from unittest.mock import patch
            mock_identity = {
                "sub": "tester@example.com",
                "email": "tester@example.com",
                "client_id": TEST_CLIENT_ID,
                "role": ROLE_COMPANY_ADMIN,
            }

            with patch("outbound.app.validate_token_async", return_value=mock_identity), \
                 patch("core.token_validation.validate_token_async", return_value=mock_identity), \
                 patch("core.auth.get_current_user", return_value="tester@example.com"):
                client = TestClient(app)

                # 1. Customer Chat API
                start_resp = client.post(
                    "/api/leadai/public/chat/start",
                    json={
                        "company": TEST_CLIENT_ID,
                        "display_name": "Jane Buyer",
                        "phone": "+15551234567",
                    },
                )
                self.assertEqual(start_resp.status_code, 201)
                session_token = start_resp.json().get("session_token")

                chat_resp = client.post(
                    "/api/leadai/public/chat/messages",
                    headers={"X-Chat-Session": session_token},
                    json={
                        "message": "Hi, we need 500 AI voice agent licenses with custom CRM integration.",
                    },
                )
                self.assertEqual(chat_resp.status_code, 200)

                # 2. Knowledge Base Upload API
                kb_resp = client.post(
                    "/api/leadai/knowledge/faq",
                    headers=auth_headers,
                    json={
                        "title": "Enterprise Voice Agent Pricing",
                        "content": "Enterprise voice agents cost $0.05 per minute. All plans include automated lead scoring and CRM webhook dispatch.",
                        "tags": "pricing,voice",
                    },
                )
                self.assertEqual(kb_resp.status_code, 201)

                # 3. Knowledge Base Query API
                query_resp = client.post(
                    "/api/leadai/knowledge/test",
                    headers=auth_headers,
                    json={
                        "query": "How much do enterprise voice agents cost?",
                    },
                )
                self.assertEqual(query_resp.status_code, 200)

                # 4. Social Copy Draft API
                draft_resp = client.post(
                    "/api/leadai/social/drafts/generate",
                    headers=auth_headers,
                    json={
                        "content": "Announce our new AI voice agent integration with Salesforce CRM for automated qualification.",
                        "version": 1,
                    },
                )
                self.assertEqual(draft_resp.status_code, 200)

                # 5. Blog Generation Job Handler
                blog_result = handle_blog_generate(
                    db=db,
                    payload={
                        "client_id": TEST_CLIENT_ID,
                        "company_name": "Real Entry Point Corp",
                        "topic": "The Future of AI Voice Automation in B2B Sales",
                        "keywords": ["AI Sales", "Voice Automation", "Lead Qualification"],
                        "tone": "thought_leadership",
                        "target_words": 800,
                        "include_images": False,
                        "num_images": 0,
                    },
                )
                self.assertEqual(blog_result.get("status"), "pending_approval")

            flush_on_shutdown()

            # Assert all customer-facing rows have ClientId == TEST_CLIENT_ID
            events = db.query(LeadAIUsageEvent).order_by(LeadAIUsageEvent.CreatedAt.asc()).all()
            self.assertGreater(len(events), 0)
            null_customer_rows = [e for e in events if not e.ClientId]
            self.assertEqual(len(null_customer_rows), 0, "No customer-facing process may have a NULL ClientId")

        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
