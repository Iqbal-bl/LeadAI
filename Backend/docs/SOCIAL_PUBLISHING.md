# Social Publishing — integration guide

The `browser_agent_v2` project is now part of this application. Its endpoints
appear in the existing Swagger at `/docs` under **LeadAI • Social Publishing**,
and every one of them posts to the Facebook Page / Instagram account belonging
to the company that made the call.

---

## The one thing that changed conceptually

The agent was **single-tenant**. It read the target Page and its token from
process environment variables:

```python
# graph_api/client.py, before
def _access_token() -> str:
    return os.environ["FB_PAGE_ACCESS_TOKEN"]
```

That works for one operator posting to one Page. Run two companies through it
and every request posts to whichever Page happened to be in the environment —
Company A's campaign lands on Company B's Page.

It is now **multi-tenant**. The target is resolved per request from the
`leadai_channel_accounts` row belonging to the authenticated caller's company:

```
request → identity token → Principal → ClientId
        → LeadChannelAccount for that ClientId + platform
        → credentials bound to the request context
        → graph_api reads them
```

A caller cannot name a Page. There is no request field for one. That is what
makes cross-company posting structurally impossible rather than merely absent.

---

## Flow: how a company goes live

1. **Connect the account** — the existing Channels API, unchanged:

   ```http
   POST /api/leadai/channels
   {
     "channel": "messenger",          // a Facebook Page
     "name": "Acme Official",
     "external_id": "1234567890",     // the Page id
     "access_token": "EAAG..."        // Page access token, stored encrypted
   }
   ```

   For Instagram, `channel: "instagram"` with the IG professional account id.

2. **Check what is publishable**

   ```http
   GET /api/leadai/social/platforms
   ```

   Reports `connected: true/false` per platform, with the reason when false.

3. **Publish**

   ```http
   POST /api/leadai/social/posts
   {
     "caption": "Our new showroom is open.",
     "media": [{ "type": "image", "data": "<base64>", "mime_type": "image/jpeg" }],
     "platforms": ["facebook", "instagram"]
   }
   ```

A company that already connected its Page for Messenger can publish
immediately — no second registration step, because `ExternalId` already holds
the right id.

### The shared-token case

Instagram has no token of its own; you publish to it with the linked **Page's**
token. Companies routinely leave the Instagram row's token blank. `resolve()`
detects this and borrows the token from the company's Page row, so the common
setup works instead of failing with a confusing "no access token".

---

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/social/platforms` | What this company can publish to |
| POST | `/social/posts` | **Primary.** Exact caption + base64 media → all platforms |
| POST | `/social/posts/from-urls` | Same, for already-hosted media |
| POST | `/social/posts/ai` | Agent writes the caption from a topic |
| GET | `/social/posts` | History (filter by `status`, `platform`) |
| GET | `/social/posts/{id}` | One post's detail |
| POST | `/social/facebook/posts` | AI post, Facebook only |
| POST | `/social/facebook/posts/direct` | Exact caption; text-only if no media |
| POST | `/social/facebook/posts/multi-photo` | Album post (photos only) |
| GET | `/social/facebook/posts` | Recent posts on the Page |
| DELETE | `/social/facebook/posts/{id}` | Delete |
| POST | `/social/facebook/comments/reply-all` | AI replies, KB-grounded |
| POST | `/social/facebook/messages/reply-all` | AI replies |
| GET | `/social/facebook/leads` | Lead Ads submissions |
| POST | `/social/instagram/posts` | AI post, Instagram only |
| POST | `/social/instagram/posts/direct` | Exact caption (media required) |
| POST | `/social/instagram/posts/carousel` | 2–10 items, mixed media allowed |
| DELETE | `/social/instagram/posts/{id}` | Delete |
| POST | `/social/instagram/comments/reply-all` | AI replies |
| POST | `/social/instagram/messages/reply-all` | AI replies |
| GET/POST | `/social/topics` | Content queue |
| POST | `/social/topics/{id}/publish` | Publish a queued topic |
| DELETE | `/social/topics/{id}` | Remove from queue |

All prefixed with `/api/leadai`.

### Platform capability handling

Facebook Pages have no native mixed-media or multi-video post. A request
carrying both a video and an image is **skipped for Facebook with a stated
reason**, while Instagram's carousel still publishes. One platform's limitation
never blocks another's — the response carries a per-platform result:

```json
{
  "post_id": "…",
  "status": "partial",
  "results": {
    "facebook":  { "success": false, "skipped": true,
                   "error": "facebook does not support this media combination (mixed_carousel)." },
    "instagram": { "success": true, "id": "17895…" }
  }
}
```

---

## Permissions

Three new capabilities, separate from `channel.*` because connecting an account
is an admin act while publishing to it is a daily marketing act — companies
often want the second without the first.

| Permission | company_admin | manager | employee |
|---|---|---|---|
| `social.read` | ✅ | ✅ | ✅ |
| `social.post` | ✅ | ✅ | — |
| `social.manage` (delete) | ✅ | — | — |

Overridable per role through the existing role-permissions table.

---

## What was added / changed

**New**
```
social_agent/                    the vendored agent (graph_api, media_hosting,
                                 agent, tools, crag, browser, image_gen, vision)
social_agent/context.py          per-request credentials (ContextVar)
LeadAI/social/credentials.py     LeadChannelAccount → credentials
LeadAI/social/service.py         publish orchestration, per-platform fan-out
LeadAI/social/agent_bridge.py    lazy, tenant-aware entry to the LLM agent
LeadAI/social/schemas.py         request/response models
LeadAI/models_social.py          leadai_social_posts, leadai_social_topics
LeadAI/routers/social.py         the router
requirements-social.txt          optional AI extras
```

**Modified (four small, additive edits)**
- `LeadAI/router.py` — include the social router
- `LeadAI/rbac.py` — three permissions + role grants
- `LeadAI/models.py` — register the two new tables
- `social_agent/graph_api/{client,pages,instagram}.py` — credentials come from
  the context instead of `os.environ`

`main.py`, `multiligual_call.py` and every existing route are untouched. Tables
are created by the existing `ensure_tables()` on startup; no manual migration.

---

## Configuration

Nothing new is required. Credentials come from the database, per company.

| Variable | Default | Purpose |
|---|---|---|
| `LEADAI_SOCIAL_STRICT_TENANCY` | `true` | Fail closed when no company is bound to the request |
| `MINIO_*` | — | Already configured; base64 media is hosted here |

**Leave strict tenancy on.** With it off, a Graph call made outside a request
context silently falls back to `FB_PAGE_ACCESS_TOKEN` from the environment —
so a wiring bug shows up as *"posted to the wrong company's Page"* instead of a
loud error. The fallback exists only for the standalone CLI scripts in
`tasks/`, which genuinely are single-tenant.

Meta fetches media from the URL you give it, so `MINIO_PUBLIC_BASE_URL` must be
externally reachable over HTTPS. A LAN address or the MinIO console URL will
fail on Meta's side.

---

## Notes for the frontend

- Auth is the identity-server token already in use. The old `X-API-Key` header
  is gone.
- Build the publish screen on `POST /social/posts`; the per-platform routes are
  for the cases that genuinely differ (albums, carousels) and for parity with
  the old API.
- A 409 means "connect an account first" — the message says which. A 502 means
  Meta rejected the call, with Meta's own error text.
- Expect `status: "partial"`. It is normal and not an error.
