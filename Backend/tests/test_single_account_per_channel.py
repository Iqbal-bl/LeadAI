"""A company may have at most one connected account per channel (whatsapp, messenger,
instagram, linkedin). Reproduces a live incident: connecting a Facebook Page whose
linked Instagram business account differed from the company's already-connected
Instagram account silently added a SECOND, independent Instagram channel — nothing
told the operator, and nothing stopped it.

Covers all three ways a channel account gets created:
  * POST /channels (manual connect — WhatsApp today)
  * the Facebook Page login flow (_upsert_fb_account — messenger + instagram)
  * LinkedIn's OAuth callback (save_tokens), which used to silently REPLACE the
    existing account instead of refusing

In every case: reconnecting the SAME external account (a token refresh / re-auth) must
still work with no error; connecting a genuinely DIFFERENT account for a channel the
company already has must be refused until the existing one is disconnected.

Run: python tests/test_single_account_per_channel.py
"""
import asyncio
import types

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from fastapi import HTTPException

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.rbac import ROLE_PERMISSIONS, Principal  # noqa: E402
from LeadAI.routers import channels  # noqa: E402
from LeadAI.schemas_ext import ChannelAccountCreate  # noqa: E402
from LeadAI.social import linkedin  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def fake_request():
    return types.SimpleNamespace(
        client=types.SimpleNamespace(host="127.0.0.1"), headers={},
        base_url="https://x.test/", url=types.SimpleNamespace(scheme="https"),
    )


def setup():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.commit()
    principal = Principal(email="admin@kestrel.test", role="company_admin", client_id=client.Id,
                          permissions=set(ROLE_PERMISSIONS["company_admin"]))
    return db, client, principal


# --------------------------------------------------------------- POST /channels (manual)
def test_a_second_whatsapp_number_is_refused_until_the_first_is_disconnected():
    db, client, principal = setup()
    channels.create_account(
        ChannelAccountCreate(channel="whatsapp", name="Main line", external_id="wa-1",
                            access_token="x" * 20),
        fake_request(), scope=(principal, client.Id), db=db,
    )
    try:
        channels.create_account(
            ChannelAccountCreate(channel="whatsapp", name="Second line", external_id="wa-2",
                                access_token="x" * 20),
            fake_request(), scope=(principal, client.Id), db=db,
        )
        assert False, "a second WhatsApp number should have been refused"
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "already has a whatsapp account" in exc.detail


def test_a_different_companys_first_whatsapp_number_is_unaffected():
    db, client, principal = setup()
    db2, client2, principal2 = setup()
    channels.create_account(
        ChannelAccountCreate(channel="whatsapp", name="Line A", external_id="wa-a1",
                            access_token="x" * 20),
        fake_request(), scope=(principal, client.Id), db=db,
    )
    # A brand-new company's first WhatsApp number must not be blocked by another
    # company's existing connection.
    out = channels.create_account(
        ChannelAccountCreate(channel="whatsapp", name="Line B", external_id="wa-b1",
                            access_token="x" * 20),
        fake_request(), scope=(principal2, client2.Id), db=db2,
    )
    assert out.name == "Line B"


def test_reconnecting_a_disconnected_whatsapp_number_revives_it_instead_of_duplicate_key_error():
    """A real production incident: disconnecting a channel soft-deletes the
    row (IsDeleted=1), but (Channel, ExternalId) is unique at the DB level
    regardless of IsDeleted. The lookup used to filter IsDeleted==False, so a
    soft-deleted row was invisible to it and reconnecting tried to INSERT a
    second row with the same key — MySQLdb.IntegrityError: Duplicate entry.
    Reconnecting the same number must revive the existing row instead."""
    db, client, principal = setup()
    first = channels.create_account(
        ChannelAccountCreate(channel="whatsapp", name="Main line", external_id="wa-revive",
                            access_token="x" * 20),
        fake_request(), scope=(principal, client.Id), db=db,
    )
    channels.delete_account(first.id, fake_request(), scope=(principal, client.Id), db=db)
    row = db.get(models.LeadChannelAccount, first.id)
    assert row.IsDeleted is True

    revived = channels.create_account(
        ChannelAccountCreate(channel="whatsapp", name="Main line (again)", external_id="wa-revive",
                            access_token="y" * 20),
        fake_request(), scope=(principal, client.Id), db=db,
    )
    assert revived.id == first.id   # same row revived, not a second one
    db.refresh(row)
    assert row.IsDeleted is False
    assert row.Name == "Main line (again)"


# ----------------------------------------------------------------------- Facebook login
def test_a_different_facebook_page_is_refused_but_reconnecting_the_same_page_is_not():
    db, client, principal = setup()
    channels._upsert_fb_account(
        db, client_id=client.Id, channel="messenger", external_id="page-1",
        name="Page One", page_token="tok1", meta={},
    )
    db.flush()
    # Reconnecting the SAME page (e.g. token refresh) must not be blocked.
    again = channels._upsert_fb_account(
        db, client_id=client.Id, channel="messenger", external_id="page-1",
        name="Page One", page_token="tok1-new", meta={},
    )
    assert again.ExternalId == "page-1"
    # A DIFFERENT page for the same company must be refused.
    try:
        channels._upsert_fb_account(
            db, client_id=client.Id, channel="messenger", external_id="page-2",
            name="Page Two", page_token="tok2", meta={},
        )
        assert False, "a second Facebook Page should have been refused"
    except HTTPException as exc:
        assert exc.status_code == 409


def test_reconnecting_a_disconnected_facebook_page_revives_it_instead_of_duplicate_key_error():
    """Same production incident as the WhatsApp version above, for the
    Facebook/Instagram OAuth callback path specifically — this is the exact
    function behind the real stack trace (MySQLdb.IntegrityError: Duplicate
    entry 'instagram-...' for key 'uq_leadai_channel_external'), since
    _upsert_fb_account shares its structure with the Instagram callback."""
    db, client, principal = setup()
    first = channels._upsert_fb_account(
        db, client_id=client.Id, channel="messenger", external_id="page-revive",
        name="Page One", page_token="tok1", meta={},
    )
    db.commit()
    page_id = first.Id

    row = db.get(models.LeadChannelAccount, page_id)
    row.IsDeleted = True
    db.commit()

    revived = channels._upsert_fb_account(
        db, client_id=client.Id, channel="messenger", external_id="page-revive",
        name="Page One (reconnected)", page_token="tok1-new", meta={},
    )
    db.commit()
    assert revived.Id == page_id   # same row revived, not a duplicate-key crash
    db.refresh(row)
    assert row.IsDeleted is False
    assert row.Name == "Page One (reconnected)"


def test_a_facebook_pages_linked_instagram_account_is_refused_if_a_different_one_is_connected():
    # The exact live incident: an existing, unrelated Instagram connection must block a
    # DIFFERENT Instagram account that happens to come bundled with a Facebook Page login.
    db, client, principal = setup()
    db.add(models.LeadChannelAccount(ClientId=client.Id, Channel="instagram",
                                     ExternalId="ig-existing", Name="@existing"))
    db.commit()
    try:
        channels._upsert_fb_account(
            db, client_id=client.Id, channel="instagram", external_id="ig-from-fb-page",
            name="@bundled", page_token="tok", meta={},
        )
        assert False, "a second, different Instagram account should have been refused"
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "already has a instagram account" in exc.detail


# ------------------------------------------------------------------------------ LinkedIn
def test_reconnecting_the_same_linkedin_person_is_a_refresh_not_a_new_account():
    db, client, principal = setup()
    asyncio.run(linkedin.save_tokens(db, client.Id, "urn:li:person:A", "tok-a", 3600))
    asyncio.run(linkedin.save_tokens(db, client.Id, "urn:li:person:A", "tok-a-refreshed", 3600))
    rows = db.query(models.LeadChannelAccount).filter_by(ClientId=client.Id, Channel="linkedin").all()
    assert len(rows) == 1
    assert rows[0].ExternalId == "urn:li:person:A"


def test_a_different_linkedin_person_is_refused_not_silently_swapped_in():
    # Before this fix, save_tokens silently repointed the existing row at the NEW person,
    # losing the original connection with no warning to the operator.
    db, client, principal = setup()
    asyncio.run(linkedin.save_tokens(db, client.Id, "urn:li:person:A", "tok-a", 3600))
    try:
        asyncio.run(linkedin.save_tokens(db, client.Id, "urn:li:person:B", "tok-b", 3600))
        assert False, "a second, different LinkedIn person should have been refused"
    except ValueError as exc:
        assert "already has a LinkedIn account" in str(exc)
    rows = db.query(models.LeadChannelAccount).filter_by(ClientId=client.Id, Channel="linkedin").all()
    assert len(rows) == 1
    assert rows[0].ExternalId == "urn:li:person:A", "the original connection must survive the refused attempt"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
