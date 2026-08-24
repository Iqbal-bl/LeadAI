"""
Builds the LangGraph agent graph.

Graph shape:

    agent --(tool call requested?)--> tools --(finish tool used?)--> END
      ^                                  |
      |______________(otherwise)_________|

- "agent": calls the LLM (with tools bound) on the running message list.
- "tools": LangGraph's prebuilt ToolNode executes whatever tool(s) the model
  called and appends ToolMessages with the results.
- After tools run, if `finish` was among them, we end; otherwise loop back
  to "agent" with the new tool results in context.
- "nudge": model answered in plain text instead of calling a tool — push it
  to actually call `finish` or another tool.
- "stuck": model repeated the exact same *mutating* action (click/type/
  goto/upload) without progress — redirect it, and give up gracefully after
  two such redirects rather than grinding to the recursion limit.
- "attach_screenshot": optional vision-mode node (see ENABLE_VISION).

IMPORTANT: repeat-detection only applies to mutating actions. Read-only
tools like extract_text/dismiss_popups have no distinguishing arguments, so
comparing (name, args) can't tell "pointlessly repeating a failed click"
apart from "reading the page again after it finished loading" — the latter
is normal and often necessary, so those tools are exempt entirely.
"""

import asyncio
import base64
import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph, MessagesState
from langgraph.prebuilt import ToolNode

from agent.memory import log_llm_input, trim_messages
from models import get_model

# Vision mode: attach a Set-of-Mark screenshot (numbered boxes drawn on the
# real page) after every tool round. Requires a vision-capable model — see
# models.py's VISION_MODEL and its caveats before enabling this.
ENABLE_VISION = False

# When vision mode is on, save a copy of each image actually sent to the
# LLM (overwriting the same file each turn) — open it to see exactly what
# the model saw, since otherwise there's no way to tell it's even happening.
SAVE_VISION_DEBUG_IMAGE = True

# Set to True to print the exact messages sent to the model each turn.
DEBUG_LLM_INPUT = False

# Tools where repeating the exact same call is normal/harmless — they only
# observe the page, never click/type/navigate — so they're never
# loop-blocked no matter how many times in a row they're called.
READ_ONLY_TOOLS = {"extract_text", "dismiss_popups", "answer_company_question"}

SYSTEM_PROMPT_BROWSER = """You are a browser automation agent. You control a real \
web browser through tools to complete the user's task.

After every action, you'll be shown the current page state: the URL, title, \
and a numbered list of interactive elements, in top-to-bottom visual order. \
Some elements are marked [disabled] (don't try to click these — they won't \
respond), [checked]/[unchecked] (checkboxes, toggles, radio buttons — use \
this to know the current state before deciding whether to click), or \
[likely-unread] (bold text, a common signal for unread items in inbox/ \
conversation lists — but it's a heuristic, not guaranteed, so if nothing \
is marked [likely-unread] where you expected something, say so rather \
than guessing at an arbitrary item). Always use those index numbers \
with click/type_text — never guess selectors or assume an element exists \
that isn't listed.

Work step by step and call exactly one tool per turn. Before calling \
`finish` to report success on any task that performs an action (posting, \
submitting a form, sending something), verify the outcome actually \
happened — check the current page state or call extract_text to confirm \
the expected result (e.g. the dialog actually closed, a confirmation \
message appeared). Do not call finish claiming success just because a \
tool call didn't return an error; an action can execute without producing \
the intended real-world effect. When the task is genuinely complete, call \
`finish` with a clear summary. If you get stuck (an expected element isn't \
there, a page won't load, etc.) try an alternative approach first; if \
you're truly stuck, call `finish` and explain what went wrong and what you \
were unable to verify.
"""

SYSTEM_PROMPT_NO_BROWSER = """You are an automation agent working entirely \
through API tools (Meta Graph API, company knowledge base) — there is no \
browser and no page state; ignore any instinct to describe pages or clicks.

Work step by step and call exactly one tool per turn. Before calling \
`finish` to report success, make sure you actually completed every part \
of the task (e.g. replied to every item in a list, not just the first \
one). When genuinely complete, call `finish` with a clear summary \
including any relevant counts. If you get stuck, call `finish` and \
explain what went wrong.
"""


def _tool_call_signature(msg):
    if not (isinstance(msg, AIMessage) and msg.tool_calls):
        return None
    tc = msg.tool_calls[0]
    return (tc["name"], json.dumps(tc["args"], sort_keys=True))


def build_graph(tools, browser=None):
    model = get_model(tools, vision=ENABLE_VISION)

    async def call_model(state: MessagesState):
        messages = trim_messages(state["messages"])
        if not any(isinstance(m, SystemMessage) for m in messages):
            system_prompt = SYSTEM_PROMPT_BROWSER if browser is not None else SYSTEM_PROMPT_NO_BROWSER
            messages = [SystemMessage(content=system_prompt), *messages]
        if DEBUG_LLM_INPUT:
            log_llm_input(messages)

        # Retry a few times on any LLM-call failure (this catches transient
        # decoding glitches, e.g. some models occasionally emit a malformed
        # pseudo-tool-call that the provider hard-rejects). If all retries
        # fail, synthesize a finish call instead of crashing the whole run.
        MAX_LLM_RETRIES = 3
        last_exc = None
        for attempt in range(MAX_LLM_RETRIES):
            try:
                response = await model.ainvoke(messages)
                return {"messages": [response]}
            except Exception as e:
                last_exc = e
                print(f"[llm-error] Attempt {attempt + 1}/{MAX_LLM_RETRIES} failed: {e}")
                await asyncio.sleep(1.5 * (attempt + 1))

        fallback = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "finish",
                    "args": {"result": f"Stopped after repeated LLM errors: {last_exc}"},
                    "id": "fallback_finish_call",
                }
            ],
        )
        return {"messages": [fallback]}

    def route_from_agent(state: MessagesState):
        last = state["messages"][-1]

        if isinstance(last, AIMessage) and last.tool_calls:
            tc_name = last.tool_calls[0]["name"]
            if tc_name in READ_ONLY_TOOLS:
                return "tools"  # never loop-block read-only/observation tools

            sig = _tool_call_signature(last)
            repeat_count = 0
            ai_seen = 0
            for msg in reversed(state["messages"][:-1]):
                if isinstance(msg, AIMessage):
                    ai_seen += 1
                    if _tool_call_signature(msg) == sig:
                        repeat_count += 1
                    if ai_seen >= 6:
                        break

            if repeat_count >= 2:
                # Already redirected here before and still hasn't broken the
                # loop — don't send it back around forever.
                stuck_escalations = sum(
                    1
                    for m in state["messages"]
                    if isinstance(m, HumanMessage)
                    and isinstance(m.content, str)
                    and m.content.startswith("You've called the same action")
                )
                if stuck_escalations >= 2:
                    return END
                return "stuck"
            return "tools"

        # Model answered in plain text instead of calling a tool.
        consecutive_no_tool_call = 0
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage) and not msg.tool_calls:
                consecutive_no_tool_call += 1
            else:
                break
        if consecutive_no_tool_call >= 2:
            return END
        return "nudge"

    def nudge(state: MessagesState):
        return {
            "messages": [
                HumanMessage(
                    content="You didn't call a tool. If the task is complete, "
                    "call the `finish` tool now with your result. Otherwise "
                    "call whichever tool moves the task forward."
                )
            ]
        }

    def stuck(state: MessagesState):
        # route_from_agent sends us here INSTEAD of executing the pending
        # tool call — so that call is still unanswered in history. Every
        # tool_call in an AIMessage must get a matching ToolMessage before
        # any other message can follow it (OpenAI enforces this strictly;
        # Groq didn't, which is why this only broke after switching
        # providers). Close it out first, then add the redirect.
        messages = []
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            for tc in last.tool_calls:
                messages.append(
                    ToolMessage(
                        content="Skipped — this exact action was already "
                        "repeated without progress (loop detection). Do "
                        "not call it again with the same arguments.",
                        tool_call_id=tc["id"],
                        name=tc["name"],
                    )
                )
        messages.append(
            HumanMessage(
                content="You've called the same action with the same "
                "arguments multiple times without progress — it isn't "
                "working. Do not repeat it. Try a different element, "
                "scroll first, re-check the current page state, or if "
                "you have enough information already, call `finish` "
                "with your best answer and a note about what didn't work."
            )
        )
        return {"messages": messages}

    def route_from_tools(state: MessagesState):
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage):
                break
            if isinstance(msg, ToolMessage) and msg.name == "finish":
                return END
        return "attach_screenshot" if ENABLE_VISION else "agent"

    async def attach_screenshot(state: MessagesState):
        b64 = await browser.screenshot_with_boxes()

        if SAVE_VISION_DEBUG_IMAGE:
            debug_path = Path("last_vision_frame.png")
            debug_path.write_bytes(base64.b64decode(b64))
            print(f"[vision] Sending image to the LLM this turn — saved a copy to {debug_path.resolve()}")

        return {
            "messages": [
                HumanMessage(
                    content=[
                        {
                            "type": "text",
                            "text": "Screenshot of the current page. Each interactive "
                            "element is boxed and labeled with its index number — "
                            "use these to confirm what you're about to click/type into.",
                        },
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ]
                )
            ]
        }

    tool_node = ToolNode(tools)

    graph = StateGraph(MessagesState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", tool_node)
    graph.add_node("nudge", nudge)
    graph.add_node("stuck", stuck)
    graph.set_entry_point("agent")
    graph.add_conditional_edges(
        "agent", route_from_agent, {"tools": "tools", "nudge": "nudge", "stuck": "stuck", END: END}
    )
    if ENABLE_VISION:
        graph.add_node("attach_screenshot", attach_screenshot)
        graph.add_conditional_edges("tools", route_from_tools, {"attach_screenshot": "attach_screenshot", END: END})
        graph.add_edge("attach_screenshot", "agent")
    else:
        graph.add_conditional_edges("tools", route_from_tools, {"agent": "agent", END: END})
    graph.add_edge("nudge", "agent")
    graph.add_edge("stuck", "agent")

    return graph.compile()
