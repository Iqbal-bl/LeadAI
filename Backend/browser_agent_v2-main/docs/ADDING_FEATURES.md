# Adding New Features

Everything in this project follows the same shape: **mechanics module →
tool wrapper → (optional) registered task**. Adding something new means
adding to this chain, not restructuring anything existing.

## The three layers

```
graph_api/pages.py          <- mechanics: plain functions, no LLM involved
        |
tools/graph_tools.py        <- tool wrapper: @tool-decorated, gives the LLM access
        |
agent/registry.py           <- (optional) named task: a ready-made prompt using the tool
```

Any new integration — a different platform, a database, a payment API,
whatever — follows this same shape.

## Step 1: mechanics — a new module, zero LLM/agent dependency

Pick the right home:
- Talks to an external API? → new file/folder next to `graph_api/`
  (e.g. `linkedin_api/`, `stripe_api/`) — same pattern: a `client.py` for
  auth/HTTP, plain `async def` functions per action.
- Drives the browser? → add a method to `browser/browser.py`.
- Touches the local knowledge base? → extend `crag/`.

Keep it a plain library — no `@tool`, no prompt text, nothing
LangChain-specific here. This is what makes each piece independently
testable (see `graph_api/smoke_test.py` for the pattern) and portable to
another project.

## Step 2: tool wrapper — expose it to the agent

In `tools/`, either add to an existing file (if it fits a category
already there) or create a new one, e.g. `tools/linkedin_tools.py`:

```python
from langchain_core.tools import tool
from linkedin_api import posts as linkedin_posts

@tool
async def linkedin_create_post(message: str) -> str:
    """One clear sentence for what this does and when to use it."""
    try:
        result = await linkedin_posts.create_post(message)
        return f"Created LinkedIn post: {result}"
    except Exception as e:
        return f"Error creating LinkedIn post: {e}"

LINKEDIN_TOOLS = [linkedin_create_post]
```

Then wire it into `tools/__init__.py`:
```python
from .linkedin_tools import LINKEDIN_TOOLS
# ...
tools = [*CRAG_TOOLS, *GRAPH_API_TOOLS, *LINKEDIN_TOOLS, finish]
```

That's it — every task now has access to it automatically.

**If it needs the browser** (like `browser_tools.py`/`vision_tools.py`),
use a `build_*(browser)` factory instead of a flat list, and add it to
`build_tools()`'s browser-present branch.

## Step 3 (optional): a named task — a ready-made prompt

Only needed if you want a reusable, parameterized prompt rather than
typing one fresh each time. Add an entry to `agent/registry.py`:

```python
"linkedin_post_from_topic": (
    "Write a short professional post about \"{topic}\". Then call "
    "linkedin_create_post with your text. Call finish confirming."
),
```

Run it: `run_agent_task("linkedin_post_from_topic", {"topic": "..."})`.

## Reply-generation policy — read this if your feature replies to people

Any task that replies to a comment/message (regardless of platform or
mechanism) should follow the same rule already used throughout
`agent/registry.py`: factual questions go through `answer_company_question`
(CRAG) only, never general knowledge; everything else gets a short,
generic acknowledgment — no invented specifics.

## A note on tool docstrings

The docstring is the ONLY thing the LLM sees to decide when to use a
tool — write it like you're explaining to someone who can't see your
code. State what it does, what a bad/wrong input looks like, and when to
prefer it over a similar tool (see `click`/`vision_scan`'s docstrings for
an example of steering the model away from the wrong choice).
