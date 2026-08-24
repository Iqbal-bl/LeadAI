"""
Companies (tenants) and their AI settings.

A "company" is a row in the EXISTING `Clients` table, so a company created here
is immediately usable by the batch-calling features that already reference
`Batch.ClientId`. LeadAI adds per-company AI configuration alongside it rather
than duplicating the company record.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from Domain.models import Client

from .. import activity
from ..activity import A
from ..config import settings
from ..db import get_leadai_db
from ..models import LeadCompanySettings, LeadUserRole, utcnow
from ..models import ROLE_ADMIN, ROLE_EMPLOYEE, ROLE_MANAGER
from ..rbac import (
    ROLE_COMPANY_ADMIN,
    Principal,
    assert_owns,
    current_principal,
    require,
    resolve_scope,
)
from ..schemas import (
    CompanyCreate,
    CompanyOut,
    CompanySettingsIn,
    CompanySettingsOut,
    CompanyUpdate,
    CompanyUserOut,
    CompanyUsersOut,
    Ok,
    ServiceItemOut,
)
from ..serializers import company_out
from ..services import ai_engine, script_engine


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/companies", tags=["LeadAI • Companies"])


@router.get("", response_model=list[CompanyOut], summary="List companies I can access")
def list_companies(
    include_inactive: bool = Query(default=False),
    principal: Principal = Depends(require("company.read")),
    db: Session = Depends(get_leadai_db),
):
    query = db.query(Client).filter(Client.IsDeleted == False)  # noqa: E712
    if not include_inactive:
        query = query.filter(Client.IsActive == True)  # noqa: E712

    # A company-scoped user only ever sees the companies they hold a grant in.
    if not principal.is_platform_admin:
        allowed = set(principal.accessible_client_ids) | (
            {principal.client_id} if principal.client_id else set()
        )
        if not allowed:
            return []
        query = query.filter(Client.Id.in_(allowed))

    return [company_out(db, c) for c in query.order_by(Client.Name.asc()).all()]


@router.post(
    "",
    response_model=CompanyOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a company (platform admin)",
)
def create_company(
    payload: CompanyCreate,
    request: Request,
    principal: Principal = Depends(require("company.manage")),
    db: Session = Depends(get_leadai_db),
):
    existing = (
        db.query(Client)
        .filter(Client.Name == payload.name, Client.IsDeleted == False)  # noqa: E712
        .first()
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "A company with that name already exists")

    client = Client(
        Name=payload.name,
        Email=payload.email,
        PhoneNumber=payload.phone_number,
        Description=payload.description,
        IsActive=True,
        CreatedBy=principal.email,
    )
    db.add(client)
    db.flush()

    # Seed the editable prompt set so the dashboard has something to show and the
    # assistant is usable the moment a document is uploaded.
    script_engine.seed_prompts(db, client.Id, created_by=principal.email)
    db.add(LeadCompanySettings(ClientId=client.Id, CreatedBy=principal.email))

    # Seed services/features for the company (only explicitly listed services are enabled)
    if payload.services:
        for s_key in payload.services:
            s_key = s_key.strip().lower()
            db.add(
                LeadCompanyService(
                    ClientId=client.Id,
                    ServiceKey=s_key,
                    IsEnabled=True,
                    CreatedBy=principal.email,
                )
            )



    if payload.admin_email:
        # Create user in identity server directly
        import httpx
        import os
        admin_name = payload.admin_name or payload.admin_email.split("@")[0]
        idp_url = os.getenv("LOCAL_IDENTITY_SERVER", "").rstrip("/")
        try:
            with httpx.Client(verify=False, timeout=10) as http:
                http.post(
                    f"{idp_url}/api/user-management/create",
                    json={
                        "email": payload.admin_email,
                        "password": "Admin@123",
                        "name": admin_name,
                        "role": "CompanyAdmin",
                        "clientId": client.Id,
                        "clientName": payload.name,
                        "sendEmailConfirmation": True,
                    },
                )
        except Exception as exc:
            logger.warning("[Companies] failed to create admin in identity server: %s", exc)

        db.add(
            LeadUserRole(
                UserEmail=payload.admin_email.lower(),
                FullName=payload.admin_name,
                Role=ROLE_COMPANY_ADMIN,
                ClientId=client.Id,
                IsActive=True,
                CreatedBy=principal.email,
            )
        )

    activity.log_principal(
        db,
        principal,
        action=A.COMPANY_CREATED,
        client_id=client.Id,
        entity_type="company",
        entity_id=client.Id,
        message=f"Created company '{client.Name}'",
        meta={"admin_granted": bool(payload.admin_email)},
        request=request,
    )
    db.commit()
    db.refresh(client)
    return company_out(db, client)


@router.get("/{company_id}", response_model=CompanyOut, summary="Get one company")
def get_company(
    company_id: str,
    principal: Principal = Depends(require("company.read")),
    db: Session = Depends(get_leadai_db),
):
    if not principal.is_platform_admin:
        allowed = set(principal.accessible_client_ids) | (
            {principal.client_id} if principal.client_id else set()
        )
        if company_id not in allowed:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    client = db.get(Client, company_id)
    if not client or client.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    return company_out(db, client)


@router.get(
    "/{company_id}/users",
    response_model=CompanyUsersOut,
    summary="Who belongs to this company",
)
def list_company_users(
    company_id: str,
    include_inactive: bool = Query(default=False),
    principal: Principal = Depends(require("role.read")),
    db: Session = Depends(get_leadai_db),
):
    """The company's people, grouped so a details screen can render them directly.

    WHY THIS EXISTS
    `/user-management/employees` returns members of the CALLER's own company,
    resolved through `resolve_scope`. That is the right shape for a company admin
    managing their own team, but it cannot answer "show me who is in company X"
    on a platform-admin company-details screen — there was no endpoint for that.

    Roles are returned grouped as well as flat, because the question a details
    screen asks is "who is the admin here, and who else is there", not "give me
    an unordered list I have to bucket myself".

    Users granted a GLOBAL role (ClientId NULL) are included and flagged: a
    platform admin can act on this company without being a member of it, and
    hiding that from the screen would misrepresent who has access.
    """
    if not principal.is_platform_admin:
        allowed = set(principal.accessible_client_ids) | (
            {principal.client_id} if principal.client_id else set()
        )
        if company_id not in allowed:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    client = db.get(Client, company_id)
    if not client or client.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")

    query = db.query(LeadUserRole).filter(
        LeadUserRole.IsDeleted == False,  # noqa: E712
        or_(
            LeadUserRole.ClientId == company_id,
            LeadUserRole.ClientId.is_(None),   # global admins
        ),
    )
    if not include_inactive:
        query = query.filter(LeadUserRole.IsActive == True)  # noqa: E712

    rows = query.order_by(LeadUserRole.CreatedAt.asc()).all()

    users = [
        CompanyUserOut(
            id=str(row.Id),
            user_id=str(row.UserId) if row.UserId else None,
            email=row.UserEmail,
            name=row.FullName,
            role=row.Role,
            is_active=bool(row.IsActive),
            is_global=row.ClientId is None,
            created_at=row.CreatedAt,
        )
        for row in rows
    ]

    by_role: dict[str, list[CompanyUserOut]] = {}
    for user in users:
        by_role.setdefault(user.role, []).append(user)

    return CompanyUsersOut(
        company_id=company_id,
        company_name=client.Name,
        total=len(users),
        admins=by_role.get(ROLE_COMPANY_ADMIN, []) + by_role.get(ROLE_ADMIN, []),
        managers=by_role.get(ROLE_MANAGER, []),
        employees=by_role.get(ROLE_EMPLOYEE, []),
        users=users,
    )


@router.patch("/{company_id}", response_model=CompanyOut, summary="Update a company")
def update_company(
    company_id: str,
    payload: CompanyUpdate,
    request: Request,
    principal: Principal = Depends(require("company.manage")),
    db: Session = Depends(get_leadai_db),
):
    client = db.get(Client, company_id)
    if not client or client.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")

    changed = {}
    for field, column in (
        ("name", "Name"),
        ("email", "Email"),
        ("phone_number", "PhoneNumber"),
        ("description", "Description"),
        ("is_active", "IsActive"),
    ):
        value = getattr(payload, field)
        if value is not None:
            changed[field] = value
            setattr(client, column, value)
    client.UpdatedBy = principal.email
    client.UpdatedAt = utcnow()

    activity.log_principal(
        db,
        principal,
        action=A.COMPANY_UPDATED,
        client_id=client.Id,
        entity_type="company",
        entity_id=client.Id,
        message=f"Updated company '{client.Name}'",
        meta={"changed_fields": list(changed)},
        request=request,
    )
    db.commit()
    db.refresh(client)
    return company_out(db, client)


@router.delete("/{company_id}", response_model=Ok, summary="Deactivate a company")
def deactivate_company(
    company_id: str,
    request: Request,
    principal: Principal = Depends(require("company.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Soft-deactivate only. Hard deletion is never exposed: a company's
    conversations are business records, and batches in the outbound app may still
    reference the ClientId."""
    client = db.get(Client, company_id)
    if not client or client.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")

    client.IsActive = False
    client.UpdatedBy = principal.email
    client.UpdatedAt = utcnow()

    activity.log_principal(
        db,
        principal,
        action=A.COMPANY_DEACTIVATED,
        client_id=client.Id,
        entity_type="company",
        entity_id=client.Id,
        message=f"Deactivated company '{client.Name}'",
        log_type="Security",
        request=request,
    )
    db.commit()
    return Ok(message=f"Company '{client.Name}' deactivated")


# ===========================================================================
# per-company AI settings
# ===========================================================================


def _settings_row(db: Session, client_id: str, created_by: str) -> LeadCompanySettings:
    row = (
        db.query(LeadCompanySettings)
        .filter(
            LeadCompanySettings.ClientId == client_id,
            LeadCompanySettings.IsDeleted == False,  # noqa: E712
        )
        .one_or_none()
    )
    if row is None:
        row = LeadCompanySettings(ClientId=client_id, CreatedBy=created_by)
        db.add(row)
        db.flush()
    return row


def _settings_out(db: Session, row: LeadCompanySettings) -> CompanySettingsOut:
    threshold, top_k = ai_engine.company_thresholds(db, row.ClientId)
    return CompanySettingsOut(
        client_id=row.ClientId,
        handoff_threshold=row.HandoffThreshold,
        retrieval_top_k=row.RetrievalTopK,
        default_language=row.DefaultLanguage,
        auto_assign_enabled=bool(row.AutoAssignEnabled),
        auto_call_on_hot_lead=bool(row.AutoCallOnHotLead),
        widget_enabled=bool(row.WidgetEnabled),
        widget_greeting=row.WidgetGreeting,
        effective_handoff_threshold=threshold,
        effective_retrieval_top_k=top_k,
    )


@router.get(
    "/{company_id}/settings",
    response_model=CompanySettingsOut,
    summary="Get a company's AI settings",
)
def get_settings(
    company_id: str,
    principal: Principal = Depends(require("company.read")),
    db: Session = Depends(get_leadai_db),
):
    if not principal.is_platform_admin and company_id != principal.client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    row = _settings_row(db, company_id, principal.email)
    db.commit()
    return _settings_out(db, row)


@router.put(
    "/{company_id}/settings",
    response_model=CompanySettingsOut,
    summary="Update a company's AI settings",
)
def update_settings(
    company_id: str,
    payload: CompanySettingsIn,
    request: Request,
    principal: Principal = Depends(require("settings.manage")),
    db: Session = Depends(get_leadai_db),
):
    if not principal.is_platform_admin and company_id != principal.client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    row = _settings_row(db, company_id, principal.email)
    changed = {}
    for field, column in (
        ("handoff_threshold", "HandoffThreshold"),
        ("retrieval_top_k", "RetrievalTopK"),
        ("default_language", "DefaultLanguage"),
        ("auto_assign_enabled", "AutoAssignEnabled"),
        ("auto_call_on_hot_lead", "AutoCallOnHotLead"),
        ("widget_enabled", "WidgetEnabled"),
        ("widget_greeting", "WidgetGreeting"),
    ):
        value = getattr(payload, field)
        if value is not None:
            changed[field] = value
            setattr(row, column, value)
    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()

    activity.log_principal(
        db,
        principal,
        action=A.SETTINGS_UPDATED,
        client_id=company_id,
        entity_type="company_settings",
        entity_id=row.Id,
        message="Updated company AI settings",
        meta=changed,
        request=request,
    )
    db.commit()
    return _settings_out(db, row)


# ===========================================================================
# Company Services / Features Management
# ===========================================================================
@router.get(
    "/{company_id}/services",
    response_model=CompanyServicesOut,
    summary="Get services/features enabled for a company",
)
def get_company_services(
    company_id: str,
    principal: Principal = Depends(require("company.read")),
    db: Session = Depends(get_leadai_db),
):
    if not principal.is_platform_admin:
        allowed = set(principal.accessible_client_ids) | (
            {principal.client_id} if principal.client_id else set()
        )
        if company_id not in allowed:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")

    client = db.get(Client, company_id)
    if not client or client.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")

    existing_rows = (
        db.query(LeadCompanyService)
        .filter(
            LeadCompanyService.ClientId == company_id,
            LeadCompanyService.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadCompanyService.ServiceKey.asc())
        .all()
    )

    items = [
        ServiceItemOut(key=r.ServiceKey, is_enabled=r.IsEnabled)
        for r in existing_rows
    ]

    return CompanyServicesOut(
        company_id=client.Id,
        company_name=client.Name,
        services=items,
    )


@router.patch(
    "/{company_id}/services",
    response_model=CompanyServicesOut,
    summary="Patch/update services/features enabled status for a company (super admin)",
)
def patch_company_services(
    company_id: str,
    payload: CompanyServicesPatchIn,
    request: Request,
    principal: Principal = Depends(require("company.manage")),
    db: Session = Depends(get_leadai_db),
):
    client = db.get(Client, company_id)
    if not client or client.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")

    for item in payload.services:
        s_key = item.key.strip().lower()
        row = (
            db.query(LeadCompanyService)
            .filter(
                LeadCompanyService.ClientId == company_id,
                LeadCompanyService.ServiceKey == s_key,
                LeadCompanyService.IsDeleted == False,  # noqa: E712
            )
            .first()
        )
        if row:
            row.IsEnabled = item.is_enabled
            row.UpdatedBy = principal.email
            row.UpdatedAt = utcnow()
        else:
            db.add(
                LeadCompanyService(
                    ClientId=company_id,
                    ServiceKey=s_key,
                    IsEnabled=item.is_enabled,
                    CreatedBy=principal.email,
                )
            )

    activity.log_principal(
        db,
        principal,
        action=A.COMPANY_UPDATED,
        client_id=company_id,
        entity_type="company_services",
        entity_id=company_id,
        message=f"Patched company services for '{client.Name}'",
        meta={"services_patched": len(payload.services)},
        request=request,
    )
    db.commit()
    return get_company_services(company_id=company_id, principal=principal, db=db)


