"""
Bridge: a company's registered channel -> the agent's credential context.

This is the ONLY place that turns a `LeadChannelAccount` row into something the
vendored `social_agent` code can use. Keeping it in one function is what makes
the tenancy guarantee auditable: if this function is correct, no endpoint can
post to a Page the caller does not own, because no endpoint has any other way
to obtain a token.

HOW A COMPANY'S ACCOUNTS MAP ONTO META'S OBJECTS
------------------------------------------------
The existing Channels feature was built for *messaging*, so its vocabulary is
`whatsapp | messenger | instagram`. Publishing uses the same underlying
objects, just different fields:

    posting to a Facebook Page  ->  needs the PAGE id      + a Page token
    posting to Instagram        ->  needs the IG USER id   + the SAME Page token

`LeadChannelAccount.ExternalId` already stores exactly the right id for each
channel (Page id for `messenger`, IG professional account id for `instagram`),
so no new registration step is needed: a company that has connected its Page
for Messenger can already publish to it.

THE SHARED-TOKEN CASE
---------------------
An Instagram professional account is published to with the access token of the
Facebook Page it is linked to — Instagram has no separate token. Companies
therefore very often paste the token on the Messenger row and leave the
Instagram row's token blank (or connect Instagram alone and put the Page token
there). `resolve()` handles both: if the Instagram row carries no token it
borrows the token from the company's Page row. Without this, the common setup
would fail with a confusing "no access token" even though the company had
supplied one.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from social_agent.context import SocialCredentials

from ..models import LeadChannelAccount
from ..security import decrypt_pii

logger = logging.getLogger(__name__)

# Platform name used by the publishing API -> channel values that can serve it.
# `facebook` accepts the legacy `messenger` value because that is what the
# Channels UI writes when a Page is connected for messaging; the Page id is the
# same object either way.
PLATFORM_CHANNELS: dict[str, tuple[str, ...]] = {
    "facebook": ("facebook", "messenger"),
    "instagram": ("instagram",),
}

SUPPORTED_PLATFORMS = tuple(PLATFORM_CHANNELS)


class ChannelNotConnected(Exception):
    """The company has not connected a usable account for this platform.

    Distinct from a credentials error: the fix is an onboarding action ("connect
    your Page"), not a debugging one, and the API layer turns it into a 409 with
    that instruction rather than a 500.
    """


def _accounts_for(db: Session, client_id: str, channels: tuple[str, ...]) -> list[LeadChannelAccount]:
    return (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.ClientId == client_id,
            LeadChannelAccount.Channel.in_(channels),
            LeadChannelAccount.IsActive == True,  # noqa: E712
            LeadChannelAccount.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadChannelAccount.CreatedAt.asc())
        .all()
    )


def _token(account: LeadChannelAccount | None) -> str | None:
    if account is None or not account.AccessTokenEnc:
        return None
    try:
        return decrypt_pii(account.AccessTokenEnc)
    except Exception as exc:  # noqa: BLE001
        # A token that will not decrypt means the Fernet key rotated without a
        # re-encrypt. Log it and treat the account as tokenless so the caller
        # gets "reconnect this account" instead of a raw crypto traceback.
        logger.error(
            "[social] could not decrypt token for channel account %s: %s", account.Id, exc
        )
        return None


def _secret(account: LeadChannelAccount | None) -> str | None:
    if account is None or not account.AppSecretEnc:
        return None
    try:
        return decrypt_pii(account.AppSecretEnc)
    except Exception:  # noqa: BLE001
        return None


def resolve(
    db: Session,
    client_id: str,
    platform: str,
    account_id: str | None = None,
) -> SocialCredentials:
    """Build the credential context for one company on one platform.

    `account_id` pins a specific connected account, for companies that run more
    than one Page. Left None, the company's oldest active account for that
    platform is used, which keeps the common single-Page case a no-argument call
    while staying deterministic (never "whichever row the DB returned first").
    """
    platform = (platform or "").strip().lower()
    channels = PLATFORM_CHANNELS.get(platform)
    if channels is None:
        raise ChannelNotConnected(
            f"Unsupported platform '{platform}'. Supported: {', '.join(SUPPORTED_PLATFORMS)}."
        )

    accounts = _accounts_for(db, client_id, channels)
    if not accounts:
        raise ChannelNotConnected(
            f"No active {platform} account is connected for this company. "
            f"Connect one first: POST /channels with channel="
            f"'{'messenger' if platform == 'facebook' else platform}'."
        )

    if account_id:
        account = next((a for a in accounts if a.Id == account_id), None)
        if account is None:
            # Deliberately the same message as "does not exist": an id belonging
            # to another company must not be distinguishable from a bad id.
            raise ChannelNotConnected(
                f"Channel account '{account_id}' is not a connected {platform} "
                "account for this company."
            )
    else:
        account = accounts[0]

    token = _token(account)
    page_id: str | None = None
    ig_user_id: str | None = None

    if platform == "facebook":
        page_id = account.ExternalId
    else:
        ig_user_id = account.ExternalId
        # BusinessAccountId is where the Channels UI records the linked Page for
        # an IG row when the operator supplies it; useful for insights calls.
        page_id = account.BusinessAccountId

    # Instagram publishes with the linked Page's token — borrow it when the IG
    # row itself has none. See the module docstring.
    if not token and platform == "instagram":
        for page_account in _accounts_for(db, client_id, PLATFORM_CHANNELS["facebook"]):
            token = _token(page_account)
            if token:
                page_id = page_id or page_account.ExternalId
                logger.info(
                    "[social] instagram account %s using Page account %s's token",
                    account.Id,
                    page_account.Id,
                )
                break

    if not token:
        raise ChannelNotConnected(
            f"The connected {platform} account '{account.Name}' has no access token. "
            f"Add one with PATCH /channels/{account.Id}."
        )

    return SocialCredentials(
        client_id=client_id,
        account_id=account.Id,
        access_token=token,
        page_id=page_id,
        ig_user_id=ig_user_id,
        app_secret=_secret(account),
        api_version=account.ApiVersion,
        account_name=account.Name,
        meta={"channel": account.Channel, "external_id": account.ExternalId},
    )


def connected_platforms(db: Session, client_id: str) -> dict[str, dict]:
    """What this company can currently publish to — powers the UI's platform picker."""
    out: dict[str, dict] = {}
    for platform in SUPPORTED_PLATFORMS:
        try:
            creds = resolve(db, client_id, platform)
        except ChannelNotConnected as exc:
            out[platform] = {"connected": False, "reason": str(exc)}
            continue
        out[platform] = {
            "connected": True,
            "account_id": creds.account_id,
            "account_name": creds.account_name,
            "target_id": creds.page_id if platform == "facebook" else creds.ig_user_id,
        }
    return out
