# Meta Graph API Setup

Complete walkthrough for getting every credential in `.env`'s Graph API
section, from zero. Do these in order — each step needs the one before it.

## 1. Create a Meta Developer account

Go to https://developers.facebook.com, log in with your Facebook account,
click "Get Started" if you haven't registered as a developer before.

## 2. Create an app

App Dashboard → **Create App** → choose **"Business"** as the type. Give
it any name. Once created, note down (Settings → Basic):

- **App ID** → `FB_APP_ID`
- **App Secret** → `FB_APP_SECRET`

## 3. Make sure you have a Facebook Page (not a personal profile)

You must be an Admin on it. Create one at facebook.com → Pages → Create
Page if you don't have one yet.

## 4. Link an Instagram Business/Creator account (only if using Instagram)

The Instagram account must be Business or Creator type, not personal —
convert via the Instagram app: Settings → Account type and tools →
Switch to professional account. Then link it to your Page: Meta Business
Suite → Settings → Accounts → Instagram accounts → Connect account (or
from the Instagram app: Settings → Linked accounts).

If this fails with a broken-looking dialog in Business Suite, try the
Instagram app's own flow instead — it tends to be more reliable.

## 5. Add products to your app

App Dashboard → **Add Product** → add **Facebook Login** (needed to
generate tokens) and **Instagram Graph API** if using Instagram. To read
or send Instagram DMs, also add **Messenger API for Instagram** (it may be
presented as the Messenger product with Instagram messaging setup) and
complete its Instagram-account connection.

## 6. Generate a token via Graph API Explorer

Go to https://developers.facebook.com/tools/explorer/.

1. Top-right dropdowns: select your app, select "User Token".
2. Click **Get Token → Get User Access Token**.
3. Tick only the permissions you actually need:
   `pages_show_list`, `pages_manage_posts`, `pages_read_engagement`,
   `pages_manage_engagement`, `pages_messaging`, `instagram_basic`,
   `instagram_content_publish`, `instagram_manage_messages`,
   `leads_retrieval`.
4. Generate — this only works for your own account until App Review
   (fine for development/testing).

This token expires in ~1 hour — just a stepping stone for the next step.

## 7. Exchange for a long-lived User Token (60 days)

```bash
curl -i -X GET "https://graph.facebook.com/v25.0/oauth/access_token?grant_type=fb_exchange_token&client_id=YOUR_APP_ID&client_secret=YOUR_APP_SECRET&fb_exchange_token=SHORT_LIVED_TOKEN_FROM_STEP_6"
```

**Windows PowerShell note:** `curl` there is aliased to `Invoke-WebRequest`,
which doesn't understand `-i -X GET`. Either add `.exe` (`curl.exe ...`) or
use the native equivalent:
```powershell
Invoke-RestMethod -Uri "https://graph.facebook.com/v25.0/oauth/access_token?grant_type=fb_exchange_token&client_id=YOUR_APP_ID&client_secret=YOUR_APP_SECRET&fb_exchange_token=SHORT_LIVED_TOKEN"
```

Grab `access_token` from the response.

## 8. Get your Page Access Token and Page ID

```bash
curl -i -X GET "https://graph.facebook.com/v25.0/me/accounts?access_token=LONG_LIVED_USER_TOKEN_FROM_STEP_7"
```

Returns a `data` array — one entry per Page you admin. From your Page's
entry:
- top-level `"id"` → `FB_PAGE_ID`
- `"access_token"` (inside that same entry) → `FB_PAGE_ACCESS_TOKEN`

This Page token effectively doesn't expire as long as the underlying
setup (App, user token) stays valid. Check `"tasks"` in the response
includes what you need (`MANAGE`, `CREATE_CONTENT`, `MESSAGING`, etc.).

## 9. Get your Instagram Business Account ID (only if using Instagram)

```bash
curl -i -X GET "https://graph.facebook.com/v25.0/YOUR_PAGE_ID?fields=instagram_business_account&access_token=YOUR_PAGE_ACCESS_TOKEN"
```

If linking (step 4) succeeded, the response includes
`"instagram_business_account": {"id": "..."}`. If it's missing entirely,
the linking step didn't go through — redo step 4.

That `id` → `IG_BUSINESS_ACCOUNT_ID`.

## 10. Fill in `.env`

Copy `.env.example` to `.env` and fill in everything from steps 2, 8, 9.

## 11. Test it

```bash
python -m graph_api.smoke_test
```
with only `create_text_post` uncommented first. Check your Page's feed
to confirm.

## Common issues

- **App Review**: anything beyond your own test accounts needs it —
  per-permission use-case write-up, privacy policy URL, a screencast.
  Budget real weeks for messaging permissions especially.
- **API version deprecation**: Meta deprecates versions roughly every 2
  years. If you see `x-ad-api-version-warning` in a response header,
  update `GRAPH_API_VERSION` in `graph_api/client.py`.
- **Token expiry**: if you start getting auth errors weeks/months from
  now, redo steps 6-8 — don't assume the code broke.
- **Instagram publishing needs a PUBLIC image URL** — no direct local
  file upload, unlike Facebook Page photos. See `graph_api/instagram.py`.
