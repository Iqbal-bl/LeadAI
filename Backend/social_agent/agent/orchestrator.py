"""
The core engine: given a natural-language prompt (run_prompt) or a named
task from the registry (run_agent_task), runs it through the LangGraph
agent end-to-end and returns a structured result.

Both main.py (interactive) and tasks/task_N.py (standalone/scheduled) call
into this — neither duplicates the agent-running loop itself.
"""

from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from social_agent.agent.graph import build_graph
from social_agent.agent.registry import TASK_PROMPTS
from social_agent.browser.browser import BrowserController
from social_agent.tools import build_tools


def _ai_text(msg: AIMessage) -> str:
    if isinstance(msg.content, str):
        return msg.content.strip()
    return " ".join(b.get("text", "") for b in msg.content if isinstance(b, dict)).strip()


async def run_prompt(
    prompt: str,
    headless: bool = True,
    verbose: bool = False,
    storage_state_path: str | None = None,
    user_data_dir: str | None = None,
    show_debug_boxes: bool = False,
    browser: BrowserController | None = None,
    recursion_limit: int = 50,
    browser_engine: str = "firefox",
    use_browser: bool = True,
) -> dict:
    """Runs one free-form task description through the agent.

    use_browser=False: skips launching Playwright entirely — for Graph
    API-only tasks (posting/replying on the Page) that never touch a
    browser. Requires ENABLE_VISION=False in agent/graph.py. Ignored if
    an existing `browser` is passed in (that always wins).

    browser_engine: "chromium" (default, uses real Chrome via channel=
    "chrome") or "firefox". Only used when no existing `browser` is passed
    in — an already-started browser keeps whatever engine it was created
    with.

    For a persistent login on strict sites (Google/YouTube, LinkedIn,
    Facebook), pass user_data_dir (e.g. "sessions_profiles/linkedin", from
    save_login_session.py) rather than storage_state_path — see
    browser/session.py for why persistent profiles are more robust.

    Pass show_debug_boxes=True to draw live numbered boxes directly on the
    page (visible in the actual browser window) so you can watch what the
    agent's indexing sees — independent of ENABLE_VISION in agent/graph.py,
    which controls whether a screenshot gets sent to the LLM. Requires
    headless=False to actually see anything.

    Pass an already-started `browser` to run this prompt on an EXISTING
    session instead of starting/stopping a fresh one — this is what
    workflows/ scripts use to chain multiple skills (e.g. like posts, then
    check messenger, then post) on one shared, already-logged-in browser,
    without re-navigating or re-authenticating between them. When `browser`
    is passed in, this function does NOT start or stop it — the caller owns
    that lifecycle.
    """
    owns_browser = browser is None and use_browser
    if owns_browser:
        browser = BrowserController(
            storage_state_path=storage_state_path,
            user_data_dir=user_data_dir,
            show_debug_boxes=show_debug_boxes,
            browser_engine=browser_engine,
        )
        await browser.start(headless=headless)

    final_result, last_ai_text, error = None, None, None

    try:
        tools = build_tools(browser)
        app = build_graph(tools, browser)

        if browser is not None:
            initial_state = await browser.get_state()
            task_text = f"Task: {prompt}\n\nCurrent page state:\n{initial_state}"
        else:
            task_text = f"Task: {prompt}"
        inputs = {"messages": [HumanMessage(content=task_text)]}

        async for step in app.astream(inputs, config={"recursion_limit": recursion_limit}, stream_mode="updates"):
            for node_name, node_output in step.items():
                for msg in node_output.get("messages", []):
                    if isinstance(msg, AIMessage):
                        text = _ai_text(msg)
                        if text:
                            last_ai_text = text
                            if verbose:
                                print(f"[thinking] {text}")
                        if verbose:
                            for tc in msg.tool_calls:
                                print(f"[proposing] {tc['name']}({tc['args']})")
                    elif isinstance(msg, ToolMessage):
                        if verbose and node_name == "tools":
                            preview = (msg.content if isinstance(msg.content, str) else str(msg.content))[:200]
                            print(f"[executed] {msg.name} -> {preview}")
                        if msg.name == "finish":
                            final_result = msg.content
                if verbose and node_name == "stuck":
                    print("[loop-blocked] Repeated action detected — redirecting model.")
    except Exception as e:
        error = str(e)
        if verbose:
            print(f"[error] {error}")
    finally:
        if owns_browser:
            await browser.stop()

    if final_result:
        return {
            "prompt": prompt,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success": True,
            "result": final_result,
        }
    return {
        "prompt": prompt,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "success": False,
        "result": last_ai_text,
        "message": error or "Agent ended without calling finish.",
    }


async def run_agent_task(
    task_name: str,
    kwargs: dict,
    headless: bool = True,
    verbose: bool = False,
    storage_state_path: str | None = None,
    user_data_dir: str | None = None,
    show_debug_boxes: bool = False,
    browser: BrowserController | None = None,
    recursion_limit: int = 50,
    browser_engine: str = "firefox",
    use_browser: bool = True,
) -> dict:
    """Runs a named, parameterized task from agent/registry.py. See
    run_prompt() for what passing an existing `browser`, browser_engine,
    or use_browser=False does."""
    if task_name not in TASK_PROMPTS:
        return {
            "task": task_name,
            "success": False,
            "message": f"Unknown task '{task_name}'. Available: {list(TASK_PROMPTS)}",
        }
    try:
        prompt = TASK_PROMPTS[task_name].format(**kwargs)
    except KeyError as e:
        return {"task": task_name, "success": False, "message": f"Missing required argument: {e}"}

    result = await run_prompt(
        prompt,
        headless=headless,
        verbose=verbose,
        storage_state_path=storage_state_path,
        user_data_dir=user_data_dir,
        show_debug_boxes=show_debug_boxes,
        browser=browser,
        recursion_limit=recursion_limit,
        browser_engine=browser_engine,
        use_browser=use_browser,
    )
    result["task"] = task_name
    return result
