"""The inbox had a `campaign_id` filter for "show me this ONE campaign's
conversations", but no way to ask the broader question an operator actually
has: "show me leads that came in on their own" vs "show me leads that came
from a batch I imported" vs "show me leads from a broadcast I sent". That
split exists at the campaign level (LeadCampaign.CreatedVia) but was never
exposed as an inbox filter — this adds `lead_source=inbound|import|broadcast`.

Run: python tests/test_inbox_lead_source_filter.py
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

    import_campaign = models.LeadCampaign(ClientId=client.Id, Name="Home Loan — Import", Kind="call",
                                          Channel="voice", AudienceType="list", CreatedVia="import")
    broadcast_campaign = models.LeadCampaign(ClientId=client.Id, Name="Diwali Blast", Kind="message",
                                             Channel="whatsapp", AudienceType="customers",
                                             CreatedVia="manual")
    db.add_all([import_campaign, broadcast_campaign])
    db.flush()

    def make_conversation(name, campaign_id):
        customer = models.LeadCustomer(ClientId=client.Id, PublicRef=name, DisplayName=name)
        db.add(customer)
        db.flush()
        conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="whatsapp",
                                       Status="open", MessageCount=3, CampaignId=campaign_id)
        db.add(conv)
        return conv

    organic = make_conversation("Organic Lead", None)
    imported = make_conversation("Imported Lead", import_campaign.Id)
    broadcasted = make_conversation("Broadcasted Lead", broadcast_campaign.Id)
    db.commit()
    return db, client, organic, imported, broadcasted


def _list(db, client, **overrides):
    kwargs = dict(
        status_filter=None, lead_status=None, channel=None, assigned_to=None, search=None,
        min_score=None, above_threshold=None, campaign_id=None, lead_source=None,
        include_unreached=False, sort="recent", page=1, page_size=25,
        principal=_principal(client.Id), db=db,
    )
    kwargs.update(overrides)
    return inbox.list_conversations(**kwargs)


def test_no_filter_shows_all_three():
    db, client, organic, imported, broadcasted = _setup()
    ids = {c.id for c in _list(db, client).items}
    assert {organic.Id, imported.Id, broadcasted.Id} <= ids


def test_inbound_shows_only_the_one_with_no_campaign_at_all():
    db, client, organic, imported, broadcasted = _setup()
    ids = {c.id for c in _list(db, client, lead_source="inbound").items}
    assert ids == {organic.Id}


def test_import_shows_only_the_lead_import_batchs_lead():
    db, client, organic, imported, broadcasted = _setup()
    ids = {c.id for c in _list(db, client, lead_source="import").items}
    assert ids == {imported.Id}


def test_broadcast_shows_only_the_manually_created_campaigns_lead():
    db, client, organic, imported, broadcasted = _setup()
    ids = {c.id for c in _list(db, client, lead_source="broadcast").items}
    assert ids == {broadcasted.Id}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
