"""
Per-request Meta credentials — the primitive that turns this agent from a
single-tenant script into a multi-tenant service.

THE PROBLEM THIS SOLVES
-----------------------
The original agent read `FB_PAGE_ACCESS_TOKEN`, `FB_PAGE_ID` and
`IG_BUSINESS_ACCOUNT_ID` straight from `os.environ`, at call time, deep inside
`graph_api/`. That is fine for one operator posting to one Page. It is wrong
the moment two companies use the same process: every request would post to
whichever Page happened to be in the environment, so Company A's campaign
would appear on Company B's Page.

The fix is NOT to thread a `credentials` argument through all ~40 functions in
`graph_api/` — that would touch every call site, every task, every tool, and
would be easy to forget in one branch (which is exactly the branch that leaks).
Instead the credentials live in a `contextvars.ContextVar`, set once at the
edge (the router, from the authenticated company's `LeadChannelAccount`) and
read at the bottom (`client.py`, `pages.py`, `instagram.py`).

WHY contextvars AND NOT A GLOBAL / threading.local
--------------------------------------------------
The app is async. A single thread interleaves many requests, so a module-level
global or a `threading.local` would be shared across concurrent requests and
would leak credentials between tenants under load — the exact bug we are
preventing. `ContextVar` is copied per-task by asyncio, so a value set inside
one request is invisible to every other, including tasks spawned concurrently.

FAIL-CLOSED
-----------
When `LEADAI_SOCIAL_STRICT_TENANCY` is true (the default), a Graph call with no
active context raises instead of silently falling back to the process-wide
environment variables. Falling back would mean a bug in the wiring shows up as
"posted to the wrong company's Page" rather than as a loud error. The env-var
path is kept only for the standalone CLI/scheduler scripts, which set
`LEADAI_SOCIAL_STRICT_TENANCY=false` because they genuinely are single-tenant.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


class MissingCredentialsError(RuntimeError):
    """No usable Meta credentials for the current company/platform.

    Raised instead of returning None so that a wiring mistake surfaces as a
    clear 4xx/5xx at the API boundary rather than as a confusing Graph API
    error about a malformed token.
    """


@dataclass(frozen=True)
class SocialCredentials:
    """One company's credentials for one Meta surface.

    Frozen because nothing downstream has any business mutating the active
    tenant mid-request.
    """

    client_id: str
    """The owning company (LeadChannelAccount.ClientId). Carried purely so log
    lines and stored post rows can be attributed without a second lookup."""

    account_id: str | None = None
    """LeadChannelAccount.Id — the row these credentials came from."""

    access_token: str = ""
    page_id: str | None = None
    ig_user_id: str | None = None
    app_secret: str | None = None
    api_version: str | None = None
    account_name: str = ""
    graph_base: str | None = None
    """Graph API host override.  Standalone Instagram accounts must use
    graph.instagram.com; Page-linked accounts use graph.facebook.com."""
    meta: dict = field(default_factory=dict)


_current: ContextVar[SocialCredentials | None] = ContextVar(
    "leadai_social_credentials", default=None
)


def _strict() -> bool:
    return os.getenv("LEADAI_SOCIAL_STRICT_TENANCY", "true").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def get_credentials() -> SocialCredentials | None:
    """The credentials bound to the current request/task, if any."""
    return _current.get()


def set_credentials(creds: SocialCredentials):
    """Bind credentials to the current context. Returns the reset token.

    Prefer `use_credentials()` — it cannot be left dangling.
    """
    return _current.set(creds)


def reset_credentials(token) -> None:
    _current.reset(token)


@contextmanager
def use_credentials(creds: SocialCredentials):
    """Scope credentials to a block.

    The `finally` reset matters even though ContextVar values do not escape an
    asyncio task: within ONE task (a single request handler posting to two
    platforms in sequence) the second platform must not inherit the first
    platform's IG id.
    """
    token = _current.set(creds)
    try:
        yield creds
    finally:
        _current.reset(token)


# ---------------------------------------------------------------------------
# Resolvers used by graph_api/. Context first, environment only as an explicit,
# non-default fallback.
# ---------------------------------------------------------------------------
def _resolve(attr: str, env_name: str, label: str) -> str:
    creds = _current.get()
    if creds is not None:
        value = getattr(creds, attr, None)
        if value:
            return value
        raise MissingCredentialsError(
            f"The connected account '{creds.account_name or creds.account_id}' has no "
            f"{label}. Add it under Channels before posting."
        )

    if _strict():
        raise MissingCredentialsError(
            f"No social account is bound to this request, so {label} cannot be resolved. "
            "This endpoint must be called through the LeadAI API so the company's "
            "connected account is loaded. (Set LEADAI_SOCIAL_STRICT_TENANCY=false only "
            "for single-tenant CLI use.)"
        )

    value = os.environ.get(env_name)
    if not value:
        raise MissingCredentialsError(f"{env_name} is not set and no account is bound.")
    return value


def resolve_access_token() -> str:
    return _resolve("access_token", "FB_PAGE_ACCESS_TOKEN", "an access token")


def resolve_page_id() -> str:
    return _resolve("page_id", "FB_PAGE_ID", "a linked Facebook Page id")


def resolve_ig_user_id() -> str:
    return _resolve("ig_user_id", "IG_BUSINESS_ACCOUNT_ID", "a linked Instagram account id")


def resolve_api_version(default: str) -> str:
    creds = _current.get()
    if creds is not None and creds.api_version:
        return creds.api_version
    return os.environ.get("GRAPH_API_VERSION", default)


def resolve_graph_base() -> str:
    """Return the Graph API host for the current credentials.

    Standalone Instagram accounts (LoginType='instagram') must use
    graph.instagram.com; Page-linked accounts use graph.facebook.com.
    Falls back to graph.facebook.com for single-tenant CLI use.
    """
    creds = _current.get()
    if creds is not None and creds.graph_base:
        return creds.graph_base
    return os.environ.get("GRAPH_BASE_URL", "https://graph.facebook.com")
