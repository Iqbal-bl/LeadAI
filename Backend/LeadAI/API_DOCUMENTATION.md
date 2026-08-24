# LeadAI × AIOutbound — Backend Integration Guide & API Reference

Everything below describes code that is **in this zip and running**. Nothing is aspirational.

- **Base URL:** `{SERVER_URL}/api/leadai`
- **Interactive docs:** `{SERVER_URL}/docs` — the LeadAI routes appear under the `LeadAI • …` tags alongside your existing endpoints
- **Health check (public):** `GET /api/leadai/health`

---

## Part 1 — What was added, and what was not touched

### The one-line summary

A multi-tenant AI lead-generation platform was added **alongside** your outbound voice app, sharing its database, its auth, its logging conventions and — critically — **its existing call pipeline**.

### Files changed in your existing codebase: exactly one

`main.py` gained a 22-line block at the bottom:

```python
try:
    from LeadAI.integration import register as register_leadai
    register_leadai(app)
except Exception as _leadai_exc:
    logger.error(f"[LeadAI] not registered ...")
```

That is the entire modification. `multiligual_call.py`, `voice_agent.py`, `batching.py`, `db.py`, `database.py`, `auth.py`, `Domain/models.py`, `xml_parser.py`, the `bot/` package, the `Repositories/`, the `scripts/` — **byte-identical**. The `try/except` means that if LeadAI ever fails to import, your voice agent and batch calling keep serving normally.

### Everything else lives in `LeadAI/`

```
LeadAI/
├── integration.py          ← the ONLY thing that touches the existing app
├── config.py               ← settings, all env-driven, all with defaults
├── models.py               ← 12 new tables, all prefixed leadai_
├── db.py                   ← session on the EXISTING MySQL engine
├── rbac.py                 ← roles, permission matrix, tenant scoping
├── activity.py             ← audit logging
├── security.py             ← PII encryption + chat session tokens
├── schemas.py              ← request/response contracts
├── serializers.py          ← ORM→DTO + the PII masking rule
├── router.py               ← aggregator
├── requirements-leadai.txt
├── .env.leadai.example
├── services/
│   ├── embeddings.py       ← OpenAI embeddings + offline fallback
│   ├── vectorstore.py      ← hybrid retrieval, tenant-scoped
│   ├── ingest.py           ← extraction + chunking
│   ├── llm.py              ← OpenAI chat completions
│   ├── ai_engine.py        ← answer / qualify / summarise / handoff
│   ├── script_engine.py    ← per-company dynamic scripts
│   ├── telephony.py        ← Exotel + Twilio adapters
│   ├── call_bridge.py      ← ← THE BRIDGE to your existing call pipeline
│   └── cache.py            ← Redis or in-process
└── routers/
    ├── chat.py             ← PUBLIC customer widget (lead generation)
    ├── inbox.py            ← staff inbox, RBAC-enforced
    ├── knowledge.py        ← per-company knowledge bases
    ├── scripts.py          ← scripts + prompts
    ├── companies.py        ← companies (tenants) + AI settings
    ├── roles.py            ← RBAC management
    ├── voice.py            ← calls, reusing your pipeline
    ├── analytics.py        ← dashboards
    └── activity.py         ← audit log + health
```

### Alignment with your existing conventions

| Your convention | How LeadAI follows it |
|---|---|
| `base.Base` declarative base | All 12 tables registered on it — so your `db.connect_db()` creates them too |
| `database.py` MySQL engine | Same engine, same pool. **No second connection pool.** |
| PascalCase columns + `Id/CreatedBy/CreatedAt/UpdatedBy/UpdatedAt/IsDeleted` | `LeadAI/models.py:LeadAIBase` mirrors `Domain/models.py:BaseDocument` exactly |
| Identity-server auth via `TokenValidationMiddleware` | Reused unchanged. LeadAI adds *authorization* on top, never a second login. |
| `Clients` table for companies | **Reused as the tenant.** No competing "tenants" table. |
| `BatchLogs (LogMessage/LogType)` logging shape | `leadai_activity_logs` uses the same shape plus audit columns |
| `Websockets.connection.manager` | Reused for LeadAI's live channels |
| XML scripts via `xml_parser` | **Same dialect, same parser.** Scripts move to DB, format unchanged. |

---

## Part 2 — Conceptual architecture

### 2.1 The RAG design (and why it's built this way)

**Which RAG?** A **hybrid dense + sparse retriever with sentence-level reranking and retrieval-derived confidence**, tenant-partitioned. Not LangChain, not LlamaIndex — direct implementation, because the framework overhead buys nothing here and the confidence signal (below) needs access to the scoring internals that frameworks hide.

**The pipeline:**

```
Document (pdf/docx/txt/md/csv/html)
   │
   ├─ extract        pypdf / python-docx / plain decode
   ├─ clean          collapse whitespace, rejoin PDF hard-wraps, keep headings
   ├─ chunk          paragraph-aware, ~900 chars, 150-char word-boundary overlap
   ├─ embed          OpenAI text-embedding-3-small (1536-d)
   └─ store          leadai_kb_chunks (ClientId, DocumentId, ChunkText, Embedding)
                     └─ optional: Qdrant, with client_id payload filter

Question
   │
   ├─ embed query    same model
   ├─ score          hybrid: 0.55·normalised_cosine + 0.45·idf_lexical_coverage
   ├─ rerank         sentence-level, relative 70%-of-best cutoff
   ├─ confidence     0.45·chunk_strength + 0.55·best_sentence_coverage
   └─ generate       gpt-4o-mini, grounded ONLY on retrieved chunks
                     └─ or extractive fallback if no API key / API down
```

**Why hybrid instead of pure vector search.** Pure cosine fails this workload in two specific ways:

1. **Length dilution.** A four-word question against a 900-character chunk produces a small dot product even on an exact hit, so a short irrelevant chunk can outrank the paragraph that literally contains the answer.
2. **Rare-token blindness.** Embeddings smooth over exactly the tokens that decide a B2B question — "CLIA", a policy number, "Dubai", a product name. Two passages about "coverage" look near-identical when only one mentions travel insurance.

The sparse half is **IDF-weighted lexical coverage**: what share of the *question's* meaning does this passage cover, weighting each query term by how rare it is in *that company's* corpus. A term the corpus has never seen is treated as maximally informative — so **failing to match it is what sinks the score**.

**Why that matters more than retrieval quality: it produces the confidence number.** Confidence is computed from measured retrieval coverage, *never* asked of the model (a model's self-reported confidence is worthless). Low coverage → low confidence → **hand off to a human instead of hallucinating**. That is the core safety property of the whole product, and it's why retrieval scoring is implemented rather than delegated.

**Tenant isolation by construction.** The `ClientId` filter is applied *inside* `vectorstore.py`, in both backends, as a **required positional argument on every function**. There is no code path that can search without it. Verified in testing: identical query, two companies → Acme answers at confidence 1.0, Globex escalates at confidence 0.0.

**Offline fallback.** With no `OPENAI_API_KEY`, embeddings become deterministic hashed-ngram vectors and answering becomes extractive (quoting the company's own documents). This exists for two reasons: local dev needs no key, and an OpenAI outage degrades to lexical retrieval rather than breaking the product. The extractive path is *safe by construction* — it can only return sentences that exist in the company's documents.

Each chunk stores its `EmbeddingModel`. Mixing vector spaces makes cosine meaningless, so `/knowledge/stats` reports `needs_reindex: true` when more than one model is present, and `/knowledge/documents/{id}/reindex` fixes it.

### 2.2 The AI stack

| Concern | Provider | Where | Notes |
|---|---|---|---|
| Embeddings | **OpenAI** `text-embedding-3-small` | `services/embeddings.py` | 1536-d, batched 64/request, multilingual enough for Hinglish phone transcripts |
| LLM | **OpenAI** `gpt-4o-mini` | `services/llm.py` | temp 0.25 (factual Q&A); 220-token cap on voice, 600 on chat |
| Vector store | MySQL + numpy, or **Qdrant** | `services/vectorstore.py` | Qdrant auto-detected via `QDRANT_URL` |
| Telephony | **Exotel**, falling back to your Twilio leg | `services/telephony.py` | Indian carrier termination + DLT caller ID |
| STT | **Sarvam** | your existing `sarvam_stt.py` | unchanged |
| TTS | **Sarvam** | your existing `sarvam_tts.py` | unchanged |
| Cache | Redis or in-process | `services/cache.py` | needed for rate limits across workers |

**Why Exotel for the India leg:** it terminates on Indian carriers with a DLT-registered local caller ID, which drives both answer rates and TRAI compliance; Twilio's Indian termination is more restricted.

**The key insight that made voice reuse possible:** Exotel's bidirectional media streaming (Voicebot/Stream applet) delivers **8 kHz mono PCM base64 frames over a WebSocket — the same shape your `/media-stream` handler already consumes from Twilio.** The audio pipeline is carrier-agnostic. Only call origination and the status webhook differ, and both are isolated in `telephony.py`. Point an Exotel flow at your existing `/media-stream` and the entire STT → LLM → TTS loop is reused untouched.

### 2.3 How voice reuses your existing pipeline

This is the part worth reading closely.

**What your app already does:** `/api/make-call` places a call and stashes agent config in the module-level dict `active_calls[call_sid]`. When the carrier hits `/outbound-twiml` and opens `/media-stream`, the handler reads `active_calls[call_sid]["xml_sections"]` (plus language/gender/speaker/multi_stt) and builds a `SimpleAgent`. Transcript turns go to `conversations`, status to `calllogs`/`callstatus`, audio to `recordings`.

**The limitation:** config was chosen *per browser session* (`session_xml_sections[session_id]`) and scripts lived as files on disk. Fine for one operator running one campaign; it cannot express "Acme Bank's agent behaves this way, Globex Realty's behaves that way, both live simultaneously."

**What `call_bridge.py` does:** populates `active_calls[call_sid]` from a **company's** script + knowledge base instead of from the operator's session.

```
POST /voice/conversations/{id}/call
   │
   ├─ 1. resolve the company's voice script  (script_engine)
   ├─ 2. parse to sections                    (YOUR xml_parser — cached on the row)
   ├─ 3. decrypt the customer's phone, validate  (YOUR validate_number)
   ├─ 4. place the call                       (Exotel, else YOUR twilio_client)
   ├─ 5. register_call_context()              → active_calls[call_sid] = {...}
   └─ 6. write the CallSid ↔ conversation link (leadai_calls)

           ↓ carrier calls back, milliseconds later

/outbound-twiml → /media-stream → SimpleAgent  ← ALL UNCHANGED
   reads active_calls[call_sid]["xml_sections"] — now the company's script
   transcript → conversations table (unchanged)

           ↓ call ends

POST /voice/calls/{id}/sync   (or the Exotel webhook fires it automatically)
   └─ mirror conversations → leadai_messages → re-qualify → re-summarise
```

**Two design points:**

*Ordering.* Step 5 must happen before the carrier can hit `/outbound-twiml`. Call placement returns as soon as the carrier accepts; the TwiML webhook arrives milliseconds later. The registry write is therefore synchronous and never deferred.

*Mirroring, not moving.* `conversations` stays the single source of truth for call transcripts — your existing reporting, CSV exports and batch flows read it. Mirroring into `leadai_messages` is additive and **idempotent** (keyed on CallSid + sender + content), so re-running it cannot duplicate turns. The payoff: **the same qualification and summarisation pipeline that scores a chat also scores a phone call.** One brain, two channels.

Verified in testing: `active_calls` was populated with the company's 18-section script, and transcript sync imported turns and re-scored the lead to `qualified/100`.

### 2.4 Dynamic per-company scripts

Scripts moved from disk to `leadai_company_scripts`, keyed by `ClientId` — but **the XML dialect is unchanged**, parsed by your own `xml_parser.parse_xml_to_sections`. A company script therefore drives a real voice call with no translation layer.

**Resolution order** for "which script governs this conversation?":

1. explicit `script_id`, if owned by this company
2. the company's default for the requested channel (`chat` / `voice`)
3. the company's default for channel `all`
4. any active script for the company
5. none → prompt templates + knowledge base alone (still a grounded assistant)

**Prompt layering.** The system prompt is composed in two layers, deliberately kept separate:

```
Layer 1: channel template (sales / voice / escalation)  ← the ROLE + grounding rules
Layer 2: the company's script sections                  ← persona, behaviour, flow
```

An admin can rewrite a company's script without being able to delete "answer only from the knowledge base." That guard-rail is exactly what you don't want a non-technical user able to remove.

Scripts are **versioned by name**: editing bumps `Version`, so a company can prove what wording its agent used on a given date. `POST /scripts/{id}/preview` renders the exact final prompt — the single most useful debugging endpoint when someone says "the bot isn't following the script."

`GET /scripts/importable` + `POST /scripts/import` adopt your existing `scripts/*.xml` files, so a working script can be migrated rather than re-authored.

### 2.5 RBAC

Authentication is **not** re-implemented. Your `TokenValidationMiddleware` validates every request against the identity server and `auth.get_current_user` yields an email. LeadAI answers the *next* question: what may this identity do, to which company's data?

**Two independent gates, both required:**

1. **Permission** (route level) — does this role hold this capability?
2. **Tenant scope** (query level) — is this company one they may touch?

Plus a third for restricted roles: **row-level visibility** — an `agent` sees only conversations assigned to them.

**Roles** (`leadai_user_roles`, keyed on email; `ClientId` NULL = global):

| Role | Scope | Summary |
|---|---|---|
| `Admin` | all companies | everything; only role that can create companies or mint another platform admin |
| `company_admin` | one company | full control of their company incl. PII reveal |
| `manager` | one company | see/assign all leads, no PII reveal, no KB writes |
| `agent` | own leads only | reply, claim, call — **no PII reveal**, no config |
| `viewer` | own leads only | read-only |

23 named permissions; full matrix at `GET /access/permissions`.

**Design details worth knowing:**

- `resolve_scope()` is the **only** place a company id is chosen. Routers never read `ClientId` from a request body for a company-scoped user — that is what makes cross-tenant leakage structurally impossible rather than merely absent.
- `_load()` in the inbox returns **404, not 403**, for a conversation in another company or assigned to someone else. Telling an agent "that exists but isn't yours" leaks the existence of other leads.
- You **cannot demote or deactivate yourself** — the classic way an admin locks a company out of its own dashboard.
- Role revocation is a **soft delete**: the audit trail must still explain a past action by someone whose access was later removed.
- **Bootstrap:** a fresh install has no grants, which would lock everyone out. `LEADAI_BOOTSTRAP_ADMINS` auto-grants `Admin` on first API call. Remove it once real admins exist.

Verified in testing: agent blocked from PII reveal (403), from creating a company (403), from another company's inbox (403), and saw only their 1 assigned conversation out of the company's total.

### 2.6 PII protection

Customer phone/email/WhatsApp/Instagram are **Fernet-encrypted at rest**. Agents see `Customer #48022` and a masked phone `+919876*****210`. The masking rule lives in `serializers.py` — in *one* place, applied by the two functions that build *every* conversation response, so no route can forget it.

- `GET /inbox/{id}/contact` is the **only** endpoint that decrypts PII into a response. It requires `lead.reveal_pii` and writes a `Security`-level audit row.
- That audit row records **who** revealed **whose** contact — never the values. `activity._safe_meta()` redacts contact-shaped keys, so the log proves what happened without becoming a second copy of the data it protects. (Verified: the reveal row's meta contains only `customer_ref` and `conversation_id`.)
- `PhoneHash` is a **keyed** HMAC (not plain SHA) so a leaked database can't be attacked by hashing every possible Indian mobile number.
- Bulk `/inbox/export/leads` deliberately **excludes** contact details — exporting raw PII in bulk would defeat the reveal audit trail.

### 2.7 Activity logging

Every state-changing action writes to `leadai_activity_logs`. Two rules make it trustworthy:

1. **It never raises.** A logging failure must not roll back a business transaction that succeeded. Writes are wrapped, and if the caller's session is poisoned the row is retried on a fresh session.
2. **It records the actor's role at the time of the action.** Roles change; the log should still explain why the action was permitted.

`log()` does not commit by default, so an action and its audit row land in the **same transaction**. The log is **append-only by design** — there is no update or delete endpoint, and none should be added. An audit trail an admin can edit is not an audit trail.

~45 canonical action names in `activity.A`. Verified in testing: a single end-to-end session produced 25 audit rows across 12 distinct actions.

### 2.8 The lead qualification engine

Three things happen on **every** customer turn:

**1. Answer** — retrieve, generate, attach a retrieval-derived confidence.

**2. Qualify** — recompute intent / budget / timeline / product / sentiment and a 0–100 score. **Recomputed from scratch each turn, never incremented**, so a correction late in the conversation fixes the score instead of leaving a stale signal.

Scoring is additive and fully explainable — the breakdown is stored on the row so the dashboard can show *why* a lead is hot:

```json
{"base": 8, "engagement": 24, "intent": 32, "timeline": 22,
 "budget_known": 12, "product_known": 8, "sentiment": 0}
```

| Component | Range | Signal |
|---|---|---|
| base | 8 | started a conversation |
| engagement | 0–24 | 6 per customer turn, capped |
| intent | 4–32 | ready_to_buy 32 · comparing 20 · evaluating 16 · browsing 4 |
| timeline | 0–22 | immediate 22 · this_month 15 · next_quarter 7 |
| budget_known | 0–12 | a money figure was extracted |
| product_known | 0–8 | product resolved against the knowledge base |
| sentiment | −8–6 | positive 6 · neutral 0 · negative −8 |

Bands: `cold` <36 · `warm` 36–61 · `hot` 62–77 · `qualified` ≥78 **and** all three facts known **and** ≥3 customer turns **and** intent `ready_to_buy`.

That compound condition is deliberate: **"qualified" must mean the AI actually *established* the facts**, not that one enthusiastic message scored well. Otherwise the sales team chases noise and stops trusting the score.

**Product detection reads the knowledge base**, not a hardcoded list — the product taxonomy *is* the company's own documents. That's what makes qualification work for any industry with zero configuration.

Budget extraction handles Indian formats: `₹5,00,000`, `12 lakh`, `8 LPA`, `2cr`, `50k`.

**3. Summarise** — a 3-line brief plus a recommended next step, so an agent picking up a handed-off conversation is productive in ten seconds.

**The handoff rule** — escalate when **either**:
- the customer asks for a human (regex, checked *before* retrieval — someone saying "just put me through" must not get a product FAQ), **or**
- confidence < the company's threshold (default 0.40)

Once `Status == "assigned"`, **the AI goes silent.** Qualification still runs so the score stays live, but the bot must not talk over a human agent. That's a trust property, not an optimisation.

### 2.9 Channel-agnostic by design

There is no `web_chats` table. Widget, WhatsApp, Instagram and voice all land in `leadai_conversations` + `leadai_messages`.

`chat.py:_handle_customer_turn()` is the channel-agnostic turn pipeline. **Adding WhatsApp later is an adapter** — translate Meta's webhook payload, call that function — not a second pipeline with its own qualification logic that will inevitably drift from the first.

---

## Part 3 — Data model

12 new tables, all prefixed `leadai_`. The tenant anchor is your **existing `Clients` table**.

```
Clients (EXISTING)  ← "a company"
   │
   ├── leadai_user_roles ......... email → role, scoped to a company (RBAC)
   ├── leadai_activity_logs ...... append-only audit trail
   ├── leadai_company_settings ... per-company AI overrides (1 row)
   ├── leadai_company_prompts .... editable prompt templates (5 keys)
   ├── leadai_company_scripts .... versioned XML scripts (your dialect)
   ├── leadai_kb_documents ....... uploaded sources
   │      └── leadai_kb_chunks ... chunk text + embedding vector
   ├── leadai_customers .......... PublicRef + Fernet-encrypted contacts
   └── leadai_conversations ...... one per conversation, any channel
          ├── leadai_messages .... turns (customer | ai | agent | system)
          ├── leadai_leads ....... qualification state (1:1)
          └── leadai_calls ....... LINK to CallSid in your existing tables
                                        │
                    ┌───────────────────┴────────────────────┐
                    │   EXISTING, UNCHANGED, authoritative   │
                    │  calllogs · callstatus · conversations │
                    │             · recordings               │
                    └────────────────────────────────────────┘
```

Tables are created automatically at startup (`integration.register()` → `db.ensure_tables()`), one at a time with `checkfirst=True`, mirroring `db.connect_db()`'s failure-tolerant strategy.

### Key enumerations

| Field | Values |
|---|---|
| `LeadUserRole.Role` | `Admin` `company_admin` `manager` `agent` `viewer` |
| `LeadConversation.Status` | `open` `needs_human` `assigned` `closed` |
| `LeadConversation.Channel` | `web` `whatsapp` `instagram` `voice` |
| `LeadMessage.Sender` | `customer` `ai` `agent` `system` |
| `Lead.Status` | `cold` `warm` `hot` `qualified` `lost` |
| `LeadCall.Provider` | `twilio` `exotel` `simulated` |
| `LeadCall.Mode` | `ai_voice` `agent` |
| `LeadCompanyScript.Channel` | `all` `chat` `voice` |
| `LeadCompanyPrompt.PromptKey` | `greeting` `sales` `qualification` `escalation` `voice` |
| `LeadActivityLog.LogType` | `Info` `Warning` `Error` `Security` |

---

## Part 4 — API Reference

### 4.0 Conventions

**Two auth surfaces:**

| Surface | Header | Applies to |
|---|---|---|
| **Staff** | `Authorization: Bearer <identity-server token>` | everything except `/public/*` |
| **Customer widget** | `X-Chat-Session: <token from /public/chat/start>` | `/public/chat/*` only |

Public (no auth): `/api/leadai/public/*`, `/api/leadai/voice/exotel/status`, `/api/leadai/health`. These are registered into `multiligual_call.PUBLIC_PATHS` at import time — **without editing the file** (the middleware reads that module attribute per request, so rebinding it extends the exemption list).

**Company scoping — read this once:**

- **Platform admins** must pass `?client_id={id}` on company-scoped endpoints. Omitting it returns `400 Pick a company first`.
- **Company-scoped users** (admin/manager/agent/viewer) should **omit** `client_id` — their own company is resolved from their grant. Passing another company's id returns `403`.

Every example below shows `?client_id=...` for clarity; drop it if you're a company-scoped user.

**Status codes:** `200` ok · `201` created · `400` bad scope · `401` no/expired token · `403` permission or wrong company · `404` not found *or* not yours · `409` conflict · `413` file too large · `422` validation/unparseable · `429` rate limited.

---

### 4.1 System & access control

#### `GET /health` · public
Which backing services are live vs in fallback. Use this to verify a deployment.
```json
{
  "status": "ok",
  "llm": "openai", "llm_model": "gpt-4o-mini",
  "embeddings": "openai", "embedding_model": "text-embedding-3-small",
  "vector_store": "mysql+numpy",
  "telephony": {"voice_provider": "exotel", "exotel_configured": true,
                "stt": "sarvam", "tts": "sarvam", "default_language": "en-IN"},
  "tables": {"cache": "redis", "retrieval_top_k": 5,
             "handoff_threshold": 0.4, "api_prefix": "/api/leadai"}
}
```

#### `GET /access/me` · any authenticated user
**The first call your frontend should make.** Drives menu visibility, button enablement and the company switcher.
```json
{
  "email": "asha@acmebank.com",
  "full_name": "Asha Agent",
  "role": "agent",
  "client_id": "9f2c...", "client_name": "Acme Bank",
  "permissions": ["analytics.read","call.initiate","call.read","company.read",
                  "kb.read","lead.read.assigned","lead.reply","lead.status","script.read"],
  "accessible_companies": [{"id":"9f2c...","name":"Acme Bank","is_active":true}]
}
```
> **Frontend tip:** gate UI on `permissions`, never on `role`. Roles may gain capabilities; permission names are the stable contract.

#### `GET /access/permissions`
Full catalogue + role matrix. Renders an accurate permissions screen without hardcoding the matrix.

#### `GET /access/roles` · `role.read`
Query: `for_company` (platform admin only). Company admins see only their own company's grants.

#### `POST /access/roles` · `role.manage` → `201`
```json
{"user_email": "asha@acmebank.com", "role": "agent",
 "full_name": "Asha Agent", "client_id": "9f2c..."}
```
Idempotent: re-granting an existing user a different role **updates** it rather than returning `409`. `Admin` is always global (`client_id` ignored). A company admin may only grant `manager`/`agent`/`viewer`.

#### `PATCH /access/roles/{grant_id}` · `role.manage`
`{"role": "manager", "full_name": "...", "is_active": false}` — all optional. Blocks self-demotion and self-deactivation.

#### `DELETE /access/roles/{grant_id}` · `role.manage`
Soft delete. Cannot revoke your own access.

#### `GET /access/assignable-users` · `lead.assign`
Users a conversation can be assigned to (active `agent`/`manager`/`company_admin` in this company).

---

### 4.2 Companies

#### `GET /companies` · `company.read`
Query: `include_inactive`. Company-scoped users see only companies they hold a grant in.
```json
[{"id":"9f2c...","name":"Acme Bank","email":"ops@acmebank.com",
  "phone_number":null,"description":"Retail banking","is_active":true,
  "created_at":"2026-07-30T09:12:00Z",
  "user_count":4,"document_count":6,"chunk_count":143,
  "script_count":2,"conversation_count":87}]
```

#### `POST /companies` · `company.manage` → `201`
```json
{"name": "Acme Bank", "email": "ops@acmebank.com",
 "description": "Retail banking",
 "admin_email": "admin@acmebank.com", "admin_name": "Acme Admin"}
```
Creates a row in your existing `Clients` table, **seeds the 5 prompt templates**, creates a settings row, and optionally grants the first `company_admin`. The new company is immediately usable by your batch features that reference `Batch.ClientId`.

#### `GET /companies/{id}` · `PATCH /companies/{id}` · `company.manage`
PATCH accepts `name`, `email`, `phone_number`, `description`, `is_active`.

#### `DELETE /companies/{id}` · `company.manage`
**Soft deactivate only.** Hard deletion is never exposed — conversations are business records and your batches may still reference the id.

#### `GET /companies/{id}/settings` · `company.read`
#### `PUT /companies/{id}/settings` · `settings.manage`
```json
{"handoff_threshold": 0.55, "retrieval_top_k": 6,
 "default_language": "hi-IN", "auto_assign_enabled": false,
 "auto_call_on_hot_lead": false, "widget_enabled": true,
 "widget_greeting": "Hi! Ask me anything about Acme home loans."}
```
Response echoes `effective_handoff_threshold` / `effective_retrieval_top_k` (company override or global default), so the UI can show what's actually in force.

> **Why per-company thresholds:** a bank wants early escalation; a furniture retailer would rather the bot keep trying. Recommended: `0.55–0.65` banking/insurance/health · `0.40` default · `0.25–0.35` retail/real estate.

---

### 4.3 Knowledge base

#### `GET /knowledge/documents` · `kb.read`
Query: `status` (`pending|indexing|indexed|failed|deleted`).

#### `POST /knowledge/documents` · `kb.manage` → `201`
`multipart/form-data`, field `file`. Query: `tags`. Accepts pdf · docx · txt · md · csv · json · xml · html. Max 15 MB (`LEADAI_MAX_UPLOAD_BYTES`).
```json
{"id":"3a1f...","title":"Home Loan Policy 2026.pdf","file_name":"Home Loan Policy 2026.pdf",
 "content_type":"application/pdf","source_type":"upload","status":"indexed",
 "status_message":null,"chunk_count":24,"char_count":18432,
 "embedding_model":"text-embedding-3-small","tags":"loans,policy",
 "created_at":"2026-07-30T09:20:11Z","created_by":"admin@acmebank.com"}
```
`422` if no readable text (scanned image without OCR, or too little content).

#### `POST /knowledge/faq` · `POST /knowledge/text` · `kb.manage` → `201`
```json
{"title": "Home Loan FAQ", "content": "Eligibility\nMinimum monthly income...", "tags": "loans"}
```
Use `\n\n` between sections — chunking is paragraph-aware and respects them.

#### `GET /knowledge/documents/{id}/chunks` · `kb.read`
Query: `limit` (≤500). **Shows exactly what the retriever sees.** Invaluable when a company insists the bot "doesn't know" something that's in their PDF — usually the text extracted badly, and this is where you find that out.
```json
{"document_id":"3a1f...","title":"Home Loan Policy 2026.pdf","total_chunks":24,
 "chunks":[{"id":"c1","position":0,"text":"Home Loan Eligibility\nAcme Bank home loans require...",
            "token_count":216,"embedding_model":"text-embedding-3-small","embedding_dim":1536}]}
```

#### `POST /knowledge/documents/{id}/reindex` · `kb.manage`
Re-chunks and re-embeds from retained `RawText`. Needed after switching embedding models — vectors from different models aren't comparable.

#### `DELETE /knowledge/documents/{id}` · `kb.manage`
Chunks hard-deleted (derived data that would otherwise keep answering queries); document row soft-deleted to preserve the trail.

#### `POST /knowledge/test` · `kb.test`
**The tenant-isolation proof.** Run the same query under two `client_id`s.
```json
{"query": "what is the home loan interest rate?", "top_k": 5}
```
```json
{"company":"Acme Bank","query":"what is the home loan interest rate?",
 "answer":"The home loan interest rate starts at 8.35 percent per annum for salaried applicants...",
 "confidence":1.0,"needs_human":false,"handoff_reason":null,
 "model":"gpt-4o-mini","latency_ms":812,
 "sources":[{"chunk_id":"c1","document_id":"3a1f...","score":0.94,
             "excerpt":"Interest Rates\nThe home loan interest rate starts at 8.35..."}]}
```
Same query against a company without that knowledge returns `confidence: 0.0`, `needs_human: true`, and an honest "I don't have that in our knowledge base" — **the safety property working as designed.**

#### `GET /knowledge/stats` · `kb.read`
```json
{"client_id":"9f2c...","documents":6,"chunks":143,
 "models":{"text-embedding-3-small":143},"needs_reindex":false,
 "backend":"mysql+numpy","embedding_backend":"openai",
 "embedding_model":"text-embedding-3-small"}
```
`needs_reindex: true` means mixed vector spaces — prompt the admin to re-index.

---

### 4.4 Scripts & prompts

#### `GET /scripts` · `script.read`
Query: `channel` (`all|chat|voice`), `include_inactive`.

#### `POST /scripts` · `script.manage` → `201`
```json
{"name": "Acme Home Loan Flow", "description": "Inbound home loan enquiries",
 "channel": "all", "language": "en-IN",
 "script_xml": "<?xml version=\"1.0\"?><script>...</script>",
 "is_default": true, "voice_gender": "female",
 "voice_speaker": "anushka", "multi_stt": false}
```
XML is parsed **eagerly** — an invalid script is rejected at authoring time, not at 3am when a call connects.

Minimal valid script:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<script>
  <section title="Identity" type="identity">
    <field name="name" value="Riya"/>
    <field name="company" value="Acme Bank"/>
    <field name="description" value="You help customers with home loan enquiries."/>
  </section>
  <section title="Objective" type="text">
    Qualify the customer's income and timeline, then offer a callback.
  </section>
</script>
```

#### `GET /scripts/active?channel=chat` · `script.read`
Resolves through the **same order the live conversation path uses** — "this is what your customers are talking to right now."

#### `GET /scripts/importable` · `POST /scripts/import` · `script.manage`
Lists / adopts your existing `scripts/*.xml`. `{"filename": "banking_sales_agent.xml"}`

#### `GET /scripts/{id}` · `PATCH /scripts/{id}` · `DELETE /scripts/{id}`
Detail includes `script_xml`, parsed `sections`, and `rendered_prompt`. Editing `script_xml` **bumps `version`**.

#### `POST /scripts/{id}/set-default` · `script.manage`
Makes it the default for its channel, clearing the previous one.

#### `POST /scripts/{id}/preview?channel=voice` · `script.read`
**The debugging endpoint.** Renders the exact fully-layered system prompt the model receives.
```json
{"script_id":"7b2e...","script_name":"Acme Home Loan Flow","channel":"voice",
 "system_prompt":"You are Acme Bank's voice agent on a live phone call...\n\n--- COMPANY SCRIPT: Acme Home Loan Flow ---\n...",
 "sections":[...],"character_count":1284}
```

#### `GET /prompts` · `PUT /prompts/{key}` · `POST /prompts/{key}/reset`
Keys: `greeting` `sales` `qualification` `escalation` `voice`. `{company}` is substituted at read time. `is_customised` tells the UI whether it differs from the default.

---

### 4.5 Public customer chat — lead generation

> **No `Authorization` header.** Uses `X-Chat-Session`.

#### `GET /public/companies` · public
Only name/description/greeting — never contact details, counts or settings.
```json
[{"id":"9f2c...","name":"Acme Bank","description":"Retail banking",
  "widget_greeting":"Hi! Ask me anything about Acme home loans."}]
```

#### `POST /public/chat/start` · public → `201`
```json
{"company": "9f2c...", "display_name": "Rahul Sharma",
 "phone": "+919876543210", "email": "rahul@example.com",
 "whatsapp": null, "instagram": null, "channel": "web", "language": "en-IN"}
```
`company` accepts the id **or** the company name. All contact fields optional — a lead with no contact is still a lead. Everything supplied is Fernet-encrypted before storage.
```json
{"session_token":"eyJhbGciOi...","conversation_id":"ea96...",
 "company":"Acme Bank",
 "greeting":"Hi! I'm the Acme Bank assistant. What can I help you with today?",
 "expires_in_minutes":1440}
```
Rate limited: 60 sessions/hour/IP.

#### `POST /public/chat/messages` · `X-Chat-Session` required
```json
{"message": "I earn 12 LPA and want to apply today for a 50 lakh home loan"}
```
```json
{"reply":"Acme Bank home loans require a minimum monthly income of 40,000 rupees and a CIBIL score above 720...",
 "confidence":0.47,"needs_human":false,"handed_off_to_human":false,
 "sources":[{"chunk_id":"c1","document_id":"3a1f...","score":0.61,"excerpt":"..."}],
 "lead_status":"qualified","lead_score":100}
```
Rate limited: 30 msg/min/conversation (`429`), enforced **before** any retrieval or LLM call.

**When a human has taken over** (`Status == assigned`), `reply` is `""` and `handed_off_to_human` is `true` — render "an advisor is with you" and keep polling history. The AI deliberately stays silent.

**Observed progression** from the test run:

| Turn | Confidence | Lead |
|---|---|---|
| "hi" | 1.00 | cold/18 |
| "interest rate and processing fee?" | 0.82 | cold/32 |
| "what documents do I need?" | 0.41 | hot/66 |
| "I earn 12 LPA, want to apply today, 50 lakh" | 0.47 | **qualified/100** |

#### `GET /public/chat/messages` · `X-Chat-Session`
Poll this (3–5s) so agent replies appear after a handoff. `system` rows filtered out; `confidence`/`sources`/`model_used` blanked — internal signals never reach the customer.

#### `POST /public/chat/end` · `X-Chat-Session`
Won't close a conversation a human is working — the agent still needs it queued.

---

### 4.6 Staff inbox

#### `GET /inbox` · `lead.read.all` or `lead.read.assigned`
Query: `status` · `lead_status` · `channel` · `assigned_to` (`me`|`unassigned`|email) · `search` · `min_score` · `sort` (`recent|score|oldest`) · `page` · `page_size`.

**Agents are automatically restricted to their own assigned conversations** — `assigned_to` is ignored for them.
```json
{"total_items": 87, "page": 1, "page_size": 25,
 "items": [{
   "id":"ea96...","client_id":"9f2c...","channel":"web","status":"needs_human",
   "customer_ref":"Customer #80022",
   "customer_name":null,
   "customer_phone_masked":"+919876*****210",
   "summary":"Customer is asking about Home Loan Eligibility. Budget/income signal: 12 LPA. Timeline: immediate...",
   "next_step":"Call now and close — the customer is ready to proceed.",
   "assigned_user_email":null,"handoff_reason":"Customer asked to speak to a human",
   "language":"en-IN","message_count":9,
   "last_message_at":"2026-07-30T09:41:02Z","created_at":"2026-07-30T09:33:18Z",
   "lead":{"status":"qualified","score":100,"interest":"Home Loan Eligibility",
           "intent":"ready_to_buy","budget":"12 LPA","timeline":"immediate",
           "product":"Home Loan Eligibility","sentiment":"neutral",
           "score_breakdown":{"base":8,"engagement":24,"intent":32,"timeline":22,
                              "budget_known":12,"product_known":8,"sentiment":0},
           "qualified_at":"2026-07-30T09:40:55Z"}}]}
```
> `customer_name` is `null` unless the viewer holds `lead.reveal_pii`. `customer_phone_masked` is **always** masked, even for admins — the full number requires the audited `/contact` call.

#### `GET /inbox/queue` · `lead.read.*`
The work queue: `needs_human` and unclaimed, **ordered by lead score** so the most valuable is worked first.

#### `GET /inbox/{id}` · `lead.read.*`
Adds `messages[]`, `calls[]`, and `suggestions[]` (AI coaching for the agent):
```json
{"suggestions": ["Call now and close — the customer is ready to proceed.",
                 "Send the application link before ending the conversation."]}
```

#### `POST /inbox/{id}/assign` · `lead.assign`
`{"user_email": "asha@acmebank.com"}` — or `{"user_email": null}` to unassign.

The assignee must hold an **active grant in this company** (`404` otherwise) — otherwise a lead could be parked with someone who can't open it. Unassigning returns status to `needs_human`, not `open`: the reason it needed a human hasn't gone away.

#### `POST /inbox/{id}/claim` · `lead.reply`
Self-service pickup. Agents can't *assign* but must be able to *take* unclaimed work, or the queue only moves when a manager is online. `409` if someone else already has it.

#### `POST /inbox/{id}/reply` · `lead.reply`
`{"message": "Hi Rahul, Asha here from Acme Bank..."}`

Writes into the **same conversation** the AI was using, so the customer sees one continuous thread. **Replying implicitly takes ownership** — otherwise two agents silently answer the same customer. Re-summarises so the handoff card stays current.

#### `POST /inbox/{id}/status` · `lead.status`
`{"status": "closed"}`

#### `GET /inbox/{id}/contact` · `lead.reveal_pii` 🔒
**The only endpoint that decrypts PII.** Writes a `Security` audit row naming the actor.
```json
{"phone":"+919876543210","email":"rahul@example.com",
 "whatsapp":"+919876543210","instagram":null,
 "revealed_at":"2026-07-30T10:02:44Z",
 "warning":"This reveal has been recorded in the activity log."}
```

#### `POST /inbox/{id}/requalify` · `lead.status`
Force re-scoring. Useful after a knowledge-base change — product detection reads the corpus, so a lead scored before an upload may now resolve its product correctly.

#### `GET /inbox/export/leads` · `lead.export` 🔒
Query: `lead_status`, `limit` (≤5000). Audited. **Excludes contact details by design.**

---

### 4.7 Voice — reusing your existing pipeline

#### `GET /voice/status` · `call.read`
```json
{"voice_provider":"exotel","exotel_configured":true,
 "stt":"sarvam","tts":"sarvam","default_language":"en-IN"}
```
Lets the UI show "Exotel connected / simulated" instead of failing a call for a reason the operator can't see.

#### `POST /voice/conversations/{id}/call` · `call.initiate` → `201`
```json
{"mode": "ai_voice", "script_id": null, "override_number": null}
```
- `mode`: `ai_voice` (bot answers, driven by the company's voice script) or `agent`
- `script_id`: omit to use the company's default voice script
- `override_number`: requires `lead.reveal_pii` (dialling an arbitrary number bypasses masking)

```json
{"id":"c88a...","conversation_id":"ea96...",
 "call_sid":"CAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
 "provider":"exotel","mode":"ai_voice","status":"in-progress",
 "handed_off":false,"duration_sec":0,"phone_masked":"+919876*****210",
 "language":"en-IN","script_id":"7b2e...",
 "initiated_by_email":"asha@acmebank.com","failure_reason":null,
 "created_at":"2026-07-30T10:05:00Z"}
```

> Returns `201` **even on failure**, with `status:"failed"` and `failure_reason` — the attempt is itself a record the UI needs in the call history. Check `status`, not the HTTP code.

Provider selection: Exotel if configured → falls back to your Twilio leg → falls back to `simulated` (synthetic CallSid) so the lead/qualify/handoff flow still demos offline.

#### `GET /voice/conversations/{id}/calls` · `call.read`

#### `POST /voice/calls/{id}/hangup` · `call.initiate`
Records the reason in your existing `globals.call_hangup_reasons` so your transcript annotation stays accurate.

#### `POST /voice/calls/{id}/sync` · `call.read`
Mirrors the transcript from your `conversations` table into the lead thread, then re-qualifies from the **combined chat + call** history. **Idempotent** — safe to call repeatedly.
```json
{"imported": 2, "total_messages": 18, "lead_status": "qualified",
 "lead_score": 100, "reason": null}
```

#### `POST /voice/calls/{id}/turn` · `call.initiate`
Runs **one turn of the voice brain over HTTP**. In production this loop lives inside your existing `/media-stream` handler driven by Sarvam STT frames; this endpoint exists so the full RAG → answer → handoff → qualification chain can be tested and demoed without a live carrier.
```json
{"utterance": "Yes, what is the processing fee on the home loan?"}
```
```json
{"reply":"Processing fee is 0.5 percent of the loan amount, capped at 10,000 rupees.",
 "confidence":0.71,"handed_off":false,
 "tts":{"provider":"sarvam","language":"en-IN","text":"Processing fee is 0.5 percent..."},
 "lead_status":"qualified","lead_score":100}
```
`utterance` stands in for what STT transcribed; `tts.text` is what would go to Sarvam TTS. Uses the `voice` prompt template and a tighter 220-token cap — a long answer is dead air on a call.

#### `POST /voice/exotel/status` · public webhook
Configure as your Exotel StatusCallback. Treated as **untrusted input**: only `CallSid` and status are read from the payload; company/conversation/lead are resolved from our own row, so a forged payload cannot re-parent a call or reach another tenant's data. Unknown Sid → `200` with no effect (never an error-retry loop). On a terminal status it **auto-syncs the transcript and re-scores the lead** — the sales team sees the outcome without pressing anything.

---

### 4.8 Analytics

#### `GET /analytics?days=7` · `analytics.read`
Agents get the same endpoint scoped to their own assigned conversations — one component serves both the manager dashboard and the agent's personal stats.
```json
{"client_id":"9f2c...","leads_today":12,"total_leads":87,
 "cold":31,"warm":24,"hot":18,"qualified":14,
 "assigned":29,"unassigned":58,"needs_human":11,"closed":22,
 "calls":34,"completed_calls":27,"failed_calls":4,"avg_call_duration":112.4,
 "conversion_rate":16.1,"avg_lead_score":43.7,"ai_containment_rate":74.7,
 "documents":6,"chunks":143,
 "daily":[{"date":"2026-07-24","leads":9,"hot":3,"calls":4}],
 "agents":[{"email":"asha@acmebank.com","name":"Asha Agent","role":"agent",
            "assigned":12,"closed":7,"qualified":4,"calls":9}],
 "channels":{"web":71,"voice":14,"whatsapp":2}}
```
> **`ai_containment_rate` is the metric that justifies the product**: the share of conversations the AI handled without ever needing a human. It's the inverse of the handoff rate, and it's what moves when the knowledge base improves. Track it as your primary KPI.

#### `GET /analytics/funnel` · `analytics.read`
Ordered stages, ready to render without the frontend knowing the ordering rule.

---

### 4.9 Activity / audit log

#### `GET /activity` · `activity.read`
Query: `action` · `action_prefix` (e.g. `lead.`) · `log_type` · `entity_type` · `entity_id` · `actor_email` · `since` · `until` · `page` · `page_size`.
```json
{"total_items":25,"page":1,"page_size":50,
 "items":[{"id":"a1...","client_id":"9f2c...",
   "actor_email":"admin@acmebank.com","actor_role":"company_admin",
   "action":"lead.pii_revealed","log_type":"Security",
   "entity_type":"customer","entity_id":"cu77...",
   "message":"admin@acmebank.com revealed contact details for Customer #80022 (conversation ea96...)",
   "meta":{"customer_ref":"Customer #80022","conversation_id":"ea96..."},
   "ip_address":"203.0.113.9","created_at":"2026-07-30T10:02:44Z"}]}
```
Note the `meta`: **no phone number.** Redaction is automatic.

#### `GET /activity/actions` · `activity.read`
Distinct action names present, for filter dropdowns.

**Action reference** (~45 total):

| Area | Actions |
|---|---|
| RBAC 🔒 | `role.granted` `role.revoked` `role.updated` |
| Company | `company.created` `company.updated` `company.deactivated` 🔒 `company.settings_updated` |
| Knowledge | `kb.document_uploaded` `kb.document_indexed` `kb.document_index_failed` `kb.document_deleted` 🔒 `kb.document_reindexed` `kb.retrieval_tested` |
| Scripts | `script.created` `script.updated` `script.deleted` 🔒 `script.set_default` `prompt.updated` `prompt.reset` |
| Chat | `chat.session_started` `chat.customer_message` `chat.ai_replied` `chat.ai_requested_human` ⚠ `chat.agent_replied` `chat.customer_ended` |
| Leads | `lead.qualified` `lead.assigned` `lead.unassigned` `lead.status_changed` `lead.requalified` `lead.exported` 🔒 `lead.pii_revealed` 🔒 |
| Voice | `call.initiated` `call.failed` ❗ `call.completed` `call.status_updated` `call.transcript_synced` |

🔒 = `Security` · ⚠ = `Warning` · ❗ = `Error`

---

### 4.10 WebSockets

| Endpoint | Purpose |
|---|---|
| `ws://.../ws/leadai/inbox/{client_id}` | new lead · handoff · assignment · call status |
| `ws://.../ws/leadai/conversation/{conversation_id}` | live message stream for an open thread |

Auth: `?token=<identity-server token>`. These reuse your existing `Websockets.connection.manager`, so LeadAI events travel over the same infrastructure as your batch/call monitors.

> **Note:** the WS channels are registered and authenticated, but broadcast *emission* is not yet wired into the turn handlers — poll `GET /inbox` / `GET /public/chat/messages` for now. Adding emission is a one-line `manager.broadcast(...)` call at each point marked in `chat.py` and `voice.py`.

---

## Part 5 — Frontend build guide

### 5.1 Screens to build, in dependency order

| # | Screen | Endpoints | Gate on |
|---|---|---|---|
| 1 | **Bootstrap / shell** | `GET /access/me` | — |
| 2 | Company switcher | `GET /companies` | `accessible_companies.length > 1` |
| 3 | Dashboard | `GET /analytics`, `/analytics/funnel` | `analytics.read` |
| 4 | Inbox list | `GET /inbox`, `/inbox/queue` | `lead.read.all` \|\| `lead.read.assigned` |
| 5 | Conversation detail | `GET /inbox/{id}` + assign/claim/reply/status/contact | `lead.reply` etc. |
| 6 | Knowledge base | `GET/POST/DELETE /knowledge/*`, `/knowledge/test` | `kb.read` / `kb.manage` |
| 7 | Scripts | `GET/POST/PATCH /scripts/*`, `/preview` | `script.read` / `script.manage` |
| 8 | Prompts | `GET /prompts`, `PUT /prompts/{key}` | `prompt.read` / `prompt.manage` |
| 9 | Team & roles | `GET/POST/PATCH/DELETE /access/roles` | `role.read` / `role.manage` |
| 10 | Company settings | `GET/PUT /companies/{id}/settings` | `settings.manage` |
| 11 | Activity log | `GET /activity`, `/activity/actions` | `activity.read` |
| 12 | **Chat widget** (separate app) | `POST /public/chat/start`, `/messages` | none |

### 5.2 Two HTTP clients

```ts
// Staff — identity-server token, plus client_id for platform admins
staffClient.interceptors.request.use(cfg => {
  cfg.headers.Authorization = `Bearer ${identityToken}`;
  if (isPlatformAdmin && selectedClientId) {
    cfg.params = { ...cfg.params, client_id: selectedClientId };
  }
  return cfg;
});

// Widget — session token only, NEVER the identity token
widgetClient.interceptors.request.use(cfg => {
  cfg.headers['X-Chat-Session'] = sessionStorage.getItem('leadai_chat');
  return cfg;
});
```

### 5.3 Permission-driven UI

```ts
const can = (p: string) => me.permissions.includes(p);

can('lead.reveal_pii')  // show the "Reveal contact" button
can('lead.assign')      // show the assignee dropdown
can('kb.manage')        // show upload / delete
can('script.manage')    // show the script editor (vs read-only)
can('role.manage')      // show the Team screen
can('activity.read')    // show the Activity nav item
```
Gate on `permissions`, never on `role`.

### 5.4 Conversation view state machine

```
status = "open"         → AI is handling. Show "AI active". Reply still allowed (takes ownership).
status = "needs_human"  → Banner: handoff_reason. Show [Claim] or [Assign].
status = "assigned"     → Show assigned_user_email. AI is silent. Reply box primary.
status = "closed"       → Read-only. Show [Reopen] → POST /status {"status":"open"}.
```

### 5.5 Widget polling

```ts
// After each send, and every 4s while a human is involved
const msgs = await widgetClient.get('/public/chat/messages');
// reply === "" && handed_off_to_human → render "An advisor is with you"
```

### 5.6 Rendering lead quality

`score_breakdown` is designed to be shown directly — a stacked bar or a list of contributions. Showing *why* a lead is hot is what makes a sales team trust the number.

```
Engagement    ████████         24
Intent        ██████████       32   (ready_to_buy)
Timeline      ███████          22   (immediate)
Budget known  ████             12   (12 LPA)
Product       ███               8   (Home Loan)
Sentiment     ─                 0   (neutral)
Base          ███               8
                            ─────
                              100   QUALIFIED
```

---

## Part 6 — Deployment

### 6.1 Install

```bash
pip install -r requirements.txt
pip install -r LeadAI/requirements-leadai.txt
```
Required additions: `cryptography` (PII), `pypdf` + `python-docx` (extraction). Optional: `redis`, `qdrant-client`.

### 6.2 Configure

Append `LeadAI/.env.leadai.example` to your existing `.env`. Minimum for a real deployment:

```bash
LEADAI_BOOTSTRAP_ADMINS=you@yourcompany.com   # remove after first login
OPENAI_API_KEY=sk-...
LEADAI_PII_KEY=<python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())">
```

> **Set `LEADAI_PII_KEY` explicitly.** Left blank, it's derived from `JWT_SECRET_KEY` — rotating that would make every stored contact undecryptable.

### 6.3 Start

```bash
python main.py     # or: uvicorn main:app --host 0.0.0.0 --port 5050
```

Tables are created automatically. Expected log line:
```
[LeadAI] tables ready (ok=12, skipped=0)
[LeadAI] public paths registered: /api/leadai/public, /api/leadai/voice/exotel/status, /api/leadai/health
[LeadAI] registered at /api/leadai | llm=openai embeddings=openai vectors=mysql voice=exotel
```

### 6.4 Verify

```bash
curl localhost:5050/api/leadai/health
curl -H "Authorization: Bearer $TOKEN" localhost:5050/api/leadai/access/me
```

### 6.5 First-run walkthrough

```bash
# 1. Create a company
POST /companies {"name":"Acme Bank","admin_email":"admin@acmebank.com"}
# 2. Add knowledge
POST /knowledge/faq?client_id=<id> {"title":"Home Loan FAQ","content":"..."}
# 3. Prove it works — and prove isolation
POST /knowledge/test?client_id=<id> {"query":"what is the interest rate?"}
# 4. Add a voice script (or import one)
POST /scripts/import?client_id=<id> {"filename":"banking_sales_agent.xml"}
# 5. Generate a lead
POST /public/chat/start {"company":"<id>","phone":"+91..."}
POST /public/chat/messages  (X-Chat-Session)  {"message":"..."}
# 6. Work it
GET  /inbox?client_id=<id>
POST /inbox/{cid}/assign?client_id=<id> {"user_email":"asha@acmebank.com"}
POST /voice/conversations/{cid}/call?client_id=<id> {"mode":"ai_voice"}
```

### 6.6 Exotel setup

1. Buy a DLT-registered ExoPhone → `EXOTEL_CALLER_ID`
2. API credentials from Exotel dashboard → `EXOTEL_SID`, `EXOTEL_API_KEY`, `EXOTEL_API_TOKEN`
3. In App Bazaar, build a flow containing the **Voicebot / Stream** applet pointing at `wss://{SERVER_URL}/media-stream` → flow id becomes `EXOTEL_FLOW_APP_ID`
4. Set StatusCallback to `{SERVER_URL}/api/leadai/voice/exotel/status`
5. `LEADAI_VOICE_PROVIDER=exotel`

Without step 3 calls still connect, but bridged agent↔customer with no bot.

### 6.7 Rollback

Comment out the `register_leadai(app)` block in `main.py` and restart. The `leadai_*` tables are inert when nothing reads them; no existing table is touched.

---

## Part 7 — Verification performed

An end-to-end functional test (`test_leadai.py`, run against SQLite with only your infrastructure modules stubbed — all LeadAI code real) exercised **48 assertions, all passing**:

| Area | Verified |
|---|---|
| Tables | 12 LeadAI tables created on the shared `Base` |
| RBAC bootstrap | empty-table lockout avoided; Admin granted with 23 permissions |
| **Tenant isolation** | identical query → Acme conf **1.0** answers · Globex conf **0.0** escalates |
| Knowledge base | FAQ indexed, chunked, embedded; stats + model tracking correct |
| Dynamic scripts | real `scripts/*.xml` imported (**18 sections**); per-company script created and resolved; prompt preview contains company persona |
| Lead pipeline | 4-turn chat progressed **cold/18 → cold/32 → hot/66 → qualified/100** with correct score breakdown |
| Handoff | "talk to a real person" → escalated, AI went silent |
| PII masking | agent saw `Customer #80022` + `+919876*****210`; raw number absent |
| RBAC enforcement | agent **403** on PII reveal, **403** on company create, **403** on another company; saw 1 of N conversations |
| Two-way thread | agent reply appeared in the customer's polled history |
| **Voice reuse** | `active_calls` populated with the company's script sections and all 9 expected keys — your `/media-stream` receives exactly what it expects |
| Transcript sync | 2 turns imported from the outbound `conversations` table, lead re-scored |
| Activity log | 25 rows / 12 distinct actions in one session; PII-reveal meta correctly **redacted** |
| Analytics | funnel, per-agent rollups, channel split, containment rate |

Every file compiles (`compileall` clean). Only `main.py` differs from your original tree.

---

## Part 8 — Known gaps

Honest list of what is deliberately not finished:

1. **WebSocket emission.** Channels are registered and authenticated; broadcast calls aren't yet placed in the turn handlers. Poll for now; adding emission is one line per event site.
2. **WhatsApp / Instagram adapters.** The data model and `_handle_customer_turn()` are channel-agnostic and ready, but Meta webhook handlers aren't written.
3. **Async indexing.** Large uploads index synchronously inside the request. A 15 MB PDF can take ~20–30s with OpenAI embeddings. Move `_index()` to a background task if that's a problem.
4. **OCR.** Scanned PDFs with no text layer return `422`. Add `pytesseract` to `ingest.py` if needed.
5. **LLM-based qualification.** `qualify()` is deterministic (regex + KB product detection) — fast, free, explainable. `llm.complete_json()` is wired and ready if you want a model-driven extractor; the `qualification` prompt key already exists.
6. **`auto_assign_enabled` / `auto_call_on_hot_lead`** settings are stored and returned but not yet acted on.
7. **Conversation dedup by `PhoneHash`.** The keyed fingerprint is stored so a returning customer *can* be recognised, but the lookup isn't wired into `/chat/start` yet.
