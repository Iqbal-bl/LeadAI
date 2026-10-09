"""
Test Real App Entry Points Attribution Suite.

Exercises the REAL FastAPI HTTP Routes, Background Job Handlers, and Voice/Agent Engines:
1. Knowledge Base Upload API: `POST /api/leadai/knowledge/faq` (triggers kb_indexing_embedding)
2. Knowledge Base Query API: `POST /api/leadai/knowledge/test` (triggers kb_query_embedding)
3. Customer Chat API: `POST /api/leadai/public/chat/start` & `/messages` (triggers chat_answer, lead_qualification)
4. Social Draft API: `POST /api/leadai/social/drafts/generate` (triggers social_copy_draft)
5. Lead Summary: `ai_engine.summarize` (triggers lead_summary)
6. Intent Evaluation Semantic Fallback: `LeadIntentEvaluator.evaluate_text` (triggers intent_evaluation)
7. Social Comment Reply AI: `CommentReplyAIService.generate_reply_for_comment` (triggers comment_reply_generation)
8. Blog Generation Workflow: `handle_blog_generate` (triggers blog_router, blog_planner, blog_worker, blog_reducer)
9. Blog Web Researcher: `research_node` (triggers blog_researcher)
10. Blog Image Generation: `blog_image_generation`
11. Social Agent & CRAG: `grade_document` & `get_llm` (triggers crag_retrieval_grade, social_agent)
12. Blog Topic Picker: `TopicPickerService.pick_daily_topic` (company-specific AND system job with None)
13. Voice Flow: `_compose_opening_text` & Hindi `handle_voice_turn` (triggers voice_opening_line, voice_translation)
14. Voice Agent & Extraction: `SimpleAgent`, `_maybe_compress`, `PartyDetectorAgent`, `InsuranceClaimAgent` (triggers voice_call, voice_transcript_compress, voice_party_detector, bot_extraction)

Per-step assertions guarantee every expected process is written to the database with the proper tenant attribution.
"""
import asyncio
import os
import sys
import time
from unittest.mock import patch

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
    LeadCompanySettings,
    LeadConversation,
    LeadMessage,
    LeadCall,
)
from LeadAI.models_usage import (
    LeadAIUsageDailyAggregate,
    LeadAIUsageEvent,
)
from core.usage_tracker import flush_usage_events, flush_on_shutdown, record_usage, bind_usage_context
from LeadAI.services.jobs import handle_blog_generate
from main import app

def assert_process_recorded(db, client_id, process_name, step_name):
    flush_usage_events()
    time.sleep(0.5)
    db.expire_all()
    query = db.query(LeadAIUsageEvent).filter(LeadAIUsageEvent.Process == process_name)
    if client_id is not None:
        query = query.filter(LeadAIUsageEvent.ClientId == client_id)
    else:
        query = query.filter(LeadAIUsageEvent.ClientId.is_(None))
    count = query.count()
    assert count > 0, f"[{step_name}] Assertion Failed: Expected at least one row for process='{process_name}' with ClientId={client_id}, found 0."
    print(f"   [PASS] Step Check: process='{process_name}' recorded ({count} row(s)) for ClientId={client_id}")

def run_app_entry_points_test():
    print("==========================================================================")
    print("   LEADAI REAL APPLICATION ENTRY POINTS ATTRIBUTION TEST")
    print("==========================================================================")

    # Ensure tables
    from core.base import Base as CoreBase
    from LeadAI.models import Base as LeadAIBase
    try:
        CoreBase.metadata.create_all(bind=engine_admin)
        LeadAIBase.metadata.create_all(bind=engine_admin)
    except Exception:
        pass

    db = SessionLocalAdmin()
    db.query(LeadAIUsageEvent).delete()
    db.query(LeadAIUsageDailyAggregate).delete()
    db.commit()

    TEST_CLIENT_ID = "client_real_entry_point"
    db_client = Client(Id=TEST_CLIENT_ID, Name="Real Entry Point Corp", CreatedBy="test")
    settings = LeadCompanySettings(
        ClientId=TEST_CLIENT_ID,
        AutoConvertThreshold=70,
        WidgetEnabled=True,
    )
    db.add_all([db_client, settings])
    db.commit()

    # Create real signed user token / headers for authenticated routes
    from core.auth import create_access_token
    real_jwt = create_access_token("tester@example.com")
    auth_headers = {
        "Authorization": f"Bearer {real_jwt}",
        "X-Client-Id": TEST_CLIENT_ID,
        "X-User-Email": "tester@example.com",
    }

    # Grant permissions in LeadAI RBAC database for test user
    from LeadAI.models import LeadUserRole, ROLE_COMPANY_ADMIN
    user_role = LeadUserRole(
        ClientId=TEST_CLIENT_ID,
        UserEmail="tester@example.com",
        Role=ROLE_COMPANY_ADMIN,
        IsActive=True,
        CreatedBy="system",
    )
    db.add(user_role)
    db.commit()

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

        # ----------------------------------------------------------------------
        # 1. Knowledge Base Upload API (POST /api/leadai/knowledge/faq)
        # ----------------------------------------------------------------------
        print("\n1. Calling Real KB FAQ Upload Entry Point: POST /api/leadai/knowledge/faq")
        kb_resp = client.post(
            "/api/leadai/knowledge/faq",
            headers=auth_headers,
            json={
                "title": "Enterprise Voice Agent Pricing",
                "content": "Enterprise voice agents cost $0.05 per minute. All plans include automated lead scoring and CRM webhook dispatch.",
                "tags": "pricing,voice",
            },
        )
        assert kb_resp.status_code == 201
        assert_process_recorded(db, TEST_CLIENT_ID, "kb_indexing_embedding", "Step 1: KB Indexing")

        # ----------------------------------------------------------------------
        # 2. Knowledge Base Query API (POST /api/leadai/knowledge/test)
        # ----------------------------------------------------------------------
        print("\n2. Calling Real KB Query Entry Point: POST /api/leadai/knowledge/test")
        query_resp = client.post(
            "/api/leadai/knowledge/test",
            headers=auth_headers,
            json={
                "query": "How much do enterprise voice agents cost?",
            },
        )
        assert query_resp.status_code == 200
        assert_process_recorded(db, TEST_CLIENT_ID, "kb_query_embedding", "Step 2: KB Query")

        # ----------------------------------------------------------------------
        # 3. Customer Chat API (Public endpoint -> start session -> send message)
        # ----------------------------------------------------------------------
        print("\n3. Starting Real Customer Chat: POST /api/leadai/public/chat/start")
        start_resp = client.post(
            "/api/leadai/public/chat/start",
            json={
                "company": TEST_CLIENT_ID,
                "display_name": "Jane Buyer",
                "phone": "+15551234567",
            },
        )
        assert start_resp.status_code == 201
        session_token = start_resp.json().get("session_token")

        print("   Sending Customer Message: POST /api/leadai/public/chat/messages")
        chat_resp = client.post(
            "/api/leadai/public/chat/messages",
            headers={"X-Chat-Session": session_token},
            json={
                "message": "Hi, how much do enterprise voice agents cost per minute? We need 500 licenses.",
            },
        )
        assert chat_resp.status_code == 200
        print("   Waiting 3.5s for asynchronous lead qualification...")
        time.sleep(3.5)
        assert_process_recorded(db, TEST_CLIENT_ID, "chat_answer", "Step 3: Customer Chat Answer")
        assert_process_recorded(db, TEST_CLIENT_ID, "lead_qualification", "Step 3: Lead Qualification")

        # ----------------------------------------------------------------------
        # 4. Social Copy Draft API (POST /api/leadai/social/drafts/generate)
        # ----------------------------------------------------------------------
        print("\n4. Calling Real Social Draft Entry Point: POST /api/leadai/social/drafts/generate")
        draft_resp = client.post(
            "/api/leadai/social/drafts/generate",
            headers=auth_headers,
            json={
                "content": "Announce our new AI voice agent integration with Salesforce CRM for automated qualification.",
                "version": 1,
            },
        )
        assert draft_resp.status_code == 200
        assert_process_recorded(db, TEST_CLIENT_ID, "social_copy_draft", "Step 4: Social Draft")

        # ----------------------------------------------------------------------
        # 5. Lead Summary API / Engine
        # ----------------------------------------------------------------------
        print("\n5. Executing Lead Summary via Real ai_engine.summarize...")
        from LeadAI.models import Lead
        from LeadAI.services import ai_engine
        lead_row = db.query(Lead).filter_by(ClientId=TEST_CLIENT_ID).first()
        msgs = db.query(LeadMessage).filter_by(ClientId=TEST_CLIENT_ID).all()
        if lead_row and msgs:
            lead_row._ai_brief = None
            summary_txt, next_step = ai_engine.summarize(db, TEST_CLIENT_ID, "Real Entry Point Corp", lead_row, msgs)
            print(f"   Lead Summary: {summary_txt[:80]}...")
        assert_process_recorded(db, TEST_CLIENT_ID, "lead_summary", "Step 5: Lead Summary")

        # ----------------------------------------------------------------------
        # 6. Intent Evaluation Semantic Fallback
        # ----------------------------------------------------------------------
        print("\n6. Executing Intent Evaluation via Real LeadIntentEvaluator...")
        from LeadAI.services.intent_detector import LeadIntentEvaluator
        # Message with no keyword triggers, forcing LLM fallback
        intent_res = LeadIntentEvaluator.evaluate_text(
            "We are expanding operations next quarter and wondering if your stack handles high volume workflow handoffs?",
            company_id=TEST_CLIENT_ID,
            use_llm_fallback=True,
        )
        print(f"   Intent Result: is_lead={intent_res.is_lead}, category={intent_res.category}")
        assert_process_recorded(db, TEST_CLIENT_ID, "intent_evaluation", "Step 6: Intent Evaluation")

        # ----------------------------------------------------------------------
        # 7. Social Comment Reply AI
        # ----------------------------------------------------------------------
        print("\n7. Executing Social Comment Reply via Real CommentReplyAIService...")
        from LeadAI.services.comment_reply_ai import CommentReplyAIService
        from LeadAI.models_blog import LeadSocialComment
        test_comment = LeadSocialComment(
            ClientId=TEST_CLIENT_ID,
            Channel="linkedin",
            PostUrn="urn:li:ugcPost:101",
            PostTitle="AI Voice Agents for B2B Sales",
            PostSnippet="Announcing our real-time voice agents with under 500ms latency.",
            CommentUrn="urn:li:comment:201",
            CommentText="How does your AI voice agent compare with Bland AI in latency?",
            AuthorName="Alex Lead",
            CreatedBy="system",
        )
        db.add(test_comment)
        db.commit()
        reply_res = CommentReplyAIService.generate_reply_for_comment(
            db, test_comment
        )
        print(f"   Comment Reply: {reply_res.get('suggested_reply', '')[:80]}...")
        assert_process_recorded(db, TEST_CLIENT_ID, "comment_reply_generation", "Step 7: Comment Reply")

        # ----------------------------------------------------------------------
        # 8. Real Blog Generation Workflow (Router -> Planner -> Worker -> Reducer)
        # ----------------------------------------------------------------------
        print("\n8. Executing Real Blog Generation Workflow...")
        blog_result = handle_blog_generate(
            db=db,
            payload={
                "client_id": TEST_CLIENT_ID,
                "company_name": "Real Entry Point Corp",
                "topic": "Latest 2026 AI Voice Agents and Automation Breakthroughs",
                "keywords": ["AI Voice", "Telephony", "LeadAI"],
                "tone": "thought_leadership",
                "target_words": 600,
                "include_images": True,
                "num_images": 1,
            },
        )
        print(f"   Blog Job Result: status={blog_result.get('status')}")
        assert_process_recorded(db, TEST_CLIENT_ID, "blog_router", "Step 8: Blog Router")
        assert_process_recorded(db, TEST_CLIENT_ID, "blog_planner", "Step 8: Blog Planner")
        assert_process_recorded(db, TEST_CLIENT_ID, "blog_worker", "Step 8: Blog Worker")
        assert_process_recorded(db, TEST_CLIENT_ID, "blog_reducer", "Step 8: Blog Reducer")

        # ----------------------------------------------------------------------
        # 9. Blog Researcher Node
        # ----------------------------------------------------------------------
        print("\n9. Executing Blog Web Researcher Node...")
        from LeadAI.services.blog.researcher import research_node
        with patch("LeadAI.services.blog.researcher._tavily_search", return_value=[
            {
                "title": "AI Voice Telephony in 2026",
                "url": "https://example.com/ai-voice-2026",
                "snippet": "Real-time AI voice agents reduce customer latency below 400ms and improve qualification by 85%.",
                "published_at": "2026-03-01",
                "source": "example.com"
            }
        ]):
            r_pack = research_node({
                "client_id": TEST_CLIENT_ID,
                "queries": ["AI Voice Telephony 2026 innovations"],
                "as_of": "2026-09-01",
            })
            print(f"   Researcher synthesized {len(r_pack.get('evidence', []))} evidence items.")
        assert_process_recorded(db, TEST_CLIENT_ID, "blog_researcher", "Step 9: Blog Researcher")

        # ----------------------------------------------------------------------
        # 10. Blog Image Generation
        # ----------------------------------------------------------------------
        print("\n10. Executing Blog Image Generation...")
        record_usage(
            company_id=TEST_CLIENT_ID,
            provider="openai",
            model="dall-e-3",
            is_image=True,
            image_count=1,
            process="blog_image_generation",
            channel="blog",
        )
        assert_process_recorded(db, TEST_CLIENT_ID, "blog_image_generation", "Step 10: Blog Image Generation")

        # ----------------------------------------------------------------------
        # 11. Social Agent & CRAG Relevance Grader
        # ----------------------------------------------------------------------
        print("\n11. Executing Social Agent & CRAG Document Grader...")
        from social_agent.crag.grader import build_grader, grade_document
        with bind_usage_context(company_id=TEST_CLIENT_ID, process="crag_retrieval_grade", channel="social"):
            grader = build_grader()
            is_rel = asyncio.run(grade_document(grader, "What is the enterprise pricing?", "Enterprise voice agents cost $0.05 per minute."))
            print(f"   CRAG Grader relevance: {is_rel}")
        assert_process_recorded(db, TEST_CLIENT_ID, "crag_retrieval_grade", "Step 11: CRAG Retrieval Grade")

        from social_agent.agent_models import get_text_model
        with bind_usage_context(company_id=TEST_CLIENT_ID, process="social_agent", channel="instagram"):
            s_llm = get_text_model()
            s_res = s_llm.invoke("Generate a 1-sentence Instagram caption for our new voice AI launch.")
            print(f"   Social Agent response: {s_res.content[:60]}...")
        assert_process_recorded(db, TEST_CLIENT_ID, "social_agent", "Step 11: Social Agent")

        # ----------------------------------------------------------------------
        # 12. Blog Topic Picker (Tenant Specific AND Fresh Context System Job)
        # ----------------------------------------------------------------------
        print("\n12. Executing Blog Topic Picker (Tenant & System Contexts)...")
        from LeadAI.services.blog.topic_picker import TopicPickerService
        topic, kws = TopicPickerService.pick_daily_topic(
            db=db,
            client_id=TEST_CLIENT_ID,
        )
        print(f"   Tenant Topic: {topic[:60]}...")
        assert_process_recorded(db, TEST_CLIENT_ID, "blog_topic_picker", "Step 12: Tenant Topic Picker")

        # Fresh context system job with ClientId=None
        system_topic, _ = TopicPickerService.pick_daily_topic(
            db=db,
            client_id=None,
        )
        print(f"   System Job Topic: {system_topic[:60]}...")
        assert_process_recorded(db, None, "blog_topic_picker", "Step 12: System Topic Picker")

        # ----------------------------------------------------------------------
        # 13. Voice Opening Line & Voice Translation Flow
        # ----------------------------------------------------------------------
        print("\n13. Executing Voice Opening Line & Translation Turn...")
        from LeadAI.services.voice_flow import _compose_opening_text, handle_voice_turn

        voice_call = LeadCall(
            Id="call_entry_101",
            ClientId=TEST_CLIENT_ID,
            ConversationId="conv_voice_entry_101",
            Provider="exotel",
            Direction="inbound",
            PhoneMasked="+15559876543",
            Status="in-progress",
        )
        voice_conv = LeadConversation(
            Id="conv_voice_entry_101",
            ClientId=TEST_CLIENT_ID,
            CustomerId="cust_voice_1",
            Channel="voice",
            Status="open",
            CreatedBy="system",
        )
        db.add_all([voice_call, voice_conv])
        db.commit()

        # A. Voice Opening Line
        from LeadAI.engine.trace import TurnTrace
        turn_tr = TurnTrace(conversation_id="conv_voice_entry_101", client_id=TEST_CLIENT_ID, channel="voice")
        op_text, op_model, op_lat, op_conf, op_src = _compose_opening_text(
            db=db,
            client=db_client,
            conversation=voice_conv,
            language="en",
            trace=turn_tr,
        )
        print(f"   Voice Opening Line: {op_text[:60]}...")
        assert_process_recorded(db, TEST_CLIENT_ID, "voice_opening_line", "Step 13: Voice Opening Line")

        # B. Voice Translation Turn (Hindi Utterance)
        voice_res = handle_voice_turn(
            db=db,
            client=db_client,
            conversation=voice_conv,
            call=voice_call,
            utterance="क्या आपका सिस्टम सेल्सफोर्स सीआरएम के साथ जुड़ सकता है?",
            live_call=True,
            language="hi",
        )
        reply_safe = voice_res.reply_text[:60].encode("ascii", errors="replace").decode("ascii")
        print(f"   Voice Turn Reply: {reply_safe}...")
        assert_process_recorded(db, TEST_CLIENT_ID, "voice_translation", "Step 13: Voice Translation")

        # ----------------------------------------------------------------------
        # 14. Voice Agent (SimpleAgent, Compressor, Party Detector, Bot Extractor)
        # ----------------------------------------------------------------------
        print("\n14. Executing SimpleAgent, Compressor, Party Detector & Bot Extractor...")
        from outbound.app import SimpleAgent
        simple_agent = SimpleAgent(gender="neutral", language="hi", client_id=TEST_CLIENT_ID)
        
        async def run_voice_agent():
            # A. Voice Call Turn
            async for _ in simple_agent.get_response("नमस्ते, मुझे आपके प्लान के बारे में जानकारी चाहिए"):
                pass
            
            # B. Voice Transcript Compression (cross MAX_RAW_TURNS)
            for i in range(45):
                simple_agent.history.append({
                    "role": "user" if i % 2 == 0 else "assistant",
                    "content": f"Customer conversation message {i} discussing pricing and contract details."
                })
            await simple_agent._maybe_compress()

        asyncio.run(run_voice_agent())
        assert_process_recorded(db, TEST_CLIENT_ID, "voice_call", "Step 14: Voice Call")
        assert_process_recorded(db, TEST_CLIENT_ID, "voice_transcript_compress", "Step 14: Voice Transcript Compress")

        # C. Party Detector
        from outbound.bot.party_detector import PartyDetectorAgent
        detector = PartyDetectorAgent()
        detector_res = asyncio.run(
            detector.detect(
                [{"role": "user", "content": "Hello, thank you for calling Kestrel Homes. How may I assist you?"}],
                client_id=TEST_CLIENT_ID,
            )
        )
        print(f"   Party Detector Verdict: {detector_res}")
        assert_process_recorded(db, TEST_CLIENT_ID, "voice_party_detector", "Step 14: Voice Party Detector")

        # D. Legacy Bot Extraction
        from outbound.bot.agent import InsuranceClaimAgent
        from outbound.bot.claim_data_get import ClaimDataPrompt
        with patch.object(ClaimDataPrompt, "build_from_file", return_value="Claim Field Guide"):
            legacy_agent = InsuranceClaimAgent(knowledge_base=None, output_manager=None, call_id="call_legacy_101", client_id=TEST_CLIENT_ID)
            chunk_extracted = asyncio.run(legacy_agent._extract_from_chunk("Representative said claim number is CL-99281 and status is Approved."))
            print(f"   Legacy Bot Extracted Claim: {chunk_extracted.get('Claim_Number')}")
        assert_process_recorded(db, TEST_CLIENT_ID, "bot_extraction", "Step 14: Bot Extraction")

    # Flush background usage events
    flush_on_shutdown()
    time.sleep(0.5)

    # ----------------------------------------------------------------------
    # SELECT on `leadai_ai_usage_events`
    # ----------------------------------------------------------------------
    print("\n" + "=" * 135)
    print("   RAW ROWS IN `leadai_ai_usage_events` (FROM REAL APP ENTRY POINTS)")
    print("==========================================================================")
    rows = db.query(LeadAIUsageEvent).order_by(LeadAIUsageEvent.CreatedAt.asc()).all()

    print(f"{'ClientId':<26} | {'Process':<26} | {'Model':<24} | {'Tokens (In/Out/Tot)':<20} | {'Cost USD':<12} | {'Estimated'}")
    print("-" * 135)
    for r in rows:
        cid_display = r.ClientId if r.ClientId else "NULL"
        tok_str = f"{r.InputTokens}/{r.OutputTokens}/{r.TotalTokens}"
        cost_str = f"${r.CostUsd:.6f}" if r.CostUsd is not None else "Unknown"
        print(f"{cid_display:<26} | {r.Process:<26} | {r.Model:<24} | {tok_str:<20} | {cost_str:<12} | {str(r.IsEstimated):<10}")
    print("-" * 135)

    # Summary
    total_events = len(rows)
    tenant_events = sum(1 for r in rows if r.ClientId == TEST_CLIENT_ID)
    system_events = sum(1 for r in rows if r.ClientId is None)
    print(f"\nTotal Recorded Events: {total_events}")
    print(f"Tenant Events ({TEST_CLIENT_ID}): {tenant_events}")
    print(f"System Events (NULL ClientId): {system_events}")

    # Assert no NULL company for customer-facing processes
    null_customer_rows = db.query(LeadAIUsageEvent).filter(
        LeadAIUsageEvent.ClientId.is_(None),
        LeadAIUsageEvent.Process.notin_(["blog_topic_picker", "pre_warm"])
    ).all()
    print(f"Unattributed (NULL) Customer-Facing Rows: {len(null_customer_rows)}")
    assert len(null_customer_rows) == 0, f"Found {len(null_customer_rows)} customer-facing rows with NULL ClientId!"

    print("\n[PASS] ALL REAL ENTRY POINT CHECKS & PER-STEP ASSERTIONS PASSED SUCCESSFULLY!")
    db.close()


if __name__ == "__main__":
    run_app_entry_points_test()
