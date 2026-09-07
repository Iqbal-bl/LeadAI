"""
Aggregates every LeadAI router under one prefix.

Route ordering matters in two places:

1. `chat.router` and `webhooks.router` both sit under `/public`, which the
   integration hook marks as exempt from the identity-server middleware. They
   are registered first so it is obvious which surface is externally reachable.

2. `voice.router` is imported LAST because it pulls in the call bridge, which
   imports multiligual_call. Keeping it late avoids any chance of a circular
   import during app construction.
"""
from fastapi import APIRouter

from .routers import (
    activity,
    analytics,
    billing,
    campaigns,
    channels,
    chat,
    companies,
    customers,
    files,
    inbox,
    knowledge,
    linkedin,
    role_permissions,
    roles,
    scripts,
    social,
    social_drafts,
    user_management,
    webhooks,
)

api_router = APIRouter()

# ---------------------------------------------------------------------------
# Public, unauthenticated surface.
#   chat     - the web widget (kept: it is the fallback while Meta app review
#              is pending, and remains the right channel for a website visitor)
#   webhooks - WhatsApp / Messenger / Instagram inbound, authenticated by HMAC
#              signature rather than by a user token
# ---------------------------------------------------------------------------
api_router.include_router(chat.router)
api_router.include_router(webhooks.router)

# ---------------------------------------------------------------------------
# Staff surface - identity-server token AND a LeadAI role grant required.
# ---------------------------------------------------------------------------
api_router.include_router(roles.router)
api_router.include_router(role_permissions.router)
api_router.include_router(companies.router)
api_router.include_router(knowledge.router)
api_router.include_router(scripts.router)
api_router.include_router(scripts.prompts_router)
api_router.include_router(inbox.router)
api_router.include_router(activity.router)
api_router.include_router(analytics.router)
api_router.include_router(user_management.router)
api_router.include_router(billing.router)
api_router.include_router(billing.admin_router)


# Phase 2: social channels, campaigns, CRM, document store, lead threshold.
api_router.include_router(channels.router)
api_router.include_router(linkedin.router)
api_router.include_router(campaigns.lists_router)
api_router.include_router(campaigns.router)
api_router.include_router(customers.router)
api_router.include_router(files.router)
api_router.include_router(files.threshold_router)

# Phase 3: social publishing. Registered after channels because it is the same
# connected accounts being used for a different purpose — a company connects a
# Page once under /channels and can then both converse through it and publish
# to it. Nothing here works until an account exists there.
api_router.include_router(social.router)
api_router.include_router(social_drafts.router)

# Voice last - see the note at the top of this file.
from .routers import voice  # noqa: E402

api_router.include_router(voice.router)

__all__ = ["api_router"]
