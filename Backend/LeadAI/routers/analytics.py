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
