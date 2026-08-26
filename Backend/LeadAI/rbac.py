"""
Role-based access control for LeadAI.

Authentication is NOT re-implemented here. The outbound app already validates
every request against the identity server in TokenValidationMiddleware and
stashes the claims on `request.state.identity`; `auth.get_current_user` turns
that into an email. This module answers the next question only: *what may this
identity do, and to which company's data?*

Two independent gates, both required:

  1. PERMISSION  — does the caller's role include this capability?
  2. TENANT SCOPE — is the ClientId they are touching one they may touch?

Tenant scope is resolved in exactly one place (`resolve_scope`). Routers never
read ClientId from the request body for a company-scoped user, which is what
makes cross-company leakage structurally impossible rather than merely absent.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Iterable

from fastapi import Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from auth import get_current_user

from .db import get_leadai_db
from .models import (
    ROLE_EMPLOYEE,
    ROLE_COMPANY_ADMIN,
    ROLE_MANAGER,
    ROLE_ADMIN,
    LeadRolePermission,
    LeadUserRole,
)

logger = logging.getLogger(__name__)


# ===========================================================================
# Permission catalogue
# ===========================================================================
# Named capabilities, so a route says what it needs rather than which roles are
# allowed. Adding a role later means editing one table, not every route.

P = {
    # companies
    "company.read": "View companies",
    "company.manage": "Create / update / deactivate companies",
    # RBAC
    "role.read": "View role assignments",
    "role.manage": "Grant / revoke roles",
    # knowledge base
    "kb.read": "List knowledge documents",
    "kb.manage": "Upload / delete knowledge documents, re-index",
    "kb.test": "Run retrieval tests against the knowledge base",
    # scripts + prompts
    "script.read": "View conversation scripts",
    "script.manage": "Create / edit / activate scripts",
    "prompt.read": "View prompts",
    "prompt.manage": "Edit prompts",
    "settings.manage": "Edit company AI settings",
    # inbox / leads
    "lead.read.all": "See every conversation in the company",
    "lead.read.assigned": "See only conversations assigned to me",
    "lead.reply": "Send a message into a conversation",
    "lead.assign": "Assign a conversation to a user",
    "lead.status": "Change conversation status",
    "lead.reveal_pii": "Reveal a customer's real contact details",
    "lead.export": "Export leads",
    # voice
    "call.initiate": "Place an outbound call for a lead",
    "call.read": "View call history for a lead",
    # analytics + audit
    "analytics.read": "View dashboards",
    "activity.read": "Read the activity log",
    # --- Phase 2 -----------------------------------------------------------
    # social channels
    "channel.read": "View connected WhatsApp / Facebook / Instagram accounts",
    "channel.manage": "Connect, edit and disconnect social channels",
    # campaigns
    "campaign.read": "View campaigns and their results",
    "campaign.manage": "Create and edit campaigns and contact lists",
    # Separate from campaign.manage on purpose: building a 30,000-recipient
    # campaign and actually SENDING it are different levels of authority.
    "campaign.send": "Start, pause, cancel and retry a campaign",
    # customers (CRM)
    "customer.read": "View customers assigned to me",
    "customer.read.all": "View every customer in the company",
    "customer.manage": "Create, edit, convert and delete customers",
    # document store
    "file.read": "List and download stored documents",
    "file.manage": "Upload and delete stored documents",
    # social publishing — posting to the company's own connected Page/IG account.
    # Split from channel.* on purpose: connecting an account is an IT/admin act,
    # whereas publishing to it is a day-to-day marketing act, and plenty of
    # companies want the second without granting the first (a marketer who can
    # post but cannot rotate the access token).
    "social.read": "View connected publishing targets and post history",
    "social.post": "Publish posts and AI replies to connected social accounts",
    "social.manage": "Delete published posts",
    # Platform-specific permissions
    "social.facebook": "Access Facebook publishing, webhooks, and pages",
    "social.instagram": "Access Instagram publishing, webhooks, and media",
    "social.whatsapp": "Access WhatsApp channel and messaging",
    "social.linkedin": "Access LinkedIn publishing",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    ROLE_ADMIN: set(P),  # everything, across all companies
    ROLE_COMPANY_ADMIN: {
        "company.read",
        "role.read",
        "role.manage",
        "kb.read",
        "kb.manage",
        "kb.test",
        "script.read",
        "script.manage",
        "prompt.read",
        "prompt.manage",
        "settings.manage",
        "lead.read.all",
        "lead.reply",
        "lead.assign",
        "lead.status",
        "lead.reveal_pii",
        "lead.export",
        "call.initiate",
        "call.read",
        "analytics.read",
        "activity.read",
        "channel.read",
        "channel.manage",
        "campaign.read",
        "campaign.manage",
        "campaign.send",
        "customer.read",
        "customer.read.all",
        "customer.manage",
        "file.read",
        "file.manage",
        "social.read",
        "social.post",
        "social.manage",
    },
    ROLE_MANAGER: {
        "company.read",
        "role.read",
        "kb.read",
        "kb.test",
        "script.read",
        "script.manage",
        "prompt.read",
        "prompt.manage",
        "lead.read.all",
        "lead.reply",
        "lead.assign",
        "lead.status",
        "lead.export",
        "call.initiate",
        "call.read",
        "analytics.read",
        "activity.read",
        "channel.read",
        "campaign.read",
        "campaign.manage",
        "campaign.send",
        "customer.read",
        "customer.read.all",
        "customer.manage",
        "file.read",
        "file.manage",
        "social.read",
        "social.post",
    },
    ROLE_EMPLOYEE: {
        "company.read",
        "kb.read",
        "script.read",
        "lead.read.assigned",
        "lead.reply",
        "lead.status",
        "call.initiate",
        "call.read",
        "analytics.read",
        # An employee may see channels and campaigns, and work their own
        # customers, but may not connect a channel or fire a bulk send.
        "channel.read",
        "campaign.read",
        "customer.read",
        "file.read",
        # Sees what went out, but publishing to the company's public accounts
        # needs manager or above — same reasoning as campaign.send.
        "social.read",
    },
}


def permissions_for(role: str) -> set[str]:
    """Return the hardcoded default permissions for a role (no DB query)."""
    return set(ROLE_PERMISSIONS.get(role, set()))


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


def effective_permissions_for(db: Session, role: str) -> set[str]:
    """Compute effective permissions: hardcoded defaults + database overrides."""
    perms = set(ROLE_PERMISSIONS.get(role, set()))
    overrides = _db_overrides(db, role)
    for key, granted in overrides.items():
        if granted:
            perms.add(key)
        else:
            perms.discard(key)
    return perms


# ===========================================================================
# Principal
# ===========================================================================


@dataclass
class Principal:
    """The authenticated caller plus their resolved authority."""

    email: str
    role: str
    client_id: str | None                     # None => all companies (platform admin)
    full_name: str | None = None
    user_id: str | None = None
    permissions: set[str] = field(default_factory=set)
    # Every company this identity holds a grant for, for the company switcher.
    accessible_client_ids: list[str] = field(default_factory=list)

    @property
    def is_platform_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def sees_only_assigned(self) -> bool:
        """Agents/viewers are restricted to conversations assigned to them."""
        return "lead.read.all" not in self.permissions

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, permission: str) -> None:
        if not self.can(permission):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Your role ({self.role}) does not allow this action "
                    f"({P.get(permission, permission)})."
                ),
            )


# ===========================================================================
# Bootstrap
# ===========================================================================
# A brand-new deployment has an empty leadai_user_roles table, which would lock
# everyone out. LEADAI_BOOTSTRAP_ADMINS (comma-separated emails) are treated as
# platform admins; the first time one of them calls the API their grant row is
# written, so the bootstrap list can be removed afterwards.

import os

_BOOTSTRAP = {
    e.strip().lower()
    for e in os.getenv("LEADAI_BOOTSTRAP_ADMINS", "").split(",")
    if e.strip()
}


def _bootstrap_if_needed(db: Session, email: str) -> LeadUserRole | None:
    if email.lower() not in _BOOTSTRAP:
        return None
    row = LeadUserRole(
        UserEmail=email.lower(),
        Role=ROLE_ADMIN,
        ClientId=None,
        IsActive=True,
        CreatedBy="bootstrap",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.warning("[LeadAI RBAC] bootstrapped Admin for %s", email)
    return row


# ===========================================================================
# Dependencies
# ===========================================================================


def _grants(db: Session, email: str) -> list[LeadUserRole]:
    return (
        db.query(LeadUserRole)
        .filter(
            LeadUserRole.UserEmail == email.lower(),
            LeadUserRole.IsActive == True,   # noqa: E712
            LeadUserRole.IsDeleted == False,  # noqa: E712
        )
        .all()
    )


def current_principal(
    request: Request,
    client_id: str | None = Query(
        default=None,
        alias="client_id",
        description="Company to act on. Required for platform admins; ignored "
                    "for company-scoped users (their own company is used).",
    ),
    email: str = Depends(get_current_user),
    db: Session = Depends(get_leadai_db),
) -> Principal:
    """Resolve the caller into a Principal with a locked-in company scope."""
    grants = _grants(db, email)
    if not grants:
        created = _bootstrap_if_needed(db, email)
        if created:
            grants = [created]
    if not grants:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=(
                "You are authenticated but have no LeadAI role yet. "
                "Ask a platform admin to grant you access."
            ),
        )

    platform = next((g for g in grants if g.Role == ROLE_ADMIN), None)
    accessible = [g.ClientId for g in grants if g.ClientId]

    if platform:
        # Platform admin: may target any company, but must name one for
        # company-scoped endpoints. resolve_scope() enforces that per-route.
        return Principal(
            email=email,
            role=ROLE_ADMIN,
            client_id=client_id,
            full_name=platform.FullName,
            user_id=platform.UserId,
            permissions=effective_permissions_for(db, ROLE_ADMIN),
            accessible_client_ids=accessible,
        )

    # Company-scoped. If the caller holds grants in several companies they may
    # select one with ?client_id=, but only from their own set.
    chosen = None
    if client_id:
        chosen = next((g for g in grants if g.ClientId == client_id), None)
        if not chosen:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="You do not have access to that company.",
            )
    else:
        # Highest-authority grant wins when no company is named.
        order = [ROLE_COMPANY_ADMIN, ROLE_MANAGER, ROLE_EMPLOYEE]
        chosen = sorted(
            grants,
            key=lambda g: order.index(g.Role) if g.Role in order else 99,
        )[0]

    return Principal(
        email=email,
        role=chosen.Role,
        client_id=chosen.ClientId,
        full_name=chosen.FullName,
        user_id=chosen.UserId,
        permissions=effective_permissions_for(db, chosen.Role),
        accessible_client_ids=accessible,
    )


def require(*permissions: str) -> Callable[..., Principal]:
    """Route guard: `principal: Principal = Depends(require("kb.manage"))`.

    Passing several permissions means ANY of them is sufficient — used where a
    manager and an agent reach the same route through different capabilities
    (e.g. lead.read.all vs lead.read.assigned).
    """

    def guard(principal: Principal = Depends(current_principal)) -> Principal:
        if not any(principal.can(p) for p in permissions):
            names = ", ".join(P.get(p, p) for p in permissions)
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail=f"Your role ({principal.role}) does not allow this action ({names}).",
            )
        return principal

    return guard


def resolve_scope(principal: Principal) -> str:
    """Return the ClientId this request operates on, or 403/400.

    This is the ONLY place a company id is chosen. Call it at the top of every
    company-scoped handler.
    """
    if principal.client_id:
        return principal.client_id
    if principal.is_platform_admin:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Platform admins must specify which company to act on (?client_id=...).",
        )
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        detail="No company is associated with your account.",
    )


def scoped(*permissions: str) -> Callable[..., tuple[Principal, str]]:
    """Convenience guard returning (principal, client_id) in one dependency."""

    def guard(principal: Principal = Depends(require(*permissions))) -> tuple[Principal, str]:
        return principal, resolve_scope(principal)

    return guard


def assert_owns(row_client_id: str | None, client_id: str) -> None:
    """Last line of defence: a row fetched by primary key must belong to the
    resolved company. Raises 404 (not 403) so an id in another company is
    indistinguishable from one that does not exist."""
    if row_client_id != client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")


def visible_roles(principal: Principal) -> Iterable[str]:
    """Which roles this principal is allowed to grant."""
    if principal.is_platform_admin:
        return (ROLE_ADMIN, ROLE_COMPANY_ADMIN, ROLE_MANAGER, ROLE_EMPLOYEE)
    if principal.role == ROLE_COMPANY_ADMIN:
        return (ROLE_MANAGER, ROLE_EMPLOYEE)
    return ()
