"""
LinkedIn integration router for LeadAI.
"""
from __future__ import annotations

import logging
from typing import Optional, Any, List, Dict
from fastapi import APIRouter, Depends, HTTPException, Request, status, BackgroundTasks, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from .. import activity
from ..activity import A
from ..db import get_leadai_db
from ..models import LeadChannelAccount, LeadAccount, LeadConversation, LeadCustomer, Lead, LeadMessage, utcnow
from ..models_ext import LeadChannelIdentity
from ..rbac import Principal, assert_owns, scoped
from ..security import encrypt_pii

import time

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/linkedin", tags=["LeadAI • LinkedIn"])

_LAST_COMMENT_SYNC_BY_CLIENT: dict[str, float] = {}
_IS_COMMENT_SYNC_RUNNING = False

async def _bg_auto_sync_comments(company_id: str):
    global _IS_COMMENT_SYNC_RUNNING
    if _IS_COMMENT_SYNC_RUNNING:
        logger.debug("[LinkedIn Auto-Sync] Comment sync is already running, skipping duplicate.")
        return
    _IS_COMMENT_SYNC_RUNNING = True
    from core.database import SessionLocalAdmin
    from ..social import linkedin_bot
    from ..models_ext import LeadChannelAccount
    db_bg = SessionLocalAdmin()
    try:
        account = db_bg.query(LeadChannelAccount).filter(
            LeadChannelAccount.ClientId == company_id,
            LeadChannelAccount.Channel == "linkedin",
            LeadChannelAccount.IsDeleted == False
        ).first()
        if account and (account.LinkedinCookieEnc or (account.LinkedinUsernameEnc and account.LinkedinPasswordEnc)):
            await linkedin_bot.fetch_recent_posts_and_comments_browser(db_bg, account, limit_posts=2)
    except Exception as exc:
        logger.debug("[LinkedIn Auto-Sync] Background refresh notice for client %s: %s", company_id, exc)
    finally:
        _IS_COMMENT_SYNC_RUNNING = False
        db_bg.close()

# ===========================================================================
# LinkedIn OAuth & Status
# ===========================================================================

@router.get(
    "/connect",
    summary="Retrieve LinkedIn authorization link",
)
async def linkedin_connect(
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    from ..social import linkedin
    from ..services import billing as billing_svc

    principal, client_id = scope
    allowed, reason = billing_svc.check_channel_access(db, client_id, "linkedin")
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, reason)

    try:
        url = await linkedin.build_authorize_url(db, client_id)
        return {"authorize_url": url}
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get(
    "/status",
    summary="Get LinkedIn connection status",
)
async def linkedin_status(
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    from ..models_ext import LeadChannelAccount

    principal, client_id = scope
    cred = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.ClientId == client_id,
            LeadChannelAccount.Channel == "linkedin",
            LeadChannelAccount.IsDeleted == False,
        )
        .order_by(LeadChannelAccount.UpdatedAt.desc())
        .first()
    )

    if not cred:
        return {
            "connected": False,
            "person_urn": None,
            "access_token_valid": False,
            "has_refresh_token": False,
            "has_cookie_credentials": False,
            "auto_accept": False,
            "welcome_message": None,
            "auto_dm_leads": True,
        }

    now = utcnow()
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)

    access_token_valid = (
        cred.TokenExpiresAt > now
        if cred.TokenExpiresAt and cred.AccessTokenEnc
        else False
    )

    meta = cred.MetaJson or {}

    has_credentials = bool(cred.LinkedinCookieEnc or (cred.LinkedinUsernameEnc and cred.LinkedinPasswordEnc))

    return {
        "connected": bool(cred.AccessTokenEnc),
        "person_urn": cred.ExternalId,
        "access_token_valid": access_token_valid,
        "has_refresh_token": bool(cred.AppSecretEnc),
        "has_cookie_credentials": has_credentials,
        "auto_accept": meta.get("linkedin_auto_accept", False),
        "welcome_message": meta.get("linkedin_welcome_message"),
        "auto_dm_leads": meta.get("linkedin_auto_dm_leads", True),
    }



@router.post(
    "/disconnect",
    summary="Disconnect LinkedIn account",
)
async def linkedin_disconnect(
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    from ..models_ext import LeadChannelAccount
    from ..models_blog import LeadSocialComment

    principal, client_id = scope
    cred = db.query(LeadChannelAccount).filter(
        LeadChannelAccount.ClientId == client_id,
        LeadChannelAccount.Channel == "linkedin",
        LeadChannelAccount.IsDeleted == False
    ).first()

    if cred:
        cred.IsDeleted = True
        cred.LinkedinCookieEnc = None
        cred.LinkedinUsernameEnc = None
        cred.LinkedinPasswordEnc = None
        cred.UpdatedAt = utcnow()
        
        # Soft-delete all existing comments for this company & channel so old account comments do not persist
        db.query(LeadSocialComment).filter(
            LeadSocialComment.ClientId == client_id,
            LeadSocialComment.Channel == "linkedin",
            LeadSocialComment.IsDeleted == False
        ).update(
            {LeadSocialComment.IsDeleted: True, LeadSocialComment.UpdatedAt: utcnow()},
            synchronize_session=False
        )
        _LAST_COMMENT_SYNC_BY_CLIENT.pop(client_id, None)

        activity.log_principal(
            db,
            principal,
            action=A.CHANNEL_UPDATED,
            client_id=client_id,
            entity_type="channel_account",
            entity_id=cred.Id,
            message="Disconnected LinkedIn account",
            request=request,
        )
        db.commit()

    return {"ok": True}


class LinkedInBotCredentialsInput(BaseModel):
    cookie_li_at: str | None = None
    username: str | None = None
    password: str | None = None

class LinkedInGenerateKeywordsInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=500)

class LinkedInSearchProfilesInput(BaseModel):
    keywords: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=15, ge=1, le=50)

class LinkedInProfileInput(BaseModel):
    public_id: str
    urn_id: str | None = None
    name: str | None = None

class LinkedInSendInvitationsInput(BaseModel):
    profiles: list[LinkedInProfileInput]
    message: str | None = None


@router.get(
    "/callback",
    summary="LinkedIn OAuth Callback",
)
async def linkedin_callback(
    code: str = None,
    state: str = None,
    error: str = None,
    error_description: str = None,
    db: Session = Depends(get_leadai_db),
):
    from fastapi.responses import HTMLResponse
    from ..social import linkedin

    if error:
        return HTMLResponse(
            f"<h3>Authentication Failed</h3><p>{error}: {error_description}</p>",
            status_code=400
        )

    if not code or not state:
        return HTMLResponse(
            "<h3>Authentication Failed</h3><p>Missing auth code or state parameter.</p>",
            status_code=400
        )

    # Validate state and retrieve company_id
    company_id = await linkedin.consume_oauth_state(db, state)
    if not company_id:
        return HTMLResponse(
            "<h3>Authentication Failed</h3><p>OAuth state is invalid or expired. Please try connecting again.</p>",
            status_code=400
        )

    try:
        token_data = await linkedin.exchange_code_for_tokens(code)
        access_token = token_data["access_token"]
        person_urn = await linkedin.fetch_person_urn(access_token)

        await linkedin.save_tokens(
            db=db,
            client_id=company_id,
            person_urn=person_urn,
            access_token=access_token,
            expires_in_seconds=token_data["expires_in"],
            refresh_token=token_data.get("refresh_token"),
            refresh_token_expires_in_seconds=token_data.get("refresh_token_expires_in"),
        )

        # Return a simple script to notify the opener window and close the popup
        html_content = f"""<!DOCTYPE html>
<html>
<head><title>LinkedIn Connected</title></head>
<body>
    <p>Connected successfully. Redirecting...</p>
    <script>
        if (window.opener) {{
            window.opener.postMessage({{
                type: 'LINKEDIN_OAUTH_SUCCESS',
                state: '{state}',
                person_urn: '{person_urn}'
            }}, '*');
        }}
        window.close();
    </script>
</body>
</html>"""
        return HTMLResponse(html_content)
    except Exception as exc:
        logger.error("LinkedIn OAuth callback completion failed: %s", exc)
        return HTMLResponse(
            f"<h3>Connection Failed</h3><p>An error occurred: {str(exc)}</p>",
            status_code=500
        )


class LinkedInOAuthCallbackInput(BaseModel):
    code: str
    state: str


@router.post(
    "/callback",
    summary="LinkedIn OAuth Callback (JSON)",
)
async def linkedin_callback_json(
    payload: LinkedInOAuthCallbackInput,
    db: Session = Depends(get_leadai_db),
):
    from ..social import linkedin

    # Validate state and retrieve company_id
    company_id = await linkedin.consume_oauth_state(db, payload.state)
    if not company_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "OAuth state is invalid or expired")

    try:
        token_data = await linkedin.exchange_code_for_tokens(payload.code)
        access_token = token_data["access_token"]
        person_urn = await linkedin.fetch_person_urn(access_token)

        await linkedin.save_tokens(
            db=db,
            client_id=company_id,
            person_urn=person_urn,
            access_token=access_token,
            expires_in_seconds=token_data["expires_in"],
            refresh_token=token_data.get("refresh_token"),
            refresh_token_expires_in_seconds=token_data.get("refresh_token_expires_in"),
        )
        return {"success": True, "person_urn": person_urn}
    except Exception as exc:
        logger.error("LinkedIn OAuth JSON callback failed: %s", exc)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))


@router.post(
    "/credentials",
    summary="Save LinkedIn credentials/cookie for candidates automation",
)
async def save_linkedin_credentials(
    payload: LinkedInBotCredentialsInput,
    background_tasks: BackgroundTasks = None,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    from ..services import billing as billing_svc
    allowed, reason = billing_svc.check_channel_access(db, company_id, "linkedin")
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, reason)

    # Find the active LeadChannelAccount row
    row = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.ClientId == company_id,
            LeadChannelAccount.Channel == "linkedin",
            LeadChannelAccount.IsDeleted == False,
        )
        .order_by(LeadChannelAccount.UpdatedAt.desc())
        .first()
    )

    if not row:
        # Check if there is an existing row that can be reactivated
        row = (
            db.query(LeadChannelAccount)
            .filter(
                LeadChannelAccount.ClientId == company_id,
                LeadChannelAccount.Channel == "linkedin",
            )
            .order_by(LeadChannelAccount.UpdatedAt.desc())
            .first()
        )
        if row:
            row.IsDeleted = False
            row.IsActive = True
        else:
            row = LeadChannelAccount(
                ClientId=company_id,
                Channel="linkedin",
                Provider="linkedin",
                LoginType="linkedin",
                Name="LinkedIn Account",
                IsActive=True,
                CreatedBy="system",
            )
            db.add(row)

    # Ensure all other older active rows for this company are retired
    other_active = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.ClientId == company_id,
            LeadChannelAccount.Channel == "linkedin",
            LeadChannelAccount.Id != row.Id,
            LeadChannelAccount.IsDeleted == False,
        )
        .all()
    )
    for o in other_active:
        o.IsDeleted = True
        o.UpdatedAt = utcnow()

    # Encrypt and save the credentials
    if payload.cookie_li_at:
        import re
        raw_cookie = payload.cookie_li_at.strip()
        li_at_match = re.search(r'li_at=([^;]+)', raw_cookie)
        jsessionid_match = re.search(r'JSESSIONID="?([^";]+)"?', raw_cookie)
        
        li_at = (li_at_match.group(1) if li_at_match else raw_cookie).strip('"; \t\r\n')
        jsessionid = (jsessionid_match.group(1) if jsessionid_match else "").strip('"; \t\r\n')
        
        if jsessionid:
            row.LinkedinCookieEnc = encrypt_pii(f"{li_at}|||{jsessionid}")
        else:
            row.LinkedinCookieEnc = encrypt_pii(li_at)
            
        if payload.username and payload.password:
            row.LinkedinUsernameEnc = encrypt_pii(payload.username.strip())
            row.LinkedinPasswordEnc = encrypt_pii(payload.password.strip())
    elif payload.username and payload.password:
        row.LinkedinUsernameEnc = encrypt_pii(payload.username.strip())
        row.LinkedinPasswordEnc = encrypt_pii(payload.password.strip())
        
        # Attempt automated headless browser session extraction
        from ..social import linkedin_bot
        extracted_cookie = await linkedin_bot.extract_session_cookie_via_browser(payload.username.strip(), payload.password.strip())
        if extracted_cookie:
            row.LinkedinCookieEnc = encrypt_pii(extracted_cookie)
        else:
            # Wipe stale expired cookie so it is not used
            row.LinkedinCookieEnc = None
            row.UpdatedAt = utcnow()
            db.commit()
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "LinkedIn triggered a security check (CAPTCHA / 2FA code) or invalid login. Please switch to 'Mode B: Session Token (li_at)' and paste your li_at token directly."
            )

    # Soft delete existing comments for this company & channel so that previous account comments are isolated
    from ..models_blog import LeadSocialComment
    db.query(LeadSocialComment).filter(
        LeadSocialComment.ClientId == company_id,
        LeadSocialComment.Channel == "linkedin",
        LeadSocialComment.IsDeleted == False
    ).update(
        {LeadSocialComment.IsDeleted: True, LeadSocialComment.UpdatedAt: utcnow()},
        synchronize_session=False
    )
    _LAST_COMMENT_SYNC_BY_CLIENT.pop(company_id, None)

    row.UpdatedAt = utcnow()
    db.commit()

    # Trigger fresh background sync for the newly connected credentials/account
    if background_tasks is not None:
        background_tasks.add_task(_bg_auto_sync_comments, company_id)

    return {"ok": True, "has_cookie": bool(row.LinkedinCookieEnc)}


@router.delete(
    "/credentials",
    summary="Remove personal LinkedIn session cookie and credentials",
)
@router.post(
    "/credentials/disconnect",
    summary="Remove personal LinkedIn session cookie and credentials",
)
async def disconnect_linkedin_credentials(
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    row = db.query(LeadChannelAccount).filter(
        LeadChannelAccount.ClientId == client_id,
        LeadChannelAccount.Channel == "linkedin",
        LeadChannelAccount.IsDeleted == False
    ).first()

    if row:
        row.LinkedinCookieEnc = None
        row.LinkedinUsernameEnc = None
        row.LinkedinPasswordEnc = None
        row.UpdatedAt = utcnow()
        
        # Soft-delete all existing comments for this company & channel so disconnected account data is wiped
        from ..models_blog import LeadSocialComment
        db.query(LeadSocialComment).filter(
            LeadSocialComment.ClientId == client_id,
            LeadSocialComment.Channel == "linkedin",
            LeadSocialComment.IsDeleted == False
        ).update(
            {LeadSocialComment.IsDeleted: True, LeadSocialComment.UpdatedAt: utcnow()},
            synchronize_session=False
        )
        _LAST_COMMENT_SYNC_BY_CLIENT.pop(client_id, None)

        activity.log_principal(
            db,
            principal,
            action=A.CHANNEL_UPDATED,
            client_id=client_id,
            entity_type="channel_account",
            entity_id=row.Id,
            message="Removed personal LinkedIn session cookie and credentials",
            request=request,
        )
        db.commit()

    return {"ok": True, "message": "Personal LinkedIn credentials and session cookie removed successfully"}


@router.post(
    "/generate-keywords",
    summary="Generate Boolean search keywords from a description prompt",
)
async def linkedin_generate_keywords(
    payload: LinkedInGenerateKeywordsInput,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
):
    from ..social import linkedin_bot
    keywords = await linkedin_bot.generate_search_keywords(payload.prompt)
    return {"keywords": keywords}


@router.post(
    "/search-profiles",
    summary="Search profiles on LinkedIn using configured credentials",
)
async def linkedin_search_profiles(
    payload: LinkedInSearchProfilesInput,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    from ..services import billing as billing_svc
    allowed, reason = billing_svc.check_channel_access(db, company_id, "linkedin")
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, reason)

    from ..social import linkedin_bot

    # Retrieve credentials from database
    row = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.ClientId == company_id,
            LeadChannelAccount.Channel == "linkedin",
            LeadChannelAccount.IsDeleted == False,
        )
        .order_by(LeadChannelAccount.UpdatedAt.desc())
        .first()
    )

    if not row or (not row.LinkedinCookieEnc and not (row.LinkedinUsernameEnc and row.LinkedinPasswordEnc)):
        raise HTTPException(status.HTTP_409_CONFLICT, "LinkedIn search credentials/cookies are not configured")

    try:
        profiles = await linkedin_bot.search_profiles_api(row, payload.keywords, limit=payload.limit)
        return {"profiles": profiles}
    except Exception as exc:
        logger.error("LinkedIn profile search failed: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LinkedIn search failed: {str(exc)}")


async def _send_manual_invitations_task(account_id: str, profiles_dict: list[dict], message: str | None, client_id: str, actor_email: str):
    from ..social import linkedin_bot
    from .. import activity
    from ..activity import A
    from ..db import session as db_session
    
    with db_session() as db:
        account = db.get(LeadChannelAccount, account_id)
        if not account:
            return

    results = await linkedin_bot.send_connection_invitations_api(account, profiles_dict, message=message)
    
    with db_session() as db:
        sent_count = 0
        failed_count = 0
        for p in profiles_dict:
            pid = p.get("public_id")
            name = p.get("name") or p.get("full_name") or pid or "Candidate"
            headline = p.get("headline") or ""
            profile_url = p.get("profile_url") or (f"https://www.linkedin.com/in/{pid}" if pid and not pid.startswith("urn:") else "")
            
            res_detail = results.get(pid, {})
            success = res_detail.get("success", False)
            res_msg = res_detail.get("message", "Sent" if success else "Failed")
            
            if success:
                sent_count += 1
                activity.log(
                    db,
                    action=A.LINKEDIN_CONNECTION_SENT,
                    client_id=client_id,
                    actor_email=actor_email,
                    entity_type="linkedin",
                    entity_id=pid,
                    log_type="Info",
                    message=f"Sent connection invitation to {name}" + (f" ({headline})" if headline else ""),
                    meta={
                        "name": name,
                        "public_id": pid,
                        "headline": headline,
                        "profile_url": profile_url,
                        "invitation_message": message,
                        "mode": "manual",
                    },
                    commit=True,
                )
            else:
                failed_count += 1
                activity.log(
                    db,
                    action=A.LINKEDIN_CONNECTION_FAILED,
                    client_id=client_id,
                    actor_email=actor_email,
                    entity_type="linkedin",
                    entity_id=pid,
                    log_type="Warning",
                    message=f"Failed to send connection invitation to {name}: {res_msg}",
                    meta={
                        "name": name,
                        "public_id": pid,
                        "headline": headline,
                        "profile_url": profile_url,
                        "error": res_msg,
                        "mode": "manual",
                    },
                    commit=True,
                )


@router.post(
    "/send-invitations",
    summary="Send connection requests to selected profiles",
)
async def linkedin_send_invitations(
    payload: LinkedInSendInvitationsInput,
    background_tasks: BackgroundTasks,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    principal, company_id = scope
    from ..services import billing as billing_svc
    allowed, reason = billing_svc.check_channel_access(db, company_id, "linkedin")
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, reason)

    # Retrieve credentials from database
    row = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.ClientId == company_id,
            LeadChannelAccount.Channel == "linkedin",
            LeadChannelAccount.IsDeleted == False,
        )
        .order_by(LeadChannelAccount.UpdatedAt.desc())
        .first()
    )

    if not row or (not row.LinkedinCookieEnc and not (row.LinkedinUsernameEnc and row.LinkedinPasswordEnc)):
        raise HTTPException(status.HTTP_409_CONFLICT, "LinkedIn automation credentials/cookies are not configured")

    try:
        profiles_dict = [p.model_dump() for p in payload.profiles]
        background_tasks.add_task(
            _send_manual_invitations_task,
            row.Id,
            profiles_dict,
            payload.message,
            company_id,
            principal.email or "user",
        )
        
        # Generate compatible response mapping so the frontend requires no changes
        results = {}
        for p in profiles_dict:
            pid = p.get("public_id")
            if pid:
                results[pid] = {
                    "success": True,
                    "message": "Invitation queued in background"
                }
        return {"results": results}
    except Exception as exc:
        logger.error("LinkedIn send invitations background queuing failed: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LinkedIn invitation background dispatch failed: {str(exc)}")


class LinkedInSettingsInput(BaseModel):
    auto_accept: bool = False
    welcome_message: str | None = None
    auto_dm_leads: bool = True


@router.post(
    "/settings",
    summary="Update LinkedIn settings (auto-accept, welcome message, auto-capture DM leads)",
)
async def save_linkedin_settings(
    payload: LinkedInSettingsInput,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    from sqlalchemy.orm.attributes import flag_modified

    _, company_id = scope
    row = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.ClientId == company_id,
            LeadChannelAccount.Channel == "linkedin",
            LeadChannelAccount.IsDeleted == False,
        )
        .order_by(LeadChannelAccount.UpdatedAt.desc())
        .first()
    )

    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "LinkedIn channel account not found. Connect OAuth first.")

    meta = dict(row.MetaJson or {})
    meta["linkedin_auto_accept"] = payload.auto_accept
    meta["linkedin_welcome_message"] = payload.welcome_message
    meta["linkedin_auto_dm_leads"] = payload.auto_dm_leads
    row.MetaJson = meta
    flag_modified(row, "MetaJson")
    row.UpdatedAt = utcnow()
    db.commit()
    return {"ok": True}


class LinkedInAutoConnectSettingsInput(BaseModel):
    enabled: bool = False
    runs_per_day: int = Field(default=3, ge=1, le=10)
    profiles_per_run: int = Field(default=5, ge=1, le=15)
    target_prompt: str | None = None
    target_keywords: str | None = None
    custom_message: str | None = None
    active_hours_start: int = Field(default=9, ge=0, le=23)
    active_hours_end: int = Field(default=19, ge=0, le=23)


@router.get(
    "/auto-connect/settings",
    summary="Get automated candidate search & connection scheduler settings",
)
async def get_auto_connect_settings(
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    row = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.ClientId == company_id,
            LeadChannelAccount.Channel == "linkedin",
            LeadChannelAccount.IsDeleted == False,
        )
        .order_by(LeadChannelAccount.UpdatedAt.desc())
        .first()
    )

    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "LinkedIn channel account not found.")

    meta = row.MetaJson or {}
    auto_cfg = meta.get("linkedin_auto_connect") or {}
    defaults = {
        "enabled": False,
        "runs_per_day": 3,
        "profiles_per_run": 5,
        "target_prompt": "",
        "target_keywords": "",
        "custom_message": "",
        "active_hours_start": 9,
        "active_hours_end": 19,
        "last_run_at": None,
        "next_run_at": None,
        "total_sent_today": 0,
        "total_sent_all_time": 0,
        "last_run_status": None,
        "last_run_detail": None,
    }
    defaults.update(auto_cfg)
    return {"settings": defaults}


@router.post(
    "/auto-connect/settings",
    summary="Update automated candidate search & connection scheduler settings",
)
async def save_auto_connect_settings(
    payload: LinkedInAutoConnectSettingsInput,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    from sqlalchemy.orm.attributes import flag_modified

    _, company_id = scope
    from ..services import jobs
    from ..services.jobs import calculate_next_random_schedule

    row = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.ClientId == company_id,
            LeadChannelAccount.Channel == "linkedin",
            LeadChannelAccount.IsDeleted == False,
        )
        .order_by(LeadChannelAccount.UpdatedAt.desc())
        .first()
    )

    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "LinkedIn channel account not found.")

    meta = dict(row.MetaJson or {})
    auto_cfg = dict(meta.get("linkedin_auto_connect") or {})
    
    auto_cfg["enabled"] = payload.enabled
    auto_cfg["runs_per_day"] = payload.runs_per_day
    auto_cfg["profiles_per_run"] = payload.profiles_per_run
    auto_cfg["target_prompt"] = payload.target_prompt or ""
    auto_cfg["target_keywords"] = payload.target_keywords or ""
    auto_cfg["custom_message"] = payload.custom_message or ""
    auto_cfg["active_hours_start"] = payload.active_hours_start
    auto_cfg["active_hours_end"] = payload.active_hours_end
    
    if payload.enabled:
        next_dt = calculate_next_random_schedule(
            runs_per_day=payload.runs_per_day,
            active_hours_start=payload.active_hours_start,
            active_hours_end=payload.active_hours_end
        )
        auto_cfg["next_run_at"] = next_dt.isoformat()
    else:
        auto_cfg["next_run_at"] = None

    meta["linkedin_auto_connect"] = auto_cfg
    row.MetaJson = meta
    flag_modified(row, "MetaJson")
    row.UpdatedAt = utcnow()
    db.commit()

    activity.log(
        db,
        action=A.LINKEDIN_SETTINGS_UPDATED,
        client_id=company_id,
        entity_type="linkedin",
        entity_id=row.Id,
        log_type="Info",
        message=f"LinkedIn Auto-Pilot settings updated (enabled={payload.enabled}, runs_per_day={payload.runs_per_day}, keywords='{payload.target_keywords}')",
        meta=auto_cfg,
        commit=True,
    )

    return {"ok": True, "settings": auto_cfg}


@router.post(
    "/auto-connect/run-now",
    summary="Trigger immediate execution of automated candidate search and connect",
)
async def trigger_auto_connect_now(
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    from ..services import jobs, billing as billing_svc
    _, company_id = scope

    allowed, reason = billing_svc.check_channel_access(db, company_id, "linkedin")
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, reason)

    # Enqueue a job to run immediately for this company
    jobs.enqueue(
        db,
        "linkedin.auto_search_and_connect",
        payload={"company_id": company_id},
        commit=True,
    )

    activity.log(
        db,
        action=A.LINKEDIN_AUTO_SEARCH_STARTED,
        client_id=company_id,
        entity_type="linkedin",
        log_type="Info",
        message="Manual run triggered for LinkedIn Auto-Pilot Candidate Search & Connection",
        commit=True,
    )

    return {"ok": True, "message": "Automated search and connection dispatch enqueued in background"}


@router.post(
    "/sync-invitations",
    summary="Trigger immediate LinkedIn connection request sync",
)
async def trigger_linkedin_sync(
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    from ..services import jobs, billing as billing_svc
    _, company_id = scope
    
    allowed, reason = billing_svc.check_channel_access(db, company_id, "linkedin")
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, reason)

    # Enqueue a job to run immediately for this company
    jobs.enqueue(
        db, 
        "linkedin.process_invitations", 
        payload={"company_id": company_id}, 
        commit=True
    )
    return {"ok": True, "message": "LinkedIn connection request sync queued successfully"}


class LinkedInReplyInvitationInput(BaseModel):
    invitation_urn: str
    shared_secret: str
    action: str = "accept"  # "accept" or "reject"
    sender_name: str | None = None
    sender_urn: str | None = None
    public_id: str | None = None


@router.get(
    "/invitations",
    summary="Get list of pending received LinkedIn invitations",
)
async def get_linkedin_invitations(
    limit: int = 50,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    from ..social import linkedin_bot

    row = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.ClientId == company_id,
            LeadChannelAccount.Channel == "linkedin",
            LeadChannelAccount.IsDeleted == False,
        )
        .order_by(LeadChannelAccount.UpdatedAt.desc())
        .first()
    )

    if not row or (not row.LinkedinCookieEnc and not (row.LinkedinUsernameEnc and row.LinkedinPasswordEnc)):
        raise HTTPException(status.HTTP_409_CONFLICT, "LinkedIn automation credentials/cookies are not configured")

    try:
        invitations = await linkedin_bot.fetch_received_invitations_api(row, limit=limit)
        for inv in invitations:
            public_id = inv.get("public_id")
            sender_urn = inv.get("sender_urn")
            if public_id and not inv.get("profile_url"):
                inv["profile_url"] = f"https://www.linkedin.com/in/{public_id}"

            is_lead = False
            crm_account_id = None

            # 1. Check by unique sender URN in channel identities and pipeline leads
            if sender_urn:
                ident = (
                    db.query(LeadChannelIdentity)
                    .filter(
                        LeadChannelIdentity.ClientId == company_id,
                        LeadChannelIdentity.Channel == "linkedin",
                        LeadChannelIdentity.ExternalUserId == str(sender_urn),
                        LeadChannelIdentity.IsDeleted == False,
                    )
                    .first()
                )
                if ident and ident.CustomerId:
                    conv = (
                        db.query(LeadConversation)
                        .filter(
                            LeadConversation.ClientId == company_id,
                            LeadConversation.CustomerId == ident.CustomerId,
                            LeadConversation.Channel == "linkedin",
                            LeadConversation.IsDeleted == False,
                        )
                        .first()
                    )
                    if conv:
                        lead_record = (
                            db.query(Lead)
                            .filter(
                                Lead.ConversationId == conv.Id,
                                Lead.IsDeleted == False,
                            )
                            .first()
                        )
                        if lead_record:
                            is_lead = True
                            crm_account_id = lead_record.ConvertedAccountId

            # 2. Check by verified public_id in customer profile URL if not found by URN
            if not is_lead and public_id:
                cust_match = (
                    db.query(LeadCustomer)
                    .filter(
                        LeadCustomer.ClientId == company_id,
                        LeadCustomer.LinkedinProfileUrl.like(f"%linkedin.com/in/{public_id}%"),
                        LeadCustomer.IsDeleted == False,
                    )
                    .first()
                )
                if cust_match:
                    conv = (
                        db.query(LeadConversation)
                        .filter(
                            LeadConversation.ClientId == company_id,
                            LeadConversation.CustomerId == cust_match.Id,
                            LeadConversation.Channel == "linkedin",
                            LeadConversation.IsDeleted == False,
                        )
                        .first()
                    )
                    if conv:
                        lead_record = (
                            db.query(Lead)
                            .filter(
                                Lead.ConversationId == conv.Id,
                                Lead.IsDeleted == False,
                            )
                            .first()
                        )
                        if lead_record:
                            is_lead = True
                            crm_account_id = lead_record.ConvertedAccountId

            inv["is_crm_lead"] = is_lead
            inv["crm_account_id"] = crm_account_id
            inv["is_converted"] = bool(crm_account_id)
        return {"invitations": invitations}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except Exception as exc:
        logger.error("Failed to fetch received LinkedIn invitations: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to retrieve invitations: {str(exc)}")


@router.post(
    "/invitations/reply",
    summary="Accept or reject a received LinkedIn connection request",
)
async def reply_linkedin_invitation(
    payload: LinkedInReplyInvitationInput,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    from ..social import linkedin_bot

    row = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.ClientId == company_id,
            LeadChannelAccount.Channel == "linkedin",
            LeadChannelAccount.IsDeleted == False,
        )
        .order_by(LeadChannelAccount.UpdatedAt.desc())
        .first()
    )

    if not row or (not row.LinkedinCookieEnc and not (row.LinkedinUsernameEnc and row.LinkedinPasswordEnc)):
        raise HTTPException(status.HTTP_409_CONFLICT, "LinkedIn automation credentials/cookies are not configured")

    try:
        result = linkedin_bot.reply_invitation_api(
            db=db,
            account=row,
            invitation_urn=payload.invitation_urn,
            shared_secret=payload.shared_secret,
            action=payload.action,
            sender_name=payload.sender_name,
            sender_urn=payload.sender_urn,
            public_id=payload.public_id,
        )
        if not result.get("success"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, result.get("message", "Failed to process invitation"))
        return result
    except HTTPException:
        raise
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except Exception as exc:
        logger.error("Failed to reply to LinkedIn invitation: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LinkedIn reply failed: {str(exc)}")


@router.post(
    "/invitations/accept-all",
    summary="Accept all pending received LinkedIn connection requests",
)
async def accept_all_linkedin_invitations(
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    from ..social import linkedin_bot

    row = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.ClientId == company_id,
            LeadChannelAccount.Channel == "linkedin",
            LeadChannelAccount.IsDeleted == False,
        )
        .order_by(LeadChannelAccount.UpdatedAt.desc())
        .first()
    )

    if not row or (not row.LinkedinCookieEnc and not (row.LinkedinUsernameEnc and row.LinkedinPasswordEnc)):
        raise HTTPException(status.HTTP_409_CONFLICT, "LinkedIn automation credentials/cookies are not configured")

    try:
        result = await linkedin_bot.accept_all_invitations_api(db, row)
        return result
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except Exception as exc:
        logger.error("Failed to accept all LinkedIn invitations: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Batch accept failed: {str(exc)}")


# ===========================================================================
# LinkedIn Direct Messages & InMail
# ===========================================================================

class LinkedInSendMessageInput(BaseModel):
    message: str = Field(min_length=1, max_length=5000)


@router.get(
    "/conversations",
    summary="Get list of recent LinkedIn conversation threads",
)
async def get_linkedin_conversations(
    limit: int = 25,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    from ..social import linkedin_bot

    row = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.ClientId == company_id,
            LeadChannelAccount.Channel == "linkedin",
            LeadChannelAccount.IsDeleted == False,
        )
        .order_by(LeadChannelAccount.UpdatedAt.desc())
        .first()
    )

    if not row or (not row.LinkedinCookieEnc and not (row.LinkedinUsernameEnc and row.LinkedinPasswordEnc)):
        raise HTTPException(status.HTTP_409_CONFLICT, "LinkedIn automation credentials/cookies are not configured")

    try:
        conversations = await linkedin_bot.fetch_conversations_api(row, limit=limit)
        
        # Enrich conversation items with CRM Lead Candidate qualification status
        from ..models import LeadConversation, Lead
        from ..services.intent_detector import LeadIntentEvaluator
        
        thread_ids = [c.get("conversation_id") for c in conversations if c.get("conversation_id")]
        conv_rows = {}
        if thread_ids:
            conv_rows = {
                r.ExternalThreadId: r for r in db.query(LeadConversation).filter(
                    LeadConversation.ClientId == company_id,
                    LeadConversation.Channel == "linkedin",
                    LeadConversation.ExternalThreadId.in_(thread_ids),
                    LeadConversation.IsDeleted == False
                ).all()
            }
        
        conv_ids = [r.Id for r in conv_rows.values()]
        lead_rows = {}
        if conv_ids:
            lead_rows = {
                l.ConversationId: l for l in db.query(Lead).filter(
                    Lead.ClientId == company_id,
                    Lead.ConversationId.in_(conv_ids),
                    Lead.IsDeleted == False
                ).all()
            }
            
        for c in conversations:
            tid = c.get("conversation_id")
            conv = conv_rows.get(tid)
            lead = lead_rows.get(conv.Id) if conv else None
            if lead:
                c["is_lead_candidate"] = True
                c["lead_status"] = lead.Status
                c["lead_score"] = lead.Score
                c["lead_intent"] = lead.Intent
                c["crm_account_id"] = lead.ConvertedAccountId
                c["is_converted"] = bool(lead.ConvertedAccountId)
            else:
                last_msg = c.get("last_message") or ""
                eval_res = LeadIntentEvaluator.evaluate_text(last_msg, use_llm_fallback=False)
                c["is_lead_candidate"] = eval_res.is_lead
                c["lead_status"] = "warm" if eval_res.is_lead else None
                c["lead_score"] = int(eval_res.score * 100) if eval_res.is_lead else 0
                c["lead_intent"] = eval_res.category if eval_res.is_lead else None
                c["crm_account_id"] = None
                c["is_converted"] = False

                # Automatically capture DM as Lead in pipeline if commercial buying intent is detected!
                if eval_res.is_lead:
                    try:
                        captured_lead = linkedin_bot.capture_linkedin_dm_as_lead(
                            db=db,
                            account=row,
                            conversation_id=tid,
                            contact_name=c.get("contact_name"),
                            contact_urn=c.get("contact_urn"),
                            public_id=c.get("contact_public_id"),
                            profile_url=c.get("profile_url"),
                            last_message=last_msg,
                            eval_res=eval_res,
                        )
                    except Exception as auto_err:
                        logger.warning("Auto-capture DM lead error: %s", auto_err)

        return {"conversations": conversations}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except Exception as exc:
        logger.error("Failed to fetch LinkedIn conversations: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to retrieve conversations: {str(exc)}")


@router.get(
    "/conversations/{conversation_urn_id}/messages",
    summary="Get message history for a specific LinkedIn conversation thread",
)
async def get_linkedin_conversation_messages(
    conversation_urn_id: str,
    load_earlier: bool = Query(default=False),
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    from ..social import linkedin_bot

    row = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.ClientId == company_id,
            LeadChannelAccount.Channel == "linkedin",
            LeadChannelAccount.IsDeleted == False,
        )
        .order_by(LeadChannelAccount.UpdatedAt.desc())
        .first()
    )

    if not row or (not row.LinkedinCookieEnc and not (row.LinkedinUsernameEnc and row.LinkedinPasswordEnc)):
        raise HTTPException(status.HTTP_409_CONFLICT, "LinkedIn automation credentials/cookies are not configured")

    try:
        messages = await linkedin_bot.fetch_conversation_messages_api(row, conversation_urn_id, load_earlier=load_earlier)
        # Check thread messages for commercial buying intent and auto-capture if found
        from ..services.intent_detector import LeadIntentEvaluator
        for m in messages:
            if not m.get("is_self"):
                # Enrich profile URL on customer if message turn contains profile URL
                sender_url = m.get("sender_profile_url")
                if sender_url:
                    existing_conv = db.query(LeadConversation).filter(
                        LeadConversation.ClientId == company_id,
                        LeadConversation.Channel == "linkedin",
                        LeadConversation.ExternalThreadId == str(conversation_urn_id),
                        LeadConversation.IsDeleted == False,
                    ).first()
                    if existing_conv and existing_conv.CustomerId:
                        cust = db.get(LeadCustomer, existing_conv.CustomerId)
                        if cust and (not cust.LinkedinProfileUrl or ("ACoAA" in cust.LinkedinProfileUrl and "ACoAA" not in sender_url)):
                            cust.LinkedinProfileUrl = sender_url
                            cust.UpdatedAt = utcnow()
                            db.commit()

            if not m.get("is_self") and m.get("text"):
                eval_res = LeadIntentEvaluator.evaluate_text(m["text"], use_llm_fallback=False)
                if eval_res.is_lead:
                    try:
                        linkedin_bot.auto_convert_linkedin_dm_to_crm_lead(
                            db=db,
                            account=row,
                            conversation_id=conversation_urn_id,
                            contact_name=m.get("sender_name") or m.get("sender"),
                            contact_urn=m.get("sender_urn"),
                            public_id=m.get("sender_public_id"),
                            profile_url=m.get("sender_profile_url"),
                            last_message=m["text"],
                            eval_res=eval_res,
                        )
                    except Exception as auto_conv_err:
                        logger.warning("Error auto-converting message turn to CRM lead: %s", auto_conv_err)
                    break
        return {"messages": messages}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except Exception as exc:
        logger.error("Failed to fetch LinkedIn messages for thread %s: %s", conversation_urn_id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to retrieve messages: {str(exc)}")


@router.post(
    "/conversations/{conversation_urn_id}/send",
    summary="Send a message reply to a LinkedIn conversation thread",
)
async def send_linkedin_conversation_message(
    conversation_urn_id: str,
    payload: LinkedInSendMessageInput,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    from ..services import billing as billing_svc
    allowed, reason = billing_svc.check_channel_access(db, company_id, "linkedin")
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, reason)

    from ..social import linkedin_bot

    row = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.ClientId == company_id,
            LeadChannelAccount.Channel == "linkedin",
            LeadChannelAccount.IsDeleted == False,
        )
        .order_by(LeadChannelAccount.UpdatedAt.desc())
        .first()
    )

    if not row or (not row.LinkedinCookieEnc and not (row.LinkedinUsernameEnc and row.LinkedinPasswordEnc)):
        raise HTTPException(status.HTTP_409_CONFLICT, "LinkedIn automation credentials/cookies are not configured")

    try:
        result = await linkedin_bot.send_conversation_message_api(
            account=row,
            conversation_urn_id=conversation_urn_id,
            message_body=payload.message
        )
        return result
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except Exception as exc:
        logger.error("Failed to send LinkedIn message to thread %s: %s", conversation_urn_id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to send message: {str(exc)}")


@router.post(
    "/sync-messages",
    summary="Sync LinkedIn conversations & messages to LeadAI database",
)
async def sync_linkedin_messages(
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    from ..services import billing as billing_svc
    allowed, reason = billing_svc.check_channel_access(db, company_id, "linkedin")
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, reason)

    from ..social import linkedin_bot

    row = db.query(LeadChannelAccount).filter(
        LeadChannelAccount.ClientId == company_id,
        LeadChannelAccount.Channel == "linkedin",
        LeadChannelAccount.IsDeleted == False
    ).first()

    if not row or (not row.LinkedinCookieEnc and not (row.LinkedinUsernameEnc and row.LinkedinPasswordEnc)):
        raise HTTPException(status.HTTP_409_CONFLICT, "LinkedIn automation credentials/cookies are not configured")

    try:
        import asyncio
        if asyncio.iscoroutinefunction(linkedin_bot.sync_linkedin_conversations):
            result = await linkedin_bot.sync_linkedin_conversations(db, row)
        else:
            result = await asyncio.to_thread(linkedin_bot.sync_linkedin_conversations, db, row)
        return result
    except Exception as exc:
        logger.error("Failed to sync LinkedIn conversations: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to sync messages: {str(exc)}")


# ===========================================================================
# LinkedIn Comments & AI Replies Automation
# ===========================================================================

class LinkedInCommentSettingsInput(BaseModel):
    is_auto_reply_enabled: bool = False
    require_approval_for_questions: bool = True
    reply_tone: str = "thought_leadership"
    custom_instructions: Optional[str] = None
    signature_text: Optional[str] = None
    auto_capture_leads: bool = True
    min_lead_intent_threshold: float = 0.6
    exclude_keywords: list[str] = Field(default_factory=list)


class LinkedInGenerateReplyInput(BaseModel):
    custom_instruction: Optional[str] = None


class LinkedInPostReplyInput(BaseModel):
    reply_text: str = Field(min_length=1, max_length=2000)


LinkedInCommentSettingsInput.model_rebuild()
LinkedInGenerateReplyInput.model_rebuild()
LinkedInPostReplyInput.model_rebuild()


@router.get(
    "/comments/settings",
    summary="Get company's LinkedIn comment automation & AI reply settings",
)
async def get_linkedin_comment_settings(
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    from ..services.comment_reply_ai import CommentReplyAIService
    settings = CommentReplyAIService.get_or_create_settings(db, company_id, "linkedin")
    return {
        "is_auto_reply_enabled": settings.IsAutoReplyEnabled,
        "require_approval_for_questions": settings.RequireApprovalForQuestions,
        "reply_tone": settings.ReplyTone,
        "custom_instructions": settings.CustomInstructions,
        "signature_text": settings.SignatureText,
        "auto_capture_leads": settings.AutoCaptureLeads,
        "min_lead_intent_threshold": settings.MinLeadIntentThreshold,
        "exclude_keywords": settings.ExcludeKeywords or [],
    }


@router.post(
    "/comments/settings",
    summary="Update company's LinkedIn comment automation & AI reply settings",
)
async def update_linkedin_comment_settings(
    payload: LinkedInCommentSettingsInput,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    from ..services.comment_reply_ai import CommentReplyAIService
    settings = CommentReplyAIService.get_or_create_settings(db, company_id, "linkedin")
    
    settings.IsAutoReplyEnabled = payload.is_auto_reply_enabled
    settings.RequireApprovalForQuestions = payload.require_approval_for_questions
    settings.ReplyTone = payload.reply_tone
    settings.CustomInstructions = payload.custom_instructions
    settings.SignatureText = payload.signature_text
    settings.AutoCaptureLeads = payload.auto_capture_leads
    settings.MinLeadIntentThreshold = payload.min_lead_intent_threshold
    settings.ExcludeKeywords = payload.exclude_keywords
    settings.UpdatedAt = utcnow()
    
    db.commit()
    return {"ok": True, "message": "Comment automation settings saved successfully"}


@router.get(
    "/comments",
    summary="List LinkedIn post comments with AI reply suggestions & lead intent",
)
async def get_linkedin_comments(
    status_filter: Optional[str] = None,
    sentiment: Optional[str] = None,
    is_lead_only: bool = False,
    limit: int = 50,
    background_tasks: BackgroundTasks = None,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    from ..models_blog import LeadSocialComment

    active_account = db.query(LeadChannelAccount).filter(
        LeadChannelAccount.ClientId == company_id,
        LeadChannelAccount.Channel == "linkedin",
        LeadChannelAccount.IsDeleted == False,
    ).first()

    active_account = db.query(LeadChannelAccount).filter(
        LeadChannelAccount.ClientId == company_id,
        LeadChannelAccount.Channel == "linkedin",
        LeadChannelAccount.IsDeleted == False,
    ).first()

    q = db.query(LeadSocialComment).filter(
        LeadSocialComment.ClientId == company_id,
        LeadSocialComment.Channel == "linkedin",
        LeadSocialComment.IsDeleted == False,
    )

    if active_account:
        from sqlalchemy import or_
        q = q.filter(or_(LeadSocialComment.AccountId == active_account.Id, LeadSocialComment.AccountId == None))

    if status_filter:
        q = q.filter(LeadSocialComment.Status == status_filter)
    if sentiment:
        q = q.filter(LeadSocialComment.Sentiment == sentiment)
    if is_lead_only:
        q = q.filter(LeadSocialComment.IsLeadCandidate == True)

    comments = q.order_by(LeadSocialComment.CreatedAt.desc()).limit(limit).all()

    return {
        "comments": [
            {
                "id": c.Id,
                "post_urn": c.PostUrn,
                "post_title": c.PostTitle,
                "post_snippet": c.PostSnippet,
                "comment_urn": c.CommentUrn,
                "parent_comment_urn": c.ParentCommentUrn,
                "author_name": c.AuthorName,
                "author_headline": c.AuthorHeadline,
                "author_avatar": c.AuthorAvatar,
                "author_profile_url": c.AuthorProfileUrl,
                "comment_text": c.CommentText,
                "comment_created_at": c.CommentCreatedAt or c.CreatedAt,
                "sentiment": c.Sentiment,
                "intent_score": c.IntentScore,
                "is_question": c.IsQuestion,
                "is_lead_candidate": c.IsLeadCandidate,
                "suggested_reply": c.SuggestedReply,
                "suggested_reply_rationale": c.SuggestedReplyRationale,
                "status": c.Status,
                "reply_text": c.ReplyText,
                "reply_urn": c.ReplyUrn,
                "replied_at": c.RepliedAt,
                "replied_by": c.RepliedBy,
                "customer_id": c.CustomerId,
            }
            for c in comments
        ],
        "total": len(comments),
    }


@router.post(
    "/comments/{comment_id}/generate-reply",
    summary="Generate or regenerate AI contextual reply for a comment",
)
async def generate_linkedin_comment_reply(
    comment_id: str,
    payload: LinkedInGenerateReplyInput,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    from ..models_blog import LeadSocialComment
    from ..services.comment_reply_ai import CommentReplyAIService

    comment = db.query(LeadSocialComment).filter(
        LeadSocialComment.Id == comment_id,
        LeadSocialComment.ClientId == company_id,
        LeadSocialComment.IsDeleted == False,
    ).first()

    if not comment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found")

    result = CommentReplyAIService.generate_reply_for_comment(
        db=db,
        comment=comment,
        custom_instruction_override=payload.custom_instruction,
    )
    return {
        "ok": True,
        "suggested_reply": comment.SuggestedReply,
        "rationale": comment.SuggestedReplyRationale,
        "sentiment": comment.Sentiment,
        "intent_score": comment.IntentScore,
        "is_lead_candidate": comment.IsLeadCandidate,
    }


@router.post(
    "/comments/{comment_id}/reply",
    summary="Approve and post a reply to a LinkedIn comment",
)
async def post_linkedin_comment_reply(
    comment_id: str,
    payload: LinkedInPostReplyInput,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    from ..services import billing as billing_svc
    allowed, reason = billing_svc.check_channel_access(db, company_id, "linkedin")
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, reason)

    from ..models_blog import LeadSocialComment
    from ..social import linkedin as linkedin_oauth, linkedin_bot

    comment = db.query(LeadSocialComment).filter(
        LeadSocialComment.Id == comment_id,
        LeadSocialComment.ClientId == company_id,
        LeadSocialComment.IsDeleted == False,
    ).first()

    if not comment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found")

    account = db.query(LeadChannelAccount).filter(
        LeadChannelAccount.ClientId == company_id,
        LeadChannelAccount.Channel == "linkedin",
        LeadChannelAccount.IsDeleted == False,
    ).first()

    if not account:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "LinkedIn account not connected")

    reply_text = payload.reply_text.strip()
    reply_success = False
    reply_urn = None

    # Post comment reply directly via browser session automation
    # (LinkedIn's OAuth API restricts comment replies to Enterprise Partners and returns 403 ACCESS_DENIED)
    if account.LinkedinCookieEnc or (account.LinkedinUsernameEnc and account.LinkedinPasswordEnc):
        try:
            bot_res = await linkedin_bot.post_comment_reply_browser(
                account=account,
                post_urn_or_url=comment.PostUrn,
                comment_urn=comment.CommentUrn,
                reply_text=reply_text,
                target_comment_text=comment.CommentText,
                target_author=comment.AuthorName,
            )
            if bot_res.get("success"):
                reply_success = True
                reply_urn = bot_res.get("reply_urn") or f"reply-{comment.CommentUrn}"
                if bot_res.get("posted_text"):
                    reply_text = bot_res["posted_text"]
            else:
                err_msg = bot_res.get("error") or "Failed to post comment reply via browser."
                raise HTTPException(status.HTTP_400_BAD_REQUEST, err_msg)
        except HTTPException:
            raise
        except Exception as bot_exc:
            logger.error(f"Browser comment reply failed: {bot_exc}")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Comment reply failed: {bot_exc}")
    else:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "LinkedIn session token (li_at) is required to post comment replies. Please connect your session cookie in the Connection tab."
        )

    if not reply_success:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not post reply. Verify LinkedIn connection credentials.")

    comment.Status = "replied"
    comment.ReplyText = reply_text
    comment.ReplyUrn = reply_urn
    comment.RepliedAt = utcnow()
    comment.RepliedBy = "operator"
    comment.UpdatedAt = utcnow()
    db.commit()

    return {"ok": True, "message": "Reply posted successfully to LinkedIn", "reply_urn": reply_urn}


@router.post(
    "/comments/{comment_id}/ignore",
    summary="Ignore a comment from the review queue",
)
async def ignore_linkedin_comment(
    comment_id: str,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    from ..models_blog import LeadSocialComment

    comment = db.query(LeadSocialComment).filter(
        LeadSocialComment.Id == comment_id,
        LeadSocialComment.ClientId == company_id,
        LeadSocialComment.IsDeleted == False,
    ).first()

    if not comment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found")

    comment.Status = "ignored"
    comment.UpdatedAt = utcnow()
    db.commit()
    return {"ok": True, "message": "Comment marked as ignored"}


@router.post(
    "/comments/{comment_id}/capture-lead",
    summary="Convert commenter into a CRM LeadCustomer",
)
async def capture_comment_lead(
    comment_id: str,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    from ..models_blog import LeadSocialComment
    from ..services.comment_reply_ai import CommentReplyAIService

    comment = db.query(LeadSocialComment).filter(
        LeadSocialComment.Id == comment_id,
        LeadSocialComment.ClientId == company_id,
        LeadSocialComment.IsDeleted == False,
    ).first()

    if not comment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found")

    customer = CommentReplyAIService.capture_commenter_as_lead(db, comment)
    return {
        "ok": True,
        "message": f"Successfully captured {comment.AuthorName} as a CRM Lead",
        "customer_id": customer.Id if customer else None,
        "display_name": customer.DisplayName if customer else comment.AuthorName,
        "linkedin_profile_url": getattr(customer, "LinkedinProfileUrl", None) if customer else comment.AuthorProfileUrl,
    }





@router.post(
    "/comments/sync",
    summary="Poll LinkedIn for new comments on posts and generate AI replies",
)
async def sync_linkedin_comments(
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    from ..services import billing as billing_svc
    allowed, reason = billing_svc.check_channel_access(db, company_id, "linkedin")
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, reason)

    from ..social import linkedin_bot

    account = db.query(LeadChannelAccount).filter(
        LeadChannelAccount.ClientId == company_id,
        LeadChannelAccount.Channel == "linkedin",
        LeadChannelAccount.IsDeleted == False,
    ).first()

    if not account or (not account.LinkedinCookieEnc and not (account.LinkedinUsernameEnc and account.LinkedinPasswordEnc)):
        raise HTTPException(status.HTTP_409_CONFLICT, "LinkedIn automation credentials/cookies are not configured")

    try:
        result = await linkedin_bot.fetch_recent_posts_and_comments_browser(db, account, limit_posts=2)
        if result.get("error"):
            err_str = result.get("error", "")
            if "ERR_TOO_MANY_REDIRECTS" in err_str or "auth" in err_str.lower() or "login" in err_str.lower():
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED,
                    "LinkedIn session token (li_at) is expired or invalid. Please copy a fresh session cookie from your browser and paste it in the Connection tab."
                )
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Comment sync failed: {err_str}")

        return {
            "ok": True,
            "message": f"Scanned recent posts. Synced {result.get('synced_comments', 0)} new comments, identified {result.get('new_leads', 0)} high-intent leads.",
            "data": result,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to sync LinkedIn comments: {exc}")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Comment sync failed: {exc}")



