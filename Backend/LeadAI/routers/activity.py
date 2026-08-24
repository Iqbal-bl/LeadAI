"""
The activity log (read side) and system health.

The log is append-only by design: there is no update or delete endpoint, and none
should be added. An audit trail an admin can edit is not an audit trail.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_leadai_db
from ..models import LeadActivityLog
from ..rbac import Principal, require, resolve_scope
from ..schemas import ActivityListOut, HealthOut
from ..serializers import activity_out
from ..services import cache, embeddings, llm, telephony, vectorstore

router = APIRouter(tags=["LeadAI • Activity & system"])


@router.get(
    "/activity",
    response_model=ActivityListOut,
    summary="Read the activity / audit log",
)
def list_activity(
    action: str | None = Query(default=None, description="Exact action name"),
    action_prefix: str | None = Query(
        default=None, description="e.g. 'lead.' or 'kb.' to filter a whole area"
    ),
    log_type: str | None = Query(default=None, pattern="^(Info|Warning|Error|Security)$"),
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_email: str | None = None,
    since: datetime | None = Query(default=None, description="ISO timestamp"),
    until: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require("activity.read")),
    db: Session = Depends(get_leadai_db),
):
    query = db.query(LeadActivityLog)

    # Platform admins may read across companies only when they have not selected
    # one; otherwise the log is company-scoped like everything else.
    if principal.is_platform_admin and not principal.client_id:
        pass
    else:
        query = query.filter(LeadActivityLog.ClientId == resolve_scope(principal))

    if action:
        query = query.filter(LeadActivityLog.Action == action)
    if action_prefix:
        query = query.filter(LeadActivityLog.Action.like(f"{action_prefix}%"))
    if log_type:
        query = query.filter(LeadActivityLog.LogType == log_type)
    if entity_type:
        query = query.filter(LeadActivityLog.EntityType == entity_type)
    if entity_id:
        query = query.filter(LeadActivityLog.EntityId == entity_id)
    if actor_email:
        query = query.filter(LeadActivityLog.ActorEmail == actor_email.lower())
    if since:
        query = query.filter(LeadActivityLog.CreatedAt >= since)
    if until:
        query = query.filter(LeadActivityLog.CreatedAt <= until)

    total = query.count()
    rows = (
        query.order_by(LeadActivityLog.CreatedAt.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
        .all()
    )
    return ActivityListOut(
        total_items=total,
        page=page,
        page_size=page_size,
        items=[activity_out(r) for r in rows],
    )


@router.get(
    "/activity/actions",
    summary="Distinct action names present in the log (for filter dropdowns)",
)
def activity_actions(
    principal: Principal = Depends(require("activity.read")),
    db: Session = Depends(get_leadai_db),
):
    query = db.query(LeadActivityLog.Action).distinct()
    if not (principal.is_platform_admin and not principal.client_id):
        query = query.filter(LeadActivityLog.ClientId == resolve_scope(principal))
    return {"actions": sorted(a for (a,) in query.all() if a)}


@router.get(
    "/health",
    response_model=HealthOut,
    summary="Which backing services are live vs in fallback",
    # Public: this is how a deployment is verified before anyone can log in.
)
def health():
    """Shows the real state of every dependency. Deliberately exposes MODES, not
    credentials — 'llm: builtin-extractive' tells an operator the OpenAI key is
    missing without revealing anything sensitive."""
    return HealthOut(
        status="ok",
        llm=llm.provider(),
        llm_model=llm.model_name(),
        embeddings=embeddings.backend(),
        embedding_model=embeddings.active_model(),
        vector_store=vectorstore.backend(),
        telephony=telephony.status_report(),
        tables={
            "cache": cache.backend(),
            "retrieval_top_k": settings.retrieval_top_k,
            "handoff_threshold": settings.handoff_confidence_threshold,
            "api_prefix": settings.api_prefix,
        },
    )
