# Frontend API Guide — LeadAI Backend

Base URL: `https://<your-domain>/api/leadai`

All endpoints require an `Authorization: Bearer <token>` header unless marked as public.

---

## 1. Channel Management

### List Channels
```
GET /channels
```
Returns all connected channel accounts for the company.

**Response:** `Channel[]`
```json
[
  {
    "id": "uuid",
    "channel": "instagram",
    "name": "@lion.83534746",
    "external_id": "17841479633674193",
    "business_account_id": "27469075269460649",
    "is_active": true,
    "auto_reply": true,
    "login_type": "instagram",
    "has_access_token": true,
    "last_inbound_at": "2026-08-25T10:00:00Z"
  }
]
```

### Channel Status
```
GET /channels/status
```
Dashboard summary — total/active channels, total contacts, messages today.

### Create Channel (manual)
```
POST /channels
```
```json
{
  "channel": "whatsapp",
  "name": "My Business",
  "external_id": "phone_number_id",
  "access_token": "...",
  "app_secret": "..."
}
```

### Update Channel
```
PATCH /channels/{account_id}
```
```json
{
  "auto_reply": false,
  "is_active": true,
  "script_id": "uuid"
}
```

### Delete Channel
```
DELETE /channels/{account_id}
```

### Test Channel
```
POST /channels/{account_id}/test
```
```json
{
  "to": "+15551234567",
  "message": "Hello from LeadAI"
}
```

### Channel Contacts
```
GET /channels/{account_id}/contacts?page=1&page_size=50
```

---

## 2. Instagram Connection Flow (Standalone)

### Step 1: Start OAuth
```
GET /channels/instagram/connect?publishing=true
```
Requires `channel.manage` permission.

**Response:**
```json
{
  "authorize_url": "https://www.instagram.com/oauth/authorize?client_id=...&redirect_uri=...&scope=...&state=...",
  "state": "random-state-token",
  "expires_in": 600
}
```
**Frontend action:** Open `authorize_url` in a new browser window/popup. The user authorizes on Instagram. Instagram redirects back to your callback URL with `?code=...&state=...`.

### Step 2: Complete Callback
```
POST /channels/instagram/callback
```
```json
{
  "code": "<authorization-code-from-instagram>",
  "state": "<state-from-step-1>"
}
```

**Response:** `ChannelAccountOut`
```json
{
  "id": "uuid",
  "channel": "instagram",
  "name": "@lion.83534746",
  "external_id": "17841479633674193",
  "business_account_id": "27469075269460649",
  "login_type": "instagram",
  "is_active": true,
  "auto_reply": true,
  "has_access_token": true,
  "verify_token": "leadai-verify",
  "webhook_url": "https://<domain>/api/leadai/public/webhooks/meta"
}
```
**Frontend action:** After receiving 200, refresh the channels list. The account is now connected and subscribed to webhooks automatically.

### Step 3: Refresh Token (if needed)
```
POST /channels/{account_id}/refresh-token
```
No body required. Returns updated `ChannelAccountOut` with new `token_expires_at`.

**Frontend action:** Instagram tokens expire after ~60 days. Show a warning badge when `token_expires_at` is within 7 days, and call this endpoint on click.

---

## 3. Webhook Configuration (Manual, one-time)

The backend handles webhook verification and message processing automatically. The **frontend does NOT call webhooks** — but you should display the webhook info to the user so they can configure it in Meta's dashboard:

| Field | Value |
|-------|-------|
| Callback URL | `https://<your-domain>/api/leadai/public/webhooks/meta` |
| Verify Token | `leadai-verify` |

Display these on the Instagram channel detail page so the user can paste them into the Meta Developer Dashboard.

---

## 4. Social Posting

### Multi-Platform Publish (base64 media)
```
POST /social/posts
```
Requires `social.post` permission.

```json
{
  "caption": "Check out our new product!",
  "media": [
    {
      "type": "image",
      "data": "data:image/jpeg;base64,/9j/4AAQ...",
      "mime_type": "image/jpeg"
    }
  ],
  "platforms": ["facebook", "instagram"],
  "account_id": "optional-specific-account",
  "schedule_time": "2026-08-26T10:00:00Z"
}
```
- `schedule_time` is optional. Omit for immediate publish. Must be future UTC.
- `account_id` is optional. Omit to use the default connected account for each platform.

**Response (201):**
```json
{
  "post_id": "uuid",
  "status": "published",
  "results": {
    "facebook": { "success": true, "id": "123456789" },
    "instagram": { "success": true, "id": "1784..." }
  }
}
```

### Multi-Platform Publish (pre-hosted URLs)
```
POST /social/posts/from-urls
```
```json
{
  "caption": "Hello world",
  "media": [
    { "url": "https://example.com/photo.jpg", "is_video": false }
  ],
  "platforms": ["instagram"]
}
```

### AI-Generated Post
```
POST /social/posts/ai
```
```json
{
  "topic": "Summer sale on电动车",
  "instructions": "Write in a friendly, casual tone. Include emojis.",
  "image_url": "https://example.com/background.jpg",
  "platforms": ["facebook", "instagram"]
}
```
The AI writes the caption and publishes. Response same structure as direct post.

### Instagram-Specific Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/social/instagram/posts` | AI-written IG post |
| `POST` | `/social/instagram/posts/direct` | Exact caption publish |
| `POST` | `/social/instagram/posts/carousel` | 2-10 item carousel |
| `DELETE` | `/social/instagram/posts/{media_id}` | Delete media |
| `POST` | `/social/instagram/comments/reply-all` | AI-reply to comments |
| `POST` | `/social/instagram/messages/reply-all` | AI-reply to DMs |

### Facebook-Specific Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/social/facebook/posts` | AI-written FB post |
| `POST` | `/social/facebook/posts/direct` | Exact caption publish |
| `POST` | `/social/facebook/posts/multi-photo` | Multi-photo album |
| `GET` | `/social/facebook/posts` | Recent Page posts |
| `DELETE` | `/social/facebook/posts/{post_id}` | Delete post |
| `POST` | `/social/facebook/comments/reply-all` | AI-reply to comments |
| `POST` | `/social/facebook/messages/reply-all` | AI-reply to DMs |
| `GET` | `/social/facebook/leads` | Lead Ads submissions |

### Post History
```
GET /social/posts?page=1&page_size=25&status=published&platform=instagram
```

**Response:**
```json
{
  "total_items": 42,
  "page": 1,
  "page_size": 25,
  "items": [
    {
      "id": "uuid",
      "platforms": ["instagram"],
      "mode": "direct",
      "status": "published",
      "caption": "Hello world",
      "media_urls": ["https://..."],
      "results": { "instagram": { "success": true } },
      "instagram_media_id": "1784...",
      "published_at": "2026-08-25T10:00:00Z"
    }
  ]
}
```

### Content Queue (Topics)
```
GET    /social/topics              — list queued topics
POST   /social/topics              — queue a topic
POST   /social/topics/{id}/publish — publish now
DELETE /social/topics/{id}         — remove
```

### Platform Status
```
GET /social/platforms
```
**Response:**
```json
{
  "platforms": {
    "facebook": { "connected": true, "account_id": "...", "account_name": "My Page" },
    "instagram": { "connected": true, "account_id": "...", "account_name": "@username" }
  }
}
```

---

## 5. Inbox

### List Conversations
```
GET /inbox?status=open&channel=instagram&page=1&page_size=25&sort=recent
```
**Query params:** `status`, `lead_status`, `channel`, `assigned_to`, `search`, `min_score`, `sort`, `page`, `page_size`, `above_threshold`, `campaign_id`

**Response:**
```json
{
  "total_items": 150,
  "page": 1,
  "page_size": 25,
  "items": [
    {
      "id": "conversation-uuid",
      "channel": "instagram",
      "status": "needs_human",
      "customer_ref": "Customer #96364",
      "customer_name": "17841479633674193",
      "customer_phone_masked": null,
      "summary": "Customer asking about pricing...",
      "next_step": "Send pricing sheet",
      "assigned_user_email": null,
      "handoff_reason": "Answer confidence 0.37 below threshold 0.4",
      "language": "en-IN",
      "message_count": 10,
      "last_message_at": "2026-08-25T07:58:37Z",
      "lead": {
        "status": "hot",
        "score": 76,
        "interest": "General enquiry",
        "sentiment": "positive"
      }
    }
  ]
}
```
**Note:** `customer_name` shows the Instagram handle or IG-scoped ID when the real display name is unavailable (standalone Instagram accounts). Only visible to users with `lead.reveal_pii` permission.

### Conversation Detail
```
GET /inbox/{conversation_id}
```
Returns full `LeadDetail` with `messages[]`, `calls[]`, `suggestions[]`.

### Assign / Claim
```
POST /inbox/{id}/assign    — { "user_email": "agent@example.com" }
POST /inbox/{id}/claim     — (no body, assigns to current user)
```

### Reply
```
POST /inbox/{id}/reply
```
```json
{ "message": "Thanks for reaching out! Here's the pricing info..." }
```

### Set Status
```
POST /inbox/{id}/status
```
```json
{ "status": "closed" }
```

### Requalify (Re-score)
```
POST /inbox/{id}/requalify
```

### Export Leads
```
GET /inbox/export/leads?lead_status=hot&limit=100
```

---

## 6. Contact Reveal (PII)

### Reveal Contact Details
```
GET /inbox/{conversation_id}/contact
```
Requires `lead.reveal_pii` permission (Admin, company_admin).

**Response:**
```json
{
  "phone": "+919876543210",
  "email": "jane@example.com",
  "whatsapp": "+919876543210",
  "instagram": "@janedoe",
  "display_name": "Jane Doe",
  "social_identities": [
    {
      "channel": "instagram",
      "handle": "@janedoe",
      "profile_name": "Jane Doe",
      "external_user_id": "17841400123456789",
      "profile_url": "https://instagram.com/janedoe",
      "opted_out": false,
      "last_message_at": "2026-08-25T14:30:00Z"
    }
  ],
  "revealed_at": "2026-08-25T15:00:00Z",
  "warning": "This reveal has been recorded in the activity log."
}
```
**Note:** Every reveal is audit-logged. The `warning` field confirms this.

---

## 7. Key Frontend Behaviors

### Instagram Standalone Accounts
- `login_type` will be `"instagram"` (not `"facebook"`)
- `external_id` is the IGID (e.g., `17841...`)
- `business_account_id` is the app-scoped user ID (e.g., `27469...`)
- Publishing uses `graph.instagram.com` (not `graph.facebook.com`)
- `customer_name` in inbox may show the raw IG-scoped ID if display name is unavailable

### Webhook Verification
- Display the webhook URL and verify token on the channel detail page
- The GET handshake is automatic — user just pastes values into Meta dashboard

### Token Expiry
- Instagram tokens expire after ~60 days
- Show a warning when `token_expires_at` is within 7 days
- Call `POST /channels/{id}/refresh-token` to extend

### Auto-Reply Toggle
- `PATCH /channels/{id}` with `{ "auto_reply": true/false }`
- When off, messages are stored but AI does not respond

### Media Upload
- Base64 media is uploaded to MinIO by the backend
- Frontend should convert files to base64 before sending
- Supported: `image/jpeg`, `image/png`, `video/mp4`
- Instagram limits: single image, single video (Reel), or 2-10 item carousel

---

## 8. Environment Config

```typescript
// src/environments/environment.ts
export const environment = {
  apiPrefix: 'https://<your-domain>/api/leadai',
  // ...
};
```

All `ApiService` calls prepend `apiPrefix` + the path. The `companyScoped` flag adds the company header if needed.
