"""Converting a lead into a CRM customer used to throw away everything the AI had
already learned in the conversation. create_account() has always accepted a `fields`
dict, but convert_lead() never passed one — every freshly-converted customer started
with FieldsJson=null even when Lead.FactsJson (city, budget, family size, ...) was
sitting right there on the row being converted.

Run: python tests/test_crm_convert.py
"""
import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.security import encrypt_pii, phone_fingerprint  # noqa: E402
from LeadAI.services import crm  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def setup(facts=None, display_name="Manmeet Kaur", phone="+917696086310"):
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="Customer #1",
                                   DisplayName=display_name, PhoneEnc=encrypt_pii(phone))
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="instagram")
    db.add(conv)
    db.flush()
    lead = models.Lead(ClientId=client.Id, ConversationId=conv.Id, Score=90, Status="hot",
                       Product="2 BHK Apartment", FactsJson=facts or [])
    db.add(lead)
    db.commit()
    return db, client, conv, lead


def test_conversion_carries_over_what_the_ai_already_learned():
    db, client, conv, lead = setup(facts=["City: Mohali", "Family of four", "Budget: 80 lakh"])
    account = crm.convert_lead(db, client.Id, conv, lead, actor="agent@kestrel.test")
    db.commit()
    assert account.FieldsJson == {
        "lead_facts": ["City: Mohali", "Family of four", "Budget: 80 lakh"]
    }


def test_a_lead_with_no_stated_facts_gets_no_empty_fields_object():
    db, client, conv, lead = setup(facts=[])
    account = crm.convert_lead(db, client.Id, conv, lead, actor="agent@kestrel.test")
    db.commit()
    assert account.FieldsJson is None


def test_converting_an_already_existing_account_never_overwrites_its_own_fields():
    # find_account_by_phone matches an existing account (e.g. imported earlier with its
    # own data) — conversion must not blow away fields that account already has.
    db, client, conv, lead = setup(facts=["City: Mohali"])
    existing = models.LeadAccount(ClientId=client.Id, DisplayName="Manmeet Kaur",
                                  PhoneHash=phone_fingerprint("+917696086310"),
                                  FieldsJson={"crm_source": "manual import"})
    db.add(existing)
    db.commit()

    account = crm.convert_lead(db, client.Id, conv, lead, actor="agent@kestrel.test")
    db.commit()
    assert account.Id == existing.Id
    assert account.FieldsJson == {"crm_source": "manual import"}  # untouched


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
