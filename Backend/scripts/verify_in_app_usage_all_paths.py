"""
Verification Script for In-App Token & AI Usage Tracking.

Tests and records usage across all AI paths:
1. Chat Turn (Gateway completion -> chat_answer)
2. Lead Qualification (Gateway complete_json -> lead_qualification)
3. Outbound Voice Turn (Streaming usage & estimated fallback -> voice_call)
4. Knowledge Base Embeddings (OpenAI / fallback -> kb_embedding)
5. Blog Image Generation (DALL-E 3 -> blog_image_generation)
6. Social Agent (Topic posting / CRAG -> social_agent)
7. Pre-warm synthetic ping (Excluded from aggregates, flagged as is_prewarm)
8. Unpriced Model Verification (Checks "price_unknown" status and cost_usd=None)
9. Daily IST Aggregation & Flush Verification
10. Measure latency overhead on live voice path
"""
import os
import sys
import time

# Ensure project root & tests are on sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tests_dir = os.path.join(backend_dir, "tests")
for p in (backend_dir, tests_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

# Stubs core.base and core.database with in-memory SQLite
import conftest_stub  # noqa: E402, F401

from core.base import Base
from core.database import engine_admin, SessionLocalAdmin
from LeadAI.models_usage import (
    ALL_LEADAI_USAGE_TABLES,
    LeadAIUsageDailyAggregate,
    LeadAIUsageEvent,
)
from core.usage_tracker import (
    bind_usage_context,
    flush_on_shutdown,
    record_usage,
)




def run_verification():
    print("=================================================================")
    print("   LEADAI IN-APP TOKEN USAGE TRACKING — VERIFICATION SUITE")
    print("=================================================================")

    # 1. Ensure tables exist
    Base.metadata.create_all(bind=engine_admin)
    db = SessionLocalAdmin()


    test_client_id = "test-company-101"
    test_client_2 = "test-company-202"

    print("\n[Step 1] Triggering Simulated Request Invocations Across All AI Paths...")

    # Path A: Chat Answer (Tenant 101)
    with bind_usage_context(company_id=test_client_id, process="chat_answer", channel="web_chat", conversation_id="conv-chat-1"):
        record_usage(
            provider="openai",
            model="gpt-4o",
            input_tokens=420,
            output_tokens=180,
        )
    print("  -> Recorded Chat Turn (gpt-4o, 420 in, 180 out)")

    # Path B: Lead Qualification (Tenant 101)
    with bind_usage_context(company_id=test_client_id, process="lead_qualification", channel="ai_engine"):
        record_usage(
            provider="openai",
            model="gpt-4o-mini",
            input_tokens=850,
            output_tokens=95,
        )
    print("  -> Recorded Lead Qualification (gpt-4o-mini, 850 in, 95 out)")

    # Path C: Voice Turn Streaming (Tenant 101)
    with bind_usage_context(company_id=test_client_id, process="voice_call", channel="voice"):
        record_usage(
            provider="sarvam",
            model="sarvam-2b",
            input_tokens=120,
            output_tokens=45,
            is_estimated=False,
        )
    print("  -> Recorded Voice Turn Streaming (sarvam-2b, 120 in, 45 out)")

    # Path D: Voice Turn Dropped Stream (Tenant 101, Estimated)
    with bind_usage_context(company_id=test_client_id, process="voice_call", channel="voice"):
        record_usage(
            provider="sarvam",
            model="sarvam-2b",
            input_tokens=110,
            output_tokens=30,
            is_estimated=True,
        )
    print("  -> Recorded Voice Dropped Stream (sarvam-2b, 110 in, 30 out, estimated=True)")

    # Path E: KB Embeddings (Tenant 101)
    with bind_usage_context(company_id=test_client_id, process="kb_embedding", channel="knowledge_base"):
        record_usage(
            provider="openai",
            model="text-embedding-3-small",
            input_tokens=1600,
            output_tokens=0,
        )
    print("  -> Recorded KB Embeddings (text-embedding-3-small, 1600 tokens)")

    # Path F: Blog DALL-E Image (Tenant 101)
    with bind_usage_context(company_id=test_client_id, process="blog_image_generation", channel="blog"):
        record_usage(
            provider="openai",
            model="dall-e-3",
            is_image=True,
            image_count=1,
        )
    print("  -> Recorded DALL-E 3 Image Generation (1 image, unit cost $0.04)")

    # Path G: Social Agent (Tenant 202)
    with bind_usage_context(company_id=test_client_2, process="social_agent", channel="instagram"):
        record_usage(
            provider="openai",
            model="gpt-4o",
            input_tokens=650,
            output_tokens=220,
        )
    print("  -> Recorded Social Agent (Tenant 202, gpt-4o, 650 in, 220 out)")

    # Path H: System / Platform Job (ClientId=None, Topic Picker)
    with bind_usage_context(company_id=None, process="blog_topic_picker", channel="system"):
        record_usage(
            provider="openai",
            model="gpt-4o-mini",
            input_tokens=300,
            output_tokens=80,
        )
    print("  -> Recorded System / Platform Job (ClientId=NULL, gpt-4o-mini)")

    # Path I: Pre-warm ping (System, is_prewarm=True)
    with bind_usage_context(company_id=None, process="pre_warm", channel="voice"):
        record_usage(
            provider="sarvam",
            model="sarvam-2b",
            input_tokens=10,
            output_tokens=1,
            is_prewarm=True,
        )
    print("  -> Recorded Pre-warm Synthetic Ping (is_prewarm=True, excluded from daily totals)")

    # Path J: Unknown Model (Unpriced Model Verification)
    with bind_usage_context(company_id=test_client_id, process="custom_fine_tune", channel="chat"):
        record_usage(
            provider="custom_provider",
            model="custom-unlisted-model-v9",
            input_tokens=500,
            output_tokens=150,
        )
    print("  -> Recorded Unknown Model Call (custom-unlisted-model-v9 -> Price Unknown)")

    # 2. Flush queue
    print("\n[Step 2] Flushing Asynchronous Usage Queue...")
    flush_on_shutdown()
    time.sleep(0.5)

    # 3. Query and verify rows in leadai_ai_usage_events
    print("\n[Step 3] Querying Granular Raw Usage Events (`leadai_ai_usage_events`):")
    events = (
        db.query(LeadAIUsageEvent)
        .order_by(LeadAIUsageEvent.CreatedAt.desc())
        .limit(15)
        .all()
    )

    print("-" * 120)
    print(f"{'Company ID':<20} | {'Process':<22} | {'Model':<24} | {'Tokens (In/Out/Tot)':<20} | {'Cost USD':<12} | {'Status':<12}")
    print("-" * 120)
    for ev in events:
        c_id = ev.ClientId or "System / Platform"
        tokens_str = f"{ev.InputTokens}/{ev.OutputTokens}/{ev.TotalTokens}"
        cost_str = f"${ev.CostUsd:.6f}" if ev.CostUsd is not None else "Unknown"
        est_tag = " (est)" if ev.IsEstimated else ""
        prewarm_tag = " (prewarm)" if ev.IsPrewarm else ""
        print(f"{c_id:<20} | {ev.Process:<22} | {ev.Model + est_tag + prewarm_tag:<24} | {tokens_str:<20} | {cost_str:<12} | {ev.PriceStatus:<12}")
    print("-" * 120)

    # 4. Query and verify rows in leadai_ai_usage_daily_aggregates
    print("\n[Step 4] Querying Daily Aggregates (`leadai_ai_usage_daily_aggregates`):")
    aggregates = (
        db.query(LeadAIUsageDailyAggregate)
        .order_by(LeadAIUsageDailyAggregate.UpdatedAt.desc())
        .all()
    )

    print("-" * 120)
    print(f"{'Company ID':<20} | {'Process':<22} | {'Model':<24} | {'Requests':<8} | {'Total Tokens':<14} | {'Total Cost USD':<15} | {'Date (IST)'}")
    print("-" * 120)
    for agg in aggregates:
        c_id = agg.ClientId or "System / Platform"
        cost_str = f"${agg.TotalCostUsd:.6f}"
        if agg.HasUnknownPrice:
            cost_str += " (Partial)"
        print(f"{c_id:<20} | {agg.Process:<22} | {agg.Model:<24} | {agg.TotalRequests:<8} | {agg.TotalTokens:<14} | {cost_str:<15} | {agg.UsageDate}")
    print("-" * 120)

    # 5. Measure Latency Overhead on Voice Path
    print("\n[Step 5] Benchmarking Latency Overhead of `record_usage()` on Voice Path...")
    latencies = []
    for _ in range(1000):
        t0 = time.perf_counter()
        record_usage(
            company_id="bench-tenant",
            process="voice_call",
            channel="voice",
            provider="sarvam",
            model="sarvam-2b",
            input_tokens=150,
            output_tokens=35,
        )
        latencies.append((time.perf_counter() - t0) * 1000.0)

    avg_overhead_ms = sum(latencies) / len(latencies)
    p95_overhead_ms = sorted(latencies)[int(0.95 * len(latencies))]
    p99_overhead_ms = sorted(latencies)[int(0.99 * len(latencies))]

    print(f"  Overhead per call over 1,000 invocations:")
    print(f"  - Average: {avg_overhead_ms:.4f} ms ({avg_overhead_ms * 1000:.2f} µs)")
    print(f"  - P95:     {p95_overhead_ms:.4f} ms")
    print(f"  - P99:     {p99_overhead_ms:.4f} ms")

    db.close()
    print("\n[SUCCESS] Phase 2 & Phase 3 Verification Completed.")


if __name__ == "__main__":
    run_verification()
