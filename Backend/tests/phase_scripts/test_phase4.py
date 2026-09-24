"""
Phase 4 Verification Script:
1. Bug #9: Yearly Bundle Voice Minutes Multiplier (12x monthly voice minutes credited for annual plans).
2. Bug #8: Proration based on benchmark daily rate (monthly_price / 30.0 * remaining_days).
3. Bug #6: Cancel specific channel add-on mid-cycle (remains active until cycle ends, removed from NextCycleChannels, AutoPay renewal sync).
"""
import sys
import os
import math
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

BACKEND_DIR = r"c:\Users\Pratik\work\LeadAI\Backend"
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from core.base import Base
from LeadAI.models import (
    LeadClientRecharge,
    LeadRechargePlanTemplate,
    LeadUsageLog,
    RECHARGE_STATUS_ACTIVE,
    RECHARGE_STATUS_PENDING,
)
from LeadAI.schemas import CustomBundleSubscriptionCreate
from LeadAI.services.billing import (
    create_custom_bundle_subscription,
    get_channel_addon_quote,
    cancel_channel_for_next_cycle,
    handle_razorpay_webhook,
)

# SQLite In-Memory Database for clean, isolated testing
engine = create_engine("sqlite:///:memory:", echo=False)
Base.metadata.create_all(bind=engine)
SessionLocal = sessionmaker(bind=engine)


def test_bug_9_yearly_bundle_minutes():
    print("\n--- Testing Bug #9: Yearly Bundle Minute Allotment (12x) ---")
    db = SessionLocal()
    client_id = f"client_bug9_{uuid4().hex[:6]}"

    mock_rzp_client = MagicMock()
    mock_rzp_client.plan.create.return_value = {"id": "plan_mock_yearly"}
    mock_rzp_client.subscription.create.return_value = {
        "id": "sub_mock_yearly",
        "short_url": "https://rzp.io/i/sub_mock_yearly",
        "status": "created",
    }

    with patch("LeadAI.services.billing.get_razorpay_client", return_value=mock_rzp_client):
        # 1. Yearly Bundle with 500 mins/month
        payload_yearly = CustomBundleSubscriptionCreate(
            include_voice=True,
            voice_minutes=500.0,
            channels=["whatsapp", "instagram"],
            billing_cycle="yearly",
        )
        res_yearly = create_custom_bundle_subscription(
            db=db,
            client_id=client_id,
            payload=payload_yearly.model_dump(),
            user_email="test@example.com",
        )
        recharge_yearly = db.query(LeadClientRecharge).filter_by(RazorpaySubscriptionId="sub_mock_yearly").first()

        assert recharge_yearly is not None, "Yearly subscription recharge record should exist"
        # 500 mins * 12 months = 6000 mins
        assert recharge_yearly.PurchasedMinutes == 6000.0, f"Expected 6000.0 PurchasedMinutes, got {recharge_yearly.PurchasedMinutes}"
        assert recharge_yearly.RemainingMinutes == 6000.0, f"Expected 6000.0 RemainingMinutes, got {recharge_yearly.RemainingMinutes}"
        assert recharge_yearly.ValidityDaysSnapshot == 365, f"Validity must be 365 days, got {recharge_yearly.ValidityDaysSnapshot}"
        print(f"PASSED: Yearly bundle with 500/mo granted {recharge_yearly.PurchasedMinutes} mins with {recharge_yearly.ValidityDaysSnapshot} days validity.")

        # 2. Monthly Bundle with 500 mins/month
        client_monthly_id = f"client_bug9_m_{uuid4().hex[:6]}"
        mock_rzp_client.subscription.create.return_value = {
            "id": "sub_mock_monthly",
            "short_url": "https://rzp.io/i/sub_mock_monthly",
            "status": "created",
        }
        payload_monthly = CustomBundleSubscriptionCreate(
            include_voice=True,
            voice_minutes=500.0,
            channels=["whatsapp"],
            billing_cycle="monthly",
        )
        res_monthly = create_custom_bundle_subscription(
            db=db,
            client_id=client_monthly_id,
            payload=payload_monthly.model_dump(),
            user_email="test@example.com",
        )
        recharge_monthly = db.query(LeadClientRecharge).filter_by(RazorpaySubscriptionId="sub_mock_monthly").first()

        assert recharge_monthly is not None, "Monthly subscription recharge record should exist"
        assert recharge_monthly.PurchasedMinutes == 500.0, f"Expected 500.0 PurchasedMinutes, got {recharge_monthly.PurchasedMinutes}"
        assert recharge_monthly.ValidityDaysSnapshot == 30, f"Validity must be 30 days, got {recharge_monthly.ValidityDaysSnapshot}"
        print(f"PASSED: Monthly bundle granted {recharge_monthly.PurchasedMinutes} mins with {recharge_monthly.ValidityDaysSnapshot} days validity.")
    db.close()


def test_bug_8_benchmark_daily_rate_proration():
    print("\n--- Testing Bug #8: Benchmark Daily-Rate Proration Math ---")
    db = SessionLocal()

    # Case 1: Monthly Plan with exactly 18 days remaining
    client_monthly = f"client_monthly_{uuid4().hex[:6]}"
    now = datetime.now(timezone.utc)
    rec_monthly = LeadClientRecharge(
        ClientId=client_monthly,
        PlanNameSnapshot="Starter Monthly",
        PricePaid=2999.0,
        PurchasedMinutes=500.0,
        RemainingMinutes=500.0,
        ValidityDaysSnapshot=30,
        Status=RECHARGE_STATUS_ACTIVE,
        ActiveChannels=["voice"],
        NextCycleChannels=["voice"],
        RechargedAt=now - timedelta(days=12),
        ExpiresAt=now + timedelta(days=18),
    )
    db.add(rec_monthly)
    db.commit()

    quote_monthly = get_channel_addon_quote(db, client_monthly, "facebook")
    # Facebook monthly benchmark = 799.0.
    # Daily benchmark = 799.0 / 30.0 = 26.6333...
    # 18 days * 26.6333... = 479.40
    expected_price_m = round((quote_monthly["monthly_price"] / 30.0) * 18, 2)
    assert quote_monthly["remaining_days"] == 18, f"Expected 18 days remaining, got {quote_monthly['remaining_days']}"
    assert abs(quote_monthly["prorated_price"] - expected_price_m) < 0.05, (
        f"Expected ~{expected_price_m}, got {quote_monthly['prorated_price']}"
    )
    print(f"PASSED: Monthly plan 18 days proration = Rs. {quote_monthly['prorated_price']} (daily benchmark rate).")

    # Case 2: Annual Plan with 300 days remaining
    client_yearly = f"client_yearly_{uuid4().hex[:6]}"
    rec_yearly = LeadClientRecharge(
        ClientId=client_yearly,
        PlanNameSnapshot="Enterprise Yearly",
        PricePaid=29990.0,
        PurchasedMinutes=6000.0,
        RemainingMinutes=6000.0,
        ValidityDaysSnapshot=365,
        Status=RECHARGE_STATUS_ACTIVE,
        ActiveChannels=["voice"],
        NextCycleChannels=["voice"],
        RechargedAt=now - timedelta(days=65),
        ExpiresAt=now + timedelta(days=300),
    )
    db.add(rec_yearly)
    db.commit()

    quote_yearly = get_channel_addon_quote(db, client_yearly, "facebook")
    expected_price_y = round((quote_yearly["monthly_price"] / 30.0) * 300, 2)
    assert quote_yearly["remaining_days"] == 300, f"Expected 300 days remaining, got {quote_yearly['remaining_days']}"
    assert abs(quote_yearly["prorated_price"] - expected_price_y) < 0.05, (
        f"Expected ~{expected_price_y}, got {quote_yearly['prorated_price']}"
    )
    print(f"PASSED: Annual plan 300 days proration = Rs. {quote_yearly['prorated_price']} without annual divisor distortions.")
    db.close()


def test_bug_6_cancel_specific_channel():
    print("\n--- Testing Bug #6: Cancel Specific Channel Add-on Mid-Cycle ---")
    db = SessionLocal()
    client_id = f"client_bug6_{uuid4().hex[:6]}"
    now = datetime.now(timezone.utc)

    # Active plan with Voice + WhatsApp + Instagram
    rec = LeadClientRecharge(
        ClientId=client_id,
        PlanNameSnapshot="Pro Bundle",
        PricePaid=4999.0,
        PurchasedMinutes=1000.0,
        RemainingMinutes=800.0,
        ValidityDaysSnapshot=30,
        Status=RECHARGE_STATUS_ACTIVE,
        ActiveChannels=["voice", "whatsapp", "instagram"],
        NextCycleChannels=["voice", "whatsapp", "instagram"],
        RazorpaySubscriptionId="sub_test_bug6",
        RechargedAt=now - timedelta(days=10),
        ExpiresAt=now + timedelta(days=20),
    )
    db.add(rec)
    db.commit()

    # 1. Attempt to cancel voice (base service) -> should raise ValueError or HTTPException
    try:
        cancel_channel_for_next_cycle(db, client_id, "voice")
        assert False, "Should not allow cancelling voice channel via cancel_channel"
    except (ValueError, HTTPException) as e:
        print("PASSED: Correctly blocked cancelling base 'voice' channel (requires subscription cancel).")

    # 2. Attempt to cancel a channel not active -> should raise ValueError or HTTPException
    try:
        cancel_channel_for_next_cycle(db, client_id, "linkedin")
        assert False, "Should not allow cancelling an inactive channel"
    except (ValueError, HTTPException) as e:
        print("PASSED: Correctly rejected cancelling 'linkedin' which is not in active channels.")

    # 3. Cancel Instagram mid-cycle
    res = cancel_channel_for_next_cycle(db, client_id, "instagram")
    db.refresh(rec)

    # Key Assertions:
    # A) Instagram MUST remain in ActiveChannels for the remainder of the paid cycle
    assert "instagram" in rec.ActiveChannels, "Customer paid for Instagram, it must remain active until ExpiresAt!"
    assert "whatsapp" in rec.ActiveChannels
    assert "voice" in rec.ActiveChannels

    # B) Instagram MUST be removed from NextCycleChannels
    assert "instagram" not in rec.NextCycleChannels, "Instagram must be excluded from NextCycleChannels!"
    assert "whatsapp" in rec.NextCycleChannels
    assert "voice" in rec.NextCycleChannels
    print(f"PASSED: Mid-cycle cancel: ActiveChannels={rec.ActiveChannels}, NextCycleChannels={rec.NextCycleChannels}")

    # 4. Verify Next Cycle Renewal carries only NextCycleChannels
    renewal_payload = {
        "event": "subscription.charged",
        "payload": {
            "subscription": {
                "entity": {
                    "id": "sub_test_bug6",
                    "status": "active",
                    "current_end": int((now + timedelta(days=50)).timestamp()),
                }
            },
            "payment": {
                "entity": {
                    "id": "pay_renew_12345",
                    "amount": 349900,
                    "status": "captured",
                }
            }
        }
    }

    import json
    raw_body = json.dumps(renewal_payload).encode("utf-8")
    mock_rzp = MagicMock()
    mock_rzp.utility.verify_webhook_signature.return_value = True
    with patch("LeadAI.services.billing.get_razorpay_client", return_value=mock_rzp):
        webhook_res = handle_razorpay_webhook(
            db,
            renewal_payload,
            signature="mock_sig",
            raw_body=raw_body,
        )
        assert webhook_res["status"] == "renewed"

        # Find the renewal recharge record
        renewal_rec = db.query(LeadClientRecharge).filter_by(
            PaymentReference="pay_renew_12345"
        ).first()
        assert renewal_rec is not None, "Renewal recharge record must exist"
        assert "instagram" not in renewal_rec.ActiveChannels, "New renewal cycle must NOT include cancelled channel!"
        assert "whatsapp" in renewal_rec.ActiveChannels
        assert "voice" in renewal_rec.ActiveChannels
        print(f"PASSED: AutoPay renewal activated new cycle with channels: {renewal_rec.ActiveChannels}")

    db.close()


if __name__ == "__main__":
    print("=== STARTING PHASE 4 VERIFICATION TESTS ===")
    test_bug_9_yearly_bundle_minutes()
    test_bug_8_benchmark_daily_rate_proration()
    test_bug_6_cancel_specific_channel()
    print("\nALL PHASE 4 TESTS PASSED PERFECTLY!")
