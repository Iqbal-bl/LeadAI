# LeadAI Backend

FastAPI backend for LeadAI, a multi-tenant AI lead-generation platform. It sits on top of
the original AI outbound-calling app. One process serves both, and one MySQL database holds both.

## Run it

```bash
python -m venv .venv && .venv/Scripts/activate      # source .venv/bin/activate on Linux/macOS
pip install -r requirements.txt
cp .env.example .env                                # then fill it in
python main.py                                      # or: uvicorn main:app --port 6789
```

Docker: `docker compose up -d` (API, background worker, MinIO). Health check: `GET /api/leadai/health`.
Swagger UI: `/docs`. Server setup notes: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), [docs/DOCKER.md](docs/DOCKER.md).

## Where things are

```
Backend/
├── main.py            Entry point. Builds the app, adds batch routes, registers LeadAI.
│
├── core/              Shared infrastructure: config check, DB engine, auth, storage, paths, websockets
├── domain/            Shared DB tables (Clients = a company/tenant, users, batches, call logs) + schemas
│
├── outbound/          The original outbound-calling app
│   ├── app.py           voice calls, Twilio webhooks, live audio <-> speech <-> LLM
│   ├── batching.py      batch-calling engine and /api/batches
│   ├── speech/          Sarvam speech-to-text / text-to-speech
│   ├── repositories/    data access for batch tables
│   └── bot/             insurance-claim bot toolkit (only its CSV export is used)
│
├── LeadAI/            The lead-generation platform (everything under /api/leadai)
│   ├── routers/         HTTP endpoints, one file per feature (inbox, campaigns, billing, ...)
│   ├── services/        business logic (AI engine, RAG, campaigns, billing, jobs, telephony)
│   ├── social/          LinkedIn and social publishing helpers
│   ├── models*.py       its database tables (all prefixed leadai_)
│   ├── schemas*.py      request / response models
│   ├── rbac.py          roles and permissions
│   └── integration.py   the single hook main.py calls to attach LeadAI to the app
│
├── social_agent/      Facebook / Instagram Graph API client and the AI posting agent
│
├── scripts/           XML agent scripts (runtime data, mounted as a docker volume)
├── tools/             One-off maintenance scripts
├── tests/             Tests and test stubs
├── demo/              Demo company ("Nexa Finserv") seed data and runbook
├── data/              Old sample data and binaries that no code reads
└── docs/              Guides and design notes (see docs/README.md)
```

## How a request flows

`main.py` imports the voice app from `outbound/app.py`, mounts the batch router, then calls
`LeadAI.integration.register(app)`. If LeadAI fails to load, the voice and batch APIs still work.

LeadAI routes live under `/api/leadai`. Each one checks a permission (`LeadAI/rbac.py`) and
resolves which company the caller may touch. Background work (campaigns, LinkedIn) runs in a
separate worker container from the same image (`LEADAI_WORKER_ENABLED=true`).

## Working in this codebase

- **Adding an endpoint:** router in `LeadAI/routers/`, logic in `LeadAI/services/`, request/response
  models in `LeadAI/schemas*.py`, then include the router in `LeadAI/router.py`.
- **Adding a table:** model in `LeadAI/models*.py` (tables are created on startup; new columns are
  added automatically, nothing is ever dropped).
- **File paths:** use `core/paths.py` instead of relative paths, so the app works from any folder.
- **Imports:** always absolute from the package roots: `core`, `domain`, `outbound`, `LeadAI`.
- **Known limits:** run one uvicorn worker (live call state is in process memory).
