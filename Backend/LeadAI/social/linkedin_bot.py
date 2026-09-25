import os
import logging
import asyncio
from typing import List, Optional
from linkedin_api import Linkedin
from ..security import decrypt_pii
from ..models import utcnow

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
            kwargs["allow_redirects"] = False
            res = orig_send(request, **kwargs)
            if res.status_code in (301, 302, 303, 307, 308):
                loc = (res.headers.get("Location") or "").lower()
                if any(x in loc for x in ["login", "authwall", "checkpoint", "uas/login", "uas/authenticate"]):
                    raise RuntimeError("Your LinkedIn session (li_at) has expired or was revoked by LinkedIn. Please enter a fresh li_at token or save your credentials in the Connection tab.")
            return res

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
    import random
    from ..models import LeadCustomer, LeadChannelIdentity
    from ..security import encrypt_pii

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
        identity = db.query(LeadChannelIdentity).filter(
            LeadChannelIdentity.ChannelAccountId == account.Id,
            LeadChannelIdentity.ExternalUserId == str(sender_urn),
            LeadChannelIdentity.IsDeleted == False
        ).first()

        if not identity:
            customer = LeadCustomer(
                ClientId=account.ClientId,
                PublicRef=f"Customer #{random.randint(10000, 99999)}",
                DisplayName=display_name,
                PhoneEnc=encrypt_pii(None),
                CreatedBy="linkedin",
            )
            db.add(customer)
            db.flush()

            identity = LeadChannelIdentity(
                ClientId=account.ClientId,
                ChannelAccountId=account.Id,
                Channel="linkedin",
                ExternalUserId=str(sender_urn),
                CustomerId=customer.Id,
                ProfileName=display_name,
                CreatedBy="linkedin",
            )
            db.add(identity)
            db.flush()
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

        # Check if they already exist in database
        identity = db.query(LeadChannelIdentity).filter(
            LeadChannelIdentity.ChannelAccountId == account.Id,
            LeadChannelIdentity.ExternalUserId == str(sender_urn),
            LeadChannelIdentity.IsDeleted == False
        ).first()

        customer = None
        if identity:
            customer = db.get(LeadCustomer, identity.CustomerId)
        
        if not identity:
            # Create new customer
            customer = LeadCustomer(
                ClientId=account.ClientId,
                PublicRef=f"Customer #{random.randint(10000, 99999)}",
                DisplayName=display_name,
                PhoneEnc=encrypt_pii(None),
                CreatedBy="linkedin",
            )
            db.add(customer)
            db.flush()

            # Create new identity
            identity = LeadChannelIdentity(
                ClientId=account.ClientId,
                ChannelAccountId=account.Id,
                Channel="linkedin",
                ExternalUserId=str(sender_urn),
                CustomerId=customer.Id,
                ProfileName=display_name,
                CreatedBy="linkedin",
            )
            db.add(identity)
            db.flush()
            db.commit()
            logger.info("Created new LinkedIn lead/identity: %s (%s)", display_name, sender_urn)

        processed_count += 1

        # Accept invitation if auto_accept is active
        if auto_accept:
            try:
                # Accept invitation
                api.reply_invitation(
                    invitation_entity_urn=entity_urn,
                    invitation_shared_secret=shared_secret,
                    action="accept"
                )
                accepted_count += 1
                logger.info("Accepted LinkedIn invitation from %s (%s)", display_name, sender_urn)
                
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


async def fetch_conversations_api(account, limit: int = 25) -> list[dict]:
    """Fetch recent LinkedIn conversation threads with sender information and last message."""
    cookie = decrypt_pii(account.LinkedinCookieEnc) if account.LinkedinCookieEnc else None
    if not cookie:
        return []
        
    if "|||" in cookie:
        li_at, jsession = cookie.split("|||", 1)
    else:
        li_at = cookie
        jsession = "ajax:1234567890"

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            
            await context.add_cookies([
                {"name": "li_at", "value": li_at, "domain": ".linkedin.com", "path": "/"},
                {"name": "JSESSIONID", "value": f'"{jsession.strip(chr(34))}"', "domain": ".linkedin.com", "path": "/"},
            ])
            
            page = await context.new_page()
            await page.goto("https://www.linkedin.com/messaging/", wait_until="commit", timeout=25000)
            
            try:
                await page.wait_for_selector("li.msg-conversation-listitem, .msg-conversation-listitem", timeout=10000)
            except Exception:
                pass
                
            await asyncio.sleep(2)
            
            conversations_data = await page.evaluate('''() => {
                const items = document.querySelectorAll('li.msg-conversation-listitem');
                const listItems = items.length > 0 ? items : document.querySelectorAll('.msg-conversation-listitem');
                const results = [];
                const seen = new Set();
                
                listItems.forEach((el, index) => {
                    const nameEl = el.querySelector('.msg-conversation-listitem__participant-names, .msg-conversation-card__participant-names, h3');
                    const lastMsgEl = el.querySelector('.msg-conversation-card__message-snippet, .msg-conversation-listitem__message-snippet');
                    const timeEl = el.querySelector('time, .msg-conversation-listitem__time-stamp');
                    const linkEl = el.querySelector('a[href*="/messaging/thread/"]');
                    const imgEl = el.querySelector('img');
                    const badgeEl = el.querySelector('.msg-conversation-card__unread-count, .badge');

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

                    results.push({
                        conversation_id: threadId,
                        conversation_urn: `urn:li:msg_conversation:${threadId}`,
                        contact_name: name,
                        contact_headline: '',
                        contact_public_id: '',
                        contact_urn: '',
                        contact_avatar: imgEl ? imgEl.src : null,
                        participants: [{
                            name: name,
                            headline: '',
                            public_id: '',
                            urn: '',
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
            }''')
            
            await browser.close()
            return conversations_data[:limit]
    except Exception as exc:
        logger.warning("Browser conversation extraction error: %s", exc)
        return []


async def fetch_conversation_messages_api(account, conversation_urn_id: str) -> list[dict]:
    """Fetch full message events history for a given conversation thread."""
    clean_id = extract_clean_conversation_id(conversation_urn_id)
    cookie = decrypt_pii(account.LinkedinCookieEnc) if account.LinkedinCookieEnc else None
    if not cookie:
        return []
        
    if "|||" in cookie:
        li_at, jsession = cookie.split("|||", 1)
    else:
        li_at = cookie
        jsession = "ajax:1234567890"

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            await context.add_cookies([
                {"name": "li_at", "value": li_at, "domain": ".linkedin.com", "path": "/"},
                {"name": "JSESSIONID", "value": f'"{jsession.strip(chr(34))}"', "domain": ".linkedin.com", "path": "/"},
            ])
            page = await context.new_page()
            
            target_url = f"https://www.linkedin.com/messaging/thread/{clean_id}/" if not clean_id.startswith("conv-") else "https://www.linkedin.com/messaging/"
            await page.goto(target_url, wait_until="commit", timeout=25000)
            
            if clean_id.startswith("conv-"):
                idx = int(clean_id.replace("conv-", "") or "0")
                items = page.locator("li.msg-conversation-listitem, .msg-conversation-listitem")
                if await items.count() > idx:
                    await items.nth(idx).click()
                    await asyncio.sleep(1.5)
            
            try:
                await page.wait_for_selector("li.msg-s-message-list__event, .msg-s-message-list__event", timeout=10000)
            except Exception:
                pass
                
            messages = await page.evaluate('''() => {
                const list = [];
                const seen = new Set();
                const items = document.querySelectorAll('li.msg-s-message-list__event');
                const listItems = items.length > 0 ? items : document.querySelectorAll('.msg-s-message-list__event');
                const myImg = document.querySelector('.global-nav__me-photo, img[alt*="Photo of"]');
                const myName = myImg ? (myImg.alt || '').replace('Photo of', '').trim().toLowerCase() : '';

                listItems.forEach((el, idx) => {
                    const senderEl = el.querySelector('.msg-s-message-group__name, .msg-s-message-group__profile-link, h4');
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
                            sender_urn: '',
                            sender_public_id: '',
                            sender_avatar: imgEl ? imgEl.src : null,
                            is_self: isSelf,
                        });
                    });
                });
                return list;
            }''')
            await browser.close()
            return messages
    except Exception as exc:
        logger.warning("Browser messages extraction error: %s", exc)
        return []


async def send_conversation_message_api(account, conversation_urn_id: str, message_body: str) -> dict:
    """Send a reply message directly to a LinkedIn conversation thread."""
    clean_id = extract_clean_conversation_id(conversation_urn_id)
    cookie = decrypt_pii(account.LinkedinCookieEnc) if account.LinkedinCookieEnc else None
    if not cookie:
        raise ValueError("LinkedIn session credentials not configured")
        
    if "|||" in cookie:
        li_at, jsession = cookie.split("|||", 1)
    else:
        li_at = cookie
        jsession = "ajax:1234567890"

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            await context.add_cookies([
                {"name": "li_at", "value": li_at, "domain": ".linkedin.com", "path": "/"},
                {"name": "JSESSIONID", "value": f'"{jsession.strip(chr(34))}"', "domain": ".linkedin.com", "path": "/"},
            ])
            page = await context.new_page()
            
            target_url = f"https://www.linkedin.com/messaging/thread/{clean_id}/" if not clean_id.startswith("conv-") else "https://www.linkedin.com/messaging/"
            await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
            
            if clean_id.startswith("conv-"):
                idx = int(clean_id.replace("conv-", "") or "0")
                items = page.locator(".msg-conversation-listitem, li.msg-conversation-card")
                if await items.count() > idx:
                    await items.nth(idx).click()
                    await asyncio.sleep(1.5)

            composer = page.locator(".msg-form__contenteditable, div[role='textbox'][aria-label*='Write a message']").first
            await composer.fill(message_body)
            await asyncio.sleep(0.5)
            
            send_btn = page.locator("button.msg-form__send-button, button[type='submit']").first
            if await send_btn.count() > 0:
                await send_btn.click()
            else:
                await composer.press("Enter")
                
            await asyncio.sleep(1.5)
            await browser.close()
            return {"success": True, "message": "Message sent successfully"}
    except Exception as exc:
        logger.error("Failed to send message via browser: %s", exc)
        raise RuntimeError(f"Failed to dispatch message: {str(exc)}")


def sync_linkedin_conversations(db, account) -> dict:
    """Sync conversations and messages into LeadAI database (LeadConversation and LeadMessage)."""
    import datetime
    from ..models import LeadCustomer, LeadConversation, LeadMessage
    from ..models_ext import LeadChannelIdentity
    from ..security import encrypt_pii

    api = get_linkedin_client(account)
    my_urn = account.ExternalId

    res = api.get_conversations()
    elements = res.get("elements", []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
    
    synced_conversations = 0
    synced_messages = 0

    for c in elements:
        summary = parse_conversation_summary(c, my_urn=my_urn)
        if not summary or not summary.get("conversation_id"):
            continue

        conv_id = summary["conversation_id"]
        contact_name = summary.get("contact_name") or "LinkedIn Member"
        contact_urn = summary.get("contact_urn") or summary.get("contact_public_id")

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

        last_act_ms = summary.get("last_activity_at")
        last_act_dt = datetime.datetime.fromtimestamp(last_act_ms / 1000.0, tz=datetime.timezone.utc).replace(tzinfo=None) if last_act_ms else utcnow()

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
            thread_res = api.get_conversation(conv_id)
            thread_events = thread_res.get("elements", []) if isinstance(thread_res, dict) else (thread_res if isinstance(thread_res, list) else [])
            for e in thread_events:
                parsed_msg = parse_message_event(e, my_urn=my_urn)
                if not parsed_msg or not parsed_msg.get("text"):
                    continue

                event_urn = parsed_msg.get("event_urn")
                existing_msg = db.query(LeadMessage).filter(
                    LeadMessage.ClientId == account.ClientId,
                    LeadMessage.ConversationId == db_conv.Id,
                    LeadMessage.ExternalMessageId == event_urn
                ).first() if event_urn else None

                if not existing_msg:
                    msg_time_ms = parsed_msg.get("created_at")
                    msg_dt = datetime.datetime.fromtimestamp(msg_time_ms / 1000.0, tz=datetime.timezone.utc).replace(tzinfo=None) if msg_time_ms else utcnow()
                    
                    sender_type = "agent" if parsed_msg.get("is_self") else "customer"
                    new_msg = LeadMessage(
                        ClientId=account.ClientId,
                        ConversationId=db_conv.Id,
                        Sender=sender_type,
                        Content=parsed_msg["text"],
                        ExternalMessageId=event_urn,
                        DeliveryStatus="sent" if sender_type == "agent" else None,
                        CreatedAt=msg_dt
                    )
                    db.add(new_msg)
                    synced_messages += 1
        except Exception as thread_exc:
            logger.warning("Failed syncing messages for conversation %s: %s", conv_id, thread_exc)

    db.commit()
    return {"synced_conversations": synced_conversations, "synced_messages": synced_messages}


