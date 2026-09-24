# Connecting Instagram without a Facebook Page

**Short answer: yes, this is possible, but it is not a setting you can flip on
your existing app. It is a second Meta app and a second code path, and this
release adds both.**

---

## Why your current setup requires a Page

Meta ships two different Instagram integrations. They are not options within
one product — they are separate products with separate hosts, credentials,
scopes and token lifecycles.

| | Instagram API with **Facebook Login** | Instagram API with **Instagram Login** |
|---|---|---|
| Facebook Page | **Required** | Not needed |
| Auth host | `facebook.com/dialog/oauth` | `instagram.com/oauth/authorize` |
| API host | `graph.facebook.com` | `graph.instagram.com` |
| Token type | Page access token | Instagram User access token |
| Token life | Long-lived Page token | 60 days, must be refreshed |
| Credentials | Facebook App ID / Secret | **Instagram** App ID / Secret |
| Scopes | `instagram_manage_messages`, `pages_messaging` | `instagram_business_manage_messages`, … |

Your code was built entirely against the left-hand column. `_graph_url()` had a
single hard-coded base (`META_GRAPH_BASE`), so every call went to
`graph.facebook.com` regardless. That is why Instagram only worked when linked
to a Page with the Facebook app id/secret in the database — it was the only
integration the code could express.

The second option was launched by Meta on **23 July 2024** and is exactly what
you are asking for: the Instagram account authenticates directly, with no
Facebook Page anywhere in the picture.

## The constraint that shapes everything

Meta's documentation is explicit:

> You can only add one setup per app. If you want to implement both setups,
> create an app for each setup.

So you cannot add Instagram Login to your existing app. **You need a second Meta
app**, which issues its own Instagram App ID and Instagram App Secret — different
values from the Facebook ones you have in the database today.

This is why `LeadChannelAccount` now carries `LoginType`, `AppId` and its own
`AppSecretEnc`. One company can have a Page-linked Instagram account on the old
app *and* a standalone Instagram account on the new one, and each has to be
signed, addressed and refreshed differently.

## The failure that will cost you an afternoon if you skip it

An Instagram User access token is **not valid at `graph.facebook.com`**. Sent
there it returns:

```
Invalid OAuth access token - Cannot parse access token
```

That reads like a bad credential, so the instinct is to regenerate the token —
which produces another token that fails identically. The problem is the host,
not the token. `channels._graph_base()` now routes per account so this cannot
happen by accident.

---

## Setup

### 1. The Instagram account

Must be a **professional** account — Business or Creator. Personal accounts
cannot use this API at all; the Basic Display API that once served them was
sunset in December 2024 with no replacement.

Instagram app → Settings → Account type and tools → Switch to professional
account. Free, reversible, keeps the handle and followers.

### 2. A second Meta app

1. developers.facebook.com → Create App → **Business**
2. Add Product → **Instagram** → choose **API setup with Instagram Login**
   (not "API setup with Facebook Login")
3. Add the messaging permissions:
   - `instagram_business_basic`
   - `instagram_business_manage_messages`
   - `instagram_business_manage_comments`
   - `instagram_business_content_publish` *(only if publishing)*
4. Under **Business login settings**, add your redirect URI to Valid OAuth
   Redirect URIs. It must match `INSTAGRAM_REDIRECT_URI` **exactly** — scheme,
   host, path, trailing slash. A mismatch produces a generic OAuth error that
   does not mention the URI.
5. Copy the **Instagram App ID** and **Instagram App Secret** from that screen.
   These are not your Facebook app's values.

> Scope names changed in January 2025. The old `business_basic`,
> `business_manage_messages` etc. were deprecated on 27 January 2025. Any
> tutorial using them predates the rename.

### 3. Webhooks

In the same app: Webhooks → **Instagram** object → subscribe to `messages`,
`messaging_postbacks`, `comments`.

- Callback URL: `https://your-domain/api/leadai/public/webhooks/meta`
- Verify token: whatever you set as `INSTAGRAM_VERIFY_TOKEN`

The existing webhook handler already parses `object == "instagram"` and routes
by `entry[].id`, which for a standalone account is the Instagram professional
account id. No new endpoint is needed.

**Signature verification uses the Instagram app secret**, not the Facebook one.
`app_secret_for()` now resolves this per account.

### 4. Environment

```bash
# Second Meta app — "API setup with Instagram Login"
INSTAGRAM_APP_ID=...
INSTAGRAM_APP_SECRET=...
INSTAGRAM_REDIRECT_URI=https://your-domain/channels/instagram/callback
INSTAGRAM_VERIFY_TOKEN=pick-a-long-random-string
INSTAGRAM_GRAPH_VERSION=v23.0

# Your existing Facebook app values stay exactly as they are.
META_APP_SECRET=...
META_GRAPH_VERSION=v21.0
```

### 5. Connect an account

```
GET  /api/leadai/channels/instagram/connect     → { authorize_url, state }
```

Redirect the browser to `authorize_url`. Instagram returns to your redirect URI
with `code` and `state`. Post both back:

```
POST /api/leadai/channels/instagram/callback
     { "code": "...", "state": "..." }
```

That exchanges the code, upgrades to a long-lived token, reads the profile,
verifies the account is professional, and stores a `LeadChannelAccount` with
`LoginType="instagram"`.

`state` is what binds the callback to a company and is single-use — a replayed
callback cannot rebind an account. The callback is deliberately not
staff-token-scoped, because a browser returning from Instagram may not be
carrying one.

---

## Token expiry — the part that will page you

```
authorization code  →  short-lived token   (1 hour)
short-lived token   →  long-lived token    (60 days)
long-lived token    →  refreshed token     (another 60 days)
```

A long-lived token can only be refreshed while it is **at least 24 hours old and
not yet expired**. Miss that window and there is no recovery: the company must
go through consent again.

Sixty days is long enough that everyone will have stopped thinking about it. Run
the sweep daily:

```python
from LeadAI.services import instagram_login, db
instagram_login.refresh_all_due(db.session())
```

It refreshes at day ~50, leaving ten days of retries before the cliff. A token
that has already expired deactivates the account and sets `LastError`, so the
dashboard shows "reconnect required" rather than failing silently on the next
send.

Manual refresh: `POST /api/leadai/channels/{id}/refresh-token`.

---

## App review

**Standard Access** covers Instagram accounts you own or have added to your app
in the App Dashboard. That is enough to build, test and demo.

**Advanced Access** is required to serve other companies' accounts, and needs
full Meta App Review. `instagram_business_manage_messages` is among the slower
permissions to clear — plan several weeks, and note that a reviewer request for
changes restarts the clock. Start it before you need it.

---

## Which to use

Keep both. They serve different customers.

**Instagram Login** when the business has no Facebook Page or does not want one.
Fewer steps, less to explain, no Page admin permissions to chase. This is most
small businesses.

**Facebook Login** when they already run a Page, or you need Messenger and
Instagram in one connection, or you need Page-level features. The token does not
expire on a 60-day clock, which is one less moving part.

The code supports both simultaneously, per account.

---

## Files changed

```
LeadAI/services/instagram_login.py   NEW — OAuth, token exchange, refresh sweep
LeadAI/services/channels.py          _graph_base() per-account host routing;
                                     app_secret_for() picks the right secret
LeadAI/models_ext.py                 LoginType, AppId, TokenExpiresAt, TokenRefreshedAt
LeadAI/config.py                     instagram_* settings
LeadAI/routers/channels.py           /instagram/connect, /instagram/callback,
                                     /{id}/refresh-token
LeadAI/schemas_ext.py                InstagramCallbackIn; login_type on output
LeadAI/serializers_ext.py            expose login_type and token expiry
```

No manual migration — `ensure_columns()` adds the four columns on startup.
Existing accounts default to `LoginType="facebook"` and behave exactly as
before.

Also removed while in `channels.py`: a log line in `app_secret_for()` that wrote
the length and last four characters of the app secret to the logs at WARNING
level on every call.

---

## Troubleshooting

**"Cannot parse access token"** — wrong host. Check `LoginType` on the account
is `instagram`; if it is `facebook`, the row was created by the manual connect
endpoint rather than the OAuth flow.

**"Matching code was not found or was already used"** — the callback ran twice.
Browser retry, double-clicked button, or a proxy replaying the request. Codes
are single-use.

**Redirect URI mismatch** — must match the App Dashboard byte for byte,
including the trailing slash.

**Connect works, sending fails** — `instagram_business_manage_messages` was not
in the granted scopes. Reconnect; the account row will show `permissions` in
`MetaJson`.

**Webhook signature failures on the new account only** — the Instagram app
secret is not set, so it fell back to `META_APP_SECRET`.

**Worked for 60 days, then stopped** — the token expired and the refresh sweep
is not running.
