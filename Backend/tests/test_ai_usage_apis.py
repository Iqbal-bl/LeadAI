"""Tests for Phase 4 & Phase 5 AI Usage APIs, RBAC, Cross-Tenant Isolation, Cost Visibility, Reconciliation, Sarvam & Social Agent.

All tests run through REAL DB-backed role/permission resolution:
- LeadUserRole rows in database for each test identity (Company Admin A, Company Admin B, Manager, Employee, Super Admin).
- LeadRolePermission rows for permission overrides.
- Zero mock overrides on require(), scoped(), current_principal(), or resolve_scope().
- Only get_current_user dependency returns the authenticated user email from identity provider.
"""
import asyncio
import logging
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import conftest_stub  # installs core.base/core.database/core.auth stubs first
from conftest_stub import Base, SessionLocalAdmin, engine

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Setup test DB before imports
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["LEADAI_DATABASE_URL"] = "sqlite:///:memory:"
os.environ["AI_USAGE_SHOW_COST_TO_CLIENT"] = "false"

from core.auth import get_current_user
from core.usage_tracker import InAppUsageCallbackHandler, PROCESS_CATALOGUE, record_usage as real_record_usage
from domain.models import Client
from LeadAI.config import settings
from LeadAI.db import get_leadai_db
from LeadAI.models import (
    LeadRolePermission,
    LeadUserRole,
    ROLE_ADMIN,
    ROLE_COMPANY_ADMIN,
    ROLE_MANAGER,
    ROLE_EMPLOYEE,
)
from LeadAI.models_usage import LeadAIUsageDailyAggregate
from LeadAI.router import api_router

for _t in Base.metadata.sorted_tables:
    try:
        _t.create(bind=engine, checkfirst=True)
    except Exception:
        pass
TestingSessionLocal = SessionLocalAdmin

# Real FastAPI app with LeadAI router mounted under /api/leadai
app = FastAPI()
app.include_router(api_router, prefix=settings.api_prefix)


class TestAIUsageAPIsAndPipelines(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.db = TestingSessionLocal()
        app.dependency_overrides[get_leadai_db] = lambda: cls.db

        # 1. Seed test companies (Clients)
        cls.company_a = Client(Id="comp_alpha", Name="Alpha Enterprise", PhoneNumber="+15551111111", CreatedBy="system", IsActive=True)
        cls.company_b = Client(Id="comp_beta", Name="Beta Logistics", PhoneNumber="+15552222222", CreatedBy="system", IsActive=True)
        cls.db.add_all([cls.company_a, cls.company_b])

        # 2. Seed REAL LeadAI User Roles into database
        cls.super_admin_user = LeadUserRole(UserEmail="super@leadai.io", Role=ROLE_ADMIN, ClientId=None, IsActive=True, CreatedBy="system")
        cls.comp_admin_a_user = LeadUserRole(UserEmail="admin@alpha.com", Role=ROLE_COMPANY_ADMIN, ClientId="comp_alpha", IsActive=True, CreatedBy="system")
        cls.comp_admin_b_user = LeadUserRole(UserEmail="admin@beta.com", Role=ROLE_COMPANY_ADMIN, ClientId="comp_beta", IsActive=True, CreatedBy="system")
        cls.manager_a_user = LeadUserRole(UserEmail="manager@alpha.com", Role=ROLE_MANAGER, ClientId="comp_alpha", IsActive=True, CreatedBy="system")
        cls.employee_a_user = LeadUserRole(UserEmail="emp@alpha.com", Role=ROLE_EMPLOYEE, ClientId="comp_alpha", IsActive=True, CreatedBy="system")

        cls.db.add_all([
            cls.super_admin_user,
            cls.comp_admin_a_user,
            cls.comp_admin_b_user,
            cls.manager_a_user,
            cls.employee_a_user,
        ])

        # 3. Seed usage aggregates for testing (IST date)
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # Company A aggregates
        cls.db.add(LeadAIUsageDailyAggregate(
            ClientId="comp_alpha",
            Process="chat_answer",
            Model="gpt-4o-mini",
            UsageDate=today_str,
            TotalRequests=10,
            TotalInputTokens=2000,
            TotalOutputTokens=500,
            TotalTokens=2500,
            TotalCostUsd=0.000600,
            HasUnknownPrice=False,
        ))
        cls.db.add(LeadAIUsageDailyAggregate(
            ClientId="comp_alpha",
            Process="voice_call",
            Model="gpt-4o-mini",
            UsageDate=today_str,
            TotalRequests=5,
            TotalInputTokens=4000,
            TotalOutputTokens=200,
            TotalTokens=4200,
            TotalCostUsd=0.000720,
            HasUnknownPrice=False,
        ))

        # Company B aggregates
        cls.db.add(LeadAIUsageDailyAggregate(
            ClientId="comp_beta",
            Process="social_copy_draft",
            Model="gpt-4o-mini",
            UsageDate=today_str,
            TotalRequests=3,
            TotalInputTokens=600,
            TotalOutputTokens=300,
            TotalTokens=900,
            TotalCostUsd=0.000270,
            HasUnknownPrice=False,
        ))

        # System/Platform aggregate (ClientId is None, non-customer-facing)
        cls.db.add(LeadAIUsageDailyAggregate(
            ClientId=None,
            Process="blog_topic_picker",
            Model="gpt-4o-mini-2024-07-18",
            UsageDate=today_str,
            TotalRequests=1,
            TotalInputTokens=350,
            TotalOutputTokens=120,
            TotalTokens=470,
            TotalCostUsd=0.000125,
            HasUnknownPrice=False,
        ))

        # Unattributed Customer aggregate (ClientId is None, customer-facing)
        cls.db.add(LeadAIUsageDailyAggregate(
            ClientId=None,
            Process="voice_call",
            Model="sarvam-105b",
            UsageDate=today_str,
            TotalRequests=2,
            TotalInputTokens=200,
            TotalOutputTokens=100,
            TotalTokens=300,
            TotalCostUsd=0.000060,
            HasUnknownPrice=False,
        ))
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        cls.db.close()

    def test_super_admin_api_full_aggregation(self):
        """Super admin (real ROLE_ADMIN in DB) sees all companies, unattributed & system rows, process display names, and normalized models."""
        app.dependency_overrides[get_current_user] = lambda: "super@leadai.io"
        resp = self.client.get("/api/leadai/usage/admin")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()

        # 1. Summary checks
        summary = data["summary"]
        self.assertGreater(summary["total_requests"], 0)
        self.assertGreater(summary["total_tokens"], 0)
        self.assertGreater(summary["total_cost_usd"], 0.0)

        # 2. Company list checks
        companies = data["companies"]
        company_keys = [c["company_key"] for c in companies]
        self.assertIn("comp_alpha", company_keys)
        self.assertIn("comp_beta", company_keys)
        self.assertIn("system", company_keys)
        self.assertIn("unattributed", company_keys)

        system_row = next(c for c in companies if c["company_key"] == "system")
        self.assertEqual(system_row["company_name"], "System / Platform")
        self.assertTrue(system_row["is_system"])
        self.assertFalse(system_row["is_unattributed"])

        unatt_row = next(c for c in companies if c["company_key"] == "unattributed")
        self.assertEqual(unatt_row["company_name"], "Unattributed")
        self.assertTrue(unatt_row["is_unattributed"])
        self.assertFalse(unatt_row["is_system"])

        # 3. Model family normalization (gpt-4o-mini-2024-07-18 -> gpt-4o-mini)
        models = data["by_model"]
        model_names = [m["model"] for m in models]
        self.assertIn("gpt-4o-mini", model_names)
        self.assertNotIn("gpt-4o-mini-2024-07-18", model_names)

        # 4. Filter check: ?client_id=system vs ?client_id=unattributed
        resp_sys = self.client.get("/api/leadai/usage/admin?client_id=system")
        self.assertEqual(resp_sys.status_code, 200)
        sys_data = resp_sys.json()
        sys_procs = [p["process"] for p in sys_data["by_process"]]
        self.assertIn("blog_topic_picker", sys_procs)
        self.assertNotIn("voice_call", sys_procs)

        resp_unatt = self.client.get("/api/leadai/usage/admin?client_id=unattributed")
        self.assertEqual(resp_unatt.status_code, 200)
        unatt_data = resp_unatt.json()
        unatt_procs = [p["process"] for p in unatt_data["by_process"]]
        self.assertIn("voice_call", unatt_procs)
        self.assertNotIn("blog_topic_picker", unatt_procs)

    def test_company_admin_a_calling_client_with_param_b_only_gets_a(self):
        """Cross-tenant attack: company admin A attempts ?client_id=comp_beta and header tampering.

        - Query param ?client_id=comp_beta is rejected with 403 Forbidden because caller has no grant for company B.
        - Header tampering only ever returns company A's data because tenant scope is locked to the authenticated principal.
        """
        app.dependency_overrides[get_current_user] = lambda: "admin@alpha.com"
        
        # 1. Attacker passes ?client_id=comp_beta -> Rejected with 403 Forbidden
        resp_query = self.client.get("/api/leadai/usage/client?client_id=comp_beta")
        self.assertEqual(resp_query.status_code, 403)
        self.assertIn("You do not have access to that company", resp_query.text)

        # 2. Attacker passes custom header X-Client-ID: comp_beta -> Server ignores header and returns strictly comp_alpha
        resp = self.client.get("/api/leadai/usage/client", headers={"X-Client-ID": "comp_beta"})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()

        # ClientId returned must strictly be comp_alpha
        self.assertEqual(data["client_id"], "comp_alpha")
        self.assertEqual(data["company_name"], "Alpha Enterprise")
        
        # Must contain comp_alpha processes and NOT comp_beta processes
        proc_keys = [p["process"] for p in data["by_process"]]
        self.assertIn("chat_answer", proc_keys)
        self.assertIn("voice_call", proc_keys)
        self.assertNotIn("social_copy_draft", proc_keys)


    def test_manager_and_employee_without_usage_read_get_403(self):
        """Manager and Employee roles do not have usage.read permission in RBAC table -> 403 Forbidden."""
        # 1. Manager
        app.dependency_overrides[get_current_user] = lambda: "manager@alpha.com"
        resp = self.client.get("/api/leadai/usage/client")
        self.assertEqual(resp.status_code, 403)

        # 2. Employee
        app.dependency_overrides[get_current_user] = lambda: "emp@alpha.com"
        resp = self.client.get("/api/leadai/usage/client")
        self.assertEqual(resp.status_code, 403)

    def test_company_admin_calling_usage_admin_gets_403(self):
        """Company Admin cannot access /usage/admin -> 403 Forbidden."""
        app.dependency_overrides[get_current_user] = lambda: "admin@alpha.com"
        resp = self.client.get("/api/leadai/usage/admin")
        self.assertEqual(resp.status_code, 403)

    def test_company_admin_with_revoked_permission_override_gets_403(self):
        """Company Admin with a database permission override revoking usage.read receives 403."""
        override = LeadRolePermission(Role=ROLE_COMPANY_ADMIN, PermissionKey="usage.read", IsGranted=False)
        self.db.add(override)
        self.db.commit()
        try:
            app.dependency_overrides[get_current_user] = lambda: "admin@alpha.com"
            resp = self.client.get("/api/leadai/usage/client")
            self.assertEqual(resp.status_code, 403)
        finally:
            self.db.delete(override)
            self.db.commit()

    def test_platform_admin_can_read_all_companies(self):
        """Platform Admin can read all companies and filter by company or system."""
        app.dependency_overrides[get_current_user] = lambda: "super@leadai.io"
        
        # Read all
        resp = self.client.get("/api/leadai/usage/admin")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        c_ids = {c["client_id"] for c in data["companies"]}
        self.assertTrue({"comp_alpha", "comp_beta", None}.issubset(c_ids))

        # Filter by specific company
        resp_filter = self.client.get("/api/leadai/usage/admin?client_id=comp_alpha")
        self.assertEqual(resp_filter.status_code, 200)
        data_filter = resp_filter.json()
        filter_ids = {c["client_id"] for c in data_filter["companies"]}
        self.assertEqual(filter_ids, {"comp_alpha"})

        # Filter by system
        resp_sys = self.client.get("/api/leadai/usage/admin?client_id=system")
        self.assertEqual(resp_sys.status_code, 200)
        data_sys = resp_sys.json()
        sys_ids = {c["client_id"] for c in data_sys["companies"]}
        self.assertEqual(sys_ids, {None})

    def test_raw_json_cost_fields_hidden_when_cost_flag_is_false(self):
        """When AI_USAGE_SHOW_COST_TO_CLIENT=false, raw JSON for /usage/client contains no cost values."""
        app.dependency_overrides[get_current_user] = lambda: "admin@alpha.com"
        
        with patch.dict(os.environ, {"AI_USAGE_SHOW_COST_TO_CLIENT": "false"}):
            resp = self.client.get("/api/leadai/usage/client")
            self.assertEqual(resp.status_code, 200)
            raw_json = resp.json()

            # show_cost must be False
            self.assertFalse(raw_json["show_cost"])
            # summary cost must be None
            self.assertIsNone(raw_json["summary"]["total_cost_usd"])
            # each process cost must be None
            for proc in raw_json["by_process"]:
                self.assertIsNone(proc["total_cost_usd"])
            # daily trend cost must be None
            for day in raw_json["daily_trend"]:
                self.assertIsNone(day["cost_usd"])

    def test_exact_reconciliation_seeded_events_match_database_sums(self):
        """Reconciliation Test:
        Asserts GET /usage/admin and GET /usage/client summary totals equal the exact database sums.
        """
        # Database rows total across entire table
        all_aggregates = self.db.query(LeadAIUsageDailyAggregate).all()
        db_total_reqs = sum(a.TotalRequests for a in all_aggregates)
        db_total_in_tok = sum(a.TotalInputTokens for a in all_aggregates)
        db_total_out_tok = sum(a.TotalOutputTokens for a in all_aggregates)
        db_total_tok = sum(a.TotalTokens for a in all_aggregates)
        db_total_cost = round(sum(a.TotalCostUsd for a in all_aggregates), 6)

        # 1. Test Super Admin API Reconciliation
        app.dependency_overrides[get_current_user] = lambda: "super@leadai.io"
        resp = self.client.get("/api/leadai/usage/admin")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        summary = data["summary"]

        self.assertEqual(summary["total_requests"], db_total_reqs)
        self.assertEqual(summary["total_input_tokens"], db_total_in_tok)
        self.assertEqual(summary["total_output_tokens"], db_total_out_tok)
        self.assertEqual(summary["total_tokens"], db_total_tok)
        self.assertAlmostEqual(summary["total_cost_usd"], db_total_cost, places=5)

        # 2. Test Client API Reconciliation for Company A
        comp_a_aggregates = self.db.query(LeadAIUsageDailyAggregate).filter_by(ClientId="comp_alpha").all()
        exp_a_reqs = sum(a.TotalRequests for a in comp_a_aggregates)
        exp_a_in_tok = sum(a.TotalInputTokens for a in comp_a_aggregates)
        exp_a_out_tok = sum(a.TotalOutputTokens for a in comp_a_aggregates)
        exp_a_tok = sum(a.TotalTokens for a in comp_a_aggregates)
        exp_a_cost = round(sum(a.TotalCostUsd for a in comp_a_aggregates), 6)

        app.dependency_overrides[get_current_user] = lambda: "admin@alpha.com"
        with patch.dict(os.environ, {"AI_USAGE_SHOW_COST_TO_CLIENT": "true"}):
            resp = self.client.get("/api/leadai/usage/client")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            c_summary = data["summary"]

            self.assertEqual(c_summary["total_requests"], exp_a_reqs)
            self.assertEqual(c_summary["total_input_tokens"], exp_a_in_tok)
            self.assertEqual(c_summary["total_output_tokens"], exp_a_out_tok)
            self.assertEqual(c_summary["total_tokens"], exp_a_tok)
            self.assertAlmostEqual(c_summary["total_cost_usd"], exp_a_cost, places=5)

    def test_process_catalogue_endpoint(self):
        """Catalogue endpoint returns all processes with display names and categories."""
        app.dependency_overrides[get_current_user] = lambda: "admin@alpha.com"
        resp = self.client.get("/api/leadai/usage/catalog")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("catalog", data)
        items = {item["process"]: item for item in data["catalog"]}
        self.assertIn("voice_call", items)
        self.assertEqual(items["voice_call"]["display_name"], "AI Voice Call Turn")
        self.assertEqual(items["voice_call"]["category"], "voice")

    def test_voice_call_without_client_id_unattributed_and_is_leadai_flag(self):
        """Call setup (active_calls -> agent creation) tests:
        1. LeadAI call with client_id -> records with company_id
        2. LeadAI call missing client_id -> records with company_id=None (Unattributed) + warning
        3. Non-LeadAI call -> skips recording entirely + debug log
        """
        from outbound.app import active_calls, build_agent_for_call
        from LeadAI.services.call_bridge import register_call_context
        
        mock_chunk = MagicMock()
        mock_chunk.usage = None
        mock_chunk.choices = [MagicMock(delta=MagicMock(content="Hello"), finish_reason="stop")]

        async def mock_stream(*args, **kwargs):
            yield mock_chunk

        async def run_agent(ag):
            collected = []
            async for token in ag.get_response("Hi"):
                collected.append(token)
            return "".join(collected)

        # 1. LeadAI call with client_id
        register_call_context("CA_leadai_1", "+919999999991", sections=[], voice={"language": "hi", "gender": "female"}, client_id="comp_alpha")
        call_data_1 = active_calls["CA_leadai_1"]
        agent_1 = build_agent_for_call(call_data_1)
        agent_1._client.chat.completions.create = AsyncMock(return_value=mock_stream())

        with patch("core.usage_tracker.record_usage") as mock_record_1:
            _ = asyncio.run(run_agent(agent_1))
            mock_record_1.assert_called_once()
            kwargs_1 = mock_record_1.call_args.kwargs
            self.assertEqual(kwargs_1["company_id"], "comp_alpha")
            self.assertEqual(kwargs_1["process"], "voice_call")

        # 2. LeadAI call missing client_id (e.g. orphaned/unassigned campaign call)
        register_call_context("CA_leadai_2", "+919999999992", sections=[], voice={"language": "hi"}, client_id=None)
        call_data_2 = active_calls["CA_leadai_2"]
        agent_2 = build_agent_for_call(call_data_2)
        agent_2._client.chat.completions.create = AsyncMock(return_value=mock_stream())

        with patch("core.usage_tracker.record_usage") as mock_record_2:
            _ = asyncio.run(run_agent(agent_2))
            mock_record_2.assert_called_once()
            kwargs_2 = mock_record_2.call_args.kwargs
            self.assertIsNone(kwargs_2["company_id"])  # Unattributed
            self.assertEqual(kwargs_2["process"], "voice_call")

        # 3. Non-LeadAI standalone call (leadai=False / not in active_calls)
        active_calls["CA_non_leadai"] = {"phone_number": "+919999999993", "leadai": False, "client_id": None}
        call_data_3 = active_calls["CA_non_leadai"]
        agent_3 = build_agent_for_call(call_data_3)
        agent_3._client.chat.completions.create = AsyncMock(return_value=mock_stream())

        with patch("core.usage_tracker.record_usage") as mock_record_3:
            _ = asyncio.run(run_agent(agent_3))
            mock_record_3.assert_not_called()

    def test_sarvam_streaming_missing_usage_estimated_flag(self):
        """SimpleAgent handles Sarvam streaming with missing final usage chunk via fallback estimation."""
        from outbound.app import SimpleAgent
        
        agent = SimpleAgent(client_id="comp_alpha", language="hi")
        
        # Mock chunk without usage object
        mock_chunk_1 = MagicMock()
        mock_chunk_1.usage = None
        mock_chunk_1.choices = [MagicMock(delta=MagicMock(content="नमस्ते! "), finish_reason=None)]
        
        mock_chunk_2 = MagicMock()
        mock_chunk_2.usage = None  # Missing usage in final chunk!
        mock_chunk_2.choices = [MagicMock(delta=MagicMock(content="आपकी क्या सहायता करूँ?"), finish_reason="stop")]

        async def mock_stream(*args, **kwargs):
            yield mock_chunk_1
            yield mock_chunk_2

        agent._client.chat.completions.create = AsyncMock(return_value=mock_stream())

        async def run_agent():
            collected = []
            async for token in agent.get_response("नमस्ते"):
                collected.append(token)
            return "".join(collected)

        with patch("core.usage_tracker.record_usage") as mock_record:
            reply = asyncio.run(run_agent())
            self.assertIn("नमस्ते", reply)
            
            # Verify record_usage was called with is_estimated=True
            mock_record.assert_called_once()
            call_kwargs = mock_record.call_args.kwargs
            self.assertEqual(call_kwargs["company_id"], "comp_alpha")
            self.assertEqual(call_kwargs["process"], "voice_call")
            self.assertTrue(call_kwargs["is_estimated"])

    def test_social_agent_real_graph_multi_turn_tool_loop_usage(self):
        """Social Agent real LangGraph tool loop runs a multi-turn task and records one usage row per model call
        using the production get_model() factory and InAppUsageCallbackHandler wiring without the fake model invoking the handler directly.
        """
        from langchain_core.messages import AIMessage, HumanMessage
        from langchain_core.outputs import ChatGeneration, ChatResult
        from langchain_core.tools import tool
        from langchain_openai import ChatOpenAI
        from social_agent.agent.graph import build_graph

        @tool
        def mock_search_tool(query: str) -> str:
            """Mock search tool."""
            return f"Found results for {query}"

        @tool
        def finish(result: str) -> str:
            """Finish task."""
            return f"Finished: {result}"

        turn1_gen = ChatGeneration(
            message=AIMessage(
                content="",
                tool_calls=[{"name": "mock_search_tool", "args": {"query": "pricing"}, "id": "call_1"}],
            )
        )
        res1 = ChatResult(
            generations=[turn1_gen],
            llm_output={"token_usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}, "model_name": "gpt-4o-mini"},
        )

        turn2_gen = ChatGeneration(
            message=AIMessage(
                content="",
                tool_calls=[{"name": "finish", "args": {"result": "task complete"}, "id": "call_2"}],
            )
        )
        res2 = ChatResult(
            generations=[turn2_gen],
            llm_output={"token_usage": {"prompt_tokens": 180, "completion_tokens": 40, "total_tokens": 220}, "model_name": "gpt-4o-mini"},
        )

        turns = [res1, res2]
        turn_idx = 0

        async def fake_agenerate(self_model, messages, stop=None, run_manager=None, **kwargs):
            nonlocal turn_idx
            res = turns[turn_idx]
            turn_idx += 1
            return res

        with patch.object(ChatOpenAI, "_agenerate", fake_agenerate):
            graph = build_graph(tools=[mock_search_tool, finish])
            with patch("core.usage_tracker.record_usage") as mock_record:
                _ = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content="Search for pricing and finish")]}))
                self.assertEqual(turn_idx, 2)
                self.assertEqual(mock_record.call_count, 2)
                call1 = mock_record.call_args_list[0].kwargs
                call2 = mock_record.call_args_list[1].kwargs
                self.assertEqual(call1["input_tokens"], 100)
                self.assertEqual(call1["output_tokens"], 20)
                self.assertEqual(call2["input_tokens"], 180)
                self.assertEqual(call2["output_tokens"], 40)

    def test_end_to_end_record_flush_aggregate_and_api_verification(self):
        """End-to-end: record_usage -> queue -> flush batch -> daily aggregate upsert -> exact API totals."""
        from core.usage_tracker import _flush_batch, _usage_queue
        from LeadAI.models_usage import LeadAIUsageDailyAggregate, LeadAIUsageEvent

        # Clear prior chat_answer aggregates for comp_alpha to assert exact isolated numbers
        self.db.query(LeadAIUsageDailyAggregate).filter(
            LeadAIUsageDailyAggregate.ClientId == "comp_alpha",
            LeadAIUsageDailyAggregate.Process == "chat_answer",
        ).delete()
        self.db.commit()

        # 1. First batch: enqueue via real_record_usage
        real_record_usage(
            company_id="comp_alpha",
            process="chat_answer",
            channel="chat",
            provider="openai",
            model="gpt-4o-mini",
            input_tokens=1000,
            output_tokens=200,
            conversation_id="conv_e2e_1",
        )
        real_record_usage(
            company_id="comp_beta",
            process="comment_reply_generation",
            channel="social",
            provider="openai",
            model="gpt-4o-mini",
            input_tokens=500,
            output_tokens=100,
            conversation_id="conv_e2e_2",
        )

        # Drain queue for Flush 1
        batch1 = []
        while not _usage_queue.empty():
            batch1.append(_usage_queue.get_nowait())
            _usage_queue.task_done()
        self.assertEqual(len(batch1), 2)
        _flush_batch(batch1)

        # 2. Second batch for SAME company/process/model/day to prove aggregate upsert adds without duplicating
        real_record_usage(
            company_id="comp_alpha",
            process="chat_answer",
            channel="chat",
            provider="openai",
            model="gpt-4o-mini",
            input_tokens=500,
            output_tokens=100,
            conversation_id="conv_e2e_3",
        )
        batch2 = []
        while not _usage_queue.empty():
            batch2.append(_usage_queue.get_nowait())
            _usage_queue.task_done()
        self.assertEqual(len(batch2), 1)
        _flush_batch(batch2)

        # Verify raw events in DB: 3 distinct events
        ev_count = self.db.query(LeadAIUsageEvent).filter(LeadAIUsageEvent.ConversationId.in_(["conv_e2e_1", "conv_e2e_2", "conv_e2e_3"])).count()
        self.assertEqual(ev_count, 3)

        # Verify aggregate table for comp_alpha: exactly 1 aggregate row with accumulated sum (1000+500=1500 in, 200+100=300 out)
        aggs = self.db.query(LeadAIUsageDailyAggregate).filter(
            LeadAIUsageDailyAggregate.ClientId == "comp_alpha",
            LeadAIUsageDailyAggregate.Process == "chat_answer",
        ).all()
        self.assertEqual(len(aggs), 1, "Aggregate upsert must maintain exactly 1 row per (client, process, model, date)")
        self.assertEqual(aggs[0].TotalRequests, 2)
        self.assertEqual(aggs[0].TotalInputTokens, 1500)
        self.assertEqual(aggs[0].TotalOutputTokens, 300)
        self.assertEqual(aggs[0].TotalTokens, 1800)

        # Verify super admin API aggregates
        app.dependency_overrides[get_current_user] = lambda: "super@leadai.io"
        resp_admin = self.client.get("/api/leadai/usage/admin")
        self.assertEqual(resp_admin.status_code, 200)
        data_admin = resp_admin.json()
        self.assertGreater(data_admin["summary"]["total_requests"], 0)

        # Verify client API for comp_alpha contains exact totals
        app.dependency_overrides[get_current_user] = lambda: "admin@alpha.com"
        resp_client = self.client.get("/api/leadai/usage/client")
        self.assertEqual(resp_client.status_code, 200)
        data_client = resp_client.json()
        self.assertEqual(data_client["client_id"], "comp_alpha")
        chat_proc = next(p for p in data_client["by_process"] if p["process"] == "chat_answer")
        self.assertEqual(chat_proc["total_requests"], 2)
        self.assertEqual(chat_proc["total_input_tokens"], 1500)
        self.assertEqual(chat_proc["total_output_tokens"], 300)
        self.assertEqual(chat_proc["total_tokens"], 1800)


if __name__ == "__main__":
    unittest.main(verbosity=2)
