# Cleanup, Context Fixes and Demo Tenant

Everything in this document describes changes already applied to this tree. It is
organised as: the memory problem and its fix, the cleanup, the demo company, and
what is still outstanding.

---

## Part 1 — Conversation context: what was broken

The question was whether chat and calling retain context from previous
conversations. The answer was *partly*, and the gaps sat exactly where a
demonstration would expose them.

### 1.1 What already worked

**Chat, within one thread.** `conversation_flow.handle_customer_turn()` loaded
every `LeadMessage` for the conversation and handed the last eight turns to the
model. Correct, and unchanged in principle.

**Voice, within one call.** `SimpleAgent` (`multiligual_call.py:708`) keeps the
full turn list in `self.history` and only compresses once the raw transcript
passes 40 turns, retaining the last 20 verbatim plus a rolling summary. This is
a better design than most voice agents ship with.

**Call transcripts flowing back into chat.** `call_bridge.sync_call_transcript()`
mirrors call turns into `leadai_messages` idempotently, so after a call the lead
thread contains everything.

### 1.2 Gap one — chat context never reached a call

`call_bridge.prepare_agent_context()` built the voice agent's prompt from the
company script alone. It never read `leadai_messages`. The consequence:

> A customer spends ten minutes in the chat widget explaining they want a six
> lakh personal loan, they are salaried, and they earn ₹62,000 a month. Staff
> click *Call*. The agent opens with "Hi, I'm Aanya from Nexa Finserv, how can I
> help?" and asks whether they are salaried or self-employed.

Memory flowed one way only: calls fed chat, chat never fed calls.

**Fixed.** `prepare_agent_context()` now accepts a `conversation` and prepends
two sections built by `memory.voice_briefing()` — a "Caller Context" digest and a
"Recent Messages" block with the last turns verbatim.

Prepended, not appended, deliberately. `xml_parser.sections_to_prompt()`
concatenates in order and instruction-following degrades toward the middle of a
long prompt. The "you have spoken to this person before, do not introduce
yourself" framing has to land before the agent reads its opening line, or it will
introduce itself anyway. The company's Identity section follows immediately, so
persona is unaffected.

When there is no history — a genuinely cold outbound lead — this adds nothing and
the prompt is byte-identical to before.

### 1.3 Gap two — retrieval had no memory, so the bot escalated on its own KB

Retrieval embedded the raw customer utterance. Consider a real three-turn
sequence:

```
customer: Tell me about the salaried personal loan
Aanya:    ...12.99% onwards, up to ₹25 lakh...
customer: and what about the processing fee on that?
```

The third message contains no product noun. Embedded alone it retrieves almost
nothing, `confidence` lands near 0.1, the handoff rule fires, and the bot
escalates a question that is squarely inside its own knowledge base.

This is worse than it first appears: it fires on the *second* question of most
conversations. That is precisely the moment a demo audience is paying attention.

**Fixed.** `ai_engine.answer()` now retrieves against
`memory.retrieval_query(question, history)`, which prepends salient nouns from
recent customer turns when — and only when — the utterance looks like a follow-up
(short, opening with a connective, or leaning on a pronoun).

Verified behaviour:

| Utterance | Query actually embedded |
|---|---|
| `and what about processing fees for that?` | `Tell about salaried personal loan and what about processing fees for that?` |
| `ok` | `Tell about salaried personal loan ok` |
| `Tell me about home loan eligibility criteria` | *(unchanged — already self-contained)* |
| `what documents do I need` *(no history)* | *(unchanged)* |

Confidence scoring still uses the **original** question. Lexical coverage measures
how well a retrieved sentence answers what the customer actually asked, and
padding that side with carried-over words would inflate confidence and suppress
handoffs that should happen.

### 1.4 Gap three — returning customers on a new channel were strangers

A customer who chats on the website in March and WhatsApps in April gets a new
`LeadConversation` row — correctly, since it is a different channel and a
different thread. But the AI met them with no memory of March.

**Fixed.** `handle_customer_turn()` builds a cross-channel digest via
`memory.customer_memory()` and injects it as a leading `system` turn.

Two decisions worth noting. First, it is built **only at thread start**
(`len(history) <= 2`); mid-conversation the thread itself carries context, and
re-injecting the digest every turn wastes prompt budget and makes the bot repeat
old facts. Second, it is **not merged into the knowledge context** — the "answer
only from company knowledge" rule must keep governing product facts while the
agent is still free to use what it knows about the person.

### 1.5 Gap four — audit rows were being fed back as assistant turns

The old inline mapping turned every `LeadMessage` into a chat turn, including
`system` rows like *"Outbound ai_voice call placed via twilio (+9198\*\*\*\*\*210)."*
Those were reaching the model as **assistant** messages — text for it to imitate.

**Fixed.** `memory.llm_window()` filters to `customer` / `ai` / `agent` senders
only, and the window widened from 8 to 12 turns.

### 1.6 The new module

`LeadAI/services/memory.py` — read-only, never writes, every path degrades to
empty string. No branch in it may raise into a live call.

| Function | Purpose |
|---|---|
| `thread_history()` | Messages for one conversation. Centralised so voice and chat cannot window differently. |
| `llm_window()` | Maps rows to OpenAI chat format, dropping `system` audit rows. |
| `customer_memory()` | Cross-channel digest: lead qualification fields, prior conversation summaries tagged by channel, current summary. |
| `retrieval_query()` | History-aware search rewriting. |
| `voice_briefing()` | Renders the digest as `xml_parser`-shaped sections for splicing into `xml_sections`. |

Budgets are counted in **characters, not tokens**, on purpose. Token counting
needs the tokeniser for whichever model is configured (Sarvam on voice, OpenAI on
chat); being 15% off on a 1,400-character budget costs nothing, while being wrong
about which tokeniser to import costs an ImportError at call time.

### 1.7 Files touched

```
NEW   LeadAI/services/memory.py
EDIT  LeadAI/services/call_bridge.py       prepare_agent_context() + call site
EDIT  LeadAI/services/ai_engine.py         retrieval query, llm_window, carryover param
EDIT  LeadAI/services/conversation_flow.py history load, carryover digest
```

All four parse clean. The pure functions in `memory.py` were unit-tested against
the cases in the table above.

---

## Part 2 — Cleanup

### 2.1 Applied

**`config_guard.py` — fail-fast configuration.** The insecure defaults live in
three places (`auth.py:11`, `multiligual_call.py:169`, `LeadAI/config.py:79`),
all falling back to `"demo-secret-key"`. Fixing them in place would mean editing
`multiligual_call.py`, which the project's integration doctrine keeps
byte-identical to production.

So instead the environment is validated once, as the **first import in
`main.py`** — before `multiligual_call` is imported, because that module builds
its `SessionMiddleware` with `os.getenv("SESSION_SECRET", "demo-secret-key")` at
import time. A check placed after that import would be reporting on a secret
already baked into the middleware.

`ENVIRONMENT=production` → exit 78 on any critical issue. Anything else → loud
warnings, boot continues. Development stays frictionless; production cannot start
misconfigured.

It also catches something subtle: an unset `LEADAI_PII_KEY` makes
`security._fernet()` derive a key from the JWT secret. That works — until someone
rotates the JWT secret, at which point every stored phone number and email
becomes permanently unreadable. In production that has to be an explicit choice.

**`Dockerfile`** — the missing one. `docker-compose.yml` uses `build: .` and the
CI workflow builds an image, but `dockerfile` was in `.gitignore`, so neither
could run from a fresh clone. Two-stage (compilers stay in the builder), non-root,
health-checked against `/api/leadai/health`.

It runs **one uvicorn worker**, with the reason in a comment: `active_calls`,
`transcript_connections` and the websocket manager are process-local dicts in
`multiligual_call.py`. A second worker would field the `/media-stream` webhook
for a call it has never heard of and the audio leg would fail silently. Scale by
running more containers behind a sticky load balancer, or move that state to
Redis first.

**Port mismatch resolved.** `main.py` defaulted to 5050, compose and CI
hard-coded 6789, the README's ngrok script tunnelled 5050 — three answers for one
service. `PORT` is now the single source of truth, threaded through compose and
both services, defaulting to 6789 to preserve existing deployment behaviour.

**`pyproject.toml`** — ruff config replacing black + isort + flake8. Line length
100, not black's 88: the code is already written at roughly that width, and
reflowing every file to 88 would produce a diff large enough to bury the
interesting changes. Legacy files are quarantined per-file rather than globally,
so `ruff check` stays green on new code instead of drowning in pre-existing
findings — the intent is to delete entries from that list module by module.

**`scripts/cleanup_repo.sh`** — dry-run by default, because two steps delete
thousands of lines and you should read the list first.

### 2.2 Scripted but not executed

Run `./scripts/cleanup_repo.sh --apply` when ready:

1. **Scrub the leaked token from `README.md`.** The script refuses to pretend
   this is sufficient — see below.
2. **Delete `voice_agent.py`.** 2,104 lines, 17 route decorators, zero importers.
   It is a fork of `multiligual_call.py`, not a copy (only 5 of 24 function names
   overlap), so it cannot be diffed away — it is simply the previous generation.
   The script re-verifies there are no importers before deleting.
3. **Report the agent-fork divergence.** `social_agent/` and
   `browser_agent_v2-main/` share 41 Python files: **27 byte-identical, 14
   diverged**. The script lists the 14 but does not act — which fork wins is a
   judgement call about which divergences to keep.
4. **Delete `dll_file/`.** `RNNOISESHARE.dll` and `prompts.cp310-win_amd64.pyd`
   are referenced by no Python source, and the `.pyd` is a CPython 3.10 *Windows*
   extension that cannot load on the Linux runtime image anyway.
5. **Un-ignore the Dockerfile.**
6. **Strip caches**, then format and lint.

### 2.3 Do this before anything else

`README.md` line 21 contains a live GitHub personal access token
(`ghp_aPZRq…`) embedded in a clone URL for
`BharatLogic-com/AI-outbound-Agent-Backend`.

**Revoke it on GitHub first.** It is in git history regardless of what happens to
the working copy, and scrubbing the file while the token stays valid is strictly
worse than leaving it visible — it stops anyone noticing.

---

## Part 3 — Demo tenant: Nexa Finserv

A Mohali-based digital lending NBFC. Lending was chosen because it produces
natural qualification questions (amount, employment type, income, timeline), it
works identically across chat, social and voice, and it gives the agent hard
factual boundaries — a rate quoted wrong is obviously wrong, which makes the
"answers only from the knowledge base" property visible to an audience rather
than merely claimed.

### 3.1 The four files

```
demo/nexa_finserv_knowledge_base.md    → RAG corpus
demo/nexa_finserv_agent_script.xml     → persona, flow, guardrails
demo/nexa_finserv_prompts.json         → the five per-channel prompt overrides
demo/seed_demo_company.py              → provisions everything, then proves it
```

### 3.2 Division of labour

This is the part that determines whether the demo holds up under questions.

**The knowledge base carries every product fact** — rates, limits, eligibility,
fees, document lists, turnarounds. Five products, plus a fees-and-charges
section, the application journey, and a common-questions section.

**The script carries persona, flow and guardrails, and deliberately no product
facts.** Duplicating rates into the script would create two sources of truth that
drift, and the first time someone edits the KB without editing the script, the
agent starts quoting a stale rate on a live call.

**The prompts carry only what differs per channel** — chat versus voice versus
escalation. They are intentionally short. Repeating the script in them would
double prompt size for no gain and give the model two places to disagree with
itself.

### 3.3 Script validation

Validated against your own `xml_parser.parse_xml_to_sections()`:

```
sections parsed: 12
  Identity | identity            5 fields
  Mission | text                 430 chars
  Source Of Truth | text         485 chars
  Qualification Fields | data    7 fields
  Conversation Flow | script     6 steps
  Qualification Questions | list 9 items
  Handling Eligibility | list    4 items
  Rules | rules                  must-do + must-not
  Scenarios | scenarios          10 scenarios
  Personality | personality      4 tones
  Output | output                3 special rules
  Data Not Available | data-unavailable  5 items

rendered prompt: 9,561 characters
```

It exercises every section type the parser supports, so it doubles as a
reference template for authoring the next company's script.

The persona is **Aanya, senior loan advisor** — warm, direct, unhurried, no
exclamation marks, no gushing. The `Output` section forbids URLs, long numbers
and lists of more than three items on voice, and requires amounts spoken as a
person speaks them ("twenty-five lakh", not "2500000").

### 3.4 Running it

```bash
export LEADAI_TOKEN="<staff bearer token>"
python demo/seed_demo_company.py --base http://localhost:6789
```

The email in that token must be in `LEADAI_BOOTSTRAP_ADMINS`, or already hold a
platform-admin grant, for `company.manage` to pass.

Five steps: find-or-create the company, upload the KB and **poll until indexing
completes** (rather than sleeping a fixed interval — embedding takes ~2s with an
OpenAI key and ~0.2s on the offline fallback, and a fixed sleep is wrong for one
of those), upload and default the script, write the prompts, then run a ten-turn
scripted chat against the public widget endpoint printing confidence, lead score
and status per turn.

Step five is the point. Seeding is easy; what you need before a demo is evidence
that retrieval is *answering* rather than escalating. The transcript includes the
deliberate context-dependent follow-up:

```
customer  what interest rate can I get?
customer  and the processing fee on that?     ← the turn that used to fail
```

Re-running is safe. The company is looked up by name, the script is versioned
rather than overwritten, prompt writes are idempotent PUTs. The KB is the one
exception — pass `--skip-kb` on a re-run to avoid a second copy.

If turns come back low-confidence, it is almost always one of three things: the
KB never finished indexing, `OPENAI_API_KEY` is unset so you are on the offline
extractive path, or the question genuinely is not covered — which is correct
behaviour, not a bug.

---

## Part 4 — Still outstanding

Ordered by what would hurt most.

**Test coverage is near zero.** `test_tenancy.py` and
`LeadAI/functional_test.py` exist; nothing covers the voice pipeline, batching or
the agents. For a system that spends real money per call, this is the largest
remaining risk. The natural first target is `memory.py` — its pure functions are
trivially testable and they now sit on the critical path for every channel.

**Global mutable state blocks horizontal scaling.** Documented in the Dockerfile
and mitigated by the single-worker default, but not solved. Moving `active_calls`
and the websocket manager into Redis is the unlock for running more than one API
replica.

**The two agent forks.** 14 diverged files, no canonical version. Every day this
persists is another day a fix lands in one and not the other.

**Error swallowing.** 140+ `except Exception` blocks across the three legacy
files; `batching.py` has 59 `print()` calls instead of logging. Ruff's `T20` rule
is configured and currently suppressed for those files — working through them is
mechanical but should be done a module at a time, not in one pass, against a
system that places real calls.

**`database.py` header-based DB selection.** `get_db_from_headers` reads an
unverified `user-email` header and defaults to `admin@gmail.com`. It is currently
inert — every path returns the same session — but it is a trap for whoever wires
up real per-tenant routing later. `get_db_from_query` has the same shape plus a
typo'd default (`admin@ogmail.com`).

**Raw SQL interpolation** in `database.py:56-58` (`SHOW DATABASES LIKE '{db_name}'`
and `CREATE DATABASE {db_name}`). The input is an environment variable rather
than user input, so it is not reachable by an attacker today, but it should still
be an identifier check rather than an f-string.
