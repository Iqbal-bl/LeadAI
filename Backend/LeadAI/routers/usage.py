"""AI Usage & Token Attribution Routers — Super Admin and Company Admin APIs.

Multi-tenant & IST Date Scoped:
- /api/leadai/usage/admin: Platform Admin only. Aggregates usage across companies, system jobs, processes, models, and daily trends.
- /api/leadai/usage/client: Company Admin only (guarded by `usage.read`). Tenant ID resolved strictly from logged-in principal. Cost visibility controlled by `AI_USAGE_SHOW_COST_TO_CLIENT`.
- /api/leadai/usage/catalog: Process catalogue with display names and categories.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from core.pricing import PROCESS_CATALOGUE, normalize_model_name
from ..config import settings
from ..db import get_leadai_db
from domain.models import Client
from ..models import ROLE_ADMIN
from ..models_usage import LeadAIUsageDailyAggregate, LeadAIUsageEvent
from ..rbac import Principal, require, scoped

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/usage", tags=["LeadAI • AI Usage & Analytics"])

IST = ZoneInfo("Asia/Kolkata")


def _get_default_date_range(days: int = 30) -> tuple[str, str]:
    now_ist = datetime.now(IST)
    end_date = now_ist.strftime("%Y-%m-%d")
    start_date = (now_ist - timedelta(days=days)).strftime("%Y-%m-%d")
    return start_date, end_date


def _get_process_info(proc_key: str) -> dict[str, Any]:
    cat = PROCESS_CATALOGUE.get(proc_key, {})
    return {
        "process": proc_key,
        "display_name": cat.get("display_name", proc_key.replace("_", " ").title()),
        "category": cat.get("category", "general"),
        "is_customer_facing": cat.get("is_customer_facing", True),
    }


# ===========================================================================
# 1. Process Catalogue Endpoint
# ===========================================================================
@router.get(
    "/catalog",
    summary="Get AI Process Catalogue with display names and categories",
)
def get_usage_catalog(
    principal: Principal = Depends(require("usage.read")),
):
    items = []
    for proc, meta in PROCESS_CATALOGUE.items():
        items.append({
            "process": proc,
            "display_name": meta.get("display_name", proc.replace("_", " ").title()),
            "category": meta.get("category", "general"),
            "is_customer_facing": meta.get("is_customer_facing", True),
        })
    return {"catalog": items}


# ===========================================================================
# 2. Super Admin API (Platform Admin Only)
# ===========================================================================
@router.get(
    "/admin",
    summary="Super Admin: Comprehensive AI usage and cost analytics across companies",
)
def get_super_admin_ai_usage(
    start_date: Optional[str] = Query(None, description="Start date in YYYY-MM-DD (IST)"),
    end_date: Optional[str] = Query(None, description="End date in YYYY-MM-DD (IST)"),
    client_id: Optional[str] = Query(None, description="Optional ClientId or 'system' / 'unattributed' filter"),
    principal: Principal = Depends(require("usage.read")),
    db: Session = Depends(get_leadai_db),
):
    if not (principal.is_platform_admin or principal.role == ROLE_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super Admin privileges required to view cross-company AI usage.",
        )

    def_start, def_end = _get_default_date_range(30)
    s_date = start_date or def_start
    e_date = end_date or def_end

    system_processes = {k for k, v in PROCESS_CATALOGUE.items() if not v.get("is_customer_facing", True)}

    # Query daily aggregates for the date range
    query = db.query(LeadAIUsageDailyAggregate).filter(
        LeadAIUsageDailyAggregate.UsageDate >= s_date,
        LeadAIUsageDailyAggregate.UsageDate <= e_date,
    )

    if client_id:
        if client_id.lower() == "system":
            query = query.filter(
                LeadAIUsageDailyAggregate.ClientId.is_(None),
                LeadAIUsageDailyAggregate.Process.in_(system_processes),
            )
        elif client_id.lower() == "unattributed":
            query = query.filter(
                LeadAIUsageDailyAggregate.ClientId.is_(None),
                ~LeadAIUsageDailyAggregate.Process.in_(system_processes),
            )
        else:
            query = query.filter(LeadAIUsageDailyAggregate.ClientId == client_id)

    rows = query.all()

    # Client map for names
    clients = db.query(Client.Id, Client.Name).all()
    client_name_map = {c.Id: c.Name for c in clients}

    # Aggregations
    total_reqs = 0
    total_in_tok = 0
    total_out_tok = 0
    total_tok = 0
    total_cost = 0.0
    has_unknown_price = False

    by_company_map: dict[str, dict[str, Any]] = {}
    by_process_map: dict[str, dict[str, Any]] = {}
    by_model_map: dict[str, dict[str, Any]] = {}
    daily_map: dict[str, dict[str, Any]] = {}

    for r in rows:
        cid = r.ClientId
        proc = r.Process
        mdl = r.Model
        udate = r.UsageDate

        reqs = r.TotalRequests or 0
        in_t = r.TotalInputTokens or 0
        out_t = r.TotalOutputTokens or 0
        tot_t = r.TotalTokens or (in_t + out_t)
        cost = r.TotalCostUsd or 0.0
        unk_p = bool(r.HasUnknownPrice)

        total_reqs += reqs
        total_in_tok += in_t
        total_out_tok += out_t
        total_tok += tot_t
        total_cost += cost
        if unk_p:
            has_unknown_price = True

        p_info = _get_process_info(proc)
        is_cust = p_info.get("is_customer_facing", True)

        # Determine company bucket key & display
        if cid is None:
            if is_cust:
                comp_key = "unattributed"
                comp_name = "Unattributed"
                is_sys = False
                is_unatt = True
                real_cid = None
            else:
                comp_key = "system"
                comp_name = "System / Platform"
                is_sys = True
                is_unatt = False
                real_cid = None
        else:
            comp_key = cid
            comp_name = client_name_map.get(cid, "Unknown Company")
            is_sys = False
            is_unatt = False
            real_cid = cid

        # By Company
        if comp_key not in by_company_map:
            by_company_map[comp_key] = {
                "client_id": real_cid,
                "company_key": comp_key,
                "company_name": comp_name,
                "is_system": is_sys,
                "is_unattributed": is_unatt,
                "total_requests": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "has_unknown_price": False,
            }
        by_company_map[comp_key]["total_requests"] += reqs
        by_company_map[comp_key]["total_input_tokens"] += in_t
        by_company_map[comp_key]["total_output_tokens"] += out_t
        by_company_map[comp_key]["total_tokens"] += tot_t
        by_company_map[comp_key]["total_cost_usd"] = round(by_company_map[comp_key]["total_cost_usd"] + cost, 6)
        if unk_p:
            by_company_map[comp_key]["has_unknown_price"] = True

        # By Process
        if proc not in by_process_map:
            by_process_map[proc] = {
                **p_info,
                "total_requests": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "has_unknown_price": False,
            }
        by_process_map[proc]["total_requests"] += reqs
        by_process_map[proc]["total_input_tokens"] += in_t
        by_process_map[proc]["total_output_tokens"] += out_t
        by_process_map[proc]["total_tokens"] += tot_t
        by_process_map[proc]["total_cost_usd"] = round(by_process_map[proc]["total_cost_usd"] + cost, 6)
        if unk_p:
            by_process_map[proc]["has_unknown_price"] = True

        # By Model (normalized canonical family)
        norm_mdl = normalize_model_name(mdl)
        if norm_mdl not in by_model_map:
            by_model_map[norm_mdl] = {
                "model": norm_mdl,
                "total_requests": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "has_unknown_price": False,
            }
        by_model_map[norm_mdl]["total_requests"] += reqs
        by_model_map[norm_mdl]["total_input_tokens"] += in_t
        by_model_map[norm_mdl]["total_output_tokens"] += out_t
        by_model_map[norm_mdl]["total_tokens"] += tot_t
        by_model_map[norm_mdl]["total_cost_usd"] = round(by_model_map[norm_mdl]["total_cost_usd"] + cost, 6)
        if unk_p:
            by_model_map[norm_mdl]["has_unknown_price"] = True

        # Daily Trend
        if udate not in daily_map:
            daily_map[udate] = {
                "date": udate,
                "requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "tokens": 0,
                "cost_usd": 0.0,
            }
        daily_map[udate]["requests"] += reqs
        daily_map[udate]["input_tokens"] += in_t
        daily_map[udate]["output_tokens"] += out_t
        daily_map[udate]["tokens"] += tot_t
        daily_map[udate]["cost_usd"] = round(daily_map[udate]["cost_usd"] + cost, 6)

    # Sort companies: real companies by cost desc, unattributed and system at bottom
    companies_list = sorted(
        by_company_map.values(),
        key=lambda c: (c["is_system"] or c["is_unattributed"], -c["total_cost_usd"]),
    )
    process_list = sorted(by_process_map.values(), key=lambda p: p["total_cost_usd"], reverse=True)
    model_list = sorted(by_model_map.values(), key=lambda m: m["total_cost_usd"], reverse=True)
    trend_list = sorted(daily_map.values(), key=lambda d: d["date"])

    return {
        "summary": {
            "total_requests": total_reqs,
            "total_input_tokens": total_in_tok,
            "total_output_tokens": total_out_tok,
            "total_tokens": total_tok,
            "total_cost_usd": round(total_cost, 6),
            "has_unknown_price": has_unknown_price,
            "is_partial": has_unknown_price,
            "start_date": s_date,
            "end_date": e_date,
            "filter_client_id": client_id,
        },
        "companies": companies_list,
        "by_process": process_list,
        "by_model": model_list,
        "daily_trend": trend_list,
    }


# ===========================================================================
# 3. Company Admin API (Tenant Scoped Only)
# ===========================================================================
@router.get(
    "/client",
    summary="Company Admin: AI usage metrics, request volume and process breakdown for current company",
)
def get_company_ai_usage(
    start_date: Optional[str] = Query(None, description="Start date in YYYY-MM-DD (IST)"),
    end_date: Optional[str] = Query(None, description="End date in YYYY-MM-DD (IST)"),
    scope: tuple[Principal, str] = Depends(scoped("usage.read")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant context required for company AI usage.",
        )

    def_start, def_end = _get_default_date_range(30)
    s_date = start_date or def_start
    e_date = end_date or def_end

    # Cost visibility setting
    show_cost = getattr(settings, "ai_usage_show_cost_to_client", False) or (
        os.getenv("AI_USAGE_SHOW_COST_TO_CLIENT", "false").lower() in ("true", "1", "yes")
    )

    # Query daily aggregates strictly for this tenant
    rows = (
        db.query(LeadAIUsageDailyAggregate)
        .filter(
            LeadAIUsageDailyAggregate.ClientId == client_id,
            LeadAIUsageDailyAggregate.UsageDate >= s_date,
            LeadAIUsageDailyAggregate.UsageDate <= e_date,
        )
        .all()
    )

    total_reqs = 0
    total_in_tok = 0
    total_out_tok = 0
    total_tok = 0
    total_cost = 0.0

    by_process_map: dict[str, dict[str, Any]] = {}
    daily_map: dict[str, dict[str, Any]] = {}

    for r in rows:
        proc = r.Process
        udate = r.UsageDate

        reqs = r.TotalRequests or 0
        in_t = r.TotalInputTokens or 0
        out_t = r.TotalOutputTokens or 0
        tot_t = r.TotalTokens or (in_t + out_t)
        cost = r.TotalCostUsd or 0.0

        total_reqs += reqs
        total_in_tok += in_t
        total_out_tok += out_t
        total_tok += tot_t
        total_cost += cost

        # Process aggregation
        if proc not in by_process_map:
            p_info = _get_process_info(proc)
            by_process_map[proc] = {
                **p_info,
                "total_requests": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0 if show_cost else None,
            }
        by_process_map[proc]["total_requests"] += reqs
        by_process_map[proc]["total_input_tokens"] += in_t
        by_process_map[proc]["total_output_tokens"] += out_t
        by_process_map[proc]["total_tokens"] += tot_t
        if show_cost:
            by_process_map[proc]["total_cost_usd"] = round(by_process_map[proc]["total_cost_usd"] + cost, 6)

        # Daily Trend
        if udate not in daily_map:
            daily_map[udate] = {
                "date": udate,
                "requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "tokens": 0,
                "cost_usd": 0.0 if show_cost else None,
            }
        daily_map[udate]["requests"] += reqs
        daily_map[udate]["input_tokens"] += in_t
        daily_map[udate]["output_tokens"] += out_t
        daily_map[udate]["tokens"] += tot_t
        if show_cost:
            daily_map[udate]["cost_usd"] = round(daily_map[udate]["cost_usd"] + cost, 6)

    process_list = sorted(by_process_map.values(), key=lambda p: p["total_tokens"], reverse=True)
    trend_list = sorted(daily_map.values(), key=lambda d: d["date"])

    # Client details
    client = db.get(Client, client_id)
    company_name = client.Name if client else "Current Company"

    return {
        "client_id": client_id,
        "company_name": company_name,
        "show_cost": show_cost,
        "summary": {
            "total_requests": total_reqs,
            "total_input_tokens": total_in_tok,
            "total_output_tokens": total_out_tok,
            "total_tokens": total_tok,
            "total_cost_usd": round(total_cost, 6) if show_cost else None,
            "start_date": s_date,
            "end_date": e_date,
        },
        "by_process": process_list,
        "daily_trend": trend_list,
    }
