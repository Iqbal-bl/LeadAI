"""
LinkedIn integration router for LeadAI.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException, Request, status, BackgroundTasks
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from .. import activity
from ..activity import A
from ..db import get_leadai_db
from ..models import LeadChannelAccount, utcnow
from ..rbac import Principal, assert_owns, scoped
from ..security import encrypt_pii

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/linkedin", tags=["LeadAI • LinkedIn"])

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

    principal, client_id = scope
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
    cred = db.query(LeadChannelAccount).filter(
        LeadChannelAccount.ClientId == client_id,
        LeadChannelAccount.Channel == "linkedin",
        LeadChannelAccount.IsDeleted == False
    ).first()

    if not cred or not cred.AccessTokenEnc:
        return {"connected": False}

    now = utcnow()
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)

    access_token_valid = (
        cred.TokenExpiresAt > now
        if cred.TokenExpiresAt
        else False
    )

    meta = cred.MetaJson or {}

    return {
        "connected": True,
        "person_urn": cred.ExternalId,
        "access_token_valid": access_token_valid,
        "has_refresh_token": bool(cred.AppSecretEnc),
        "auto_accept": meta.get("linkedin_auto_accept", False),
        "welcome_message": meta.get("linkedin_welcome_message")
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

    principal, client_id = scope
    cred = db.query(LeadChannelAccount).filter(
        LeadChannelAccount.ClientId == client_id,
        LeadChannelAccount.Channel == "linkedin",
        LeadChannelAccount.IsDeleted == False
    ).first()

    if cred:
        cred.IsDeleted = True
        cred.UpdatedAt = utcnow()
        
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
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope

    # Find the corresponding LeadChannelAccount row
    row = db.query(LeadChannelAccount).filter(
        LeadChannelAccount.ClientId == company_id,
        LeadChannelAccount.Channel == "linkedin"
    ).first()

    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "LinkedIn channel account not found. Connect OAuth first.")

    # Encrypt and save the credentials
    if payload.cookie_li_at:
        row.LinkedinCookieEnc = encrypt_pii(payload.cookie_li_at)
        row.LinkedinUsernameEnc = None
        row.LinkedinPasswordEnc = None
    else:
        row.LinkedinCookieEnc = None
        row.LinkedinUsernameEnc = encrypt_pii(payload.username)
        row.LinkedinPasswordEnc = encrypt_pii(payload.password)

    row.UpdatedAt = utcnow()
    db.commit()
    return {"ok": True}


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
    from ..social import linkedin_bot

    # Retrieve credentials from database
    row = db.query(LeadChannelAccount).filter(
        LeadChannelAccount.ClientId == company_id,
        LeadChannelAccount.Channel == "linkedin"
    ).first()

    if not row or (not row.LinkedinCookieEnc and not (row.LinkedinUsernameEnc and row.LinkedinPasswordEnc)):
        raise HTTPException(status.HTTP_409_CONFLICT, "LinkedIn search credentials/cookies are not configured")

    try:
        profiles = await linkedin_bot.search_profiles_api(row, payload.keywords, limit=payload.limit)
        return {"profiles": profiles}
    except Exception as exc:
        logger.error("LinkedIn profile search failed: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LinkedIn search failed: {str(exc)}")


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
    _, company_id = scope
    from ..social import linkedin_bot

    # Retrieve credentials from database
    row = db.query(LeadChannelAccount).filter(
        LeadChannelAccount.ClientId == company_id,
        LeadChannelAccount.Channel == "linkedin"
    ).first()

    if not row or (not row.LinkedinCookieEnc and not (row.LinkedinUsernameEnc and row.LinkedinPasswordEnc)):
        raise HTTPException(status.HTTP_409_CONFLICT, "LinkedIn automation credentials/cookies are not configured")

    try:
        profiles_dict = [p.model_dump() for p in payload.profiles]
        background_tasks.add_task(
            linkedin_bot.send_connection_invitations_api,
            row,
            profiles_dict,
            payload.message
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


@router.post(
    "/settings",
    summary="Update LinkedIn settings (auto-accept, welcome message)",
)
async def save_linkedin_settings(
    payload: LinkedInSettingsInput,
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    _, company_id = scope
    row = db.query(LeadChannelAccount).filter(
        LeadChannelAccount.ClientId == company_id,
        LeadChannelAccount.Channel == "linkedin",
        LeadChannelAccount.IsDeleted == False
    ).first()

    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "LinkedIn channel account not found. Connect OAuth first.")

    meta = row.MetaJson or {}
    meta["linkedin_auto_accept"] = payload.auto_accept
    meta["linkedin_welcome_message"] = payload.welcome_message
    row.MetaJson = meta
    row.UpdatedAt = utcnow()
    db.commit()
    return {"ok": True}


@router.post(
    "/sync-invitations",
    summary="Trigger immediate LinkedIn connection request sync",
)
async def trigger_linkedin_sync(
    scope: tuple[Principal, str] = Depends(scoped("social.linkedin")),
    db: Session = Depends(get_leadai_db),
):
    from ..services import jobs
    _, company_id = scope
    
    # Enqueue a job to run immediately for this company
    jobs.enqueue(
        db, 
        "linkedin.process_invitations", 
        payload={"company_id": company_id}, 
        commit=True
    )
    return {"ok": True, "message": "LinkedIn connection request sync queued successfully"}
