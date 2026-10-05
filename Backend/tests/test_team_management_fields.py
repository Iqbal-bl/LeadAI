"""Team Management showed a hardcoded "+1 (555) 000-0000" for every member and
"0" for every assigned-lead count, because MemberOut never carried either field —
phone didn't exist anywhere in the data model at all, and assigned_leads was real,
computable data (LeadConversation.AssignedUserEmail) that just never got wired in.

Run: python tests/test_team_management_fields.py
"""
import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.rbac import ROLE_PERMISSIONS, Principal  # noqa: E402
from LeadAI.routers import user_management  # noqa: E402

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
    grant = models.LeadUserRole(UserEmail="agent@kestrel.test", FullName="Agent One",
                                Phone="+15551234567", Role="employee", ClientId=client.Id,
                                IsActive=True)
    db.add(grant)
    db.flush()
    for i in range(3):
        customer = models.LeadCustomer(ClientId=client.Id, PublicRef=f"Customer #{i}")
        db.add(customer)
        db.flush()
        conv = models.LeadConversation(
            ClientId=client.Id, CustomerId=customer.Id, Channel="web",
            AssignedUserEmail="agent@kestrel.test" if i < 2 else None,  # 2 of 3 assigned
        )
        db.add(conv)
    db.commit()
    principal = Principal(email="admin@kestrel.test", role="company_admin", client_id=client.Id,
                          permissions=set(ROLE_PERMISSIONS["company_admin"]))
    return db, client, grant, principal


def test_list_employees_returns_real_phone_and_a_real_assigned_count():
    db, client, grant, principal = setup()
    out = user_management.list_employees(principal=principal, db=db)
    row = next(m for m in out.items if m.email == "agent@kestrel.test")
    assert row.phone == "+15551234567"
    assert row.assigned_leads == 2


def test_a_member_with_no_phone_on_file_returns_null_not_a_fake_placeholder():
    db = SessionLocalAdmin()
    client = Client(Name="Nexa Finserv")
    db.add(client)
    db.flush()
    db.add(models.LeadUserRole(UserEmail="nophone@kestrel.test", FullName="No Phone",
                               Role="employee", ClientId=client.Id, IsActive=True))
    db.commit()
    principal = Principal(email="admin@nexa.test", role="company_admin", client_id=client.Id,
                          permissions=set(ROLE_PERMISSIONS["company_admin"]))
    out = user_management.list_employees(principal=principal, db=db)
    assert out.items[0].phone is None
    assert out.items[0].assigned_leads == 0


def test_updating_an_employee_can_set_their_phone():
    db, client, grant, principal = setup()
    from LeadAI.schemas import MemberUpdate

    updated = user_management.update_employee(
        str(grant.Id), MemberUpdate(phone="+919876543210"), request=None,
        principal=principal, db=db,
    )
    assert updated.phone == "+919876543210"
    db.refresh(grant)
    assert grant.Phone == "+919876543210"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
