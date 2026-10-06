"""
Tenant Onboarding Router for LeadAI.

Provides state tracking, step progression, and subscription verification
for new company user onboarding (Channel Wizard -> Knowledge Base -> Dashboard).
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional
from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_leadai_db
from ..models import (
    LeadClientRecharge,
    LeadCompanyOnboarding,
    LeadKbDocument,
    utcnow,
)
from ..models_ext import LeadChannelAccount
from ..rbac import Principal, current_principal, resolve_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding", tags=["LeadAI • Onboarding"])


class OnboardingStepIn(BaseModel):
    step: str = Field(..., description="'channels' or 'knowledge_base'")
    action: str = Field(..., description="'complete' or 'skip'")
    connected: Optional[bool] = None


class OnboardingStateOut(BaseModel):
    eligible: bool
    has_active_subscription: bool
    status: str
    current_step: str
    is_completed: bool
    channel_connected: bool
    channel_skipped: bool
    kb_added: bool
    kb_skipped: bool
    completed_at: Optional[str] = None
    connected_channels: List[str] = []
    active_plan_channels: List[str] = []
    kb_document_count: int = 0
    client_id: str


def _get_or_create_onboarding_record(
    db: Session, client_id: str
) -> LeadCompanyOnboarding:
    record = (
        db.query(LeadCompanyOnboarding)
        .filter(
            LeadCompanyOnboarding.ClientId == client_id,
            LeadCompanyOnboarding.IsDeleted == False,  # noqa: E712
        )
        .first()
    )
    if record:
        return record

    # Check if this company already has connected channels or KB docs prior to this release
    channel_count = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.ClientId == client_id,
            LeadChannelAccount.IsDeleted == False,  # noqa: E712
            LeadChannelAccount.IsActive == True,  # noqa: E712
        )
        .count()
    )
    kb_count = (
        db.query(LeadKbDocument)
        .filter(
            LeadKbDocument.ClientId == client_id,
            LeadKbDocument.IsDeleted == False,  # noqa: E712
        )
        .count()
    )

    # Existing users with active data default to completed
    if channel_count > 0 or kb_count > 0:
        record = LeadCompanyOnboarding(
            ClientId=client_id,
            Status="onboarding_completed",
            CurrentStep="completed",
            IsCompleted=True,
            ChannelConnected=bool(channel_count > 0),
            KnowledgeBaseAdded=bool(kb_count > 0),
            CompletedAt=utcnow(),
        )
    else:
        record = LeadCompanyOnboarding(
            ClientId=client_id,
            Status="channel_pending",
            CurrentStep="channels",
            IsCompleted=False,
            ChannelConnected=False,
            ChannelSkipped=False,
            KnowledgeBaseAdded=False,
            KnowledgeBaseSkipped=False,
        )

    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("/state", response_model=OnboardingStateOut, summary="Get current onboarding state")
def get_onboarding_state(
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_leadai_db),
):
    """Retrieves onboarding state, verifying subscription and current progress."""
    client_id = resolve_scope(principal)

    # 1. Subscription Check
    is_platform = principal.is_platform_admin
    active_recharge = (
        db.query(LeadClientRecharge)
        .filter(
            LeadClientRecharge.ClientId == client_id,
            LeadClientRecharge.Status.in_(["active", "exhausted"]),
            LeadClientRecharge.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadClientRecharge.CreatedAt.desc())
        .first()
    )
    has_sub = bool(is_platform or active_recharge is not None)
    active_plan_channels: List[str] = (
        active_recharge.ActiveChannels or []
        if active_recharge and active_recharge.ActiveChannels
        else []
    )

    # 2. Onboarding record
    record = _get_or_create_onboarding_record(db, client_id)

    # 3. Dynamic assets sync
    active_channels_db = (
        db.query(LeadChannelAccount.Channel)
        .filter(
            LeadChannelAccount.ClientId == client_id,
            LeadChannelAccount.IsDeleted == False,  # noqa: E712
            LeadChannelAccount.IsActive == True,  # noqa: E712
        )
        .all()
    )
    connected_channels = list({c[0].lower() for c in active_channels_db if c[0]})

    kb_count = (
        db.query(LeadKbDocument)
        .filter(
            LeadKbDocument.ClientId == client_id,
            LeadKbDocument.IsDeleted == False,  # noqa: E712
        )
        .count()
    )

    if connected_channels and not record.ChannelConnected:
        record.ChannelConnected = True
        db.commit()

    if kb_count > 0 and not record.KnowledgeBaseAdded:
        record.KnowledgeBaseAdded = True
        db.commit()

    return OnboardingStateOut(
        eligible=has_sub,
        has_active_subscription=has_sub,
        status=record.Status,
        current_step=record.CurrentStep,
        is_completed=record.IsCompleted,
        channel_connected=record.ChannelConnected,
        channel_skipped=record.ChannelSkipped,
        kb_added=record.KnowledgeBaseAdded,
        kb_skipped=record.KnowledgeBaseSkipped,
        completed_at=record.CompletedAt.isoformat() if record.CompletedAt else None,
        connected_channels=connected_channels,
        active_plan_channels=active_plan_channels,
        kb_document_count=kb_count,
        client_id=client_id,
    )


@router.post("/step", response_model=OnboardingStateOut, summary="Advance or skip an onboarding step")
def advance_onboarding_step(
    payload: OnboardingStepIn,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_leadai_db),
):
    """Records completion or skip of a specific onboarding step."""
    client_id = resolve_scope(principal)
    record = _get_or_create_onboarding_record(db, client_id)

    normalized_step = payload.step.lower().strip()
    normalized_action = payload.action.lower().strip()

    if normalized_step in ("channels", "channel"):
        if normalized_action == "skip":
            record.ChannelSkipped = True
            record.Status = "channel_skipped"
            record.CurrentStep = "knowledge_base"
        else:
            record.ChannelConnected = True
            record.Status = "channel_completed"
            record.CurrentStep = "knowledge_base"

    elif normalized_step in ("knowledge_base", "kb", "knowledgebase"):
        if normalized_action == "skip":
            record.KnowledgeBaseSkipped = True
            record.Status = "onboarding_completed"
            record.CurrentStep = "completed"
            record.IsCompleted = True
            record.CompletedAt = utcnow()
        else:
            record.KnowledgeBaseAdded = True
            record.Status = "onboarding_completed"
            record.CurrentStep = "completed"
            record.IsCompleted = True
            record.CompletedAt = utcnow()

    db.commit()
    db.refresh(record)

    return get_onboarding_state(principal=principal, db=db)


@router.post("/complete", response_model=OnboardingStateOut, summary="Mark onboarding finished")
def complete_onboarding(
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_leadai_db),
):
    """Explicitly concludes the onboarding process."""
    client_id = resolve_scope(principal)
    record = _get_or_create_onboarding_record(db, client_id)

    record.Status = "onboarding_completed"
    record.CurrentStep = "completed"
    record.IsCompleted = True
    record.CompletedAt = utcnow()

    db.commit()
    db.refresh(record)

    return get_onboarding_state(principal=principal, db=db)


@router.post("/reset", response_model=OnboardingStateOut, summary="Reset onboarding state")
def reset_onboarding(
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_leadai_db),
):
    """Allows resetting onboarding for the company to re-run the wizard."""
    client_id = resolve_scope(principal)
    record = _get_or_create_onboarding_record(db, client_id)

    record.Status = "channel_pending"
    record.CurrentStep = "channels"
    record.IsCompleted = False
    record.ChannelConnected = False
    record.ChannelSkipped = False
    record.KnowledgeBaseAdded = False
    record.KnowledgeBaseSkipped = False
    record.CompletedAt = None

    db.commit()
    db.refresh(record)

    return get_onboarding_state(principal=principal, db=db)
