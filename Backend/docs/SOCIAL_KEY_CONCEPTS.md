# Social Publishing — Key Concepts & Gotchas

The things worth knowing before you extend, debug, or deploy this. Ordered by
how much pain they cause when missed.

---

## 1. Tenancy is structural, not a check — keep it that way

There is **no request field anywhere for a Page id or a token**. The target is
derived only from the auth token → `ClientId` → `LeadChannelAccount`. Posting to
someone else's Page isn't blocked by a validation that could be skipped; it is
unrepresentable.

**The rule to preserve:** if you add an endpoint, never accept a Page id,
Instagram id, or access token from the request body. Always go through
`credentials.resolve(db, client_id, platform)`.

The moment one endpoint takes a `page_id` parameter "just for an internal tool",
the guarantee is gone for the whole system — and the resulting bug posts a real
customer's content to the wrong public audience.

`account_id` is the *only* caller-supplied selector, and `resolve()` verifies it
belongs to the caller's company before using it. That check is why a
cross-company `account_id` returns "not connected" rather than working.

---

## 2. ContextVar, not a global — this is an async app

Credentials live in `contextvars.ContextVar`.

Do **not** "simplify" this into a module-level global or `threading.local`. One
thread interleaves many requests here, and every Graph call awaits on the
network. A global set by Company A is visible to Company B the moment A awaits.
Tokens would cross under load — and only under load, so it would pass every test
you write by hand.

```python
# CORRECT — inside a request
with use_credentials(creds):
    await graph_pages.create_text_post(caption)

# WRONG — no tenant bound; raises under strict tenancy (by design)
await graph_pages.create_text_post(caption)
```

If you spawn work with `asyncio.create_task`, the context is copied at creation
— so credentials propagate correctly. If you hand work to a **thread pool** or a
background worker process, it does not. Resolve and pass credentials explicitly
there.

---

## 3. Fail-closed is deliberate — leave `LEADAI_SOCIAL_STRICT_TENANCY=true`

With strict tenancy on, a Graph call with no bound company raises. With it off,
it silently falls back to `FB_PAGE_ACCESS_TOKEN` from the environment.

That difference is the difference between a **loud error in staging** and
**posting to the wrong company's Page in production**. The fallback exists only
for the standalone single-tenant scripts in `tasks/`.

If you ever see "No social account is bound to this request", the fix is to
route the call through the API layer — not to disable the flag.

---

## 4. Instagram has no token of its own

You publish to an Instagram professional account using **the access token of the
Facebook Page it is linked to**. There is no separate Instagram token to obtain.

Consequences:

- Users will leave the token blank on the Instagram channel row. `resolve()`
  borrows the Page's token automatically. Say so in the UI or they'll go hunting
  for something that doesn't exist.
- If a company connects Instagram but *not* its Page, there is no token to
  borrow and publishing fails. The 409 message says this.
- Rotating the Page token silently affects Instagram publishing too.

---

## 5. Meta fetches your media — it is not uploaded to Meta

For Instagram, inline upload is impossible; Meta's servers fetch the media from
a URL you supply. So the URL must be reachable **from Meta's infrastructure**,
not from your network.

`MINIO_PUBLIC_BASE_URL` must be an externally reachable **HTTPS** endpoint.
These all fail, and they fail on Meta's side with an unhelpful message:

- a LAN/private address (`http://192.168.x.x:9000`)
- the MinIO *console* URL rather than the object endpoint
- plain HTTP
- a presigned URL that expires before Meta fetches it (video processing can take
  minutes)
- anything behind auth

This is the single most common deployment failure. Verify by fetching the URL
from outside your network before blaming the code.

---

## 6. Instagram publishing is two calls and is asynchronous

Every Instagram post is: create a **container** → poll until `FINISHED` →
publish. Meta processes the media on its own schedule.

- Photos: a few seconds.
- Reels/video: often 30–90s, sometimes longer. This is why the video path gets a
  larger recursion budget in `agent_bridge.RECURSION_LIMITS`.

So Instagram requests are *slow by nature*. Don't tune HTTP timeouts down
without accounting for it, and don't treat a long request as a hang.

Facebook photo/text posts are a single synchronous call — the asymmetry is
Meta's, not ours.

---

## 7. Platform capabilities differ, and that's data not exceptions

| Shape | Facebook | Instagram |
|---|---|---|
| Text only | yes | **no** |
| Single image | yes | yes |
| Single video | yes | yes (Reel) |
| Multiple images | yes (album) | yes (carousel) |
| Multiple videos | **no** | yes |
| Mixed image + video | **no** | yes (carousel, ≤10) |

A request whose shape a platform can't handle is **skipped with a reason**, not
failed — and the other platforms still publish. `status: "partial"` is a normal
outcome, not an error.

Adding a platform means adding a `_post_<platform>` function and one entry in
`PLATFORM_CAPABILITIES`. No endpoint logic changes.

---

## 8. Direct vs AI are genuinely different paths

| | Direct | AI |
|---|---|---|
| Caption | published verbatim | written by the model |
| Dependencies | none beyond current | langchain, langgraph, CRAG |
| Latency | seconds | 20–60s+ |
| Determinism | exact | varies per run |
| Grounding | — | the company's own knowledge base |

Conflating these produced the standalone agent's most confusing behaviour
("why did my caption change?"). Keep them visibly distinct in the UI.

The AI path is **lazily imported** — an `ImportError` becomes a 503 rather than
taking down the whole LeadAI API at boot. Direct posting needs nothing new
installed.

---

## 9. One post row per request, not per platform

`leadai_social_posts` stores one row for a "post to Facebook and Instagram"
action, with per-platform outcomes inside `Results`. This matches how the
operator thinks ("I made one post") while still capturing that Instagram
succeeded and Facebook was skipped.

When you query or report, remember: **row count ≠ platform post count.** Use
`Results` for per-platform analytics.

---

## 10. Publishing is irreversible and public

Unlike most CRUD in this system:

- A successful post is **live to a real audience immediately**.
- A double-clicked button posts twice. Nothing deduplicates — disable on submit.
- Deletes are permanent and hit the live Page.
- Reply-all writes public replies at scale. Confirm before firing.

Treat these endpoints with the caution you'd give an outbound bulk send, not a
draft save. That's also why `social.post` is separate from `social.read`, and
`social.manage` (delete) is company-admin only.

---

## 11. Tokens expire, and the failure is remote

Page access tokens expire or get invalidated (password change, permission
revocation, app review). The failure surfaces as a **502 with Meta's message**,
not as a local error.

`LeadChannelAccount` already has `LastError`, `LastErrorAt`, `LastOutboundAt` —
worth surfacing in the Channels UI as a health indicator so companies fix a dead
token before a campaign depends on it.

Also: if the Fernet encryption key rotates without re-encrypting stored tokens,
decryption fails. That's logged and treated as "no token", producing a
"reconnect this account" 409 rather than a crypto traceback.

---

## 12. API versions are per company

`LeadChannelAccount.ApiVersion` is per row, so `BASE_URL` **cannot** be a module
constant — different companies onboard at different times and Meta deprecates
versions on a rolling schedule (roughly every two years). `client._base_url()`
resolves it per call.

If you see version-related Graph errors, check the account row, not the code.

---

## 13. Permissions are split by act, not by object

`channel.*` governs *connecting* an account (an IT/admin act). `social.*`
governs *publishing* to it (daily marketing work). Many companies want a
marketer who can post but cannot rotate the access token.

| | company_admin | manager | employee |
|---|---|---|---|
| `social.read` | yes | yes | yes |
| `social.post` | yes | yes | — |
| `social.manage` | yes | — | — |

Overridable per role in the role-permissions table, like everything else.

---

## 14. Where to look when something breaks

| Symptom | Look at |
|---|---|
| Posted to the wrong Page | `credentials.resolve()`; confirm strict tenancy is on |
| "No social account is bound" | Call is bypassing the router / not inside `use_credentials` |
| 409 on publish | Channels: no active account, or no token to borrow |
| 502 with a Meta message | Token validity, permissions granted, `ApiVersion` |
| Instagram fails, Facebook works | Media URL reachability from outside; IG needs media |
| Media URL errors | `MINIO_PUBLIC_BASE_URL` — public? HTTPS? object endpoint? |
| 503 on AI endpoints | `pip install -r requirements-social.txt` |
| Slow Instagram video | Normal — container processing |

---

## 15. Regression test

`test_tenancy.py` seeds two companies, stubs only the HTTP layer, and exercises
the real resolution and fan-out code — including **ten concurrent interleaved
publishes** asserting zero token/page mismatch, and a cross-company `account_id`
attempt that must be rejected.

Run it after any change to `credentials.py`, `context.py`, `service.py`, or the
`graph_api` credential functions. It is the guard on the property that matters
most, and it catches the class of bug that manual testing with one company never
will.
