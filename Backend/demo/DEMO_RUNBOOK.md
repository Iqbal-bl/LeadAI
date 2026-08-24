# Demo Runbook — Nexa Finserv

Twenty minutes of setup, then a demonstration that shows chat, social, calling
and cross-channel memory as one system rather than four features.

---

## Setup

### 1. Environment

Generate the two secrets the config guard will otherwise refuse to boot on:

```bash
python -c "import secrets; print('JWT_SECRET_KEY=' + secrets.token_urlsafe(48))"
python -c "import secrets; print('SESSION_SECRET=' + secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print('LEADAI_PII_KEY=' + Fernet.generate_key().decode())"
```

Minimum viable `.env` for a demo:

```bash
ENVIRONMENT=development          # 'production' enforces every check in config_guard.py
PORT=6789

MYSQL_HOST=localhost
MYSQL_USER=chatuser
MYSQL_PASSWORD=<real password>
MYSQL_DATABASE=aichat_db

JWT_SECRET_KEY=<generated above>
SESSION_SECRET=<generated above>
LEADAI_PII_KEY=<generated above>
LEADAI_BOOTSTRAP_ADMINS=you@yourcompany.com

OPENAI_API_KEY=sk-...            # see the note below — this one matters
SERVER_URL=https://<your-ngrok>.ngrok-free.app
```

**`OPENAI_API_KEY` is not optional for a demo.** Without it LeadAI falls back to
offline embeddings and extractive answering. That path works — it is a genuinely
good piece of engineering — but it returns stitched sentences rather than
composed replies, and an audience reads that as the product being rough.

Verify before starting the app:

```bash
python config_guard.py
```

### 2. Boot and seed

```bash
python main.py

# in another shell
export LEADAI_TOKEN="<staff bearer token>"
python demo/seed_demo_company.py --base http://localhost:6789
```

The email inside that token must appear in `LEADAI_BOOTSTRAP_ADMINS`, or hold a
platform-admin grant already, or `company.manage` will 403.

Expected tail:

```
5. Live chat smoke test  (public widget endpoints, no auth)
...
  All turns answered from the knowledge base. Ready to demo.

  Company id for the widget embed and for /voice calls:
    <uuid>
```

If any turn escalated, fix it before the demo — see §4.

### 3. Have ready

- The company id from the seeder output.
- The staff inbox open in a browser tab, already authenticated.
- A phone you can answer, its number saved on the demo customer record.
- `/docs` open in a tab. Swagger is a surprisingly effective closer for a
  technical audience.

---

## The demonstration

### Act 1 — Chat that actually knows the company (3 min)

Open the widget. Ask, in this order:

1. *"I'm looking at a personal loan, I'm salaried"*
2. *"what interest rate can I get?"*
3. **"and the processing fee on that?"**

Turn 3 is the one to draw attention to. It contains no product noun. Before the
memory fix it retrieved nothing, scored ~0.1 confidence and escalated to a human
— on a question sitting in its own knowledge base. Now it resolves against the
prior turns and answers.

Then ask something deliberately outside the KB: *"what's your rate on a car
loan?"* The agent should say it does not have that confirmed and offer an
advisor. **Point this out explicitly.** An assistant that declines cleanly is the
whole value proposition; every buyer in the room has seen a bot invent a number.

### Act 2 — Qualification happening silently (2 min)

Continue: *"my take home is around 62,000 a month, I need about 6 lakh"*, then
*"how soon can I get the money?"*

Switch to the staff inbox. The conversation is there live, the lead score has
climbed, and the extracted fields — amount, employment, income, timeline — are
populated. Nobody typed those; they came from `ai_engine.qualify()` running on
every turn.

Show the masked phone number. Then reveal it, and show the audit entry the
reveal produced. Regulated buyers care about this more than they care about the
AI.

### Act 3 — The call that remembers (5 min)

From the inbox, place an outbound call to that conversation.

Answer the phone. **The agent will not introduce itself from scratch.** It opens
referring to the chat, and it does not re-ask the amount, the income or the
employment type — all of which the customer already gave in Act 1.

This is the moment the demo is built around. Explain what is underneath it:
`prepare_agent_context()` now takes the conversation and prepends a caller
briefing to the company script, so the voice agent boots with the chat history
already in its system prompt. Before, memory flowed one way — calls fed chat,
chat never fed calls.

Ask the agent something on the call that was answered in chat. It should stay
consistent, because both channels retrieve from the same knowledge base and run
the same qualification engine.

Hang up. Return to the inbox: the call transcript has mirrored into the same
thread. One conversation, two channels.

### Act 4 — Social, same engine (3 min)

If Meta channels are connected, message the connected WhatsApp or Instagram
account from the same customer identity.

`resolve_social_conversation()` de-duplicates on the phone fingerprint, so a
returning customer resumes rather than forking. The cross-channel digest means
the agent opens knowing about the loan enquiry even though this is a new thread
on a new channel.

If Meta review is still pending, say so and show `/docs` under
**LeadAI • Channels** instead. The web widget is the supported fallback and the
pipeline is identical — `handle_customer_turn()` is one function with four
adapters, which is worth saying out loud.

### Act 5 — Multi-tenancy (2 min)

Create a second company through `/companies`, upload a two-line knowledge base,
and ask the first company's widget a question only the second company's KB
answers. It will decline.

Tenancy is enforced in one place — `rbac.resolve_scope()` — and routers never
read `ClientId` from a request body for a company-scoped user. Cross-company
leakage is structurally difficult rather than merely absent. For anyone
evaluating this as a platform rather than an app, this is the slide that matters.

---

## Editing the demo company

**Change a product fact** → edit `demo/nexa_finserv_knowledge_base.md`, re-upload
via `POST /api/leadai/knowledge/documents`. No restart, no deploy.

**Change persona, tone or flow** → edit
`demo/nexa_finserv_agent_script.xml`, `POST /api/leadai/scripts` (it versions
rather than overwrites), then `POST /api/leadai/scripts/{id}/set-default`.

**Change per-channel behaviour** → edit `demo/nexa_finserv_prompts.json`,
`PUT /api/leadai/prompts/{key}`.

**Never put a rate in the script.** Product facts live in the KB. Two sources of
truth drift, and the first person to edit one without the other puts a stale
number into a live call.

### Building a second demo company

The four files are a template. Keep the script's structure and swap the domain —
it exercises every section type `xml_parser` supports, so it is the reference
implementation. The knowledge base should keep its final "Boundaries for the AI
Assistant" section; that is what produces clean refusals rather than
hallucinations, and clean refusals are what close deals.

---

## Troubleshooting

**Every turn escalates to a human.** The knowledge base did not index. Check
`GET /api/leadai/knowledge/stats` for a non-zero chunk count, then
`POST /api/leadai/knowledge/test` to see what retrieval actually returns for a
given question.

**Replies are stilted and read like stitched-together sentences.**
`OPENAI_API_KEY` is unset — you are on the offline extractive path.

**The agent ignores the script's persona.**
`POST /api/leadai/scripts/{id}/preview?channel=chat` renders the exact system
prompt the model receives. If the script's sections are not in it, it is not the
default for that channel. This is the single most useful debugging endpoint in
the system.

**The call connects and then goes silent.** The carrier cannot reach your
webhooks. `SERVER_URL` / `NGROKURL` must be the current public tunnel — ngrok
URLs change on restart unless reserved.

**The call agent introduces itself from scratch despite prior chat.** The
conversation had no prior messages, or `voice_briefing()` returned empty. Look
for `[LeadAI call] agent context includes N memory section(s)` in the logs; the
absence of that line is the diagnostic.

**403 on every staff endpoint.** The token's email is not in
`LEADAI_BOOTSTRAP_ADMINS` and has no role grant. Check with
`GET /api/leadai/access/me`.
