# Social Publishing — Documentation Index

The `browser_agent_v2` project, integrated as a multi-tenant feature of this
application. Endpoints appear in the existing Swagger at `/docs` under
**LeadAI • Social Publishing**, at `/api/leadai/social/*`.

Each company publishes to the Facebook Page / Instagram account it registered
through the existing Channels API.

## Read in this order

| Doc | For | Read if |
|---|---|---|
| **[SOCIAL_PUBLISHING.md](SOCIAL_PUBLISHING.md)** | Everyone | Quick start: setup, endpoint table, config. Start here. |
| **[SOCIAL_FRONTEND_GUIDE.md](SOCIAL_FRONTEND_GUIDE.md)** | Frontend | Screens to build, payloads, response handling, error states, validation |
| **[SOCIAL_ARCHITECTURE.md](SOCIAL_ARCHITECTURE.md)** | Backend | How it's wired, why, request lifecycle, files changed, data model |
| **[SOCIAL_KEY_CONCEPTS.md](SOCIAL_KEY_CONCEPTS.md)** | Everyone | Concepts and gotchas — the things that cause hard-to-debug problems |

## The 60-second version

The agent was single-tenant: it read the target Page and token from environment
variables. Mounted as-is, every company's post would go to whichever Page was in
the environment.

It now resolves the target per request from the caller's own
`leadai_channel_accounts` row. **No endpoint accepts a Page id or a token** — the
target comes from the auth token, so cross-company posting is unrepresentable
rather than merely blocked.

Three things that most often go wrong:

1. `MINIO_PUBLIC_BASE_URL` must be publicly reachable over HTTPS — Meta fetches
   media from it directly.
2. `status: "partial"` is a normal outcome, not an error. Render per-platform
   results.
3. Instagram publishes with the **linked Facebook Page's** token; it has none of
   its own.

Regression guard: `test_tenancy.py` (in the repo root of this delivery).
