"""
User management — create users in the identity server.
"""
from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from Domain.models import Client

from .. import activity
from ..activity import A
from ..db import get_leadai_db
from ..models import (
    ROLE_ADMIN,
    ROLE_COMPANY_ADMIN,
    ROLE_EMPLOYEE,
    ROLE_MANAGER,
    LeadUserRole,
    utcnow,
)
from ..rbac import Principal, require, resolve_scope, visible_roles
from ..schemas import MemberCreate, MemberListOut, MemberOut, MemberUpdate, UserManagementCreate, UserManagementOut, UserManagementUpdate

router = APIRouter(prefix="/user-management", tags=["LeadAI • User Management"])

# ---------------------------------------------------------------------------
# Identity server integration
# ---------------------------------------------------------------------------

IDP_BASE_URL = os.getenv("LOCAL_IDENTITY_SERVER", "").rstrip("/")
IDP_CONNECT_TIMEOUT = float(os.getenv("IDP_CONNECT_TIMEOUT", "10"))
IDP_READ_TIMEOUT = float(os.getenv("IDP_READ_TIMEOUT", "15"))

# LeadAI role -> identity-server role. The IDP's POST /api/user-management/create
# contract expects one of its OWN role names (CompanyAdmin / Employee / ...), so
# the LeadAI grant (stored in leadai_user_roles) is translated here.
IDP_ROLE_MAP = {
    ROLE_ADMIN: "CompanyAdmin",
    ROLE_COMPANY_ADMIN: "CompanyAdmin",
    ROLE_MANAGER: "Manager",
    ROLE_EMPLOYEE: "Employee",
}
IDP_DEFAULT_ROLE = "Employee"


def _idp_role(leadai_role: str | None) -> str:
    """Translate a LeadAI role into the identity server's role name."""
    if not leadai_role:
        return IDP_DEFAULT_ROLE
    return IDP_ROLE_MAP.get(leadai_role, IDP_DEFAULT_ROLE)


async def _create_idp_user(
    *,
    email: str,
    password: str,
    name: str,
    role: str | None = None,
    client_id: str | None = None,
    client_name: str | None = None,
    send_email_confirmation: bool = False,
) -> dict:
    """Create a user in the identity server via POST /api/user-management/create."""
    url = f"{IDP_BASE_URL}/api/user-management/create"
    payload = {
        "email": email,
        "password": password,
        "name": name,
        "role": _idp_role(role),
        "clientId": client_id,
        "clientName": client_name,
        "sendEmailConfirmation": send_email_confirmation,
    }
    timeout = httpx.Timeout(IDP_READ_TIMEOUT, connect=IDP_CONNECT_TIMEOUT)
    async with httpx.AsyncClient(verify=False, timeout=timeout) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code == 400:
            detail = resp.json()
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=detail.get("Errors", detail),
            )
        if resp.status_code == 409:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="User already exists in identity server",
            )
        resp.raise_for_status()
        return resp.json() if resp.content else {}


async def _delete_idp_user(*, user_id: str) -> dict:
    """Delete a user from the identity server via DELETE /api/user-management/{userId}."""
    url = f"{IDP_BASE_URL}/api/user-management/{user_id}"
    timeout = httpx.Timeout(IDP_READ_TIMEOUT, connect=IDP_CONNECT_TIMEOUT)
    async with httpx.AsyncClient(verify=False, timeout=timeout) as client:
        resp = await client.delete(url)
        if resp.status_code == 404:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail="User not found in identity server",
            )
        if resp.status_code == 400:
            detail = resp.json()
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=detail.get("Errors", detail),
            )
        resp.raise_for_status()
        return resp.json() if resp.content else {}


async def _update_idp_user(
    *,
    user_id: str,
    email: str | None = None,
    password: str | None = None,
    name: str | None = None,
    role: str | None = None,
    client_id: str | None = None,
    client_name: str | None = None,
) -> dict:
    """Update a user in the identity server via PUT /api/user-management/{userId}."""
    url = f"{IDP_BASE_URL}/api/user-management/{user_id}"
    payload: dict = {}
    if email is not None:
        payload["email"] = email
    if password is not None:
        payload["password"] = password
    if name is not None:
        payload["name"] = name
    if role is not None:
        payload["role"] = _idp_role(role)
    if client_id is not None:
        payload["clientId"] = client_id
    if client_name is not None:
        payload["clientName"] = client_name
    timeout = httpx.Timeout(IDP_READ_TIMEOUT, connect=IDP_CONNECT_TIMEOUT)
    async with httpx.AsyncClient(verify=False, timeout=timeout) as client:
        resp = await client.put(url, json=payload)
        if resp.status_code == 404:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail="User not found in identity server",
            )
        if resp.status_code == 400:
            detail = resp.json()
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=detail.get("Errors", detail),
            )
        if resp.status_code == 409:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="Email already exists in identity server",
            )
        resp.raise_for_status()
        return resp.json() if resp.content else {}


@router.post(
    "/create",
    response_model=UserManagementOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user in the identity server",
)
async def create_user(
    payload: UserManagementCreate,
    request: Request,
    db: Session = Depends(get_leadai_db),
):
    # 1. Create user in the identity server
    try:
        idp_result = await _create_idp_user(
            email=payload.email,
            password=payload.password,
            name=payload.name,
            role=payload.role,
            client_id=payload.client_id,
            client_name=payload.client_name,
            send_email_confirmation=payload.send_email_confirmation,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Identity server error: {exc}",
        )

    user_id = idp_result.get("id") or idp_result.get("userId") or payload.email

    # 2. Record the user locally.
    #
    # THE BUG THIS FIXES
    # This whole block used to be wrapped in `if payload.role:`. Create a user
    # without an explicit role — which the schema permits, since `role` is
    # optional — and NOTHING was written here: no leadai_user_roles row, no
    # activity log, no commit. The user existed in the identity server and was
    # invisible to this application, so the company details screen had no way to
    # list who belonged to a company.
    #
    # Every user created through this endpoint now gets a directory row. A user
    # with no role specified is recorded as an employee, which is the least
    # privilege any member can hold — recording them is a directory concern, and
    # granting them capability is a separate decision made by ROLE_PERMISSIONS.
    granted_role = payload.role or ROLE_EMPLOYEE

    existing = (
        db.query(LeadUserRole)
        .filter(
            LeadUserRole.UserEmail == payload.email.lower(),
            LeadUserRole.ClientId == payload.client_id,
            LeadUserRole.IsDeleted == False,  # noqa: E712
        )
        .first()
    )
    if existing:
        existing.Role = granted_role
        existing.UserId = existing.UserId or str(user_id)
        existing.FullName = payload.name or existing.FullName
        existing.IsActive = True
        existing.UpdatedBy = "system"
        existing.UpdatedAt = utcnow()
        client_id = existing.ClientId
    else:
        db.add(
            LeadUserRole(
                UserEmail=payload.email.lower(),
                UserId=str(user_id),
                FullName=payload.name,
                Role=granted_role,
                ClientId=payload.client_id,
                IsActive=True,
                CreatedBy="system",
            )
        )
        client_id = payload.client_id

    activity.log(
        db,
        action=A.USER_CREATED,
        client_id=payload.client_id,
        actor_email=payload.email,
        entity_type="user",
        entity_id=str(user_id),
        message=f"Created user '{payload.email}' as {granted_role}",
        meta={
            "role": granted_role,
            "role_explicit": bool(payload.role),
            "client_id": payload.client_id,
        },
        request=request,
    )
    db.commit()

    return UserManagementOut(
        id=str(user_id),
        email=payload.email,
        name=payload.name,
        email_confirmed=not payload.send_email_confirmation,
        role_granted=granted_role,
        client_id=client_id,
    )


# ---------------------------------------------------------------------------
# Company admin: create members in their company
# ---------------------------------------------------------------------------

COMPANY_ROLES = {"employee", "manager", "company_admin"}


@router.post(
    "/members",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a member in your company (company admin)",
)
async def create_member(
    payload: MemberCreate,
    request: Request,
    principal: Principal = Depends(require("role.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)

    if payload.role not in COMPANY_ROLES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role '{payload.role}'. Allowed: {', '.join(sorted(COMPANY_ROLES))}",
        )

    # Check for existing role within the same company (including soft-deleted)
    existing = (
        db.query(LeadUserRole)
        .filter(
            LeadUserRole.UserEmail == payload.email.lower(),
            LeadUserRole.ClientId == client_id,
        )
        .first()
    )
    if existing and not existing.IsDeleted:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"User '{payload.email}' already exists in this company.",
        )

    # 1. Create user in the identity server directly
    client = (
        db.query(Client)
        .filter(Client.Id == client_id, Client.IsDeleted == False)  # noqa: E712
        .first()
    )
    try:
        idp_result = await _create_idp_user(
            email=payload.email,
            password=payload.password,
            name=payload.name,
            role=payload.role,
            client_id=client_id,
            client_name=client.Name if client else None,
            send_email_confirmation=payload.send_email_confirmation,
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_409_CONFLICT:
            # User already exists in IDP — proceed with local role assignment
            idp_result = {}
        else:
            raise
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Identity server error: {exc}",
        )

    user_id = idp_result.get("id") or idp_result.get("userId") or payload.email

    # 2. Grant role in this company
    if existing:
        # Reactivate soft-deleted role
        existing.UserId = user_id
        existing.FullName = payload.name
        existing.Role = payload.role
        existing.IsActive = True
        existing.IsDeleted = False
        existing.UpdatedBy = principal.email
        grant = existing
    else:
        grant = LeadUserRole(
            UserEmail=payload.email.lower(),
            UserId=user_id,
            FullName=payload.name,
            Role=payload.role,
            ClientId=client_id,
            IsActive=True,
            CreatedBy=principal.email,
        )
        db.add(grant)

    activity.log_principal(
        db,
        principal,
        action=A.USER_CREATED,
        client_id=client_id,
        entity_type="member",
        entity_id=str(user_id),
        message=f"Created member '{payload.email}' with role '{payload.role}'",
        meta={"role": payload.role},
        request=request,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"User '{payload.email}' already exists in this company.",
        )
    db.refresh(grant)

    return MemberOut(
        id=str(user_id),
        email=payload.email,
        name=payload.name,
        role=payload.role,
        client_id=client_id,
        is_active=True,
        created_at=grant.CreatedAt,
    )


# ---------------------------------------------------------------------------
# Company admin: list and update employees
# ---------------------------------------------------------------------------


@router.get(
    "/employees",
    response_model=MemberListOut,
    summary="List employees in your company (company admin)",
)
def list_employees(
    principal: Principal = Depends(require("role.read")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)

    rows = (
        db.query(LeadUserRole)
        .filter(
            LeadUserRole.ClientId == client_id,
            LeadUserRole.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadUserRole.CreatedAt.desc())
        .all()
    )

    items = [
        MemberOut(
            id=str(r.Id),
            email=r.UserEmail,
            name=r.FullName,
            role=r.Role,
            client_id=str(r.ClientId) if r.ClientId else "",
            is_active=bool(r.IsActive),
            created_at=r.CreatedAt,
        )
        for r in rows
    ]

    return MemberListOut(total=len(items), items=items)


@router.patch(
    "/employees/{employee_id}",
    response_model=MemberOut,
    summary="Update an employee in your company (company admin)",
)
def update_employee(
    employee_id: str,
    payload: MemberUpdate,
    request: Request,
    principal: Principal = Depends(require("role.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)

    row = db.get(LeadUserRole, employee_id)
    if not row or row.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found")

    if row.ClientId != client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found")

    # Self-lockout guard
    if row.UserEmail == principal.email.lower():
        if payload.role and payload.role != row.Role:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="You cannot change your own role",
            )
        if payload.is_active is False:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="You cannot deactivate your own access",
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
        client_id=client_id,
        entity_type="member",
        entity_id=row.Id,
        message=f"Updated employee '{row.UserEmail}'",
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

    return MemberOut(
        id=str(row.Id),
        email=row.UserEmail,
        name=row.FullName,
        role=row.Role,
        client_id=str(row.ClientId) if row.ClientId else "",
        is_active=bool(row.IsActive),
        created_at=row.CreatedAt,
    )


# ---------------------------------------------------------------------------
# Identity server user management: update & delete
# ---------------------------------------------------------------------------


@router.put(
    "/{user_id}",
    response_model=UserManagementOut,
    summary="Update a user in the identity server",
)
async def update_user(
    user_id: str,
    payload: UserManagementUpdate,
    request: Request,
    principal: Principal = Depends(require("role.manage")),
    db: Session = Depends(get_leadai_db),
):
    # 1. Update user in the identity server
    try:
        await _update_idp_user(
            user_id=user_id,
            email=payload.email,
            password=payload.password,
            name=payload.name,
            role=payload.role,
            client_id=payload.client_id,
            client_name=payload.client_name,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Identity server error: {exc}",
        )

    # 2. Update local role grant if role or client changed
    role_granted = None
    client_id = None
    if payload.role:
        grants = (
            db.query(LeadUserRole)
            .filter(
                LeadUserRole.UserId == user_id,
                LeadUserRole.IsDeleted == False,  # noqa: E712
            )
            .all()
        )
        # Update the grant matching the target client, or the first one
        target = None
        if payload.client_id:
            target = next((g for g in grants if g.ClientId == payload.client_id), None)
        if not target and grants:
            target = grants[0]

        if target:
            target.Role = payload.role
            if payload.name:
                target.FullName = payload.name
            if payload.email:
                target.UserEmail = payload.email.lower()
            target.UpdatedBy = principal.email
            target.UpdatedAt = utcnow()
            role_granted = target.Role
            client_id = target.ClientId
        else:
            grant = LeadUserRole(
                UserEmail=(payload.email or "").lower(),
                UserId=user_id,
                FullName=payload.name,
                Role=payload.role,
                ClientId=payload.client_id,
                IsActive=True,
                CreatedBy=principal.email,
            )
            db.add(grant)
            role_granted = payload.role
            client_id = payload.client_id

        activity.log_principal(
            db,
            principal,
            action=A.USER_UPDATED,
            client_id=client_id,
            entity_type="user",
            entity_id=user_id,
            message=f"Updated user '{user_id}'",
            meta={
                "role": payload.role,
                "client_id": client_id,
            },
            log_type="Security",
            request=request,
        )
        db.commit()

    return UserManagementOut(
        id=user_id,
        email=payload.email or "",
        name=payload.name,
        role_granted=role_granted,
        client_id=client_id,
    )


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a user from the identity server",
)
async def delete_user(
    user_id: str,
    request: Request,
    principal: Principal = Depends(require("role.manage")),
    db: Session = Depends(get_leadai_db),
):
    # 1. Delete user from the identity server
    try:
        await _delete_idp_user(user_id=user_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Identity server error: {exc}",
        )

    # 2. Soft-delete all local role grants for this user
    grants = (
        db.query(LeadUserRole)
        .filter(
            LeadUserRole.UserId == user_id,
            LeadUserRole.IsDeleted == False,  # noqa: E712
        )
        .all()
    )
    for grant in grants:
        grant.IsDeleted = True
        grant.IsActive = False
        grant.UpdatedBy = principal.email
        grant.UpdatedAt = utcnow()

    activity.log_principal(
        db,
        principal,
        action=A.USER_DELETED,
        client_id=grants[0].ClientId if grants else None,
        entity_type="user",
        entity_id=user_id,
        message=f"Deleted user '{user_id}'",
        meta={"grants_removed": len(grants)},
        log_type="Security",
        request=request,
    )
    db.commit()

    return {"detail": "User deleted successfully", "user_id": user_id}
