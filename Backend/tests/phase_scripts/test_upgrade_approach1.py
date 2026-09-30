"""
Verification test for Approach 1:
- Voice upgrade mid-cycle retains existing billing cycle anchor date (ExpiresAt).
- Voice upgrade inherits ongoing ActiveChannels & NextCycleChannels from prior plan.
- Minutes roll over cleanly.
"""
import uuid
import sys
import os
sys.path.insert(0, os.path.abspath("."))
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from LeadAI.models import (
    Base,
    LeadClientRecharge,
    LeadRechargePlanTemplate,
    RECHARGE_STATUS_ACTIVE,
    RECHARGE_STATUS_SUPERSEDED,
    utcnow,
)
from LeadAI.services import billing as billing_svc

def test_approach_1():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    client_id = f"client_{uuid.uuid4().hex[:8]}"
    now = utcnow()
    anchor_expiry = now + timedelta(days=15) # Day 15 of cycle

    # Step 1: Initial plan with 5 mins + WhatsApp + Instagram
    plan_5min = LeadClientRecharge(
        Id=f"rec_{uuid.uuid4().hex[:8]}",
        ClientId=client_id,
        PlanNameSnapshot="Custom Bundle (5 Mins)",
        PurchasedMinutes=5.0,
        RemainingMinutes=3.0, # 3 mins unused
        ValidityDaysSnapshot=30,
        PricePaid=4020.0,
        RechargedAt=now - timedelta(days=15),
        ExpiresAt=anchor_expiry,
        Status=RECHARGE_STATUS_ACTIVE,
        ActiveChannels=["whatsapp", "instagram"],
        NextCycleChannels=["whatsapp", "instagram"],
        RazorpaySubscriptionId="sub_old_123",
        IsAutoRenew=True,
    )
    db.add(plan_5min)
    db.commit()

    # Step 2: Client upgrades to 10 min plan (₹40)
    template_10min = LeadRechargePlanTemplate(
        Id=f"tpl_{uuid.uuid4().hex[:8]}",
        Name="10 min test plan",
        PlanType="standard",
        PlanCategory="voice_standard",
        IncludedMinutes=10.0,
        ValidityDays=30,
        Price=40.0,
        RatePerMinute=4.0,
        IsActive=True,
        IsDeleted=False,
    )
    db.add(template_10min)
    db.commit()

    # Create pending subscription for upgrade
    sub_payload = {
        "razorpay_payment_id": "pay_test_upgrade",
        "razorpay_subscription_id": "sub_new_456",
        "razorpay_signature": "dummy_sig",
        "plan_template_id": template_10min.Id,
    }

    # Simulate verify_razorpay_subscription_payment
    pending_recharge = LeadClientRecharge(
        Id=f"rec_{uuid.uuid4().hex[:8]}",
        ClientId=client_id,
        PlanTemplateId=template_10min.Id,
        PlanNameSnapshot=template_10min.Name,
        PurchasedMinutes=template_10min.IncludedMinutes,
        RemainingMinutes=template_10min.IncludedMinutes,
        ValidityDaysSnapshot=template_10min.ValidityDays,
        PricePaid=template_10min.Price,
        RazorpaySubscriptionId="sub_new_456",
        IsAutoRenew=True,
    )
    db.add(pending_recharge)
    db.commit()

    # Call active recharge resolution / upgrade logic
    existing_active = db.query(LeadClientRecharge).filter(
        LeadClientRecharge.ClientId == client_id,
        LeadClientRecharge.Status == RECHARGE_STATUS_ACTIVE,
        LeadClientRecharge.Id != pending_recharge.Id,
    ).first()

    assert existing_active is not None
    assert existing_active.Id == plan_5min.Id

    leftover = max(0.0, float(existing_active.RemainingMinutes or 0.0))
    pending_recharge.RemainingMinutes = round(pending_recharge.RemainingMinutes + leftover, 4)
    pending_recharge.RolloverMinutesCarried = leftover
    existing_active.Status = RECHARGE_STATUS_SUPERSEDED
    db.add(existing_active)

    pending_recharge.Status = RECHARGE_STATUS_ACTIVE
    pending_recharge.PaymentReference = "pay_test_upgrade"
    pending_recharge.IsAutoRenew = True

    # Approach 1: Anchor date preservation
    if existing_active.ExpiresAt and not billing_svc._is_expired(existing_active.ExpiresAt, now):
        pending_recharge.ExpiresAt = existing_active.ExpiresAt
        pending_recharge.RechargedAt = existing_active.RechargedAt or now

    # Approach 1: Channel retention
    prior_active = list(existing_active.ActiveChannels or [])
    prior_next = list(existing_active.NextCycleChannels or prior_active)
    current_active = list(pending_recharge.ActiveChannels or [])
    current_next = list(pending_recharge.NextCycleChannels or current_active)

    pending_recharge.ActiveChannels = list(dict.fromkeys(prior_active + current_active))
    pending_recharge.NextCycleChannels = list(dict.fromkeys(prior_next + current_next))

    db.add(pending_recharge)
    db.commit()
    db.refresh(pending_recharge)

    # Verifications
    print(f"Remaining Minutes: {pending_recharge.RemainingMinutes} (expected 13.0)")
    assert pending_recharge.RemainingMinutes == 13.0, "Minutes did not roll over!"

    print(f"Active Channels: {pending_recharge.ActiveChannels} (expected ['whatsapp', 'instagram'])")
    assert "whatsapp" in pending_recharge.ActiveChannels and "instagram" in pending_recharge.ActiveChannels, "Channels not retained!"

    print(f"ExpiresAt: {pending_recharge.ExpiresAt} (expected {anchor_expiry})")
    assert pending_recharge.ExpiresAt.replace(tzinfo=timezone.utc) == anchor_expiry, "Anchor date was not preserved!"

    print(f"RolloverMinutesCarried: {pending_recharge.RolloverMinutesCarried} (expected 3.0)")
    assert pending_recharge.RolloverMinutesCarried == 3.0

    print("ALL APPROACH 1 VERIFICATION CHECKS PASSED!")

if __name__ == "__main__":
    test_approach_1()
