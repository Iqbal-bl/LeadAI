"""
BrowserSession — owns the Playwright lifecycle: launching the browser,
creating a context/page, and tearing it down cleanly. Kept separate from
BrowserController (browser.py) so lifecycle management doesn't get tangled
with page-action logic.

browser_engine controls which engine actually launches: "chromium"
(default — real Chrome via channel="chrome") or "firefox". Chrome-specific
options (channel, the anti-automation-detection flag) only apply when
using chromium.

Two ways to persist a login, in order of robustness:

1. user_data_dir (persistent browser profile) — RECOMMENDED for strict
   sites (Google, LinkedIn, Facebook). A real profile folder holds the
   entire session (cookies, local storage, browser fingerprint). You log
   in once by hand inside it; every future launch reuses that same
   profile, so there's no separate "automation drove the login" moment
   for the site to detect.

2. storage_state_path (cookie export/import) — simpler, fine for sites
   without aggressive bot detection, but restoring cookies into a fresh
   automated browser can still get flagged by strict sites even with
   valid cookies.
"""

from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, async_playwright


class BrowserSession:
    def __init__(
        self,
        storage_state_path: str | None = None,
        user_data_dir: str | None = None,
        browser_engine: str = "firefox",
    ):
        self._playwright = None
        self.browser: Browser | None = None  # only set in non-persistent mode
        self.context: BrowserContext = None
        self.page: Page = None
        self.pending_file_chooser = None  # set by _attach_file_chooser_handler
        self.storage_state_path = storage_state_path
        self.user_data_dir = user_data_dir
        if browser_engine not in ("chromium", "firefox"):
            raise ValueError(f"browser_engine must be 'chromium' or 'firefox', got {browser_engine!r}")
        self.browser_engine = browser_engine

    async def start(self, headless: bool = False):
        self._playwright = await async_playwright().start()
        engine = getattr(self._playwright, self.browser_engine)
        is_chromium = self.browser_engine == "chromium"

        # Firefox-specific anti-detection preferences.
        firefox_prefs = {
            "dom.webdriver.enabled": False,
            "useAutomationExtension": False,
        }

        if self.user_data_dir:
            Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)
            launch_kwargs = {"headless": headless}
            if is_chromium:
                # channel="chrome" uses your real installed Chrome (not
                # Playwright's bundled Chromium) — requires `playwright
                # install chrome` once.
                launch_kwargs["channel"] = "chrome"
                launch_kwargs["args"] = ["--disable-blink-features=AutomationControlled"]
            else:
                launch_kwargs["firefox_user_prefs"] = firefox_prefs
            self.context = await engine.launch_persistent_context(self.user_data_dir, **launch_kwargs)
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        else:
            launch_kwargs = {"headless": headless}
            if is_chromium:
                launch_kwargs["channel"] = "chrome"
            else:
                launch_kwargs["firefox_user_prefs"] = firefox_prefs
            self.browser = await engine.launch(**launch_kwargs)
            context_kwargs = {}
            if self.storage_state_path and Path(self.storage_state_path).exists():
                context_kwargs["storage_state"] = self.storage_state_path
            self.context = await self.browser.new_context(**context_kwargs)
            self.page = await self.context.new_page()

        self._attach_dialog_handler(self.page)
        self._attach_file_chooser_handler(self.page)
        self.context.on("page", self._handle_new_page)

    def _attach_dialog_handler(self, page: Page):
        async def _on_dialog(dialog):
            # Native JS alert()/confirm()/prompt() dialogs block the page
            # until dismissed — left unhandled, they can silently stall
            # the agent. Auto-accept by default, logged so you can see
            # what was dismissed (a specific task may want to dismiss
            # instead of accept for something like a "leave page?" prompt).
            print(f"[browser] Auto-accepting JS dialog ({dialog.type}): {dialog.message}")
            await dialog.accept()

        page.on("dialog", _on_dialog)

    def _attach_file_chooser_handler(self, page: Page):
        # Many sites (Facebook included) have a custom-styled "upload"
        # button whose onClick handler calls the hidden <input type=file>'s
        # own .click() internally. That still triggers the browser's REAL
        # native OS file-picker window — Playwright has no way to close a
        # native OS window once it's open. The fix: Playwright suppresses
        # the native dialog entirely for the lifetime of the page as long
        # as a "filechooser" listener is registered — instead it hands us
        # a FileChooser object we can call .set_files() on directly. We
        # just stash the latest one; upload_file() in browser.py checks
        # for it before falling back to a raw <input type=file> locator.
        def _on_file_chooser(file_chooser):
            self.pending_file_chooser = file_chooser

        page.on("filechooser", _on_file_chooser)

    async def _handle_new_page(self, new_page: Page):
        # A new tab/popup opened (target="_blank" link, window.open(), an
        # OAuth popup, etc.) — switch to it so subsequent actions operate
        # on what's actually in front of the user now, instead of a stale
        # background tab the agent can no longer usefully act on.
        try:
            await new_page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        print(f"[browser] New tab opened: {new_page.url or '(loading)'} — switching to it")
        self.page = new_page
        self._attach_dialog_handler(new_page)
        self._attach_file_chooser_handler(new_page)

    async def save_storage_state(self, path: str | None = None) -> str:
        """Only meaningful in storage_state mode — persistent profiles save
        automatically to disk as you use them, with nothing to export."""
        if self.user_data_dir:
            raise ValueError(
                "Persistent profile mode (user_data_dir) saves automatically — no explicit save needed."
            )
        target = path or self.storage_state_path
        if not target:
            raise ValueError("No storage_state_path configured to save to.")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        await self.context.storage_state(path=target)
        return target

    async def stop(self):
        try:
            if self.context:
                await self.context.close()
        except Exception as e:
            print(f"[warning] Context was already closed or unreachable: {e}")
        if self.browser:
            try:
                await self.browser.close()
            except Exception as e:
                print(f"[warning] Browser was already closed or unreachable: {e}")
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            print(f"[warning] Playwright driver was already stopped: {e}")
