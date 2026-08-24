"""
Dynamic role-permission management.

Allows platform admins to customise which permissions each role has, overriding
the hardcoded defaults in rbac.ROLE_PERMISSIONS.  Company admins cannot change
permissions — only platform admins can.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import activity
from ..activity import A
from ..db import get_leadai_db
from ..models import ALL_ROLES, LeadRolePermission, utcnow
from ..rbac import (
    P,
    Principal,
    ROLE_PERMISSIONS,
    current_principal,
    require,
)
from ..schemas import (
    Ok,
    RolePermissionOut,
    RolePermissionUpdate,
    RolePermissionsBulkUpdate,
    RolePermissionsOut,
)

router = APIRouter(prefix="/access/role-permissions", tags=["LeadAI • Role permissions"])


def _db_overrides(db: Session, role: str) -> dict[str, bool]:
    """Return {permission_key: is_granted} overrides from the database."""
    rows = (
        db.query(LeadRolePermission)
        .filter(
            LeadRolePermission.Role == role,
            LeadRolePermission.IsDeleted == False,  # noqa: E712
        )
        .all()
    )
    return {r.PermissionKey: r.IsGranted for r in rows}


def effective_permissions(db: Session, role: str) -> set[str]:
    """Compute the effective permission set for a role.

    Starts with the hardcoded defaults, then applies any database overrides.
    """
    perms = set(ROLE_PERMISSIONS.get(role, set()))
    overrides = _db_overrides(db, role)
    for key, granted in overrides.items():
        if granted:
            perms.add(key)
        else:
            perms.discard(key)
    return perms


@router.get(
    "/all",
    response_model=list[RolePermissionsOut],
    summary="List effective permissions for all roles",
)
def list_all_role_permissions(
    principal: Principal = Depends(require("role.manage")),
    db: Session = Depends(get_leadai_db),
):
    results = []
    for role in ALL_ROLES:
        perms = effective_permissions(db, role)
        overrides = _db_overrides(db, role)
        items = []
        for key, desc in sorted(P.items()):
            is_default = key in ROLE_PERMISSIONS.get(role, set())
            is_granted = key in perms
            items.append(
                RolePermissionOut(
                    role=role,
                    permission_key=key,
                    description=desc,
                    is_granted=is_granted,
                    is_default=is_default,
                )
            )
        results.append(
            RolePermissionsOut(
                role=role,
                permissions=items,
                effective_permissions=sorted(perms),
            )
        )
    return results


@router.get(
    "/{role}",
    response_model=RolePermissionsOut,
    summary="List permissions for a specific role",
)
def get_role_permissions(
    role: str,
    principal: Principal = Depends(require("role.manage")),
    db: Session = Depends(get_leadai_db),
):
    if role not in ALL_ROLES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role: {role}. Valid roles: {', '.join(ALL_ROLES)}",
        )

    perms = effective_permissions(db, role)
    overrides = _db_overrides(db, role)
    items = []
    for key, desc in sorted(P.items()):
        is_default = key in ROLE_PERMISSIONS.get(role, set())
        is_granted = key in perms
        items.append(
            RolePermissionOut(
                role=role,
                permission_key=key,
                description=desc,
                is_granted=is_granted,
                is_default=is_default,
            )
        )
    return RolePermissionsOut(
        role=role,
        permissions=items,
        effective_permissions=sorted(perms),
    )


@router.put(
    "/{role}",
    response_model=RolePermissionsOut,
    summary="Set permissions for a role (full replace)",
)
def set_role_permissions(
    role: str,
    payload: RolePermissionsBulkUpdate,
    request: Request,
    principal: Principal = Depends(require("role.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Replace all custom permissions for a role. Only the permissions in the
    request body are affected; omitted permissions revert to the hardcoded
    default."""
    if role not in ALL_ROLES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role: {role}. Valid roles: {', '.join(ALL_ROLES)}",
        )

    # Validate all permission keys exist.
    invalid = [p.permission_key for p in payload.permissions if p.permission_key not in P]
    if invalid:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown permission keys: {', '.join(invalid)}",
        )

    # Prevent removing platform-admin-only permissions from Admin.
    if role == "Admin":
        critical = {"company.manage", "role.manage"}
        for p in payload.permissions:
            if not p.is_granted and p.permission_key in critical:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot revoke {p.permission_key} from Admin role",
                )

    # Delete existing overrides for this role.
    db.query(LeadRolePermission).filter(
        LeadRolePermission.Role == role,
        LeadRolePermission.IsDeleted == False,  # noqa: E712
    ).update({"IsDeleted": True, "UpdatedBy": principal.email, "UpdatedAt": utcnow()})

    # Insert new overrides (only for permissions that differ from defaults).
    default_perms = ROLE_PERMISSIONS.get(role, set())
    for p in payload.permissions:
        is_default = p.permission_key in default_perms
        # Only store overrides that differ from the default.
        if p.is_granted != is_default:
            row = LeadRolePermission(
                Role=role,
                PermissionKey=p.permission_key,
                IsGranted=p.is_granted,
                Description=P.get(p.permission_key),
                CreatedBy=principal.email,
            )
            db.add(row)

    activity.log_principal(
        db,
        principal,
        action=A.PERMISSION_UPDATED,
        client_id=None,
        entity_type="role_permission",
        entity_id=role,
        message=f"Updated permissions for role {role}",
        meta={
            "role": role,
            "permissions": {p.permission_key: p.is_granted for p in payload.permissions},
        },
        log_type="Security",
        request=request,
    )
    db.commit()

    # Return the updated state.
    perms = effective_permissions(db, role)
    overrides = _db_overrides(db, role)
    items = []
    for key, desc in sorted(P.items()):
        is_default = key in ROLE_PERMISSIONS.get(role, set())
        is_granted = key in perms
        items.append(
            RolePermissionOut(
                role=role,
                permission_key=key,
                description=desc,
                is_granted=is_granted,
                is_default=is_default,
            )
        )
    return RolePermissionsOut(
        role=role,
        permissions=items,
        effective_permissions=sorted(perms),
    )


@router.patch(
    "/{role}",
    response_model=RolePermissionsOut,
    summary="Patch a single permission for a role",
)
def patch_role_permission(
    role: str,
    payload: RolePermissionUpdate,
    request: Request,
    principal: Principal = Depends(require("role.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Toggle a single permission for a role without replacing the entire set."""
    if role not in ALL_ROLES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role: {role}. Valid roles: {', '.join(ALL_ROLES)}",
        )
    if payload.permission_key not in P:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown permission key: {payload.permission_key}",
        )
    if role == "Admin" and not payload.is_granted:
        if payload.permission_key in ("company.manage", "role.manage"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot revoke {payload.permission_key} from Admin role",
            )

    # Upsert the override row.
    existing = (
        db.query(LeadRolePermission)
        .filter(
            LeadRolePermission.Role == role,
            LeadRolePermission.PermissionKey == payload.permission_key,
            LeadRolePermission.IsDeleted == False,  # noqa: E712
        )
        .one_or_none()
    )

    default_perms = ROLE_PERMISSIONS.get(role, set())
    is_default = payload.permission_key in default_perms

    if existing:
        if payload.is_granted == is_default:
            # Revert to default: delete the override.
            existing.IsDeleted = True
            existing.UpdatedBy = principal.email
            existing.UpdatedAt = utcnow()
        else:
            existing.IsGranted = payload.is_granted
            existing.UpdatedBy = principal.email
            existing.UpdatedAt = utcnow()
    else:
        if payload.is_granted != is_default:
            row = LeadRolePermission(
                Role=role,
                PermissionKey=payload.permission_key,
                IsGranted=payload.is_granted,
                Description=P.get(payload.permission_key),
                CreatedBy=principal.email,
            )
            db.add(row)

    activity.log_principal(
        db,
        principal,
        action=A.PERMISSION_UPDATED,
        client_id=None,
        entity_type="role_permission",
        entity_id=role,
        message=f"Updated {payload.permission_key} for role {role} -> {'granted' if payload.is_granted else 'revoked'}",
        meta={
            "role": role,
            "permission": payload.permission_key,
            "is_granted": payload.is_granted,
        },
        log_type="Security",
        request=request,
    )
    db.commit()

    # Return the updated state.
    perms = effective_permissions(db, role)
    overrides = _db_overrides(db, role)
    items = []
    for key, desc in sorted(P.items()):
        is_default = key in ROLE_PERMISSIONS.get(role, set())
        is_granted = key in perms
        items.append(
            RolePermissionOut(
                role=role,
                permission_key=key,
                description=desc,
                is_granted=is_granted,
                is_default=is_default,
            )
        )
    return RolePermissionsOut(
        role=role,
        permissions=items,
        effective_permissions=sorted(perms),
    )


@router.delete(
    "/{role}",
    response_model=Ok,
    summary="Reset role permissions to defaults",
)
def reset_role_permissions(
    role: str,
    request: Request,
    principal: Principal = Depends(require("role.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Remove all custom overrides for a role, reverting to the hardcoded defaults."""
    if role not in ALL_ROLES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role: {role}. Valid roles: {', '.join(ALL_ROLES)}",
        )

    count = (
        db.query(LeadRolePermission)
        .filter(
            LeadRolePermission.Role == role,
            LeadRolePermission.IsDeleted == False,  # noqa: E712
        )
        .update({"IsDeleted": True, "UpdatedBy": principal.email, "UpdatedAt": utcnow()})
    )

    if count:
        activity.log_principal(
            db,
            principal,
            action=A.PERMISSION_UPDATED,
            client_id=None,
            entity_type="role_permission",
            entity_id=role,
            message=f"Reset permissions for role {role} to defaults",
            meta={"role": role, "reset": True},
            log_type="Security",
            request=request,
        )
    db.commit()
    return Ok(message=f"Permissions for role {role} reset to defaults ({count} overrides removed)")
