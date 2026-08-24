"""
Vision-fallback tools — for elements DOM indexing genuinely can't see
(closed shadow roots, canvas, cross-origin iframes). Last resort only;
much slower than indexed click/type_text.
"""

from langchain_core.tools import tool

from browser.browser import BrowserController
from vision.omniparser_client import parse_screenshot


def build_vision_tools(browser: BrowserController) -> list:
    vision_elements: dict[int, dict] = {}

    @tool
    async def vision_scan() -> str:
        """LAST RESORT — only if the element isn't in the normal numbered
        element list at all. Screenshots the page and runs it through
        OmniParser (pixel-based UI detection, independent of the DOM).
        Returns a [V#]-indexed list; pass an index to click_vision_element."""
        nonlocal vision_elements
        png_bytes, width, height = await browser.screenshot_bytes()
        try:
            detected = await parse_screenshot(png_bytes, width, height)
        except Exception as e:
            return f"Error running vision_scan: {e}"
        vision_elements = {i: el for i, el in enumerate(detected)}
        if not vision_elements:
            return "vision_scan found no elements on this screenshot."
        lines = [f"[V{i}] \"{el['label']}\"" for i, el in vision_elements.items()]
        return "Vision-detected elements:\n" + "\n".join(lines)

    @tool
    async def click_vision_element(vision_index: int) -> str:
        """Click an element found by vision_scan, using its [V#] index.
        Must call vision_scan first in this run."""
        if vision_index not in vision_elements:
            return f"No vision element V{vision_index} — call vision_scan first."
        el = vision_elements[vision_index]
        try:
            result = await browser.click_at_coordinates(el["x"], el["y"])
        except Exception as e:
            result = f"Error clicking vision element V{vision_index}: {e}"
        state = await browser.get_state()
        return f"{result}\n\nCurrent page state:\n{state}"

    return [vision_scan, click_vision_element]
