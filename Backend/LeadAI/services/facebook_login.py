"""
Facebook Login for Business — connect a company's Facebook Page (Messenger)
and, when one is linked, the Instagram professional account behind it.

WHY THIS IS NOT JUST instagram_login.py WITH DIFFERENT URLS
-----------------------------------------------------------
Instagram Login authenticates an ACCOUNT: one consent screen, one token, one
channel, done. Facebook Login authenticates a PERSON, who may administer zero,
one, or fifteen Pages. So the flow has a step Instagram's does not have — after
the token exchange you must list the Pages and let the company choose. A
callback that silently grabs `pages[0]` will, sooner or later, connect the
wrong Page to a paying customer and route their leads into a Page they forgot
they owned.

That is why connecting is split across two endpoints in routers/channels.py:

    POST /channels/facebook/callback   code -> user token -> LIST of pages
    POST /channels/facebook/select     chosen page id  -> account row(s)

THE TOKEN LIFECYCLE IS THE OPPOSITE OF INSTAGRAM'S
---------------------------------------------------
    authorization code       ->  short-lived USER token   (~1 hour)
    short-lived USER token   ->  long-lived USER token    (~60 days)
    long-lived USER token    ->  PAGE token               (NO EXPIRY)

A Page token derived from a LONG-LIVED user token does not expire. This is the
one place Facebook is easier than Instagram: there is no refresh job, no 60-day
cliff, no `refresh_due()` to write. But it only holds if the user token was
long-lived FIRST — derive a Page token from the short-lived one and you get a
one-hour token that dies overnight and looks like a random auth failure the
next morning. `complete_connection()` therefore always exchanges before it
lists, and never skips that step "because it worked in testing".

ONE PAGE CAN PRODUCE TWO CHANNELS
----------------------------------
If the Page has a linked Instagram professional account, `list_pages()` returns
it under `instagram`. The caller should create TWO LeadChannelAccount rows from
one connection — channel `messenger` keyed on the Page id, and channel
`instagram` keyed on the IG business account id — both holding the same Page
token and both with LoginType `facebook`.

This is how a Page-linked Instagram account differs from a standalone one:

                        LoginType=facebook        LoginType=instagram
    host                graph.facebook.com        graph.instagram.com
    token               Page token (EAA...)       IG user token (IGAA...)
    app secret          META_APP_SECRET           INSTAGRAM_APP_SECRET
    expires             never                     60 days
    webhook object      page / instagram          instagram
    needs a Page        yes                       no

Both arrive at the same webhook endpoint, both may carry `object: instagram`,
and they are signed with DIFFERENT app secrets. `channels.app_secret_for()`
resolves per account, which is the only reason that works.

WEBHOOKS: THE FAILURE THAT LOOKS LIKE SUCCESS
----------------------------------------------
`subscribe_page()` subscribes the PAGE to the app. Separately, the APP must
have `object: "page"` registered with a callback URL in the App Dashboard.
Those are independent registrations, and Meta lets the first succeed while the
second is missing — `subscribed_apps` returns 200 and not one webhook is ever
delivered. Check the app side with:

    GET /<META_APP_ID>/subscriptions?access_token=<APP_ID>|<APP_SECRET>

and look for an entry with object `page`. `{"data":[]}` means no Messenger
message will ever reach you, however healthy the account looks in your UI.

APP REVIEW
----------
`pages_messaging` needs Advanced Access for Pages the app does not own, which
means full App Review. Standard Access covers Pages administered by people with
a role on the app — enough to build and demo, not enough to onboard a customer.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from ..config import settings

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# hosts
# --------------------------------------------------------------------------- #
# The consent dialog lives on www.facebook.com; every API call goes to
# graph.facebook.com. Unlike the Instagram hosts these are safe to read from
# config, because META_GRAPH_BASE is already the Facebook host everywhere else.
FB_AUTH_HOST = "https://www.facebook.com"

LOGIN_TYPE_FACEBOOK = "facebook"

# Scopes.
#   pages_show_list        — enumerate the Pages this person administers
#   pages_messaging        — send and receive Messenger messages
#   pages_manage_metadata  — required to call /<page>/subscribed_apps
#   business_management    — Pages owned by a Business rather than a person
SCOPES_MESSAGING = (
    "pages_show_list",
    "pages_messaging",
    "pages_manage_metadata",
    "business_management",
)
# Only requested when the caller wants the Page's linked Instagram too. Asking
# for Instagram scopes when the company has no linked IG account makes the
# consent screen longer and scarier for no benefit.
SCOPES_INSTAGRAM = (
    "instagram_basic",
    "instagram_manage_messages",
)


class FacebookLoginError(RuntimeError):
    """Raised with a message safe to show an operator."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _graph(path: str) -> str:
    base = settings.meta_graph_base.rstrip("/")
    return f"{base}/{settings.meta_graph_version}/{path.lstrip('/')}"


def is_configured() -> bool:
    return bool(settings.meta_app_id and settings.meta_app_secret)


def _require_config() -> tuple[str, str, str]:
    if not is_configured():
        raise FacebookLoginError(
            "Facebook login is not configured. Set META_APP_ID, META_APP_SECRET and "
            "FACEBOOK_REDIRECT_URI from a Meta app with Facebook Login for Business. "
            "These are NOT the Instagram App ID and Instagram App Secret — an app "
            "with the Instagram Login setup has both pairs and they are not "
            "interchangeable."
        )
    if not settings.facebook_redirect_uri:
        raise FacebookLoginError(
            "FACEBOOK_REDIRECT_URI is not set. It must match a Valid OAuth Redirect "
            "URI in the Meta App Dashboard exactly, including scheme and trailing slash."
        )
    return settings.meta_app_id, settings.meta_app_secret, settings.facebook_redirect_uri


# =========================================================================== #
# step 1 — send the company to Facebook
# =========================================================================== #
def authorize_url(state: str | None = None, *, with_instagram: bool = True) -> tuple[str, str]:
    """Build the consent URL. Returns (url, state).

    `state` is CSRF protection and is echoed back to the callback. Store it
    server-side against the company and reject any state you did not issue —
    without that check, anyone who can reach the callback can attach a Page of
    their choosing to a tenant.
    """
    app_id, _, redirect_uri = _require_config()
    state = state or secrets.token_urlsafe(24)

    scopes = list(SCOPES_MESSAGING)
    if with_instagram:
        scopes += list(SCOPES_INSTAGRAM)

    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": ",".join(scopes),
        "state": state,
    }
    url = f"{FB_AUTH_HOST}/{settings.meta_graph_version}/dialog/oauth?{urlencode(params)}"
    logger.info("[LeadAI fb-login] authorize url built, scopes=%s", ",".join(scopes))
    return url, state


# =========================================================================== #
# step 2 — code -> short-lived user token
# =========================================================================== #
def exchange_code(code: str, *, timeout: float = 20.0) -> dict:
    """Swap the authorization code for a short-lived user token.

    Note this is a GET with query params, where Instagram's equivalent is a
    POST with a form body. Copying the Instagram call shape here returns a
    confusing 400.

    "This authorization code has been used" almost always means the callback
    ran twice — a browser retry or a double-clicked button — not a bad code.
    """
    app_id, app_secret, redirect_uri = _require_config()

    import httpx

    try:
        response = httpx.get(
            _graph("oauth/access_token"),
            params={
                "client_id": app_id,
                "client_secret": app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        raise FacebookLoginError(f"Could not reach Facebook: {exc}") from exc

    if response.status_code >= 400:
        raise FacebookLoginError(
            f"Facebook rejected the authorization code ({response.status_code}): "
            f"{response.text[:300]}"
        )

    data = response.json() or {}
    token = data.get("access_token")
    if not token:
        raise FacebookLoginError(f"Facebook returned no access token: {str(data)[:300]}")
    logger.info("[LeadAI fb-login] short-lived user token obtained")
    return data


# =========================================================================== #
# step 3 — short-lived -> long-lived user token
# =========================================================================== #
def exchange_long_lived(short_token: str, *, timeout: float = 20.0) -> dict:
    """Upgrade to a ~60 day user token.

    MUST happen before Pages are listed. Page tokens inherit their lifetime
    from the user token they were derived from: from a long-lived one they
    never expire, from a short-lived one they die in about an hour. Skipping
    this step produces a connection that works during testing and silently
    breaks the next day.

    Note `grant_type=fb_exchange_token` — Instagram's is `ig_exchange_token`.
    """
    app_id, app_secret, _ = _require_config()

    import httpx

    try:
        response = httpx.get(
            _graph("oauth/access_token"),
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": short_token,
            },
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        raise FacebookLoginError(f"Could not reach Facebook: {exc}") from exc

    if response.status_code >= 400:
        raise FacebookLoginError(
            f"Facebook refused the long-lived exchange ({response.status_code}): "
            f"{response.text[:300]}"
        )

    data = response.json() or {}
    if not data.get("access_token"):
        raise FacebookLoginError("Facebook returned no long-lived token.")
    logger.info(
        "[LeadAI fb-login] long-lived user token obtained, expires_in=%s",
        data.get("expires_in"),
    )
    return data


# =========================================================================== #
# step 4 — list the Pages this person administers
# =========================================================================== #
def list_pages(user_token: str, *, timeout: float = 20.0) -> list[dict]:
    """Return the Pages, each with its own token and any linked IG account.

    Returns [{page_id, name, access_token, category, instagram: {...} | None}].

    An empty list is the single most common support ticket in this flow, and it
    is never a bug in this function. It means the person has no Page role, or
    they unticked the Page on the consent screen, or the Page is owned by a
    Business the app has no access to. Say that in the UI rather than showing an
    empty picker.
    """
    import httpx

    try:
        response = httpx.get(
            _graph("me/accounts"),
            params={
                "fields": "id,name,category,access_token,"
                          "instagram_business_account{id,username,name,profile_picture_url}",
                "access_token": user_token,
                "limit": 100,
            },
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        raise FacebookLoginError(f"Could not reach Facebook: {exc}") from exc

    if response.status_code >= 400:
        raise FacebookLoginError(
            f"Could not list Pages ({response.status_code}): {response.text[:300]}"
        )

    pages: list[dict] = []
    for row in (response.json() or {}).get("data", []):
        ig = row.get("instagram_business_account") or None
        pages.append(
            {
                "page_id": str(row.get("id")),
                "name": row.get("name"),
                "category": row.get("category"),
                "access_token": row.get("access_token"),
                "instagram": (
                    {
                        "id": str(ig.get("id")),
                        "username": ig.get("username"),
                        "name": ig.get("name"),
                        "profile_picture_url": ig.get("profile_picture_url"),
                    }
                    if ig
                    else None
                ),
            }
        )
    logger.info("[LeadAI fb-login] %d page(s) available", len(pages))
    return pages


# =========================================================================== #
# step 5 — subscribe the Page to this app's webhooks
# =========================================================================== #
SUBSCRIBE_FIELDS = (
    "messages",
    "messaging_postbacks",
    "messaging_optins",
    "message_reactions",
    "messaging_referrals",   # SINGULAR. Meta's field name has no trailing s.
)


def subscribe_page(page_id: str, page_token: str, *, timeout: float = 20.0) -> list[str]:
    """Subscribe the Page, then VERIFY the subscription actually took.

    The POST returning 200 does not mean the subscription exists — that
    combination is exactly how a channel ends up looking connected in the UI
    while receiving nothing forever. The GET below is what turns a silent
    failure into a visible one, so do not remove it to save a round trip.
    """
    import httpx

    try:
        httpx.post(
            _graph(f"{page_id}/subscribed_apps"),
            params={
                "subscribed_fields": ",".join(SUBSCRIBE_FIELDS),
                "access_token": page_token,
            },
            timeout=timeout,
        )
        check = httpx.get(
            _graph(f"{page_id}/subscribed_apps"),
            params={"access_token": page_token},
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        raise FacebookLoginError(f"Could not subscribe the Page to webhooks: {exc}") from exc

    fields: list[str] = []
    for row in (check.json() or {}).get("data", []):
        fields.extend(row.get("subscribed_fields") or [])

    if "messages" not in fields:
        raise FacebookLoginError(
            "The Page was connected but is NOT subscribed to message webhooks, so no "
            "Messenger message will reach LeadAI. This is usually a missing "
            "pages_manage_metadata grant, or the app is missing Advanced Access for "
            "pages_messaging."
        )
    logger.info("[LeadAI fb-login] page %s subscribed to: %s", page_id, ",".join(fields))
    return fields


# =========================================================================== #
# orchestration
# =========================================================================== #
def complete_connection(code: str) -> dict:
    """code -> long-lived user token -> the Pages the company can choose from.

    Deliberately stops at the list. Picking a Page is a decision for the
    company, not for this function; see the module docstring.
    """
    short = exchange_code(code)
    long_lived = exchange_long_lived(short["access_token"])

    expires_at = None
    if long_lived.get("expires_in"):
        expires_at = _now() + timedelta(seconds=int(long_lived["expires_in"]))

    pages = list_pages(long_lived["access_token"])
    if not pages:
        raise FacebookLoginError(
            "No Facebook Pages were returned. The person who authorised must have an "
            "admin role on at least one Page, and must tick that Page on the consent "
            "screen — Facebook shows the Page list as opt-in checkboxes and unticking "
            "them is easy to do by accident."
        )

    return {
        "user_access_token": long_lived["access_token"],
        "user_token_expires_at": expires_at,
        "pages": pages,
    }