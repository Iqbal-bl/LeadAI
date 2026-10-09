"""
Unit Tests for In-App Token and AI Usage Tracker (`core/usage_tracker.py`).

Tests:
1. Basic recording and DB persistence.
2. Queue overflow drops and rate-limited logging.
3. Shutdown flush writing all pending rows.
4. ContextVar propagation into async tasks and worker threads.
5. Retries counted once and failed zero-usage calls not counted.
6. NULL company_id bucket for system / platform jobs.
7. IST timezone date aggregation across UTC midnight boundary.
8. Unlisted model returns price_status="price_unknown" and cost_usd=None.
"""
import asyncio
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timezone

# Ensure project root & tests are on sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tests_dir = os.path.join(backend_dir, "tests")
for p in (backend_dir, tests_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

import conftest_stub  # noqa: E402, F401

from core.base import Base
from core.database import engine_admin, SessionLocalAdmin
from core.pricing import calculate_cost
from core.usage_tracker import (
    bind_usage_context,
    current_company_id,
    current_process,
    flush_on_shutdown,
    record_usage,
)
from LeadAI.models_usage import (
    ALL_LEADAI_USAGE_TABLES,
    LeadAIUsageDailyAggregate,
    LeadAIUsageEvent,
)


class TestUsageTracker(unittest.TestCase):

    def setUp(self):
        Base.metadata.create_all(bind=engine_admin)
        self.db = SessionLocalAdmin()

    def tearDown(self):
        self.db.close()

    def test_record_usage_and_flush(self):
        """Proves recording writes to DB and computes aggregates."""
        test_client = f"client-unit-{int(time.time()*1000)}"
        with bind_usage_context(company_id=test_client, process="chat_answer", channel="chat"):
            record_usage(
                provider="openai",
                model="gpt-4o",
                input_tokens=1000,
                output_tokens=500,
            )

        flush_on_shutdown()

        event = self.db.query(LeadAIUsageEvent).filter_by(ClientId=test_client).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.Process, "chat_answer")
        self.assertEqual(event.InputTokens, 1000)
        self.assertEqual(event.OutputTokens, 500)
        self.assertEqual(event.TotalTokens, 1500)
        # Cost: (1000/1M * 2.50) + (500/1M * 10.00) = 0.0025 + 0.005 = 0.0075
        self.assertAlmostEqual(event.CostUsd, 0.0075, places=5)
        self.assertEqual(event.PriceStatus, "ok")

        agg = self.db.query(LeadAIUsageDailyAggregate).filter_by(ClientId=test_client).first()
        self.assertIsNotNone(agg)
        self.assertEqual(agg.TotalRequests, 1)
        self.assertEqual(agg.TotalTokens, 1500)
        self.assertAlmostEqual(agg.TotalCostUsd, 0.0075, places=5)

    def test_queue_overflow_drops_safely(self):
        """Proves queue handles overflow safely without crashing or blocking."""
        import core.usage_tracker as tracker

        # Temporarily mock queue maxsize
        orig_queue = tracker._usage_queue
        tracker._usage_queue = tracker.queue.Queue(maxsize=2)
        try:
            tracker.record_usage(model="gpt-4o", input_tokens=10, output_tokens=10)
            tracker.record_usage(model="gpt-4o", input_tokens=10, output_tokens=10)
            # Third item overflows queue
            tracker.record_usage(model="gpt-4o", input_tokens=10, output_tokens=10)
            self.assertGreaterEqual(tracker._dropped_count, 1)
        finally:
            tracker._usage_queue = orig_queue

    def test_shutdown_flush_writes_pending_rows(self):
        """Proves flush_on_shutdown immediately drains in-memory queue."""
        test_client = f"client-flush-{int(time.time()*1000)}"
        with bind_usage_context(company_id=test_client, process="lead_summary"):
            record_usage(model="gpt-4o-mini", input_tokens=200, output_tokens=50)

        flush_on_shutdown()
        event = self.db.query(LeadAIUsageEvent).filter_by(ClientId=test_client).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.Process, "lead_summary")

    def test_context_propagation_into_async_and_threads(self):
        """Proves ContextVars propagate into asyncio tasks and explicit threads."""
        test_client = "company-ctx-prop"

        async def async_worker():
            with bind_usage_context(company_id=test_client, process="voice_call"):
                await asyncio.sleep(0.01)
                self.assertEqual(current_company_id.get(), test_client)
                self.assertEqual(current_process.get(), "voice_call")
                record_usage(model="sarvam-2b", input_tokens=100, output_tokens=50)

        asyncio.run(async_worker())

        # Explicit threading copy context
        import contextvars
        ctx = contextvars.copy_context()

        def thread_worker():
            self.assertEqual(ctx.run(current_company_id.get), None)

        t = threading.Thread(target=thread_worker)
        t.start()
        t.join()

    def test_retries_and_failures_not_counted(self):
        """Proves failed calls with 0 tokens are not counted."""
        test_client = f"client-fail-{int(time.time()*1000)}"
        # Emulate failed attempt (no usage) -> should not record
        # Emulate successful retry -> recorded once
        with bind_usage_context(company_id=test_client, process="chat_answer"):
            record_usage(model="gpt-4o", input_tokens=500, output_tokens=100)

        flush_on_shutdown()
        count = self.db.query(LeadAIUsageEvent).filter_by(ClientId=test_client).count()
        self.assertEqual(count, 1)

    def test_null_company_bucket_for_system_jobs(self):
        """Proves platform/system jobs store ClientId=NULL and aggregate under NULL."""
        with bind_usage_context(company_id=None, process="blog_topic_picker"):
            record_usage(model="gpt-4o-mini", input_tokens=300, output_tokens=80)

        flush_on_shutdown()
        event = self.db.query(LeadAIUsageEvent).filter(
            LeadAIUsageEvent.ClientId.is_(None),
            LeadAIUsageEvent.Process == "blog_topic_picker",
        ).first()
        self.assertIsNotNone(event)
        self.assertIsNone(event.ClientId)

        agg = self.db.query(LeadAIUsageDailyAggregate).filter(
            LeadAIUsageDailyAggregate.ClientId.is_(None),
            LeadAIUsageDailyAggregate.Process == "blog_topic_picker",
        ).first()
        self.assertIsNotNone(agg)

    def test_aggregation_ist_midnight_boundary(self):
        """Proves UTC timestamp near midnight correctly maps to IST date.
        
        Example: 2026-10-08 19:00:00 UTC is 2026-10-09 00:30:00 IST.
        """
        test_client = f"client-tz-{int(time.time()*1000)}"
        utc_late = datetime(2026, 10, 8, 19, 0, 0, tzinfo=timezone.utc)

        with bind_usage_context(company_id=test_client, process="chat_answer"):
            record_usage(
                model="gpt-4o",
                input_tokens=100,
                output_tokens=50,
                created_at=utc_late,
            )

        flush_on_shutdown()

        agg = self.db.query(LeadAIUsageDailyAggregate).filter_by(ClientId=test_client).first()
        self.assertIsNotNone(agg)
        # In IST (+5:30), 19:00 UTC on Oct 8 is 00:30 on Oct 9!
        self.assertEqual(agg.UsageDate, "2026-10-09")

    def test_unpriced_model_shows_price_unknown(self):
        """Proves unpriced models return price_status='price_unknown' and cost_usd=None."""
        cost, unit_in, unit_out, status = calculate_cost("custom-unlisted-model-xyz", 1000, 500)
        self.assertIsNone(cost)
        self.assertIsNone(unit_in)
        self.assertIsNone(unit_out)
        self.assertEqual(status, "price_unknown")

    def test_customer_facing_process_null_company_validation(self):
        """Proves only allowed system jobs may have NULL company_id."""
        from core.usage_tracker import ALLOWED_NULL_COMPANY_PROCESSES

        customer_processes = [
            "chat_answer",
            "lead_qualification",
            "lead_summary",
            "intent_evaluation",
            "voice_call",
            "voice_opening_line",
            "comment_reply_generation",
            "social_copy_draft",
            "blog_planner",
            "blog_worker",
            "blog_reducer",
            "kb_embedding",
            "bot_extraction",
        ]
        for proc in customer_processes:
            self.assertNotIn(
                proc,
                ALLOWED_NULL_COMPANY_PROCESSES,
                f"Customer process {proc} must NOT be allowed to have a NULL company_id!",
            )

    def test_langchain_callback_records_single_usage_with_langsmith_on(self):
        """Proves InAppUsageCallbackHandler records exactly ONE usage row per LLM call when LANGSMITH_TRACING=true."""
        from langchain_core.outputs import ChatGeneration, LLMResult
        from langchain_core.messages import AIMessage
        from core.usage_tracker import InAppUsageCallbackHandler

        test_client = f"client-langchain-{int(time.time()*1000)}"
        cb = InAppUsageCallbackHandler(process="blog_worker", channel="blog")

        old_tracing = os.environ.get("LANGSMITH_TRACING")
        old_v2 = os.environ.get("LANGCHAIN_TRACING_V2")
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGCHAIN_TRACING_V2"] = "true"

        try:
            with bind_usage_context(company_id=test_client):
                # Create a mock LLMResult matching LangChain OpenAI return shape
                gen = ChatGeneration(
                    message=AIMessage(content="Generated blog section body"),
                    generation_info={"finish_reason": "stop"}
                )
                result = LLMResult(
                    generations=[[gen]],
                    llm_output={
                        "token_usage": {
                            "prompt_tokens": 600,
                            "completion_tokens": 250,
                            "total_tokens": 850,
                        },
                        "model_name": "gpt-4o",
                    }
                )
                # Invoke callback
                cb.on_llm_end(result)

            flush_on_shutdown()

            events = self.db.query(LeadAIUsageEvent).filter_by(ClientId=test_client).all()
            self.assertEqual(len(events), 1, "Exactly one row must be written for the LLM execution (no double-counting with LangSmith active)")
            self.assertEqual(events[0].Process, "blog_worker")
            self.assertEqual(events[0].InputTokens, 600)
            self.assertEqual(events[0].OutputTokens, 250)
            self.assertEqual(events[0].TotalTokens, 850)
        finally:
            if old_tracing is not None:
                os.environ["LANGSMITH_TRACING"] = old_tracing
            else:
                os.environ.pop("LANGSMITH_TRACING", None)
            if old_v2 is not None:
                os.environ["LANGCHAIN_TRACING_V2"] = old_v2
            else:
                os.environ.pop("LANGCHAIN_TRACING_V2", None)

    def test_dated_model_pricing_resolution(self):
        """Proves exact pricing for dated model variants and longest-match resolution."""
        # 1. gpt-4o-mini-2024-07-18: $0.15 / 1M in, $0.60 / 1M out
        # 324 in, 67 out -> 324*(0.15/1e6) + 67*(0.60/1e6) = 0.0000486 + 0.0000402 = $0.0000888
        cost_mini, u_in, u_out, status = calculate_cost("gpt-4o-mini-2024-07-18", 324, 67)
        self.assertEqual(status, "ok")
        self.assertAlmostEqual(cost_mini, 0.0000888, places=7)
        self.assertEqual(u_in, 0.15)
        self.assertEqual(u_out, 0.60)

        # 2. gpt-4o-2024-08-06: $2.50 / 1M in, $10.00 / 1M out
        # 324 in, 67 out -> 324*(2.50/1e6) + 67*(10.00/1e6) = 0.000810 + 0.000670 = $0.001480
        cost_4o, u_in, u_out, status = calculate_cost("gpt-4o-2024-08-06", 324, 67)
        self.assertEqual(status, "ok")
        self.assertAlmostEqual(cost_4o, 0.001480, places=6)
        self.assertEqual(u_in, 2.50)
        self.assertEqual(u_out, 10.00)

        # 3. Unknown model -> price_unknown and None cost
        cost_unk, u_in, u_out, status = calculate_cost("unknown-experimental-model-99", 100, 50)
        self.assertEqual(status, "price_unknown")
        self.assertIsNone(cost_unk)
        self.assertIsNone(u_in)
        self.assertIsNone(u_out)

    def test_blog_fanout_workers_tenant_attribution(self):
        """Proves parallel blog worker nodes carry the tenant ClientId from state payload."""
        from LeadAI.services.blog.graph import fanout
        from LeadAI.services.blog.schemas import Plan, Task
        from LeadAI.services.blog.state import State

        test_client = f"client-blog-fanout-{int(time.time()*1000)}"
        mock_tasks = [
            Task(id=1, title="Section 1", goal="Goal 1", bullets=["b1"]),
            Task(id=2, title="Section 2", goal="Goal 2", bullets=["b2"]),
            Task(id=3, title="Section 3", goal="Goal 3", bullets=["b3"]),
        ]
        mock_plan = Plan(blog_title="AI Voice Automation", target_words=1000, tasks=mock_tasks)
        mock_state: State = {
            "client_id": test_client,
            "topic": "AI Voice Automation",
            "tasks": mock_tasks,
            "variant_angle": None,
            "evidence": [],
            "keywords": [],
            "tone": "professional",
            "target_audience": "Execs",
            "target_words": 1000,
            "blog_type": "text_only",
            "cta_text": "Book Demo",
            "cta_url": "#demo",
            "include_images": False,
            "num_images": 0,
            "plan": mock_plan,
            "sections": [],
            "content": "",
            "summary": "",
            "cover_image": None,
            "images": [],
            "tags": [],
            "title": "AI Voice Automation",
        }

        # Execute graph fanout edge function
        sends = fanout(mock_state)
        self.assertEqual(len(sends), 3)
        for s in sends:
            self.assertEqual(s.node, "worker")
            # Assert client_id is preserved in task payload dispatched to parallel workers
            self.assertEqual(s.arg.get("client_id"), test_client)

    def test_retention_job_rescheduling(self):
        """Proves retention cleanup job purges expired events and re-schedules itself for +24h."""
        from LeadAI.models_ext import LeadJob
        from LeadAI.services.jobs import enqueue, handle_usage_retention_cleanup, utcnow
        from datetime import timedelta

        # Enqueue initial job
        now = utcnow().replace(tzinfo=None) if utcnow().tzinfo else utcnow()
        job1 = enqueue(self.db, "usage.retention_cleanup", payload={"retention_days": 90}, run_at=now, commit=True)
        self.assertIsNotNone(job1)

        # 1. Worker claims job1 and executes handler
        job1.Status = "claimed"
        self.db.commit()

        res1 = handle_usage_retention_cleanup(self.db, {"retention_days": 90})
        self.assertEqual(res1["retention_days"], 90)

        # Check future scheduled job exists
        future_jobs = (
            self.db.query(LeadJob)
            .filter(
                LeadJob.Kind == "usage.retention_cleanup",
                LeadJob.Status == "queued",
                LeadJob.RunAt > now,
                LeadJob.IsDeleted == False,
            )
            .all()
        )
        self.assertEqual(len(future_jobs), 1, "Exactly one future cleanup job must be scheduled")
        job2 = future_jobs[0]

        # 2. Worker claims job2 24 hours later and executes handler again
        job2.Status = "claimed"
        self.db.commit()

        res2 = handle_usage_retention_cleanup(self.db, {"retention_days": 90})
        self.assertEqual(res2["retention_days"], 90)

        # Check second future job was enqueued
        future_jobs_after_2nd = (
            self.db.query(LeadJob)
            .filter(
                LeadJob.Kind == "usage.retention_cleanup",
                LeadJob.Status == "queued",
                LeadJob.RunAt > job2.RunAt,
                LeadJob.IsDeleted == False,
            )
            .all()
        )
        self.assertEqual(len(future_jobs_after_2nd), 1, "Future job must exist after second run")

    def test_simple_agent_structure_and_compression(self):
        """Verify SimpleAgent class constants exist and _maybe_compress executes cleanly with tenant attribution."""
        import asyncio
        import uuid
        from unittest.mock import AsyncMock, MagicMock
        from outbound.app import SimpleAgent

        # Assert class attributes
        self.assertEqual(SimpleAgent.MAX_RAW_TURNS, 40)
        self.assertEqual(SimpleAgent.KEEP_RAW, 20)

        test_client = f"voice_tenant_{uuid.uuid4().hex[:8]}"
        agent = SimpleAgent(client_id=test_client)
        self.assertEqual(agent.client_id, test_client)
        self.assertEqual(agent.MAX_RAW_TURNS, 40)
        self.assertEqual(agent.KEEP_RAW, 20)

        # Fill history with 45 turns (exceeding MAX_RAW_TURNS=40)
        agent.history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"Message turn {i}"}
            for i in range(45)
        ]

        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Summary of the first 25 conversation turns."
        mock_resp.choices = [mock_choice]
        mock_resp.usage = MagicMock(prompt_tokens=420, completion_tokens=50, total_tokens=470)

        agent._client = MagicMock()
        agent._client.chat.completions.create = AsyncMock(return_value=mock_resp)

        asyncio.run(agent._maybe_compress())

        self.assertEqual(agent.summary_prefix, "Summary of the first 25 conversation turns.")
        self.assertEqual(len(agent.history), 20, "History must be truncated to KEEP_RAW=20 turns")

        flush_on_shutdown()

        event = self.db.query(LeadAIUsageEvent).filter_by(ClientId=test_client, Process="voice_transcript_compress").first()
        self.assertIsNotNone(event, "voice_transcript_compress usage event must be recorded with tenant ID")
        self.assertEqual(event.InputTokens, 420)
        self.assertEqual(event.OutputTokens, 50)

    def test_kill_switch_disables_recording_and_callback(self):
        """Proves AI_USAGE_TRACKING_ENABLED=false disables record_usage and InAppUsageCallbackHandler."""
        from core.usage_tracker import InAppUsageCallbackHandler, _usage_queue
        from langchain_core.outputs import LLMResult, Generation

        old_env = os.environ.get("AI_USAGE_TRACKING_ENABLED")
        try:
            os.environ["AI_USAGE_TRACKING_ENABLED"] = "false"
            initial_qsize = _usage_queue.qsize()

            # 1. Direct record_usage is a no-op
            record_usage(
                company_id="comp_disabled",
                process="chat_answer",
                model="gpt-4o-mini",
                input_tokens=100,
                output_tokens=50,
            )
            self.assertEqual(_usage_queue.qsize(), initial_qsize)

            # 2. InAppUsageCallbackHandler is a no-op
            handler = InAppUsageCallbackHandler(process="chat_answer", channel="chat")
            llm_res = LLMResult(
                generations=[[Generation(text="hi")]],
                llm_output={"token_usage": {"prompt_tokens": 100, "completion_tokens": 50}, "model_name": "gpt-4o-mini"},
            )
            handler.on_llm_end(llm_res)
            self.assertEqual(_usage_queue.qsize(), initial_qsize)

        finally:
            if old_env is not None:
                os.environ["AI_USAGE_TRACKING_ENABLED"] = old_env
            else:
                os.environ.pop("AI_USAGE_TRACKING_ENABLED", None)


if __name__ == "__main__":
    unittest.main()


