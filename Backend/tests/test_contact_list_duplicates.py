"""Building a contact list from a leads filter correctly writes InvalidReason=
"Duplicate" on the duplicate ROW itself, but never rolled that up into the list's
own DuplicateCount — it stayed 0 no matter how many duplicates were actually in
the list. The file-upload path (create_list) has always set this correctly from
its own ingest result; only create_list_from_leads never aggregated it.

Run: python tests/test_contact_list_duplicates.py
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

    def make_lead(name, phone, score, channel):
        customer = models.LeadCustomer(ClientId=client.Id, PublicRef=name, DisplayName=name,
                                       PhoneEnc=encrypt_pii(phone) if phone else None)
        db.add(customer)
        db.flush()
        conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel=channel)
        db.add(conv)
        db.flush()
        db.add(models.Lead(ClientId=client.Id, ConversationId=conv.Id, Score=score, Status="hot",
                           Product="unknown", Interest="unknown"))

    # No contact detail at all -> invalid, not a duplicate.
    make_lead("Manmeet Kaur", None, 44, "messenger")
    # First with this phone -> valid.
    make_lead("Manmeet Kaur", "+917696086310", 90, "instagram")
    # Same phone again -> invalid AND a duplicate.
    make_lead("Karan Sharma", "+917696086310", 44, "instagram")

    db.commit()
    principal = Principal(email="admin@kestrel.test", role="company_admin", client_id=client.Id,
                          permissions=set(ROLE_PERMISSIONS["company_admin"]))
    return db, client, principal


def test_duplicate_count_matches_the_actual_duplicate_rows():
    db, client, principal = setup()
    out = campaigns.create_list_from_leads(
        ContactListFromLeads(name="hot leads sept"), request=None,
        scope=(principal, client.Id), db=db,
    )
    assert out.total_count == 3
    assert out.valid_count == 1
    assert out.invalid_count == 2
    assert out.duplicate_count == 1


def test_a_list_with_no_duplicates_reports_zero_not_a_stale_default():
    db, client, principal = setup()
    # Overwrite the third lead's phone so nothing collides.
    db.query(models.LeadCustomer).filter_by(PublicRef="Karan Sharma").update(
        {"PhoneEnc": encrypt_pii("+919999999999")}
    )
    db.commit()
    out = campaigns.create_list_from_leads(
        ContactListFromLeads(name="no dupes"), request=None,
        scope=(principal, client.Id), db=db,
    )
    assert out.duplicate_count == 0
    assert out.valid_count == 2


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
