"""Revealing a converted customer's contact details only ever returned phone/email/
whatsapp — even for a customer converted from Instagram/Messenger, where the actual
identity behind the account (handle, IGSID, profile link) was one join away the
whole time via LeadAccount.CustomerId. inbox.py's conversation-level reveal already
did this join; customers.py's account-level reveal never did.

Run: python tests/test_customer_reveal_social.py
"""
import uuid

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.rbac import ROLE_PERMISSIONS, Principal  # noqa: E402
from LeadAI.routers import customers  # noqa: E402

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
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="Customer #1", DisplayName="Manmeet Kaur")
    db.add(customer)
    db.flush()
    account_obj = models.LeadChannelAccount(ClientId=client.Id, Channel="instagram", Name="acct",
                                            ExternalId=uuid.uuid4().hex)
    db.add(account_obj)
    db.flush()
    db.add(
        models.LeadChannelIdentity(
            ClientId=client.Id, ChannelAccountId=account_obj.Id, Channel="instagram",
            ExternalUserId="940609752392281", CustomerId=customer.Id,
            ProfileName="Manmeet Kaur", ExternalUsername="_man11_10",
        )
    )
    account = models.LeadAccount(ClientId=client.Id, CustomerId=customer.Id, DisplayName="Manmeet Kaur",
                                 Source="instagram")
    db.add(account)
    db.commit()
    principal = Principal(email="admin@kestrel.test", role="company_admin", client_id=client.Id,
                          permissions=set(ROLE_PERMISSIONS["company_admin"]))
    return db, client, account, principal


def test_revealing_an_instagram_sourced_customer_includes_their_instagram_identity():
    db, client, account, principal = setup()
    out = customers.reveal_contact(account.Id, request=None, scope=(principal, client.Id), db=db)
    assert len(out["social_identities"]) == 1
    ident = out["social_identities"][0]
    assert ident.channel == "instagram"
    assert ident.external_user_id == "940609752392281"
    assert ident.handle == "_man11_10"
    assert ident.profile_url == "https://instagram.com/_man11_10"


def test_the_handle_is_the_real_at_username_not_the_display_name():
    # Found in production: ProfileName ("Manmeet Kaur", a real name with a space) was
    # preferred over ExternalUsername ("_man11_10", the actual resolved @handle), so
    # `handle` showed a name instead of a handle and profile_url was an invalid link
    # (a space in a URL). Both got populated in this exact order for this exact contact.
    db, client, account, principal = setup()
    out = customers.reveal_contact(account.Id, request=None, scope=(principal, client.Id), db=db)
    ident = out["social_identities"][0]
    assert ident.handle != "Manmeet Kaur"
    assert " " not in ident.profile_url
    assert ident.profile_name == "Manmeet Kaur"  # the real name still surfaces, just not as `handle`


def test_messenger_has_no_handle_concept_so_the_display_name_is_used():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="Customer #2", DisplayName="Karan Sharma")
    db.add(customer)
    db.flush()
    account_obj = models.LeadChannelAccount(ClientId=client.Id, Channel="messenger", Name="acct",
                                            ExternalId=uuid.uuid4().hex)
    db.add(account_obj)
    db.flush()
    db.add(
        models.LeadChannelIdentity(
            ClientId=client.Id, ChannelAccountId=account_obj.Id, Channel="messenger",
            ExternalUserId="1036903189236884", CustomerId=customer.Id, ProfileName="Karan Sharma",
        )
    )
    db.commit()
    from LeadAI.services import channels as ch
    result = ch.social_identities_for(db, customer.Id)
    assert result[0].handle == "Karan Sharma"       # no @handle concept on Messenger at all
    assert result[0].profile_url == "https://m.me/1036903189236884"  # deep link, not a public profile


def test_a_customer_with_no_customer_id_gets_an_empty_list_not_an_error():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    account = models.LeadAccount(ClientId=client.Id, DisplayName="Walk-in customer", CustomerId=None)
    db.add(account)
    db.commit()
    principal = Principal(email="admin@kestrel.test", role="company_admin", client_id=client.Id,
                          permissions=set(ROLE_PERMISSIONS["company_admin"]))
    out = customers.reveal_contact(account.Id, request=None, scope=(principal, client.Id), db=db)
    assert out["social_identities"] == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
