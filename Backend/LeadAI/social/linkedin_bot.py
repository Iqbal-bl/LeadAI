import os
import re
import logging
import asyncio
import random
import time
from typing import List, Optional, Any, Dict, Union
from linkedin_api import Linkedin
from ..security import decrypt_pii, encrypt_pii
from ..models import utcnow
from ..services.intent_detector import LeadIntentEvaluator, IntentEvaluationResult

_utcnow = utcnow

logger = logging.getLogger("leadai.social.linkedin_bot")

def get_linkedin_client(account) -> Linkedin:
    """Initialize tomquirk's Linkedin API client using cookies or credentials."""
    import requests
    import time
    import random

    cookie = decrypt_pii(account.LinkedinCookieEnc) if account.LinkedinCookieEnc else None
    username = decrypt_pii(account.LinkedinUsernameEnc) if account.LinkedinUsernameEnc else None
    password = decrypt_pii(account.LinkedinPasswordEnc) if account.LinkedinPasswordEnc else None

    # Ensure the directory path ends with a separator so the library doesn't concatenate the username onto the directory name
    cookies_dir = os.path.join(os.getcwd(), ".linkedin_cookies") + os.path.sep
    os.makedirs(cookies_dir, exist_ok=True)

    if cookie:
        logger.info("Initializing LinkedIn Bot API client using session cookie (li_at)")
        stored_li_at = cookie
        stored_jsessionid = None
        
        # Check if stored as composite
        if "|||" in cookie:
            parts = cookie.split("|||", 1)
            stored_li_at = parts[0]
            stored_jsessionid = parts[1]
        elif cookie.startswith("{") and "li_at" in cookie:
            import json
            try:
                cdata = json.loads(cookie)
                stored_li_at = cdata.get("li_at", cookie)
                stored_jsessionid = cdata.get("jsessionid")
            except Exception:
                pass

        session = requests.Session()
        session.headers.update({
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "accept-language": "en-US,en;q=0.9",
            "x-li-lang": "en_US",
            "x-restli-protocol-version": "2.0.0",
        })

        if not stored_jsessionid:
            try:
                session.get("https://www.linkedin.com", timeout=6)
            except Exception:
                pass

        expires = int(time.time()) + 365 * 24 * 3600
        session.cookies.set("li_at", stored_li_at, domain=".linkedin.com", path="/", expires=expires)

        jsessionid = stored_jsessionid or session.cookies.get("JSESSIONID")
        if not jsessionid:
            jsessionid = f"ajax:{random.randint(100000000000000000, 999999999999999999)}"

        clean_csrf = jsessionid.replace('"', '').strip()
        session.cookies.set("JSESSIONID", f'"{clean_csrf}"', domain=".linkedin.com", path="/", expires=expires)
        session.headers["csrf-token"] = clean_csrf

        api = Linkedin("session_user", "session_pass", authenticate=False, cookies_dir=cookies_dir)
        api.client.session = session

        # Intercept redirect loops on expired or invalid session tokens
        orig_send = api.client.session.send
        def safe_send(request, **kwargs):
            try:
                res = orig_send(request, **kwargs)
                # Check if redirected to login / authwall / checkpoint
                redirect_chain = list(res.history) + [res] if hasattr(res, "history") else [res]
                for r in redirect_chain:
                    loc = (r.headers.get("Location") or "").lower()
                    req_url = (r.url or "").lower()
                    if any(x in loc or x in req_url for x in ["/login", "/authwall", "/checkpoint", "uas/login", "uas/authenticate"]):
                        raise RuntimeError("Your LinkedIn session (li_at) has expired or was revoked by LinkedIn. Please enter a fresh li_at token or save your credentials in the Connection tab.")
                return res
            except requests.exceptions.TooManyRedirects:
                raise RuntimeError("Your LinkedIn session (li_at) has expired or is invalid (caused too many redirects). Please enter a fresh li_at token in the Connection tab.")

        api.client.session.send = safe_send
        return api
    elif username and password:
        logger.info("Session cookie not found. Attempting client login for %s", username)
        try:
            from linkedin_api.client import ChallengeException
            return Linkedin(username=username, password=password, cookies_dir=cookies_dir)
        except ChallengeException:
            raise RuntimeError("LinkedIn triggered a security check (CHALLENGE/CAPTCHA) on your account for automated login. Please switch to 'Mode B: Session Token (li_at)' and paste your li_at token directly.")
        except Exception as exc:
            raise RuntimeError(f"LinkedIn authentication failed: {str(exc)}. Please connect using 'Mode B: Session Token (li_at)'.")
    else:
        raise ValueError("LinkedIn session credentials are not configured. Please enter your session token (li_at) in the Connection tab.")


async def extract_session_cookie_via_browser(username: str, password: str) -> Optional[str]:
    """Automate headless browser login to extract the li_at session cookie from username/password."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("Playwright is not installed. Skipping browser session extraction.")
        return None

    try:
        logger.info("Attempting automated browser session extraction for %s", username)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                ]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            page = await context.new_page()
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

            await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(1)

            email_loc = page.locator("#username, input[type='email']:visible, input[name='session_key']:visible, input[autocomplete='username']:visible").first
            pass_loc = page.locator("#password, input[type='password']:visible, input[name='session_password']:visible, input[autocomplete='current-password']:visible").first

            if await email_loc.count() > 0 and await pass_loc.count() > 0:
                await email_loc.fill(username)
                await asyncio.sleep(0.3)
                await pass_loc.fill(password)
                await asyncio.sleep(0.3)
                
                submit_btn = page.locator("button[type='submit']:visible, button[data-litms-control-urn='login-submit']:visible").first
                if await submit_btn.count() > 0:
                    await submit_btn.click()
                else:
                    await pass_loc.press("Enter")

                # Wait up to 12 seconds for session establishment
                for _ in range(12):
                    await asyncio.sleep(1)
                    cookies = await context.cookies()
                    li_at = next((c["value"] for c in cookies if c["name"] == "li_at"), None)
                    jsessionid = next((c["value"] for c in cookies if c["name"] == "JSESSIONID"), None)
                    if li_at:
                        logger.info("Successfully extracted li_at & JSESSIONID via browser for %s", username)
                        await browser.close()
                        if jsessionid:
                            return f"{li_at}|||{jsessionid}"
                        return li_at

            await browser.close()
            return None
    except Exception as exc:
        logger.warning("Browser automated session extraction encountered: %s", exc)
        return None


async def generate_search_keywords(prompt: str) -> str:
    """Use the OpenAI model via complete_json to build simple search keywords."""
    from ..services.llm import complete_json

    system_prompt = (
        "You are an expert LinkedIn search query assistant.\n"
        "Your task is to convert a natural language description of target profiles "
        "into a list of key search terms for the LinkedIn search bar.\n"
        "Rules:\n"
        "1. Avoid complex Boolean operators (like AND, OR, NOT, or parentheses) as they cause search failure.\n"
        "2. Keep the query concise, outputting simple space-separated keywords/titles (e.g., 'Software Engineer Python').\n"
        "3. Output ONLY a JSON object in this format:\n"
        "{\n"
        "  \"keywords\": \"Simple search terms here\"\n"
        "}\n"
    )
    
    messages = [{"role": "user", "content": f"Description: {prompt}"}]
    try:
        result, _ = complete_json(system_prompt, messages)
        if result and "keywords" in result:
            return result["keywords"]
    except Exception as exc:
        logger.warning("LLM keyword generation failed: %s", exc)
    
    # Fallback to exact phrase
    return f'"{prompt}"'


async def search_profiles_api(account, keywords: str, limit: int = 15) -> List[dict]:
    """Perform people search on LinkedIn using the unofficial api wrapper."""
    api = get_linkedin_client(account)
    
    def _search():
        results = api.search_people(keywords=keywords, limit=limit, include_private_profiles=True)
        profiles = []
        for r in results:
            urn_id = r.get("urn_id")
            name = r.get("name", "")
            headline = r.get("jobtitle", "")
            location = r.get("location", "")
            
            if urn_id:
                profiles.append({
                    "public_id": urn_id,
                    "urn_id": urn_id,
                    "name": name,
                    "headline": headline,
                    "location": location,
                })
        return profiles

    return await asyncio.to_thread(_search)


async def send_connection_invitations_api(account, profiles: List[dict], message: Optional[str] = None) -> dict:
    """Send connection requests to a list of public IDs/URNs with sleep delays to mimic human behavior."""
    api = get_linkedin_client(account)
    
    last_response = None
    original_post = api._post

    def patched_post(*args, **kwargs):
        nonlocal last_response
        res = original_post(*args, **kwargs)
        last_response = res
        return res

    api._post = patched_post

    def _send_invitation(profile_id: str, urn_id: Optional[str] = None):
        nonlocal last_response
        try:
            p_urn = urn_id or profile_id
            if p_urn and ":" in p_urn:
                p_urn = p_urn.split(":")[-1]
                
            last_response = None
            # tomquirk's signature: add_connection(self, profile_public_id, message='', profile_urn=None)
            # Returns True if error occurred, False if successful
            is_error = api.add_connection(profile_public_id=profile_id, message=message or "", profile_urn=p_urn)
            if is_error:
                error_msg = "Failed to send connection request"
                if last_response is not None:
                    try:
                        error_data = last_response.json()
                        if "message" in error_data:
                            error_msg = error_data["message"]
                        elif "exceptionClass" in error_data:
                            error_msg = f"{error_data.get('exceptionClass')}: {error_data.get('message')}"
                        else:
                            error_msg = f"LinkedIn Error {last_response.status_code}: {last_response.text}"
                    except Exception:
                        error_msg = f"LinkedIn Error {last_response.status_code}: {last_response.text}"
                return {"success": False, "message": error_msg}
            return {"success": True, "message": "Invitation sent successfully"}
        except Exception as exc:
            logger.error("Failed to send connection to %s: %s", profile_id, exc)
            return {"success": False, "message": str(exc)}

    results = {}
    for p in profiles:
        pid = p.get("public_id")
        urn_id = p.get("urn_id")
        if not pid:
            continue
        res = await asyncio.to_thread(_send_invitation, pid, urn_id)
        results[pid] = res
        # Sleep random 6.0 to 12.0s between requests to mimic human behavior and avoid rate limits
        import random
        await asyncio.sleep(random.uniform(6.0, 12.0))
        
    return results


def parse_invitation(invite: dict) -> dict | None:
    """Safely parse LinkedIn invitation data object into a standardized dictionary."""
    entity_urn = invite.get("entityUrn")
    shared_secret = invite.get("sharedSecret")
    if not entity_urn or not shared_secret:
        return None

    # Extract sender details
    from_member = invite.get("fromMember", {}) or invite.get("sender", {}) or invite.get("miniProfile", {})
    first_name = from_member.get("firstName") or from_member.get("miniProfile", {}).get("firstName") or invite.get("sender", {}).get("firstName", "")
    if isinstance(first_name, dict):
        first_name = first_name.get("text", "") or ""
    last_name = from_member.get("lastName") or from_member.get("miniProfile", {}).get("lastName") or invite.get("sender", {}).get("lastName", "")
    if isinstance(last_name, dict):
        last_name = last_name.get("text", "") or ""
    display_name = f"{first_name} {last_name}".strip() or "LinkedIn Member"
    
    sender_urn = (
        from_member.get("entityUrn") 
        or from_member.get("miniProfile", {}).get("entityUrn") 
        or invite.get("sender", {}).get("entityUrn") 
        or invite.get("fromMemberUrn")
    )
    public_id = (
        from_member.get("publicIdentifier") 
        or from_member.get("miniProfile", {}).get("publicIdentifier") 
        or invite.get("sender", {}).get("publicIdentifier")
    )
    headline = (
        from_member.get("occupation")
        or from_member.get("headline")
        or from_member.get("miniProfile", {}).get("occupation")
        or ""
    )
    if isinstance(headline, dict):
        headline = headline.get("text", "") or ""

    message_text = invite.get("message") or invite.get("customMessage") or ""
    sent_time = invite.get("sentTime")

    return {
        "invitation_urn": entity_urn,
        "shared_secret": shared_secret,
        "sender_urn": str(sender_urn) if sender_urn else None,
        "public_id": public_id,
        "name": display_name,
        "headline": headline,
        "message": message_text,
        "sent_time": sent_time,
    }


def get_raw_invitations(api: Linkedin, limit: int = 50) -> list[dict]:
    """Fetch invitations directly from LinkedIn with retry and payload parsing."""
    params = {
        "start": 0,
        "count": limit,
        "includeInsights": True,
        "q": "receivedInvitation",
    }
    res = api._fetch("/relationships/invitationViews", params=params)
    if res.status_code == 401:
        logger.warning("LinkedIn 401 during invitation fetch; attempting retry")
        res = api._fetch("/relationships/invitationViews", params=params)

    if res.status_code != 200:
        logger.warning("LinkedIn invitationViews returned status %s: %s", res.status_code, res.text[:200])
        return []

    try:
        payload = res.json()
        elements = payload.get("elements", [])
        invitations = []
        for element in elements:
            if isinstance(element, dict) and "invitation" in element:
                invitations.append(element["invitation"])
            else:
                invitations.append(element)
        return invitations
    except Exception as e:
        logger.error("Failed to parse invitations JSON: %s", e)
        return []


async def fetch_received_invitations_api(account, limit: int = 50) -> list[dict]:
    """Fetch pending received invitations from LinkedIn."""
    api = get_linkedin_client(account)
    def _fetch():
        invites = get_raw_invitations(api, limit=limit)
        results = []
        for inv in invites:
            parsed = parse_invitation(inv)
            if parsed:
                results.append(parsed)
        return results
    return await asyncio.to_thread(_fetch)



def find_or_link_linkedin_customer(
    db,
    client_id: str,
    channel_account_id: str,
    sender_urn: Optional[str] = None,
    public_id: Optional[str] = None,
    profile_url: Optional[str] = None,
    display_name: Optional[str] = None,
    conversation_id: Optional[str] = None,
    created_by: str = "linkedin_bot",
) -> tuple[Any, Any]:
    """Find an existing LeadCustomer across URNs, member tokens, profile URLs, and threads.
    
    If found, ensures the new URN/token is linked to the SAME customer in LeadChannelIdentity,
    preventing duplicate customer records across DMs, Connection Requests, and Comments.
    """
    import random
    from ..models import LeadCustomer, LeadAccount, utcnow
    from ..models_ext import LeadChannelIdentity
    from ..security import encrypt_pii
    from ..services.crm import extract_linkedin_token, extract_linkedin_slug

    urn_token = extract_linkedin_token(sender_urn)
    slug = public_id or extract_linkedin_slug(profile_url)
    display_name = (display_name or "LinkedIn Member").strip()

    identity = None
    customer = None

    # 1. Exact match on ExternalUserId
    if sender_urn:
        identity = db.query(LeadChannelIdentity).filter(
            LeadChannelIdentity.ClientId == client_id,
            LeadChannelIdentity.Channel == "linkedin",
            LeadChannelIdentity.ExternalUserId == str(sender_urn),
            LeadChannelIdentity.IsDeleted == False
        ).first()
        if identity:
            customer = db.get(LeadCustomer, identity.CustomerId)

    # 2. Token match on ExternalUserId (bridges fsd_profile and fs_miniProfile)
    if not customer and urn_token and len(urn_token) >= 5:
        identity_by_token = db.query(LeadChannelIdentity).filter(
            LeadChannelIdentity.ClientId == client_id,
            LeadChannelIdentity.Channel == "linkedin",
            LeadChannelIdentity.ExternalUserId.like(f"%{urn_token}%"),
            LeadChannelIdentity.IsDeleted == False
        ).first()
        if identity_by_token:
            customer = db.get(LeadCustomer, identity_by_token.CustomerId)
            identity = identity_by_token

    # 3. Match by vanity slug or profile URL in existing CRM accounts or customers
    if not customer and slug and len(slug) >= 3:
        account_match = db.query(LeadAccount).filter(
            LeadAccount.ClientId == client_id,
            LeadAccount.LinkedinProfileUrl.like(f"%{slug}%"),
            LeadAccount.IsDeleted == False
        ).first()
        if account_match and account_match.CustomerId:
            customer = db.get(LeadCustomer, account_match.CustomerId)
        if not customer:
            customer = db.query(LeadCustomer).filter(
                LeadCustomer.ClientId == client_id,
                LeadCustomer.LinkedinProfileUrl.like(f"%{slug}%"),
                LeadCustomer.IsDeleted == False
            ).first()

    # 4. Check if conversation_id already exists in LeadConversation
    if not customer and conversation_id:
        from ..models import LeadConversation
        db_conv_existing = db.query(LeadConversation).filter(
            LeadConversation.ClientId == client_id,
            LeadConversation.Channel == "linkedin",
            LeadConversation.ExternalThreadId == str(conversation_id),
            LeadConversation.IsDeleted == False,
        ).first()
        if db_conv_existing and db_conv_existing.CustomerId:
            customer = db.get(LeadCustomer, db_conv_existing.CustomerId)

    # 5. If customer found: ensure this specific sender_urn is linked in LeadChannelIdentity
    if customer:
        if sender_urn:
            exact_ident = db.query(LeadChannelIdentity).filter(
                LeadChannelIdentity.ClientId == client_id,
                LeadChannelIdentity.Channel == "linkedin",
                LeadChannelIdentity.ExternalUserId == str(sender_urn),
                LeadChannelIdentity.IsDeleted == False
            ).first()
            if not exact_ident:
                exact_ident = LeadChannelIdentity(
                    ClientId=client_id,
                    ChannelAccountId=channel_account_id,
                    Channel="linkedin",
                    ExternalUserId=str(sender_urn),
                    CustomerId=customer.Id,
                    ProfileName=display_name or customer.DisplayName,
                    CreatedBy=created_by,
                )
                db.add(exact_ident)
                db.flush()
            identity = exact_ident

        # Upgrade profile URL on customer if incoming is vanity slug
        if profile_url and (not customer.LinkedinProfileUrl or "ACoAA" in customer.LinkedinProfileUrl):
            customer.LinkedinProfileUrl = profile_url
            customer.UpdatedAt = utcnow()
        return customer, identity

    # 6. If brand new: create LeadCustomer + LeadChannelIdentity
    customer = LeadCustomer(
        ClientId=client_id,
        PublicRef=f"Lead #{random.randint(10000, 99999)}",
        DisplayName=display_name,
        LinkedinProfileUrl=profile_url,
        PhoneEnc=encrypt_pii(None),
        CreatedBy=created_by,
    )
    db.add(customer)
    db.flush()

    if sender_urn:
        identity = LeadChannelIdentity(
            ClientId=client_id,
            ChannelAccountId=channel_account_id,
            Channel="linkedin",
            ExternalUserId=str(sender_urn),
            CustomerId=customer.Id,
            ProfileName=display_name,
            CreatedBy=created_by,
        )
        db.add(identity)
        db.flush()

    return customer, identity


def reply_invitation_api(
    db, 
    account, 
    invitation_urn: str, 
    shared_secret: str, 
    action: str = "accept",
    sender_name: Optional[str] = None,
    sender_urn: Optional[str] = None,
    public_id: Optional[str] = None
) -> dict:
    """Respond to a single invitation and sync customer record if accepted."""
    from ..models import utcnow

    api = get_linkedin_client(account)
    
    # Reply to invitation (action: 'accept' or 'reject')
    success = api.reply_invitation(
        invitation_entity_urn=invitation_urn,
        invitation_shared_secret=shared_secret,
        action=action
    )
    if not success:
        return {"success": False, "message": f"Failed to {action} LinkedIn invitation"}
        
    if action == "accept" and sender_urn:
        display_name = sender_name or "LinkedIn Member"
        profile_url = f"https://www.linkedin.com/in/{public_id}" if public_id else None

        customer, identity = find_or_link_linkedin_customer(
            db=db,
            client_id=account.ClientId,
            channel_account_id=account.Id,
            sender_urn=sender_urn,
            public_id=public_id,
            profile_url=profile_url,
            display_name=display_name,
            created_by="linkedin_invite",
        )

        if customer and profile_url:
            customer.LinkedinProfileUrl = profile_url
            customer.UpdatedAt = utcnow()
            logger.info(
                "[CRM Lead LinkedIn URL] Accepted connection for '%s' (URN: %s) -> Saved verified URL: %s",
                display_name, sender_urn, profile_url
            )

        from ..services import crm as crm_service
        crm_service.create_account(
            db, account.ClientId,
            display_name=display_name,
            stage="lead",
            source="linkedin_invitation",
            customer_id=customer.Id if customer else (identity.CustomerId if identity else None),
            linkedin_profile_url=profile_url,
            tags="linkedin,connection_accepted,auto_captured",
            actor="linkedin_invite_ai"
        )
        db.commit()

        # Send welcome message if configured
        meta = account.MetaJson or {}
        welcome_message = meta.get("linkedin_welcome_message")
        if welcome_message:
            recipient_id = public_id or str(sender_urn).split(":")[-1]
            try:
                api.send_message(recipients=[recipient_id], message_body=welcome_message)
            except Exception as e:
                try:
                    api.send_message(conversation_urn_id=recipient_id, message_body=welcome_message)
                except Exception as e2:
                    logger.warning("Failed to send welcome message to %s: %s / %s", display_name, e, e2)

    return {"success": True, "action": action, "message": f"Successfully {action}ed invitation"}


async def accept_all_invitations_api(db, account) -> dict:
    """Accept all received invitations in batch and sync leads."""
    def _accept_all():
        processed_count, accepted_count = process_pending_invitations(db, account)
        return {"processed": processed_count, "accepted": accepted_count}
    return await asyncio.to_thread(_accept_all)


def process_pending_invitations(db, account) -> tuple[int, int]:
    """Poll for pending connection requests, accept them, and send welcome messages."""
    import random
    from ..models import LeadCustomer, LeadChannelIdentity
    from ..security import encrypt_pii

    api = get_linkedin_client(account)
    
    # 1. Fetch invitations
    try:
        invitations = get_raw_invitations(api, limit=50)
    except Exception as exc:
        logger.error("Failed to fetch LinkedIn invitations for client %s: %s", account.ClientId, exc)
        raise exc


    processed_count = 0
    accepted_count = 0
    
    # Retrieve settings from account.MetaJson
    meta = account.MetaJson or {}
    auto_accept = meta.get("linkedin_auto_accept", False)
    welcome_message = meta.get("linkedin_welcome_message")

    for invite in invitations:
        parsed = parse_invitation(invite)
        if not parsed:
            continue

        entity_urn = parsed["invitation_urn"]
        shared_secret = parsed["shared_secret"]
        display_name = parsed["name"]
        sender_urn = parsed["sender_urn"]
        public_id = parsed["public_id"]

        if not sender_urn:
            continue

        processed_count += 1

        # Accept invitation if auto_accept is active, and only then capture to CRM
        if auto_accept:
            try:
                # Accept invitation
                success = api.reply_invitation(
                    invitation_entity_urn=entity_urn,
                    invitation_shared_secret=shared_secret,
                    action="accept"
                )
                if not success:
                    logger.warning("LinkedIn API returned failure accepting invitation from %s (%s)", display_name, sender_urn)
                    continue

                accepted_count += 1
                logger.info("Accepted LinkedIn invitation from %s (%s)", display_name, sender_urn)

                # Find or create identity & customer on accepted connection via unified resolver
                customer, identity = find_or_link_linkedin_customer(
                    db=db,
                    client_id=account.ClientId,
                    channel_account_id=account.Id,
                    sender_urn=sender_urn,
                    public_id=public_id,
                    profile_url=profile_url,
                    display_name=display_name,
                    created_by="linkedin_invite",
                )

                if customer and profile_url:
                    customer.LinkedinProfileUrl = profile_url
                    customer.UpdatedAt = utcnow()

                if profile_url:
                    logger.info(
                        "[CRM Lead LinkedIn URL] Accepted auto-invitation for '%s' (URN: %s) -> Saved verified URL: %s",
                        display_name, sender_urn, profile_url
                    )

                from ..services import crm as crm_service
                headline = parsed.get("headline") or ""
                note = parsed.get("message") or ""
                crm_service.create_account(
                    db, account.ClientId,
                    display_name=display_name,
                    stage="lead",
                    source="linkedin_invitation",
                    customer_id=customer.Id if customer else (identity.CustomerId if identity else None),
                    linkedin_profile_url=profile_url,
                    company_name=headline[:100] if headline else None,
                    tags="linkedin,connection_accepted,auto_captured" + (",has_note" if note else ""),
                    fields={"headline": headline, "invitation_note": note, "sender_urn": sender_urn},
                    actor="linkedin_invite_ai"
                )
                db.commit()
                logger.info("Captured accepted LinkedIn connection as CRM lead: %s (%s)", display_name, sender_urn)

                # Send welcome message if configured
                if welcome_message:
                    recipient_id = public_id or sender_urn.split(":")[-1]
                    try:
                        api.send_message(recipients=[recipient_id], message_body=welcome_message)
                        logger.info("Sent welcome message to connected member: %s", display_name)
                    except Exception as msg_exc:
                        try:
                            api.send_message(conversation_urn_id=recipient_id, message_body=welcome_message)
                            logger.info("Sent welcome message using conversation URN id to connected member: %s", display_name)
                        except Exception as msg_exc2:
                            logger.error("Failed to send welcome message to connected member %s: %s (fallback: %s)", display_name, msg_exc, msg_exc2)
            except Exception as accept_exc:
                logger.error("Failed to accept invitation from %s: %s", display_name, accept_exc)

    return processed_count, accepted_count


def extract_clean_conversation_id(conversation_urn_id: str) -> str:
    """Normalize and clean LinkedIn conversation URN or entity ID for API calls."""
    if not conversation_urn_id:
        return ""
    if conversation_urn_id.startswith("urn:li:fs_conversation:"):
        return conversation_urn_id.replace("urn:li:fs_conversation:", "")
    if conversation_urn_id.startswith("urn:li:msg_conversation:"):
        return conversation_urn_id.replace("urn:li:msg_conversation:", "")
    return conversation_urn_id


def parse_member_profile(member_obj: dict) -> dict:
    """Extract standard profile fields from LinkedIn Voyager member object."""
    if not isinstance(member_obj, dict):
        return {}

    mini = (
        member_obj.get("com.linkedin.voyager.messaging.MessagingMember", {}).get("miniProfile")
        or member_obj.get("miniProfile")
        or member_obj
    )

    first_name = mini.get("firstName", "")
    if isinstance(first_name, dict):
        first_name = first_name.get("text", "")
    last_name = mini.get("lastName", "")
    if isinstance(last_name, dict):
        last_name = last_name.get("text", "")

    name = f"{first_name} {last_name}".strip() or "LinkedIn Member"
    headline = mini.get("occupation") or mini.get("headline") or ""
    if isinstance(headline, dict):
        headline = headline.get("text", "")

    public_id = mini.get("publicIdentifier") or ""
    entity_urn = mini.get("entityUrn") or ""

    picture_url = None
    picture = mini.get("picture")
    if isinstance(picture, dict):
        vector_img = picture.get("com.linkedin.common.VectorImage", {})
        root_url = vector_img.get("rootUrl", "")
        artifacts = vector_img.get("artifacts", [])
        if root_url and artifacts:
            picture_url = root_url + artifacts[-1].get("fileIdentifyingUrlPathSegment", "")

    return {
        "name": name,
        "headline": str(headline) if headline else "",
        "public_id": public_id,
        "urn": str(entity_urn) if entity_urn else "",
        "picture_url": picture_url,
    }


def parse_event_text(event_content: dict) -> str:
    """Extract message body text from Voyager MessageEvent or InmailEvent."""
    if not isinstance(event_content, dict):
        return ""

    msg_event = (
        event_content.get("com.linkedin.voyager.messaging.event.MessageEvent")
        or event_content.get("messageEvent")
    )
    if msg_event:
        body = msg_event.get("attributedBody", {})
        if isinstance(body, dict) and "text" in body:
            return body.get("text", "")
        if isinstance(body, str):
            return body

    inmail = (
        event_content.get("com.linkedin.voyager.messaging.event.InmailEvent")
        or event_content.get("inmailEvent")
    )
    if inmail:
        body = inmail.get("attributedBody", {})
        subject = inmail.get("subject", "")
        text = body.get("text", "") if isinstance(body, dict) else str(body)
        if subject and text:
            return f"{subject}\n{text}"
        return subject or text

    return ""


def parse_conversation_summary(conv: dict, my_urn: Optional[str] = None) -> dict | None:
    """Parse a single conversation object from get_conversations into a structured summary."""
    if not isinstance(conv, dict):
        return None

    entity_urn = conv.get("entityUrn", "")
    if not entity_urn:
        return None

    conv_id = extract_clean_conversation_id(entity_urn)

    raw_participants = conv.get("conversationParticipants") or conv.get("participants") or []
    participants = []
    other_participant = None

    for p in raw_participants:
        parsed_p = parse_member_profile(p)
        if parsed_p.get("name"):
            is_self = False
            if my_urn and (my_urn in str(parsed_p.get("urn", "")) or my_urn in str(parsed_p.get("public_id", ""))):
                is_self = True
            parsed_p["is_self"] = is_self
            participants.append(parsed_p)
            if not is_self and not other_participant:
                other_participant = parsed_p

    if not other_participant and participants:
        other_participant = participants[0]

    # Extract last event snippet
    events = conv.get("events", [])
    last_message_text = ""
    last_message_at = conv.get("lastActivityAt")
    last_sender_name = None

    if events and isinstance(events, list):
        latest_event = events[0]
        last_message_text = parse_event_text(latest_event.get("eventContent", {}))
        if not last_message_at:
            last_message_at = latest_event.get("createdAt")
        sender_p = parse_member_profile(latest_event.get("from", {}))
        last_sender_name = sender_p.get("name")

    unread_count = conv.get("unreadCount", 0)
    is_read = conv.get("read", True) if "read" in conv else (unread_count == 0)

    return {
        "conversation_urn": entity_urn,
        "conversation_id": conv_id,
        "contact_name": other_participant.get("name", "LinkedIn Member") if other_participant else "LinkedIn Member",
        "contact_headline": other_participant.get("headline", "") if other_participant else "",
        "contact_public_id": other_participant.get("public_id", "") if other_participant else "",
        "contact_urn": other_participant.get("urn", "") if other_participant else "",
        "contact_avatar": other_participant.get("picture_url") if other_participant else None,
        "participants": participants,
        "last_message": last_message_text,
        "last_sender_name": last_sender_name,
        "last_activity_at": last_message_at,
        "unread_count": unread_count,
        "is_read": is_read,
        "total_events": len(events),
    }


def parse_message_event(event: dict, my_urn: Optional[str] = None) -> dict | None:
    """Parse an individual message event object from get_conversation into a chat item."""
    if not isinstance(event, dict):
        return None

    entity_urn = event.get("entityUrn", "")
    created_at = event.get("createdAt")
    sender = parse_member_profile(event.get("from", {}))
    text = parse_event_text(event.get("eventContent", {}))

    is_self = False
    if my_urn and (my_urn in str(sender.get("urn", "")) or my_urn in str(sender.get("public_id", ""))):
        is_self = True

    return {
        "event_urn": entity_urn,
        "created_at": created_at,
        "text": text,
        "sender_name": sender.get("name", "LinkedIn Member"),
        "sender_urn": sender.get("urn", ""),
        "sender_public_id": sender.get("public_id", ""),
        "sender_avatar": sender.get("picture_url"),
        "is_self": is_self,
    }


# ===========================================================================
# Persistent Browser Session Manager for LinkedIn (Anti-Bot & Stealth Enabled)
# ===========================================================================

import time
import re
import random

async def human_type(locator, text: str):
    """
    Simulate realistic human keystroke intervals with micro-pauses and variable timing
    to prevent LinkedIn behavior-based bot detection.
    """
    await locator.click()
    await asyncio.sleep(random.uniform(0.15, 0.35))
    for char in text:
        await locator.type(char, delay=random.randint(30, 80))
        if char in ".!?,":
            await asyncio.sleep(random.uniform(0.2, 0.45))
        elif random.random() < 0.04:
            await asyncio.sleep(random.uniform(0.1, 0.25))


def _parse_graphql_comments_payload(data: dict) -> list[dict]:
    """Extract full comment trees from LinkedIn GraphQL / Voyager response payloads."""
    if not isinstance(data, dict):
        return []

    parsed = []
    included = data.get("included", [])
    
    # 1. Build lookup tables for miniProfiles and members
    profile_lookup = {}
    for item in included:
        if not isinstance(item, dict):
            continue
        entity_type = item.get("$type", "")
        entity_urn = item.get("entityUrn", "")
        
        if "MiniProfile" in entity_type or "miniProfile" in item or "publicIdentifier" in item:
            fn = item.get("firstName", "")
            if isinstance(fn, dict):
                fn = fn.get("text", "")
            ln = item.get("lastName", "")
            if isinstance(ln, dict):
                ln = ln.get("text", "")
            name = f"{fn} {ln}".strip() or "LinkedIn Member"
            
            headline = item.get("occupation") or item.get("headline") or ""
            if isinstance(headline, dict):
                headline = headline.get("text", "")
                
            public_id = item.get("publicIdentifier", "")
            
            avatar = None
            pic = item.get("picture")
            if isinstance(pic, dict):
                v_img = pic.get("com.linkedin.common.VectorImage") or pic.get("VectorImage") or {}
                root_url = v_img.get("rootUrl", "")
                artifacts = v_img.get("artifacts", [])
                if root_url and artifacts:
                    avatar = root_url + artifacts[-1].get("fileIdentifyingUrlPathSegment", "")
            
            p_data = {
                "name": name,
                "headline": str(headline) if headline else "",
                "profile_url": f"https://www.linkedin.com/in/{public_id}" if public_id else "",
                "avatar": avatar,
            }
            if entity_urn:
                profile_lookup[entity_urn] = p_data
            if public_id:
                profile_lookup[public_id] = p_data

    # 2. Extract comments from included objects or elements
    for item in included:
        if not isinstance(item, dict):
            continue
        item_type = item.get("$type", "")
        
        if "Comment" in item_type or "UpdateComment" in item_type or item.get("comment") or "commentText" in item:
            c_urn = item.get("entityUrn") or item.get("urn") or item.get("id")
            if not c_urn:
                continue
                
            # Comment text
            text = ""
            c_val = item.get("comment") or item.get("commentText") or item.get("text") or item.get("body")
            if isinstance(c_val, dict):
                vals = c_val.get("values", [])
                if vals and isinstance(vals, list) and isinstance(vals[0], dict):
                    text = vals[0].get("value", "")
                elif "text" in c_val:
                    text = c_val["text"]
            elif isinstance(c_val, str):
                text = c_val
                
            if not text or not text.strip():
                continue
                
            # Commenter author resolution
            commenter_urn = item.get("commenterUrn") or item.get("commenter") or item.get("actor")
            if isinstance(commenter_urn, dict):
                commenter_urn = commenter_urn.get("entityUrn") or commenter_urn.get("urn") or ""
                
            author_info = profile_lookup.get(str(commenter_urn), {})
            
            parsed.append({
                "comment_urn": c_urn,
                "author_name": author_info.get("name", "LinkedIn Member"),
                "author_headline": author_info.get("headline", ""),
                "author_profile_url": author_info.get("profile_url", ""),
                "author_avatar": author_info.get("avatar"),
                "comment_text": text.strip(),
            })

    # 3. Fallback to elements array (Voyager / REST format)
    if not parsed and "elements" in data:
        for el in data.get("elements", []):
            if not isinstance(el, dict):
                continue
            c_urn = el.get("entityUrn") or el.get("id") or el.get("urn")
            if not c_urn:
                continue
            
            text = ""
            if el.get("comment") and isinstance(el["comment"], dict) and el["comment"].get("values"):
                text = el["comment"]["values"][0].get("value", "")
            elif el.get("comment") and isinstance(el["comment"], str):
                text = el["comment"]
            elif el.get("commentText") and isinstance(el["commentText"], dict):
                text = el["commentText"].get("text", "")
            
            if not text or not text.strip():
                continue
                
            name = "LinkedIn Member"
            headline = ""
            profile_url = ""
            avatar = None
            
            commenter = el.get("commenter", {})
            if isinstance(commenter, dict):
                mini = commenter.get("miniProfile") or commenter
                fn = mini.get("firstName", "")
                ln = mini.get("lastName", "")
                if isinstance(fn, dict): fn = fn.get("text", "")
                if isinstance(ln, dict): ln = ln.get("text", "")
                if fn or ln: name = f"{fn} {ln}".strip()
                headline = mini.get("occupation") or mini.get("headline") or ""
                if isinstance(headline, dict): headline = headline.get("text", "")
                if mini.get("publicIdentifier"):
                    profile_url = f"https://www.linkedin.com/in/{mini['publicIdentifier']}"
                if mini.get("picture") and isinstance(mini["picture"], dict):
                    v = mini["picture"].get("com.linkedin.common.VectorImage", {})
                    r_url = v.get("rootUrl", "")
                    arts = v.get("artifacts", [])
                    if r_url and arts:
                        avatar = r_url + arts[-1].get("fileIdentifyingUrlPathSegment", "")
                        
            parsed.append({
                "comment_urn": c_urn,
                "author_name": name,
                "author_headline": headline,
                "author_profile_url": profile_url,
                "author_avatar": avatar,
                "comment_text": text.strip(),
            })

def parse_event_text(content: dict) -> str:
    """Extract plain text string from GraphQL or Voyager event content."""
    if not isinstance(content, dict):
        return ""
    if "attributedBody" in content and isinstance(content["attributedBody"], dict):
        return content["attributedBody"].get("text", "")
    for v in content.values():
        if isinstance(v, dict) and "attributedBody" in v:
            return v["attributedBody"].get("text", "")
    return content.get("text", "")


def parse_member_profile(item: dict) -> dict:
    """Parse a GraphQL MessagingMember or MiniProfile item into a standard dict."""
    if not isinstance(item, dict):
        return {}
    mini = item.get("miniProfile") or item
    fn = mini.get("firstName", "")
    ln = mini.get("lastName", "")
    if isinstance(fn, dict): fn = fn.get("text", "")
    if isinstance(ln, dict): ln = ln.get("text", "")
    name = f"{fn} {ln}".strip() if (fn or ln) else mini.get("name", "LinkedIn Member")
    
    headline = mini.get("occupation") or mini.get("headline") or ""
    if isinstance(headline, dict): headline = headline.get("text", "")
    
    public_id = mini.get("publicIdentifier") or ""
    profile_url = f"https://www.linkedin.com/in/{public_id}" if public_id else ""
    urn = mini.get("entityUrn") or item.get("entityUrn") or ""
    
    avatar = None
    if mini.get("picture") and isinstance(mini["picture"], dict):
        v = mini["picture"].get("com.linkedin.common.VectorImage", {})
        r_url = v.get("rootUrl", "")
        arts = v.get("artifacts", [])
        if r_url and arts:
            avatar = r_url + arts[-1].get("fileIdentifyingUrlPathSegment", "")
            
    return {
        "name": name or "LinkedIn Member",
        "headline": headline,
        "public_id": public_id,
        "profile_url": profile_url,
        "urn": urn,
        "picture_url": avatar,
    }


def parse_message_event(item: dict) -> Optional[dict]:
    """Parse a GraphQL MessageEvent into standard message format."""
    if not isinstance(item, dict):
        return None
    content = item.get("eventContent", {})
    text = parse_event_text(content)
    if not text:
        return None
    urn = item.get("entityUrn", "")
    from_member = item.get("from", {})
    from_urn = from_member if isinstance(from_member, str) else from_member.get("entityUrn", "")
    return {
        "event_urn": urn,
        "text": text,
        "sender_urn": from_urn,
        "created_at": item.get("createdAt"),
    }


def parse_conversation_summary(el: dict) -> Optional[dict]:
    """Parse a legacy Voyager conversation element."""
    if not isinstance(el, dict):
        return None
    conv_urn = el.get("entityUrn", "")
    conv_id = extract_clean_conversation_id(conv_urn)
    events = el.get("events", [])
    last_msg = ""
    if events and isinstance(events, list) and isinstance(events[0], dict):
        last_msg = parse_event_text(events[0].get("eventContent", {}))
    participants = []
    other_p = None
    for p in el.get("participants", []):
        parsed_p = parse_member_profile(p)
        participants.append(parsed_p)
        if not other_p:
            other_p = parsed_p
    return {
        "conversation_id": conv_id,
        "conversation_urn": conv_urn,
        "contact_name": other_p.get("name", "LinkedIn Member") if other_p else "LinkedIn Member",
        "contact_headline": other_p.get("headline", "") if other_p else "",
        "contact_public_id": other_p.get("public_id", "") if other_p else "",
        "contact_urn": other_p.get("urn", "") if other_p else "",
        "contact_avatar": other_p.get("picture_url") if other_p else None,
        "participants": participants,
        "last_message": last_msg,
        "last_sender_name": other_p.get("name") if other_p else None,
        "last_activity_at": el.get("lastActivityAt"),
        "unread_count": el.get("unreadCount", 0),
        "is_read": el.get("unreadCount", 0) == 0,
        "total_events": len(events),
    }


def _parse_graphql_conversations_payload(data: dict) -> list[dict]:
    """Extract conversations and thread summaries from LinkedIn GraphQL messaging responses."""
    if not isinstance(data, dict):
        return []

    results = []
    included = data.get("included", [])
    
    # Build miniProfile lookup
    profile_lookup = {}
    for item in included:
        if not isinstance(item, dict):
            continue
        entity_type = item.get("$type", "")
        entity_urn = item.get("entityUrn", "")
        if "MessagingMember" in entity_type or "MiniProfile" in entity_type or "miniProfile" in item:
            profile_lookup[entity_urn] = parse_member_profile(item)

    for item in included:
        if not isinstance(item, dict):
            continue
        entity_type = item.get("$type", "")
        if "Conversation" in entity_type or "conversation" in entity_type.lower():
            entity_urn = item.get("entityUrn", "")
            if not entity_urn:
                continue
            conv_id = extract_clean_conversation_id(entity_urn)
            
            # Find participants
            raw_p = item.get("conversationParticipants") or item.get("participants") or []
            participants = []
            other_p = None
            for p_urn in raw_p:
                p_key = p_urn if isinstance(p_urn, str) else p_urn.get("entityUrn", "")
                p_info = profile_lookup.get(p_key, {})
                if p_info:
                    participants.append(p_info)
                    if not other_p:
                        other_p = p_info
            
            last_msg = ""
            events = item.get("events", [])
            if events and isinstance(events, list) and isinstance(events[0], dict):
                last_msg = parse_event_text(events[0].get("eventContent", {}))
                
            results.append({
                "conversation_id": conv_id,
                "conversation_urn": entity_urn,
                "contact_name": other_p.get("name", "LinkedIn Member") if other_p else "LinkedIn Member",
                "contact_headline": other_p.get("headline", "") if other_p else "",
                "contact_public_id": other_p.get("public_id", "") if other_p else "",
                "contact_urn": other_p.get("urn", "") if other_p else "",
                "contact_avatar": other_p.get("picture_url") if other_p else None,
                "participants": participants,
                "last_message": last_msg,
                "last_sender_name": other_p.get("name") if other_p else None,
                "last_activity_at": item.get("lastActivityAt"),
                "unread_count": item.get("unreadCount", 0),
                "is_read": item.get("unreadCount", 0) == 0,
                "total_events": len(events),
            })

    # Elements fallback (legacy Voyager)
    if not results and "elements" in data:
        for el in data.get("elements", []):
            parsed_c = parse_conversation_summary(el)
            if parsed_c:
                results.append(parsed_c)

    return results


async def safe_evaluate(page, script: str, retries: int = 2, fallback = None):
    """Safely evaluate JavaScript in page, retrying if navigation temporarily destroyed execution context."""
    for attempt in range(retries):
        try:
            return await page.evaluate(script)
        except Exception as exc:
            err_msg = str(exc)
            if "Execution context was destroyed" in err_msg or "navigation" in err_msg:
                await asyncio.sleep(1.0)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=4000)
                except Exception:
                    pass
            elif attempt == retries - 1:
                logger.debug("safe_evaluate notice: %s", exc)
                return fallback
    return fallback


def persist_refreshed_session_cookie(account_id: int, new_cookie: str):
    """Persist auto-renewed session cookie back to the database for this account."""
    try:
        from ..db import session as db_session
        from ..models_ext import LeadChannelAccount
        from ..security import encrypt_pii
        from ..models import utcnow

        with db_session() as s:
            acc = s.query(LeadChannelAccount).filter(LeadChannelAccount.Id == account_id).first()
            if acc:
                acc.LinkedinCookieEnc = encrypt_pii(new_cookie)
                acc.UpdatedAt = utcnow()
                s.commit()
                logger.info("Persisted refreshed LinkedIn session cookie to DB for account %s", account_id)
    except Exception as e:
        logger.warning("Failed to persist refreshed LinkedIn cookie: %s", e)


class LinkedInBrowserManager:
    """Maintains a persistent, warm Playwright browser session with anti-detection evasions.
    Enforces strict process isolation between active messaging (DMs/InMail) and background comment scraping.
    """
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._messaging_page = None
        self._comments_page = None
        self._account_id = None
        self._cookie_hash = None
        self._last_active = 0
        self._messaging_locks = {}
        self._comments_locks = {}
        self._loop = None
        self._last_messaging_activity = 0.0

    def get_messaging_lock(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop not in self._messaging_locks:
            self._messaging_locks[loop] = asyncio.Lock()
        return self._messaging_locks[loop]

    def get_comments_lock(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop not in self._comments_locks:
            self._comments_locks[loop] = asyncio.Lock()
        return self._comments_locks[loop]

    def get_lock(self):
        # Backward compatibility
        return self.get_messaging_lock()

    def mark_messaging_active(self):
        self._last_messaging_activity = time.time()

    def is_messaging_active(self, cooldown: float = 60.0) -> bool:
        return (time.time() - self._last_messaging_activity) < cooldown

    async def _ensure_context(self, account):
        cookie = decrypt_pii(account.LinkedinCookieEnc) if account.LinkedinCookieEnc else None
        username = decrypt_pii(account.LinkedinUsernameEnc) if account.LinkedinUsernameEnc else None
        password = decrypt_pii(account.LinkedinPasswordEnc) if account.LinkedinPasswordEnc else None

        if not cookie and username and password:
            extracted = await extract_session_cookie_via_browser(username, password)
            if extracted:
                cookie = extracted
                persist_refreshed_session_cookie(account.Id, extracted)
                try:
                    account.LinkedinCookieEnc = encrypt_pii(extracted)
                except Exception:
                    pass

        if not cookie:
            raise ValueError("LinkedIn session credentials not configured")

        li_at = cookie.split("|||")[0] if "|||" in cookie else cookie
        jsession = cookie.split("|||")[1] if "|||" in cookie else "ajax:1234567890"
        cookie_hash = f"{li_at[:15]}|||{jsession}"

        current_loop = asyncio.get_running_loop()
        if self._loop != current_loop:
            self._messaging_page = None
            self._comments_page = None
            self._context = None
            self._browser = None
            self._playwright = None
            self._loop = current_loop

        if self._context and self._browser and self._account_id == account.Id and self._cookie_hash == cookie_hash:
            self._last_active = time.time()
            return

        await self.close()

        from playwright.async_api import async_playwright
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage",
            ]
        )
        self._context = await self._browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="America/New_York",
        )
        await self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        """)

        clean_jsession = jsession.replace('"', '').strip()
        await self._context.add_cookies([
            {"name": "li_at", "value": li_at, "domain": ".linkedin.com", "path": "/"},
            {"name": "JSESSIONID", "value": f'"{clean_jsession}"', "domain": ".linkedin.com", "path": "/"},
        ])
        self._account_id = account.Id
        self._cookie_hash = cookie_hash
        self._last_active = time.time()

    async def get_messaging_page(self, account):
        self.mark_messaging_active()
        await self._ensure_context(account)
        if not self._messaging_page or self._messaging_page.is_closed():
            self._messaging_page = await self._context.new_page()
        self._last_active = time.time()
        return self._messaging_page

    async def get_comments_page(self, account):
        await self._ensure_context(account)
        if not self._comments_page or self._comments_page.is_closed():
            self._comments_page = await self._context.new_page()
        self._last_active = time.time()
        return self._comments_page

    async def close_comments_page(self):
        try:
            if self._comments_page and not self._comments_page.is_closed():
                await self._comments_page.close()
        except Exception:
            pass
        self._comments_page = None

    async def get_page(self, account):
        # Backward compatibility
        return await self.get_messaging_page(account)

    async def navigate_with_session(self, page, account, target_url: str, wait_until: str = "domcontentloaded", timeout: int = 20000):
        try:
            res = await page.goto(target_url, wait_until=wait_until, timeout=timeout)
            curr_url = page.url.lower()
            if any(x in curr_url for x in ["/login", "/authwall", "/checkpoint", "uas/login"]):
                raise RuntimeError("Session expired, redirected to login")
            return res
        except Exception as exc:
            err_msg = str(exc)
            if "ERR_TOO_MANY_REDIRECTS" in err_msg or "redirected to login" in err_msg or "login" in err_msg.lower():
                logger.info("LinkedIn session expired on %s. Checking for saved username/password auto-recovery...", target_url)
                username = decrypt_pii(account.LinkedinUsernameEnc) if account.LinkedinUsernameEnc else None
                password = decrypt_pii(account.LinkedinPasswordEnc) if account.LinkedinPasswordEnc else None
                if username and password:
                    logger.info("Attempting automatic re-authentication for %s...", username)
                    extracted = await extract_session_cookie_via_browser(username, password)
                    if extracted:
                        li_at = extracted.split("|||")[0] if "|||" in extracted else extracted
                        jsession = extracted.split("|||")[1] if "|||" in extracted else "ajax:1234567890"
                        clean_jsession = jsession.replace('"', '').strip()
                        if self._context:
                            await self._context.clear_cookies()
                            await self._context.add_cookies([
                                {"name": "li_at", "value": li_at, "domain": ".linkedin.com", "path": "/"},
                                {"name": "JSESSIONID", "value": f'"{clean_jsession}"', "domain": ".linkedin.com", "path": "/"},
                            ])
                        self._cookie_hash = f"{li_at[:15]}|||{jsession}"
                        persist_refreshed_session_cookie(account.Id, extracted)
                        account.LinkedinCookieEnc = encrypt_pii(extracted)
                        logger.info("Successfully refreshed and persisted new session token. Retrying navigation...")
                        return await page.goto(target_url, wait_until=wait_until, timeout=timeout)
            raise exc

    async def close(self):
        try:
            if self._messaging_page and not self._messaging_page.is_closed():
                await self._messaging_page.close()
        except Exception:
            pass
        try:
            if self._comments_page and not self._comments_page.is_closed():
                await self._comments_page.close()
        except Exception:
            pass
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._messaging_page = None
        self._comments_page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._account_id = None
        self._cookie_hash = None

_browser_manager = LinkedInBrowserManager()


async def fetch_conversations_api(account, limit: int = 25) -> list[dict]:
    """
    Fetch recent LinkedIn conversation threads with sender information and last message.
    Uses Passive Network Interception on GraphQL responses (immune to CSS changes)
    with semantic DOM fallback.
    """
    cookie = decrypt_pii(account.LinkedinCookieEnc) if account.LinkedinCookieEnc else None
    if not cookie and not (account.LinkedinUsernameEnc and account.LinkedinPasswordEnc):
        return []

    async with _browser_manager.get_messaging_lock():
        try:
            page = await _browser_manager.get_messaging_page(account)
            intercepted_conversations = []

            async def handle_response(response):
                url = response.url
                if ("voyagerMessagingGraphQL" in url or "messengerConversations" in url or "/messaging/conversations" in url) and response.status == 200:
                    try:
                        data = await response.json()
                        parsed = _parse_graphql_conversations_payload(data)
                        if parsed:
                            intercepted_conversations.extend(parsed)
                    except Exception:
                        pass

            page.on("response", handle_response)

            try:
                if "linkedin.com/messaging" not in page.url:
                    await _browser_manager.navigate_with_session(page, account, "https://www.linkedin.com/messaging/", wait_until="domcontentloaded", timeout=20000)
                else:
                    await page.reload(wait_until="domcontentloaded", timeout=15000)
                
                await asyncio.sleep(2.0)
            finally:
                page.remove_listener("response", handle_response)

            # 1. Return network intercepted conversations if captured (100% immune to UI/CSS changes)
            if intercepted_conversations:
                seen = set()
                unique_convs = []
                for c in intercepted_conversations:
                    cid = c.get("conversation_id")
                    if cid and cid not in seen:
                        seen.add(cid)
                        unique_convs.append(c)
                if unique_convs:
                    logger.info("Captured %d conversations via passive network interception", len(unique_convs))
                    return unique_convs[:limit]

            # 2. Resilient DOM fallback with multi-attribute matching
            conversations_data = await safe_evaluate(page, '''() => {
                const items = document.querySelectorAll('li.msg-conversation-listitem, .msg-conversation-listitem, [data-control-name="conversation_item"]');
                const results = [];
                const seen = new Set();

                items.forEach((el, index) => {
                    const nameEl = el.querySelector('.msg-conversation-listitem__participant-names, .msg-conversation-card__participant-names, h3, [data-anonymize="person-name"]');
                    const lastMsgEl = el.querySelector('.msg-conversation-card__message-snippet, .msg-conversation-listitem__message-snippet, p');
                    const timeEl = el.querySelector('time, .msg-conversation-listitem__time-stamp');
                    const linkEl = el.querySelector('a[href*="/messaging/thread/"]');
                    const imgEl = el.querySelector('img');
                    const badgeEl = el.querySelector('.msg-conversation-card__unread-count, .badge, [aria-label*="unread"]');

                    let threadId = '';
                    if (linkEl && linkEl.href) {
                        const match = linkEl.href.match(/\\/messaging\\/thread\\/([^\\/]+)/);
                        if (match) threadId = match[1];
                    }
                    if (!threadId) {
                        threadId = `conv-${index}`;
                    }

                    const name = nameEl ? nameEl.innerText.trim() : 'LinkedIn Member';
                    const lastMsg = lastMsgEl ? lastMsgEl.innerText.trim() : '';

                    const dedupeKey = threadId && !threadId.startsWith('conv-') ? threadId : `${name}|||${lastMsg}`;
                    if (seen.has(dedupeKey)) return;
                    seen.add(dedupeKey);

                    let profileUrl = '';
                    let publicId = '';
                    const profileLink = el.querySelector('a[href*="/in/"], a[data-control-name="view_profile"]');
                    if (profileLink && profileLink.href) {
                        profileUrl = profileLink.href.split('?')[0];
                        const m = profileUrl.match(/\/in\/([^\/\?#]+)/);
                        if (m) publicId = m[1];
                    }
                    if (!publicId && nameEl) {
                        const parentLink = nameEl.closest('a[href*="/in/"]');
                        if (parentLink && parentLink.href) {
                            profileUrl = parentLink.href.split('?')[0];
                            const m = profileUrl.match(/\/in\/([^\/\?#]+)/);
                            if (m) publicId = m[1];
                        }
                    }

                    results.push({
                        conversation_id: threadId,
                        conversation_urn: `urn:li:msg_conversation:${threadId}`,
                        contact_name: name,
                        contact_headline: '',
                        contact_public_id: publicId,
                        contact_urn: publicId ? `urn:li:fsd_profile:${publicId}` : '',
                        profile_url: profileUrl,
                        contact_avatar: imgEl ? imgEl.src : null,
                        participants: [{
                            name: name,
                            headline: '',
                            public_id: publicId,
                            profile_url: profileUrl,
                            urn: publicId ? `urn:li:fsd_profile:${publicId}` : '',
                            picture_url: imgEl ? imgEl.src : null,
                            is_self: false
                        }],
                        last_message: lastMsg,
                        last_sender_name: name,
                        last_activity_at: timeEl ? timeEl.innerText.trim() : '',
                        unread_count: badgeEl ? parseInt(badgeEl.innerText.trim()) || 0 : 0,
                        is_read: !badgeEl,
                        total_events: 1
                    });
                });
                return results;
            }''', fallback=[])
            return conversations_data[:limit]
        except Exception as exc:
            logger.warning("Browser conversation extraction error: %s", exc)
            return []


async def fetch_conversation_messages_api(account, conversation_urn_id: str, load_earlier: bool = False) -> list[dict]:
    """
    Fetch full message events history for a given conversation thread.
    Supports load_earlier to scroll up and expand past message history chunks.
    Uses Passive Network Interception with accessible semantic DOM fallback.
    """
    clean_id = extract_clean_conversation_id(conversation_urn_id)
    cookie = decrypt_pii(account.LinkedinCookieEnc) if account.LinkedinCookieEnc else None
    if not cookie and not (account.LinkedinUsernameEnc and account.LinkedinPasswordEnc):
        return []

    async with _browser_manager.get_messaging_lock():
        try:
            page = await _browser_manager.get_messaging_page(account)
            intercepted_messages = []

            async def handle_response(response):
                url = response.url
                if ("voyagerMessagingGraphQL" in url or "messengerMessages" in url or "/events" in url) and response.status == 200:
                    try:
                        data = await response.json()
                        included = data.get("included", [])
                        for item in included:
                            if isinstance(item, dict) and ("MessageEvent" in item.get("$type", "") or item.get("eventContent")):
                                parsed = parse_message_event(item)
                                if parsed and parsed.get("text"):
                                    intercepted_messages.append(parsed)
                    except Exception:
                        pass

            page.on("response", handle_response)

            try:
                if "linkedin.com/messaging" not in page.url:
                    await _browser_manager.navigate_with_session(page, account, "https://www.linkedin.com/messaging/", wait_until="domcontentloaded", timeout=20000)

                # Select target conversation
                if clean_id.startswith("conv-"):
                    idx = int(clean_id.replace("conv-", "") or "0")
                    items = page.locator("li.msg-conversation-listitem, .msg-conversation-listitem, [role='listitem']")
                    if await items.count() > idx:
                        await items.nth(idx).click()
                        await asyncio.sleep(1.2)
                elif not clean_id.startswith("conv-") and f"/messaging/thread/{clean_id}" not in page.url:
                    link_target = page.locator(f"a[href*='{clean_id}']").first
                    if await link_target.count() > 0:
                        await link_target.click()
                        await asyncio.sleep(1.2)
                    else:
                        await _browser_manager.navigate_with_session(page, account, f"https://www.linkedin.com/messaging/thread/{clean_id}/", wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(1.2)

                # Wait for messages to load in the active pane and scroll
                try:
                    await page.wait_for_selector("li.msg-s-message-list__event, .msg-s-message-list__event", timeout=8000)
                    if load_earlier:
                        for _ in range(3):
                            await safe_evaluate(page, '''() => {
                                const scroller = document.querySelector('.msg-s-message-list, .msg-s-message-list-container');
                                if (scroller) scroller.scrollTop = 0;
                                const btn = document.querySelector('button.msg-s-message-list__load-more-button, .msg-s-message-list__loader button');
                                if (btn) btn.click();
                            }''')
                            await asyncio.sleep(0.8)
                    else:
                        await safe_evaluate(page, '''() => {
                            const scroller = document.querySelector('.msg-s-message-list, .msg-s-message-list-container');
                            if (scroller) scroller.scrollTop = 0;
                        }''')
                        await asyncio.sleep(0.3)

                    await safe_evaluate(page, '''() => {
                        const scroller = document.querySelector('.msg-s-message-list, .msg-s-message-list-container');
                        if (scroller) scroller.scrollTop = scroller.scrollHeight;
                    }''')
                    await asyncio.sleep(0.3)
                except Exception:
                    pass
            finally:
                page.remove_listener("response", handle_response)

            if intercepted_messages:
                logger.info("Captured %d messages via passive network interception", len(intercepted_messages))
                return intercepted_messages

            # Semantic DOM fallback
            messages = await safe_evaluate(page, '''() => {
                const list = [];
                const seen = new Set();
                const listItems = document.querySelectorAll('li.msg-s-message-list__event, .msg-s-message-list__event, [role="listitem"]');
                const myImg = document.querySelector('.global-nav__me-photo, img[alt*="Photo of"]');
                const myName = myImg ? (myImg.alt || '').replace('Photo of', '').trim().toLowerCase() : '';

                // Extract active thread header profile link if available
                const headerLink = document.querySelector('.msg-thread__link-to-profile, .msg-title-bar a[href*="/in/"], .msg-entity-lockup a[href*="/in/"], a.msg-thread__profile-link, .msg-conversation-header a[href*="/in/"], a[href*="/in/"]');
                let headerProfileUrl = '';
                let headerPublicId = '';
                if (headerLink && headerLink.href) {
                    headerProfileUrl = headerLink.href.split('?')[0];
                    const m = headerProfileUrl.match(/\/in\/([^\/\?#]+)/);
                    if (m) headerPublicId = m[1];
                }

                listItems.forEach((el, idx) => {
                    const senderEl = el.querySelector('.msg-s-message-group__name, .msg-s-message-group__profile-link, h4');
                    const profileLinkEl = el.querySelector('a.msg-s-message-group__profile-link, a[href*="/in/"]');
                    let senderProfileUrl = headerProfileUrl;
                    let senderPublicId = headerPublicId;
                    if (profileLinkEl && profileLinkEl.href) {
                        senderProfileUrl = profileLinkEl.href.split('?')[0];
                        const m = senderProfileUrl.match(/\/in\/([^\/\?#]+)/);
                        if (m) senderPublicId = m[1];
                    }

                    const timeEl = el.querySelector('time, .msg-s-message-group__timestamp');
                    const time = timeEl ? timeEl.innerText.trim() : '';
                    const sender = senderEl ? senderEl.innerText.trim() : 'LinkedIn Member';
                    const imgEl = el.querySelector('img');
                    const isSenderClass = el.querySelector('.msg-s-message-group--is-sender') !== null || el.classList.contains('msg-s-message-list__event--is-sender');
                    const isSelf = isSenderClass || (myName && sender.toLowerCase().includes(myName));

                    const bodyElements = el.querySelectorAll('.msg-s-event-listitem__body, .msg-s-message-group__message, p');
                    if (bodyElements.length === 0) return;

                    bodyElements.forEach((bodyEl, bIdx) => {
                        const text = bodyEl.innerText.trim();
                        if (!text) return;

                        const dedupeKey = `${sender}|||${time}|||${text}`;
                        if (seen.has(dedupeKey)) return;
                        seen.add(dedupeKey);

                        list.push({
                            event_urn: `event-${idx}-${bIdx}`,
                            created_at: time,
                            text: text,
                            sender_name: sender,
                            sender_urn: isSelf ? '' : (senderPublicId ? `urn:li:fsd_profile:${senderPublicId}` : ''),
                            sender_public_id: isSelf ? '' : senderPublicId,
                            sender_profile_url: isSelf ? '' : senderProfileUrl,
                            sender_avatar: imgEl ? imgEl.src : null,
                            is_self: isSelf,
                        });
                    });
                });
                return list;
            }''', fallback=[])
            return messages
        except Exception as exc:
            logger.warning("Browser messages extraction error: %s", exc)
            return []


async def send_conversation_message_api(account, conversation_urn_id: str, message_body: str) -> dict:
    """
    Send a message reply to a LinkedIn conversation thread using
    Accessible ARIA Locators and Human-Paced Typing to prevent bot detection.
    """
    clean_id = extract_clean_conversation_id(conversation_urn_id)
    cookie = decrypt_pii(account.LinkedinCookieEnc) if account.LinkedinCookieEnc else None
    if not cookie and not (account.LinkedinUsernameEnc and account.LinkedinPasswordEnc):
        raise ValueError("LinkedIn session credentials not configured")

    async with _browser_manager.get_lock():
        try:
            page = await _browser_manager.get_page(account)
            if "linkedin.com/messaging" not in page.url:
                await _browser_manager.navigate_with_session(page, account, "https://www.linkedin.com/messaging/", wait_until="domcontentloaded", timeout=20000)

            # Select thread
            if clean_id.startswith("conv-"):
                idx = int(clean_id.replace("conv-", "") or "0")
                items = page.locator("li.msg-conversation-listitem, .msg-conversation-listitem")
                if await items.count() > idx:
                    await items.nth(idx).click()
                    await asyncio.sleep(random.uniform(0.6, 1.0))
            elif not clean_id.startswith("conv-") and f"/messaging/thread/{clean_id}" not in page.url:
                link_target = page.locator(f"a[href*='{clean_id}']").first
                if await link_target.count() > 0:
                    await link_target.click()
                else:
                    await _browser_manager.navigate_with_session(page, account, f"https://www.linkedin.com/messaging/thread/{clean_id}/", wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(random.uniform(0.6, 1.0))

            # 1. Locate composer using Accessible Semantic Locators (W3C standard)
            composer = page.get_by_role("textbox", name=re.compile(r"Write a message|Type a message", re.I)).first
            if not await composer.count():
                composer = page.locator("[role='textbox'], [contenteditable='true'], .msg-form__contenteditable").last

            # 2. Humanized typing simulation with keystroke micro-delays
            await human_type(composer, message_body)
            await asyncio.sleep(random.uniform(0.6, 1.2))

            # 3. Submit via accessible Send button or Enter
            send_btn = page.get_by_role("button", name=re.compile(r"^Send$", re.I)).first
            if not await send_btn.count():
                send_btn = page.locator("button.msg-form__send-button, button[type='submit']").first

            if await send_btn.count() > 0 and await send_btn.is_enabled():
                await send_btn.click()
            else:
                await composer.press("Enter")

            await asyncio.sleep(random.uniform(1.0, 1.8))
            return {"success": True, "message": "Message sent successfully"}
        except Exception as exc:
            logger.error("Failed to send message via browser: %s", exc)
            raise RuntimeError(f"Failed to dispatch message: {str(exc)}")


def auto_convert_linkedin_dm_to_crm_lead(
    db,
    account,
    conversation_id: str,
    contact_name: str,
    contact_urn: Optional[str] = None,
    public_id: Optional[str] = None,
    profile_url: Optional[str] = None,
    last_message: Optional[str] = None,
    eval_res: Optional[Any] = None,
) -> Optional[Any]:
    """Auto-captures a qualified LinkedIn DM / InMail with commercial buying intent into CRM Customers.
    
    Idempotent by design: if an account for this customer already exists, it updates metadata
    and returns the existing account without creating duplicates.
    """
    from ..models import LeadCustomer, LeadConversation, Lead, LeadAccount, utcnow
    from ..models_ext import LeadChannelIdentity
    from ..security import encrypt_pii
    from ..services import crm as crm_service
    from ..services.intent_detector import LeadIntentEvaluator

    # Check settings: is auto_dm_leads enabled for this account?
    meta = account.MetaJson or {}
    if not meta.get("linkedin_auto_dm_leads", True):
        logger.debug("LinkedIn auto DM leads conversion is disabled in settings; skipping CRM capture.")
        return None

    if not eval_res or not eval_res.is_lead:
        if not last_message:
            return None
        eval_res = LeadIntentEvaluator.evaluate_text(last_message, contact_name=contact_name)
        if not eval_res.is_lead:
            return None

    effective_contact_name = contact_name or "LinkedIn Member"
    effective_urn = contact_urn or (f"urn:li:fsd_profile:{public_id}" if public_id else f"li_{conversation_id}")
    
    if not profile_url and public_id:
        profile_url = f"https://www.linkedin.com/in/{public_id}"

    # 1. Find or link LeadCustomer and LeadChannelIdentity using unified multi-key resolver
    customer, identity = find_or_link_linkedin_customer(
        db=db,
        client_id=account.ClientId,
        channel_account_id=account.Id,
        sender_urn=effective_urn,
        public_id=public_id,
        profile_url=profile_url,
        display_name=effective_contact_name,
        conversation_id=conversation_id,
        created_by="linkedin_dm_ai",
    )

    if customer and profile_url and customer.LinkedinProfileUrl != profile_url:
        customer.LinkedinProfileUrl = profile_url
        customer.UpdatedAt = utcnow()
        logger.info(
            "[CRM Lead LinkedIn URL] Stored profile URL '%s' for LeadCustomer %s ('%s')",
            profile_url, customer.Id, effective_contact_name
        )

    # 2. Find or create LeadConversation
    db_conv = db.query(LeadConversation).filter(
        LeadConversation.ClientId == account.ClientId,
        LeadConversation.Channel == "linkedin",
        LeadConversation.ExternalThreadId == str(conversation_id),
        LeadConversation.IsDeleted == False,
    ).first()

    if not db_conv:
        db_conv = LeadConversation(
            ClientId=account.ClientId,
            CustomerId=customer.Id if customer else account.ClientId,
            Channel="linkedin",
            Status="open",
            ChannelAccountId=account.Id,
            ExternalThreadId=str(conversation_id),
            Summary=last_message[:500] if last_message else None,
            LastMessageAt=utcnow(),
        )
        db.add(db_conv)
        db.flush()
    else:
        if last_message:
            db_conv.Summary = last_message[:500]
        db_conv.LastMessageAt = utcnow()

    # 3. Create or return LeadAccount in CRM
    crm_lead = crm_service.create_account(
        db,
        account.ClientId,
        display_name=effective_contact_name,
        stage="lead",
        source="linkedin_dm",
        customer_id=customer.Id if customer else None,
        linkedin_profile_url=profile_url or (customer.LinkedinProfileUrl if customer else None),
        tags=f"linkedin,dm_lead,{eval_res.category}," + ",".join(eval_res.signals),
        fields={
            "intent_score": eval_res.score,
            "intent_category": eval_res.category,
            "intent_signals": eval_res.signals,
            "conversation_id": conversation_id,
            "last_message": last_message,
            "rationale": eval_res.rationale,
        },
        actor="linkedin_dm_ai",
    )

    if profile_url and not crm_lead.LinkedinProfileUrl:
        crm_lead.LinkedinProfileUrl = profile_url
        crm_lead.UpdatedAt = utcnow()

    # 4. Link or update Lead in leadai_leads
    db_lead = db.query(Lead).filter(
        Lead.ConversationId == db_conv.Id,
        Lead.IsDeleted == False,
    ).first()

    if not db_lead:
        db_lead = Lead(
            ClientId=account.ClientId,
            ConversationId=db_conv.Id,
            CreatedBy="linkedin_dm_ai",
        )
        db.add(db_lead)
        db.flush()

    db_lead.Status = "hot" if eval_res.score >= 0.85 else "warm"
    db_lead.Score = int(eval_res.score * 100)
    db_lead.Intent = eval_res.category
    db_lead.ScoreBreakdown = {
        "signals": eval_res.signals,
        "rationale": eval_res.rationale,
        "evaluated_at": utcnow().isoformat(),
    }
    db_lead.ConvertedAccountId = crm_lead.Id
    db_lead.ConvertedAt = utcnow()
    db_lead.UpdatedAt = utcnow()

    if not crm_lead.SourceConversationId:
        crm_lead.SourceConversationId = db_conv.Id
    if not crm_lead.SourceLeadId:
        crm_lead.SourceLeadId = db_lead.Id

    db.commit()
    logger.info(
        "Auto-converted LinkedIn DM to CRM Customer Lead: %s (%s) - Intent: %s (score=%.2f)",
        effective_contact_name, crm_lead.Id, eval_res.category, eval_res.score
    )
    return crm_lead


async def sync_linkedin_conversations(db, account) -> dict:
    """Sync conversations and messages into LeadAI database (LeadConversation and LeadMessage)."""
    from ..models import LeadCustomer, LeadConversation, LeadMessage
    from ..models_ext import LeadChannelIdentity
    from ..security import encrypt_pii

    conversations = await fetch_conversations_api(account, limit=25)

    synced_conversations = 0
    synced_messages = 0

    for summary in conversations:
        if not summary or not summary.get("conversation_id"):
            continue

        conv_id = summary["conversation_id"]
        contact_name = summary.get("contact_name") or "LinkedIn Member"
        contact_urn = summary.get("contact_urn") or summary.get("contact_public_id") or f"li_{conv_id}"

        # Find or create customer
        customer = None
        if contact_urn:
            identity = db.query(LeadChannelIdentity).filter(
                LeadChannelIdentity.ChannelAccountId == account.Id,
                LeadChannelIdentity.ExternalUserId == str(contact_urn),
                LeadChannelIdentity.IsDeleted == False
            ).first()
            if identity:
                customer = db.get(LeadCustomer, identity.CustomerId)
            else:
                import random
                customer = LeadCustomer(
                    ClientId=account.ClientId,
                    PublicRef=f"Customer #{random.randint(10000, 99999)}",
                    DisplayName=contact_name,
                    PhoneEnc=encrypt_pii(None),
                    CreatedBy="linkedin",
                )
                db.add(customer)
                db.flush()

                identity = LeadChannelIdentity(
                    ClientId=account.ClientId,
                    ChannelAccountId=account.Id,
                    Channel="linkedin",
                    ExternalUserId=str(contact_urn),
                    CustomerId=customer.Id,
                    ProfileName=contact_name,
                    CreatedBy="linkedin",
                )
                db.add(identity)
                db.flush()

        # Find or create conversation
        db_conv = db.query(LeadConversation).filter(
            LeadConversation.ClientId == account.ClientId,
            LeadConversation.Channel == "linkedin",
            LeadConversation.ExternalThreadId == conv_id
        ).first()

        last_act_dt = utcnow()

        if not db_conv:
            db_conv = LeadConversation(
                ClientId=account.ClientId,
                CustomerId=customer.Id if customer else account.ClientId,
                Channel="linkedin",
                Status="open",
                ChannelAccountId=account.Id,
                ExternalThreadId=conv_id,
                Summary=summary.get("last_message", "")[:500] if summary.get("last_message") else None,
                LastMessageAt=last_act_dt,
            )
            db.add(db_conv)
            db.flush()
            synced_conversations += 1
        else:
            db_conv.LastMessageAt = last_act_dt
            if summary.get("last_message"):
                db_conv.Summary = summary.get("last_message")[:500]

        # Fetch messages for this thread to sync turns
        try:
            from ..services.intent_detector import LeadIntentEvaluator
            from ..services import crm as crm_service
            from ..models import Lead

            has_lead_intent = False
            top_intent_result = None

            thread_messages = await fetch_conversation_messages_api(account, conv_id)
            for parsed_msg in thread_messages:
                if not parsed_msg or not parsed_msg.get("text"):
                    continue

                event_urn = parsed_msg.get("event_urn")
                existing_msg = db.query(LeadMessage).filter(
                    LeadMessage.ClientId == account.ClientId,
                    LeadMessage.ConversationId == db_conv.Id,
                    LeadMessage.ExternalMessageId == event_urn
                ).first() if event_urn else None

                sender_type = "agent" if parsed_msg.get("is_self") else "customer"
                if not existing_msg:
                    new_msg = LeadMessage(
                        ClientId=account.ClientId,
                        ConversationId=db_conv.Id,
                        Sender=sender_type,
                        Content=parsed_msg["text"],
                        ExternalMessageId=event_urn,
                        DeliveryStatus="sent" if sender_type == "agent" else None,
                        CreatedAt=utcnow()
                    )
                    db.add(new_msg)
                    synced_messages += 1

                # Evaluate customer turn for commercial buying intent
                if sender_type == "customer":
                    eval_res = LeadIntentEvaluator.evaluate_text(parsed_msg["text"], contact_name=contact_name)
                    if eval_res.is_lead:
                        has_lead_intent = True
                        if top_intent_result is None or eval_res.score > top_intent_result.score:
                            top_intent_result = eval_res

            # Also check the conversation summary/last_message if not yet flagged
            if not has_lead_intent and summary.get("last_message"):
                summary_eval = LeadIntentEvaluator.evaluate_text(summary["last_message"], contact_name=contact_name)
                if summary_eval.is_lead:
                    has_lead_intent = True
                    top_intent_result = summary_eval

            # If commercial buying intent was detected, auto-capture into CRM as a qualified Lead!
            if has_lead_intent and top_intent_result:
                public_id = summary.get("contact_public_id")
                auto_convert_linkedin_dm_to_crm_lead(
                    db=db,
                    account=account,
                    conversation_id=conv_id,
                    contact_name=contact_name,
                    contact_urn=contact_urn,
                    public_id=public_id,
                    last_message=summary.get("last_message"),
                    eval_res=top_intent_result,
                )
        except Exception as thread_exc:
            logger.warning("Failed syncing messages for conversation %s: %s", conv_id, thread_exc)

    db.commit()
    return {"synced_conversations": synced_conversations, "synced_messages": synced_messages}


# ===========================================================================
# LinkedIn Comments & Replies Automation via Browser
# ===========================================================================

async def fetch_recent_posts_and_comments_browser(db, account, limit_posts: int = 2) -> dict:
    """
    Extract recent posts and comments using hybrid In-Browser Voyager API
    with intelligent DOM fallback, sync into LeadSocialComment table,
    and trigger AI contextual reply generation.
    Enforces strict process isolation from messaging/InMail to prevent session contention and detection.
    """
    from ..models_blog import LeadSocialComment, LeadCommentSettings, LeadArticle
    from ..services.comment_reply_ai import CommentReplyAIService

    limit_posts = min(max(1, limit_posts), 2)

    cookie = decrypt_pii(account.LinkedinCookieEnc) if account.LinkedinCookieEnc else None
    if not cookie and not (account.LinkedinUsernameEnc and account.LinkedinPasswordEnc):
        raise ValueError("LinkedIn session credentials not configured")

    if _browser_manager.is_messaging_active():
        logger.info("[LinkedIn Isolation] Active messaging detected. Deferring background comments scan.")
        return {"synced_comments": 0, "status": "deferred", "reason": "messaging_in_use"}

    settings = CommentReplyAIService.get_or_create_settings(db, account.ClientId, "linkedin")

    extracted_posts = []
    try:
        async with _browser_manager.get_comments_lock():
            if _browser_manager.is_messaging_active():
                logger.info("[LinkedIn Isolation] Messaging became active before comments lock. Deferring.")
                return {"synced_comments": 0, "status": "deferred", "reason": "messaging_in_use"}

            page = await _browser_manager.get_comments_page(account)
            if not page:
                return {"synced_comments": 0, "status": "deferred", "reason": "messaging_in_use"}

            try:
                # Ensure page is on linkedin domain
                if "linkedin.com" not in page.url or "about:blank" in page.url:
                    try:
                        await _browser_manager.navigate_with_session(page, account, "https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(1.5)
                    except Exception as feed_err:
                        logger.debug("Initial feed load notice: %s", feed_err)

                # 1. Discover posts to scan (minimal & light to avoid detection)
                posts_to_scan = []
                existing_urns = set()

                # Correlate first with published DB articles
                db_articles = db.query(LeadArticle).filter(
                    LeadArticle.ClientId == account.ClientId,
                    LeadArticle.LinkedInPostId != None,
                    LeadArticle.IsDeleted == False
                ).order_by(LeadArticle.CreatedAt.desc()).limit(limit_posts).all()

                for art in db_articles:
                    if art.LinkedInPostId and (art.LinkedInPostId.startswith("urn:li:activity:") or art.LinkedInPostId.startswith("urn:li:ugcPost:")):
                        if art.LinkedInPostId not in existing_urns and len(posts_to_scan) < limit_posts:
                            posts_to_scan.append({
                                "post_urn": art.LinkedInPostId,
                                "post_url": f"https://www.linkedin.com/feed/update/{art.LinkedInPostId}/",
                                "title": art.Title,
                                "article_id": art.Id,
                            })
                            existing_urns.add(art.LinkedInPostId)

                # If needed, check ONLY the notifications tab (never spam 5 URLs)
                if len(posts_to_scan) < limit_posts:
                    try:
                        await _browser_manager.navigate_with_session(page, account, "https://www.linkedin.com/notifications/", wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(1.5)

                        activity_urns = await safe_evaluate(page, '''() => {
                            const urns = [];
                            const seen = new Set();
                            const items = document.querySelectorAll('.feed-shared-update-v2, [data-urn*="urn:li:activity"], [data-urn*="urn:li:ugcPost"], [data-id*="urn:li:activity"], .nt-card');
                            items.forEach(el => {
                                const u = el.getAttribute('data-urn') || el.getAttribute('data-id') || el.getAttribute('data-activity-urn') || '';
                                const match = u.match(/urn:li:(activity|ugcPost|share):[0-9]+/);
                                if (match && !seen.has(match[0])) {
                                    seen.add(match[0]);
                                    urns.push(match[0]);
                                }
                            });
                            const links = document.querySelectorAll('a[href*="/feed/update/"], a[href*="activity:"], .nt-card__headline');
                            links.forEach(l => {
                                const href = l.href || '';
                                const match = href.match(/urn:li:(activity|ugcPost|share):[0-9]+/);
                                if (match && !seen.has(match[0])) {
                                    seen.add(match[0]);
                                    urns.push(match[0]);
                                }
                            });
                            return urns;
                        }''', fallback=[])

                        for act_urn in (activity_urns or []):
                            if act_urn not in existing_urns and len(posts_to_scan) < limit_posts:
                                p_url = f"https://www.linkedin.com/feed/update/{act_urn}/" if not act_urn.startswith("http") else act_urn
                                posts_to_scan.append({
                                    "post_urn": act_urn,
                                    "post_url": p_url,
                                    "title": None,
                                    "article_id": None,
                                })
                                existing_urns.add(act_urn)
                    except Exception as tab_err:
                        logger.debug("Checking notifications notice: %s", tab_err)

                logger.info("[LinkedIn Safe Scan] Found %d post(s) to scan for comments", len(posts_to_scan))

                # 2. Extract comments for each post using Hybrid In-Browser Voyager API
                for p_info in posts_to_scan:
                    if _browser_manager.is_messaging_active():
                        logger.info("[LinkedIn Isolation] Yielding comment scan to active messaging session.")
                        break

                    post_urn = p_info["post_urn"]
                    target_url = p_info["post_url"]
                    post_comments = []
                    post_text = ""

                # --- Passive Network Interception on GraphQL Comments ---
                captured_comments = []

                async def handle_comment_response(response):
                    url = response.url
                    if ("voyagerFeedDashComments" in url or "voyagerSocialDashComments" in url or "comments" in url or "graphql" in url) and response.status == 200:
                        try:
                            data = await response.json()
                            parsed = _parse_graphql_comments_payload(data)
                            if parsed:
                                captured_comments.extend(parsed)
                        except Exception:
                            pass

                page.on("response", handle_comment_response)

                try:
                    await _browser_manager.navigate_with_session(page, account, target_url, wait_until="domcontentloaded", timeout=20000)
                    # Natural human scroll into comments section to trigger lazy loading
                    await safe_evaluate(page, "() => window.scrollBy(0, 600)")
                    await asyncio.sleep(1.0)
                    
                    # 1. Switch comment filter dropdown from 'Most relevant' to 'All comments' / 'Most recent' if present
                    await safe_evaluate(page, '''() => {
                        const sortDropdown = document.querySelector('button[aria-label*="sort" i], button.comments-sort-order-toggle, button[aria-controls*="sort" i]');
                        if (sortDropdown) {
                            sortDropdown.click();
                        }
                    }''')
                    await asyncio.sleep(0.5)
                    await safe_evaluate(page, '''() => {
                        const menuItems = Array.from(document.querySelectorAll('div[role="menuitem"], li[role="menuitem"], button[role="menuitem"]'));
                        for (const item of menuItems) {
                            const t = (item.innerText || '').toLowerCase();
                            if (t.includes('all comments') || t.includes('most recent') || t.includes('recent')) {
                                item.click();
                                break;
                            }
                        }
                    }''')
                    await asyncio.sleep(1.0)

                    # 2. Multi-pass recursive expansion for 'Load more comments', 'Previous comments', and nested replies
                    for _ in range(3):
                        clicked_any = await safe_evaluate(page, '''() => {
                            let clicked = false;
                            const buttons = Array.from(document.querySelectorAll('button, span[role="button"], a[role="button"]'));
                            for (const b of buttons) {
                                const text = (b.innerText || '').trim().toLowerCase();
                                if (
                                    text.includes('previous comments') ||
                                    text.includes('more comments') ||
                                    text.includes('load comments') ||
                                    text.includes('load previous') ||
                                    text.includes('previous replies') ||
                                    text.includes('more replies') ||
                                    text.includes('show replies') ||
                                    text.includes('show previous') ||
                                    /\d+\s+repl(y|ies)/.test(text)
                                ) {
                                    try {
                                        b.click();
                                        clicked = true;
                                    } catch(e) {}
                                }
                            }
                            return clicked;
                        }''', fallback=False)
                        if clicked_any:
                            await asyncio.sleep(random.uniform(1.2, 2.0))
                        else:
                            break
                finally:
                    page.remove_listener("response", handle_comment_response)

                # 1b. Check Embedded GraphQL Hydration JSON (<code id="bpr-guid-...">)
                embedded_json_payloads = await safe_evaluate(page, '''() => {
                    const payloads = [];
                    const codeTags = document.querySelectorAll('code[id^="bpr-guid-"]');
                    codeTags.forEach(el => {
                        try {
                            const raw = el.textContent || el.innerText;
                            if (raw && (raw.includes('Comment') || raw.includes('comment') || raw.includes('included') || raw.includes('dash.feed'))) {
                                payloads.push(JSON.parse(raw));
                            }
                        } catch(e) {}
                    });
                    return payloads;
                }''', fallback=[])

                if embedded_json_payloads:
                    for p in embedded_json_payloads:
                        parsed = _parse_graphql_comments_payload(p)
                        if parsed:
                            captured_comments.extend(parsed)

                all_extracted_comments = []
                if captured_comments:
                    all_extracted_comments.extend(captured_comments)

                # 1c. DOM Scraper for any dynamically rendered comments
                try:
                    dom_comments = await safe_evaluate(page, r'''() => {
                        const comments = [];
                        const seenTexts = new Set();
                        
                        const postAuthorEl = document.querySelector('.update-components-actor__name, .feed-shared-actor__name, .feed-shared-actor__title');
                        const postAuthorName = postAuthorEl ? postAuthorEl.innerText.split('\n')[0].replace(/\s+2nd.*/, '').replace(/•.*/, '').trim().toLowerCase() : '';

                        // 1. Target all comment item container blocks
                        const containers = Array.from(document.querySelectorAll(
                            'article.comments-comment-item, .comments-comment-item, [data-id*="urn:li:comment"], [data-id*="urn:li:fsd_comment"], .comments-comments-list__comment-item'
                        ));

                        for (const container of containers) {
                            const textEl = container.querySelector(
                                '.comments-comment-item__main-content, [data-testid="expandable-text-box"], .comments-comment-item-content-body, span.update-components-text, span[dir="ltr"]'
                            ) || container.querySelector('p, span');
                            
                            const commentText = textEl ? (textEl.innerText || '').trim() : '';
                            if (!commentText || commentText.length > 800 || seenTexts.has(commentText)) continue;
                            seenTexts.add(commentText);

                            const authorLink = container.querySelector('a[href*="/in/"]');
                            let authorName = 'LinkedIn Member';
                            let profileUrl = '';
                            if (authorLink) {
                                const rawName = authorLink.innerText ? authorLink.innerText.split('\n')[0] : '';
                                authorName = rawName.replace(/\s+2nd.*/, '').replace(/\s+1st.*/, '').replace(/\s+3rd.*/, '').replace(/•.*/, '').replace(/View.*profile/i, '').trim() || 'LinkedIn Member';
                                profileUrl = authorLink.href ? authorLink.href.split('?')[0] : '';
                            }

                            const headlineEl = container.querySelector('.comments-comment-meta__description, .comments-comment-item__headline, .comments-post-meta__headline');
                            const headline = headlineEl ? headlineEl.innerText.trim() : '';

                            const img = container.querySelector('img');
                            const avatar = img ? img.src : null;
                            const cUrn = container.getAttribute('data-id') || container.getAttribute('id') || `c-${authorName.toLowerCase().replace(/[^a-z0-9]/g, '-')}-${commentText.slice(0, 15)}`;

                            const isAuthorBadge = Boolean(
                                container.querySelector('.comments-comment-item__badge, .comments-comment-item__author-badge, [aria-label*="Author"], .comments-post-meta__author-badge') ||
                                (container.innerText && /\bAuthor\b/i.test(container.innerText.split('\n').slice(0, 4).join(' ')))
                            );
                            const isAuthor = isAuthorBadge || (postAuthorName && authorName.toLowerCase() === postAuthorName);

                            comments.push({
                                comment_urn: cUrn,
                                author_name: authorName,
                                author_headline: headline,
                                author_profile_url: profileUrl,
                                author_avatar: avatar,
                                comment_text: commentText,
                                is_author: isAuthor,
                            });
                        }

                        // 2. Secondary scan if container matching was empty
                        if (comments.length === 0) {
                            const allTextNodes = Array.from(document.querySelectorAll('.comments-comment-item__main-content, [data-testid="expandable-text-box"]'));
                            for (const tNode of allTextNodes) {
                                const txt = (tNode.innerText || '').trim();
                                if (!txt || txt.length > 800 || seenTexts.has(txt)) continue;
                                seenTexts.add(txt);
                                
                                let p = tNode.parentElement;
                                let aLink = null;
                                for (let i = 0; i < 6 && p; i++) {
                                    aLink = p.querySelector('a[href*="/in/"]');
                                    if (aLink) break;
                                    p = p.parentElement;
                                }
                                const name = aLink ? aLink.innerText.split('\n')[0].trim() : 'LinkedIn Member';
                                const url = aLink ? aLink.href.split('?')[0] : '';
                                comments.push({
                                    comment_urn: `c-${name.toLowerCase().replace(/[^a-z0-9]/g, '-')}-${txt.slice(0, 15)}`,
                                    author_name: name,
                                    author_headline: '',
                                    author_profile_url: url,
                                    author_avatar: null,
                                    comment_text: txt,
                                    is_author: false,
                                });
                            }
                        }

                        return comments;
                    }''', fallback=[])
                    if dom_comments:
                        all_extracted_comments.extend(dom_comments)
                except Exception as dom_err:
                    logger.debug("DOM scraping fallback notice: %s", dom_err)

                # Deduplicate and merge comments across GraphQL & DOM
                seen_signatures = set()
                for c in all_extracted_comments:
                    c_urn = c.get("comment_urn")
                    c_author = (c.get("author_name") or "").strip().lower()
                    c_text = (c.get("comment_text") or "").strip().lower()
                    if not c_text:
                        continue
                    sig = c_urn if (c_urn and not c_urn.startswith("c-")) else f"{c_author}::{c_text[:30]}"
                    if sig not in seen_signatures:
                        seen_signatures.add(sig)
                        post_comments.append(c)

                logger.info("Found total %d distinct comments for post %s", len(post_comments), post_urn)

                extracted_posts.append({
                    "post_urn": post_urn,
                    "post_title": p_info.get("title"),
                    "article_id": p_info.get("article_id"),
                    "comments": post_comments,
                    "post_text": post_text
                })
            finally:
                await _browser_manager.close_comments_page()

        # 3. Database Sync & AI processing (Runs outside browser lock)
        synced_comments_count = 0
        new_leads_count = 0
        auto_replies_count = 0
        account_owner_name = (account.Name or "").strip().lower()

        for p_data in extracted_posts:
            post_urn = p_data["post_urn"]
            post_snippet = p_data.get("post_text", "")
            article_title = p_data.get("post_title")
            post_title = article_title or (post_snippet.split("\n")[0][:120] if post_snippet else "LinkedIn Post")
            article_id = p_data.get("article_id")

            if not article_id:
                linked_article = db.query(LeadArticle).filter(
                    LeadArticle.ClientId == account.ClientId,
                    LeadArticle.LinkedInPostId == post_urn
                ).first()
                if linked_article:
                    article_id = linked_article.Id
                    post_title = linked_article.Title

            for c_data in p_data.get("comments", []):
                c_urn = c_data["comment_urn"]
                c_text = c_data["comment_text"]
                author_name = (c_data.get("author_name") or "").strip()

                # Extra safety guard against corrupted post text
                if len(c_text) > 800 or c_text.startswith("🚀 Scaling AI Solutions") or c_text.startswith("🚀 Integrating AI"):
                    continue

                # 1. Skip comments posted by the post author / account owner (self-replies)
                if c_data.get("is_author") or (author_name.lower() in ("harjit singh", account_owner_name) and author_name != "LinkedIn Member"):
                    logger.info("Skipping post author self-reply comment from %s: %s", author_name, c_text[:40])
                    continue

                # 2. Skip comments matching any previously sent reply text in LeadAI
                already_replied = db.query(LeadSocialComment).filter(
                    LeadSocialComment.ClientId == account.ClientId,
                    LeadSocialComment.ReplyText == c_text,
                    LeadSocialComment.IsDeleted == False,
                ).first()
                if already_replied:
                    logger.info("Skipping comment that matches an already sent reply: %s", c_text[:40])
                    continue

                existing_comment = db.query(LeadSocialComment).filter(
                    LeadSocialComment.ClientId == account.ClientId,
                    LeadSocialComment.AccountId == account.Id,
                    LeadSocialComment.CommentUrn == c_urn,
                    LeadSocialComment.IsDeleted == False
                ).first()

                if not existing_comment:
                    new_comment = LeadSocialComment(
                        ClientId=account.ClientId,
                        AccountId=account.Id,
                        Channel="linkedin",
                        PostUrn=post_urn,
                        PostTitle=post_title,
                        PostSnippet=post_snippet[:1000] if post_snippet else post_title,
                        ArticleId=article_id,
                        CommentUrn=c_urn,
                        AuthorName=c_data["author_name"],
                        AuthorHeadline=c_data.get("author_headline", ""),
                        AuthorAvatar=c_data.get("author_avatar"),
                        AuthorProfileUrl=c_data.get("author_profile_url"),
                        CommentText=c_text,
                        Status="pending_review",
                        CreatedBy="linkedin_sync",
                    )
                    db.add(new_comment)
                    db.flush()

                    # Trigger AI contextual reply generation
                    ai_result = CommentReplyAIService.generate_reply_for_comment(db, new_comment)
                    synced_comments_count += 1

                    if ai_result.get("is_lead_candidate"):
                        new_leads_count += 1

                    # Check for auto-reply eligibility if enabled
                    if settings.IsAutoReplyEnabled:
                        exclude_kw = settings.ExcludeKeywords or []
                        has_excluded = any(kw.lower() in new_comment.CommentText.lower() for kw in exclude_kw)
                        is_blocked_by_question_rule = settings.RequireApprovalForQuestions and new_comment.IsQuestion

                        if not has_excluded and not is_blocked_by_question_rule and new_comment.SuggestedReply:
                            new_comment.Status = "auto_replied"
                            new_comment.ReplyText = new_comment.SuggestedReply
                            new_comment.RepliedAt = utcnow()
                            new_comment.RepliedBy = "ai_auto"
                            auto_replies_count += 1
                            db.commit()

        db.commit()
        return {
            "synced_comments": synced_comments_count,
            "new_leads": new_leads_count,
            "auto_replies": auto_replies_count,
            "total_posts_scanned": len(extracted_posts)
        }

    except Exception as exc:
        err_str = str(exc)
        logger.error(f"Failed to fetch LinkedIn posts and comments: {exc}")
        if "ERR_TOO_MANY_REDIRECTS" in err_str or "auth" in err_str.lower() or "login" in err_str.lower():
            return {
                "error": "LinkedIn session token (li_at) has expired or was revoked because another account was logged in. Please paste a fresh li_at token in the Connection tab.",
                "synced_comments": 0,
            }
        return {"error": err_str, "synced_comments": 0}


async def post_comment_reply_browser(
    account,
    post_urn_or_url: str,
    comment_urn: str,
    reply_text: str,
    target_comment_text: Optional[str] = None,
    target_author: Optional[str] = None,
) -> dict:
    """Post a comment reply to LinkedIn using persistent stealth browser session."""
    cookie = decrypt_pii(account.LinkedinCookieEnc) if account.LinkedinCookieEnc else None
    if not cookie and not (account.LinkedinUsernameEnc and account.LinkedinPasswordEnc):
        raise ValueError("LinkedIn session credentials not configured")

    try:
        async with _browser_manager.get_lock():
            page = await _browser_manager.get_page(account)

            # Target post or activity feed
            clean_post = post_urn_or_url.strip()
            if clean_post.startswith("urn:li:"):
                target_url = f"https://www.linkedin.com/feed/update/{clean_post}/"
            elif clean_post.startswith("http"):
                target_url = clean_post
            else:
                target_url = f"https://www.linkedin.com/feed/update/{clean_post}/"

            logger.info("Opening LinkedIn post for reply: %s", target_url)
            await _browser_manager.navigate_with_session(page, account, target_url, wait_until="domcontentloaded", timeout=25000)

            # Anti-bot humanized jitter: wait 2.0 to 3.5 seconds after page load
            await asyncio.sleep(random.uniform(2.0, 3.5))

            # Humanized scroll down into comment section
            await safe_evaluate(page, "() => window.scrollBy({ top: 450, behavior: 'smooth' })")
            await asyncio.sleep(random.uniform(1.2, 2.0))

            # Locate the reply button for the target comment
            clicked_reply = await safe_evaluate(page, r'''({ targetText, targetAuthor }) => {
                const textNodes = Array.from(document.querySelectorAll('[data-testid="expandable-text-box"], .comments-comment-item__main-content, article.comments-comment-item, .comments-comments-list__comment-item'));

                let matchedContainer = null;
                for (const tNode of textNodes) {
                    const text = (tNode.innerText || '').trim();
                    if (targetText && text && text.toLowerCase().includes(targetText.toLowerCase().slice(0, 30))) {
                        matchedContainer = tNode;
                        break;
                    }
                    if (targetAuthor && text && text.toLowerCase().includes(targetAuthor.toLowerCase())) {
                        matchedContainer = tNode;
                        break;
                    }
                }

                if (matchedContainer) {
                    let curr = matchedContainer;
                    for (let i = 0; i < 8 && curr; i++) {
                        const replyBtn = curr.querySelector('button[aria-label="Reply"], button.comments-comment-item__reply-button');
                        if (replyBtn) {
                            replyBtn.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            replyBtn.click();
                            return true;
                        }
                        curr = curr.parentElement;
                    }
                }

                // Fallback: click any available Reply button on the page
                const allReplyBtns = Array.from(document.querySelectorAll('button[aria-label="Reply"], button.comments-comment-item__reply-button'));
                if (allReplyBtns.length > 0) {
                    const btn = allReplyBtns[allReplyBtns.length - 1];
                    btn.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    btn.click();
                    return true;
                }
                return false;
            }''', arg={
                "targetText": (target_comment_text or "").strip()[:50],
                "targetAuthor": (target_author or "").strip()
            }, fallback=False)

            if not clicked_reply:
                # Direct locator fallback
                reply_btn = page.locator('button[aria-label="Reply"], button:has-text("Reply")').last
                if await reply_btn.count() > 0 and await reply_btn.is_visible():
                    await reply_btn.scroll_into_view_if_needed()
                    await asyncio.sleep(random.uniform(0.4, 0.8))
                    await reply_btn.click()
                    clicked_reply = True

            # Wait for comment reply box to expand
            await asyncio.sleep(random.uniform(1.5, 2.5))

            # Locate editor
            editor_selectors = [
                "div[role='textbox'][contenteditable='true']",
                ".ql-editor[contenteditable='true']",
                ".comments-comment-box__editor",
                "div[contenteditable='true']",
            ]
            editor = None
            for sel in editor_selectors:
                loc = page.locator(sel).last
                if await loc.count() > 0 and await loc.is_visible():
                    editor = loc
                    break

            if not editor:
                logger.error("Could not find visible comment editor for post %s", target_url)
                return {
                    "success": False,
                    "error": "Could not locate active comment reply editor on LinkedIn.",
                }

            # Avoid duplicate recipient names:
            # LinkedIn's reply editor automatically tags the recipient as an @mention badge.
            # If the reply text also starts with "Vanshika,", "Hi Vanshika,", etc., strip that prefix.
            clean_reply_text = reply_text.strip()
            if target_author and target_author.strip() and target_author.strip().lower() != "linkedin member":
                author_clean = target_author.strip()
                first_name = author_clean.split()[0]
                pattern = rf"^(?:(?:hi|hello|hey|dear)\s+)?(?:{re.escape(author_clean)}|{re.escape(first_name)})[,\s:!–—\-]+\s*"
                clean_reply_text = re.sub(pattern, "", clean_reply_text, flags=re.IGNORECASE).strip()
                if clean_reply_text and clean_reply_text[0].islower():
                    clean_reply_text = clean_reply_text[0].upper() + clean_reply_text[1:]

            # Humanized typing into editor
            await editor.scroll_into_view_if_needed()
            await asyncio.sleep(random.uniform(0.3, 0.6))
            await editor.click()
            await asyncio.sleep(random.uniform(0.5, 0.9))

            # Type each character with natural typing variation (anti-bot)
            for char in clean_reply_text:
                await page.keyboard.type(char)
                delay = random.uniform(0.04, 0.09)
                if char in (" ", ",", ".", "!", "?"):
                    delay += random.uniform(0.08, 0.16)
                await asyncio.sleep(delay)

            # Natural pause after typing
            await asyncio.sleep(random.uniform(1.2, 2.0))

            # Locate and click submit button
            submit_selectors = [
                "button.comments-comment-box__submit-button",
                "button:has-text('Reply')",
                "button:has-text('Post')",
                "button[type='submit']",
            ]
            submitted = False
            for sel in submit_selectors:
                btn = page.locator(sel).last
                if await btn.count() > 0 and await btn.is_visible():
                    if await btn.is_enabled():
                        await btn.hover()
                        await asyncio.sleep(random.uniform(0.2, 0.5))
                        await btn.click()
                        submitted = True
                        break

            if not submitted:
                # Keyboard shortcut fallback (Control+Enter sends comments in LinkedIn)
                await page.keyboard.press("Control+Enter")
                submitted = True

            # Wait for request completion and post render
            await asyncio.sleep(random.uniform(3.0, 4.5))
            logger.info("Comment reply successfully submitted to LinkedIn for %s", target_url)

            return {
                "success": True,
                "message": "Comment reply posted successfully via stealth browser session",
                "reply_urn": f"urn:li:commentReply:{int(time.time())}",
                "posted_text": clean_reply_text,
            }

    except Exception as exc:
        err_str = str(exc)
        logger.error(f"Browser comment reply error: {err_str}")
        if "ERR_TOO_MANY_REDIRECTS" in err_str or "auth" in err_str.lower() or "login" in err_str.lower():
            return {
                "success": False,
                "error": "LinkedIn session token (li_at) has expired or was revoked. Please enter a fresh li_at token in the Connection tab.",
            }
        return {"success": False, "error": err_str}
