"""
Interactive Remote Login & CAPTCHA/2FA Solver Session Manager for LinkedIn.

Allows users to visually solve LinkedIn security checkpoints (Arkose FunCaptcha,
Email PIN/OTP verification, SMS challenge) directly inside the LeadAI web application.
"""
from __future__ import annotations

import os
import asyncio
import logging
import time
import uuid
from typing import Optional, Dict, Any

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

logger = logging.getLogger("leadai.social.linkedin_remote_session")

class RemoteLoginSession:
    def __init__(self, session_id: str, client_id: str, username: str, password: str):
        self.session_id = session_id
        self.client_id = client_id
        self.username = username
        self.password = password

        self.status: str = "initializing"  # initializing | submitting | checkpoint_required | success | failed | cancelled | expired
        self.message: str = "Initializing browser session..."
        self.challenge_type: str = "unknown"  # captcha | email_pin | sms_pin | 2fa | general_checkpoint | none
        self.error: Optional[str] = None
        
        self.extracted_cookie: Optional[str] = None
        self.last_screenshot: Optional[bytes] = None
        self.created_at: float = time.time()
        self.expires_at: float = time.time() + 600  # 10 minute lifespan

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._lock = asyncio.Lock()
        self._is_closed = False

    async def start(self) -> Dict[str, Any]:
        """Launch browser, navigate to LinkedIn, submit credentials, and detect status."""
        async with self._lock:
            try:
                self.status = "submitting"
                self.message = "Launching secure browser and submitting credentials..."
                self._playwright = await async_playwright().start()
                
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                    ]
                )
                
                self._context = await self._browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    viewport={"width": 1024, "height": 720},
                    locale="en-US",
                )
                
                self.page = await self._context.new_page()
                await self.page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

                logger.info("[RemoteSession %s] Navigating to LinkedIn login page", self.session_id)
                await self.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=25000)
                await asyncio.sleep(1.2)

                email_loc = self.page.locator("#username, input[type='email']:visible, input[name='session_key']:visible, input[autocomplete='username']:visible").first
                pass_loc = self.page.locator("#password, input[type='password']:visible, input[name='session_password']:visible, input[autocomplete='current-password']:visible").first

                if await email_loc.count() == 0 or await pass_loc.count() == 0:
                    self.status = "failed"
                    self.message = "Login input fields not found on LinkedIn page."
                    await self._take_screenshot()
                    return self.to_dict()

                await email_loc.fill(self.username)
                await asyncio.sleep(0.3)
                await pass_loc.fill(self.password)
                await asyncio.sleep(0.3)

                submit_btn = self.page.locator("button[type='submit']:visible, button[data-litms-control-urn='login-submit']:visible").first
                if await submit_btn.count() > 0:
                    await submit_btn.click()
                else:
                    await pass_loc.press("Enter")

                # Wait up to 5 seconds to observe login result
                for _ in range(5):
                    await asyncio.sleep(1)
                    if await self._check_cookies_and_update():
                        return self.to_dict()

                # Check if challenge / checkpoint / captcha / error appeared
                await self._detect_checkpoint_or_error()
                await self._take_screenshot()
                return self.to_dict()

            except Exception as exc:
                logger.exception("[RemoteSession %s] Error during start: %s", self.session_id, exc)
                self.status = "failed"
                self.error = str(exc)
                self.message = f"Browser connection error: {str(exc)}"
                return self.to_dict()

    async def _check_cookies_and_update(self) -> bool:
        """Inspect context cookies for li_at."""
        if not self._context:
            return False
        try:
            cookies = await self._context.cookies()
            li_at = next((c["value"] for c in cookies if c["name"] == "li_at"), None)
            jsessionid = next((c["value"] for c in cookies if c["name"] == "JSESSIONID"), None)
            
            if li_at:
                logger.info("[RemoteSession %s] Found valid li_at session cookie!", self.session_id)
                self.status = "success"
                self.message = "LinkedIn login verified successfully!"
                if jsessionid:
                    self.extracted_cookie = f"{li_at}|||{jsessionid}"
                else:
                    self.extracted_cookie = li_at
                return True
        except Exception as exc:
            logger.debug("[RemoteSession %s] Cookie check error: %s", self.session_id, exc)
        return False

    async def _detect_checkpoint_or_error(self):
        """Analyze the current page DOM and URL for checkpoints or login errors."""
        if not self.page:
            return

        current_url = (self.page.url or "").lower()
        logger.info("[RemoteSession %s] Analyzing page URL: %s", self.session_id, current_url)

        # Check for error banners first
        error_loc = self.page.locator("#error-for-password, #error-for-username, div.alert--error, .artdeco-inline-feedback--error, div[role='alert']").first
        if await error_loc.count() > 0:
            error_text = await error_loc.inner_text()
            if error_text and len(error_text.strip()) > 0:
                self.status = "failed"
                self.error = error_text.strip()
                self.message = f"LinkedIn Error: {self.error}"
                return

        # Check for 2FA / Authenticator App / SMS / Email PIN verification inputs
        pin_loc = self.page.locator(
            "input#two-step-submit-pin, input[name='twoStepPin'], input[name='pin'], "
            "input#input__phone_verification_pin, input#input__email_verification_pin, "
            "input[name='verification-code'], input[name='phone-pin'], input[type='tel'], "
            "input[autocomplete='one-time-code']"
        ).first

        if (
            await pin_loc.count() > 0
            or "two-step" in current_url
            or "challenge" in current_url
            or "pin" in current_url
            or "checkpoint" in current_url
            or "verification" in current_url
        ):
            if await pin_loc.count() > 0 or "two-step" in current_url or "pin" in current_url or "email-challenge" in current_url or "phone" in current_url:
                self.challenge_type = "2fa" if "two-step" in current_url else "email_pin"
                self.status = "checkpoint_required"
                self.message = (
                    "Two-Factor Authentication (2FA) / Verification Code required. "
                    "Please enter the 6-digit code from your Authenticator App, SMS, or Email below."
                )
                return

        # Check for Arkose Labs or general CAPTCHA
        captcha_loc = self.page.locator("#captcha-internal, iframe[title*='challenge'], iframe[src*='arkose'], iframe[src*='captcha'], div#captcha_container, div.checkpoint-container").first
        if await captcha_loc.count() > 0 or "checkpoint" in current_url or "challenge" in current_url:
            self.challenge_type = "captcha"
            self.status = "checkpoint_required"
            self.message = "LinkedIn presented a security challenge / CAPTCHA. Please solve it on the interactive screen below."
            return

        # Check feed / login success by URL
        if "/feed" in current_url or "/in/" in current_url or "/mynetwork" in current_url:
            await self._check_cookies_and_update()
            return

        # Default fallback if still on challenge or unknown screen
        self.challenge_type = "general_checkpoint"
        self.status = "checkpoint_required"
        self.message = "LinkedIn security verification in progress. Please review the screen and complete the challenge or approve in your mobile app."

    async def _take_screenshot(self) -> Optional[bytes]:
        """Capture JPEG screenshot of current viewport."""
        if not self.page or self._is_closed:
            return self.last_screenshot
        try:
            self.last_screenshot = await self.page.screenshot(type="jpeg", quality=75)
            return self.last_screenshot
        except Exception as exc:
            logger.debug("[RemoteSession %s] Screenshot capture failed: %s", self.session_id, exc)
            return self.last_screenshot

    async def get_screenshot(self) -> Optional[bytes]:
        """Return fresh screenshot with lock."""
        async with self._lock:
            if self.page and not self._is_closed:
                await self._take_screenshot()
            return self.last_screenshot

    async def interact(
        self,
        action: str,
        x: Optional[int] = None,
        y: Optional[int] = None,
        text: Optional[str] = None,
        key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Process user interaction on the remote browser."""
        async with self._lock:
            if not self.page or self._is_closed:
                return {"ok": False, "status": self.status, "message": "Browser session is not active."}

            try:
                if action == "click" and x is not None and y is not None:
                    logger.debug("[RemoteSession %s] Mouse click at (%d, %d)", self.session_id, x, y)
                    await self.page.mouse.click(x, y)
                    await asyncio.sleep(0.6)

                elif action == "type" and text:
                    logger.debug("[RemoteSession %s] Typing text length=%d", self.session_id, len(text))
                    await self.page.keyboard.type(text)
                    await asyncio.sleep(0.3)

                elif action == "press_key" and key:
                    logger.debug("[RemoteSession %s] Pressing key %s", self.session_id, key)
                    await self.page.keyboard.press(key)
                    await asyncio.sleep(0.4)

                elif action == "submit_pin" and text:
                    logger.info("[RemoteSession %s] Submitting 2FA / PIN / OTP verification code", self.session_id)
                    pin_input = self.page.locator(
                        "input#two-step-submit-pin:visible, input[name='twoStepPin']:visible, "
                        "input[name='pin']:visible, input#input__phone_verification_pin:visible, "
                        "input#input__email_verification_pin:visible, input[name='verification-code']:visible, "
                        "input[name='phone-pin']:visible, input[autocomplete='one-time-code']:visible, "
                        "input[type='tel']:visible, input[type='text']:visible"
                    ).first
                    if await pin_input.count() > 0:
                        await pin_input.fill(text.strip())
                        await asyncio.sleep(0.3)
                        submit_btn = self.page.locator(
                            "button#two-step-submit-button:visible, button[type='submit']:visible, "
                            "button#email-pin-submit-button:visible, button[data-litms-control-urn*='submit']:visible, "
                            "button:has-text('Submit'):visible, button:has-text('Verify'):visible, "
                            "button:has-text('Continue'):visible"
                        ).first
                        if await submit_btn.count() > 0:
                            await submit_btn.click()
                        else:
                            await pin_input.press("Enter")
                    else:
                        # Direct keyboard typing as fallback
                        await self.page.keyboard.type(text.strip())
                        await self.page.keyboard.press("Enter")
                    
                    await asyncio.sleep(1.8)

                elif action == "refresh":
                    logger.info("[RemoteSession %s] Reloading page", self.session_id)
                    await self.page.reload(wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(1.0)

                # Check if this interaction completed login
                if await self._check_cookies_and_update():
                    return self.to_dict()

                await self._detect_checkpoint_or_error()
                await self._take_screenshot()
                return self.to_dict()

            except Exception as exc:
                logger.exception("[RemoteSession %s] Interaction failed: %s", self.session_id, exc)
                return {"ok": False, "status": self.status, "message": f"Interaction error: {str(exc)}"}

    async def check_status(self) -> Dict[str, Any]:
        """Poll and update session status."""
        async with self._lock:
            if time.time() > self.expires_at:
                self.status = "expired"
                self.message = "Session timed out. Please try connecting again."
                return self.to_dict()

            if self.status not in ("success", "failed", "cancelled", "expired") and self.page and not self._is_closed:
                if await self._check_cookies_and_update():
                    return self.to_dict()
                await self._detect_checkpoint_or_error()

            return self.to_dict()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "challenge_type": self.challenge_type,
            "message": self.message,
            "has_screenshot": self.last_screenshot is not None,
            "error": self.error,
            "completed": self.status == "success",
            "expires_in": max(0, int(self.expires_at - time.time())),
        }

    async def close(self):
        """Clean up and terminate browser resources."""
        async with self._lock:
            if self._is_closed:
                return
            self._is_closed = True
            logger.info("[RemoteSession %s] Closing browser session", self.session_id)
            try:
                if self.page:
                    await self.page.close()
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


class RemoteLoginSessionManager:
    """Manages active remote login sessions."""
    def __init__(self):
        self._sessions: Dict[str, RemoteLoginSession] = {}
        self._lock = asyncio.Lock()

    async def create_session(self, client_id: str, username: str, password: str) -> RemoteLoginSession:
        await self.cleanup_expired()
        session_id = uuid.uuid4().hex
        session = RemoteLoginSession(
            session_id=session_id,
            client_id=client_id,
            username=username,
            password=password,
        )
        async with self._lock:
            self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[RemoteLoginSession]:
        return self._sessions.get(session_id)

    async def cancel_session(self, session_id: str):
        session = self._sessions.pop(session_id, None)
        if session:
            session.status = "cancelled"
            await session.close()

    async def cleanup_expired(self):
        now = time.time()
        expired_ids = []
        for sid, sess in list(self._sessions.items()):
            if now > sess.expires_at or sess.status in ("success", "failed", "cancelled", "expired"):
                # keep success/failed for 60s for client retrieval, then clean
                if now > sess.expires_at or (now - sess.created_at > 180):
                    expired_ids.append(sid)

        for sid in expired_ids:
            sess = self._sessions.pop(sid, None)
            if sess:
                try:
                    await sess.close()
                except Exception:
                    pass


# Singleton instance
remote_login_manager = RemoteLoginSessionManager()
