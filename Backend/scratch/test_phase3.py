"""
Phase 3 Verification Script:
1. User Requirement: Call duration ceiling rounding (5s -> 1m, 62s -> 2m, balance in integer minutes)
2. Bug #10: Batch call quota check, client_id resolution, and call_to_config registration
3. Bug #5: Manual outbound call quota check and tenancy binding in active_calls
4. Bug #11: Pure read-only GET /payment-history (preserves PENDING records)
"""
import sys
import os
import math
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

BACKEND_DIR = r"c:\Users\Pratik\work\LeadAI\Backend"
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)

import multiligual_call
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from base import Base
from Domain.models import Batch, CallNumber, CallNumberExecution
from LeadAI.models import (
    LeadClientRecharge,
    LeadRechargePlanTemplate,
    LeadUsageLog,
    LeadUserRole,
    RECHARGE_STATUS_ACTIVE,
    RECHARGE_STATUS_PENDING,
    RECHARGE_STATUS_FAILED,
)
from LeadAI.services.billing import (
    deduct_call_usage,
    check_call_quota,
)
from LeadAI.routers.billing import get_payment_history

# Setup in-memory SQLite DB
engine = create_engine("sqlite:///:memory:", echo=False)
tables_to_create = [
    Batch.__table__,
    CallNumber.__table__,
    CallNumberExecution.__table__,
    LeadClientRecharge.__table__,
    LeadRechargePlanTemplate.__table__,
    LeadUsageLog.__table__,
    LeadUserRole.__table__,
]
Base.metadata.create_all(bind=engine, tables=tables_to_create)
SessionLocal = sessionmaker(bind=engine)


# Configure UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def test_ceiling_rounding_and_integer_minutes():
    print("\n--- Testing Call Duration Ceiling Rounding (5s -> 1m, 62s -> 2m) ---")
    db = SessionLocal()
    client_id = f"client_{uuid4().hex[:8]}"

    recharge = LeadClientRecharge(
        Id=f"rec_{uuid4().hex[:8]}",
        ClientId=client_id,
        PlanNameSnapshot="Starter",
        ValidityDaysSnapshot=30,
        PricePaid=999.0,
        PurchasedMinutes=100.0,
        RemainingMinutes=100.0,
        Status=RECHARGE_STATUS_ACTIVE,
        ExpiresAt=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(recharge)
    db.commit()

    # Case A: 5 second call -> 1 minute deducted
    call_sid_1 = f"CA_{uuid4().hex[:8]}"
    deducted_1, bal_1, _ = deduct_call_usage(db, client_id=client_id, call_sid=call_sid_1, duration_seconds=5)
    assert deducted_1 == 1.0, f"Expected 1.0 min deducted for 5s call, got {deducted_1}"
    assert bal_1 == 99.0, f"Expected 99.0 balance, got {bal_1}"
    print(f"PASS: 5s call deducted {deducted_1} min -> Remaining: {bal_1} mins.")

    # Case B: 62 second (1m 2s) call -> 2 minutes deducted
    call_sid_2 = f"CA_{uuid4().hex[:8]}"
    deducted_2, bal_2, _ = deduct_call_usage(db, client_id=client_id, call_sid=call_sid_2, duration_seconds=62)
    assert deducted_2 == 2.0, f"Expected 2.0 mins deducted for 62s call, got {deducted_2}"
    assert bal_2 == 97.0, f"Expected 97.0 balance, got {bal_2}"
    print(f"PASS: 62s call deducted {deducted_2} mins -> Remaining: {bal_2} mins.")

    # Case C: 121 second (2m 1s) call -> 3 minutes deducted
    call_sid_3 = f"CA_{uuid4().hex[:8]}"
    deducted_3, bal_3, _ = deduct_call_usage(db, client_id=client_id, call_sid=call_sid_3, duration_seconds=121)
    assert deducted_3 == 3.0, f"Expected 3.0 mins deducted for 121s call, got {deducted_3}"
    assert bal_3 == 94.0, f"Expected 94.0 balance, got {bal_3}"
    print(f"PASS: 121s call deducted {deducted_3} mins -> Remaining: {bal_3} mins.")

    # Verify balance is strictly integer
    assert bal_3 == math.floor(bal_3), f"Balance should be exact integer minutes, got {bal_3}"
    print("PASS: Balance remains strictly in clean integer minutes!")
    db.close()


def test_bug_10_batch_telephony_quota_and_tenancy():
    print("\n--- Testing Bug #10: Batching ClientId Resolution & Call Quota ---")
    db = SessionLocal()
    client_id = f"client_{uuid4().hex[:8]}"
    batch_id = f"batch_{uuid4().hex[:8]}"

    # 1. Create Batch with ClientId
    batch = Batch(
        Id=batch_id,
        Name="Test Outbound Campaign",
        ClientId=client_id,
        Email="manager@tenant.com",
        IsDeleted=False,
    )
    call_number = CallNumber(
        Id="cn_1",
        BatchId=batch_id,
        PhoneNumber="+1234567890",
        IsDeleted=False,
    )
    db.add_all([batch, call_number])
    db.commit()

    # 2. Case A: Client has 0 balance -> Batch dial must be blocked with HTTP 402
    empty_recharge = LeadClientRecharge(
        Id=f"rec_{uuid4().hex[:8]}",
        ClientId=client_id,
        PlanNameSnapshot="Starter",
        ValidityDaysSnapshot=30,
        PricePaid=999.0,
        PurchasedMinutes=100.0,
        RemainingMinutes=0.0,  # 0 minutes
        Status=RECHARGE_STATUS_ACTIVE,
        ExpiresAt=datetime.now(timezone.utc) + timedelta(days=20),
    )
    db.add(empty_recharge)
    db.commit()

    from batching import service as batch_service

    # Mock twilio_client on batch_service
    mock_twilio = MagicMock()
    mock_call = MagicMock()
    mock_call.sid = f"CA_batch_{uuid4().hex[:8]}"
    mock_twilio.calls.create.return_value = mock_call
    batch_service.twilio_client = mock_twilio

    def mock_get_leadai_db():
        yield db

    with patch("LeadAI.db.get_leadai_db", mock_get_leadai_db):
        # Attempt to dial when balance is 0
        resp = asyncio.run(batch_service.make_single_call_core_for_batch(
            db=db,
            email="manager@tenant.com",
            to_number="+1234567890",
            batch_id=batch_id,
            batch_execution_id="exec_1",
            call_number_id="cn_1",
        ))
        # Should return JSONResponse with status_code 402
        assert resp.status_code == 402, f"Expected 402 Payment Required, got {resp.status_code}"
        assert not mock_twilio.calls.create.called, "Twilio dial should NOT be called when quota is 0!"
        print("PASS: Batch call blocked with HTTP 402 when company balance is 0 mins.")

        # 3. Case B: Client has balance (e.g. 50 mins) -> Call proceeds and attaches client_id
        empty_recharge.RemainingMinutes = 50.0
        db.commit()

        sid = asyncio.run(batch_service.make_single_call_core_for_batch(
            db=db,
            email="manager@tenant.com",
            to_number="+1234567890",
            batch_id=batch_id,
            batch_execution_id="exec_1",
            call_number_id="cn_1",
        ))
        assert sid == mock_call.sid, f"Expected call SID {mock_call.sid}, got {sid}"
        assert mock_call.sid in batch_service.call_to_config
        cfg = batch_service.call_to_config[mock_call.sid]
        assert cfg.get("client_id") == client_id, f"Expected client_id {client_id}, got {cfg.get('client_id')}"
        print(f"PASS: Batch dial attached client_id ({client_id}) to call_to_config!")

        # 4. Context hydration check in multiligual_call
        multiligual_call._ensure_batch_call_context(mock_call.sid)
        assert mock_call.sid in multiligual_call.active_calls
        active_entry = multiligual_call.active_calls[mock_call.sid]
        assert active_entry.get("client_id") == client_id
        assert active_entry.get("leadai") is True
        print("PASS: _ensure_batch_call_context correctly hydrated active_calls with client_id and leadai=True!")

        # 5. Call deduction reconciliation check
        mock_tw_fetch = MagicMock()
        mock_tw_fetch.duration = "65"  # 65s call -> 2 minutes
        mock_twilio.calls.return_value.fetch.return_value = mock_tw_fetch
        multiligual_call.twilio_client = mock_twilio

        multiligual_call._deduct_billing_usage_for_call(mock_call.sid, active_entry)
        db.refresh(empty_recharge)
        assert empty_recharge.RemainingMinutes == 48.0, f"Expected 48.0 mins (50 - 2), got {empty_recharge.RemainingMinutes}"
        print(f"PASS: Call deduction accurately reduced client balance to {empty_recharge.RemainingMinutes} mins.")

    db.close()


def test_bug_5_manual_outbound_call_quota():
    print("\n--- Testing Bug #5: Manual Call Quota & Tenancy Binding ---")
    db = SessionLocal()
    client_id = f"client_{uuid4().hex[:8]}"

    recharge = LeadClientRecharge(
        Id=f"rec_{uuid4().hex[:8]}",
        ClientId=client_id,
        PlanNameSnapshot="Starter",
        ValidityDaysSnapshot=30,
        PricePaid=999.0,
        PurchasedMinutes=50.0,
        RemainingMinutes=0.0,  # 0 mins
        Status=RECHARGE_STATUS_ACTIVE,
        ExpiresAt=datetime.now(timezone.utc) + timedelta(days=20),
    )
    db.add(recharge)
    db.commit()

    def mock_get_leadai_db():
        yield db

    mock_request = MagicMock()
    mock_request.headers = {"X-Client-Id": client_id}
    mock_request.query_params = {}
    mock_request.session = {"user": "test_user"}
    multiligual_call.session_xml_sections["test_user"] = [{"section": "intro"}]

    mock_call_data = MagicMock()
    mock_call_data.phone_number = "+919876543210"
    mock_call_data.language = "en"
    mock_call_data.gender = "female"
    mock_call_data.speaker = "default"
    mock_call_data.multi_stt = False
    mock_call_data.sections = [{"section": "intro"}]

    mock_tw = MagicMock()
    mock_call_obj = MagicMock()
    mock_call_obj.sid = f"CA_manual_{uuid4().hex[:8]}"
    mock_tw.calls.create.return_value = mock_call_obj
    multiligual_call.twilio_client = mock_tw

    with patch("LeadAI.db.get_leadai_db", mock_get_leadai_db), \
         patch("multiligual_call.validate_phone_number", return_value="+919876543210"):

        # 1. 0 Balance -> HTTP 402
        try:
            asyncio.run(multiligual_call.make_call(
                call_data=mock_call_data,
                request=mock_request,
                current_user="user@tenant.com",
            ))
            assert False, "Should have raised HTTPException 402"
        except HTTPException as exc:
            assert exc.status_code == 402, f"Expected 402, got {exc.status_code}"
            print("PASS: Manual call rejected with HTTP 402 when balance is 0.")

        # 2. Top-up -> Success and active_calls populated with client_id
        recharge.RemainingMinutes = 20.0
        db.commit()

        resp = asyncio.run(multiligual_call.make_call(
            call_data=mock_call_data,
            request=mock_request,
            current_user="user@tenant.com",
        ))
        assert resp.status_code == 200
        assert mock_call_obj.sid in multiligual_call.active_calls
        assert multiligual_call.active_calls[mock_call_obj.sid]["client_id"] == client_id
        assert multiligual_call.active_calls[mock_call_obj.sid]["leadai"] is True
        print(f"PASS: Manual call dialed and active_calls[{mock_call_obj.sid}] contains client_id={client_id}.")

    db.close()


def test_bug_11_payment_history_read_only():
    print("\n--- Testing Bug #11: Payment History Read-Only / Idempotency ---")
    db = SessionLocal()
    client_id = f"client_{uuid4().hex[:8]}"

    pending_rec = LeadClientRecharge(
        Id=f"rec_{uuid4().hex[:8]}",
        ClientId=client_id,
        PlanNameSnapshot="Pro Plan",
        ValidityDaysSnapshot=30,
        PricePaid=2999.0,
        PurchasedMinutes=500.0,
        RemainingMinutes=500.0,
        Status=RECHARGE_STATUS_PENDING,
        ExpiresAt=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(pending_rec)
    db.commit()

    mock_principal = MagicMock()
    mock_principal.email = "admin@tenant.com"
    scope = (mock_principal, client_id)

    # Call get_payment_history
    history = get_payment_history(limit=10, scope=scope, db=db)
    assert len(history) == 1
    assert history[0].status == RECHARGE_STATUS_PENDING

    # Verify the database record was NOT mutated to FAILED
    db.refresh(pending_rec)
    assert pending_rec.Status == RECHARGE_STATUS_PENDING, f"Record was mutated to {pending_rec.Status}!"
    assert pending_rec.FailureReason is None, "FailureReason should not be set by a GET query!"
    print("PASS: In-flight PENDING recharge was NOT mutated by GET /payment-history!")
    db.close()


if __name__ == "__main__":
    print("=" * 60)
    print("RUNNING PHASE 3 AUTOMATED TEST SUITE")
    print("=" * 60)
    test_ceiling_rounding_and_integer_minutes()
    test_bug_10_batch_telephony_quota_and_tenancy()
    test_bug_5_manual_outbound_call_quota()
    test_bug_11_payment_history_read_only()
    print("\n" + "=" * 60)
    print("ALL PHASE 3 VERIFICATIONS PASSED SUCCESSFULLY!")
    print("=" * 60)
