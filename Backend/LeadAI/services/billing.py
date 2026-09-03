"""
Prepaid Billing & Call Quota Management Service for LeadAI.

Handles plan template seeding, active recharge resolution, exact-second usage
deductions, pending plan queueing, and immediate call termination upon quota exhaustion.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models import (
    PLAN_TYPE_CUSTOM,
    PLAN_TYPE_STANDARD,
    RECHARGE_STATUS_ACTIVE,
    RECHARGE_STATUS_EXHAUSTED,
    RECHARGE_STATUS_EXPIRED,
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
                    LeadRechargePlanTemplate.IsActive == True,  # noqa: E712
                )
                .first()
            )
            if not existing:
                template = LeadRechargePlanTemplate(
                    Name=plan_def["name"],
                    PlanType=plan_def["plan_type"],
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

    # 1. Check current active plan
    active = (
        db.query(LeadClientRecharge)
        .filter(
            LeadClientRecharge.ClientId == client_id,
            LeadClientRecharge.Status == RECHARGE_STATUS_ACTIVE,
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
    """Check if client has active quota to make calls.
    
    Returns (has_quota, reason, remaining_minutes).
    """
    recharge = get_active_recharge(db, client_id)
    if not recharge:
        return False, "No active recharge plan. Please purchase a recharge plan to make calls.", 0.0

    now = utcnow()
    if recharge.ExpiresAt and _is_expired(recharge.ExpiresAt, now):
        return False, "Your recharge plan has expired. Please top up to continue calling.", 0.0

    if recharge.RemainingMinutes <= 0.0001:
        return False, "Your recharge plan minutes are exhausted. Please top up to continue calling.", 0.0

    return True, "Active plan available", recharge.RemainingMinutes


def deduct_call_usage(
    db: Session,
    client_id: str,
    call_sid: str,
    duration_seconds: int,
    conversation_id: Optional[str] = None,
) -> Tuple[float, float, bool]:
    """Deduct exact floating point minutes from active plan based on call duration.
    
    Returns (minutes_deducted, remaining_balance, is_exhausted).
    If balance drops to 0, marks status exhausted, logs event, and triggers active call termination.
    """
    if duration_seconds <= 0:
        active = get_active_recharge(db, client_id)
        bal = active.RemainingMinutes if active else 0.0
        return 0.0, bal, False

    # Exact floating point minutes (e.g. 75 seconds = 1.25 minutes)
    minutes_deducted = round(duration_seconds / 60.0, 4)

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
        return minutes_deducted, 0.0, True

    prev_balance = recharge.RemainingMinutes
    new_balance = max(0.0, round(prev_balance - minutes_deducted, 4))
    recharge.RemainingMinutes = new_balance

    is_exhausted = new_balance <= 0.0001
    if is_exhausted:
        recharge.Status = RECHARGE_STATUS_EXHAUSTED

    # Audit usage log
    log_entry = LeadUsageLog(
        ClientId=client_id,
        RechargeId=recharge.Id,
        CallSid=call_sid,
        ConversationId=conversation_id,
        CallDurationSeconds=duration_seconds,
        MinutesDeducted=minutes_deducted,
        PreviousBalance=prev_balance,
        NewBalance=new_balance,
        DeductedAt=utcnow(),
    )
    db.add(recharge)
    db.add(log_entry)
    db.commit()

    logger.info(
        f"[Billing] Deducted {minutes_deducted:.4f} mins ({duration_seconds}s) for call {call_sid}. "
        f"Client {client_id} balance: {prev_balance:.4f} -> {new_balance:.4f} mins."
    )

    # User Requirement: "as soon as the balance ends no matter how many calls are going on end them all"
    if is_exhausted:
        _terminate_all_active_client_calls(client_id)
        # Try activating next pending plan if available
        get_active_recharge(db, client_id)

    return minutes_deducted, new_balance, is_exhausted


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
) -> LeadClientRecharge:
    """Allocate a standard or custom plan to a client.
    
    If an active plan is running, the new plan is queued as PENDING.
    """
    ensure_default_templates(db)
    now = utcnow()

    if template_id:
        template = db.get(LeadRechargePlanTemplate, template_id)
        if not template:
            raise ValueError(f"Plan template {template_id} not found")
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
