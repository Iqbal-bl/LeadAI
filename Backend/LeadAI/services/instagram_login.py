"""
Business Login for Instagram — connect a standalone Instagram professional
account with NO linked Facebook Page.

WHY THIS FILE EXISTS
--------------------
Meta ships two different Instagram integrations, and they are not variants of
one flow — they are separate products with separate hosts, separate app
credentials, separate scopes and separate token lifecycles:

  1. "Instagram API with Facebook Login"  (what this codebase had)
     Requires the IG professional account to be linked to a Facebook Page.
     Auth goes through Facebook, you receive a PAGE access token, and every
     call goes to graph.facebook.com. This is why Instagram only worked for you
     when it was connected to a Page.

  2. "Instagram API with Instagram Login"  (this file)
     Launched 23 July 2024. The account authenticates directly with Instagram.
     No Facebook Page anywhere. You receive an INSTAGRAM USER access token, and
     every call goes to graph.instagram.com.

THE CONSTRAINT THAT DECIDES YOUR SETUP
--------------------------------------
A single Meta app can only have ONE of these two setups. Meta's own
documentation is explicit: "You can only add one setup per app. If you want to
implement both setups, create an app for each setup."

So this is NOT a matter of adding a scope to your existing app. You need a
SECOND Meta app configured with "API setup with Instagram Login", which issues
its own Instagram App ID and Instagram App Secret — different values from the
Facebook app id/secret you have in the database today.

That is why `LeadChannelAccount` now carries `LoginType`, `AppId` and its own
`AppSecretEnc`: one company can have a Page-linked Instagram account connected
through the old app AND a standalone Instagram account connected through the
new one, and each has to be signed, addressed and refreshed differently.

THE OTHER THING THAT SILENTLY BREAKS
------------------------------------
Instagram User access tokens are not valid at graph.facebook.com. If you send
one there the error is "Invalid OAuth access token - Cannot parse access
token", which reads like a bad token rather than a wrong host and costs people
hours. `_graph_base()` in channels.py now routes per account so this cannot
happen by accident.

TOKEN LIFECYCLE — THE PART THAT WILL PAGE YOU AT 3AM
----------------------------------------------------
    authorization code  ->  short-lived token   (1 hour)
    short-lived token   ->  long-lived token    (60 days)
    long-lived token    ->  refreshed token     (another 60 days)

A long-lived token can only be refreshed if it is **at least 24 hours old and
has not yet expired**. Miss the window and there is no recovery path: the
company must go through the whole consent flow again. Refresh at day ~50, not
day 59. `LeadChannelAccount.TokenExpiresAt` exists so `refresh_due()` can find
them before that happens.

ACCOUNT REQUIREMENTS
--------------------
The Instagram account must be a **professional** account (Business or Creator).
Personal accounts cannot use this API at all — the Basic Display API that once
served them was sunset in December 2024 and has no replacement. Converting is
free: Instagram app -> Settings -> Account type and tools -> Switch to
professional account.

APP REVIEW
----------
Standard Access covers accounts you own or have added to your app in the App
Dashboard — enough to build and demo. Serving other companies' Instagram
accounts needs Advanced Access, which means full Meta App Review.
`instagram_business_manage_messages` is one of the slower permissions to clear.
Start that review before you need it.
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
# Deliberately hard-coded rather than read from META_GRAPH_BASE. These are
# Instagram-Login-specific hosts; pointing them at graph.facebook.com by
# misconfiguration produces the "Cannot parse access token" failure described
# above, and making that a configurable mistake helps nobody.
IG_AUTH_HOST = "https://www.instagram.com"
IG_OAUTH_HOST = "https://api.instagram.com"
IG_GRAPH_HOST = "https://graph.instagram.com"

LOGIN_TYPE_FACEBOOK = "facebook"     # legacy: IG linked to a Page
LOGIN_TYPE_INSTAGRAM = "instagram"   # standalone IG professional account

# Scope names as of the January 2025 rename. The older `business_basic`,
# `business_manage_messages` etc. were deprecated on 27 January 2025 — if you
# find those in a tutorial, it predates the change.
SCOPES_MESSAGING = (
    "instagram_business_basic",
    "instagram_business_manage_messages",
    "instagram_business_manage_comments",
)
SCOPES_PUBLISHING = ("instagram_business_content_publish",)

# Refresh this many days before expiry. 60-day token, refreshed at day 50,
# leaves ten days of retries before the unrecoverable cliff.
REFRESH_MARGIN_DAYS = 10


class InstagramLoginError(RuntimeError):
    """Raised with a message safe to show an operator."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_configured() -> bool:
    """True when the standalone Instagram app credentials are present."""
    return bool(settings.instagram_app_id and settings.instagram_app_secret)


def _require_config() -> tuple[str, str, str]:
    if not is_configured():
        raise InstagramLoginError(
            "Standalone Instagram login is not configured. Set INSTAGRAM_APP_ID, "
            "INSTAGRAM_APP_SECRET and INSTAGRAM_REDIRECT_URI from a Meta app that "
            "uses 'API setup with Instagram Login'. These are NOT the same values "
            "as your Facebook app id and secret."
        )
    if not settings.instagram_redirect_uri:
        raise InstagramLoginError(
            "INSTAGRAM_REDIRECT_URI is not set. It must match a Valid OAuth Redirect "
            "URI in the Meta App Dashboard exactly, including scheme and trailing slash."
        )
    return (
        settings.instagram_app_id,
        settings.instagram_app_secret,
        settings.instagram_redirect_uri,
    )


# =========================================================================== #
# step 1 — send the company to Instagram
# =========================================================================== #
def authorize_url(state: str | None = None, *, publishing: bool = False) -> tuple[str, str]:
    """Build the consent URL. Returns (url, state).

    `state` is CSRF protection and is echoed back to the callback. Generate it
    here, store it against the session or company, and reject a callback whose
    state you did not issue — without that check, anyone who can reach your
    callback URL can attach an Instagram account of their choosing to a tenant.
    """
    app_id, _, redirect_uri = _require_config()
    state = state or secrets.token_urlsafe(24)

    scopes = list(SCOPES_MESSAGING)
    if publishing:
        scopes += list(SCOPES_PUBLISHING)

    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": ",".join(scopes),
        "state": state,
    }
    url = f"{IG_AUTH_HOST}/oauth/authorize?{urlencode(params)}"
    logger.info("[LeadAI ig-login] authorize url built, scopes=%s", ",".join(scopes))
    return url, state


# =========================================================================== #
# step 2 — code -> short-lived token
# =========================================================================== #
def exchange_code(code: str, *, timeout: float = 20.0) -> dict:
    """Swap the authorization code for a short-lived token (1 hour).

    Returns {"access_token", "user_id", "permissions"}.

    The code is single-use and short-lived; "Matching code was not found or was
    already used" nearly always means the callback ran twice (a browser retry, a
    double-clicked button, or a proxy replaying the request), not that the code
    was wrong.
    """
    app_id, app_secret, redirect_uri = _require_config()

    import httpx

    try:
        response = httpx.post(
            f"{IG_OAUTH_HOST}/oauth/access_token",
            data={
                "client_id": app_id,
                "client_secret": app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code,
            },
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        raise InstagramLoginError(f"Could not reach Instagram: {exc}") from exc

    if response.status_code >= 400:
        raise InstagramLoginError(
            f"Instagram rejected the authorization code ({response.status_code}): "
            f"{response.text[:300]}"
        )

    data = response.json() or {}
    if not data.get("access_token"):
        raise InstagramLoginError(f"No access token in Instagram's reply: {data}")

    logger.info("[LeadAI ig-login] short-lived token obtained for user_id=%s", data.get("user_id"))
    return data


# =========================================================================== #
# step 3 — short-lived -> long-lived (60 days)
# =========================================================================== #
def exchange_long_lived(short_token: str, *, timeout: float = 20.0) -> dict:
    """Returns {"access_token", "token_type", "expires_in"}.

    Do this immediately. A short-lived token dies in an hour, and a connection
    flow that stores one produces an integration that works during the demo and
    is broken by lunchtime.
    """
    _, app_secret, _ = _require_config()

    import httpx

    try:
        response = httpx.get(
            f"{IG_GRAPH_HOST}/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": app_secret,
                "access_token": short_token,
            },
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        raise InstagramLoginError(f"Could not reach Instagram: {exc}") from exc

    if response.status_code >= 400:
        raise InstagramLoginError(
            f"Long-lived token exchange failed ({response.status_code}): {response.text[:300]}"
        )

    data = response.json() or {}
    if not data.get("access_token"):
        raise InstagramLoginError(f"No long-lived token in Instagram's reply: {data}")

    logger.info(
        "[LeadAI ig-login] long-lived token obtained, expires_in=%s", data.get("expires_in")
    )
    return data


def refresh_long_lived(token: str, *, timeout: float = 20.0) -> dict:
    """Extend a long-lived token by another 60 days.

    Only works when the token is at least 24 hours old and not yet expired.
    Both conditions are Meta's, not ours, and neither is recoverable: an expired
    token means the company must re-authorise from scratch.
    """
    import httpx

    try:
        response = httpx.get(
            f"{IG_GRAPH_HOST}/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": token},
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        raise InstagramLoginError(f"Could not reach Instagram: {exc}") from exc

    if response.status_code >= 400:
        raise InstagramLoginError(
            f"Token refresh failed ({response.status_code}): {response.text[:300]}"
        )

    data = response.json() or {}
    if not data.get("access_token"):
        raise InstagramLoginError(f"No token in refresh reply: {data}")
    return data


# =========================================================================== #
# step 4 — who did we just connect?
# =========================================================================== #
def me(token: str, *, timeout: float = 20.0) -> dict:
    """Profile of the connected account.

    `user_id` here is the Instagram professional account id, and it is what Meta
    puts in the webhook payload's `entry[].id`. Storing it as the account's
    ExternalId is what lets an inbound webhook be routed to the right tenant
    without trusting anything in the request body.
    """
    import httpx

    try:
        response = httpx.get(
            f"{IG_GRAPH_HOST}/v23.0/me",
            params={
                "fields": "user_id,username,name,account_type,profile_picture_url,followers_count",
                "access_token": token,
            },
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        raise InstagramLoginError(f"Could not reach Instagram: {exc}") from exc

    if response.status_code >= 400:
        raise InstagramLoginError(
            f"Could not read the connected profile ({response.status_code}): "
            f"{response.text[:300]}"
        )

    data = response.json() or {}
    account_type = (data.get("account_type") or "").upper()
    if account_type and account_type not in ("BUSINESS", "CREATOR", "MEDIA_CREATOR"):
        raise InstagramLoginError(
            f"'{data.get('username')}' is a {account_type.title()} account. Messaging "
            "requires a professional account — switch it to Business or Creator in "
            "Instagram under Settings > Account type and tools, then reconnect."
        )
    return data


# =========================================================================== #
# the whole dance, once
# =========================================================================== #
def complete_connection(code: str) -> dict:
    """code -> long-lived token + profile, in one call.

    Returns everything `channels`/the router needs to create or update a
    LeadChannelAccount:
        access_token, expires_at, external_id, scoped_user_id, username,
        name, account_type

    TWO IDS, BOTH KEPT
    ------------------
    Instagram hands back two different identifiers in this flow and they are
    not interchangeable:

        /oauth/access_token -> user_id  : the APP-SCOPED id   (e.g. 2746…)
        /me?fields=user_id  -> user_id  : the IGID            (e.g. 17841…)

    Which of them appears in a webhook depends on the payload shape Meta
    chooses, and there is no way to know in advance. This function used to
    return only the IGID, which left webhook routing guessing whenever the
    other one arrived. Both are returned now and both are stored, so
    `channels.find_account` can match exactly instead of falling back to a
    heuristic that breaks as soon as a second company connects Instagram.
    """
    short = exchange_code(code)
    long_lived = exchange_long_lived(short["access_token"])
    token = long_lived["access_token"]

    expires_in = int(long_lived.get("expires_in") or 0)
    expires_at = _now() + timedelta(seconds=expires_in) if expires_in else None

    profile = me(token)

    # `user_id` is the professional-account id used in webhooks; `id` is the
    # app-scoped id. Prefer the former, fall back to the latter.
    external_id = str(profile.get("user_id") or profile.get("id") or short.get("user_id") or "")
    if not external_id:
        raise InstagramLoginError("Instagram did not return an account id for this connection")

    return {
        "access_token": token,
        "expires_at": expires_at,
        "expires_in": expires_in,
        "external_id": external_id,
        # App-scoped id from the OAuth step. May legitimately be absent or
        # identical to external_id; the caller stores it only when it differs.
        "scoped_user_id": str(short.get("user_id") or ""),
        "username": profile.get("username"),
        "name": profile.get("name") or profile.get("username"),
        "account_type": profile.get("account_type"),
        "profile_picture_url": profile.get("profile_picture_url"),
        "permissions": short.get("permissions"),
    }


# =========================================================================== #
# refresh scheduling
# =========================================================================== #
def refresh_due(expires_at: datetime | None, margin_days: int = REFRESH_MARGIN_DAYS) -> bool:
    """Should this token be refreshed now?

    Returns True for a null expiry too: an account with no recorded expiry is
    one we cannot reason about, and attempting a refresh is harmless (it either
    succeeds and gives us a real expiry, or fails and tells us why).
    """
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at - _now() <= timedelta(days=margin_days)


# =========================================================================== #
# background refresh
# =========================================================================== #
def refresh_all_due(db, *, margin_days: int = REFRESH_MARGIN_DAYS) -> dict:
    """Refresh every standalone Instagram token approaching expiry.

    Run this daily. It is the difference between an integration that keeps
    working and one that dies exactly 60 days after a successful demo, which is
    long enough for everyone to have stopped thinking about it.

    Failures deactivate the account and record the reason rather than raising:
    one company's expired token must not stop the others being refreshed, and a
    deactivated account with `LastError` set is what makes the dashboard show
    "reconnect required" instead of failing silently on the next send.
    """
    from ..models import LeadChannelAccount, utcnow
    from ..security import decrypt_pii, encrypt_pii

    accounts = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.Channel == "instagram",
            LeadChannelAccount.LoginType == LOGIN_TYPE_INSTAGRAM,
            LeadChannelAccount.IsActive == True,  # noqa: E712
            LeadChannelAccount.IsDeleted == False,  # noqa: E712
        )
        .all()
    )

    refreshed, skipped, failed = 0, 0, 0
    for account in accounts:
        if not refresh_due(account.TokenExpiresAt, margin_days):
            skipped += 1
            continue

        token = decrypt_pii(account.AccessTokenEnc)
        if not token:
            failed += 1
            account.LastError = "No stored token to refresh"
            account.LastErrorAt = utcnow()
            continue

        try:
            result = refresh_long_lived(token)
        except InstagramLoginError as exc:
            failed += 1
            account.LastError = f"Token refresh failed: {exc}"[:500]
            account.LastErrorAt = utcnow()
            # Expired is terminal — no amount of retrying recovers it, and
            # leaving the account active would produce confusing send failures
            # instead of a clear "reconnect this account" state.
            if account.TokenExpiresAt and not refresh_due(account.TokenExpiresAt, 0):
                account.IsActive = False
                logger.error(
                    "[LeadAI ig-login] token EXPIRED for %s — company must reconnect",
                    account.Name,
                )
            continue

        expires_in = int(result.get("expires_in") or 0)
        account.AccessTokenEnc = encrypt_pii(result["access_token"])
        account.TokenExpiresAt = _now() + timedelta(seconds=expires_in) if expires_in else None
        account.TokenRefreshedAt = utcnow()
        account.LastError = None
        account.LastErrorAt = None
        refreshed += 1
        logger.info(
            "[LeadAI ig-login] refreshed %s, valid until %s",
            account.Name, account.TokenExpiresAt,
        )

    db.commit()
    logger.info(
        "[LeadAI ig-login] refresh sweep: refreshed=%d skipped=%d failed=%d",
        refreshed, skipped, failed,
    )
    return {"refreshed": refreshed, "skipped": skipped, "failed": failed}
