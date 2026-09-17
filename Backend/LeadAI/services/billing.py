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
                    PlanCategory=plan_def["plan_type"] or "standard",
                    TargetClientId=None,
                    IncludedMinutes=plan_def["included_minutes"],
                    ValidityDays=plan_def["validity_days"],
                    Price=plan_def["price"],
                    RatePerMinute=plan_def["rate_per_minute"],
                    Description=plan_def["description"],
                    IsActive=True,
                )
                db.add(template)
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


def get_active_recharge(db: Session, client_id: str) -> Optional[LeadClientRecharge]:
    """Retrieve current active recharge for a client.
    
    Auto-activates queued pending plans if current active plan is expired or exhausted.
    """
    ensure_default_templates(db)
    now = utcnow()

    # 1. Check current active plan (prioritize primary plans with ValidityDays > 0)
    active = (
        db.query(LeadClientRecharge)
        .filter(
            LeadClientRecharge.ClientId == client_id,
            LeadClientRecharge.Status == RECHARGE_STATUS_ACTIVE,
        )
        .order_by(
            LeadClientRecharge.ValidityDaysSnapshot.desc(),
            LeadClientRecharge.ExpiresAt.desc(),
        )
        .first()
    )

    if active:
        # Check expiration
        is_expired = active.ExpiresAt and _is_expired(active.ExpiresAt, now)
        is_exhausted = active.RemainingMinutes <= 0.0001

        if is_expired or is_exhausted:
            active.Status = RECHARGE_STATUS_EXPIRED if is_expired else RECHARGE_STATUS_EXHAUSTED
            db.add(active)
            db.commit()
            active = None  # Force pending resolution below

    # 2. If no active plan, check for oldest pending plan to activate
    if not active:
        pending = (
            db.query(LeadClientRecharge)
            .filter(
                LeadClientRecharge.ClientId == client_id,
                LeadClientRecharge.Status == RECHARGE_STATUS_PENDING,
            )
            .order_by(LeadClientRecharge.CreatedAt.asc())
            .first()
        )
        if pending:
            pending.Status = RECHARGE_STATUS_ACTIVE
            pending.RechargedAt = now
            pending.ExpiresAt = now + timedelta(days=pending.ValidityDaysSnapshot)
            db.add(pending)
            db.commit()
            db.refresh(pending)
            logger.info(
                f"[Billing] Activated pending recharge {pending.Id} for client {client_id} "
                f"({pending.RemainingMinutes} mins, valid until {pending.ExpiresAt})"
            )
            active = pending

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
    if is_exhausted:
        recharge.Status = RECHARGE_STATUS_EXHAUSTED

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
            LeadClientRecharge.Status == RECHARGE_STATUS_ACTIVE,
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
    if is_exhausted:
        recharge.Status = RECHARGE_STATUS_EXHAUSTED

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
            # Check if this call belongs to the exhausted client
            if call_info.get("client_id") == client_id or call_info.get("leadai"):
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
    is_topup: Optional[bool] = False,
) -> LeadClientRecharge:
    """Allocate a standard or custom plan to a client, or top up an active unexpired plan.
    
    If is_topup=True or custom_validity_days is None without template_id:
      Adds minutes directly to the client's current active/exhausted unexpired plan
      and retains the exact existing ExpiresAt date.
    Otherwise:
      Allocates a full plan. If an active plan is already running, the new plan is queued as PENDING.
    """
    ensure_default_templates(db)
    now = utcnow()

    template = db.get(LeadRechargePlanTemplate, template_id) if template_id else None
    is_topup_flow = (
        bool(is_topup)
        or (template is not None and (template.PlanType == PLAN_TYPE_TOPUP or not template.ValidityDays))
        or (not template_id and custom_validity_days is None)
    )

    # -----------------------------------------------------------------------
    # 1. Handle Top-Up Mode (Inherits existing active plan's expiry)
    # -----------------------------------------------------------------------
    if is_topup_flow:
        add_mins = template.IncludedMinutes if template else float(custom_minutes or 0.0)
        topup_price = template.Price if template else float(custom_price or 0.0)

        if add_mins <= 0:
            raise ValueError("Top-up requires minutes greater than 0")

        # Find primary active or exhausted plan whose validity has not expired
        active = (
            db.query(LeadClientRecharge)
            .filter(
                LeadClientRecharge.ClientId == client_id,
                LeadClientRecharge.Status.in_([RECHARGE_STATUS_ACTIVE, RECHARGE_STATUS_EXHAUSTED]),
                LeadClientRecharge.ValidityDaysSnapshot > 0,
            )
            .order_by(LeadClientRecharge.ExpiresAt.desc())
            .with_for_update()
            .first()
        )

        if not active or not active.ExpiresAt or _is_expired(active.ExpiresAt, now):
            raise ValueError(
                "Client does not have an active subscription plan to top up. "
                "Top-up minutes can only be added to a plan before its validity expiration date. "
                "Please allocate a new plan with validity days."
            )

        prev_balance = active.RemainingMinutes
        new_balance = round(prev_balance + float(add_mins), 4)

        active.RemainingMinutes = new_balance
        active.PurchasedMinutes = round(active.PurchasedMinutes + float(add_mins), 4)
        active.Status = RECHARGE_STATUS_ACTIVE  # Revives exhausted status if minutes ran out earlier
        if topup_price:
            active.PricePaid = round(active.PricePaid + float(topup_price), 2)
        active.UpdatedBy = created_by
        active.UpdatedAt = now

        # Add top-up audit log to usage ledger (negative deduction represents credit)
        log_entry = LeadUsageLog(
            ClientId=client_id,
            RechargeId=active.Id,
            CallSid=f"TOPUP-{int(now.timestamp())}",
            ConversationId=None,
            CallDurationSeconds=0,
            MinutesDeducted=-float(add_mins),
            PreviousBalance=prev_balance,
            NewBalance=new_balance,
            DeductedAt=now,
        )
        db.add(active)
        db.add(log_entry)
        db.commit()
        db.refresh(active)

        logger.info(
            f"[Billing Top-Up] Added {add_mins} mins to active recharge {active.Id} for client {client_id}. "
            f"Balance: {prev_balance:.0f} -> {new_balance:.0f} mins. Expiry unchanged: {active.ExpiresAt}."
        )
        return active

    # -----------------------------------------------------------------------
    # 2. Handle Standard / Custom Full Plan Allocation
    # -----------------------------------------------------------------------
    if template:
        if template.IsDeleted or not template.IsActive:
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

    # Check if client has an existing ACTIVE plan
    existing_active = (
        db.query(LeadClientRecharge)
        .filter(
            LeadClientRecharge.ClientId == client_id,
            LeadClientRecharge.Status == RECHARGE_STATUS_ACTIVE,
        )
        .first()
    )

    # User Requirement: queue as pending if an active plan exists
    if existing_active and existing_active.RemainingMinutes > 0.0001 and (not existing_active.ExpiresAt or not _is_expired(existing_active.ExpiresAt, now)):
        initial_status = RECHARGE_STATUS_PENDING
        recharged_at = None
        expires_at = None
    else:
        initial_status = RECHARGE_STATUS_ACTIVE
        recharged_at = now
        expires_at = now + timedelta(days=validity)

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

    # If it's a Top-Up plan, ensure client has an active unexpired plan before charging
    is_topup_plan = template.PlanType == PLAN_TYPE_TOPUP or not template.ValidityDays
    if is_topup_plan:
        active_plan = (
            db.query(LeadClientRecharge)
            .filter(
                LeadClientRecharge.ClientId == client_id,
                LeadClientRecharge.Status.in_([RECHARGE_STATUS_ACTIVE, RECHARGE_STATUS_EXHAUSTED]),
            )
            .order_by(LeadClientRecharge.ExpiresAt.desc())
            .first()
        )
        if not active_plan or not active_plan.ExpiresAt or _is_expired(active_plan.ExpiresAt, now):
            raise ValueError(
                "Top-Up booster packs require an active subscription plan. "
                "Please purchase a standard monthly or yearly plan first."
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
            "plan_type": template.PlanType,
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
        ValidityDaysSnapshot=template.ValidityDays or 0,
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

    # If no pending recharge row existed for this order, retrieve template
    template = db.get(LeadRechargePlanTemplate, plan_template_id or (recharge.PlanTemplateId if recharge else None))
    if not template and recharge and recharge.PlanTemplateId:
        template = db.get(LeadRechargePlanTemplate, recharge.PlanTemplateId)

    is_topup_plan = (template and template.PlanType == PLAN_TYPE_TOPUP) or (recharge and recharge.ValidityDaysSnapshot == 0)

    # -----------------------------------------------------------------------
    # Case A: Top-Up Booster Plan Purchase (Merges directly into Active Plan)
    # -----------------------------------------------------------------------
    if is_topup_plan:
        active_plan = (
            db.query(LeadClientRecharge)
            .filter(
                LeadClientRecharge.ClientId == client_id,
                LeadClientRecharge.Status.in_([RECHARGE_STATUS_ACTIVE, RECHARGE_STATUS_EXHAUSTED]),
                LeadClientRecharge.ValidityDaysSnapshot > 0,
            )
            .order_by(LeadClientRecharge.ExpiresAt.desc())
            .with_for_update()
            .first()
        )

        topup_mins = template.IncludedMinutes if template else (recharge.PurchasedMinutes if recharge else 0.0)
        topup_price = template.Price if template else (recharge.PricePaid if recharge else 0.0)

        if active_plan and active_plan.ExpiresAt and not _is_expired(active_plan.ExpiresAt, now):
            prev_balance = active_plan.RemainingMinutes
            new_balance = round(prev_balance + topup_mins, 4)
            active_plan.RemainingMinutes = new_balance
            active_plan.PurchasedMinutes = round(active_plan.PurchasedMinutes + topup_mins, 4)
            active_plan.Status = RECHARGE_STATUS_ACTIVE
            active_plan.PricePaid = round(active_plan.PricePaid + topup_price, 2)
            active_plan.UpdatedBy = user_email
            active_plan.UpdatedAt = now

            if recharge:
                # Mark as superseded with 0 remaining minutes so it remains as payment invoice history but not a separate active plan
                recharge.Status = RECHARGE_STATUS_SUPERSEDED
                recharge.RemainingMinutes = 0.0
                recharge.PurchasedMinutes = topup_mins
                recharge.RechargedAt = now
                recharge.ExpiresAt = active_plan.ExpiresAt
                recharge.PaymentReference = payment_id
                recharge.FailureReason = None

            # Add top-up usage log credit
            log_entry = LeadUsageLog(
                ClientId=client_id,
                RechargeId=active_plan.Id,
                CallSid=f"TOPUP-{int(now.timestamp())}",
                ConversationId=None,
                CallDurationSeconds=0,
                MinutesDeducted=-float(topup_mins),
                PreviousBalance=prev_balance,
                NewBalance=new_balance,
                DeductedAt=now,
            )
            db.add(active_plan)
            db.add(log_entry)
            db.commit()

            target_recharge = recharge or active_plan
        else:
            # Fallback if no active plan existed
            if recharge:
                recharge.Status = RECHARGE_STATUS_ACTIVE
                recharge.RechargedAt = now
                recharge.ExpiresAt = now + timedelta(days=30)
                recharge.PaymentReference = payment_id
                target_recharge = recharge
            else:
                target_recharge = active_plan

    # -----------------------------------------------------------------------
    # Case B: Standard / Custom Full Plan Purchase
    # -----------------------------------------------------------------------
    else:
        if not recharge:
            if not template:
                raise ValueError(f"Plan template {plan_template_id} not found")
            recharge = LeadClientRecharge(
                ClientId=client_id,
                PlanTemplateId=template.Id,
                PlanNameSnapshot=template.Name,
                PurchasedMinutes=template.IncludedMinutes,
                RemainingMinutes=template.IncludedMinutes,
                ValidityDaysSnapshot=template.ValidityDays or 30,
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
                LeadClientRecharge.Status == RECHARGE_STATUS_ACTIVE,
                LeadClientRecharge.Id != recharge.Id,
            )
            .first()
        )

        if existing_active and existing_active.RemainingMinutes > 0.0001 and (
            not existing_active.ExpiresAt or not _is_expired(existing_active.ExpiresAt, now)
        ):
            recharge.Status = RECHARGE_STATUS_PENDING
            recharge.RechargedAt = None
            recharge.ExpiresAt = None
        else:
            recharge.Status = RECHARGE_STATUS_ACTIVE
            recharge.RechargedAt = now
            recharge.ExpiresAt = now + timedelta(days=recharge.ValidityDaysSnapshot)

        recharge.PaymentReference = payment_id
        recharge.FailureReason = None
        target_recharge = recharge

    recharge_record = target_recharge or recharge

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
    order_id: str,
    error_code: str | None = None,
    error_description: str | None = None,
) -> LeadClientRecharge | None:
    """Records user cancellation or gateway failure for complete history audit."""
    recharge = (
        db.query(LeadClientRecharge)
        .filter(
            LeadClientRecharge.ClientId == client_id,
            LeadClientRecharge.RazorpayOrderId == order_id,
        )
        .first()
    )
    if not recharge:
        return None

    recharge.Status = RECHARGE_STATUS_FAILED
    desc = error_description or "Payment was cancelled or dismissed before completion"
    code = error_code or "DISMISSED"
    recharge.FailureReason = f"[{code}] {desc}"

    db.commit()
    db.refresh(recharge)
    logger.info(f"[Billing] Recorded payment failure for order {order_id}: {recharge.FailureReason}")
    return recharge

