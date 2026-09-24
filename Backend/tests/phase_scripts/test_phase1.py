import sys
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

leadai_backend = r"c:\Users\Pratik\work\LeadAI\Backend"
if leadai_backend not in sys.path:
    sys.path.insert(0, leadai_backend)

import outbound.app as multiligual_call  # the voice app, formerly multiligual_call.py
from LeadAI.services import billing as billing_svc
from LeadAI.models import (
    LeadClientRecharge,
    RECHARGE_STATUS_ACTIVE,
    RECHARGE_STATUS_EXHAUSTED,
)

def test_multi_tenant_call_termination():
    """Verify that terminating calls for Client A does NOT terminate calls for Client B."""
    mock_twilio = MagicMock()
    multiligual_call.twilio_client = mock_twilio
    
    # Setup mock active_calls
    multiligual_call.active_calls = {
        "call_1": {"client_id": "client_A", "leadai": True},
        "call_2": {"client_id": "client_B", "leadai": True},
        "call_3": {"client_id": "client_A", "leadai": True},
        "call_4": {"client_id": "client_C", "leadai": False},
    }
    
    billing_svc._terminate_all_active_client_calls("client_A")
    
    terminated_sids = [call[0][0] for call in mock_twilio.calls.call_args_list]
    print(f"Terminated Call SIDs: {terminated_sids}")
    assert "call_1" in terminated_sids, "call_1 should be terminated"
    assert "call_3" in terminated_sids, "call_3 should be terminated"
    assert "call_2" not in terminated_sids, "CRITICAL: call_2 (client_B) was wrongfully terminated!"
    assert "call_4" not in terminated_sids, "call_4 (client_C) was wrongfully terminated!"
    print("PASS: Multi-tenant call termination test succeeded!")

def test_channel_access_with_zero_minutes():
    """Verify that an active plan with 0 remaining minutes does NOT block social channels."""
    mock_db = MagicMock()
    
    now = datetime.now(timezone.utc)
    future_expiry = now + timedelta(days=20)
    
    # Plan has 0 remaining minutes, but is valid for 20 more days and has WhatsApp
    plan = LeadClientRecharge(
        ClientId="client_X",
        PlanNameSnapshot="Monthly Voice & WhatsApp",
        PurchasedMinutes=500.0,
        RemainingMinutes=0.0,  # Zero minutes left
        ValidityDaysSnapshot=30,
        PricePaid=3499.0,
        ExpiresAt=future_expiry,
        Status=RECHARGE_STATUS_ACTIVE,
        ActiveChannels=["whatsapp", "instagram"],
    )
    
    # Mock DB query
    query_mock = MagicMock()
    query_mock.filter.return_value.order_by.return_value.first.return_value = plan
    mock_db.query.return_value = query_mock
    
    # 1. Voice call quota check should return False
    can_call, reason, mins = billing_svc.check_call_quota(mock_db, "client_X")
    print(f"Call Quota Check: can_call={can_call}, reason='{reason}', mins={mins}")
    assert not can_call, "Voice calls should be paused when balance < 1 min"
    assert mins == 0.0
    
    # 2. WhatsApp channel check should return True!
    wa_allowed, wa_reason = billing_svc.check_channel_access(mock_db, "client_X", "whatsapp")
    print(f"WhatsApp Access Check: allowed={wa_allowed}, reason='{wa_reason}'")
    assert wa_allowed, f"WhatsApp should be ALLOWED, but got: {wa_reason}"
    
    # 3. Instagram channel check should return True!
    ig_allowed, ig_reason = billing_svc.check_channel_access(mock_db, "client_X", "instagram")
    print(f"Instagram Access Check: allowed={ig_allowed}, reason='{ig_reason}'")
    assert ig_allowed, f"Instagram should be ALLOWED, but got: {ig_reason}"
    
    # 4. LinkedIn (not purchased) should return False
    li_allowed, li_reason = billing_svc.check_channel_access(mock_db, "client_X", "linkedin")
    print(f"LinkedIn Access Check: allowed={li_allowed}, reason='{li_reason}'")
    assert not li_allowed, "LinkedIn should be denied since it was not purchased"
    
    print("PASS: Channel access with zero minutes test succeeded!")

if __name__ == "__main__":
    test_multi_tenant_call_termination()
    test_channel_access_with_zero_minutes()
