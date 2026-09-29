"""GET /channels/{id}/contacts masks external_user_id — correct for WhatsApp, where that id
IS the phone number (real PII), but the same masking on Instagram/Messenger hides an opaque
platform id (IGSID/PSID) that isn't personal information and IS exactly the value
/channels/{id}/test's `to` field needs. Masking it there made it impossible to build a
"pick who to send a test message to" UI without asking someone to hand-type a raw numeric id
they have no way to look up.

Run: python tests/test_channel_contacts.py
"""
import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.rbac import ROLE_PERMISSIONS, Principal  # noqa: E402
from LeadAI.routers import channels  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def setup(channel: str, external_user_id: str):
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    account = models.LeadChannelAccount(ClientId=client.Id, Channel=channel, Name="acct",
                                        ExternalId=f"acct-{channel}")
    db.add(account)
    db.flush()
    db.add(
        models.LeadChannelIdentity(
            ClientId=client.Id, ChannelAccountId=account.Id, Channel=channel,
            ExternalUserId=external_user_id, CustomerId="cust-1", ProfileName="Manmeet Kaur",
        )
    )
    db.commit()
    principal = Principal(email="admin@kestrel.test", role="company_admin", client_id=client.Id,
                          permissions=set(ROLE_PERMISSIONS["company_admin"]))
    return db, client, account, principal


def test_whatsapp_external_id_stays_masked():
    db, client, account, principal = setup("whatsapp", "919876543210")
    out = channels.list_identities(account.Id, scope=(principal, client.Id), db=db)
    assert out["items"][0]["external_user_id"] == "***3210"


def test_instagram_external_id_is_shown_in_full():
    db, client, account, principal = setup("instagram", "940609752392281")
    out = channels.list_identities(account.Id, scope=(principal, client.Id), db=db)
    assert out["items"][0]["external_user_id"] == "940609752392281"


def test_messenger_external_id_is_shown_in_full():
    db, client, account, principal = setup("messenger", "1036903189236884")
    out = channels.list_identities(account.Id, scope=(principal, client.Id), db=db)
    assert out["items"][0]["external_user_id"] == "1036903189236884"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
