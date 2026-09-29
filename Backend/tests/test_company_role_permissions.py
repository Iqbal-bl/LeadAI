"""A company admin can now ALSO grant/revoke lead.reveal_pii to Manager/Employee,
scoped to their OWN company only — alongside a platform admin, whose existing access
is unchanged. Different from routers/role_permissions.py (platform-admin-only, and
leadai_role_permissions has no ClientId column, so an override there is global): this
is the mechanism requested — "let our own Managers reveal PII" must never also grant
it to every other company's Managers, and must never let a company admin touch any
permission besides lead.reveal_pii; everything else stays platform-admin-only.

Sends real requests through FastAPI so the route guards run as they do in
production; permission propagation itself is checked directly against
rbac.effective_permissions_for(), which is what current_principal() calls on every
real request. Run: python tests/test_company_role_permissions.py
"""
import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from fastapi import FastAPI
from fastapi.testclient import TestClient

from domain.models import Client  # noqa: E402
from LeadAI import rbac  # noqa: E402
from LeadAI.rbac import P, ROLE_PERMISSIONS, Principal, current_principal  # noqa: E402
from LeadAI.routers import company_role_permissions  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass

app = FastAPI()
app.include_router(company_role_permissions.router, prefix="/api/leadai")
http = TestClient(app)

_db = SessionLocalAdmin()
_company_a = Client(Name="Kestrel Homes")
_company_b = Client(Name="Nexa Finserv")
_db.add_all([_company_a, _company_b])
_db.commit()
CID_A, CID_B = _company_a.Id, _company_b.Id


def call_as(role, client_id, method, path, **kw):
    who = Principal(email="t@x.test", role=role, client_id=client_id,
                    permissions=set(ROLE_PERMISSIONS[role]))
    app.dependency_overrides[current_principal] = lambda who=who: who
    return http.request(method, "/api/leadai" + path, **kw)


def db():
    return SessionLocalAdmin()


# --------------------------------------------------------------------- the grant
def test_a_company_admin_can_grant_reveal_pii_to_manager_in_their_own_company():
    assert "lead.reveal_pii" not in rbac.effective_permissions_for(db(), "manager", client_id=CID_A)
    resp = call_as("company_admin", CID_A, "PATCH", "/access/company-role-permissions/manager",
                   json={"permission_key": "lead.reveal_pii", "is_granted": True})
    assert resp.status_code == 200, resp.text
    assert "lead.reveal_pii" in rbac.effective_permissions_for(db(), "manager", client_id=CID_A)


def test_the_grant_never_leaks_to_another_company():
    # The whole point: leadai_role_permissions (platform override) has no ClientId and would
    # leak; leadai_company_role_permissions is keyed by ClientId and must not.
    assert "lead.reveal_pii" not in rbac.effective_permissions_for(db(), "manager", client_id=CID_B)


def test_the_grant_never_leaks_to_a_different_role_in_the_same_company():
    assert "lead.reveal_pii" not in rbac.effective_permissions_for(db(), "employee", client_id=CID_A)


def test_revoking_removes_the_override_and_the_permission():
    resp = call_as("company_admin", CID_A, "PATCH", "/access/company-role-permissions/manager",
                   json={"permission_key": "lead.reveal_pii", "is_granted": False})
    assert resp.status_code == 200
    assert "lead.reveal_pii" not in rbac.effective_permissions_for(db(), "manager", client_id=CID_A)
    # Back at the role's own default: no leftover override row for this (client, role, key).
    from LeadAI.models_ext import LeadCompanyRolePermission
    row = (
        db().query(LeadCompanyRolePermission)
        .filter_by(ClientId=CID_A, Role="manager", PermissionKey="lead.reveal_pii", IsDeleted=False)
        .one_or_none()
    )
    assert row is None


# --------------------------------------------------------------------- the guards
def test_a_manager_cannot_grant_themselves_the_permission():
    resp = call_as("manager", CID_A, "PATCH", "/access/company-role-permissions/manager",
                   json={"permission_key": "lead.reveal_pii", "is_granted": True})
    assert resp.status_code == 403


def test_granting_after_a_previous_revoke_does_not_crash():
    # Found via mutation testing: the unique constraint is on the raw (ClientId, Role,
    # PermissionKey) columns, so a soft-deleted row from an earlier revoke still occupies
    # that slot. Grant -> revoke -> grant again is an entirely normal thing for an admin
    # to do and must not hit a database IntegrityError on the second grant.
    role = "employee"
    grant = {"permission_key": "lead.reveal_pii", "is_granted": True}
    revoke = {"permission_key": "lead.reveal_pii", "is_granted": False}
    assert call_as("company_admin", CID_A, "PATCH", f"/access/company-role-permissions/{role}",
                   json=grant).status_code == 200
    assert call_as("company_admin", CID_A, "PATCH", f"/access/company-role-permissions/{role}",
                   json=revoke).status_code == 200
    resp = call_as("company_admin", CID_A, "PATCH", f"/access/company-role-permissions/{role}",
                   json=grant)
    assert resp.status_code == 200, resp.text
    assert "lead.reveal_pii" in rbac.effective_permissions_for(db(), role, client_id=CID_A)
    call_as("company_admin", CID_A, "PATCH", f"/access/company-role-permissions/{role}", json=revoke)


def test_a_platform_admin_can_also_use_this_endpoint_for_one_named_company():
    # Everything else stays in the platform admin's hands as before — this endpoint adds a
    # company admin as a SECOND caller who can grant lead.reveal_pii, it does not take the
    # platform admin's own existing access away. Like every other company-scoped route, a
    # platform admin must still resolve to one company (client_id=None otherwise -> 400).
    who = Principal(email="admin@platform", role="Admin", client_id=CID_B, permissions=set(P))
    app.dependency_overrides[current_principal] = lambda: who
    resp = http.patch("/api/leadai/access/company-role-permissions/manager",
                      json={"permission_key": "lead.reveal_pii", "is_granted": True})
    assert resp.status_code == 200, resp.text
    assert "lead.reveal_pii" in rbac.effective_permissions_for(db(), "manager", client_id=CID_B)
    # ...and it is still scoped to CID_B only, same as a company admin's own grant is.
    assert "lead.reveal_pii" not in rbac.effective_permissions_for(db(), "manager", client_id=CID_A)
    http.patch("/api/leadai/access/company-role-permissions/manager",
              json={"permission_key": "lead.reveal_pii", "is_granted": False})


def test_only_lead_reveal_pii_is_grantable_this_way():
    # A permission not in COMPANY_GRANTABLE_PERMISSIONS must be refused outright — a company
    # admin must never be able to use this route to grant a wider capability.
    resp = call_as("company_admin", CID_A, "PATCH", "/access/company-role-permissions/manager",
                   json={"permission_key": "role.manage", "is_granted": True})
    assert resp.status_code == 400
    assert "role.manage" not in rbac.effective_permissions_for(db(), "manager", client_id=CID_A)


def test_a_company_admin_cannot_target_their_own_role_or_admin():
    for role in ("company_admin", "Admin"):
        resp = call_as("company_admin", CID_A, "PATCH", f"/access/company-role-permissions/{role}",
                       json={"permission_key": "lead.reveal_pii", "is_granted": True})
        assert resp.status_code == 400, f"role={role} status={resp.status_code}"


def test_get_lists_the_current_state():
    call_as("company_admin", CID_A, "PATCH", "/access/company-role-permissions/employee",
           json={"permission_key": "lead.reveal_pii", "is_granted": True})
    resp = call_as("company_admin", CID_A, "GET", "/access/company-role-permissions/employee")
    assert resp.status_code == 200
    body = resp.json()
    assert any(row["permission_key"] == "lead.reveal_pii" and row["is_granted"] for row in body)
    call_as("company_admin", CID_A, "PATCH", "/access/company-role-permissions/employee",
           json={"permission_key": "lead.reveal_pii", "is_granted": False})


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
