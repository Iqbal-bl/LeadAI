import logging
import os
import secrets
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List
import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from ..config import settings
from ..security import encrypt_pii, decrypt_pii
from ..models import utcnow
from ..models_ext import LeadChannelAccount

logger = logging.getLogger("leadai.social.linkedin")

# Constant endpoints and details for LinkedIn
AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
UGC_POSTS_URL = "https://api.linkedin.com/v2/ugcPosts"
REGISTER_UPLOAD_URL = "https://api.linkedin.com/v2/assets?action=registerUpload"
IMAGES_INIT_UPLOAD_URL = "https://api.linkedin.com/rest/images?action=initializeUpload"
POSTS_URL = "https://api.linkedin.com/rest/posts"
SCOPES = "openid profile email w_member_social"

_TIMEOUT = httpx.Timeout(30.0)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(4),
    wait=wait_exponential_jitter(initial=1, max=20),
    reraise=True,
)
async def request_with_retry(method: str, url: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=_TIMEOUT, verify=False) as client:
        response = await client.request(method, url, **kwargs)
        if response.status_code == 429 or response.status_code >= 500:
            logger.warning(
                "LinkedIn API returned retryable status",
                extra={"status_code": response.status_code, "url": url},
            )
            response.raise_for_status()
        return response


# ---------- OAuth and Token persistence ----------

async def build_authorize_url(db, client_id: str) -> str:
    from urllib.parse import urlencode
    from ..services import cache
    state = secrets.token_urlsafe(24)
    
    # Save the OAuth state mapping to cache (expires in 10 minutes)
    cache.set_value(f"leadai:linkedinlogin:{state}", client_id, ttl=600)

    redirect_uri = settings.linkedin_redirect_uri or f"{os.getenv('SERVER_URL', 'http://localhost:5050').rstrip('/')}/api/leadai/linkedin/callback"
    params = {
        "response_type": "code",
        "client_id": settings.linkedin_client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def consume_oauth_state(db, state: str, ttl_seconds: int = 600) -> Optional[str]:
    from ..services import cache
    client_id = cache.get_value(f"leadai:linkedinlogin:{state}")
    if not client_id:
        return None
    
    # Single-use: overwrite with 1-second TTL to consume
    cache.set_value(f"leadai:linkedinlogin:{state}", "", ttl=1)
    return client_id


async def exchange_code_for_tokens(code: str) -> dict:
    redirect_uri = settings.linkedin_redirect_uri or f"{os.getenv('SERVER_URL', 'http://localhost:5050').rstrip('/')}/api/leadai/linkedin/callback"
    resp = await request_with_retry(
        "POST",
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": settings.linkedin_client_id,
            "client_secret": settings.linkedin_client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code != 200:
        raise Exception(f"Token exchange failed: {resp.text}")
    return resp.json()


async def refresh_access_token(refresh_token: str) -> dict:
    resp = await request_with_retry(
        "POST",
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.linkedin_client_id,
            "client_secret": settings.linkedin_client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code != 200:
        raise Exception(f"Token refresh failed: {resp.text}")
    return resp.json()


async def fetch_person_urn(access_token: str) -> str:
    resp = await request_with_retry(
        "GET",
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if resp.status_code != 200:
        raise Exception(f"Failed to fetch user info: {resp.text}")
    data = resp.json()
    return f"urn:li:person:{data['sub']}"


async def save_tokens(
    db,
    client_id: str,
    person_urn: str,
    access_token: str,
    expires_in_seconds: int,
    refresh_token: Optional[str] = None,
    refresh_token_expires_in_seconds: Optional[int] = None,
) -> None:
    from datetime import datetime, timezone
    
    # Check if an account already exists for this channel and ExternalId
    db_cred = (
        db.query(LeadChannelAccount)
        .filter(
            LeadChannelAccount.Channel == "linkedin",
            LeadChannelAccount.ExternalId == person_urn,
        )
        .first()
    )

    if not db_cred:
        # Fallback to checking by ClientId and Channel
        db_cred = (
            db.query(LeadChannelAccount)
            .filter(
                LeadChannelAccount.ClientId == client_id,
                LeadChannelAccount.Channel == "linkedin",
            )
            .first()
        )

    now = time.time()
    access_expires_at = datetime.fromtimestamp(now + expires_in_seconds, tz=timezone.utc).replace(tzinfo=None)

    refresh_token_enc = None
    meta_json = {}
    if refresh_token:
        refresh_token_enc = encrypt_pii(refresh_token)
        if refresh_token_expires_in_seconds:
            meta_json["refresh_token_expires_at"] = now + refresh_token_expires_in_seconds
    elif db_cred:
        refresh_token_enc = db_cred.AppSecretEnc
        meta_json = db_cred.MetaJson or {}

    if not db_cred:
        db_cred = LeadChannelAccount(
            ClientId=client_id,
            Channel="linkedin",
            Provider="linkedin",
            LoginType="linkedin",
            Name="LinkedIn Account",
            IsActive=True,
            CreatedBy="system"
        )
        db.add(db_cred)

    db_cred.ClientId = client_id
    db_cred.ExternalId = person_urn
    db_cred.AccessTokenEnc = encrypt_pii(access_token)
    db_cred.TokenExpiresAt = access_expires_at
    db_cred.AppSecretEnc = refresh_token_enc
    db_cred.MetaJson = meta_json
    db_cred.IsDeleted = False
    db_cred.IsActive = True
    db_cred.UpdatedAt = utcnow()
    db.commit()


async def get_valid_access_token(db, client_id: str) -> str:
    from datetime import datetime, timezone
    
    db_cred = db.query(LeadChannelAccount).filter(
        LeadChannelAccount.ClientId == client_id,
        LeadChannelAccount.Channel == "linkedin",
        LeadChannelAccount.IsDeleted == False
    ).first()

    if not db_cred or not db_cred.AccessTokenEnc:
        raise Exception("No LinkedIn credentials found. Visit /linkedin/connect first.")

    access_token = decrypt_pii(db_cred.AccessTokenEnc)
    expires_at = db_cred.TokenExpiresAt

    # Use 60s buffer
    now = time.time()
    if expires_at and expires_at.timestamp() > (now + 60):
        return access_token

    # Token expired, try refreshing
    refresh_token = decrypt_pii(db_cred.AppSecretEnc) if db_cred.AppSecretEnc else None
    if not refresh_token:
        raise Exception("Access token expired and no refresh token is stored. Visit connect again.")

    logger.info("LinkedIn access token expired for company %s, refreshing", client_id)
    new_tokens = await refresh_access_token(refresh_token)
    await save_tokens(
        db=db,
        client_id=client_id,
        person_urn=db_cred.ExternalId,
        access_token=new_tokens["access_token"],
        expires_in_seconds=new_tokens["expires_in"],
        refresh_token=new_tokens.get("refresh_token"),
        refresh_token_expires_in_seconds=new_tokens.get("refresh_token_expires_in"),
    )
    return new_tokens["access_token"]


async def _download_url_bytes(url: str) -> bytes:
    # If the URL points to the public MinIO endpoint, rewrite it to download
    # from the local MinIO IP/port to avoid local DNS or NAT loopback timeouts.
    public_endpoint = os.getenv("MINIO_PUBLIC_ENDPOINT", "").strip().rstrip("/")
    local_endpoint = os.getenv("MINIO_ENDPOINT", "").strip().rstrip("/")
    if public_endpoint and local_endpoint and url.startswith(public_endpoint):
        url = url.replace(public_endpoint, local_endpoint, 1)
        logger.info("LinkedIn media download routed internally to: %s", url)

    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.get(url, timeout=60.0)
        resp.raise_for_status()
        return resp.content


async def create_text_post(access_token: str, person_urn: str, text: str) -> dict:
    body = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    return await _post_ugc(access_token, body)


async def register_image_upload(access_token: str, person_urn: str) -> dict:
    body = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": person_urn,
            "serviceRelationships": [
                {"relationshipType": "OWNER", "identifier": "urn:li:userGeneratedContent"}
            ],
        }
    }
    resp = await request_with_retry(
        "POST",
        REGISTER_UPLOAD_URL,
        json=body,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    if resp.status_code != 200:
        raise Exception(f"Image upload registration failed: {resp.text}")
    data = resp.json()
    upload_mechanism = data["value"]["uploadMechanism"][
        "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
    ]
    return {"upload_url": upload_mechanism["uploadUrl"], "asset_urn": data["value"]["asset"]}


async def upload_image_binary(access_token: str, upload_url: str, image_bytes: bytes) -> None:
    resp = await request_with_retry(
        "PUT",
        upload_url,
        content=image_bytes,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if resp.status_code not in (200, 201):
        raise Exception(f"Image binary upload failed: {resp.text}")


async def create_image_post(
    access_token: str, person_urn: str, text: str, asset_urn: str, image_title: str = "Image"
) -> dict:
    body = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "IMAGE",
                "media": [
                    {
                        "status": "READY",
                        "description": {"text": image_title},
                        "media": asset_urn,
                        "title": {"text": image_title},
                    }
                ],
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    return await _post_ugc(access_token, body)


async def post_text_and_image(
    access_token: str, person_urn: str, text: str, image_bytes: bytes, image_title: str = "Image"
) -> dict:
    reg = await register_image_upload(access_token, person_urn)
    await upload_image_binary(access_token, reg["upload_url"], image_bytes)
    return await create_image_post(access_token, person_urn, text, reg["asset_urn"], image_title)


async def initialize_image_upload(access_token: str, person_urn: str) -> dict:
    body = {"initializeUploadRequest": {"owner": person_urn}}
    resp = await request_with_retry(
        "POST",
        IMAGES_INIT_UPLOAD_URL,
        json=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "LinkedIn-Version": settings.linkedin_api_version,
            "X-Restli-Protocol-Version": "2.0.0",
        },
    )
    if resp.status_code != 200:
        raise Exception(f"Image init upload failed: {resp.text}")
    data = resp.json()["value"]
    return {"upload_url": data["uploadUrl"], "image_urn": data["image"]}


async def create_multi_image_post(
    access_token: str, person_urn: str, text: str, image_urns: List[str]
) -> dict:
    body = {
        "author": person_urn,
        "commentary": text,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "content": {"multiImage": {"images": [{"id": urn} for urn in image_urns]}},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    resp = await request_with_retry(
        "POST",
        POSTS_URL,
        json=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "LinkedIn-Version": settings.linkedin_api_version,
            "X-Restli-Protocol-Version": "2.0.0",
        },
    )
    if resp.status_code != 201:
        raise Exception(f"Multi-image post failed: {resp.text}")
    return {"post_id": resp.headers.get("x-restli-id", ""), "status_code": resp.status_code}


async def post_multiple_images(
    access_token: str, person_urn: str, text: str, images: List[bytes]
) -> dict:
    image_urns = []
    for image_bytes in images:
        reg = await initialize_image_upload(access_token, person_urn)
        await upload_image_binary(access_token, reg["upload_url"], image_bytes)
        image_urns.append(reg["image_urn"])
    return await create_multi_image_post(access_token, person_urn, text, image_urns)


async def _post_ugc(access_token: str, body: dict) -> dict:
    resp = await request_with_retry(
        "POST",
        UGC_POSTS_URL,
        json=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        },
    )
    if resp.status_code != 201:
        raise Exception(f"Post failed: {resp.text}")
    return {"post_id": resp.headers.get("x-restli-id", ""), "status_code": resp.status_code}


async def post_to_linkedin(
    access_token: str, person_urn: str, caption: str, uploaded: list[dict], media_shape: str
) -> dict:
    if media_shape == "none":
        return await create_text_post(access_token, person_urn, caption)

    if media_shape == "single_image":
        image_bytes = await _download_url_bytes(uploaded[0]["url"])
        return await post_text_and_image(access_token, person_urn, caption, image_bytes)

    if media_shape == "multi_image":
        image_bytes_list = []
        for m in uploaded:
            b = await _download_url_bytes(m["url"])
            image_bytes_list.append(b)
        return await post_multiple_images(access_token, person_urn, caption, image_bytes_list)

    raise ValueError(f"LinkedIn does not support media shape: {media_shape}")
