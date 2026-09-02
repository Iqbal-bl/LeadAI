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
        logger.info("Initializing LinkedIn Bot API client using session cookie")
        # Build RequestsCookieJar containing the li_at cookie and a dummy JSESSIONID
        jar = requests.cookies.RequestsCookieJar()
        expires = int(time.time()) + 365 * 24 * 3600
        jar.set("li_at", cookie, domain=".www.linkedin.com", path="/", expires=expires)
        
        jsessionid = f"ajax:{random.randint(100000000000000000, 999999999999999999)}"
        jar.set("JSESSIONID", f'"{jsessionid}"', domain=".www.linkedin.com", path="/", expires=expires)
        
        return Linkedin("session_user", "session_pass", authenticate=True, cookies=jar, cookies_dir=cookies_dir)
    elif username and password:
        logger.info("Initializing LinkedIn Bot API client using credentials")
        api = Linkedin(username=username, password=password, cookies_dir=cookies_dir)
        
        # Self-healing request hook for handling 401 Unauthorized session expirations
        session = api.client.session
        original_request = session.request

        def self_healing_request(*args, **kwargs):
            res = original_request(*args, **kwargs)
            if res.status_code == 401:
                logger.warning("LinkedIn session unauthorized (401). Deleting cached cookies and re-authenticating.")
                cookie_file = os.path.join(cookies_dir, f"{username}.jr")
                if os.path.exists(cookie_file):
                    try:
                        os.remove(cookie_file)
                        logger.info("Deleted expired cached cookie file: %s", cookie_file)
                    except Exception as e:
                        logger.error("Failed to delete expired cached cookie file: %s", e)
                
                try:
                    logger.info("Attempting fresh login for %s", username)
                    api.client.authenticate(username, password)
                    # Sync cookies to the retry request
                    kwargs["cookies"] = session.cookies
                    session.headers["csrf-token"] = session.cookies["JSESSIONID"].strip('"')
                    if "headers" in kwargs:
                        kwargs["headers"]["csrf-token"] = session.headers["csrf-token"]
                    return original_request(*args, **kwargs)
                except Exception as auth_exc:
                    logger.error("Fresh authentication retry failed: %s", auth_exc)
            return res

        session.request = self_healing_request
        return api
    else:
        raise ValueError("LinkedIn Bot credentials are not configured. Link credentials first.")


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


def process_pending_invitations(db, account) -> tuple[int, int]:
    """Poll for pending connection requests, accept them, and send welcome messages."""
    import random
    from ..models import LeadCustomer, LeadChannelIdentity
    from ..security import encrypt_pii

    api = get_linkedin_client(account)
    
    # 1. Fetch invitations
    try:
        invitations = api.get_invitations()
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
        entity_urn = invite.get("entityUrn")
        shared_secret = invite.get("sharedSecret")
        if not entity_urn or not shared_secret:
            continue

        # Extract sender details
        from_member = invite.get("fromMember", {}) or invite.get("sender", {}) or invite.get("miniProfile", {})
        first_name = from_member.get("firstName") or from_member.get("miniProfile", {}).get("firstName") or invite.get("sender", {}).get("firstName", "")
        last_name = from_member.get("lastName") or from_member.get("miniProfile", {}).get("lastName") or invite.get("sender", {}).get("lastName", "")
        display_name = f"{first_name} {last_name}".strip() or "LinkedIn Member"
        
        sender_urn = (
            from_member.get("entityUrn") 
            or from_member.get("miniProfile", {}).get("entityUrn") 
            or invite.get("sender", {}).get("entityUrn") 
            or invite.get("fromMemberUrn")
        )
        if not sender_urn:
            continue

        public_id = (
            from_member.get("publicIdentifier") 
            or from_member.get("miniProfile", {}).get("publicIdentifier") 
            or invite.get("sender", {}).get("publicIdentifier")
        )

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
                    # recipient_id can be public_id or the URN ID part
                    recipient_id = public_id or sender_urn.split(":")[-1]
                    try:
                        # Attempt to send message
                        api.send_message(recipients=[recipient_id], message_body=welcome_message)
                        logger.info("Sent welcome message to connected member: %s", display_name)
                    except Exception as msg_exc:
                        # Fallback try conversation URN id
                        try:
                            api.send_message(conversation_urn_id=recipient_id, message_body=welcome_message)
                            logger.info("Sent welcome message using conversation URN id to connected member: %s", display_name)
                        except Exception as msg_exc2:
                            logger.error("Failed to send welcome message to connected member %s: %s (fallback: %s)", display_name, msg_exc, msg_exc2)
            except Exception as accept_exc:
                logger.error("Failed to accept invitation from %s: %s", display_name, accept_exc)

    return processed_count, accepted_count
