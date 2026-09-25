"""The inbox shows the customer number always, and the name only after Reveal.

List and detail responses never carry the customer's name, for any role. The audited
Reveal call returns it, resolved from whichever channel the customer used (Instagram /
Messenger send only an opaque id, so the name lives on the channel identity). A WhatsApp
number can sit only in the WhatsApp field, and must still show masked. Runs the real serializers and the
real reveal endpoint on in-memory SQLite. Run: python tests/test_inbox_identity.py
"""
import types
import uuid

import conftest_stub  # noqa: F401  — installs core.base/core.database/core.auth stubs first
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.rbac import Principal  # noqa: E402
from LeadAI.routers import inbox  # noqa: E402
from LeadAI.security import encrypt_pii  # noqa: E402
from LeadAI.serializers import conversation_out  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001 — known duplicate index name on the domain `batchinfo` table
        pass

ADMIN_PERMS = {"lead.read.all", "lead.reveal_pii"}
AGENT_PERMS = {"lead.read.assigned"}


def make(db, *, name=None, phone=None, whatsapp=None, ig=None, profile=None, channel="instagram"):
    client = Client(Name="Nexa Finserv")
    db.add(client)
    db.flush()
    customer = models.LeadCustomer(
        ClientId=client.Id, PublicRef="Customer #51998", DisplayName=name,
        PhoneEnc=encrypt_pii(phone), WhatsAppEnc=encrypt_pii(whatsapp), InstagramEnc=encrypt_pii(ig),
    )
    db.add(customer)
    db.flush()
    account_id = str(uuid.uuid4())   # unique per customer: (account, external id) is unique
    conv = models.LeadConversation(
        ClientId=client.Id, CustomerId=customer.Id, Channel=channel, ChannelAccountId=account_id
    )
    db.add(conv)
    if profile:
        db.add(models.LeadChannelIdentity(
            ClientId=client.Id, ChannelAccountId=account_id, Channel=channel,
            ExternalUserId=str(uuid.uuid4().int)[:15], CustomerId=customer.Id, ProfileName=profile,
        ))
    db.commit()
    return client, customer, conv


def principal(client, perms, role="company_admin"):
    return Principal(email="staff@nexa.test", role=role, client_id=client.Id, permissions=set(perms))


def test_no_role_sees_the_name_in_the_list_only_the_customer_number():
    db = SessionLocalAdmin()
    client, _, conv = make(db, name="Manmeet Kaur", profile="Manmeet Kaur")
    for who in (principal(client, ADMIN_PERMS), principal(client, AGENT_PERMS, role="employee")):
        out = conversation_out(db, conv, who)
        assert out.customer_name is None                        # even for the company admin
        assert out.customer_ref == "Customer #51998"            # the number is always there
        assert "Manmeet" not in out.model_dump_json()


def test_agents_get_the_customer_number_and_a_masked_phone():
    db = SessionLocalAdmin()
    client, _, conv = make(db, name="Manmeet Kaur", phone="+919876543210")
    out = conversation_out(db, conv, principal(client, AGENT_PERMS, role="employee"))
    assert out.customer_name is None
    assert out.customer_ref == "Customer #51998"
    assert out.customer_phone_masked == "+919876*****210"       # masked, never the real number
    assert "9876543210" not in out.model_dump_json()


def test_whatsapp_only_customer_still_shows_a_masked_number():
    db = SessionLocalAdmin()
    client, _, conv = make(db, name="Riya", whatsapp="+919812345678", channel="whatsapp")
    out = conversation_out(db, conv, principal(client, ADMIN_PERMS))
    assert out.customer_phone_masked and "*****" in out.customer_phone_masked
    assert "919812345678" not in out.customer_phone_masked


def test_customer_without_any_number_has_no_masked_phone_but_keeps_the_ref():
    db = SessionLocalAdmin()
    client, _, conv = make(db, profile="Karan Sharma")          # Instagram: no number exists
    out = conversation_out(db, conv, principal(client, ADMIN_PERMS))
    assert out.customer_phone_masked is None and out.customer_ref == "Customer #51998"


def test_reveal_returns_the_name_and_the_full_number():
    db = SessionLocalAdmin()
    client, _, conv = make(db, whatsapp="+919812345678", profile="Manmeet Kaur")
    request = types.SimpleNamespace(client=types.SimpleNamespace(host="127.0.0.1"), headers={})
    out = inbox.reveal_contact(conv.Id, request, principal(client, ADMIN_PERMS), db)
    assert out.display_name == "Manmeet Kaur"                   # the name appears only here
    assert out.phone == "+919812345678"                         # found via the WhatsApp field
    audit = db.query(models.LeadActivityLog).filter_by(Action="lead.pii_revealed").count()
    assert audit == 1                                           # still audited


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
