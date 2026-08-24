# Social Publishing — Architecture

How `browser_agent_v2` became part of this application, and why it is built the
way it is.

---

## 1. What the two codebases were

**Your app** is a multi-tenant FastAPI service. Every request carries an
identity-server token; `TokenValidationMiddleware` validates it, `rbac.py` turns
it into a `Principal`, and `resolve_scope()` produces the one `ClientId` that
request is allowed to touch. Companies register their Meta accounts through
`/api/leadai/channels`, which stores the access token encrypted in
`leadai_channel_accounts`.

**The browser agent** was a separate FastAPI app on port 8080. It had its own
`X-API-Key` auth, its own SQLite database, and — the part that mattered — it read
its posting target from process environment variables:

```python
# graph_api/client.py, as it was
def _access_token() -> str:
    return os.environ["FB_PAGE_ACCESS_TOKEN"]

# graph_api/pages.py, as it was
def _page_id() -> str:
    return os.environ["FB_PAGE_ID"]
```

Both apps understood Facebook Pages. Only one of them understood *whose* Page.

---

## 2. The problem this created

Mounting the agent's routers unchanged would have compiled, started, and served
traffic. It would also have posted every company's content to whichever Page
happened to be in the environment.

That failure mode is unusually nasty for three reasons:

- **It is silent.** No error, no exception. A 200 with a real Facebook post id.
- **It is public and irreversible.** The wrong company's content is live on a
  real audience's feed before anyone notices.
- **It looks fine in single-tenant testing.** With one company connected, the
  environment variable and the database agree. The bug only appears when the
  second customer onboards.

So the integration is not "mount the routers." It is "make the agent
tenant-aware, then mount it."

---

## 3. The shape of the fix

```
HTTP request
   │  Authorization: Bearer <identity-server token>
   ▼
TokenValidationMiddleware          (existing, untouched)
   ▼
scoped("social.post")              (existing rbac.py)
   │  → (Principal, ClientId)
   ▼
LeadAI/routers/social.py           NEW — the router
   ▼
LeadAI/social/credentials.py       NEW — ClientId + platform
   │                                     → LeadChannelAccount row
   │                                     → decrypt token
   ▼
social_agent/context.py            NEW — bind to ContextVar
   ▼
social_agent/graph_api/*           PATCHED — read from context, not os.environ
   ▼
Meta Graph API
```

The important property: **there is no request field for a Page id.** A caller
cannot express "post to Page 12345". The only way the system learns a target is
by looking up rows owned by the authenticated company. Cross-tenant posting is
not blocked by a check that could be forgotten — it is unrepresentable.

---

## 4. Why credentials travel in a ContextVar

The obvious alternative is to pass a `credentials` argument down through
`graph_api`. I rejected it: that is roughly 40 function signatures across
`client.py`, `pages.py`, `instagram.py`, `comments.py`, `messaging.py` and
`leads.py`, plus every agent tool that calls them. Miss one branch and that
branch silently falls back to the environment — and the branch you miss is, by
definition, the one you weren't thinking about.

The next alternative is a module-level global or `threading.local`. **Both are
actively wrong here**, and this is the single most important design point in the
integration:

> This application is async. One thread interleaves many requests. A global set
> by Company A's request is visible to Company B's request when A awaits on a
> network call — which every Graph call does. Under load, tokens would cross.

`contextvars.ContextVar` is the async-correct primitive: asyncio copies the
context into each task, so a value set inside one request is invisible to every
other, including tasks the LangGraph agent spawns concurrently. It gives the
ergonomics of a global with the isolation of a parameter.

The test suite exercises exactly this — ten concurrent publishes from two
companies, deliberately interleaved with `await asyncio.sleep()` in the middle
of each Graph call, asserting zero token/page mismatch.

### Fail-closed

With no company bound, `resolve_access_token()` raises rather than falling back
to `os.environ`. A wiring mistake should be a loud error, not a wrong post. The
environment path still exists behind `LEADAI_SOCIAL_STRICT_TENANCY=false`, for
the standalone scripts in `tasks/` that genuinely are single-tenant.

---

## 5. Mapping your channels onto Meta's objects

The Channels feature was built for *messaging*, so its vocabulary is
`whatsapp | messenger | instagram`. Publishing uses the same underlying objects:

| To publish to | You need | Where it already lives |
|---|---|---|
| A Facebook Page | Page id + Page token | `messenger` row: `ExternalId`, `AccessTokenEnc` |
| An Instagram account | IG user id + **the Page's** token | `instagram` row: `ExternalId` |

Because `ExternalId` already holds the right id for each channel, **no new
registration step was needed**. A company that connected its Page for Messenger
can publish to it immediately.

`PLATFORM_CHANNELS` in `credentials.py` encodes the mapping, and accepts
`"facebook"` as an alias for the stored `"messenger"` value so the publishing API
reads naturally without a database migration.

### The shared-token case

Instagram has no access token of its own — you publish to an IG professional
account using the token of the Facebook Page it is linked to. Companies
therefore routinely fill in the token on the Messenger row and leave the
Instagram row blank.

`resolve()` handles this: if the IG row has no token, it borrows the token from
the company's Page row. Without that fallback, the most common real-world setup
would fail with "no access token" even though the company had supplied one.

---

## 6. Per-platform resolution inside one request

The original `/direct/posts` looped over platforms while one set of environment
credentials stayed constant. Here each platform resolves to a **different
database row** — a company's Page and its Instagram account are separate records
with different ids and potentially different tokens and API versions.

So `service.publish()` re-binds the context *inside* the loop:

```python
for platform in platforms:
    creds = resolve(db, client_id, platform, account_id=account_id)
    with use_credentials(creds):
        result = await poster(caption, uploaded, media_shape)
```

The `use_credentials` context manager resets on exit. That reset matters even
though ContextVars don't escape a task: within one request, the second platform
must not inherit the first platform's Instagram id.

This is also why one platform failing never aborts the others. They are
independent accounts. A company whose Instagram token expired should still get
its Facebook post.

---

## 7. Media is uploaded once

Meta fetches media from a public URL itself — for Instagram, an inline upload is
not possible at all. Base64 media is therefore hosted to MinIO first, and the
resulting URL is reused across every platform:

```
base64 → MinIO (once) → https://cdn/… → Facebook
                                      → Instagram
```

Uploading per platform would push a 40 MB video across the wire twice for no
benefit.

---

## 8. Capability handling, not error handling

Facebook Pages have no native mixed-media or multi-video post. Instagram's
carousel accepts both, up to 10 items. That difference is data, not branching:

```python
PLATFORM_CAPABILITIES = {
    "facebook":  {"none", "single_image", "single_video", "multi_image"},
    "instagram": {"single_image", "single_video", "multi_image",
                  "multi_video", "mixed_carousel"},
}
```

Each request's media list is classified **once** into a shape
(`single_image`, `mixed_carousel`, …), then each platform simply asks "can I
handle this shape?" A platform that can't is **skipped with a stated reason**,
not failed — and the others still publish.

Adding a platform later means adding a `_post_<platform>` function and one
capability set. No endpoint logic changes.

---

## 9. Why the AI path is lazily imported

The AI endpoints pull in langchain, langgraph, the CRAG stack and (for browser
tasks) Playwright. The direct publishing path needs none of it — just `httpx`
and `boto3`, both already in your `requirements.txt`.

Importing the agent at module load would mean a deployment that only wants
direct posting **fails to boot the entire LeadAI API** — Channels, Campaigns,
Inbox, everything — over a dependency it never asked for.

So the import happens inside `agent_bridge.run_task()`, and an `ImportError`
becomes a clean 503 naming `requirements-social.txt`. Direct posting works
today with zero new dependencies.

Credentials still propagate correctly through the agent: its tools call the same
`graph_api` functions, and binding around the `await` covers every tool call the
graph makes, including concurrent ones. The agent itself knows nothing about
tenancy.

---

## 10. Files

**New**

| Path | Role |
|---|---|
| `social_agent/` | The vendored agent (`graph_api`, `media_hosting`, `agent`, `tools`, `crag`, `browser`, `image_gen`, `vision`) |
| `social_agent/context.py` | Per-request credentials (ContextVar) |
| `LeadAI/social/credentials.py` | `LeadChannelAccount` → credentials |
| `LeadAI/social/service.py` | Publish orchestration, per-platform fan-out |
| `LeadAI/social/agent_bridge.py` | Lazy, tenant-aware entry to the LLM agent |
| `LeadAI/social/schemas.py` | Request/response models |
| `LeadAI/models_social.py` | `leadai_social_posts`, `leadai_social_topics` |
| `LeadAI/routers/social.py` | The router |
| `requirements-social.txt` | Optional AI extras |

**Modified — four files, all additive**

| Path | Change |
|---|---|
| `LeadAI/router.py` | Include the social router |
| `LeadAI/rbac.py` | Three permissions + role grants |
| `LeadAI/models.py` | Register the two new tables |
| `social_agent/graph_api/{client,pages,instagram}.py` | Credentials from context, not `os.environ` |

`main.py`, `multiligual_call.py`, and every existing route are untouched.

The vendored agent's own `api/` directory was **not** carried over — its
routers, `X-API-Key` auth and SQLite tables are replaced by the LeadAI router,
your identity-server auth, and your MySQL tables.

---

## 11. Data model

**`leadai_social_posts`** — one row per publish *request*, not per platform. A
"post to Facebook and Instagram" action is one row whose `Results` JSON records
each platform's outcome separately. This keeps the record aligned with the
operator's mental model ("I made one post") while still capturing that Instagram
succeeded and Facebook was skipped.

Key columns: `ClientId`, `Platforms`, `Mode` (`direct|ai`), `MediaShape`,
`Caption`, `Status`, `Results`, `FacebookPostId`, `InstagramMediaId`, `Error`.

**`leadai_social_topics`** — the content queue, the multi-tenant successor to
`topics.csv`. Both tables carry `ClientId` and use the standard `LeadAIBase`
audit shape, so tenant filtering, soft deletes and the activity log work exactly
as they do everywhere else.

Both are registered in `ALL_LEADAI_TABLES` and created by the existing
`ensure_tables()` on startup. No manual migration.

---

## 12. Permissions

Three capabilities, deliberately separate from `channel.*`: connecting an
account is an IT/admin act, publishing to it is daily marketing work. Plenty of
companies want the second without the first — a marketer who can post but cannot
rotate the access token.

| Permission | company_admin | manager | employee |
|---|---|---|---|
| `social.read` | yes | yes | yes |
| `social.post` | yes | yes | — |
| `social.manage` (delete) | yes | — | — |

Overridable per role through the existing role-permissions table, like every
other permission.
