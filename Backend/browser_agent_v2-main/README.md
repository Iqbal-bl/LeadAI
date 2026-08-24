# Agentic Browser

An LLM agent (LangGraph + OpenAI) that operates a Facebook Page through
the official **Meta Graph API** — posting, replying to comments and
messages, grounded strictly in a company knowledge base (CRAG) — plus a
separate Playwright browser-automation capability for anything with no
API surface.

See `docs/GRAPH_API_SETUP.md` for full credential setup, and
`docs/ADDING_FEATURES.md` for how to extend this project.

## Structure

```
agent/            LangGraph decision loop, orchestrator, named task
                   registry (agent/registry.py), message-history handling
browser/           Playwright lifecycle + page actions (legacy/optional —
                   only used by tasks that pass use_browser=True)
vision/            OmniParser fallback for elements DOM indexing can't see
crag/              Portable Corrective-RAG module — zero dependency on
                   anything else here, drop it into another project as-is
graph_api/         Meta Graph API — Pages, Instagram, comments, Messenger,
                   Lead Ads. No browser involved at all.
tools/             Where every capability above gets exposed to the agent
                   (see docs/ADDING_FEATURES.md to add your own)
content_queue/     CSV-driven post topic queue (topic, instructions) that
                   the scheduler consumes automatically
knowledge/         Company docs CRAG retrieves from
tasks/
  graph_api/       Manual one-off runs of the Graph API tasks
  legacy_browser/  Personal-profile browser automation (see note below)
workflows/         Multi-skill browser routines on one shared session
                   (legacy_browser — personal profile, not the Page)
docs/              Setup guides
models.py          LLM construction (OpenAI)
main.py            Interactive entrypoint
scheduler.py       The 24/7 unattended runner
save_login_session.py   One-time manual login -> persistent browser profile
```

## A note on "legacy_browser"

Early in this project, Facebook/Instagram automation ran entirely
through a browser driving a **personal profile**. That's what
`workflows/facebook_daily_routine.py` and `tasks/legacy_browser/` are —
kept for reference/testing, clearly labeled, not used in production.

**The actual Page now runs entirely on the official Graph API** — no
browser, no personal profile, no detection risk to manage, because it's
sanctioned API access to an asset you administer. Everything under
`graph_api/`, `tools/graph_tools.py`, and the Graph API task prompts in
`agent/registry.py` is the current, production path.

## Setup

```bash
pip install -r requirements.txt
playwright install firefox   # only needed if you use the legacy browser tasks
```

Copy `.env.example` to `.env` and fill in:
```
OPENAI_API_KEY=            # required
GOOGLE_API_KEY=             # required for CRAG (embeddings + grading)
TAVILY_API_KEY=              # only if CRAG_ALLOW_WEB_SEARCH=true
FB_APP_ID= / FB_APP_SECRET= / FB_PAGE_ID= / FB_PAGE_ACCESS_TOKEN=
IG_BUSINESS_ACCOUNT_ID=       # only if using Instagram
REPLICATE_API_TOKEN=          # only for the vision fallback
```
Full Graph API credential walkthrough: **`docs/GRAPH_API_SETUP.md`**.

## Running it

**Interactively (browser-based tasks):**
```bash
python main.py
```

**One Graph API task, manually:**
```bash
python tasks/graph_api/reply_to_comments.py
python tasks/graph_api/reply_to_messages.py
python tasks/graph_api/post_from_queue.py
```

**24/7, unattended (the actual production system):**
```bash
python scheduler.py
```
Runs three jobs on independent jittered intervals, Graph API only:
- Post the next topic from `content_queue/topics.csv` (removes it on success)
- Reply to unreplied Page comments (any nesting depth, last 50 posts)
- Reply to unread Page Messenger conversations

Every run's result is appended to `results.jsonl`.

**Legacy personal-profile browser tasks:**
```bash
python workflows/facebook_daily_routine.py
python tasks/legacy_browser/facebook_post_with_media.py
```

## Content queue

`content_queue/topics.csv` — two columns, `topic` and `instructions`:
```csv
topic,instructions
AI in healthcare,Write an upbeat post about AI helping doctors. Under 3 sentences.
```
The scheduler posts the oldest row and removes it on success. Add more
rows anytime — `content_queue/csv_queue.py` has `add_topic()` if you want
to add rows programmatically instead of editing the CSV by hand.

## Reply policy — CRAG-only, no outside information

Every comment/message reply task (browser or Graph API) follows the same
rule: factual questions about the company/product/pricing are answered
**only** via `answer_company_question` (CRAG, backed by `knowledge/company_docs/`)
— never from the model's general knowledge. Anything else (a compliment,
a general comment, small talk) gets a short, generic acknowledgment only
— no invented specifics, no outside information. `CRAG_ALLOW_WEB_SEARCH`
is `false` by default for exactly this reason; only turn it on if you
want CRAG's Tavily web-search fallback for some *other* use case — not
for replies.

## CRAG

```python
from crag.engine import CragEngine
engine = CragEngine(knowledge_dir="knowledge/company_docs")
answer = await engine.answer("What's your pricing?")
```
Pipeline: retrieve top-k chunks -> grade each for actual relevance -> if
genuinely nothing relevant, optionally web search (off by default) ->
generate, explicitly instructed to say "I don't know" over guessing.

Replace the placeholder docs in `knowledge/company_docs/` with real
content, then rebuild the index once:
```python
CragEngine(knowledge_dir="knowledge/company_docs", force_rebuild=True)
```
(Also required any time you change `EMBEDDING_MODEL` in
`crag/vector_store.py` — old vectors aren't valid input for a different
embedding model.)

### Editor draft generation API

`POST /content/drafts` turns a topic and/or source content into one editable,
professional social-post draft. It does **not** enqueue, upload, schedule, or
publish anything. The frontend can call it again with the same input and an
incremented `version` to request an alternative.

```json
{
  "topic": "How AI can simplify customer support",
  "content": "Our platform helps teams route common questions faster.",
  "instructions": "Professional and approachable. Keep it under 100 words.",
  "version": 1
}
```

It requires the normal `X-API-Key` header and returns:

```json
{"draft": "...", "version": 1}
```

## Graph API

`graph_api/` — `pages.py` (posts, photos), `instagram.py` (publish,
needs a public image URL — no direct upload), `comments.py` (read/reply,
including replies-to-replies), `messaging.py` (Page Messenger),
`leads.py` (Lead Ads submissions). All wired into `tools/graph_tools.py`.

Test any of these directly, no agent involved:
```bash
python -m graph_api.smoke_test
```

## Vision fallback (last resort only)

For elements normal DOM indexing genuinely can't see — closed shadow
roots, canvas-rendered UI, cross-origin iframes. `vision_scan` screenshots
the page, runs it through OmniParser (via Replicate), and returns a
`[V#]`-indexed list; `click_vision_element` clicks one. Much slower than
normal indexed clicking — the tool docstrings tell the model to only
reach for it when nothing else works.

## Reliability features in the browser agent

- Stable element indices across a page's lifetime
- `[disabled]`/`[checked]`/`[unchecked]`/`[likely-unread]` markers
- One tool call per turn (`parallel_tool_calls=False`) — prevents the
  model from batching DOM-mutating actions that then race each other
- Loop detection for repeated mutating actions, exempting read-only tools
- LLM calls retry 3x, then gracefully `finish` instead of crashing
- A native OS file-picker never appears — `browser/session.py` intercepts
  the `filechooser` event so uploads stay programmatic

## Extending this project

See **`docs/ADDING_FEATURES.md`** — every capability here follows the
same shape (mechanics module -> tool wrapper -> optional registered task),
so adding a new platform/integration means adding to that chain, not
restructuring anything existing.
