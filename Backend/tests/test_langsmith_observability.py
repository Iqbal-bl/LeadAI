"""
Tests for LangSmith Observability, Token Utilization, Cost Tracking, and PII Redaction.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

import conftest_stub  # noqa: F401
import core.observability as obs
from LeadAI.engine import gateway
from LeadAI.services import embeddings


class _FakeSettings:
    llm_enabled = True
    openai_api_key = "test-key"
    openai_base_url = "http://fake"
    openai_model = "gpt-4o-mini"
    openai_embed_model = "text-embedding-3-small"
    openai_timeout = 5.0


class TestLangSmithObservability(unittest.TestCase):

    def test_pii_redaction_text(self):
        sample = (
            "Contact user Kabir at kabir@kestrelhomes.in or +91 9876543210. "
            "Auth header: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-ID "
            "PAN: ABCDE1234F, Aadhaar: 1234 5678 9012, API Key: sk-12345678901234567890"
        )
        masked = obs.mask_text(sample)
        self.assertNotIn("kabir@kestrelhomes.in", masked)
        self.assertNotIn("9876543210", masked)
        self.assertNotIn("ABCDE1234F", masked)
        self.assertNotIn("1234 5678 9012", masked)
        self.assertNotIn("sk-12345678901234567890", masked)
        self.assertIn("[EMAIL_MASKED]", masked)
        self.assertIn("[PHONE_MASKED]", masked)
        self.assertIn("[REDACTED_PAN]", masked)
        self.assertIn("[REDACTED_AADHAAR]", masked)

    def test_pii_redaction_obj(self):
        data = {
            "customer_name": "Rohan Sharma",
            "email": "rohan@example.com",
            "phone": "+91 9811122233",
            "password": "supersecretpassword",
            "messages": [
                {"role": "user", "content": "Call me on 9811122233 or email me at rohan@example.com"}
            ]
        }
        masked = obs.mask_pii_obj(data)
        self.assertEqual(masked["password"], "[REDACTED]")
        self.assertEqual(masked["email"], "[EMAIL_MASKED]")
        self.assertEqual(masked["phone"], "[PHONE_MASKED]")
        self.assertNotIn("rohan@example.com", masked["messages"][0]["content"])
        self.assertNotIn("9811122233", masked["messages"][0]["content"])

    def test_sarvam_cost_calculation(self):
        # Default pricing: $0.10 / 1M input, $0.20 / 1M output
        prompt_tokens = 10_000
        completion_tokens = 2_000
        cost = obs.calculate_sarvam_cost(prompt_tokens, completion_tokens)
        # Expected: (10000/1e6)*0.10 + (2000/1e6)*0.20 = 0.001 + 0.0004 = 0.0014
        self.assertAlmostEqual(cost, 0.0014, places=6)

    def test_tracing_disabled_safety(self):
        # With tracing disabled, functions run normally and never raise
        @obs.traceable(name="test_func")
        def sample_function(x, y):
            return x + y

        res = sample_function(3, 4)
        self.assertEqual(res, 7)

    def test_gateway_complete_records_tokens(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Test reply"}}],
            "usage": {"prompt_tokens": 150, "completion_tokens": 40, "total_tokens": 190}
        }
        mock_resp.raise_for_status = MagicMock()

        fake_settings = _FakeSettings()

        with patch.object(gateway, "_post", return_value=mock_resp), \
             patch.object(gateway, "settings", fake_settings), \
             patch("LeadAI.engine.gateway.record_run_metadata") as mock_record:

            reply, meta = gateway.complete("You are a bot", [{"role": "user", "content": "Hi"}])
            self.assertEqual(reply, "Test reply")
            self.assertEqual(meta["prompt_tokens"], 150)
            self.assertEqual(meta["completion_tokens"], 40)
            mock_record.assert_called_once()
            call_kwargs = mock_record.call_args.kwargs
            self.assertEqual(call_kwargs["prompt_tokens"], 150)
            self.assertEqual(call_kwargs["completion_tokens"], 40)
            self.assertEqual(call_kwargs["model"], "gpt-4o-mini")
            self.assertEqual(call_kwargs["provider"], "openai")

    def test_embeddings_records_tokens(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [{"embedding": [0.1, 0.2], "index": 0}],
            "usage": {"prompt_tokens": 25, "total_tokens": 25}
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        fake_settings = _FakeSettings()

        with patch("LeadAI.engine.gateway.shared_client", return_value=mock_client), \
             patch.object(embeddings, "settings", fake_settings), \
             patch("LeadAI.services.embeddings.record_run_metadata") as mock_record:

            vectors, model = embeddings.embed(["Search query"])
            self.assertEqual(len(vectors), 1)
            self.assertEqual(model, "text-embedding-3-small")
            mock_record.assert_called_once()
            call_kwargs = mock_record.call_args.kwargs
            self.assertEqual(call_kwargs["prompt_tokens"], 25)
            self.assertEqual(call_kwargs["total_tokens"], 25)


if __name__ == "__main__":
    unittest.main()
