"""A company admin adding a member whose email is already registered with the identity
server used to get a silent, misleading success: the 409 from the IDP was swallowed,
a local role was granted anyway, and the response still said 201 Created — with the
password the admin just typed never actually applied anywhere (the pre-existing IDP
account keeps its old password). The new employee then can't log in with the
credentials the admin gave them, and nothing told anyone why.

Reproduces a live log: POST .../user-management/create -> 409, POST
.../user-management/members -> 201 anyway. Now the conflict must reach the caller, and
nothing must be written locally when it does. Run: python tests/test_member_create_conflict.py
"""
import asyncio

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from fastapi import HTTPException

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.rbac import ROLE_PERMISSIONS, Principal  # noqa: E402
from LeadAI.routers import user_management  # noqa: E402
from LeadAI.schemas import MemberCreate  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def setup():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.commit()
    principal = Principal(email="admin@kestrel.test", role="company_admin", client_id=client.Id,
                          permissions=set(ROLE_PERMISSIONS["company_admin"]))
    return db, client, principal


async def _conflicting_idp_user(**kwargs):
    raise HTTPException(409, detail="User already exists in identity server")


def test_a_conflicting_email_is_refused_not_silently_granted_a_role():
    db, client, principal = setup()
    real = user_management._create_idp_user
    user_management._create_idp_user = _conflicting_idp_user
    try:
        payload = MemberCreate(email="taken@example.com", password="secret1", name="Someone",
                               role="employee")
        try:
            asyncio.run(user_management.create_member(payload, request=None, principal=principal, db=db))
            assert False, "a conflicting email should have been refused"
        except HTTPException as exc:
            assert exc.status_code == 409
            assert "already registered" in exc.detail
    finally:
        user_management._create_idp_user = real

    # Nothing was written: no half-applied member with a password that never took effect.
    rows = db.query(models.LeadUserRole).filter_by(UserEmail="taken@example.com", ClientId=client.Id).all()
    assert rows == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
