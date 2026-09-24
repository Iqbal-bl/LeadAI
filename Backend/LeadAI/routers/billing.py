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
    RECHARGE_STATUS_ACTIVE,
    RECHARGE_STATUS_PENDING,
    RECHARGE_STATUS_FAILED,
    RECHARGE_STATUS_CANCELLED,
    LeadClientRecharge,
    LeadRechargePlanTemplate,
    LeadUsageLog,
)
from ..rbac import Principal, require, scoped
from ..schemas import (
    BillingSummaryOut,
    ChannelAddonOrderCreate,
    ChannelAddonQuoteOut,
    ChannelAddonVerifyIn,
    ChannelCancelIn,
    ChannelCancelOut,
    ClientRechargeAllocate,
    ClientRechargeOut,
    CustomBundleSubscriptionCreate,
    Ok,
    RazorpayOrderCreate,
    RazorpayOrderOut,
    RazorpayPaymentFailureIn,
    RazorpayPaymentVerifyIn,
    RazorpaySubscriptionCreate,
    RazorpaySubscriptionOut,
    RazorpaySubscriptionVerifyIn,
    RechargePlanTemplateCreate,
    RechargePlanTemplateOut,
    RechargePlanTemplateUpdate,
    SubscriptionCancelOut,
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
        plan_category=getattr(t, "PlanCategory", "voice_standard") or "voice_standard",
        feature_key=getattr(t, "FeatureKey", None),
        target_client_id=t.TargetClientId,
        target_client_ids=getattr(t, "TargetClientIds", None),
        addon_channels=getattr(t, "AddonChannels", None),
        included_minutes=t.IncludedMinutes,
        validity_days=t.ValidityDays,
        price=t.Price,
        rate_per_minute=t.RatePerMinute,
        razorpay_plan_id=getattr(t, "RazorpayPlanId", None),
        auto_pay_by_default=bool(getattr(t, "AutoPayByDefault", True)),
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
        rollover_minutes_carried=float(getattr(r, "RolloverMinutesCarried", 0.0) or 0.0),
        validity_days_snapshot=r.ValidityDaysSnapshot,
        price_paid=r.PricePaid,
        recharged_at=r.RechargedAt,
        expires_at=r.ExpiresAt,
        status=r.Status,
        payment_reference=r.PaymentReference,
        razorpay_order_id=r.RazorpayOrderId,
        razorpay_subscription_id=getattr(r, "RazorpaySubscriptionId", None),
        is_auto_renew=bool(getattr(r, "IsAutoRenew", False)),
        cancel_at_period_end=bool(getattr(r, "CancelAtPeriodEnd", False)),
        active_channels=getattr(r, "ActiveChannels", None) or [c for c in ("whatsapp", "instagram", "facebook", "linkedin") if c in (getattr(r, "PlanNameSnapshot", "") or "").lower()],
        next_cycle_channels=getattr(r, "NextCycleChannels", None) or getattr(r, "ActiveChannels", None) or [c for c in ("whatsapp", "instagram", "facebook", "linkedin") if c in (getattr(r, "PlanNameSnapshot", "") or "").lower()],
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

    has_quota, _, _ = billing_svc.check_call_quota(db, client_id)
    rem_mins = active.RemainingMinutes if active else 0.0

    return BillingSummaryOut(
        client_id=client_id,
        active_recharge=_serialize_recharge(active) if active else None,
        pending_recharges=[],
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

    # Standard global plans OR custom plans targeted to this client_id (excluding on-the-fly client bundles)
    rows = (
        db.query(LeadRechargePlanTemplate)
        .filter(
            LeadRechargePlanTemplate.IsActive == True,  # noqa: E712
            LeadRechargePlanTemplate.IsDeleted == False,  # noqa: E712
            LeadRechargePlanTemplate.PlanCategory != "client_self_bundle",
            ~LeadRechargePlanTemplate.Name.like("Custom Bundle (%"),
        )
        .order_by(LeadRechargePlanTemplate.Price.asc())
        .all()
    )

    matched = []
    for t in rows:
        if not t.TargetClientId and not t.TargetClientIds:
            matched.append(t)
        elif t.TargetClientId == client_id:
            matched.append(t)
        elif t.TargetClientIds and isinstance(t.TargetClientIds, list) and client_id in t.TargetClientIds:
            matched.append(t)

    return [_serialize_template(r) for r in matched]



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
    sub_id = payload.razorpay_subscription_id or payload.subscription_id
    ord_id = payload.razorpay_order_id or payload.order_id
    billing_svc.record_payment_failure(
        db=db,
        client_id=client_id,
        order_id=ord_id,
        subscription_id=sub_id,
        error_code=payload.error_code,
        error_description=payload.error_description,
    )
    return Ok(message="Failure recorded")


@router.post("/create-subscription", response_model=RazorpaySubscriptionOut, summary="Create Razorpay Subscription for auto-renewing recharge")
def create_subscription(
    payload: RazorpaySubscriptionCreate,
    scope: tuple[Principal, str] = Depends(scoped("billing.recharge", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    try:
        res = billing_svc.create_razorpay_subscription(
            db=db,
            client_id=client_id,
            plan_template_id=payload.plan_template_id,
            user_email=principal.email,
        )
        return RazorpaySubscriptionOut(**res)
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err)) from err


@router.post("/verify-subscription", response_model=ClientRechargeOut, summary="Verify subscription payment & activate plan")
def verify_subscription(
    payload: RazorpaySubscriptionVerifyIn,
    scope: tuple[Principal, str] = Depends(scoped("billing.recharge", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    try:
        recharge = billing_svc.verify_razorpay_subscription_payment(
            db=db,
            client_id=client_id,
            payload=payload.model_dump(),
            user_email=principal.email,
        )
        return _serialize_recharge(recharge)
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err)) from err


@router.post("/cancel-subscription", response_model=SubscriptionCancelOut, summary="Cancel AutoPay mandate at cycle end")
def cancel_subscription(
    scope: tuple[Principal, str] = Depends(scoped("billing.recharge", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    try:
        res = billing_svc.cancel_razorpay_subscription(db, client_id)
        return SubscriptionCancelOut(**res)
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err)) from err


@router.post("/custom-bundle/create-subscription", response_model=RazorpaySubscriptionOut, summary="Build your own custom plan subscription")
def create_custom_bundle_subscription(
    payload: CustomBundleSubscriptionCreate,
    scope: tuple[Principal, str] = Depends(scoped("billing.recharge", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    try:
        res = billing_svc.create_custom_bundle_subscription(
            db=db,
            client_id=client_id,
            payload=payload.model_dump(),
            user_email=principal.email,
        )
        return RazorpaySubscriptionOut(**res)
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err)) from err


@router.get("/channel-addon/quote", response_model=ChannelAddonQuoteOut, summary="Get mid-cycle prorated quote for adding a channel")
def get_channel_addon_quote(
    channel: str = Query(..., description="whatsapp, instagram, facebook, linkedin"),
    scope: tuple[Principal, str] = Depends(scoped("billing.read", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    try:
        quote = billing_svc.get_channel_addon_quote(db=db, client_id=client_id, channel=channel)
        return ChannelAddonQuoteOut(**quote)
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err)) from err


@router.post("/channel-addon/create-order", summary="Initiate mid-cycle prorated channel add-on payment")
def create_channel_addon_order(
    payload: ChannelAddonOrderCreate,
    scope: tuple[Principal, str] = Depends(scoped("billing.recharge", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    try:
        res = billing_svc.create_channel_addon_order(
            db=db,
            client_id=client_id,
            channel=payload.channel,
            user_email=principal.email,
        )
        return res
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err)) from err


@router.post("/channel-addon/verify-payment", response_model=ClientRechargeOut, summary="Verify payment and activate channel on active plan")
def verify_channel_addon_payment(
    payload: ChannelAddonVerifyIn,
    scope: tuple[Principal, str] = Depends(scoped("billing.recharge", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    try:
        recharge = billing_svc.verify_channel_addon_payment(
            db=db,
            client_id=client_id,
            payload=payload.model_dump(),
            user_email=principal.email,
        )
        return _serialize_recharge(recharge)
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err)) from err


@router.post("/cancel-channel", response_model=ChannelCancelOut, summary="Cancel an individual channel add-on at cycle end")
def cancel_channel_addon(
    payload: ChannelCancelIn,
    scope: tuple[Principal, str] = Depends(scoped("billing.recharge", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    try:
        res = billing_svc.cancel_channel_for_next_cycle(
            db=db,
            client_id=client_id,
            channel=payload.channel,
        )
        return ChannelCancelOut(**res)
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err)) from err

@router.post("/resume-channel", response_model=ChannelCancelOut, summary="Resume an individual channel add-on renewal (Undo cancellation)")
def resume_channel_addon(
    payload: ChannelCancelIn,
    scope: tuple[Principal, str] = Depends(scoped("billing.recharge", "company.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    try:
        res = billing_svc.resume_channel_for_next_cycle(
            db=db,
            client_id=client_id,
            channel=payload.channel,
        )
        return ChannelCancelOut(**res)
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err)) from err


@router.post("/webhook", summary="Razorpay Webhook for subscription auto-renewal events")
async def razorpay_webhook(
    request: Request,
    db: Session = Depends(get_leadai_db),
):
    try:
        raw_body = await request.body()
        import json
        event_payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON payload")

    signature = request.headers.get("X-Razorpay-Signature")
    try:
        result = billing_svc.handle_razorpay_webhook(
            db=db,
            event_payload=event_payload,
            signature=signature,
            raw_body=raw_body,
        )
        return result
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err)) from err


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

    # Pure read-only query: do not mutate in-flight PENDING or CANCELLED records (Bug #11 fix)
    return [_serialize_recharge(r) for r in rows]


@router.get("/invoices/{recharge_id}/download", summary="Download custom invoice PDF")
async def download_invoice(
    recharge_id: str,
    request: Request,
    token: str | None = Query(default=None),
    db: Session = Depends(get_leadai_db),
):
    """Serve the custom PDF invoice with authentication and tenancy validation."""
    from domain.models import Client
    from ..models import LeadClientRecharge, LeadUserRole, ROLE_ADMIN
    from ..services import invoice as invoice_svc
    from core.token_validation import validate_token_async

    # 1. Resolve auth token from header or query param (for browser tab downloads)
    auth_header = request.headers.get("Authorization")
    raw_token = None
    if auth_header and auth_header.startswith("Bearer "):
        raw_token = auth_header.replace("Bearer ", "").strip()
    elif token:
        raw_token = token.strip()

    if not raw_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication token required to download invoice")

    # 2. Validate token against identity server
    claims = await validate_token_async(raw_token)
    if not claims:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired authentication token")

    caller_email = str(
        claims.get("user_email")
        or claims.get("user_name")
        or claims.get("email")
        or claims.get("preferred_username")
        or claims.get("sub")
        or ""
    ).lower()

    # 3. Locate recharge
    recharge = db.query(LeadClientRecharge).filter(LeadClientRecharge.Id == recharge_id).first()
    if not recharge:
        raise HTTPException(status_code=404, detail="Invoice / Recharge transaction not found.")

    # 4. Enforce tenant ownership / Super Admin access / Owner access
    is_admin = (
        str(claims.get("role", "")).lower() in ("admin", "superadmin")
        or db.query(LeadUserRole).filter(
            LeadUserRole.UserEmail == caller_email,
            LeadUserRole.Role.in_([ROLE_ADMIN, "admin"]),
            LeadUserRole.IsActive == True,
            LeadUserRole.IsDeleted == False,
        ).first() is not None
    )

    if not is_admin:
        is_creator = bool(recharge.CreatedBy and recharge.CreatedBy.lower() == caller_email)
        has_client_access = db.query(LeadUserRole).filter(
            LeadUserRole.UserEmail == caller_email,
            LeadUserRole.ClientId == recharge.ClientId,
            LeadUserRole.IsActive == True,
            LeadUserRole.IsDeleted == False,
        ).first() is not None

        if not is_creator and not has_client_access:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission to access this invoice.")

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
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.post("/recharge", response_model=ClientRechargeOut, summary="Super Admin: Direct recharge grant to a client")
def self_recharge(
    payload: ClientRechargeAllocate,
    principal: Principal = Depends(require("billing.manage_global")),
    db: Session = Depends(get_leadai_db),
):
    if not payload.client_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "client_id is required for direct recharge allocation")
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
    include_deleted: bool = Query(False, description="Include soft-deleted plans"),
    principal: Principal = Depends(require("billing.manage_global")),
    db: Session = Depends(get_leadai_db),
):
    billing_svc.ensure_default_templates(db)
    query = db.query(LeadRechargePlanTemplate)
    if not include_deleted:
        query = query.filter(LeadRechargePlanTemplate.IsDeleted == False)
    # Filter out client self-serve dynamic custom bundles from master plan templates
    query = query.filter(LeadRechargePlanTemplate.PlanCategory != "client_self_bundle")
    query = query.filter(~LeadRechargePlanTemplate.Name.like("Custom Bundle (%"))
    rows = query.order_by(LeadRechargePlanTemplate.CreatedAt.desc()).all()
    return [_serialize_template(r) for r in rows]


@admin_router.post("/plans", response_model=RechargePlanTemplateOut, summary="Admin: Create standard or client custom plan")
def admin_create_plan(
    payload: RechargePlanTemplateCreate,
    principal: Principal = Depends(require("billing.manage_global")),
    db: Session = Depends(get_leadai_db),
):
    target_single = payload.target_client_id.strip() if payload.target_client_id else None
    if not target_single and payload.target_client_ids:
        target_single = payload.target_client_ids[0]

    template = LeadRechargePlanTemplate(
        Name=payload.name.strip(),
        PlanType=payload.plan_type.strip(),
        PlanCategory=getattr(payload, "plan_category", "voice_standard") or "voice_standard",
        FeatureKey=payload.feature_key.strip() if getattr(payload, "feature_key", None) else None,
        TargetClientId=target_single,
        TargetClientIds=payload.target_client_ids,
        AddonChannels=payload.addon_channels,
        AutoPayByDefault=getattr(payload, "auto_pay_by_default", True),
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
    if payload.target_client_id is not None:
        template.TargetClientId = payload.target_client_id.strip() if payload.target_client_id else None
    if payload.target_client_ids is not None:
        template.TargetClientIds = payload.target_client_ids
    if payload.addon_channels is not None:
        template.AddonChannels = payload.addon_channels
    if payload.auto_pay_by_default is not None:
        template.AutoPayByDefault = payload.auto_pay_by_default
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


@admin_router.delete("/plans/{plan_id}", response_model=Ok, summary="Admin: Soft-delete / retire plan template")
def admin_delete_plan(
    plan_id: str,
    principal: Principal = Depends(require("billing.manage_global")),
    db: Session = Depends(get_leadai_db),
):
    template = db.get(LeadRechargePlanTemplate, plan_id)
    if not template or template.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan template not found")

    template.IsDeleted = True
    template.IsActive = False
    template.UpdatedBy = principal.email

    db.add(template)
    db.commit()
    logger.info(f"[Admin Billing] Soft-deleted plan template {template.Id} ({template.Name}) by {principal.email}")
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
        )
        return _serialize_recharge(recharge)
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err)) from err


@admin_router.get("/clients-summary", response_model=list[BillingSummaryOut], summary="Admin: System-wide client billing statuses")
def admin_clients_summary(
    principal: Principal = Depends(require("billing.manage_global")),
    db: Session = Depends(get_leadai_db),
):
    from domain.models import Client

    clients = db.query(Client).all()
    summaries = []
    for client in clients:
        active = billing_svc.get_active_recharge(db, client.Id)
        has_quota, _, _ = billing_svc.check_call_quota(db, client.Id)
        rem_mins = active.RemainingMinutes if active else 0.0

        summaries.append(
            BillingSummaryOut(
                client_id=client.Id,
                active_recharge=_serialize_recharge(active) if active else None,
                pending_recharges=[],
                total_remaining_minutes=rem_mins,
                is_quota_active=has_quota,
            )
        )
    return summaries
