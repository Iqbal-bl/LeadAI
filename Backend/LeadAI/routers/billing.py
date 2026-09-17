"""
Billing & Prepaid Recharge Router for LeadAI.

Exposes REST APIs for tenant billing dashboards, self-service recharges,
minute usage ledgers, and Super Admin master plan template management.
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from ..db import get_leadai_db
from ..models import (
    PLAN_TYPE_CUSTOM,
    PLAN_TYPE_STANDARD,
    PLAN_TYPE_TOPUP,
    RECHARGE_STATUS_ACTIVE,
    RECHARGE_STATUS_PENDING,
    LeadClientRecharge,
    LeadRechargePlanTemplate,
    LeadUsageLog,
    utcnow,
)
from ..rbac import Principal, require, scoped
from ..schemas import (
    BillingSummaryOut,
    ClientRechargeAllocate,
    ClientRechargeOut,
    Ok,
    RazorpayOrderCreate,
    RazorpayOrderOut,
    RazorpayPaymentFailureIn,
    RazorpayPaymentVerifyIn,
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
        razorpay_order_id=r.RazorpayOrderId,
        invoice_url=r.InvoiceUrl,
        invoice_id=r.InvoiceId,
        failure_reason=r.FailureReason,
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
            LeadRechargePlanTemplate.IsDeleted == False,  # noqa: E712
            (LeadRechargePlanTemplate.TargetClientId == None) | (LeadRechargePlanTemplate.TargetClientId == client_id),  # noqa: E711
        )
        .order_by(LeadRechargePlanTemplate.Price.asc())
        .all()
    )

    return [_serialize_template(r) for r in rows]


@router.post("/create-order", response_model=RazorpayOrderOut, summary="Create Razorpay Order for self-recharge")
def create_order(
    payload: RazorpayOrderCreate,
    scope: tuple[Principal, str] = Depends(scoped("billing.recharge", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    try:
        res = billing_svc.create_razorpay_order(
            db=db,
            client_id=client_id,
            plan_template_id=payload.plan_template_id,
            user_email=principal.email,
        )
        return RazorpayOrderOut(**res)
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err)) from err


@router.post("/verify-payment", response_model=ClientRechargeOut, summary="Verify Razorpay payment signature & activate plan")
def verify_payment(
    payload: RazorpayPaymentVerifyIn,
    scope: tuple[Principal, str] = Depends(scoped("billing.recharge", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    try:
        recharge = billing_svc.verify_razorpay_payment(
            db=db,
            client_id=client_id,
            payload=payload.model_dump(),
            user_email=principal.email,
        )
        return _serialize_recharge(recharge)
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err)) from err


@router.post("/record-failure", response_model=Ok, summary="Log failed or cancelled payment attempt")
def record_failure(
    payload: RazorpayPaymentFailureIn,
    scope: tuple[Principal, str] = Depends(scoped("billing.recharge", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    billing_svc.record_payment_failure(
        db=db,
        client_id=client_id,
        order_id=payload.razorpay_order_id,
        error_code=payload.error_code,
        error_description=payload.error_description,
    )
    return Ok(message="Failure recorded")


@router.get("/payment-history", response_model=list[ClientRechargeOut], summary="List all transaction attempts and recharges with invoice links")
def get_payment_history(
    limit: int = Query(default=100, ge=1, le=500),
    scope: tuple[Principal, str] = Depends(scoped("billing.read", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    rows = (
        db.query(LeadClientRecharge)
        .filter(LeadClientRecharge.ClientId == client_id)
        .order_by(LeadClientRecharge.CreatedAt.desc())
        .limit(limit)
        .all()
    )
    return [_serialize_recharge(r) for r in rows]


@router.get("/invoices/{recharge_id}/download", summary="Download custom invoice PDF")
def download_invoice(
    recharge_id: str,
    db: Session = Depends(get_leadai_db),
):
    """Serve the custom PDF invoice for any verified recharge transaction."""
    from Domain.models import Client
    from ..models import LeadClientRecharge
    from ..services import invoice as invoice_svc

    recharge = db.query(LeadClientRecharge).filter(LeadClientRecharge.Id == recharge_id).first()
    if not recharge:
        raise HTTPException(status_code=404, detail="Invoice / Recharge transaction not found.")

    client = db.query(Client).filter(Client.Id == recharge.ClientId).first()
    html_content = invoice_svc.render_invoice_html(recharge, client)
    pdf_bytes = invoice_svc.generate_invoice_pdf(html_content)

    inv_number = recharge.InvoiceId or invoice_svc.build_invoice_number(recharge.Id, recharge.CreatedAt)
    inv_filename = f"LeadAI_{inv_number}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{inv_filename}"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.post("/recharge", response_model=ClientRechargeOut, summary="Purchase / apply a recharge plan")
def self_recharge(
    payload: ClientRechargeAllocate,
    scope: tuple[Principal, str] = Depends(scoped("billing.recharge", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    principal, scoped_client_id = scope
    target_client_id = payload.client_id or scoped_client_id
    try:
        recharge = billing_svc.allocate_recharge(
            db=db,
            client_id=target_client_id,
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
    return [
        UsageLogOut(
            id=r.Id,
            client_id=r.ClientId,
            recharge_id=r.RechargeId,
            call_sid=r.CallSid,
            conversation_id=r.ConversationId,
            call_duration_seconds=r.CallDurationSeconds,
            minutes_deducted=r.MinutesDeducted,
            previous_balance=r.PreviousBalance,
            new_balance=r.NewBalance,
            deducted_at=r.DeductedAt,
        )
        for r in rows
    ]


# ===========================================================================
# Super Admin Billing & Template Management Endpoints
# ===========================================================================

@admin_router.get("/plans", response_model=list[RechargePlanTemplateOut], summary="Admin: List all plan templates")
def admin_list_plan_templates(
    include_inactive: bool = Query(default=False),
    principal: Principal = Depends(require("billing.manage_global")),
    db: Session = Depends(get_leadai_db),
):
    billing_svc.ensure_default_templates(db)
    q = db.query(LeadRechargePlanTemplate).filter(LeadRechargePlanTemplate.IsDeleted == False)  # noqa: E712
    if not include_inactive:
        q = q.filter(LeadRechargePlanTemplate.IsActive == True)  # noqa: E712
    rows = q.order_by(LeadRechargePlanTemplate.Price.asc()).all()
    return [_serialize_template(r) for r in rows]


@admin_router.post("/plans", response_model=RechargePlanTemplateOut, summary="Admin: Create a new plan template")
def admin_create_plan_template(
    payload: RechargePlanTemplateCreate,
    principal: Principal = Depends(require("billing.manage_global")),
    db: Session = Depends(get_leadai_db),
):
    billing_svc.ensure_default_templates(db)
    template = LeadRechargePlanTemplate(
        Name=payload.name,
        PlanType=payload.plan_type,
        PlanCategory=payload.plan_type or "standard",
        TargetClientId=payload.target_client_id,
        IncludedMinutes=payload.included_minutes,
        ValidityDays=payload.validity_days,
        Price=payload.price,
        RatePerMinute=payload.rate_per_minute,
        Description=payload.description,
        IsActive=True,
        CreatedBy=principal.email,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    logger.info(f"[Billing] Admin {principal.email} created plan template {template.Name} ({template.Id})")
    return _serialize_template(template)


@admin_router.put("/plans/{template_id}", response_model=RechargePlanTemplateOut, summary="Admin: Update a plan template")
@admin_router.patch("/plans/{template_id}", response_model=RechargePlanTemplateOut, summary="Admin: Patch a plan template")
def admin_update_plan_template(
    template_id: str,
    payload: RechargePlanTemplateUpdate,
    principal: Principal = Depends(require("billing.manage_global")),
    db: Session = Depends(get_leadai_db),
):
    template = db.get(LeadRechargePlanTemplate, template_id)
    if not template or template.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan template not found")

    if payload.name is not None:
        template.Name = payload.name
    if payload.plan_type is not None:
        template.PlanType = payload.plan_type
    if payload.target_client_id is not None:
        template.TargetClientId = payload.target_client_id
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
        template.Description = payload.description

    template.UpdatedBy = principal.email
    template.UpdatedAt = utcnow()
    db.commit()
    db.refresh(template)
    return _serialize_template(template)


@admin_router.delete("/plans/{template_id}", response_model=Ok, summary="Admin: Retire a plan template")
def admin_delete_plan_template(
    template_id: str,
    principal: Principal = Depends(require("billing.manage_global")),
    db: Session = Depends(get_leadai_db),
):
    template = db.get(LeadRechargePlanTemplate, template_id)
    if not template or template.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan template not found")

    template.IsDeleted = True
    template.IsActive = False
    template.UpdatedBy = principal.email
    template.UpdatedAt = utcnow()
    db.commit()
    return Ok(message="Plan template retired successfully. Existing client recharges remain active.")


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
            is_topup=payload.is_topup,
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
