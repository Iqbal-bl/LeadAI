"""An imported lead gets a real, trackable conversation the moment it's
imported — before any message is ever sent (see routers/leads_import.py). If
the campaign later skips or fails to reach that recipient, nothing is ever
exchanged, and that empty shell sat in the inbox forever looking like a real
thread with nothing to read. Hidden by default now; recoverable with
include_unreached=true for whoever wants to audit unreached imports.

Run: python tests/test_inbox_hides_unreached_imports.py
"""
import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.rbac import ROLE_PERMISSIONS, Principal  # noqa: E402
from LeadAI.routers import inbox  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def _principal(client_id):
    return Principal(email="admin@kestrel.test", role="company_admin", client_id=client_id,
                     permissions=set(ROLE_PERMISSIONS["company_admin"]))


def _setup():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()

    def make_conversation(name, message_count):
        customer = models.LeadCustomer(ClientId=client.Id, PublicRef=name, DisplayName=name)
        db.add(customer)
        db.flush()
        conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="whatsapp",
                                       Status="open", MessageCount=message_count)
        db.add(conv)
        return conv

    reached = make_conversation("Reached Lead", 4)         # a real exchange happened
    unreached = make_conversation("Unreached Lead", 0)     # imported, never actually contacted
    db.commit()
    return db, client, reached, unreached


def _list(db, client, **overrides):
    kwargs = dict(
        status_filter=None, lead_status=None, channel=None, assigned_to=None, search=None,
        min_score=None, above_threshold=None, campaign_id=None, include_unreached=False,
        sort="recent", page=1, page_size=25, principal=_principal(client.Id), db=db,
    )
    kwargs.update(overrides)
    return inbox.list_conversations(**kwargs)


def test_an_unreached_import_is_hidden_from_the_inbox_by_default():
    db, client, reached, unreached = _setup()
    out = _list(db, client)
    ids = {c.id for c in out.items}
    assert reached.Id in ids
    assert unreached.Id not in ids


def test_include_unreached_brings_it_back():
    db, client, reached, unreached = _setup()
    out = _list(db, client, include_unreached=True)
    ids = {c.id for c in out.items}
    assert reached.Id in ids and unreached.Id in ids


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
