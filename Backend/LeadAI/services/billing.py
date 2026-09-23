"""
Prepaid Billing & Call Quota Management Service for LeadAI.

Handles plan template seeding, active recharge resolution, exact-second usage
deductions, pending plan queueing, and immediate call termination upon quota exhaustion.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

import razorpay

from ..config import settings
from ..models import (
    PLAN_CATEGORY_CHANNEL_ADDON,
    PLAN_CATEGORY_VOICE_STANDARD,
    PLAN_CATEGORY_VOICE_TOPUP,
    PLAN_TYPE_CUSTOM,
    PLAN_TYPE_STANDARD,
    PLAN_TYPE_TOPUP,
    RECHARGE_STATUS_ACTIVE,
    RECHARGE_STATUS_CANCELLED,
    RECHARGE_STATUS_EXHAUSTED,
    RECHARGE_STATUS_EXPIRED,
    RECHARGE_STATUS_FAILED,
    RECHARGE_STATUS_PENDING,
    RECHARGE_STATUS_SUPERSEDED,
    LeadClientRecharge,
    LeadRechargePlanTemplate,
    LeadUsageLog,
    utcnow,
)

logger = logging.getLogger(__name__)

# Standard industry benchmarks for modular channel add-ons
ADDON_BENCHMARKS = {
    "whatsapp": 1499.0,
    "instagram": 799.0,
    "facebook": 799.0,
    "linkedin": 1999.0,
}


# Standard Default Plans (Seeded if missing)
DEFAULT_PLANS = [
    {
        "name": "Monthly Standard (500 Mins)",
        "plan_type": PLAN_TYPE_STANDARD,
        "included_minutes": 500.0,
        "validity_days": 30,
        "price": 2000.0,
        "rate_per_minute": 4.0,
        "description": "30 days validity with 500 minutes of AI voice calling.",
    },
    {
        "name": "Yearly Standard (6000 Mins)",
        "plan_type": PLAN_TYPE_STANDARD,
        "included_minutes": 6000.0,
        "validity_days": 365,
        "price": 24000.0,
        "rate_per_minute": 4.0,
        "description": "365 days validity with 6,000 minutes of AI voice calling.",
    },
]


def ensure_default_templates(db: Session) -> None:
    """Ensure standard 1-month and 1-year templates exist in DB."""
    try:
        for plan_def in DEFAULT_PLANS:
            existing = (
                db.query(LeadRechargePlanTemplate)
                .filter(
                    LeadRechargePlanTemplate.Name == plan_def["name"],
                    LeadRechargePlanTemplate.PlanType == PLAN_TYPE_STANDARD,
                    LeadRechargePlanTemplate.TargetClientId == None,  # noqa: E711
                )
                .first()
            )
            if not existing:
                template = LeadRechargePlanTemplate(
                    Name=plan_def["name"],
                    PlanType=plan_def["plan_type"],
                    PlanCategory=PLAN_CATEGORY_VOICE_STANDARD,
                    TargetClientId=None,
                    IncludedMinutes=plan_def["included_minutes"],
                    ValidityDays=plan_def["validity_days"],
                    Price=plan_def["price"],
                    RatePerMinute=plan_def["rate_per_minute"],
                    Description=plan_def["description"],
                    IsActive=True,
                )
                db.add(template)
            elif not existing.PlanCategory:
                existing.PlanCategory = PLAN_CATEGORY_VOICE_STANDARD
                db.add(existing)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning(f"[Billing] Could not seed default templates: {exc}")


def _is_expired(expires_at: Optional[datetime], now: datetime) -> bool:
    """Safely compare DB datetime with current time without offset-naive/aware TypeError."""
    if not expires_at:
        return False
    exp = expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at
    n = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
    return exp <= n


def compute_cycle_expiry(start_time: Optional[datetime] = None, validity_days: int = 30) -> datetime:
    """Computes cycle expiry according to Manager's Rule:
    Starts on Day D of Month M, ends on Day D-1 of Month M+1 at 23:59:59.
    e.g., 17th Sept -> 16th Oct 23:59:59.
    Yearly: 17th Sept 2026 -> 16th Sept 2027 23:59:59.
    """
    from dateutil.relativedelta import relativedelta
    st = start_time or utcnow()
    if st.tzinfo is None:
        st = st.replace(tzinfo=timezone.utc)
    if validity_days >= 360:
        target = st + relativedelta(years=1) - timedelta(days=1)
    else:
        target = st + relativedelta(months=1) - timedelta(days=1)
    return target.replace(hour=23, minute=59, second=59, microsecond=0)



def get_active_recharge(db: Session, client_id: str) -> Optional[LeadClientRecharge]:
    """Retrieve current active recharge for a client.
    
    Exclusivity rule: A client has at most one active base plan.
    Queued/pending automatic resolution is removed per business rules.
    """
    ensure_default_templates(db)
    now = utcnow()

    # 1. Check current active plan (including unexpired plans whose voice quota was 0)
    active = (
        db.query(LeadClientRecharge)
        .filter(
            LeadClientRecharge.ClientId == client_id,
            LeadClientRecharge.Status.in_([RECHARGE_STATUS_ACTIVE, RECHARGE_STATUS_EXHAUSTED]),
        )
        .order_by(LeadClientRecharge.CreatedAt.desc())
        .first()
    )

    if active:
        # Check expiration
        is_expired = active.ExpiresAt and _is_expired(active.ExpiresAt, now)

        if is_expired:
            active.Status = RECHARGE_STATUS_EXPIRED
            db.add(active)
            db.commit()
            active = None
        else:
            # If previously marked exhausted in legacy data, restore to ACTIVE because
            # calendar validity is still unexpired (social channels remain operational)
            if active.Status != RECHARGE_STATUS_ACTIVE:
                active.Status = RECHARGE_STATUS_ACTIVE
                db.add(active)
                db.commit()

            # Auto-repair ActiveChannels if not populated on active record
            if not active.ActiveChannels:
                template = db.get(LeadRechargePlanTemplate, active.PlanTemplateId) if active.PlanTemplateId else None
                if template and template.AddonChannels:
                    active.ActiveChannels = list(template.AddonChannels)
                    active.NextCycleChannels = list(template.AddonChannels)
                    db.add(active)
                    db.commit()
                elif active.PlanNameSnapshot:
                    low_name = active.PlanNameSnapshot.lower()
                    found = [c for c in ("whatsapp", "instagram", "facebook", "linkedin") if c in low_name]
                    if found:
                        active.ActiveChannels = found
                        active.NextCycleChannels = found
                        db.add(active)
                        db.commit()

    return active



def check_call_quota(db: Session, client_id: str) -> Tuple[bool, str, float]:
    """Check if client has active quota to make calls (minimum 1 minute required).
    
    Returns (has_quota, reason, remaining_minutes).
    """
    recharge = get_active_recharge(db, client_id)
    if not recharge:
        return False, "No active recharge plan. Please purchase a recharge plan to make calls.", 0.0

    now = utcnow()
    if recharge.ExpiresAt and _is_expired(recharge.ExpiresAt, now):
        return False, "Your recharge plan has expired. Please top up to continue calling.", 0.0

    if recharge.RemainingMinutes < 1.0:
        return False, "Your recharge plan balance is less than 1 minute. Please top up to continue calling.", recharge.RemainingMinutes

    return True, "Active plan available", recharge.RemainingMinutes


def reserve_minute_pulse(
    db: Session,
    client_id: str,
    call_sid: str,
    minute_number: int,
    conversation_id: Optional[str] = None,
) -> Tuple[bool, float, bool]:
    """Atomically reserve/deduct 1 full minute for an active call pulse.
    
    Returns (success, remaining_balance, is_exhausted).
    If success is False, client has no minutes left and call should be terminated.
    """
    # Ensure active recharge with balance >= 1.0 (auto-activating pending if needed)
    recharge = get_active_recharge(db, client_id)
    if not recharge or recharge.RemainingMinutes < 1.0:
        logger.warning(
            f"[Billing Pulse] Client {client_id} cannot reserve minute {minute_number} for call {call_sid}. "
            f"Insufficient balance: {recharge.RemainingMinutes if recharge else 0.0} mins."
        )
        return False, (recharge.RemainingMinutes if recharge else 0.0), True

    # Lock row with SELECT FOR UPDATE
    recharge = (
        db.query(LeadClientRecharge)
        .filter(
            LeadClientRecharge.Id == recharge.Id,
            LeadClientRecharge.Status == RECHARGE_STATUS_ACTIVE,
        )
        .with_for_update()
        .first()
    )
    if not recharge or recharge.RemainingMinutes < 1.0:
        return False, (recharge.RemainingMinutes if recharge else 0.0), True

    prev_balance = recharge.RemainingMinutes
    new_balance = max(0.0, round(prev_balance - 1.0, 4))
    recharge.RemainingMinutes = new_balance

    is_exhausted = new_balance <= 0.0001
    # Note: Subscription status remains ACTIVE until ExpiresAt. Zero balance causes
    # check_call_quota to pause subsequent calls and pulse watcher to terminate this call.

    log_entry = LeadUsageLog(
        ClientId=client_id,
        RechargeId=recharge.Id,
        CallSid=call_sid,
        ConversationId=conversation_id,
        CallDurationSeconds=minute_number * 60,
        MinutesDeducted=1.0,
        PreviousBalance=prev_balance,
        NewBalance=new_balance,
        DeductedAt=utcnow(),
    )
    db.add(recharge)
    db.add(log_entry)
    db.commit()

    logger.info(
        f"[Billing Pulse] Reserved Minute #{minute_number} (1 min) for call {call_sid}. "
        f"Client {client_id} balance: {prev_balance:.0f} -> {new_balance:.0f} mins."
    )

    if is_exhausted:
        # Check if next pending plan can be auto-activated for subsequent pulses
        get_active_recharge(db, client_id)

    return True, new_balance, is_exhausted


def deduct_call_usage(
    db: Session,
    client_id: str,
    call_sid: str,
    duration_seconds: int,
    conversation_id: Optional[str] = None,
) -> Tuple[float, float, bool]:
    """Reconcile 1-minute pulse deduction against final Twilio call duration.
    
    1s-60s -> 1 min, 61s-120s -> 2 mins (math.ceil(duration / 60)).
    If minutes were already reserved upfront by live pulse, only deducts any remaining difference.
    Returns (minutes_deducted, remaining_balance, is_exhausted).
    """
    if duration_seconds <= 0:
        active = get_active_recharge(db, client_id)
        bal = active.RemainingMinutes if active else 0.0
        return 0.0, bal, False

    required_minutes = float(math.ceil(duration_seconds / 60.0))

    # Check how many minutes were already deducted upfront for this call_sid
    already_deducted = (
        db.query(func.sum(LeadUsageLog.MinutesDeducted))
        .filter(
            LeadUsageLog.ClientId == client_id,
            LeadUsageLog.CallSid == call_sid,
        )
        .scalar()
    ) or 0.0

    needed_minutes = max(0.0, required_minutes - already_deducted)
    if needed_minutes <= 0.0001:
        active = get_active_recharge(db, client_id)
        bal = active.RemainingMinutes if active else 0.0
        return 0.0, bal, False

    # Use SELECT FOR UPDATE to prevent race conditions during concurrent call ends
    recharge = (
        db.query(LeadClientRecharge)
        .filter(
            LeadClientRecharge.ClientId == client_id,
            LeadClientRecharge.Status.in_([RECHARGE_STATUS_ACTIVE, RECHARGE_STATUS_EXHAUSTED]),
        )
        .with_for_update()
        .first()
    )

    if not recharge:
        logger.warning(f"[Billing] No active recharge found during deduction for client {client_id}, call {call_sid}")
        return needed_minutes, 0.0, True

    prev_balance = recharge.RemainingMinutes
    new_balance = max(0.0, round(prev_balance - needed_minutes, 4))
    recharge.RemainingMinutes = new_balance

    is_exhausted = new_balance <= 0.0001
    # Note: Subscription status remains ACTIVE until ExpiresAt. Calls are paused via check_call_quota.

    log_entry = LeadUsageLog(
        ClientId=client_id,
        RechargeId=recharge.Id,
        CallSid=call_sid,
        ConversationId=conversation_id,
        CallDurationSeconds=duration_seconds,
        MinutesDeducted=needed_minutes,
        PreviousBalance=prev_balance,
        NewBalance=new_balance,
        DeductedAt=utcnow(),
    )
    db.add(recharge)
    db.add(log_entry)
    db.commit()

    logger.info(
        f"[Billing] Deducted {needed_minutes:.0f} pulse mins ({duration_seconds}s, total {required_minutes:.0f}m) for call {call_sid}. "
        f"Client {client_id} balance: {prev_balance:.0f} -> {new_balance:.0f} mins."
    )

    if is_exhausted:
        _terminate_all_active_client_calls(client_id)
        get_active_recharge(db, client_id)

    return needed_minutes, new_balance, is_exhausted


def _terminate_all_active_client_calls(client_id: str) -> None:
    """Terminate all ongoing calls for a client when balance reaches 0."""
    try:
        from multiligual_call import active_calls, twilio_client

        terminated_count = 0
        for sid, call_info in list(active_calls.items()):
            # Strictly check if this call belongs to the exhausted client
            if call_info.get("client_id") == client_id:
                try:
                    twilio_client.calls(sid).update(status="completed")
                    terminated_count += 1
                except Exception as exc:
                    logger.warning(f"[Billing] Could not hang up call {sid}: {exc}")
        if terminated_count > 0:
            logger.info(f"[Billing] Terminated {terminated_count} active call(s) for client {client_id} due to zero balance.")
    except Exception as exc:
        logger.error(f"[Billing] Error terminating active calls for client {client_id}: {exc}")


def allocate_recharge(
    db: Session,
    client_id: str,
    template_id: Optional[str] = None,
    custom_minutes: Optional[float] = None,
    custom_validity_days: Optional[int] = None,
    custom_price: Optional[float] = None,
    custom_name: Optional[str] = None,
    payment_ref: Optional[str] = None,
    created_by: str = "system",
) -> LeadClientRecharge:
    """Allocate a standard or custom plan to a client.
    
    If an active plan is running, the new plan is queued as PENDING.
    """
    ensure_default_templates(db)
    now = utcnow()

    if template_id:
        template = db.get(LeadRechargePlanTemplate, template_id)
        if not template or template.IsDeleted or not template.IsActive:
            raise ValueError(f"Plan template {template_id} is no longer available or has been retired.")
        plan_name = template.Name
        minutes = template.IncludedMinutes
        validity = template.ValidityDays
        price = template.Price
    else:
        if not custom_minutes or not custom_validity_days:
            raise ValueError("Custom plan requires custom_minutes and custom_validity_days")
        plan_name = custom_name or f"Custom Plan ({int(custom_minutes)} Mins / {custom_validity_days} Days)"
        minutes = float(custom_minutes)
        validity = int(custom_validity_days)
        price = float(custom_price or 0.0)

    # Check if client has an existing ACTIVE or UNEXPIRED plan
    existing_active = (
        db.query(LeadClientRecharge)
        .filter(
            LeadClientRecharge.ClientId == client_id,
            LeadClientRecharge.Status.in_([RECHARGE_STATUS_ACTIVE, RECHARGE_STATUS_EXHAUSTED]),
        )
        .order_by(LeadClientRecharge.CreatedAt.desc())
        .first()
    )

    is_topup = bool(template and (template.PlanType == PLAN_TYPE_TOPUP or template.PlanCategory == PLAN_CATEGORY_VOICE_TOPUP))

    if is_topup and existing_active and (not existing_active.ExpiresAt or not _is_expired(existing_active.ExpiresAt, now)):
        # Booster pack merges directly into active plan
        prev_bal = existing_active.RemainingMinutes
        existing_active.RemainingMinutes += minutes
        existing_active.Status = RECHARGE_STATUS_ACTIVE
        db.add(existing_active)

        log_entry = LeadUsageLog(
            ClientId=client_id,
            RechargeId=existing_active.Id,
            CallSid="BOOSTER_TOPUP",
            ConversationId=None,
            CallDurationSeconds=0,
            MinutesDeducted=-minutes,
            PreviousBalance=prev_bal,
            NewBalance=existing_active.RemainingMinutes,
            DeductedAt=now,
        )
        db.add(log_entry)
        db.commit()
        db.refresh(existing_active)
        logger.info(f"[Billing] Top-up booster {minutes} mins merged into active plan {existing_active.Id}")
        return existing_active

    if existing_active:
        existing_active.Status = RECHARGE_STATUS_SUPERSEDED
        db.add(existing_active)

    initial_status = RECHARGE_STATUS_ACTIVE
    recharged_at = now
    expires_at = compute_cycle_expiry(now, validity)

    channels = list(template.AddonChannels) if template and template.AddonChannels else []
    recharge = LeadClientRecharge(
        ClientId=client_id,
        PlanTemplateId=template_id,
        PlanNameSnapshot=plan_name,
        PurchasedMinutes=minutes,
        RemainingMinutes=minutes,
        ValidityDaysSnapshot=validity,
        PricePaid=price,
        RechargedAt=recharged_at,
        ExpiresAt=expires_at,
        Status=initial_status,
        PaymentReference=payment_ref,
        CreatedBy=created_by,
        ActiveChannels=channels,
        NextCycleChannels=channels,
    )
    db.add(recharge)
    db.commit()
    db.refresh(recharge)

    logger.info(
        f"[Billing] Allocated recharge {recharge.Id} ({plan_name}) for client {client_id}. Status: {initial_status}"
    )
    return recharge


# ===========================================================================
# Razorpay Payment Gateway & Invoicing Integration
# ===========================================================================

def get_razorpay_client() -> razorpay.Client:
    """Initialize authenticated Razorpay SDK client."""
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise ValueError(
            "Razorpay credentials are not configured in environment variables (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET)"
        )
    return razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))


def create_razorpay_order(
    db: Session,
    client_id: str,
    plan_template_id: str,
    user_email: str,
) -> dict:
    """Creates a Razorpay Order and logs a pending recharge record."""
    template = db.get(LeadRechargePlanTemplate, plan_template_id)
    if not template or template.IsDeleted or not template.IsActive:
        raise ValueError("Selected recharge plan template is invalid or inactive")

    if template.Price <= 0:
        raise ValueError("Zero-cost plans cannot be processed through payment gateway")

    now = utcnow()

    # Active plan exclusivity check: prevent buying base plans if already active
    is_topup = (template.PlanType == PLAN_TYPE_TOPUP or template.PlanCategory == PLAN_CATEGORY_VOICE_TOPUP)
    if not is_topup:
        active_plan = get_active_recharge(db, client_id)
        if active_plan and (not active_plan.ExpiresAt or not _is_expired(active_plan.ExpiresAt, now)):
            raise ValueError(
                "You already have an active subscription plan. Additional base plans cannot be queued during an active billing cycle. "
                "You can purchase minute top-up boosters to add minutes to your current cycle."
            )

    amount_paise = int(round(template.Price * 100))
    rzp = get_razorpay_client()

    order_payload = {
        "amount": amount_paise,
        "currency": "INR",
        "receipt": f"rcpt_{client_id[:8]}_{int(now.timestamp())}",
        "notes": {
            "client_id": client_id,
            "plan_template_id": plan_template_id,
            "plan_name": template.Name,
            "user_email": user_email,
        },
    }

    try:
        order = rzp.order.create(data=order_payload)
    except Exception as exc:
        logger.error(f"[Billing] Razorpay order creation failed: {exc}")
        raise ValueError(f"Failed to initiate order with Razorpay: {exc}") from exc

    order_id = order["id"]

    # Pre-record pending recharge record for comprehensive history tracking
    pending_recharge = LeadClientRecharge(
        ClientId=client_id,
        PlanTemplateId=template.Id,
        PlanNameSnapshot=template.Name,
        PurchasedMinutes=template.IncludedMinutes,
        RemainingMinutes=template.IncludedMinutes,
        ValidityDaysSnapshot=template.ValidityDays,
        PricePaid=template.Price,
        RechargedAt=None,
        ExpiresAt=None,
        Status=RECHARGE_STATUS_PENDING,
        PaymentReference=None,
        RazorpayOrderId=order_id,
        CreatedBy=user_email,
    )
    db.add(pending_recharge)
    db.commit()
    db.refresh(pending_recharge)

    logger.info(
        f"[Billing] Created Razorpay order {order_id} for client {client_id}, plan {template.Name} (₹{template.Price})"
    )

    return {
        "order_id": order_id,
        "amount": amount_paise,
        "currency": "INR",
        "key_id": settings.razorpay_key_id,
        "plan_id": template.Id,
        "plan_name": template.Name,
        "included_minutes": template.IncludedMinutes,
    }


def verify_razorpay_payment(
    db: Session,
    client_id: str,
    payload: dict,
    user_email: str,
) -> LeadClientRecharge:
    """Verifies cryptographic Razorpay payment signature and activates minutes."""
    order_id = payload.get("razorpay_order_id")
    payment_id = payload.get("razorpay_payment_id")
    signature = payload.get("razorpay_signature")
    plan_template_id = payload.get("plan_template_id")

    if not order_id or not payment_id or not signature:
        raise ValueError("Missing payment verification parameters (order_id, payment_id, or signature)")

    # Find pending recharge
    recharge = (
        db.query(LeadClientRecharge)
        .filter(
            LeadClientRecharge.ClientId == client_id,
            LeadClientRecharge.RazorpayOrderId == order_id,
        )
        .first()
    )

    rzp = get_razorpay_client()
    try:
        rzp.utility.verify_payment_signature(
            {
                "razorpay_order_id": order_id,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": signature,
            }
        )
    except Exception as sig_err:
        logger.error(f"[Billing] Razorpay signature verification failed for order {order_id}: {sig_err}")
        if recharge:
            recharge.Status = RECHARGE_STATUS_FAILED
            recharge.FailureReason = f"Signature verification failed: {sig_err}"
            recharge.PaymentReference = payment_id
            db.commit()
        raise ValueError(f"Invalid payment signature: {sig_err}") from sig_err

    now = utcnow()

    # If no pending recharge row existed for this order, create one now
    if not recharge:
        template = db.get(LeadRechargePlanTemplate, plan_template_id)
        if not template:
            raise ValueError(f"Plan template {plan_template_id} not found")
        recharge = LeadClientRecharge(
            ClientId=client_id,
            PlanTemplateId=template.Id,
            PlanNameSnapshot=template.Name,
            PurchasedMinutes=template.IncludedMinutes,
            RemainingMinutes=template.IncludedMinutes,
            ValidityDaysSnapshot=template.ValidityDays,
            PricePaid=template.Price,
            CreatedBy=user_email,
            RazorpayOrderId=order_id,
        )
        db.add(recharge)

    # Check if client has another ACTIVE plan
    existing_active = (
        db.query(LeadClientRecharge)
        .filter(
            LeadClientRecharge.ClientId == client_id,
            LeadClientRecharge.Status.in_([RECHARGE_STATUS_ACTIVE, RECHARGE_STATUS_EXHAUSTED]),
            LeadClientRecharge.Id != recharge.Id,
        )
        .order_by(LeadClientRecharge.CreatedAt.desc())
        .first()
    )

    template = db.get(LeadRechargePlanTemplate, recharge.PlanTemplateId) if recharge.PlanTemplateId else None
    is_topup = bool(template and (template.PlanType == PLAN_TYPE_TOPUP or template.PlanCategory == PLAN_CATEGORY_VOICE_TOPUP))

    if is_topup and existing_active and (not existing_active.ExpiresAt or not _is_expired(existing_active.ExpiresAt, now)):
        # Booster minutes merge directly into current active plan
        prev_bal = existing_active.RemainingMinutes
        existing_active.RemainingMinutes += recharge.PurchasedMinutes
        existing_active.Status = RECHARGE_STATUS_ACTIVE
        recharge.Status = RECHARGE_STATUS_SUPERSEDED
        recharge.PaymentReference = payment_id
        recharge.FailureReason = None
        db.add(existing_active)

        log_entry = LeadUsageLog(
            ClientId=client_id,
            RechargeId=existing_active.Id,
            CallSid="BOOSTER_TOPUP",
            ConversationId=None,
            CallDurationSeconds=0,
            MinutesDeducted=-recharge.PurchasedMinutes,
            PreviousBalance=prev_bal,
            NewBalance=existing_active.RemainingMinutes,
            DeductedAt=now,
        )
        db.add(log_entry)
    else:
        if existing_active:
            existing_active.Status = RECHARGE_STATUS_SUPERSEDED
            db.add(existing_active)
        recharge.Status = RECHARGE_STATUS_ACTIVE
        recharge.RechargedAt = now
        recharge.ExpiresAt = compute_cycle_expiry(now, recharge.ValidityDaysSnapshot or 30)
        recharge.PaymentReference = payment_id
        recharge.FailureReason = None
        if template and template.AddonChannels:
            recharge.ActiveChannels = list(template.AddonChannels)
            recharge.NextCycleChannels = list(template.AddonChannels)


    # Generate Custom Server-Side Invoice & Dispatch Dual Email (Body + Attachment)
    invoice_url = f"/api/leadai/billing/invoices/{recharge.Id}/download"
    invoice_id = None
    try:
        from Domain.models import Client
        from ..services import invoice as invoice_svc

        client_obj = db.get(Client, client_id)
        company_name = client_obj.Name if client_obj else "Client Company"

        invoice_id = invoice_svc.build_invoice_number(recharge.Id, now)
        recharge.InvoiceId = invoice_id
        recharge.InvoiceUrl = invoice_url

        # Render custom HTML invoice matching Texas Space Tours design aesthetics
        html_content = invoice_svc.render_invoice_html(
            recharge=recharge,
            client=client_obj,
            user_email=user_email,
        )

        # Generate custom PDF in memory
        pdf_bytes = invoice_svc.generate_invoice_pdf(html_content)
        inv_filename = f"Invoice_{invoice_id}.pdf"

        # Dispatch automated email with HTML body + PDF attachment directly to the customer
        recipient_email = (
            user_email
            or getattr(client_obj, "Email", None)
            or getattr(client_obj, "ContactEmail", None)
        )
        if recipient_email:
            email_subject = f"LeadAI Billing - Invoice {invoice_id} ({company_name})"
            invoice_svc.send_invoice_email(
                to_email=recipient_email,
                subject=email_subject,
                html_content=html_content,
                pdf_bytes=pdf_bytes,
                filename=inv_filename,
            )
        logger.info(f"[Billing] Custom invoice generated: {invoice_id} -> {invoice_url}")
    except Exception as inv_err:
        logger.warning(
            f"[Billing] Notice: Custom invoice generation/email skipped ({inv_err}).",
            exc_info=True,
        )

    recharge.InvoiceId = invoice_id
    recharge.InvoiceUrl = invoice_url

    db.commit()
    db.refresh(recharge)

    logger.info(
        f"[Billing] Successfully verified payment {payment_id} for order {order_id}. Plan {recharge.PlanNameSnapshot} activated."
    )
    return recharge


def record_payment_failure(
    db: Session,
    client_id: str,
    order_id: str | None = None,
    subscription_id: str | None = None,
    error_code: str | None = None,
    error_description: str | None = None,
) -> LeadClientRecharge | None:
    """Records user cancellation or gateway failure for complete history audit."""
    query = db.query(LeadClientRecharge).filter(LeadClientRecharge.ClientId == client_id)
    sub_id = subscription_id.strip() if subscription_id else None
    ord_id = order_id.strip() if order_id else None

    if sub_id:
        recharge = query.filter(LeadClientRecharge.RazorpaySubscriptionId == sub_id).first()
    elif ord_id:
        recharge = query.filter(LeadClientRecharge.RazorpayOrderId == ord_id).first()
    else:
        return None

    if not recharge:
        logger.warning(f"[Billing] No recharge found to mark failure for client {client_id} (sub={sub_id}, ord={ord_id})")
        return None

    # CRITICAL SECURITY FIX: Only mark PENDING transactions as failed.
    # Never mutate an ACTIVE or SUPERSEDED subscription to FAILED.
    if recharge.Status != RECHARGE_STATUS_PENDING:
        logger.warning(
            f"[Billing] Ignoring failure record for non-pending recharge {recharge.Id} (Status={recharge.Status})"
        )
        return recharge

    code = error_code or "DISMISSED"
    desc = error_description or "Payment was cancelled or dismissed before completion"
    
    recharge.Status = RECHARGE_STATUS_FAILED
    recharge.FailureReason = f"[{code}] {desc}"

    db.commit()
    db.refresh(recharge)
    logger.info(f"[Billing] Recorded payment failure: {recharge.FailureReason} -> Status: {recharge.Status}")
    return recharge


def ensure_razorpay_plan(db: Session, template: LeadRechargePlanTemplate) -> str:
    """Ensures a recurring Plan exists in Razorpay for this template and returns its ID."""
    if template.RazorpayPlanId:
        return template.RazorpayPlanId

    rzp = get_razorpay_client()
    period = "yearly" if template.ValidityDays >= 360 else "monthly"
    amount_paise = int(round(template.Price * 100))

    try:
        rzp_plan = rzp.plan.create({
            "period": period,
            "interval": 1,
            "item": {
                "name": template.Name,
                "amount": amount_paise,
                "currency": "INR",
                "description": template.Description or f"LeadAI {template.Name} ({template.IncludedMinutes} Mins)",
            },
            "notes": {
                "plan_template_id": template.Id,
                "plan_type": template.PlanType,
            },
        })
        template.RazorpayPlanId = rzp_plan["id"]
        db.commit()
        logger.info(f"[Billing] Created Razorpay recurring plan {template.RazorpayPlanId} for template {template.Name}")
        return template.RazorpayPlanId
    except Exception as err:
        err_msg = str(err).strip()
        if not err_msg or "ServerError" in type(err).__name__:
            err_msg = (
                "Subscriptions feature is not enabled on your Razorpay merchant account. "
                "Please enable 'Subscriptions / Recurring Payments' in your Razorpay Dashboard (Settings -> Subscriptions)."
            )
        logger.error(f"[Billing] Failed to create Razorpay recurring plan for template {template.Id}: {err_msg}")
        raise ValueError(f"Razorpay plan creation failed: {err_msg}") from err


def create_razorpay_subscription(
    db: Session,
    client_id: str,
    plan_template_id: str,
    user_email: str,
) -> dict:
    """Creates a Razorpay recurring subscription (e-Mandate / UPI AutoPay) for the client."""
    now = utcnow()
    active_plan = get_active_recharge(db, client_id)
    if active_plan and (not active_plan.ExpiresAt or not _is_expired(active_plan.ExpiresAt, now)) and not active_plan.CancelAtPeriodEnd:
        raise ValueError(
            "You already have an active subscription. Additional subscriptions cannot be purchased while your current plan is active."
        )

    template = db.get(LeadRechargePlanTemplate, plan_template_id)
    if not template or not template.IsActive:
        raise ValueError(f"Plan template {plan_template_id} not found or inactive")

    rzp_plan_id = ensure_razorpay_plan(db, template)
    rzp = get_razorpay_client()

    total_count = 12 if template.ValidityDays < 360 else 5
    amount_paise = int(round(template.Price * 100))

    try:
        rzp_sub = rzp.subscription.create({
            "plan_id": rzp_plan_id,
            "total_count": total_count,
            "quantity": 1,
            "customer_notify": 1,
            "notes": {
                "client_id": client_id,
                "plan_template_id": template.Id,
                "user_email": user_email,
            },
        })
    except Exception as err:
        err_msg = str(err).strip()
        if not err_msg or "ServerError" in type(err).__name__:
            err_msg = (
                "Subscriptions feature is not enabled on your Razorpay merchant account. "
                "Please enable 'Subscriptions / Recurring Payments' in your Razorpay Dashboard (Settings -> Subscriptions)."
            )
        logger.error(f"[Billing] Failed to create Razorpay subscription: {err_msg}")
        raise ValueError(f"Failed to initialize subscription: {err_msg}") from err

    sub_id = rzp_sub["id"]
    channels = list(template.AddonChannels or []) if template else []

    pending_recharge = LeadClientRecharge(
        ClientId=client_id,
        PlanTemplateId=template.Id,
        PlanNameSnapshot=template.Name,
        PurchasedMinutes=template.IncludedMinutes,
        RemainingMinutes=template.IncludedMinutes,
        ValidityDaysSnapshot=template.ValidityDays,
        PricePaid=template.Price,
        CreatedBy=user_email,
        RazorpaySubscriptionId=sub_id,
        IsAutoRenew=True,
        Status=RECHARGE_STATUS_PENDING,
        ActiveChannels=channels,
        NextCycleChannels=channels,
    )
    db.add(pending_recharge)
    db.commit()
    db.refresh(pending_recharge)

    logger.info(
        f"[Billing] Created Razorpay subscription {sub_id} for client {client_id}, plan {template.Name} (₹{template.Price}/mo)"
    )

    return {
        "subscription_id": sub_id,
        "key_id": settings.razorpay_key_id,
        "plan_id": template.Id,
        "plan_name": template.Name,
        "amount": amount_paise,
        "currency": "INR",
        "included_minutes": template.IncludedMinutes,
    }


def verify_razorpay_subscription_payment(
    db: Session,
    client_id: str,
    payload: dict,
    user_email: str,
) -> LeadClientRecharge:
    """Verifies Razorpay subscription initial payment signature and activates Month 1."""
    payment_id = payload.get("razorpay_payment_id")
    subscription_id = payload.get("razorpay_subscription_id")
    signature = payload.get("razorpay_signature")
    plan_template_id = payload.get("plan_template_id")

    if not payment_id or not subscription_id or not signature:
        raise ValueError("Missing subscription verification parameters (payment_id, subscription_id, or signature)")

    recharge = (
        db.query(LeadClientRecharge)
        .filter(
            LeadClientRecharge.ClientId == client_id,
            LeadClientRecharge.RazorpaySubscriptionId == subscription_id,
        )
        .first()
    )

    rzp = get_razorpay_client()
    try:
        rzp.utility.verify_subscription_payment_signature({
            "razorpay_payment_id": payment_id,
            "razorpay_subscription_id": subscription_id,
            "razorpay_signature": signature,
        })
    except Exception as sig_err:
        logger.error(f"[Billing] Subscription signature verification failed for sub {subscription_id}: {sig_err}")
        if recharge:
            recharge.Status = RECHARGE_STATUS_FAILED
            recharge.FailureReason = f"Signature verification failed: {sig_err}"
            recharge.PaymentReference = payment_id
            db.commit()
        raise ValueError(f"Invalid subscription signature: {sig_err}") from sig_err

    now = utcnow()

    if not recharge:
        template = db.get(LeadRechargePlanTemplate, plan_template_id)
        if not template:
            raise ValueError(f"Plan template {plan_template_id} not found")
        recharge = LeadClientRecharge(
            ClientId=client_id,
            PlanTemplateId=template.Id,
            PlanNameSnapshot=template.Name,
            PurchasedMinutes=template.IncludedMinutes,
            RemainingMinutes=template.IncludedMinutes,
            ValidityDaysSnapshot=template.ValidityDays,
            PricePaid=template.Price,
            CreatedBy=user_email,
            RazorpaySubscriptionId=subscription_id,
            IsAutoRenew=True,
        )
        db.add(recharge)

    existing_active = (
        db.query(LeadClientRecharge)
        .filter(
            LeadClientRecharge.ClientId == client_id,
            LeadClientRecharge.Status == RECHARGE_STATUS_ACTIVE,
            LeadClientRecharge.Id != recharge.Id,
        )
        .first()
    )

    if existing_active:
        existing_active.Status = RECHARGE_STATUS_SUPERSEDED
        db.add(existing_active)

    recharge.Status = RECHARGE_STATUS_ACTIVE
    recharge.RechargedAt = now
    recharge.ExpiresAt = compute_cycle_expiry(now, recharge.ValidityDaysSnapshot or 30)
    recharge.PaymentReference = payment_id
    recharge.IsAutoRenew = True
    recharge.RolloverMinutesCarried = 0.0
    recharge.FailureReason = None

    template = db.get(LeadRechargePlanTemplate, recharge.PlanTemplateId) if recharge.PlanTemplateId else None
    if template and template.AddonChannels:
        recharge.ActiveChannels = list(template.AddonChannels)
        recharge.NextCycleChannels = list(template.AddonChannels)
    elif not recharge.ActiveChannels and recharge.PlanNameSnapshot:
        low = recharge.PlanNameSnapshot.lower()
        found = [c for c in ("whatsapp", "instagram", "facebook", "linkedin") if c in low]
        if found:
            recharge.ActiveChannels = found
            recharge.NextCycleChannels = found


    invoice_url = f"/api/leadai/billing/invoices/{recharge.Id}/download"
    invoice_id = None
    try:
        from Domain.models import Client
        from ..services import invoice as invoice_svc

        client_obj = db.get(Client, client_id)
        company_name = client_obj.Name if client_obj else "Client Company"

        invoice_id = invoice_svc.build_invoice_number(recharge.Id, now)
        recharge.InvoiceId = invoice_id
        recharge.InvoiceUrl = invoice_url

        html_content = invoice_svc.render_invoice_html(
            recharge=recharge,
            client=client_obj,
            user_email=user_email,
        )
        pdf_bytes = invoice_svc.generate_invoice_pdf(html_content)
        inv_filename = f"Invoice_{invoice_id}.pdf"

        recipient_email = (
            user_email
            or getattr(client_obj, "Email", None)
            or getattr(client_obj, "ContactEmail", None)
        )
        if recipient_email:
            email_subject = f"LeadAI Billing - Subscription Invoice {invoice_id} ({company_name})"
            invoice_svc.send_invoice_email(
                to_email=recipient_email,
                subject=email_subject,
                html_content=html_content,
                pdf_bytes=pdf_bytes,
                filename=inv_filename,
            )
        logger.info(f"[Billing] Subscription invoice generated: {invoice_id} -> {invoice_url}")
    except Exception as inv_err:
        logger.warning(
            f"[Billing] Notice: Custom invoice generation/email skipped ({inv_err}).",
            exc_info=True,
        )

    recharge.InvoiceId = invoice_id
    recharge.InvoiceUrl = invoice_url

    db.commit()
    db.refresh(recharge)

    logger.info(
        f"[Billing] Successfully verified subscription payment {payment_id} for sub {subscription_id}. Plan {recharge.PlanNameSnapshot} activated with Auto-Renew."
    )
    return recharge


def handle_razorpay_webhook(
    db: Session,
    event_payload: dict,
    signature: str | None = None,
    raw_body: bytes | None = None,
) -> dict:
    """Processes asynchronous Razorpay webhook events with HMAC signature verification, deduplication & safety valves."""
    # 1. Cryptographic HMAC Signature Verification
    if settings.razorpay_webhook_secret:
        if not signature or raw_body is None:
            logger.error("[Billing Webhook] Signature verification failed: missing signature or body")
            raise ValueError("Missing webhook signature or request body")
        rzp = get_razorpay_client()
        try:
            body_str = raw_body.decode("utf-8") if isinstance(raw_body, bytes) else str(raw_body)
            rzp.utility.verify_webhook_signature(body_str, signature, settings.razorpay_webhook_secret)
        except Exception as sig_err:
            logger.error(f"[Billing Webhook] Invalid webhook signature: {sig_err}")
            raise ValueError(f"Invalid webhook signature: {sig_err}") from sig_err

    event_type = event_payload.get("event")
    logger.info(f"[Billing Webhook] Received Razorpay event: {event_type}")

    if event_type == "subscription.charged":
        sub_entity = event_payload.get("payload", {}).get("subscription", {}).get("entity", {})
        payment_entity = event_payload.get("payload", {}).get("payment", {}).get("entity", {})
        sub_id = sub_entity.get("id")
        payment_id = payment_entity.get("id")

        if not sub_id:
            return {"status": "ignored", "reason": "no_subscription_id"}

        # 2. Idempotency Check: Prevent duplicate renewals or duplicate minute rollovers
        if payment_id:
            existing_payment = (
                db.query(LeadClientRecharge)
                .filter(LeadClientRecharge.PaymentReference == payment_id)
                .first()
            )
            if existing_payment:
                logger.info(
                    f"[Billing Webhook] Payment {payment_id} already processed for recharge {existing_payment.Id}. Idempotent skip."
                )
                return {
                    "status": "ignored",
                    "reason": "already_processed",
                    "recharge_id": existing_payment.Id,
                }

        prev_recharge = (
            db.query(LeadClientRecharge)
            .filter(LeadClientRecharge.RazorpaySubscriptionId == sub_id)
            .order_by(LeadClientRecharge.CreatedAt.desc())
            .first()
        )
        if not prev_recharge:
            logger.warning(f"[Billing Webhook] No existing recharge found for subscription {sub_id}")
            return {"status": "ignored", "reason": "unknown_subscription"}

        client_id = prev_recharge.ClientId
        now = utcnow()
        template = db.get(LeadRechargePlanTemplate, prev_recharge.PlanTemplateId)
        plan_name = template.Name if template else prev_recharge.PlanNameSnapshot
        minutes = template.IncludedMinutes if template else prev_recharge.PurchasedMinutes
        validity = template.ValidityDays if template else prev_recharge.ValidityDaysSnapshot
        price = template.Price if template else prev_recharge.PricePaid

        # 3. Minute Rollover with 1x Monthly Quota Cap (prevents infinite accumulation liability)
        unspent = max(0.0, float(prev_recharge.RemainingMinutes)) if (prev_recharge and prev_recharge.RemainingMinutes) else 0.0
        rollover_cap = float(minutes)
        rollover = min(unspent, rollover_cap)

        if prev_recharge and prev_recharge.Status == RECHARGE_STATUS_ACTIVE:
            prev_recharge.Status = RECHARGE_STATUS_SUPERSEDED
            db.add(prev_recharge)

        new_recharge = LeadClientRecharge(
            ClientId=client_id,
            PlanTemplateId=template.Id if template else None,
            PlanNameSnapshot=plan_name,
            PurchasedMinutes=minutes,
            RemainingMinutes=minutes + rollover,
            RolloverMinutesCarried=rollover,
            ValidityDaysSnapshot=validity,
            PricePaid=price,
            RechargedAt=now,
            ExpiresAt=compute_cycle_expiry(now, validity or 30),
            Status=RECHARGE_STATUS_ACTIVE,
            PaymentReference=payment_id,
            RazorpaySubscriptionId=sub_id,
            IsAutoRenew=True,
            ActiveChannels=list(prev_recharge.NextCycleChannels or prev_recharge.ActiveChannels or []) if prev_recharge else [],
            NextCycleChannels=list(prev_recharge.NextCycleChannels or prev_recharge.ActiveChannels or []) if prev_recharge else [],
            CreatedBy="razorpay_autopay_webhook",
        )
        db.add(new_recharge)
        db.commit()
        db.refresh(new_recharge)

        if rollover > 0:
            log_entry = LeadUsageLog(
                ClientId=client_id,
                RechargeId=new_recharge.Id,
                CallSid="SYSTEM_ROLLOVER",
                ConversationId=None,
                CallDurationSeconds=0,
                MinutesDeducted=-rollover,
                PreviousBalance=rollover,
                NewBalance=minutes + rollover,
                DeductedAt=now,
            )
            db.add(log_entry)
            db.commit()

        try:
            from Domain.models import Client
            from ..services import invoice as invoice_svc

            client_obj = db.get(Client, client_id)
            inv_id = invoice_svc.build_invoice_number(new_recharge.Id, now)
            inv_url = f"/api/leadai/billing/invoices/{new_recharge.Id}/download"
            new_recharge.InvoiceId = inv_id
            new_recharge.InvoiceUrl = inv_url

            html_content = invoice_svc.render_invoice_html(
                recharge=new_recharge,
                client=client_obj,
                user_email=new_recharge.CreatedBy,
            )
            pdf_bytes = invoice_svc.generate_invoice_pdf(html_content)
            inv_filename = f"Invoice_{inv_id}.pdf"

            recip_email = getattr(client_obj, "Email", None) or getattr(client_obj, "ContactEmail", None)
            if recip_email:
                invoice_svc.send_invoice_email(
                    to_email=recip_email,
                    subject=f"LeadAI Billing - Auto-Renewal Invoice {inv_id}",
                    html_content=html_content,
                    pdf_bytes=pdf_bytes,
                    filename=inv_filename,
                )
            db.commit()
        except Exception as inv_err:
            logger.warning(f"[Billing Webhook] Invoice generation error: {inv_err}")

        logger.info(
            f"[Billing Webhook] Successfully auto-renewed subscription {sub_id} for client {client_id}. Rollover: {rollover:.1f} mins (unspent={unspent:.1f}, cap={rollover_cap:.1f})."
        )
        return {"status": "renewed", "client_id": client_id, "recharge_id": new_recharge.Id, "rollover_minutes": rollover}

    elif event_type in ("subscription.pending", "payment.failed"):
        sub_entity = event_payload.get("payload", {}).get("subscription", {}).get("entity", {})
        payment_entity = event_payload.get("payload", {}).get("payment", {}).get("entity", {})
        sub_id = sub_entity.get("id")
        err_desc = payment_entity.get("error_description") or "Recurring payment failed or mandate limit reached"
        err_code = payment_entity.get("error_code") or "AUTO_DEBIT_FAILED"
        if sub_id:
            logger.warning(f"[Billing Webhook] Subscription {sub_id} payment alert [{err_code}]: {err_desc}")
            active_rec = (
                db.query(LeadClientRecharge)
                .filter(LeadClientRecharge.RazorpaySubscriptionId == sub_id)
                .order_by(LeadClientRecharge.CreatedAt.desc())
                .first()
            )
            if active_rec:
                active_rec.FailureReason = f"[{err_code}] {err_desc}"
                db.commit()
        return {"status": "payment_failure_noted", "event": event_type, "error": err_desc}

    elif event_type in ("subscription.halted", "subscription.cancelled"):
        sub_id = event_payload.get("payload", {}).get("subscription", {}).get("entity", {}).get("id")
        if sub_id:
            db.query(LeadClientRecharge).filter(
                LeadClientRecharge.RazorpaySubscriptionId == sub_id,
                LeadClientRecharge.Status == RECHARGE_STATUS_ACTIVE,
            ).update({"IsAutoRenew": False})
            db.commit()
            logger.info(f"[Billing Webhook] Marked subscription {sub_id} auto-renew as False")
        return {"status": "handled", "event": event_type}

    return {"status": "unhandled_event", "event": event_type}


def cancel_razorpay_subscription(
    db: Session,
    client_id: str,
    reason: Optional[str] = None,
) -> dict:
    """Cancels Razorpay recurring AutoPay subscription at cycle end for a client."""
    recharge = (
        db.query(LeadClientRecharge)
        .filter(
            LeadClientRecharge.ClientId == client_id,
            LeadClientRecharge.Status == RECHARGE_STATUS_ACTIVE,
            LeadClientRecharge.RazorpaySubscriptionId != None,  # noqa: E711
        )
        .order_by(LeadClientRecharge.CreatedAt.desc())
        .first()
    )
    if not recharge:
        raise ValueError("No active AutoPay subscription found for this company to cancel.")

    sub_id = recharge.RazorpaySubscriptionId
    rzp = get_razorpay_client()
    try:
        # Cancel at cycle end so the user retains their paid minutes until cycle expiry
        rzp.subscription.cancel(sub_id, {"cancel_at_cycle_end": 1})
    except Exception as err:
        logger.warning(f"[Billing] Razorpay API cancellation notice (may already be cancelled): {err}")

    recharge.CancelAtPeriodEnd = True
    recharge.IsAutoRenew = False
    if reason:
        recharge.FailureReason = f"Cancelled by user: {reason}"
    db.add(recharge)
    db.commit()
    db.refresh(recharge)

    exp_str = recharge.ExpiresAt.isoformat() if recharge.ExpiresAt else None
    logger.info(f"[Billing] Subscription {sub_id} marked to cancel at period end for client {client_id}")
    return {
        "subscription_id": sub_id,
        "status": "cancellation_scheduled",
        "cancel_at_cycle_end": True,
        "expires_at": exp_str,
        "message": f"Your AutoPay mandate will cancel at the end of the billing cycle. Your plan and remaining minutes stay active until {exp_str}.",
    }


def create_custom_bundle_subscription(
    db: Session,
    client_id: str,
    payload: dict,
    user_email: str,
) -> dict:
    """Dynamically bundles AI Voice minutes and Social Media channel add-ons into a recurring subscription."""
    now = utcnow()
    active_plan = get_active_recharge(db, client_id)
    if active_plan and (not active_plan.ExpiresAt or not _is_expired(active_plan.ExpiresAt, now)) and not active_plan.CancelAtPeriodEnd:
        raise ValueError(
            "You already have an active subscription. Additional subscriptions cannot be purchased while your current plan is active."
        )

    include_voice = payload.get("include_voice", True)
    voice_mins = float(payload.get("voice_minutes", 500.0)) if include_voice else 0.0
    voice_price = voice_mins * 4.0  # ₹4/min standard benchmark

    raw_channels = payload.get("channels") or []
    addons = [ch.lower().strip() for ch in raw_channels if isinstance(ch, str)]
    addon_total = sum(ADDON_BENCHMARKS.get(ch, 799.0) for ch in addons)

    billing_cycle = str(payload.get("billing_cycle", "monthly")).lower()
    is_yearly = billing_cycle == "yearly"
    monthly_total = voice_price + addon_total
    if monthly_total <= 0:
        raise ValueError("Custom bundle must contain at least voice minutes or one channel add-on.")

    # 15% discount for yearly billing
    total_price = (monthly_total * 12 * 0.85) if is_yearly else monthly_total
    validity_days = 365 if is_yearly else 30
    total_voice_mins = (voice_mins * 12.0) if is_yearly else voice_mins

    channels_label = ", ".join([ch.title() for ch in addons]) if addons else "Voice Only"
    cycle_label = "Yearly" if is_yearly else "Monthly"
    if include_voice:
        plan_name = f"Custom Bundle ({int(total_voice_mins)} Mins - {cycle_label})"
    else:
        plan_name = f"Custom Channel Automation ({cycle_label})"

    # Create a custom plan template for this client
    template = LeadRechargePlanTemplate(
        Name=plan_name,
        PlanType=PLAN_TYPE_CUSTOM,
        PlanCategory="client_self_bundle",
        TargetClientId=client_id,
        TargetClientIds=[client_id],
        AddonChannels=addons,
        IncludedMinutes=total_voice_mins,
        ValidityDays=validity_days,
        Price=round(total_price, 2),
        RatePerMinute=4.0,
        AutoPayByDefault=True,
        IsActive=True,
        CreatedBy=user_email,
        Description=f"Tailored subscription bundle with {total_voice_mins:.0f} voice minutes and channels ({channels_label}).",
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    return create_razorpay_subscription(
        db=db,
        client_id=client_id,
        plan_template_id=template.Id,
        user_email=user_email,
    )


def get_channel_addon_quote(db: Session, client_id: str, channel: str) -> dict:
    """Calculates mid-cycle prorated charge for adding a channel to an active plan."""
    ch_key = channel.lower().strip()
    if ch_key not in ADDON_BENCHMARKS:
        raise ValueError(f"Unknown channel add-on '{channel}'. Valid options: {list(ADDON_BENCHMARKS.keys())}")

    active_plan = get_active_recharge(db, client_id)
    if not active_plan:
        raise ValueError("No active plan found. Please purchase a base plan or bundle first.")

    now = utcnow()
    if active_plan.ExpiresAt and _is_expired(active_plan.ExpiresAt, now):
        raise ValueError("Your current plan has expired. Please renew your plan before adding channels.")

    monthly_price = ADDON_BENCHMARKS[ch_key]
    total_cycle_days = active_plan.ValidityDaysSnapshot or 30

    if active_plan.ExpiresAt:
        exp = active_plan.ExpiresAt.replace(tzinfo=timezone.utc) if active_plan.ExpiresAt.tzinfo is None else active_plan.ExpiresAt
        rem_seconds = max(0, (exp - now).total_seconds())
        remaining_days = max(1, math.ceil(rem_seconds / 86400.0))
    else:
        remaining_days = total_cycle_days

    # Daily-rate proration: monthly benchmark rate covers 30 days.
    # Prorated price = daily_rate * remaining_days (Bug #8 fix)
    daily_rate = monthly_price / 30.0
    prorated_price = max(1.0, round(daily_rate * remaining_days, 2))

    # Calculate next cycle bundle price when synced with AutoPay
    template = db.get(LeadRechargePlanTemplate, active_plan.PlanTemplateId) if active_plan.PlanTemplateId else None
    base_voice_price = template.Price if template else active_plan.PricePaid
    existing_next_channels = list(active_plan.NextCycleChannels or active_plan.ActiveChannels or [])
    if ch_key not in existing_next_channels:
        combined_channels = existing_next_channels + [ch_key]
    else:
        combined_channels = existing_next_channels

    addon_sum = sum(ADDON_BENCHMARKS.get(c, 0.0) for c in combined_channels)
    next_cycle_bundle_price = round(base_voice_price + addon_sum, 2)

    return {
        "channel": ch_key,
        "channel_name": ch_key.title() + " Bot Automation",
        "monthly_price": monthly_price,
        "total_cycle_days": total_cycle_days,
        "remaining_days": remaining_days,
        "prorated_price": prorated_price,
        "active_plan_expires_at": active_plan.ExpiresAt,
        "next_cycle_bundle_price": next_cycle_bundle_price,
        "auto_pay_synced": True,
    }


def cancel_channel_for_next_cycle(
    db: Session,
    client_id: str,
    channel: str,
) -> dict:
    """Cancels an individual channel add-on at cycle end (Bug #6 fix).
    
    The channel remains active on the current plan until ExpiresAt.
    It is removed from NextCycleChannels so the customer is not billed for it on next renewal.
    """
    ch_key = channel.lower().strip()
    if ch_key == "voice":
        raise ValueError("The base voice calling service cannot be removed as an individual channel add-on. To cancel your overall subscription, please use subscription cancellation.")

    active_plan = get_active_recharge(db, client_id)
    if not active_plan:
        raise ValueError("No active plan found for this company.")

    curr_active = list(active_plan.ActiveChannels or [])
    curr_next = list(active_plan.NextCycleChannels or curr_active)

    if ch_key not in curr_active and ch_key not in curr_next:
        raise ValueError(f"Channel '{channel}' is not active on your current plan.")

    # Remove from upcoming cycle
    if ch_key in curr_next:
        curr_next.remove(ch_key)
    active_plan.NextCycleChannels = curr_next

    # Recompute next cycle bundle price
    template = db.get(LeadRechargePlanTemplate, active_plan.PlanTemplateId) if active_plan.PlanTemplateId else None
    base_voice_price = template.Price if template else active_plan.PricePaid
    addon_sum = sum(ADDON_BENCHMARKS.get(c, 0.0) for c in curr_next)
    next_cycle_price = round(base_voice_price + addon_sum, 2)

    db.add(active_plan)
    db.commit()
    db.refresh(active_plan)

    exp_str = active_plan.ExpiresAt.strftime("%d %B %Y") if active_plan.ExpiresAt else "the end of your billing cycle"
    logger.info(
        f"[Billing] Channel {ch_key} scheduled for removal at period end for client {client_id}. "
        f"Active until: {exp_str}. Next cycle channels: {curr_next}"
    )

    return {
        "channel": ch_key,
        "status": "deactivation_scheduled",
        "active_until": active_plan.ExpiresAt.isoformat() if active_plan.ExpiresAt else None,
        "active_channels": active_plan.ActiveChannels,
        "next_cycle_channels": active_plan.NextCycleChannels,
        "next_cycle_bundle_price": next_cycle_price,
        "message": f"Your {ch_key.title()} add-on will remain active until {exp_str}. You will not be charged for it in upcoming renewals.",
    }


def create_channel_addon_order(
    db: Session,
    client_id: str,
    channel: str,
    user_email: str,
) -> dict:
    """Creates a Razorpay Order for mid-cycle prorated channel add-on payment."""
    quote = get_channel_addon_quote(db, client_id, channel)
    amount_paise = int(round(quote["prorated_price"] * 100))
    now = utcnow()
    rzp = get_razorpay_client()

    order_payload = {
        "amount": amount_paise,
        "currency": "INR",
        "receipt": f"addon_{client_id[:6]}_{channel[:4]}_{int(now.timestamp())}",
        "notes": {
            "client_id": client_id,
            "type": "channel_addon",
            "channel": quote["channel"],
            "prorated_price": str(quote["prorated_price"]),
            "user_email": user_email,
        },
    }

    try:
        order = rzp.order.create(data=order_payload)
    except Exception as exc:
        logger.error(f"[Billing Addon] Razorpay order failed: {exc}")
        raise ValueError(f"Failed to initiate channel add-on payment: {exc}") from exc

    order_id = order["id"]

    # Pre-record pending order log
    pending_record = LeadClientRecharge(
        ClientId=client_id,
        PlanTemplateId=None,
        PlanNameSnapshot=f"Channel Add-on: {quote['channel'].title()} (Prorated)",
        PurchasedMinutes=0.0,
        RemainingMinutes=0.0,
        ValidityDaysSnapshot=quote["remaining_days"],
        PricePaid=quote["prorated_price"],
        Status=RECHARGE_STATUS_PENDING,
        RazorpayOrderId=order_id,
        CreatedBy=user_email,
    )
    db.add(pending_record)
    db.commit()

    logger.info(
        f"[Billing Addon] Created prorated add-on order {order_id} for client {client_id}, channel {channel} (₹{quote['prorated_price']} for {quote['remaining_days']} days)"
    )

    return {
        "order_id": order_id,
        "amount": amount_paise,
        "currency": "INR",
        "key_id": settings.razorpay_key_id,
        "channel": quote["channel"],
        "prorated_price": quote["prorated_price"],
        "remaining_days": quote["remaining_days"],
        "next_cycle_bundle_price": quote["next_cycle_bundle_price"],
    }


def verify_channel_addon_payment(
    db: Session,
    client_id: str,
    payload: dict,
    user_email: str,
) -> LeadClientRecharge:
    """Verifies payment signature, activates channel on active plan, and syncs AutoPay for next cycle."""
    order_id = payload.get("razorpay_order_id")
    payment_id = payload.get("razorpay_payment_id")
    signature = payload.get("razorpay_signature")
    channel = (payload.get("channel") or "").lower().strip()

    if not order_id or not payment_id or not signature or not channel:
        raise ValueError("Missing parameters (order_id, payment_id, signature, channel)")

    rzp = get_razorpay_client()
    try:
        rzp.utility.verify_payment_signature({
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        })
    except Exception as sig_err:
        logger.error(f"[Billing Addon] Signature verification failed for order {order_id}: {sig_err}")
        raise ValueError(f"Invalid payment signature: {sig_err}") from sig_err

    active_plan = get_active_recharge(db, client_id)
    if not active_plan:
        raise ValueError("No active plan found to attach channel add-on.")

    now = utcnow()

    # 1. Activate channel for the remainder of this cycle
    curr_active = list(active_plan.ActiveChannels or [])
    if channel not in curr_active:
        curr_active.append(channel)
    active_plan.ActiveChannels = curr_active

    # 2. Sync channel into next cycle AutoPay renewal bundle
    curr_next = list(active_plan.NextCycleChannels or curr_active)
    if channel not in curr_next:
        curr_next.append(channel)
    active_plan.NextCycleChannels = curr_next

    # 3. Update the pending order record to completed
    pending = (
        db.query(LeadClientRecharge)
        .filter(
            LeadClientRecharge.ClientId == client_id,
            LeadClientRecharge.RazorpayOrderId == order_id,
        )
        .first()
    )
    if pending:
        pending.Status = RECHARGE_STATUS_SUPERSEDED
        pending.PaymentReference = payment_id
        pending.RechargedAt = now
        pending.ExpiresAt = active_plan.ExpiresAt
        db.add(pending)

    # 4. Generate invoice for the prorated add-on charge
    try:
        from Domain.models import Client
        from ..services import invoice as invoice_svc

        client_obj = db.get(Client, client_id)
        if pending:
            inv_id = invoice_svc.build_invoice_number(pending.Id, now)
            pending.InvoiceId = inv_id
            pending.InvoiceUrl = f"/api/leadai/billing/invoices/{pending.Id}/download"

            html_content = invoice_svc.render_invoice_html(
                recharge=pending,
                client=client_obj,
                user_email=user_email,
            )
            pdf_bytes = invoice_svc.generate_invoice_pdf(html_content)
            recip_email = getattr(client_obj, "Email", None) or getattr(client_obj, "ContactEmail", None)
            if recip_email:
                invoice_svc.send_invoice_email(
                    to_email=recip_email,
                    subject=f"LeadAI Billing - Add-on Invoice {inv_id} ({channel.title()})",
                    html_content=html_content,
                    pdf_bytes=pdf_bytes,
                    filename=f"Invoice_{inv_id}.pdf",
                )
    except Exception as inv_err:
        logger.warning(f"[Billing Addon] Invoice generation error: {inv_err}")

    db.add(active_plan)
    db.commit()
    db.refresh(active_plan)

    logger.info(
        f"[Billing Addon] Successfully activated channel '{channel}' for client {client_id}. Active until {active_plan.ExpiresAt}. Synced for upcoming AutoPay."
    )
    return active_plan


def check_channel_access(db: Session, client_id: str, channel: str) -> tuple[bool, str]:
    """
    Checks whether a client company has an active, valid subscription that includes
    the specified social channel (WhatsApp, Instagram, Facebook Messenger, LinkedIn).
    
    Returns (has_access: bool, error_reason: str).
    """
    ch_norm = (channel or "").strip().lower()

    # Core non-social channels do not require channel add-ons
    if ch_norm in ("sms", "email", "web", "voice"):
        return True, ""

    active = get_active_recharge(db, client_id)
    if not active:
        return False, "No active subscription plan found. Please recharge or subscribe to an active plan with channel add-ons to perform actions on this channel."

    # Validate active status (both ACTIVE and zero-minute plans remain valid for channels until expiry)
    if active.Status not in (RECHARGE_STATUS_ACTIVE, RECHARGE_STATUS_EXHAUSTED):
        return False, f"Your subscription is not active (status: {active.Status}). An active plan is required to perform actions on this channel."

    # Validate expiration
    if active.ExpiresAt and _is_expired(active.ExpiresAt, utcnow()):
        exp_str = active.ExpiresAt.strftime('%Y-%m-%d %H:%M UTC')
        return False, f"Your subscription expired on {exp_str}. Please renew your plan to continue using this channel."

    # Map aliases & user-friendly labels
    if ch_norm in ("facebook", "messenger"):
        target_keys = {"facebook", "messenger"}
        channel_name = "Facebook Messenger"
    elif ch_norm in ("whatsapp", "wa"):
        target_keys = {"whatsapp", "wa"}
        channel_name = "WhatsApp Bot"
    elif ch_norm in ("instagram", "ig"):
        target_keys = {"instagram", "ig"}
        channel_name = "Instagram DM Automation"
    elif ch_norm in ("linkedin", "li"):
        target_keys = {"linkedin", "li"}
        channel_name = "LinkedIn Lead Gen"
    else:
        target_keys = {ch_norm}
        channel_name = channel.title()

    # Collect active channels
    active_channels = set((c or "").strip().lower() for c in (active.ActiveChannels or []))

    # Auto-repair / fallback if ActiveChannels is empty on older records
    if not active_channels:
        if active.PlanTemplateId:
            template = db.query(LeadRechargePlanTemplate).filter(LeadRechargePlanTemplate.Id == active.PlanTemplateId).first()
            if template and template.AddonChannels:
                active_channels = set((c or "").strip().lower() for c in template.AddonChannels)
                active.ActiveChannels = list(template.AddonChannels)
                try:
                    db.commit()
                except Exception:
                    db.rollback()
        if not active_channels and active.PlanNameSnapshot:
            plan_lower = active.PlanNameSnapshot.lower()
            found = [c for c in ("whatsapp", "instagram", "facebook", "linkedin") if c in plan_lower]
            if found:
                active_channels = set(found)
                active.ActiveChannels = found
                try:
                    db.commit()
                except Exception:
                    db.rollback()

    if not active_channels.intersection(target_keys):
        return False, f"Your active plan does not include '{channel_name}'. Please add this channel add-on from Billing & Prepaid Recharges to perform actions on it."

    return True, ""



