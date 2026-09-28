"""
Dashboards.

Two things worth noting:

  * An `agent` gets the same endpoint but scoped to their own assigned
    conversations, so one component serves both the manager dashboard and the
    agent's personal stats without a second API.
  * `ai_containment_rate` is the metric that actually justifies the product: the
    share of conversations the AI handled without ever needing a human. It is the
    inverse of the handoff rate, and it is what moves when the knowledge base
    improves.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_leadai_db
from ..models import (
    Lead,
    LeadCall,
    LeadConversation,
    LeadKbChunk,
    LeadKbDocument,
    LeadUserRole,
    LeadClientRecharge,
)
from ..rbac import Principal, require, resolve_scope
from ..schemas import AnalyticsOut

router = APIRouter(prefix="/analytics", tags=["LeadAI • Analytics"])


def _aware(value: datetime | None) -> datetime:
    """MySQL DATETIME columns come back naive; comparing them to an aware
    'now' raises. Normalise to UTC-aware before any comparison."""
    if value is None:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@router.get("", response_model=AnalyticsOut, summary="Dashboard overview")
def overview(
    days: int = Query(default=7, ge=1, le=90),
    principal: Principal = Depends(require("analytics.read")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)

    conv_q = db.query(LeadConversation).filter(
        LeadConversation.ClientId == client_id,
        LeadConversation.IsDeleted == False,  # noqa: E712
    )
    lead_q = db.query(Lead).filter(
        Lead.ClientId == client_id,
        Lead.IsDeleted == False,  # noqa: E712
    )
    call_q = db.query(LeadCall).filter(
        LeadCall.ClientId == client_id,
        LeadCall.IsDeleted == False,  # noqa: E712
    )

    # Row-level scoping: an agent's dashboard is about their own work.
    if principal.sees_only_assigned:
        me = principal.email.lower()
        conv_q = conv_q.filter(LeadConversation.AssignedUserEmail == me)
        lead_q = lead_q.join(
            LeadConversation, LeadConversation.Id == Lead.ConversationId
        ).filter(LeadConversation.AssignedUserEmail == me)
        call_q = call_q.filter(LeadCall.InitiatedByEmail == me)

    conversations = conv_q.all()
    leads = lead_q.all()
    calls = call_q.all()

    start_of_day = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    counts = {
        s: sum(1 for l in leads if l.Status == s)
        for s in ("cold", "warm", "hot", "qualified", "lost")
    }
    qualified = counts["qualified"]

    completed = [c for c in calls if c.Status in ("completed", "transferred")]
    failed = [c for c in calls if c.Status in ("failed", "no-answer", "busy", "canceled")]

    # Containment: conversations that never reached a human.
    escalated = sum(
        1 for c in conversations if c.Status in ("needs_human", "assigned")
        or c.HandoffReason
    )
    containment = (
        round((len(conversations) - escalated) / len(conversations) * 100, 1)
        if conversations
        else 0.0
    )

    daily = []
    for offset in range(days - 1, -1, -1):
        day = start_of_day - timedelta(days=offset)
        nxt = day + timedelta(days=1)
        daily.append(
            {
                "date": day.date().isoformat(),
                "leads": sum(1 for c in conversations if day <= _aware(c.CreatedAt) < nxt),
                "hot": sum(
                    1
                    for l in leads
                    if l.Status in ("hot", "qualified") and day <= _aware(l.UpdatedAt or l.CreatedAt) < nxt
                ),
                "calls": sum(1 for c in calls if day <= _aware(c.CreatedAt) < nxt),
            }
        )

    agents: list[dict] = []
    if not principal.sees_only_assigned:
        grants = (
            db.query(LeadUserRole)
            .filter(
                LeadUserRole.ClientId == client_id,
                LeadUserRole.IsActive == True,  # noqa: E712
                LeadUserRole.IsDeleted == False,  # noqa: E712
                LeadUserRole.Role.in_(("employee", "manager")),
            )
            .all()
        )
        lead_by_conv = {l.ConversationId: l for l in leads}
        for grant in grants:
            mine = [
                c for c in conversations if c.AssignedUserEmail == grant.UserEmail
            ]
            agents.append(
                {
                    "email": grant.UserEmail,
                    "name": grant.FullName or grant.UserEmail,
                    "role": grant.Role,
                    "assigned": len(mine),
                    "closed": sum(1 for c in mine if c.Status == "closed"),
                    "qualified": sum(
                        1
                        for c in mine
                        if lead_by_conv.get(c.Id) and lead_by_conv[c.Id].Status == "qualified"
                    ),
                    "calls": sum(1 for c in calls if c.InitiatedByEmail == grant.UserEmail),
                }
            )

    channels: dict[str, int] = {}
    for conversation in conversations:
        channels[conversation.Channel] = channels.get(conversation.Channel, 0) + 1

    return AnalyticsOut(
        client_id=client_id,
        leads_today=sum(1 for c in conversations if _aware(c.CreatedAt) >= start_of_day),
        total_leads=len(conversations),
        cold=counts["cold"],
        warm=counts["warm"],
        hot=counts["hot"],
        qualified=qualified,
        assigned=sum(1 for c in conversations if c.AssignedUserEmail),
        unassigned=sum(1 for c in conversations if not c.AssignedUserEmail),
        needs_human=sum(1 for c in conversations if c.Status == "needs_human"),
        closed=sum(1 for c in conversations if c.Status == "closed"),
        calls=len(calls),
        completed_calls=len(completed),
        failed_calls=len(failed),
        avg_call_duration=(
            round(sum(c.DurationSec or 0 for c in completed) / len(completed), 1)
            if completed
            else 0.0
        ),
        conversion_rate=(
            round(qualified / len(conversations) * 100, 1) if conversations else 0.0
        ),
        avg_lead_score=(
            round(sum(l.Score or 0 for l in leads) / len(leads), 1) if leads else 0.0
        ),
        ai_containment_rate=containment,
        documents=db.query(func.count(LeadKbDocument.Id))
        .filter(
            LeadKbDocument.ClientId == client_id,
            LeadKbDocument.IsDeleted == False,  # noqa: E712
        )
        .scalar()
        or 0,
        chunks=db.query(func.count(LeadKbChunk.Id))
        .filter(LeadKbChunk.ClientId == client_id)
        .scalar()
        or 0,
        daily=daily,
        agents=agents,
        channels=channels,
    )


@router.get("/funnel", summary="Lead funnel counts")
def funnel(
    principal: Principal = Depends(require("analytics.read")),
    db: Session = Depends(get_leadai_db),
):
    """Ordered stages, ready to render as a funnel chart without the frontend
    needing to know the ordering rule."""
    client_id = resolve_scope(principal)
    query = db.query(Lead.Status, func.count(Lead.Id)).filter(
        Lead.ClientId == client_id,
        Lead.IsDeleted == False,  # noqa: E712
    )
    if principal.sees_only_assigned:
        query = query.join(
            LeadConversation, LeadConversation.Id == Lead.ConversationId
        ).filter(LeadConversation.AssignedUserEmail == principal.email.lower())

    rows = dict(query.group_by(Lead.Status).all())
    order = ("cold", "warm", "hot", "qualified", "lost")
    return {
        "client_id": client_id,
        "stages": [{"stage": s, "count": int(rows.get(s, 0))} for s in order],
        "total": int(sum(rows.values())),
    }


@router.get("/admin-dashboard", summary="Admin user onboarding & subscription analytics")
def admin_dashboard(
    time_range: str = Query(default="30d", alias="range"),
    principal: Principal = Depends(require("analytics.read")),
    db: Session = Depends(get_leadai_db),
):
    """Aggregates user onboarding velocity and multi-tier plan purchases over time.
    Supports ranges: 7d, 30d, 3m, 6m, 1y.
    """
    days_map = {
        "7d": 7,
        "30d": 30,
        "3m": 90,
        "6m": 180,
        "1y": 365,
    }
    total_days = days_map.get(time_range, 30)

    now = datetime.now(timezone.utc)
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    range_start = start_of_today - timedelta(days=total_days)

    # 1. Total users and new users from LeadUserRole (and Domain.models.User if any)
    users_q = db.query(LeadUserRole).filter(LeadUserRole.IsDeleted == False)  # noqa: E712
    all_users = users_q.all()
    
    # If LeadUserRole is empty, also check Domain User table
    if not all_users:
        try:
            from domain.models import User
            all_users = db.query(User).filter(User.IsDeleted == False).all()  # noqa: E712
        except Exception:
            pass
            
    total_users_count = len(all_users)

    # 2. Plan purchases and active subscriptions from LeadClientRecharge
    recharges_q = db.query(LeadClientRecharge).filter(LeadClientRecharge.IsDeleted == False)  # noqa: E712
    all_recharges = recharges_q.all()
    total_plan_purchases = len(all_recharges)
    active_subscriptions = sum(
        1 for r in all_recharges
        if r.Status == "active" and (not r.ExpiresAt or _aware(r.ExpiresAt) >= now)
    )

    # Generate time buckets
    dates = []
    display_dates = []
    full_dates = []
    onboarding_counts = []
    cumulative_counts = []
    plan_purchases = {
        "Basic": [],
        "Standard": [],
        "Premium": [],
        "Enterprise": [],
    }
    totals = []

    if time_range == "1y":
        # 12 monthly points
        for i in range(11, -1, -1):
            d = now - timedelta(days=i * 30)
            iso = d.strftime("%Y-%m-%d")
            display = d.strftime("%b %y")
            full = d.strftime("%B %Y")
            dates.append(iso)
            display_dates.append(display)
            full_dates.append(full)

            m_start = d.replace(day=1, hour=0, minute=0, second=0)
            m_end = m_start + timedelta(days=32)
            m_end = m_end.replace(day=1)

            u_count = sum(1 for u in all_users if u.CreatedAt and m_start <= _aware(u.CreatedAt) < m_end)
            cum_count = sum(1 for u in all_users if u.CreatedAt and _aware(u.CreatedAt) < m_end)

            onboarding_counts.append(u_count)
            cumulative_counts.append(cum_count)

            b_cnt = sum(1 for r in all_recharges if "basic" in (r.PlanNameSnapshot or "").lower() and r.RechargedAt and m_start <= _aware(r.RechargedAt) < m_end)
            s_cnt = sum(1 for r in all_recharges if "standard" in (r.PlanNameSnapshot or "").lower() and r.RechargedAt and m_start <= _aware(r.RechargedAt) < m_end)
            p_cnt = sum(1 for r in all_recharges if "premium" in (r.PlanNameSnapshot or "").lower() and r.RechargedAt and m_start <= _aware(r.RechargedAt) < m_end)
            e_cnt = sum(1 for r in all_recharges if ("enterprise" in (r.PlanNameSnapshot or "").lower() or "custom" in (r.PlanNameSnapshot or "").lower()) and r.RechargedAt and m_start <= _aware(r.RechargedAt) < m_end)

            plan_purchases["Basic"].append(b_cnt)
            plan_purchases["Standard"].append(s_cnt)
            plan_purchases["Premium"].append(p_cnt)
            plan_purchases["Enterprise"].append(e_cnt)
            totals.append(b_cnt + s_cnt + p_cnt + e_cnt)
    else:
        step = 3 if time_range == "3m" else (7 if time_range == "6m" else 1)
        iterations = total_days // step

        for i in range(iterations - 1, -1, -1):
            d = start_of_today - timedelta(days=i * step)
            nxt = d + timedelta(days=step)
            iso = d.strftime("%Y-%m-%d")
            display = d.strftime("%b %d")
            full = d.strftime("%A, %B %d, %Y")
            dates.append(iso)
            display_dates.append(display)
            full_dates.append(full)

            u_count = sum(1 for u in all_users if u.CreatedAt and d <= _aware(u.CreatedAt) < nxt)
            cum_count = sum(1 for u in all_users if u.CreatedAt and _aware(u.CreatedAt) < nxt)

            onboarding_counts.append(u_count)
            cumulative_counts.append(cum_count)

            b_cnt = sum(1 for r in all_recharges if "basic" in (r.PlanNameSnapshot or "").lower() and r.RechargedAt and d <= _aware(r.RechargedAt) < nxt)
            s_cnt = sum(1 for r in all_recharges if "standard" in (r.PlanNameSnapshot or "").lower() and r.RechargedAt and d <= _aware(r.RechargedAt) < nxt)
            p_cnt = sum(1 for r in all_recharges if "premium" in (r.PlanNameSnapshot or "").lower() and r.RechargedAt and d <= _aware(r.RechargedAt) < nxt)
            e_cnt = sum(1 for r in all_recharges if ("enterprise" in (r.PlanNameSnapshot or "").lower() or "custom" in (r.PlanNameSnapshot or "").lower()) and r.RechargedAt and d <= _aware(r.RechargedAt) < nxt)

            plan_purchases["Basic"].append(b_cnt)
            plan_purchases["Standard"].append(s_cnt)
            plan_purchases["Premium"].append(p_cnt)
            plan_purchases["Enterprise"].append(e_cnt)
            totals.append(b_cnt + s_cnt + p_cnt + e_cnt)

    total_in_range = sum(onboarding_counts)
    all_purchases_sum = sum(totals)

    # Growth rate comparisons with previous window
    prev_range_start = range_start - timedelta(days=total_days)
    prev_new_users = sum(1 for u in all_users if u.CreatedAt and prev_range_start <= _aware(u.CreatedAt) < range_start)
    new_users_growth = (
        round(((total_in_range - prev_new_users) / prev_new_users) * 100, 1)
        if prev_new_users > 0
        else 0.0
    )

    prev_purchases = sum(1 for r in all_recharges if r.RechargedAt and prev_range_start <= _aware(r.RechargedAt) < range_start)
    purchases_growth = (
        round(((all_purchases_sum - prev_purchases) / prev_purchases) * 100, 1)
        if prev_purchases > 0
        else 0.0
    )

    peak = max(onboarding_counts) if onboarding_counts else 0
    peak_date = display_dates[onboarding_counts.index(peak)] if peak > 0 and onboarding_counts else "N/A"

    return {
        "range": time_range,
        "summary": {
            "total_users": total_users_count,
            "total_users_growth_pct": new_users_growth,
            "new_users": total_in_range,
            "new_users_growth_pct": new_users_growth,
            "total_plan_purchases": total_plan_purchases,
            "total_plan_purchases_growth_pct": purchases_growth,
            "active_subscriptions": active_subscriptions,
            "active_subscriptions_growth_pct": 0.0,
            "total_revenue": round(sum(float(r.PricePaid or 0.0) for r in all_recharges), 2),
            "conversion_rate": (
                round((total_plan_purchases / total_users_count) * 100, 1)
                if total_users_count > 0
                else 0.0
            ),
        },
        "onboarding": {
            "dates": dates,
            "display_dates": display_dates,
            "full_dates": full_dates,
            "counts": onboarding_counts,
            "cumulative": cumulative_counts,
            "peak_count": peak,
            "peak_date": peak_date,
            "avg_daily": round(total_in_range / total_days, 1),
            "total_in_range": total_in_range,
        },
        "plan_purchases": {
            "dates": dates,
            "display_dates": display_dates,
            "full_dates": full_dates,
            "plans": plan_purchases,
            "totals": totals,
        },
    }

