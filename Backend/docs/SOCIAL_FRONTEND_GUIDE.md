# Social Publishing — Frontend Guide

Everything the UI needs: which screens to build, what to call, what comes back,
and which states are easy to get wrong.

Base path for everything below: **`/api/leadai/social`**

---

## Auth

The same identity-server token every other LeadAI call uses:

```http
Authorization: Bearer <token>
```

There is no second credential. The old `X-API-Key` header is gone — if you see
it in any older reference to the agent API, ignore it.

Gate UI on permissions:

| Permission | Show |
|---|---|
| `social.read` | Post history, platform status |
| `social.post` | Compose, publish, queue |
| `social.manage` | Delete buttons on published posts |

---

## Screens to build

### 1. Connect (may already exist)

Publishing reuses the **existing Channels screen**. A company that has already
connected its Facebook Page for Messenger can publish immediately — no second
setup step.

```http
POST /api/leadai/channels
{
  "channel": "messenger",        // a Facebook Page
  "name": "Acme Official",
  "external_id": "1234567890",   // the Page id
  "access_token": "EAAG..."      // Page access token
}
```

For Instagram use `"channel": "instagram"` with the IG professional account id
as `external_id`. **The token may be left blank on the Instagram row** — the
backend borrows the linked Page's token automatically. Worth a hint in the UI so
users don't hunt for a token that doesn't exist.

---

### 2. Platform status — call this before showing Compose

```http
GET /api/leadai/social/platforms
```

```json
{
  "platforms": {
    "facebook": {
      "connected": true,
      "account_id": "8f3c…",
      "account_name": "Acme Official",
      "target_id": "1234567890"
    },
    "instagram": {
      "connected": false,
      "reason": "No active instagram account is connected for this company. Connect one first: POST /channels with channel='instagram'."
    }
  }
}
```

Use it to drive the platform checkboxes. Disable an unconnected platform and
surface `reason` as the tooltip — it is written to be shown to a user and always
names the fixable action. Link straight to the Channels screen from there.

Don't let someone compose a post, upload a 30 MB video, and only then discover
Instagram isn't connected.

---

### 3. Compose — the main screen

Build it on **one endpoint**:

```http
POST /api/leadai/social/posts
{
  "caption": "Our new showroom is open.",
  "media": [
    { "type": "image", "data": "<base64>", "mime_type": "image/jpeg" }
  ],
  "platforms": ["facebook", "instagram"]
}
```

- `media` — omit or send `[]` for a text-only post (Facebook only).
- `data` — raw base64. A `data:image/jpeg;base64,…` prefix is also accepted, so
  you can pass a `FileReader.readAsDataURL()` result straight through.
- `platforms` — defaults to both. `account_id` is optional; send it only for
  companies with more than one connected Page.

**If your media is already hosted** (a CDN, or a file uploaded through the
documents API), skip the upload entirely:

```http
POST /api/leadai/social/posts/from-urls
{
  "caption": "…",
  "media": [{ "url": "https://cdn.example.com/a.jpg", "is_video": false }],
  "platforms": ["facebook"]
}
```

The URL must be publicly reachable over HTTPS — Meta fetches it directly. A
short-lived presigned URL or anything behind a login will fail on Meta's side.

#### AI-written captions

```http
POST /api/leadai/social/posts/ai
{
  "topic": "spring sale on winter stock",
  "instructions": "friendly, under 200 characters, one emoji",
  "platforms": ["facebook", "instagram"]
}
```

Leave `image_url` empty and an image is generated for the topic. Captions are
grounded in the company's own knowledge base, so two companies get different
copy from the same topic.

This is **slow** — an LLM call plus image generation plus publishing, commonly
20–60s, and longer for video. Do not run it behind a spinner on a modal the user
can't leave. Either give it a dedicated progress state, or queue it (§5).

If the deployment hasn't installed the AI extras you get a **503** naming
`requirements-social.txt`. Treat it as "feature not enabled here" and hide the
AI tab rather than showing a scary error.

---

### 4. Reading the response — the part most likely to trip you up

```json
{
  "post_id": "3f9a…",
  "status": "partial",
  "results": {
    "facebook": {
      "success": false,
      "skipped": true,
      "error": "facebook does not support this media combination (mixed_carousel)."
    },
    "instagram": {
      "success": true,
      "id": "17895…",
      "account_name": "Acme Official"
    }
  }
}
```

**`status: "partial"` is normal and is not an error.** The HTTP status is 201
whenever the request itself was well-formed, regardless of what individual
platforms did. Render per-platform outcomes, never a single global success
banner.

| `status` | Meaning | Suggested UI |
|---|---|---|
| `published` | Every platform succeeded | Green, link to each post |
| `partial` | Some succeeded, some didn't | Amber, per-platform rows, retry only the failures |
| `failed` | None succeeded | Red, show each `error` |

Within `results`, distinguish two failure kinds:

- **`skipped: true`** — a platform limitation, not a fault. Facebook cannot do
  mixed media or multiple videos. Phrase it as *"Not supported on Facebook"*,
  not *"Failed"*, and don't offer a retry that will deterministically fail again.
- **`not_connected: true`** — that platform has no usable account. Link to
  Channels.

---

### 5. Content queue

For scheduling and bulk planning.

```http
GET    /api/leadai/social/topics?status=pending
POST   /api/leadai/social/topics
POST   /api/leadai/social/topics/{id}/publish
DELETE /api/leadai/social/topics/{id}
```

```json
{
  "topic": "customer story: the Sharma family",
  "instructions": "warm, testimonial tone",
  "platforms": ["facebook"],
  "scheduled_for": "2026-09-01T09:00:00Z"
}
```

This is the right home for AI generation: the user queues topics and gets on
with their day instead of watching a 60-second spinner. `attempts` and
`last_error` are on each topic for a retry UI.

---

### 6. History

```http
GET /api/leadai/social/posts?page=1&page_size=25&status=failed&platform=instagram
GET /api/leadai/social/posts/{id}
```

Returns `{ total_items, page, page_size, items[] }`. Each item carries
`facebook_post_id` / `instagram_media_id` for deep links, plus `media_urls`,
`duration_ms` and the full `results` object.

Also available for a live view of the Page itself:

```http
GET /api/leadai/social/facebook/posts?limit=10
```

---

### 7. Platform-specific composers (optional)

Use the primary endpoint for the common case. These exist for the shapes that
genuinely differ:

| Endpoint | When |
|---|---|
| `POST /social/facebook/posts/multi-photo` | Facebook album, **photos only**, 2+ |
| `POST /social/instagram/posts/carousel` | Instagram carousel, 2–10, **mixed media allowed** |
| `POST /social/facebook/posts/direct` | Facebook only, incl. text-only |
| `POST /social/instagram/posts/direct` | Instagram only, media required |

---

### 8. Engagement actions

```http
POST /social/facebook/comments/reply-all    { "limit": 25 }
POST /social/facebook/messages/reply-all
POST /social/instagram/comments/reply-all
POST /social/instagram/messages/reply-all
```

AI replies grounded in the company's knowledge base. Long-running and they write
publicly — confirm before firing, and show what was replied to afterwards.

Lead Ads submissions:

```http
GET /social/facebook/leads?form_id=<id>&limit=50
```

---

## Error handling

| Status | Meaning | UI |
|---|---|---|
| **400** | Bad shape (carousel with 1 item, album with a video) | Inline validation |
| **403** | Missing permission | Hide the control instead of erroring |
| **404** | Post/topic not found *or belongs to another company* | "Not found" |
| **409** | No connected account, or account has no token | **Actionable** — link to Channels; `detail` names the fix |
| **502** | Meta rejected the call; `detail` is Meta's own message | Show it verbatim, offer retry |
| **503** | AI extras not installed | Hide the AI feature |

409 is the one to invest in — it's the most common real failure and always has a
concrete fix.

---

## Client-side validation

Save users a round trip:

| Rule | Message |
|---|---|
| Instagram needs ≥1 media item | "Instagram posts need an image or video." |
| Facebook: no mixed image+video | "Facebook can't post images and video together — Instagram can." |
| Facebook: no multiple videos | "Post videos to Facebook one at a time." |
| Facebook album: photos only, 2+ | — |
| IG carousel: 2–10 items | — |
| Text-only ⇒ Facebook only | Auto-deselect Instagram |

The backend enforces all of this. Checking early just avoids uploading media
that was never going to publish.

---

## Practical notes

**Uploads are slow.** Base64 inflates payloads ~33%, and a video goes browser →
your API → MinIO → Meta. Show real progress and don't block the whole form.

**Never send a `client_id` or a Page id.** No endpoint accepts one. The target
is derived from the auth token. If you find yourself wanting to pass one, the
answer is `account_id` — and only for multi-Page companies.

**`account_id` is optional.** Omit it unless the company has more than one
connected account for that platform. When omitted the oldest active account is
used, deterministically.

**Publishing is not idempotent.** A double-click posts twice. Disable the button
on submit and key any retry off `post_id`.

**Deletes are permanent** and hit the live Page. Confirm, and gate on
`social.manage`.

---

## Suggested build order

1. `GET /social/platforms` + the connect prompt — nothing works without it
2. Compose → `POST /social/posts`, with per-platform result rendering
3. History
4. Content queue
5. AI generation (needs the optional extras installed)
6. Reply-all engagement actions
