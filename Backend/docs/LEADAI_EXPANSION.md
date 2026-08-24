# LeadAI Expansion — Architecture, Concepts, and Frontend Guide

This document covers three things, in order:

1. **The architecture question** — should this become microservices? (Short answer: no, and here is what to do instead.)
2. **The concepts** — what WhatsApp session windows, HMAC signatures, idempotency, durable queues and consent gating actually mean, and why the code is shaped the way it is.
3. **The frontend work** — every new endpoint, what screen it belongs to, and the exact flow each screen must follow.

---

# Part 1 — Should this be microservices?

## The recommendation: stay monolithic. Extract seams instead.

I want to be direct about this, because "should we go microservices" is usually asked when the real question is "how do I stop this getting slow", and those have different answers.

Splitting this system into services would make it **slower and less reliable**, not faster. Here is the specific reason, not a general one.

### Why microservices would hurt *this* codebase

**The database transaction is the thing holding you together.** Right now, when a customer sends a message, one transaction writes the message, the AI reply, the updated lead score, the threshold flag, and the activity log. Either all of it lands or none of it does. Split the lead engine from the campaign engine from the CRM and that single transaction becomes four network calls that can each fail independently. You would then need to build compensating transactions — code that undoes step two when step three fails — and that code is where distributed systems go to die. You would be writing a distributed-transaction framework instead of features.

**Your slow parts are not CPU-bound, they are wait-bound.** The expensive operations here are: waiting on the LLM, waiting on the telephony provider, waiting on Meta's Graph API. Those are I/O waits. Putting a network hop *in front of* a network wait does not make the wait shorter. It makes it longer and adds a new failure mode.

**You have one team and one deploy.** Microservices buy you independent deployment by independent teams. If one team deploys everything, you pay the entire operational cost — service discovery, distributed tracing, versioned contracts between services, N sets of dashboards — and receive none of the benefit.

### What actually makes a monolith slow, and what I did about it

A monolith gets slow for four reasons. Each has a fix that does not require splitting the process, and all four fixes are implemented:

**1. Long work happens inside the web request.**
This is the big one. If sending 5,000 WhatsApp messages happens inside the HTTP request that started the campaign, that worker thread is gone for twenty minutes, and your voice calls — which are latency-critical — start queueing behind it.

*Fix:* a durable job queue (`LeadAI/services/jobs.py`) backed by a database table. The API writes a job row and returns in milliseconds. A separate worker process drains it. The API container and the worker container run the *same image*; they differ by one environment variable, `LEADAI_WORKER_ENABLED`. This gives you the main operational benefit people want from microservices — background work cannot starve request handling — at roughly 300 lines instead of a service mesh.

**2. Shared logic gets copy-pasted, then the copies drift.**
Web chat, WhatsApp, Messenger and Instagram all do the same thing: take a customer message, answer it with RAG, score the lead, decide about handoff. Four copies means a bug fixed in one lives on in three.

*Fix:* `LeadAI/services/conversation_flow.py` holds exactly one implementation, `handle_customer_turn()`. Every channel calls it. The web chat route in `chat.py` was rewritten to delegate — it shrank from 604 lines to 405 — and **its HTTP contract did not change**, so the existing widget needs no edits.

**3. Counting things by scanning them.**
"How many recipients did this campaign deliver to?" is a `COUNT(*)` over a table that grows forever. Render a dashboard with ten campaigns and you have ten full scans.

*Fix:* counters are denormalised onto the campaign row and updated as batches complete. Likewise `Lead.IsAboveThreshold` is a stored, indexed boolean rather than a score compared against a settings lookup on every row.

**4. Queries that ignore the tenant index.**
Every new query is a chance to write one that scans all tenants' rows.

*Fix:* every new table is indexed on `ClientId` plus whatever it is filtered by, and every new query filters on `ClientId` first.

### When you *should* revisit this

Concretely, not vaguely. Split something out when:

- **The voice pipeline needs different hardware.** It is the one genuinely different workload — long-lived WebSockets, audio buffers, latency budgets in milliseconds. If you start scaling the whole app just to give voice more headroom, extract voice first. It is already the most self-contained part.
- **One tenant's campaign volume affects another tenant's response times**, and you have already added worker replicas. Then shard workers by tenant.
- **You have more than one team** that cannot ship without waiting for each other.

Until one of those is true and measurable, splitting costs you velocity and buys latency.

---

# Part 2 — Concepts

These are the ideas the new code assumes. If a design choice looks strange, the reason is probably here.

## 2.1 The 24-hour session window (the single most important rule)

Meta does not let you message people whenever you like. For WhatsApp, Messenger and Instagram:

- When a user messages **you**, a **24-hour window** opens.
- Inside that window you may reply with **anything** — free-form text, generated by the AI.
- Outside that window you may send **only a pre-approved template**, submitted to Meta in advance and reviewed by a human at Meta over hours or days.

This is not a guideline that can be worked around. Outside the window, the API rejects the send.

**Consequences that shape the whole design:**

- Your festive-wishes and cold-outreach campaigns will *almost always* be outside the window, because those people have not messaged you recently. **They need templates.** This is why campaign creation asks for a template name, and why `/campaigns/{id}/preview` warns you loudly when the template is missing. The warning is not pedantry — without it you would launch a 5,000-recipient Diwali campaign and get 5,000 failures.
- `LeadChannelIdentity.LastUserMessageAt` exists solely to track this. `within_session_window()` in `channels.py` computes it.
- The channel contacts screen shows an `in_session_window` badge so a human agent knows whether they can type freely or must pick a template.

Template variables are positional (`{{1}}`, `{{2}}`), not named — Meta's format, not ours.

## 2.2 Webhook signature verification

Your webhook endpoint is a public URL. Anyone who finds it can POST to it. Without verification, a stranger could inject fake inbound messages, which would create fake leads and make your AI generate replies you pay for.

Meta signs every request with `X-Hub-Signature-256`: an HMAC-SHA256 of the **raw request body**, keyed with your app secret.

Two details that are easy to get wrong and are handled in `webhooks.py`:

- **The raw bytes must be hashed, not the parsed JSON.** Re-serialising JSON changes whitespace and key order, and the signature will never match. The route reads `await request.body()` before parsing.
- **The comparison must be constant-time** (`hmac.compare_digest`). A normal `==` returns faster on an early-mismatching string, which leaks the correct signature one byte at a time to a patient attacker.

## 2.3 Idempotency — why the same message arrives twice

Meta retries a webhook if you do not answer `200` quickly, and it will keep retrying for days. Network hiccups mean you *will* receive the same message more than once. Without protection the customer says "hi" once and your AI replies three times.

Two defences:

1. `LeadChannelEvent.ExternalMessageId` has a **unique constraint**. A duplicate insert fails at the database, which is the only layer that can arbitrate reliably between concurrent workers.
2. The endpoint **always returns 200 fast** and does the real work in a background task. If processing throws, Meta must not be told to retry — a poison message would otherwise be redelivered forever.

The only case that returns non-200 is a signature mismatch (`403`), because that request was not from Meta at all.

## 2.4 Tenant routing — never trust the payload

A webhook payload contains a phone number id or page id. It does **not** contain your `client_id`, and it must not be allowed to imply one.

Routing works the other way: the received `ExternalId` is looked up in `leadai_channel_accounts` to find which company owns that number. If no account matches, the event is dropped. This means a forged payload cannot address a tenant it does not belong to, because the mapping lives in your database, not in the attacker's request body.

## 2.5 The durable job queue

Three approaches, and why the third won:

- **`asyncio.create_task`** — dies on deploy, taking the campaign with it, and cannot be shared across `--workers 4`.
- **Celery/RQ** — works, but adds a broker, a result backend, another deployment, and another thing to monitor.
- **A database table** (chosen) — you already have MySQL, it is already backed up, and jobs commit *in the same transaction* as the entity that created them. A campaign cannot exist without its job, or vice versa.

Claiming is the interesting part. Multiple workers must not run the same job. The claim is one atomic conditional update:

```sql
UPDATE leadai_jobs SET Status='running', WorkerId=?
 WHERE Id=? AND Status='queued'
```

Exactly one worker gets `rowcount == 1`; the rest get `0` and move on. The database's row lock does the coordination, so no distributed lock is needed.

Failures retry with exponential backoff (30s, 60s, 120s). Jobs held by a process that died are reclaimed after 15 minutes by `reclaim_stale()`, which runs on startup — so a deploy mid-campaign resumes rather than stalling.

## 2.6 Batch-and-requeue

A campaign job does not process 50,000 recipients in one run. It processes `LEADAI_CAMPAIGN_BATCH_SIZE` (default 200), then **enqueues itself again**.

This gives you, for free:

- **Pause takes effect quickly** — the next batch checks status before starting, rather than needing to interrupt a running loop.
- **Deploys are safe** — at most one batch is lost, and it is retried.
- **Rate limits are naturally respected**, because the sleep between batches is the throttle.
- **No single transaction is enormous**, so no long-held locks.

## 2.7 Quiet hours

India's TRAI regulations prohibit promotional contact outside **09:00–21:00** local time. Violations risk the sender's number, not just a fine.

`quiet_hours_check()` runs before every batch. If it is currently quiet hours, the job re-queues itself for the next permitted moment instead of sending. **Transactional** messages bypass this — an OTP or an appointment confirmation is not marketing. That distinction is the `Purpose` field on the campaign, which is why choosing it at creation time matters.

## 2.8 Consent gating

Every `LeadAccount` carries independent flags: `OptInWhatsApp`, `OptInSms`, `OptInEmail`, `OptInCall`, plus a global `DoNotDisturb`.

Every outbound send — campaign or one-off — passes through `crm.can_contact(account, channel)`. There is deliberately **one** gate rather than a check at each call site, because a consent check that exists in nine places will eventually be missing from the tenth.

The default is **restrictive**: unknown state means do not send.

Opt-out is handled before the AI ever sees the message. `webhooks.py` checks for STOP/UNSUBSCRIBE keywords first, records the opt-out on both the channel identity and the CRM record, and stops. If the AI processed "STOP" normally it would cheerfully reply — which is exactly the behaviour that gets a WhatsApp sender banned.

## 2.9 PII: encrypted, masked, and revealed under audit

Three layers, already the convention in this codebase and extended to all new tables:

- **Encrypted at rest** (`PhoneEnc`, `EmailEnc`) with Fernet. A database dump alone reveals nothing.
- **Masked for display** (`PhoneMasked` = `+91••••3210`), computed once at write time and stored. This is why listing 200 customers costs **zero** decryption operations — the list screens never decrypt anything.
- **Revealed explicitly** via `POST /customers/{id}/reveal`, which needs a separate permission and writes a `Security`-category activity log entry every single time.

Search works without decryption via `PhoneFingerprint` — a keyed hash. Equal phone numbers produce equal fingerprints, so lookup and dedupe work, but the fingerprint cannot be reversed into a number.

## 2.10 Why campaign templates are not Jinja

`render_template()` does a literal `{{key}}` string replacement. That looks primitive next to Jinja2, and it is deliberate.

Campaign bodies are written by users. Jinja is a **programming language** — `{{ ''.__class__.__mro__ }}` is the beginning of a well-known sandbox escape that ends in remote code execution. A message template needs to substitute a first name, not evaluate expressions. Anything more powerful is attack surface with no corresponding benefit.

## 2.11 Additive-only migrations

`ensure_columns()` in `db.py` adds missing columns on boot. It **only** issues `ADD COLUMN` — never `DROP`, `MODIFY` or `RENAME` — and only touches `leadai_`-prefixed tables.

This is what makes the release safe to deploy onto an existing database: `create_all()` creates missing *tables* but will never alter an existing one, so without this the new columns would silently never appear. Each statement runs independently, so one failure does not block the rest. Disable with `LEADAI_AUTO_MIGRATE=false` if you manage schema with Alembic.

---

# Part 3 — Frontend Guide

## 3.0 What did *not* change

**The chat widget needs no changes at all.** `POST /api/leadai/chat/messages` (and `/chat/start`, `/chat/end`) have the same request and response shapes they always had. Its internals now delegate to the shared pipeline, but the contract is untouched. Existing embeds keep working.

Everything below is **additive**.

## 3.1 New screens at a glance

| Screen | Endpoints | Who can see it |
|---|---|---|
| Channel Connections | `/channels/*` | `channel.read` / `channel.manage` |
| Contact Lists | `/lists/*` | `campaign.manage` |
| Campaigns | `/campaigns/*` | `campaign.read` / `manage` / `send` |
| Customers (CRM) | `/customers/*` | `customer.read` / `manage` |
| Documents | `/files/*` | `file.read` / `file.manage` |
| Lead Threshold settings | `/threshold` | `settings.manage` |

All paths are relative to `/api/leadai`. Authentication and the company scope header work exactly as they do for existing LeadAI endpoints.

Note `campaign.send` is a **separate** permission from `campaign.manage`. A manager may build and preview a campaign; pressing "Start" — which spends money and touches thousands of customers — can be restricted to a smaller group. Reflect this in the UI: show the Start button disabled with a tooltip rather than hiding it, so users understand the boundary.

## 3.2 Channel Connections screen

This is the screen that turns WhatsApp/Facebook/Instagram on. The flow is a three-step wizard, and the order matters — the user cannot complete step 2 in Meta's dashboard until step 1 has generated the values to paste.

**Step 1 — Create the account record.**

`POST /channels` with channel (`whatsapp` | `messenger` | `instagram`), a display name, the `external_id` (WhatsApp Phone Number ID, Facebook Page ID, or Instagram Account ID), the access token, and optionally an app secret.

The response includes **`webhook_url`** and **`verify_token`**. Display both with copy buttons — they are the entire point of this step.

`external_id` is globally unique. A `409` means that number is already connected, possibly to a different company; say so plainly rather than showing a generic error.

**Step 2 — Paste into Meta.**

Show static instructions: Meta App Dashboard → Webhooks → Edit Subscription → paste the URL and token → subscribe to the `messages` and `messaging_postbacks` fields. Meta immediately calls the endpoint with a `GET` handshake; if the token matches, it turns green on their side.

**Step 3 — Verify.**

`POST /channels/{id}/test` sends a real message to a number the user supplies. This is the only proof the token is valid *and* has the right permissions. Do not let the user leave the wizard on an untested account — a channel that looks connected but silently fails is worse than one that is obviously not set up.

**Ongoing screen contents:**

- `GET /channels` — the list. `has_access_token` is a boolean; the token is **never** returned. Do not build an "edit token" field that shows the current value, because there is nothing to show.
- `GET /channels/status` — a dashboard summary tile.
- `GET /channels/{id}/contacts` — people who have messaged this channel, with masked identifiers and an `in_session_window` flag. Render that flag as a clear badge ("Can reply freely" / "Template required") — agents need it before they start typing.
- `PATCH` to toggle `auto_reply`. Turning it off makes the channel human-only: messages still arrive in the inbox, the AI stays silent. Some businesses want this for Instagram while keeping the AI on WhatsApp.

## 3.3 Contact Lists screen

**Upload with a preview step.** This is the most important UX detail in the whole feature. Never parse-and-save in one action.

1. `POST /lists/preview` (multipart, file only) — parses and returns nothing saved: total rows, valid, invalid, duplicates, the detected `column_map`, and a masked sample.
2. Show the user what was found: *"1,204 rows. 1,180 valid. 18 duplicates removed. 6 invalid numbers. We read 'Mobile No.' as the phone column."*
3. Let them correct the column mapping if the guess was wrong.
4. `POST /lists` with the confirmed mapping to save.

Column detection is fuzzy and handles the things real spreadsheets do — `Mobile No.`, `contact number`, `whatsapp`, title rows above the header, Excel turning `9876543210` into `9.87654321e+09`, leading trunk zeros. It gets it right most of the time, and the preview is how the user catches the rest. Unmapped columns are preserved and become message variables.

Accepted formats: `.xlsx`, `.xls`, `.csv`, `.tsv`, `.docx`.

`POST /lists/from-leads` builds a list from existing leads by filter. Be explicit in the UI that this is a **snapshot**, not a live segment — leads created tomorrow will not appear in it.

`DELETE /lists/{id}` returns `409` if a campaign uses the list. Show which campaign.

## 3.4 Campaigns screen

The lifecycle is deliberately multi-step. Sending to thousands of people should not be one click.

```
create (draft) → build audience → preview → start → [pause ⇄ resume] → done
                                                   ↘ cancel
```

**Create** — `POST /campaigns`. Collect:
- **Kind**: `message` or `call`. Calls reuse the existing dialling pipeline.
- **Channel**: whatsapp / messenger / instagram / sms / email / voice.
- **Purpose**: promotional / festive / cold_outreach / follow_up / reactivation / **transactional**. This is not cosmetic — only `transactional` bypasses quiet hours. Explain that in the picker.
- **Audience**: a contact list, a leads filter, or a customers filter. (Bulk outreach to customers is a campaign with `audience_type=customers` — there is intentionally no separate bulk-send button on the CRM screen.)
- **Body / template**, with `{{variable}}` placeholders. Show the available variable names from the chosen list's columns.
- Optional schedule, concurrency, rate limit.

Validation is strict at create time, so failures surface before any recipients exist.

**Build** — `POST /campaigns/{id}/build`. Materialises recipient rows and deduplicates. Show a progress state; large lists take a moment. Idempotent — safe to retry.

**Preview** — `GET /campaigns/{id}/preview`. Returns recipient count, rendered sample messages **with real data substituted**, an ETA, and warnings. Render the warnings prominently:
- *Template missing but most recipients are outside the 24-hour window* → these will fail.
- *Quiet hours* → sending will pause until 09:00.
- *Recipients with no consent for this channel* → they will be skipped.

**Start** — `POST /campaigns/{id}/start`. Returns immediately; the work is queued. Requires `campaign.send`.

**Monitor** — poll `GET /campaigns/{id}` for counters (`total`, `sent`, `delivered`, `failed`, `replied`). They are denormalised, so polling every few seconds is cheap. `GET /campaigns/{id}/recipients?status=failed` lists failures with reasons.

**Control** — `pause`, `resume`, `cancel`, and `retry-failed`. `retry-failed` resets only permanently-failed recipients; make clear it will not re-send to people who already received the message.

## 3.5 Customers (CRM) screen

`GET /customers` with filters for stage, status, owner, tag, search, `follow_up_due`. Employees automatically see only the customers they own — that filtering is server-side, so the UI does not need to implement it, but should not offer an "all customers" toggle to users who lack `customer.read.all`.

**Consent must be visible.** Show the four opt-in flags and `DoNotDisturb` as clear indicators on the detail view, and grey out the corresponding send buttons when consent is absent. If a user can press a button that will always be rejected, they will assume the system is broken.

`POST /customers/convert` promotes a lead to a customer. Surface this as a button on qualified leads in the existing lead views — it is idempotent, so a double-click is harmless.

`POST /customers/{id}/message` sends one-off outreach; still consent-checked.

`POST /customers/{id}/reveal` shows the real phone or email. Treat this as a deliberate action — a click-to-reveal control, not automatic on page load. Every reveal is audit-logged with the user's identity.

`GET /customers/greetings/upcoming` powers a **festive wishes** widget: whose birthday or anniversary falls in the next N days. The natural flow is: see the list → select → create a festive campaign for them.

## 3.6 Lead Threshold screen

Small screen, real consequences.

`GET /threshold` returns the current setting plus counts of leads above and below it — so the user can see the impact before changing anything.

`PUT /threshold` sets:
- `lead_score_threshold` — the bar for "worth a human's attention".
- `hide_below_threshold` — whether the inbox default view hides everything below.
- `notify_on_threshold` — whether crossing fires a real-time notification.
- `auto_convert_threshold` — score at which a lead becomes a customer automatically. Leave blank to keep humans in the loop; recommend that default.

Saving recomputes every lead's flag immediately, so **lowering the threshold surfaces older leads right away**. Tell the user that in the confirmation — it is surprising otherwise, and pleasantly so.

Show a preview before saving: *"This will move 47 leads onto the dashboard."*

## 3.7 Documents screen

`POST /files` (multipart) with an optional `purpose` and entity link. `GET /files` lists metadata. Files live in MinIO; MySQL stores only metadata.

**Downloads are presigned, expiring URLs** — buckets are private, and a link stops working after ~15 minutes. Do not cache these URLs in frontend state and reuse them later; request a fresh one each time. `GET /files/{id}/download` also streams through the API if a direct link is awkward.

`DELETE /files/{id}` soft-deletes; `?hard=true` purges the object for genuine data-deletion requests.

## 3.8 Real-time: the threshold-crossed event

No new WebSocket connection is needed. The existing inbox socket — `/ws/leadai/inbox/{client_id}` — now also carries:

```json
{
  "type": "lead_threshold_crossed",
  "lead_id": "...",
  "conversation_id": "...",
  "score": 78,
  "threshold": 50,
  "status": "qualified",
  "channel": "whatsapp",
  "client_id": "..."
}
```

It fires **exactly once** per lead — `ThresholdNotifiedAt` guarantees a lead that oscillates around the boundary does not spam the sales floor. Handle it as a toast plus a badge increment. Ignore unknown `type` values so future events do not break the client.

## 3.9 Inbox changes

`GET /inbox` (the conversation list) gained:

- `channel` now accepts `messenger`, `sms` and `email` in addition to `web`, `whatsapp`, `instagram`, `voice`. **If your frontend has a hardcoded channel dropdown, it needs updating** — this is the one existing screen that requires an edit.
- `above_threshold` (bool) — filter by the threshold. Omit it and the company's `hide_below_threshold` setting decides the default view. Give the user a visible "Show all leads" escape hatch, otherwise hidden leads are indistinguishable from missing ones.
- `campaign_id` — show only conversations a given campaign produced. This is how campaign replies get attributed.

Channel badges should be added to conversation rows; a WhatsApp conversation and a web chat now look identical without one.

---

# Part 4 — Deployment checklist

1. `pip install -r requirements.txt` — one new package, `python-docx`.
2. Copy the variables you need from `.env.leadai.example`. **Every one has a working default**; with none of them set, the app boots and behaves exactly as before.
3. `docker compose up -d` — brings up the new `leadai-worker` container and `minio-init`, which creates the buckets.
4. On first boot the app adds the new tables and columns automatically. Watch the startup log line for confirmation.
5. Set `LEADAI_CAMPAIGN_DRY_RUN=true` and run one campaign end to end. It renders every message and records every outcome without sending anything. Turn it off once the output looks right.
6. Connect one channel, run the `test` endpoint, and send yourself a message before letting real traffic in.

## Rollback

Every feature is off by default or degrades quietly:

- `LEADAI_SOCIAL_CHANNELS_ENABLED=false` — back to web chat only.
- `LEADAI_WORKER_ENABLED=false` — campaigns stop being processed; queued jobs wait, they are not lost.
- MinIO unconfigured — file storage falls back to local disk.

The schema changes are additive only, so the previous release runs against the new schema unchanged. That is the actual rollback path: redeploy the old image, leave the database alone.
