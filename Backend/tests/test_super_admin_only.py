"""Companies, roles and permissions are managed by the super admin (role `Admin`) only.

Sends real requests through FastAPI (so the guards run as they do in production) with the
caller's identity swapped in. Every locked endpoint must answer 403 to a company admin,
manager and employee, and must let the super admin through. The self-service endpoints a
company's own staff need must stay open. Run: python tests/test_super_admin_only.py
"""
import conftest_stub  # noqa: F401  — installs core.base/core.database/core.auth stubs first
from conftest_stub import Base, SessionLocalAdmin, engine

from fastapi import FastAPI
from fastapi.testclient import TestClient

from domain.models import Client  # noqa: E402
from LeadAI.rbac import P, ROLE_PERMISSIONS, Principal, current_principal  # noqa: E402
from LeadAI.routers import companies, role_permissions, roles, user_management  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001 — known duplicate index name on the domain `batchinfo` table
        pass

app = FastAPI()
for router in (companies.router, roles.router, role_permissions.router, user_management.router):
    app.include_router(router, prefix="/api/leadai")
http = TestClient(app)

_db = SessionLocalAdmin()
_company = Client(Name="Nexa Finserv")
_db.add(_company)
_db.commit()
CID = _company.Id
GUARD_MESSAGE = "Only a super admin can do this."

# (method, path) — everything the super admin alone may call.
LOCKED = [
    ("GET", "/companies"), ("POST", "/companies"),
    ("GET", f"/companies/{CID}"), ("PATCH", f"/companies/{CID}"), ("DELETE", f"/companies/{CID}"),
    ("GET", f"/companies/{CID}/users"),
    ("GET", f"/companies/{CID}/permissions"), ("PATCH", f"/companies/{CID}/permissions"),
    ("GET", "/access/permissions"),
    ("GET", "/access/roles"), ("POST", "/access/roles"), ("PATCH", "/access/roles/x"),
    ("GET", "/access/role-permissions/all"), ("GET", "/access/role-permissions/manager"),
    ("PUT", "/access/role-permissions/manager"), ("PATCH", "/access/role-permissions/manager"),
    ("DELETE", "/access/role-permissions/manager"),
    ("POST", "/user-management/create"),          # makes a login AND a role: was unguarded
]
# Company self-service: must NOT be blocked by the super-admin guard.
OPEN_TO_COMPANY_STAFF = [
    ("GET", "/access/me"),
    ("GET", "/access/assignable-users"),
    ("GET", f"/companies/{CID}/settings"),
    ("DELETE", "/access/roles/x"),          # Team page "remove member"
]


def call_as(role, method, path):
    perms = set(P) if role == "Admin" else set(ROLE_PERMISSIONS[role])
    who = Principal(email="t@x.test", role=role, client_id=None if role == "Admin" else CID,
                    permissions=perms)
    app.dependency_overrides[current_principal] = lambda: who
    return http.request(method, "/api/leadai" + path, json={})


def blocked_by_guard(resp):
    return resp.status_code == 403 and resp.json().get("detail") == GUARD_MESSAGE


def test_company_staff_are_refused_on_every_locked_endpoint():
    for role in ("company_admin", "manager", "employee"):
        for method, path in LOCKED:
            resp = call_as(role, method, path)
            assert blocked_by_guard(resp), f"{role} was not refused on {method} {path}: {resp.status_code} {resp.text[:120]}"


def test_super_admin_gets_through_every_locked_endpoint():
    for method, path in LOCKED:
        resp = call_as("Admin", method, path)
        assert not blocked_by_guard(resp), f"super admin was refused on {method} {path}"
    assert call_as("Admin", "GET", "/companies").status_code == 200
    assert call_as("Admin", "GET", "/access/permissions").status_code == 200


def test_self_service_endpoints_stay_open_to_a_company_admin():
    for method, path in OPEN_TO_COMPANY_STAFF:
        resp = call_as("company_admin", method, path)
        assert not blocked_by_guard(resp), f"company admin wrongly locked out of {method} {path}"


def test_nobody_can_make_themselves_a_super_admin_through_user_create():
    body = {"email": "x@example.com", "password": "secret1", "name": "X", "role": "Admin"}
    for role in ("company_admin", "manager", "employee"):
        perms = set(ROLE_PERMISSIONS[role])
        who = Principal(email="t@x.test", role=role, client_id=CID, permissions=perms)
        app.dependency_overrides[current_principal] = lambda who=who: who
        resp = http.post("/api/leadai/user-management/create", json=body)
        assert blocked_by_guard(resp), f"{role}: {resp.status_code} {resp.text[:100]}"


def test_user_create_rejects_unknown_roles_and_a_missing_company():
    who = Principal(email="t@x.test", role="Admin", client_id=None, permissions=set(P))
    app.dependency_overrides[current_principal] = lambda: who
    base = {"email": "x@example.com", "password": "secret1", "name": "X"}
    bad_role = http.post("/api/leadai/user-management/create", json={**base, "role": "superuser", "client_id": CID})
    assert bad_role.status_code == 400 and "Unknown role" in bad_role.json()["detail"]
    no_company = http.post("/api/leadai/user-management/create", json={**base, "role": "company_admin"})
    assert no_company.status_code == 400 and "client_id is required" in no_company.json()["detail"]


def test_an_override_giving_company_admin_the_permission_still_does_not_unlock_it():
    # The role check comes first, so extra permissions cannot open these endpoints.
    who = Principal(email="t@x.test", role="company_admin", client_id=CID, permissions=set(P))
    app.dependency_overrides[current_principal] = lambda: who
    for method, path in LOCKED[:3]:
        assert blocked_by_guard(http.request(method, "/api/leadai" + path, json={}))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
