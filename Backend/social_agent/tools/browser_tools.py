"""
Core browser action tools — wrap BrowserController for the agent.
Each action re-fetches page state after acting so the model sees the result.
"""

from langchain_core.tools import tool

from social_agent.browser.browser import BrowserController
from utils import close_popups, dismiss_cookie_banner


def build_browser_tools(browser: BrowserController) -> list:
    @tool
    async def goto(url: str) -> str:
        """Navigate the browser to a URL."""
        result = await browser.goto(url)
        state = await browser.get_state()
        return f"{result}\n\nCurrent page state:\n{state}"

    @tool
    async def click(index: int) -> str:
        """Click an interactive element by its index. If it's not in the
        list at all (closed shadow root, canvas, cross-origin iframe),
        use vision_scan instead of guessing."""
        try:
            result = await browser.click(index)
        except Exception as e:
            result = f"Error clicking element {index}: {e}"
        state = await browser.get_state()
        return f"{result}\n\nCurrent page state:\n{state}"

    @tool
    async def type_text(index: int, text: str) -> str:
        """Type text into an input/textarea by its index. See vision_scan
        if the element isn't in the numbered list."""
        try:
            result = await browser.type_text(index, text)
        except Exception as e:
            result = f"Error typing into element {index}: {e}"
        state = await browser.get_state()
        return f"{result}\n\nCurrent page state:\n{state}"

    @tool
    async def press_key(key: str) -> str:
        """Press a keyboard key, e.g. 'Enter' or 'Escape'."""
        result = await browser.press_key(key)
        state = await browser.get_state()
        return f"{result}\n\nCurrent page state:\n{state}"

    @tool
    async def scroll(direction: str = "down") -> str:
        """Scroll the page. direction must be 'up' or 'down'."""
        result = await browser.scroll(direction)
        state = await browser.get_state()
        return f"{result}\n\nCurrent page state:\n{state}"

    @tool
    async def extract_text() -> str:
        """Extract the visible text content of the current page."""
        text = await browser.extract_text()
        state = await browser.get_state()
        return f"{text}\n\nCurrent page state:\n{state}"

    @tool
    async def dismiss_popups() -> str:
        """Dismiss a cookie-consent banner or popup/modal if one is
        covering the page."""
        cookie_dismissed = await dismiss_cookie_banner(browser.page)
        popup_dismissed = await close_popups(browser.page)
        state = await browser.get_state()
        return (
            f"Cookie banner dismissed: {cookie_dismissed}. Popup dismissed: {popup_dismissed}."
            f"\n\nCurrent page state:\n{state}"
        )

    @tool
    async def upload_file(file_path: str, index: int | None = None) -> str:
        """Attach a local file to a file-input element (bypasses the OS
        file-picker dialog, which can't be automated). Pass index if
        known, otherwise the first file input on the page is used."""
        try:
            result = await browser.upload_file(file_path, index)
        except Exception as e:
            result = f"Error uploading file: {e}"
        state = await browser.get_state()
        return f"{result}\n\nCurrent page state:\n{state}"

    return [
        goto, click, type_text, press_key, scroll,
        extract_text, dismiss_popups, upload_file,
    ]
