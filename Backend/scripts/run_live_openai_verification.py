"""
Live OpenAI Verification Script for In-App AI Token Usage Tracking.

Executes REAL calls to OpenAI's API using the configured OPENAI_API_KEY:
1. Real Chat Completion (chat_answer via gateway / conversation)
2. Real Blog Generation Node (blog_router via ChatOpenAI)
3. Real Knowledge Base Indexing & Query Embedding (via OpenAI embeddings API)

Dumps the EXACT raw database rows from `leadai_ai_usage_events` with real irregular token counts.
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

import conftest_stub  # noqa: E402, F401
from core.database import engine_admin, SessionLocalAdmin
from domain.models import Client
from LeadAI.models import LeadConversation, LeadMessage
from LeadAI.models_usage import LeadAIUsageEvent, LeadAIUsageDailyAggregate
from core.usage_tracker import bind_usage_context, flush_on_shutdown
from LeadAI.engine import gateway
from LeadAI.services.blog.router import router_node
from LeadAI.services.embeddings import embed, embed_one


def run_live_verification():
    print("==========================================================================")
    print("   LEADAI LIVE REAL OPENAI API TRAFFIC VERIFICATION")
    print("==========================================================================")

    # Ensure tables exist
    for model_cls in (Client, LeadConversation, LeadMessage, LeadAIUsageEvent, LeadAIUsageDailyAggregate):
        try:
            model_cls.__table__.create(bind=engine_admin, checkfirst=True)
        except Exception:
            pass

    db = SessionLocalAdmin()

    # Clear previous rows
    db.query(LeadAIUsageEvent).delete()
    db.query(LeadAIUsageDailyAggregate).delete()
    db.commit()

    REAL_TENANT_ID = "client_live_test_corp"
    db_client = Client(Id=REAL_TENANT_ID, Name="Live Test Corp", CreatedBy="live_test")
    db.add(db_client)
    db.commit()

    # ----------------------------------------------------------------------
    # 1. Real Chat Message
    # ----------------------------------------------------------------------
    print("\n1. Triggering Real Customer Chat Message via Gateway...")
    conv = LeadConversation(
        Id="conv_live_101",
        ClientId=REAL_TENANT_ID,
        CustomerId="cust_live_1",
        Channel="web_chat",
        Status="open",
        CreatedBy="customer",
    )
    db.add(conv)
    db.commit()

    with bind_usage_context(
        company_id=REAL_TENANT_ID,
        process="chat_answer",
        channel="web_chat",
        conversation_id=conv.Id,
    ):
        system_prompt = "You are a helpful sales assistant for an enterprise AI CRM platform."
        messages = [
            {"role": "user", "content": "Hi, what pricing plans do you offer for high-volume outbound calling?"}
        ]
        reply, meta = gateway.complete(
            system=system_prompt,
            messages=messages,
            profile="chat",
        )
        print(f"   [Reply Received ({meta.get('latency_ms')}ms)]: {reply[:120]}...")

    # ----------------------------------------------------------------------
    # 2. Real Blog Generation Node
    # ----------------------------------------------------------------------
    print("\n2. Triggering Real Blog Router Node via LangChain ChatOpenAI...")
    with bind_usage_context(
        company_id=REAL_TENANT_ID,
        process="blog_router",
        channel="blog",
    ):
        state_input = {
            "topic": "Scaling Multi-Agent Workflows in Real Estate Lead Qualification for 2026",
            "as_of": "2026-10-08",
        }
        decision = router_node(state_input)
        print(f"   [Blog Router Decision]: mode={decision.get('mode')}, needs_research={decision.get('needs_research')}, queries={decision.get('queries')}")

    # ----------------------------------------------------------------------
    # 3. Real Knowledge Base Document Indexing & Query Embedding
    # ----------------------------------------------------------------------
    print("\n3. Triggering Real Knowledge Base Index & Query Embeddings...")
    with bind_usage_context(
        company_id=REAL_TENANT_ID,
        process="kb_indexing_embedding",
        channel="knowledge_base",
    ):
        doc_chunks = [
            "LeadAI provides real-time omnichannel lead capture across WhatsApp, Instagram, Facebook, and Web.",
            "Our automated qualification engine uses LangGraph and custom AI gateway routing with sub-second latency.",
            "Enterprise compliance includes end-to-end PII masking, RBAC tenant isolation, and detailed token usage tracking."
        ]
        indexed_vectors, model_used = embed(doc_chunks)
        print(f"   [KB Indexed]: Embedded {len(indexed_vectors)} chunks (dim={len(indexed_vectors[0])}, model={model_used})")

    with bind_usage_context(
        company_id=REAL_TENANT_ID,
        process="kb_query_embedding",
        channel="knowledge_base",
    ):
        query_text = "How does LeadAI handle enterprise security and token usage tracking?"
        query_vector, model_used = embed_one(query_text)
        print(f"   [KB Queried]: Embedded query (dim={len(query_vector)}, model={model_used})")

    # Flush usage tracker queue
    flush_on_shutdown()
    time.sleep(0.5)

    # ----------------------------------------------------------------------
    # Query Database and Print Raw Records
    # ----------------------------------------------------------------------
    print("\n" + "=" * 130)
    print("   RAW DATABASE ROWS IN `leadai_ai_usage_events` (LIVE OPENAI CALLS)")
    print("=" * 130)
    rows = db.query(LeadAIUsageEvent).order_by(LeadAIUsageEvent.CreatedAt.asc()).all()

    print(f"{'ClientId':<24} | {'Process':<25} | {'Model':<24} | {'Tokens (In/Out/Tot)':<20} | {'Cost USD':<12} | {'Estimated'}")
    print("-" * 130)
    for r in rows:
        cid_display = r.ClientId if r.ClientId else "NULL"
        tok_str = f"{r.InputTokens}/{r.OutputTokens}/{r.TotalTokens}"
        cost_str = f"${r.CostUsd:.6f}" if r.CostUsd is not None else "Unknown"
        print(f"{cid_display:<24} | {r.Process:<25} | {r.Model:<24} | {tok_str:<20} | {cost_str:<12} | {str(r.IsEstimated):<10}")
    print("-" * 130)

    db.close()


if __name__ == "__main__":
    run_live_verification()
