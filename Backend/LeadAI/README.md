# LeadAI — integrated into AIOutbound

Multi-tenant AI lead generation added to your outbound voice app.
**Full documentation: [`API_DOCUMENTATION.md`](./API_DOCUMENTATION.md)**

---

## What changed in your code

**One file: `main.py`** — a 22-line block at the bottom that calls
`LeadAI.integration.register(app)`, wrapped in try/except so a LeadAI failure
can never take your voice agent down.

Everything else (`multiligual_call.py`, `voice_agent.py`, `batching.py`, `db.py`,
`database.py`, `auth.py`, `Domain/models.py`, `xml_parser.py`, `bot/`,
`Repositories/`, `scripts/`) is **byte-identical**.

## What you got

| Feature | Endpoint root |
|---|---|
| Per-company knowledge bases (RAG) | `/api/leadai/knowledge` |
| Per-company dynamic scripts (your XML dialect) | `/api/leadai/scripts` |
| Customer chat widget → lead generation | `/api/leadai/public` |
| Staff inbox with RBAC + PII masking | `/api/leadai/inbox` |
| Outbound lead calls (reuses your pipeline) | `/api/leadai/voice` |
| Companies + AI settings | `/api/leadai/companies` |
| Roles & permissions | `/api/leadai/access` |
| Dashboards | `/api/leadai/analytics` |
| Audit log | `/api/leadai/activity` |

## Stack

- **OpenAI** — `text-embedding-3-small` embeddings + `gpt-4o-mini` generation
- **Hybrid RAG** — dense cosine + IDF-weighted lexical coverage, sentence reranking,
  retrieval-derived confidence driving human handoff. Tenant-partitioned inside the
  vector store, so cross-company leakage is impossible by construction.
- **Exotel** — telephony (falls back to your working Twilio leg)
- **Sarvam** — STT/TTS, via your existing `sarvam_stt.py` / `sarvam_tts.py`
- **MySQL** — same engine, same pool, same `Base`. 12 new `leadai_*` tables.
- Optional: **Qdrant** (`QDRANT_URL`), **Redis** (`REDIS_URL`)

Everything degrades gracefully: no OpenAI key → offline embeddings + extractive
answering; no Exotel → Twilio; no Redis → in-process cache.

## Install

```bash
pip install -r requirements.txt
pip install -r LeadAI/requirements-leadai.txt

# append LeadAI/.env.leadai.example to your .env, then set at minimum:
#   LEADAI_BOOTSTRAP_ADMINS=you@yourcompany.com
#   OPENAI_API_KEY=sk-...
#   LEADAI_PII_KEY=<Fernet key>

python main.py
```

Tables create themselves. Verify:

```bash
curl localhost:5050/api/leadai/health
curl -H "Authorization: Bearer $TOKEN" localhost:5050/api/leadai/access/me
```

Swagger: `/docs` — LeadAI routes appear under the `LeadAI • …` tags.

## The two things worth understanding

**1. Voice reuses your pipeline, it doesn't replace it.**
`LeadAI/services/call_bridge.py` writes a company's script into your existing
`active_calls[call_sid]` registry before the carrier hits `/outbound-twiml`. Your
`/media-stream` → `SimpleAgent` → Sarvam loop then runs unchanged, but with
per-company personas instead of per-browser-session config. Transcripts stay
authoritative in `conversations` and are *mirrored* (idempotently) into the lead
thread so chat and phone share one qualification engine.

**2. Companies are your existing `Clients` rows.**
No competing tenant table. A company created via `POST /companies` is immediately
usable by the batch features that already reference `Batch.ClientId`.

## Rollback

Comment out the `register_leadai(app)` block in `main.py`, restart. The
`leadai_*` tables become inert; nothing existing was modified.
