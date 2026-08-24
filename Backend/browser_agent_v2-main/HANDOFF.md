# Project Handoff: Agentic Browser System

*Paste this entire document as your first message in a new chat, along with
the attached project zip, to continue this work with full context.*

## What this is

An LLM-powered browser automation agent: given a plain-English task, it
drives a real browser (Playwright) to complete it — navigating, clicking,
typing, reading pages — using an LLM (currently Google Gemini) to decide
what to do at each step. Built with LangChain + LangGraph.

**Original goal**: a general-purpose agentic browser tool.
**Current direction**: the user's manager wants this integrated into a
**lead-generation system** — prospecting, enrichment, and outreach on
platforms like LinkedIn/Facebook/Instagram — and separately wants a
**CRAG (Corrective RAG)** module added so the agent can answer factual
questions about the company/products/pricing without hallucinating.

**Important standing caution already raised with the user**: automating
outreach/scraping on social platforms raises real ToS, account-ban,
detection, and (for lead gen specifically) legal/compliance concerns
(GDPR/CCPA, anti-spam rules). This hasn't blocked development, but it's an
open conversation the user needs to have with their manager about scope —
not yet resolved as of this handoff.

## Architecture (current, as of this handoff)

```
agentic_browser/
    browser/
        session.py       # Playwright lifecycle: launch/teardown, new-tab
                          # tracking, JS dialog auto-accept, engine choice
                          # (chromium/firefox — firefox is DEFAULT)
        browser.py        # page actions: goto/click/type_text/scroll/
                           # extract_text/upload_file/get_state (DOM
                           # indexing) / screenshot_with_boxes (vision)
    agent/
        graph.py            # LangGraph state machine: agent/tools/nudge/
                             # stuck/attach_screenshot nodes, loop
                             # detection, LLM retry+fallback, vision toggle
        orchestrator.py       # run_prompt()/run_agent_task(): the engine
                               # everything else calls into; supports
                               # sharing one browser across multiple calls
        registry.py             # named, parameterized task prompts
        memory.py                 # message-history trimming + debug logging
    tasks/
        task_1.py                  # ONE standalone task, zero-argument run
    workflows/
        facebook_daily_routine.py    # chains multiple bounded skills on
                                      # ONE shared browser session
    crag/                             # STANDALONE, portable RAG module —
        ingest.py                     # zero dependency on browser/agent
        vector_store.py                # code. Public API is crag/engine.py
        grader.py                       # only. Retrieve -> grade -> branch
        web_search.py                    # (web search only if NOTHING
        crag_graph.py                     # relevant found locally) -> gen
        engine.py                          # CragEngine — the only import point
    knowledge/company_docs/               # placeholder fictional company
                                            # docs — REPLACE with real ones
    tools.py                    # LangChain tool wrappers (includes
                                # answer_company_question -> CRAG)
    models.py                    # LLM construction (ChatGoogleGenerativeAI,
                                  # both TEXT_MODEL and VISION_MODEL since
                                  # Gemini is natively multimodal)
    utils.py                      # generic helpers: cookie banners, popups,
                                   # DOM-settle wait, safe_click retry
    main.py                        # interactive entrypoint
    scheduler.py                     # 24/7 unattended runner
    save_login_session.py             # one-time manual login -> persistent
                                       # browser profile (engine-selectable)
    requirements.txt
    README.md                          # user-facing setup/usage docs
```

## Key design decisions and WHY (don't relitigate these without reason)

- **Elements are indexed with stable, persistent numbers** (not
  re-numbered every DOM scan) — fixes clicking the wrong element after a
  scroll/lazy-load shifts order.
- **Loop detection only applies to *mutating* actions** (click/type/goto/
  upload). Read-only tools (`extract_text`, `dismiss_popups`,
  `answer_company_question`) are explicitly exempt — they were incorrectly
  getting blocked as "repeats" even when legitimately re-checking state.
- **LLM calls retry 3x then synthesize a `finish` call** instead of
  crashing — this caught a real Groq/gpt-oss decoding glitch earlier.
- **Verification-before-finish is an explicit system prompt rule** — the
  agent once falsely claimed a Facebook post succeeded when it hadn't.
- **Each "skill" (workflow step) is its own bounded agent run with a
  fresh message history** — NOT one giant task. The browser session (still
  logged in) carries continuity between skills; the LLM's conversation
  does not. This was a deliberate architecture choice to avoid message
  bloat and hallucination compounding across a long multi-step routine.
- **Persistent Chrome/Firefox profiles (`user_data_dir`), not cookie
  export** — Google/LinkedIn/Facebook actively detect automation; a real
  profile where a human did the actual login is meaningfully harder to
  detect than restoring exported cookies into a fresh automated browser.
  **Firefox is currently the default engine** (the user's preference,
  confirmed explicitly — don't silently revert to Chrome).
- **Text-based DOM description is the default; vision mode
  (`ENABLE_VISION` in `agent/graph.py`) is opt-in**, since it costs more
  tokens/latency. It's genuinely necessary for one class of problem: media
  messages (images/videos sent in a chat) that text extraction literally
  cannot describe.
- **CRAG only web-searches when NOTHING relevant was found locally** — an
  earlier version searched whenever retrieval was "mixed" (some relevant,
  some not, which is normal/expected with top-k retrieval), which polluted
  answers about the (fictional) test company with real competitors' data.
- **`crag/` has zero imports from `browser/`/`agent/`** — deliberately
  portable to other projects. The only integration point is `tools.py`
  importing `crag.engine.CragEngine`.

## Known limitations / open items (not yet done)

- No iframe or shadow DOM support in element indexing (flagged as
  higher-risk, deliberately saved for last).
- Vision mode uses Gemini for both text and vision (same model) — works
  because Gemini is natively multimodal, unlike the earlier Groq setup
  which needed separate models.
- The lead-gen ToS/legal conversation with the user's manager is NOT
  resolved — don't assume broader scraping/outreach is approved.
- `EMBEDDING_MODEL` in `crag/vector_store.py` was changed by the user
  themselves (deprecation on Google's end) — don't overwrite their choice
  without confirming the current correct model name first.
- Real company knowledge-base docs haven't been provided yet — CRAG is
  currently tested against placeholder fictional "Nimbus Cloud" docs in
  `knowledge/company_docs/`.

## How to resume in a new chat

1. Paste this document as the first message.
2. Attach/upload the current project zip (or extract it and reference the
   files directly).
3. State what you want to work on next.
