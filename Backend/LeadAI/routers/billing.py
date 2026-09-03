"""
Billing & Prepaid Recharge Router for LeadAI.

Exposes REST APIs for tenant billing dashboards, self-service recharges,
minute usage ledgers, and Super Admin master plan template management.
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from ..db import get_leadai_db
from ..models import (
    PLAN_TYPE_CUSTOM,
    PLAN_TYPE_STANDARD,
    RECHARGE_STATUS_ACTIVE,
    RECHARGE_STATUS_PENDING,
    LeadClientRecharge,
    LeadRechargePlanTemplate,
    LeadUsageLog,
)
from ..rbac import Principal, require, scoped
from ..schemas import (
    BillingSummaryOut,
    ClientRechargeAllocate,
    ClientRechargeOut,
    Ok,
    RechargePlanTemplateCreate,
    RechargePlanTemplateOut,
    RechargePlanTemplateUpdate,
    UsageLogOut,
)
from ..services import billing as billing_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["LeadAI • Billing"])
admin_router = APIRouter(prefix="/admin/billing", tags=["LeadAI • Admin Billing"])


def _serialize_template(t: LeadRechargePlanTemplate) -> RechargePlanTemplateOut:
    return RechargePlanTemplateOut(
        id=t.Id,
        name=t.Name,
        plan_type=t.PlanType,
        target_client_id=t.TargetClientId,
        included_minutes=t.IncludedMinutes,
        validity_days=t.ValidityDays,
        price=t.Price,
        rate_per_minute=t.RatePerMinute,
        is_active=t.IsActive,
        description=t.Description,
        created_at=t.CreatedAt,
    )


def _serialize_recharge(r: LeadClientRecharge) -> ClientRechargeOut:
    return ClientRechargeOut(
        id=r.Id,
        client_id=r.ClientId,
        plan_template_id=r.PlanTemplateId,
        plan_name_snapshot=r.PlanNameSnapshot,
        purchased_minutes=r.PurchasedMinutes,
        remaining_minutes=r.RemainingMinutes,
        validity_days_snapshot=r.ValidityDaysSnapshot,
        price_paid=r.PricePaid,
        recharged_at=r.RechargedAt,
        expires_at=r.ExpiresAt,
        status=r.Status,
        payment_reference=r.PaymentReference,
        created_at=r.CreatedAt,
    )


def _serialize_usage(u: LeadUsageLog) -> UsageLogOut:
    return UsageLogOut(
        id=u.Id,
        client_id=u.ClientId,
        recharge_id=u.RechargeId,
        call_sid=u.CallSid,
        conversation_id=u.ConversationId,
        call_duration_seconds=u.CallDurationSeconds,
        minutes_deducted=u.MinutesDeducted,
        previous_balance=u.PreviousBalance,
        new_balance=u.NewBalance,
        deducted_at=u.DeductedAt,
    )


# ===========================================================================
# Tenant Billing Endpoints
# ===========================================================================

@router.get("/current-plan", response_model=BillingSummaryOut, summary="Get company active recharge & balance")
def get_current_plan(
    scope: tuple[Principal, str] = Depends(scoped("billing.read", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    active = billing_svc.get_active_recharge(db, client_id)

    pending_rows = (
        db.query(LeadClientRecharge)
        .filter(
            LeadClientRecharge.ClientId == client_id,
            LeadClientRecharge.Status == RECHARGE_STATUS_PENDING,
        )
        .order_by(LeadClientRecharge.CreatedAt.asc())
        .all()
    )

    has_quota, _, _ = billing_svc.check_call_quota(db, client_id)
    rem_mins = active.RemainingMinutes if active else 0.0

    return BillingSummaryOut(
        client_id=client_id,
        active_recharge=_serialize_recharge(active) if active else None,
        pending_recharges=[_serialize_recharge(p) for p in pending_rows],
        total_remaining_minutes=rem_mins,
        is_quota_active=has_quota,
    )


@router.get("/available-plans", response_model=list[RechargePlanTemplateOut], summary="List available recharge plans for company")
def list_available_plans(
    scope: tuple[Principal, str] = Depends(scoped("billing.read", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    billing_svc.ensure_default_templates(db)

    # Standard global plans OR custom plans targeted to this client_id
    rows = (
        db.query(LeadRechargePlanTemplate)
        .filter(
            LeadRechargePlanTemplate.IsActive == True,  # noqa: E712
            (LeadRechargePlanTemplate.TargetClientId == None) | (LeadRechargePlanTemplate.TargetClientId == client_id),  # noqa: E711
        )
        .order_by(LeadRechargePlanTemplate.Price.asc())
        .all()
    )

    return [_serialize_template(r) for r in rows]


@router.post("/recharge", response_model=ClientRechargeOut, summary="Purchase / apply a recharge plan")
def self_recharge(
    payload: ClientRechargeAllocate,
    scope: tuple[Principal, str] = Depends(scoped("billing.recharge")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    try:
        recharge = billing_svc.allocate_recharge(
            db=db,
            client_id=client_id,
            template_id=payload.plan_template_id,
            custom_minutes=payload.custom_minutes,
            custom_validity_days=payload.custom_validity_days,
            custom_price=payload.custom_price,
            custom_name=payload.custom_name,
            payment_ref=payload.payment_reference,
            created_by=principal.email,
        )
        return _serialize_recharge(recharge)
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err)) from err


@router.get("/usage-history", response_model=list[UsageLogOut], summary="View itemized call minute usage logs")
def get_usage_history(
    limit: int = Query(default=50, ge=1, le=500),
    scope: tuple[Principal, str] = Depends(scoped("billing.read", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    rows = (
        db.query(LeadUsageLog)
        .filter(LeadUsageLog.ClientId == client_id)
        .order_by(LeadUsageLog.DeductedAt.desc())
        .limit(limit)
        .all()
    )
    return [_serialize_usage(r) for r in rows]


# ===========================================================================
# Super Admin Billing Endpoints
# ===========================================================================

@admin_router.get("/plans", response_model=list[RechargePlanTemplateOut], summary="Admin: List all master plan templates")
def admin_list_plans(
    principal: Principal = Depends(require("billing.manage_global")),
    db: Session = Depends(get_leadai_db),
):
    billing_svc.ensure_default_templates(db)
    rows = db.query(LeadRechargePlanTemplate).order_by(LeadRechargePlanTemplate.CreatedAt.desc()).all()
    return [_serialize_template(r) for r in rows]


@admin_router.post("/plans", response_model=RechargePlanTemplateOut, summary="Admin: Create standard or client custom plan")
def admin_create_plan(
    payload: RechargePlanTemplateCreate,
    principal: Principal = Depends(require("billing.manage_global")),
    db: Session = Depends(get_leadai_db),
):
    template = LeadRechargePlanTemplate(
        Name=payload.name.strip(),
        PlanType=payload.plan_type.strip(),
        TargetClientId=payload.target_client_id.strip() if payload.target_client_id else None,
        IncludedMinutes=payload.included_minutes,
        ValidityDays=payload.validity_days,
        Price=payload.price,
        RatePerMinute=payload.rate_per_minute,
        Description=payload.description.strip() if payload.description else None,
        CreatedBy=principal.email,
        IsActive=True,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    logger.info(f"[Admin Billing] Created plan template {template.Id} ({template.Name}) by {principal.email}")
    return _serialize_template(template)


@admin_router.put("/plans/{plan_id}", response_model=RechargePlanTemplateOut, summary="Admin: Update plan template (Edits future recharges only)")
def admin_update_plan(
    plan_id: str,
    payload: RechargePlanTemplateUpdate,
    principal: Principal = Depends(require("billing.manage_global")),
    db: Session = Depends(get_leadai_db),
):
    template = db.get(LeadRechargePlanTemplate, plan_id)
    if not template:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan template not found")

    if payload.name is not None:
        template.Name = payload.name.strip()
    if payload.included_minutes is not None:
        template.IncludedMinutes = payload.included_minutes
    if payload.validity_days is not None:
        template.ValidityDays = payload.validity_days
    if payload.price is not None:
        template.Price = payload.price
    if payload.rate_per_minute is not None:
        template.RatePerMinute = payload.rate_per_minute
    if payload.is_active is not None:
        template.IsActive = payload.is_active
    if payload.description is not None:
        template.Description = payload.description.strip()

    template.UpdatedBy = principal.email

    db.add(template)
    db.commit()
    db.refresh(template)
    logger.info(f"[Admin Billing] Updated plan template {template.Id} ({template.Name}) by {principal.email}")
    return _serialize_template(template)


@admin_router.post("/recharge-client", response_model=ClientRechargeOut, summary="Admin: Direct recharge grant to a client account")
def admin_recharge_client(
    payload: ClientRechargeAllocate,
    principal: Principal = Depends(require("billing.manage_global")),
    db: Session = Depends(get_leadai_db),
):
    try:
        recharge = billing_svc.allocate_recharge(
            db=db,
            client_id=payload.client_id,
            template_id=payload.plan_template_id,
            custom_minutes=payload.custom_minutes,
            custom_validity_days=payload.custom_validity_days,
            custom_price=payload.custom_price,
            custom_name=payload.custom_name,
            payment_ref=payload.payment_reference or f"Admin Grant ({principal.email})",
            created_by=principal.email,
        )
        return _serialize_recharge(recharge)
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err)) from err


@admin_router.get("/clients-summary", response_model=list[BillingSummaryOut], summary="Admin: System-wide client billing statuses")
def admin_clients_summary(
    principal: Principal = Depends(require("billing.manage_global")),
    db: Session = Depends(get_leadai_db),
):
    from Domain.models import Client

    clients = db.query(Client).all()
    summaries = []
    for client in clients:
        active = billing_svc.get_active_recharge(db, client.Id)
        pending_rows = (
            db.query(LeadClientRecharge)
            .filter(
                LeadClientRecharge.ClientId == client.Id,
                LeadClientRecharge.Status == RECHARGE_STATUS_PENDING,
            )
            .all()
        )
        has_quota, _, _ = billing_svc.check_call_quota(db, client.Id)
        rem_mins = active.RemainingMinutes if active else 0.0

        summaries.append(
            BillingSummaryOut(
                client_id=client.Id,
                active_recharge=_serialize_recharge(active) if active else None,
                pending_recharges=[_serialize_recharge(p) for p in pending_rows],
                total_remaining_minutes=rem_mins,
                is_quota_active=has_quota,
            )
        )
    return summaries
