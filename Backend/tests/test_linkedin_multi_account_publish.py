import asyncio
import types
from unittest.mock import patch, AsyncMock

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client
from LeadAI import models
from LeadAI.models_ext import LeadChannelAccount
from LeadAI.security import encrypt_pii
from LeadAI.social import service

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:
        pass


from datetime import datetime, timezone, timedelta
import uuid

def setup():
    db = SessionLocalAdmin()
    client = Client(Name="Acme Corp")
    db.add(client)
    db.commit()

    future_expiry = datetime.now(timezone.utc) + timedelta(days=60)
    suffix = str(uuid.uuid4())[:8]

    # Add two active connected LinkedIn accounts
    acc1 = LeadChannelAccount(
        ClientId=client.Id,
        Channel="linkedin",
        ExternalId=f"urn:li:person:alice_{suffix}",
        Name="Alice Johnson",
        AccessTokenEnc=encrypt_pii("token_alice"),
        TokenExpiresAt=future_expiry,
        IsActive=True,
        IsDeleted=False,
    )
    acc2 = LeadChannelAccount(
        ClientId=client.Id,
        Channel="linkedin",
        ExternalId=f"urn:li:person:bob_{suffix}",
        Name="Bob Smith",
        AccessTokenEnc=encrypt_pii("token_bob"),
        TokenExpiresAt=future_expiry,
        IsActive=True,
        IsDeleted=False,
    )
    db.add_all([acc1, acc2])
    db.commit()
    return db, client, acc1, acc2


def test_publish_broadcasts_to_all_connected_linkedin_accounts():
    db, client, acc1, acc2 = setup()

    called_urns = []

    async def fake_post(token, person_urn, caption, uploaded, media_shape):
        called_urns.append(person_urn)
        return {"post_id": f"urn:li:share:{person_urn}", "status_code": 201}

    with patch("LeadAI.social.linkedin.post_to_linkedin", side_effect=fake_post):
        results, post_row = asyncio.run(
            service.publish(
                db,
                client.Id,
                caption="Testing multi-account LinkedIn post!",
                uploaded=[],
                platforms=["linkedin"],
                mode="direct",
                record=True,
            )
        )

    assert "linkedin" in results
    li_res = results["linkedin"]
    assert li_res["success"] is True
    assert li_res["all_success"] is True
    assert li_res["total_accounts"] == 2
    assert li_res["successful_accounts"] == 2
    assert len(li_res["accounts"]) == 2
    assert set(called_urns) == {acc1.ExternalId, acc2.ExternalId}
    assert "Alice Johnson" in li_res["account_name"]
    assert "Bob Smith" in li_res["account_name"]
    print("PASS test_publish_broadcasts_to_all_connected_linkedin_accounts")


def test_publish_handles_partial_failure():
    db, client, acc1, acc2 = setup()

    async def fake_post(token, person_urn, caption, uploaded, media_shape):
        if person_urn == acc2.ExternalId:
            raise RuntimeError("Rate limit exceeded for Bob")
        return {"post_id": f"urn:li:share:{person_urn}", "status_code": 201}

    with patch("LeadAI.social.linkedin.post_to_linkedin", side_effect=fake_post):
        results, post_row = asyncio.run(
            service.publish(
                db,
                client.Id,
                caption="Testing partial failure!",
                uploaded=[],
                platforms=["linkedin"],
                mode="direct",
                record=True,
            )
        )

    li_res = results["linkedin"]
    assert li_res["success"] is True
    assert li_res["all_success"] is False
    assert li_res["partial"] is True
    assert li_res["successful_accounts"] == 1
    assert li_res["total_accounts"] == 2

    # Verify per-account outcome
    alice_outcome = next(o for o in li_res["accounts"] if o["account_id"] == acc1.Id)
    bob_outcome = next(o for o in li_res["accounts"] if o["account_id"] == acc2.Id)

    assert alice_outcome["success"] is True
    assert alice_outcome["post_id"] == f"urn:li:share:{acc1.ExternalId}"

    assert bob_outcome["success"] is False
    assert "Rate limit exceeded" in bob_outcome["error"]
    print("PASS test_publish_handles_partial_failure")


def test_publish_filters_by_account_ids_when_specified():
    db, client, acc1, acc2 = setup()

    called_urns = []

    async def fake_post(token, person_urn, caption, uploaded, media_shape):
        called_urns.append(person_urn)
        return {"post_id": f"urn:li:share:{person_urn}", "status_code": 201}

    with patch("LeadAI.social.linkedin.post_to_linkedin", side_effect=fake_post):
        results, _ = asyncio.run(
            service.publish(
                db,
                client.Id,
                caption="Post only to Alice!",
                uploaded=[],
                platforms=["linkedin"],
                account_ids=[acc1.Id],
                mode="direct",
                record=False,
            )
        )

    li_res = results["linkedin"]
    assert li_res["success"] is True
    assert li_res["total_accounts"] == 1
    assert called_urns == [acc1.ExternalId]
    print("PASS test_publish_filters_by_account_ids_when_specified")


if __name__ == "__main__":
    test_publish_broadcasts_to_all_connected_linkedin_accounts()
    test_publish_handles_partial_failure()
    test_publish_filters_by_account_ids_when_specified()
