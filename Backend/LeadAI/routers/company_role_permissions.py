"""
Company-scoped role permission grants.

Different from routers/role_permissions.py, which lets a PLATFORM admin override any
of the full permission catalogue for a role, everywhere. This endpoint is deliberately
narrower in WHAT can be granted (only rbac.COMPANY_GRANTABLE_PERMISSIONS — today just
lead.reveal_pii — never role.manage, company.manage, or anything else; those stay
platform-admin-only, unreachable from here) but WIDER in WHO can grant it: a company
admin may now also use it, scoped to their own company only, alongside a platform
admin who could already reach the same data directly.

"Let our Managers reveal customer contact details" should not also grant it to every
other company's Managers — that is exactly what LeadCompanyRolePermission (keyed by
ClientId + Role + PermissionKey) fixes: leadai_role_permissions has no ClientId column
at all, so an override written there is global by construction. A platform admin
using THIS endpoint still must resolve to one company (resolve_scope requires
?client_id= for them, as everywhere else) — it was never a way around that.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import activity
from ..activity import A
from ..db import get_leadai_db
from ..models import utcnow
from ..models_ext import LeadCompanyRolePermission
from ..rbac import (
    COMPANY_GRANTABLE_PERMISSIONS,
    COMPANY_GRANTABLE_ROLES,
    P,
    Principal,
    ROLE_PERMISSIONS,
    require,
    resolve_scope,
)
from ..schemas import CompanyRolePermissionOut, CompanyRolePermissionUpdate, Ok

router = APIRouter(prefix="/access/company-role-permissions", tags=["LeadAI • Company role permissions"])


def _check_role(role: str) -> None:
    if role not in COMPANY_GRANTABLE_ROLES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid role: {role}. A company admin may only grant permissions to: "
                f"{', '.join(COMPANY_GRANTABLE_ROLES)}."
            ),
        )


def _check_permission_key(key: str) -> None:
    if key not in COMPANY_GRANTABLE_PERMISSIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{key}' cannot be granted this way. A company admin may only grant: "
                f"{', '.join(sorted(COMPANY_GRANTABLE_PERMISSIONS))}."
            ),
        )


@router.get(
    "/{role}",
    response_model=list[CompanyRolePermissionOut],
    summary="List this company's own grants for a role",
)
def get_company_role_permissions(
    role: str,
    principal: Principal = Depends(require("role.manage")),
    db: Session = Depends(get_leadai_db),
):
    _check_role(role)
    client_id = resolve_scope(principal)
    rows = (
        db.query(LeadCompanyRolePermission)
        .filter(
            LeadCompanyRolePermission.ClientId == client_id,
            LeadCompanyRolePermission.Role == role,
            LeadCompanyRolePermission.IsDeleted == False,  # noqa: E712
        )
        .all()
    )
    granted = {r.PermissionKey: r.IsGranted for r in rows}
    return [
        CompanyRolePermissionOut(
            role=role,
            permission_key=key,
            is_granted=granted.get(key, key in ROLE_PERMISSIONS.get(role, set())),
        )
        for key in sorted(COMPANY_GRANTABLE_PERMISSIONS)
    ]


@router.patch(
    "/{role}",
    response_model=CompanyRolePermissionOut,
    summary="Grant or revoke one permission for a role, in this company only",
)
def set_company_role_permission(
    role: str,
    payload: CompanyRolePermissionUpdate,
    request: Request,
    principal: Principal = Depends(require("role.manage")),
    db: Session = Depends(get_leadai_db),
):
    _check_role(role)
    _check_permission_key(payload.permission_key)
    client_id = resolve_scope(principal)

    default_granted = payload.permission_key in ROLE_PERMISSIONS.get(role, set())
    # Not filtered by IsDeleted: the unique constraint is on the raw (ClientId, Role,
    # PermissionKey) columns, so a previously revoked row still occupies that slot. A
    # grant -> revoke -> grant-again cycle (a completely normal thing for an admin to
    # do) must revive that same row rather than insert a second one and hit a
    # database IntegrityError.
    existing = (
        db.query(LeadCompanyRolePermission)
        .filter(
            LeadCompanyRolePermission.ClientId == client_id,
            LeadCompanyRolePermission.Role == role,
            LeadCompanyRolePermission.PermissionKey == payload.permission_key,
        )
        .one_or_none()
    )

    if existing:
        if payload.is_granted == default_granted:
            # Back to the role's own default: no override needed.
            existing.IsDeleted = True
            existing.UpdatedBy = principal.email
            existing.UpdatedAt = utcnow()
        else:
            existing.IsGranted = payload.is_granted
            existing.IsDeleted = False
            existing.UpdatedBy = principal.email
            existing.UpdatedAt = utcnow()
    elif payload.is_granted != default_granted:
        db.add(
            LeadCompanyRolePermission(
                ClientId=client_id,
                Role=role,
                PermissionKey=payload.permission_key,
                IsGranted=payload.is_granted,
                GrantedBy=principal.email,
            )
        )

    activity.log_principal(
        db,
        principal,
        action=A.PERMISSION_UPDATED,
        client_id=client_id,
        entity_type="company_role_permission",
        entity_id=role,
        message=(
            f"{'Granted' if payload.is_granted else 'Revoked'} "
            f"{P.get(payload.permission_key, payload.permission_key)} for role {role}"
        ),
        meta={"role": role, "permission": payload.permission_key, "is_granted": payload.is_granted},
        log_type="Security",
        request=request,
    )
    db.commit()

    return CompanyRolePermissionOut(
        role=role, permission_key=payload.permission_key, is_granted=payload.is_granted
    )
