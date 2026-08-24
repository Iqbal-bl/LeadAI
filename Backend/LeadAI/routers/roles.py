"""
Role management and self-introspection.

Deliberate boundaries:
  * This does NOT create user accounts. Identity lives in the identity server;
    this only grants LeadAI authority to an email that will authenticate there.
  * A company admin can grant manager/agent/viewer inside their own company and
    nothing else. Only a platform admin can mint another platform admin.
  * You cannot demote or deactivate yourself — that is the classic way an admin
    locks an entire company out of its own dashboard.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from Domain.models import Client

from .. import activity
from ..activity import A
from ..db import get_leadai_db
from ..models import ROLE_ADMIN, LeadUserRole, utcnow
from ..rbac import (
    P,
    ROLE_PERMISSIONS,
    Principal,
    current_principal,
    require,
    resolve_scope,
    visible_roles,
)
from ..schemas import (
    CompanyOut,
    MeOut,
    Ok,
    PermissionCatalogOut,
    RoleGrant,
    RoleOut,
    RoleUpdate,
)
from ..serializers import company_out, role_out

router = APIRouter(prefix="/access", tags=["LeadAI • Access control"])


@router.get("/me", response_model=MeOut, summary="Who am I and what may I do")
def me(
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_leadai_db),
):
    """The first call any frontend should make: it drives menu visibility,
    button enablement and the company switcher."""
    client_name = None
    if principal.client_id:
        client = db.get(Client, principal.client_id)
        client_name = client.Name if client else None

    companies: list[CompanyOut] = []
    if principal.is_platform_admin:
        rows = (
            db.query(Client)
            .filter(Client.IsDeleted == False, Client.IsActive == True)  # noqa: E712
            .order_by(Client.Name.asc())
            .all()
        )
        companies = [company_out(db, c, with_counts=False) for c in rows]
    elif principal.accessible_client_ids:
        rows = (
            db.query(Client)
            .filter(Client.Id.in_(principal.accessible_client_ids))
            .order_by(Client.Name.asc())
            .all()
        )
        companies = [company_out(db, c, with_counts=False) for c in rows]

    return MeOut(
        email=principal.email,
        full_name=principal.full_name,
        role=principal.role,
        client_id=principal.client_id,
        client_name=client_name,
        permissions=sorted(principal.permissions),
        accessible_companies=companies,
    )


@router.get(
    "/permissions",
    response_model=PermissionCatalogOut,
    summary="Permission catalogue and the role matrix",
)
def permission_catalogue(principal: Principal = Depends(current_principal)):
    """Static reference so a frontend can render an accurate permissions screen
    without hardcoding the matrix."""
    return PermissionCatalogOut(
        permissions=P,
        role_permissions={role: sorted(perms) for role, perms in ROLE_PERMISSIONS.items()},
    )


@router.get("/roles", response_model=list[RoleOut], summary="List role grants")
def list_roles(
    company_id: str | None = Query(default=None, alias="for_company"),
    principal: Principal = Depends(require("role.read")),
    db: Session = Depends(get_leadai_db),
):
    query = db.query(LeadUserRole).filter(LeadUserRole.IsDeleted == False)  # noqa: E712

    if principal.is_platform_admin:
        if company_id:
            query = query.filter(LeadUserRole.ClientId == company_id)
    else:
        # A company admin sees only their own company's grants — never the
        # platform-admin rows, which have ClientId NULL.
        query = query.filter(LeadUserRole.ClientId == resolve_scope(principal))

    rows = query.order_by(LeadUserRole.CreatedAt.asc()).all()
    return [role_out(db, r) for r in rows]


@router.post(
    "/roles",
    response_model=RoleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Grant a role to a user",
)
def grant_role(
    payload: RoleGrant,
    request: Request,
    principal: Principal = Depends(require("role.manage")),
    db: Session = Depends(get_leadai_db),
):
    email = payload.user_email.lower()

    allowed = tuple(visible_roles(principal))
    if payload.role not in allowed:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Your role ({principal.role}) can only grant: {', '.join(allowed) or 'nothing'}",
        )

    # Admin is global by definition; everyone else is company-scoped.
    if payload.role == ROLE_ADMIN:
        client_id = None
    else:
        client_id = payload.client_id if principal.is_platform_admin else None
        client_id = client_id or resolve_scope(principal)
        client = db.get(Client, client_id)
        if not client or client.IsDeleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")

    existing = (
        db.query(LeadUserRole)
        .filter(
            LeadUserRole.UserEmail == email,
            LeadUserRole.ClientId == client_id,
            LeadUserRole.IsDeleted == False,  # noqa: E712
        )
        .one_or_none()
    )
    if existing:
        # Idempotent upgrade path rather than a 409 — re-inviting someone with a
        # different role is the common case, and a conflict error there is just
        # friction.
        before = existing.Role
        existing.Role = payload.role
        existing.FullName = payload.full_name or existing.FullName
        existing.IsActive = True
        existing.UpdatedBy = principal.email
        existing.UpdatedAt = utcnow()
        activity.log_principal(
            db,
            principal,
            action=A.ROLE_UPDATED,
            client_id=client_id,
            entity_type="role",
            entity_id=existing.Id,
            message=f"Changed {email} from {before} to {payload.role}",
            meta={"target_user": email, "from": before, "to": payload.role},
            log_type="Security",
            request=request,
        )
        db.commit()
        db.refresh(existing)
        return role_out(db, existing)

    row = LeadUserRole(
        UserEmail=email,
        FullName=payload.full_name,
        Role=payload.role,
        ClientId=client_id,
        IsActive=True,
        CreatedBy=principal.email,
    )
    db.add(row)
    db.flush()

    activity.log_principal(
        db,
        principal,
        action=A.ROLE_GRANTED,
        client_id=client_id,
        entity_type="role",
        entity_id=row.Id,
        message=f"Granted {payload.role} to {email}",
        meta={"target_user": email, "role": payload.role},
        log_type="Security",
        request=request,
    )
    db.commit()
    db.refresh(row)
    return role_out(db, row)


@router.patch("/roles/{grant_id}", response_model=RoleOut, summary="Update a role grant")
def update_role(
    grant_id: str,
    payload: RoleUpdate,
    request: Request,
    principal: Principal = Depends(require("role.manage")),
    db: Session = Depends(get_leadai_db),
):
    row = db.get(LeadUserRole, grant_id)
    if not row or row.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role grant not found")

    if not principal.is_platform_admin and row.ClientId != resolve_scope(principal):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role grant not found")

    # Self-lockout guard.
    if row.UserEmail == principal.email.lower():
        if payload.role and payload.role != row.Role:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "You cannot change your own role"
            )
        if payload.is_active is False:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "You cannot deactivate your own access"
            )

    if payload.role and payload.role != row.Role:
        allowed = tuple(visible_roles(principal))
        if payload.role not in allowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Your role ({principal.role}) cannot grant {payload.role}",
            )
        row.Role = payload.role
    if payload.full_name is not None:
        row.FullName = payload.full_name
    if payload.is_active is not None:
        row.IsActive = payload.is_active
    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()

    activity.log_principal(
        db,
        principal,
        action=A.ROLE_UPDATED,
        client_id=row.ClientId,
        entity_type="role",
        entity_id=row.Id,
        message=f"Updated role grant for {row.UserEmail}",
        meta={
            "target_user": row.UserEmail,
            "role": row.Role,
            "is_active": bool(row.IsActive),
        },
        log_type="Security",
        request=request,
    )
    db.commit()
    db.refresh(row)
    return role_out(db, row)


@router.delete("/roles/{grant_id}", response_model=Ok, summary="Revoke a role grant")
def revoke_role(
    grant_id: str,
    request: Request,
    principal: Principal = Depends(require("role.manage")),
    db: Session = Depends(get_leadai_db),
):
    row = db.get(LeadUserRole, grant_id)
    if not row or row.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role grant not found")
    if not principal.is_platform_admin and row.ClientId != resolve_scope(principal):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role grant not found")
    if row.UserEmail == principal.email.lower():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot revoke your own access")

    # Soft delete: the audit trail must still be able to explain a past action by
    # someone whose access was later removed.
    row.IsDeleted = True
    row.IsActive = False
    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()

    activity.log_principal(
        db,
        principal,
        action=A.ROLE_REVOKED,
        client_id=row.ClientId,
        entity_type="role",
        entity_id=row.Id,
        message=f"Revoked {row.Role} from {row.UserEmail}",
        meta={"target_user": row.UserEmail, "role": row.Role},
        log_type="Security",
        request=request,
    )
    db.commit()
    return Ok(message=f"Access revoked for {row.UserEmail}")


@router.get(
    "/assignable-users",
    response_model=list[RoleOut],
    summary="Users a conversation can be assigned to",
)
def assignable_users(
    principal: Principal = Depends(require("lead.assign", "lead.read.all")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    rows = (
        db.query(LeadUserRole)
        .filter(
            LeadUserRole.ClientId == client_id,
            LeadUserRole.IsActive == True,  # noqa: E712
            LeadUserRole.IsDeleted == False,  # noqa: E712
            LeadUserRole.Role.in_(("employee", "manager", "company_admin")),
        )
        .order_by(LeadUserRole.Role.asc())
        .all()
    )
    return [role_out(db, r) for r in rows]
