"""
Reusable helpers for handling the "annoying but universal" friction almost
every site throws at automation: cookie banners, popups/modals, and pages
still loading content. Generic pattern-matching, not site-specific — safe
to call defensively even if you're not sure a target site has one.
"""

from playwright.async_api import Page

COOKIE_BANNER_SELECTORS = [
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('I agree')",
    "button:has-text('Got it')",
    "button:has-text('Allow all')",
    "[aria-label='Accept cookies']",
    "#onetrust-accept-btn-handler",
]

POPUP_CLOSE_SELECTORS = [
    "button[aria-label='Close']",
    "button[aria-label='close']",
    "button[aria-label='Dismiss']",
    "[class*='modal-close']",
    "[class*='popup-close']",
    "button:has-text('No thanks')",
    "button:has-text('Not now')",
]


async def dismiss_cookie_banner(page: Page, timeout: int = 1500) -> bool:
    for selector in COOKIE_BANNER_SELECTORS:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=timeout):
                await locator.click(timeout=timeout)
                return True
        except Exception:
            continue
    return False


async def close_popups(page: Page, timeout: int = 1200) -> bool:
    closed_any = False
    for selector in POPUP_CLOSE_SELECTORS:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=timeout):
                await locator.click(timeout=timeout)
                closed_any = True
        except Exception:
            continue
    return closed_any


async def wait_for_dom_stable(page: Page, quiet_ms: int = 500, max_wait_ms: int = 5000):
    """Waits until the DOM stops mutating for quiet_ms (or gives up after
    max_wait_ms) — useful on JS-heavy sites where content streams in."""
    await page.evaluate(
        """
        ([quietMs, maxWaitMs]) => new Promise((resolve) => {
            let timer;
            const done = () => { observer.disconnect(); resolve(); };
            const observer = new MutationObserver(() => {
                clearTimeout(timer);
                timer = setTimeout(done, quietMs);
            });
            observer.observe(document.body, { childList: true, subtree: true });
            timer = setTimeout(done, quietMs);
            setTimeout(done, maxWaitMs);
        })
        """,
        [quiet_ms, max_wait_ms],
    )


async def safe_click(page: Page, selector: str, retries: int = 3, delay_ms: int = 500) -> bool:
    for _ in range(retries):
        try:
            await page.locator(selector).first.click(timeout=3000)
            return True
        except Exception:
            await page.wait_for_timeout(delay_ms)
    return False


def _reply_log_key(conversation_key: str, message_text: str) -> str:
    raw = f"{conversation_key}::{message_text}".strip()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def has_already_replied(log_path: str, conversation_key: str, message_text: str) -> bool:
    """Checks a persistent local record of replies already sent, keyed by
    (contact, exact message text). This is what stops a fresh run from
    re-replying to something a previous run already handled — Instagram's
    own unread-indicator UI isn't reliable enough on its own for this."""
    path = Path(log_path)
    if not path.exists():
        return False
    try:
        log = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return _reply_log_key(conversation_key, message_text) in log


def record_reply(log_path: str, conversation_key: str, message_text: str) -> None:
    """Records that a reply was sent, so future runs skip this message."""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    log = {}
    if path.exists():
        try:
            log = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            log = {}
    log[_reply_log_key(conversation_key, message_text)] = {
        "conversation": conversation_key,
        "message": message_text[:200],
    }
    path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
