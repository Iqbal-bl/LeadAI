"""Building a contact list from leads marked a lead invalid ("No contact detail")
whenever the underlying LeadCustomer had no phone/email — even when the lead had a
perfectly real, resolved Instagram/Messenger identity (LeadChannelIdentity) that a
social campaign could actually reach. That silently excluded every social-only
contact from every list, on every channel, forever: a campaign built with
Channel="messenger" against such a list always resolved to zero contactable people,
even with a real Messenger contact sitting right there.

Run: python tests/test_contact_list_social_only.py
"""
import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.rbac import ROLE_PERMISSIONS, Principal  # noqa: E402
from LeadAI.routers import campaigns  # noqa: E402
from LeadAI.schemas_ext import ContactListFromLeads  # noqa: E402
from LeadAI.security import encrypt_pii  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def setup():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    account = models.LeadChannelAccount(ClientId=client.Id, Channel="messenger", Name="acct",
                                        ExternalId="page-social-only")
    db.add(account)
    db.flush()

    def make_lead(name, phone, channel, with_identity):
        customer = models.LeadCustomer(ClientId=client.Id, PublicRef=name, DisplayName=name,
                                       PhoneEnc=encrypt_pii(phone) if phone else None)
        db.add(customer)
        db.flush()
        if with_identity:
            db.add(models.LeadChannelIdentity(
                ClientId=client.Id, ChannelAccountId=account.Id, Channel=channel,
                ExternalUserId="psid-" + name.replace(" ", ""), CustomerId=customer.Id,
                ProfileName=name,
            ))
        conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel=channel)
        db.add(conv)
        db.flush()
        db.add(models.Lead(ClientId=client.Id, ConversationId=conv.Id, Score=80, Status="hot",
                           Product="unknown", Interest="unknown"))

    # Messenger contact with no phone/email at all — only reachable via the identity.
    make_lead("Manmeet Kaur", None, "messenger", with_identity=True)
    # A normal WhatsApp lead with a phone — unaffected by this change.
    make_lead("Karan Sharma", "+919000000001", "whatsapp", with_identity=False)
    # Genuinely unreachable: no phone, no email, no identity at all.
    make_lead("No Contact", None, "messenger", with_identity=False)

    db.commit()
    principal = Principal(email="admin@kestrel.test", role="company_admin", client_id=client.Id,
                          permissions=set(ROLE_PERMISSIONS["company_admin"]))
    return db, client, principal


def test_a_social_only_lead_with_a_real_identity_is_valid():
    db, client, principal = setup()
    out = campaigns.create_list_from_leads(
        ContactListFromLeads(name="social only"), request=None,
        scope=(principal, client.Id), db=db,
    )
    assert out.total_count == 3
    assert out.valid_count == 2   # Manmeet (identity-only) + Karan (phone)
    assert out.invalid_count == 1  # only "No Contact" is truly unreachable

    items = campaigns.list_items(
        out.id, page=1, page_size=50, only_invalid=False,
        scope=(principal, client.Id), db=db,
    )
    by_name = {i.name: i for i in items.items}
    assert by_name["Manmeet Kaur"].is_valid is True
    assert by_name["Karan Sharma"].is_valid is True
    assert by_name["No Contact"].is_valid is False
    assert by_name["No Contact"].invalid_reason == "No contact detail"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
