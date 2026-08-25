"""
Thin async client for Meta's Graph API. Every other module in graph_api/
(pages.py, instagram.py, leads.py) calls through graph_get()/graph_post()
here — same "one integration point" pattern as crag/engine.py.

Auth model: a Page Access Token authorizes calls made "as" that Page (and,
via its linked Instagram Business account, Instagram calls too). Get one
by:
    1. Log in via Facebook Login / Graph API Explorer as a Page admin,
       requesting whatever permissions you need (pages_manage_posts,
       instagram_content_publish, leads_retrieval, etc.)
    2. Exchange the short-lived user token for a long-lived one (60 days):
       GET /oauth/access_token?grant_type=fb_exchange_token&client_id=...
           &client_secret=...&fb_exchange_token=SHORT_LIVED_TOKEN
    3. GET /me/accounts with that long-lived user token — returns each
       Page you admin along with a Page Access Token that does NOT expire
       as long as the user token backing it stays valid.
Put the resulting Page Access Token in .env as FB_PAGE_ACCESS_TOKEN.
"""

import os

import httpx

from social_agent.context import resolve_access_token, resolve_api_version, resolve_graph_base

GRAPH_API_VERSION = "v25.0"  # v21.0 was confirmed deprecated by Meta's own
# response headers (2026-07-31) — they were auto-upgrading calls to v25.0
# anyway, but relying on that silent fallback isn't something to depend on
# long-term. Check https://developers.facebook.com/docs/graph-api/changelog
# periodically — Meta deprecates versions roughly every 2 years.


def _base_url() -> str:
    """Resolved per call, not once at import.

    A connected account carries its own ApiVersion (companies onboard at
    different times and Meta deprecates versions on a rolling schedule), so the
    base URL cannot be a module constant shared by every tenant.

    Standalone Instagram accounts (LoginType='instagram') must use
    graph.instagram.com; Page-linked accounts use graph.facebook.com.
    """
    return f"{resolve_graph_base()}/{resolve_api_version(GRAPH_API_VERSION)}"


# Kept for any caller that still imports it; per-request code uses _base_url().
BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# httpx's default timeout is only 5s total (connect+read+write+pool) — too
# aggressive for Meta's API, which can occasionally take longer on the
# first connection (TLS handshake) or under normal load. 30s gives real
# requests room to complete instead of getting killed as false failures.
_TIMEOUT = httpx.Timeout(30.0)


class GraphAPIError(Exception):
    """Raised with Meta's own error message extracted from the response
    body, instead of a generic HTTP status — Meta's error payloads (code,
    message, error_subcode, fbtrace_id) are far more actionable than
    '400 Bad Request' alone."""


def _access_token() -> str:
    """The access token of the company bound to this request.

    Delegates to social_agent.context, which reads the credentials the API
    layer loaded from that company's LeadChannelAccount row. The old
    environment-variable lookup now lives behind that same resolver as an
    opt-in single-tenant fallback.
    """
    from social_agent.context import MissingCredentialsError

    try:
        return resolve_access_token()
    except MissingCredentialsError as exc:
        # Re-raised as GraphAPIError so every existing `except GraphAPIError`
        # around the codebase keeps catching credential problems as before.
        raise GraphAPIError(str(exc)) from exc


async def graph_get(path: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    params["access_token"] = _access_token()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(f"{_base_url()}/{path.lstrip('/')}", params=params)
    return _unwrap(resp)


async def graph_post(path: str, data: dict | None = None, files: dict | None = None) -> dict:
    data = dict(data or {})
    data["access_token"] = _access_token()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(f"{_base_url()}/{path.lstrip('/')}", data=data, files=files)
    return _unwrap(resp)


async def graph_delete(path: str) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.delete(
            f"{_base_url()}/{path.lstrip('/')}", params={"access_token": _access_token()}
        )
    return _unwrap(resp)


def _unwrap(resp: httpx.Response) -> dict:
    body = resp.json()
    if resp.status_code >= 400 or "error" in body:
        err = body.get("error", {})
        raise GraphAPIError(
            f"Graph API error {err.get('code')}: {err.get('message')} "
            f"(subcode: {err.get('error_subcode')}, "
            f"fbtrace_id: {err.get('fbtrace_id')})"
        )
    return body
