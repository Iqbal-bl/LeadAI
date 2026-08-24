"""
Single entry point for the agent's tools. To add a new tool category:
create a new file here (module-level list or a build_*(browser) factory),
import it below, and add it to build_tools()'s return list. Nothing
outside this package needs to know how any individual tool is
implemented. See docs/ADDING_FEATURES.md for a full walkthrough.
"""

from langchain_core.tools import tool

from browser.browser import BrowserController

from .browser_tools import build_browser_tools
from .crag_tools import CRAG_TOOLS
from .graph_tools import GRAPH_API_TOOLS
from .image_gen_tools import IMAGE_GEN_TOOLS
from .vision_tools import build_vision_tools


@tool
def finish(result: str) -> str:
    """Call when the task is complete (or stuck), with a summary."""
    return result


def build_tools(browser: BrowserController | None = None) -> list:
    """Pass browser=None for Graph API-only tasks (no browser needed at
    all — skips launching Playwright entirely). Requires ENABLE_VISION=
    False in agent/graph.py when browser is None."""
    tools = [*CRAG_TOOLS, *GRAPH_API_TOOLS, *IMAGE_GEN_TOOLS, finish]
    if browser is not None:
        tools = [*build_browser_tools(browser), *build_vision_tools(browser), *tools]
    return tools
