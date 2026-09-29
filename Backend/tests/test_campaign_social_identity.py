"""Instagram/Messenger campaigns sent using a contact's phone number as the
recipient id — Meta always rejects that (the same "recipient[id] must be a valid
ID string" error found testing /channels/{id}/test manually). Nothing in
build_audience()/_send_message() ever resolved the real IGSID/PSID; phone was the
only thing threaded through, for every channel, unconditionally.

It also meant two DIFFERENT Instagram accounts that happen to share a phone number
(a shared test phone, a family member, whatever) were wrongly deduplicated against
each other as if they were the same recipient, purely because dedup used the phone
fingerprint with no awareness of the actual platform identity.

Run: python tests/test_campaign_social_identity.py
"""
import uuid

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.security import encrypt_pii  # noqa: E402
from LeadAI.services import campaign_runner as cr  # noqa: E402
from LeadAI.services import channels  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass

SHARED_PHONE = "+917696086310"


def setup_two_instagram_contacts_sharing_a_phone():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    account = models.LeadChannelAccount(ClientId=client.Id, Channel="instagram", Name="acct",
                                        ExternalId=uuid.uuid4().hex)
    db.add(account)
    db.flush()

    def make_contact(name, igsid, list_row_number):
        customer = models.LeadCustomer(ClientId=client.Id, PublicRef=name, DisplayName=name,
                                       PhoneEnc=encrypt_pii(SHARED_PHONE))
        db.add(customer)
        db.flush()
        db.add(models.LeadChannelIdentity(
            ClientId=client.Id, ChannelAccountId=account.Id, Channel="instagram",
            ExternalUserId=igsid, CustomerId=customer.Id, ProfileName=name,
        ))
        return customer

    manmeet = make_contact("Manmeet Kaur", "940609752392281", 1)
    karan = make_contact("Karan Sharma", "1036903189236884", 2)

    contact_list = models.LeadContactList(ClientId=client.Id, Name="hot leads sept",
                                          SourceType="leads", Status="ready")
    db.add(contact_list)
    db.flush()
    db.add(models.LeadContactListItem(ClientId=client.Id, ListId=contact_list.Id, RowNumber=1,
                                      Name="Manmeet Kaur", CustomerId=manmeet.Id,
                                      PhoneEnc=encrypt_pii(SHARED_PHONE), PhoneHash="x", IsValid=True))
    db.add(models.LeadContactListItem(ClientId=client.Id, ListId=contact_list.Id, RowNumber=2,
                                      Name="Karan Sharma", CustomerId=karan.Id,
                                      PhoneEnc=encrypt_pii(SHARED_PHONE), PhoneHash="x", IsValid=True))
    db.commit()

    campaign = models.LeadCampaign(ClientId=client.Id, Name="test", Kind="message",
                                   Channel="instagram", ChannelAccountId=account.Id,
                                   AudienceType="list", ListId=contact_list.Id,
                                   MessageBody="hi {{name}}")
    db.add(campaign)
    db.commit()
    return db, client, campaign


def test_two_different_instagram_accounts_sharing_a_phone_both_become_recipients():
    db, client, campaign = setup_two_instagram_contacts_sharing_a_phone()
    result = cr.build_audience(db, campaign)
    assert result["added"] == 2, result   # NOT deduplicated against each other
    recipients = db.query(models.LeadCampaignRecipient).filter_by(CampaignId=campaign.Id).all()
    igsids = {r.ExternalUserId for r in recipients}
    assert igsids == {"940609752392281", "1036903189236884"}


def test_a_contact_with_no_instagram_identity_is_skipped_not_sent_a_phone_based_target():
    db = SessionLocalAdmin()
    client = Client(Name="Nexa Finserv")
    db.add(client)
    db.flush()
    account = models.LeadChannelAccount(ClientId=client.Id, Channel="instagram", Name="acct",
                                        ExternalId="page-nexa")
    db.add(account)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="No IG", DisplayName="No IG",
                                   PhoneEnc=encrypt_pii("+919000000000"))
    db.add(customer)
    db.flush()
    contact_list = models.LeadContactList(ClientId=client.Id, Name="l", SourceType="leads", Status="ready")
    db.add(contact_list)
    db.flush()
    db.add(models.LeadContactListItem(ClientId=client.Id, ListId=contact_list.Id, RowNumber=1,
                                      Name="No IG", CustomerId=customer.Id,
                                      PhoneEnc=encrypt_pii("+919000000000"), PhoneHash="y", IsValid=True))
    campaign = models.LeadCampaign(ClientId=client.Id, Name="t2", Kind="message", Channel="instagram",
                                   ChannelAccountId=account.Id, AudienceType="list",
                                   ListId=contact_list.Id, MessageBody="hi")
    db.add(campaign)
    db.commit()

    result = cr.build_audience(db, campaign)
    assert result["added"] == 0
    assert result["skipped"] == 1


def test_send_message_uses_the_igsid_never_the_phone_number():
    db, client, campaign = setup_two_instagram_contacts_sharing_a_phone()
    cr.build_audience(db, campaign)
    recipient = db.query(models.LeadCampaignRecipient).filter_by(
        CampaignId=campaign.Id, ExternalUserId="940609752392281"
    ).one()

    sent_to = {}
    channels.send_text = lambda account, channel, to, text: sent_to.update(to=to) or "msg-1"
    result = cr._send_message(db, campaign, recipient, phone=SHARED_PHONE, context={"name": "Manmeet"})

    assert result == "sent"
    assert sent_to["to"] == "940609752392281"   # the IGSID, not the phone number


def test_whatsapp_campaigns_are_unaffected_and_still_use_phone():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes WA")
    db.add(client)
    db.flush()
    account = models.LeadChannelAccount(ClientId=client.Id, Channel="whatsapp", Name="wa",
                                        ExternalId="wa-1")
    db.add(account)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="WA", DisplayName="WA Contact",
                                   PhoneEnc=encrypt_pii("+919111111111"))
    db.add(customer)
    db.flush()
    contact_list = models.LeadContactList(ClientId=client.Id, Name="wa list", SourceType="leads", Status="ready")
    db.add(contact_list)
    db.flush()
    db.add(models.LeadContactListItem(ClientId=client.Id, ListId=contact_list.Id, RowNumber=1,
                                      Name="WA Contact", CustomerId=customer.Id,
                                      PhoneEnc=encrypt_pii("+919111111111"), PhoneHash="z", IsValid=True))
    campaign = models.LeadCampaign(ClientId=client.Id, Name="wa", Kind="message", Channel="whatsapp",
                                   ChannelAccountId=account.Id, AudienceType="list",
                                   ListId=contact_list.Id, MessageBody="hi")
    db.add(campaign)
    db.commit()

    result = cr.build_audience(db, campaign)
    assert result["added"] == 1
    recipient = db.query(models.LeadCampaignRecipient).filter_by(CampaignId=campaign.Id).one()
    assert recipient.ExternalUserId is None
    assert recipient.DedupeKey  # phone-fingerprint based, unchanged


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
