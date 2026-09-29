"""A phone number a customer types into the chat is saved on their record.

Reproduces the Instagram session: an agent asked for a number, the customer answered
"Yes it's 7696086310", and it stayed buried in the message text (the lead showed no phone).
Also covers the empty "template" attachment Meta sent a second later, which used to be
stored as a fake "[template received]" customer message.

Runs the real pipeline on in-memory SQLite; only the LLM and retrieval are faked.
Run: python tests/test_phone_capture.py
"""
import types

import conftest_stub  # noqa: F401  — installs core.base/core.database/core.auth stubs first
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.config import settings as real_settings  # noqa: E402
from LeadAI.rbac import Principal  # noqa: E402
from LeadAI.routers import inbox, webhooks  # noqa: E402
from LeadAI.security import decrypt_pii, encrypt_pii  # noqa: E402
from LeadAI.serializers import conversation_out  # noqa: E402
from LeadAI.services import ai_engine, contact_capture, conversation_flow  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001 — known duplicate index name on the domain `batchinfo` table
        pass


class _Settings:
    llm_enabled = False          # no network: scoring and summaries use the built-in rules

    def __getattr__(self, name):
        return getattr(real_settings, name)


ai_engine.settings = _Settings()
ai_engine.vectorstore.search = lambda *a, **k: []
ai_engine.vectorstore.idf_map = lambda *a, **k: ({}, 1.0)
ai_engine._detect_product = lambda *a, **k: None


def setup(status="open", phone=None):
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="Customer #38262", DisplayName="Manmeet Kaur",
                                   PhoneEnc=encrypt_pii(phone))
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="instagram", Status=status)
    db.add(conv)
    db.commit()
    return db, client, customer, conv


def say(db, client, conv, text):
    result = conversation_flow.handle_customer_turn(db, client, conv, text)
    db.refresh(conv)
    return result


# --------------------------------------------------------------------------- #
def test_real_phone_numbers_are_found_and_lookalikes_are_not():
    assert contact_capture.extract_phone("Yes it's 7696086310") == "+917696086310"
    assert contact_capture.extract_phone("call me on +91 98765 43210 please") == "+919876543210"
    assert contact_capture.extract_phone("my number is 98765-43210") == "+919876543210"
    assert contact_capture.extract_phone("09876543210") == "+919876543210"
    for not_a_phone in ["budget is 1.2 crore", "I need 50 lakhs", "10800000", "a 3BHK of 1545 sq ft",
                        "possession in 2027", "2 bhk will work for me", "Saturday 11am, 3 people", "", None]:
        assert contact_capture.extract_phone(not_a_phone) is None, not_a_phone


def test_number_typed_while_an_agent_has_the_chat_is_saved():
    db, client, customer, conv = setup(status="assigned")      # an agent replied, so the AI is silent
    result = say(db, client, conv, "Yes it's 7696086310")
    assert result.ai_replied is False                          # the AI still stays out of it
    db.refresh(customer)
    assert decrypt_pii(customer.PhoneEnc) == "+917696086310"
    assert customer.PhoneHash                                  # searchable without decrypting


def test_it_then_appears_masked_in_the_inbox_and_in_full_only_on_reveal():
    db, client, customer, conv = setup(status="assigned")
    say(db, client, conv, "Yes it's 7696086310")
    admin = Principal(email="a@x.test", role="company_admin", client_id=client.Id,
                      permissions={"lead.read.all", "lead.reveal_pii"})
    out = conversation_out(db, conv, admin)
    assert out.customer_phone_masked and "*****" in out.customer_phone_masked
    assert "7696086310" not in out.customer_phone_masked
    request = types.SimpleNamespace(client=types.SimpleNamespace(host="127.0.0.1"), headers={})
    assert inbox.reveal_contact(conv.Id, request, admin, db).phone == "+917696086310"


def test_audit_row_names_the_customer_but_never_the_number():
    db, client, customer, conv = setup()
    say(db, client, conv, "my number is 9876543210")
    rows = db.query(models.LeadActivityLog).filter_by(Action="lead.phone_captured", ClientId=client.Id).all()
    assert len(rows) == 1
    assert "9876543210" not in (rows[0].LogMessage or "") and "9876543210" not in str(rows[0].MetaJson)


def test_an_existing_number_is_never_overwritten():
    db, client, customer, conv = setup(phone="+919000000001")
    say(db, client, conv, "actually my friend's number is 9876543210")
    db.refresh(customer)
    assert decrypt_pii(customer.PhoneEnc) == "+919000000001"


def test_messages_without_a_number_change_nothing():
    db, client, customer, conv = setup()
    say(db, client, conv, "I am looking for a 2 bhk, budget 1.2 crore")
    db.refresh(customer)
    assert not customer.PhoneEnc


def test_an_empty_template_attachment_is_ignored():
    db, client, customer, conv = setup()
    account = models.LeadChannelAccount(ClientId=client.Id, Channel="instagram", Name="kestrel",
                                        ExternalId="17841479633674193", IsActive=True)
    db.add(account)
    db.commit()
    before = db.query(models.LeadMessage).count()
    webhooks._process_one(db, {"account_id": account.Id, "external_user_id": "940609752392281",
                               "text": "", "media_type": "template"})
    assert db.query(models.LeadMessage).count() == before      # no "[template received]" message
    assert db.query(models.LeadMessage).filter(models.LeadMessage.Content.like("%template%")).count() == 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
