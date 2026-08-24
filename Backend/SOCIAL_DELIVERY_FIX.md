# Fix: staff replies never reached Facebook / Instagram

**Symptom.** Connecting a Page or Instagram account works. Inbound messages
arrive and become leads in the dashboard. But a reply typed by a human agent in
the inbox stays in the dashboard and never reaches the customer.

**Cause.** `POST /api/leadai/inbox/{id}/reply` persisted the message, updated the
conversation status, re-summarised the thread, logged the activity and broadcast
over the WebSocket — and never called the outbound send.

---

## Why it looked like it was working

The system has two kinds of channel, and only one of them needs anything done to
it after a message is saved.

**Pull channels.** The web widget polls `GET /public/chat/messages`. Writing a
row to `leadai_messages` is genuinely sufficient — the customer's browser comes
and fetches it. This is why the widget worked and why nobody noticed.

**Push channels.** WhatsApp, Messenger and Instagram have no polling. The
message has to be handed to Meta's Graph API explicitly or it simply does not
exist as far as the customer is concerned.

The delivery function was there and correct — `conversation_flow.deliver()` — but
it had exactly one caller:

```
handle_customer_turn()  →  deliver()          # AI replies: sent
inbox.reply()           →  (nothing)          # human replies: never sent
```

So AI auto-replies on Instagram went out fine, which made the channel look
healthy. The moment a human took over the conversation, it went silent.

---

## What was changed

### 1. `inbox.reply()` now delivers

`LeadAI/routers/inbox.py` calls `conversation_flow.deliver()` after the commit
and records the outcome.

**After the commit, deliberately.** Sending first and then failing to commit
would put a message on the customer's phone that does not exist in the database
— unrecoverable, and the customer's next reply would arrive against a thread
with a hole in it. This ordering can at worst leave a persisted message
undelivered, which is visible, retryable, and now explicitly recorded.

### 2. Delivery failures are recorded instead of swallowed

`deliver()` previously returned `str | None`, collapsing four different outcomes
into one falsy value: nothing to deliver, no route configured, provider rejected
it, and outside the messaging window.

For the AI path that was tolerable — the AI is not waiting for an answer. For a
human agent it is not. They typed something, they watched it appear in the
thread, and they moved on believing the customer had it.

It now returns a `DeliveryResult` with `status`, `message_id`, `error` and an
operator-facing `detail`:

| status | meaning |
|---|---|
| `sent` | provider accepted it, `message_id` returned |
| `failed` | provider rejected it, or no route / disconnected account |
| `skipped` | deliberately not sent — outside Meta's 24-hour window |
| `not_applicable` | web widget or voice; nothing to push, and that is correct |

### 3. The 24-hour window is checked before sending, not after

Meta only permits free-form replies within 24 hours of the customer's last
inbound message. Outside it the Graph API rejects the call with an error that
does not obviously say "window closed".

`deliver()` now checks first and returns `skipped` with a plain-English
explanation naming the timestamp of the last inbound message.

One detail worth noting: the check uses the timestamp of the last **customer**
message, not `conversation.LastMessageAt`. The latter is bumped by our own
outbound turns, so using it would make the window look permanently open and
every send outside it would fail at the provider with an opaque error instead of
being caught here.

### 4. Delivery state is visible in the API

Three new nullable columns on `leadai_messages`:

```
ExternalMessageId   the provider's message id
DeliveryStatus      sent | failed | skipped | null
DeliveryError       why it failed, truncated to 500 chars
```

`ensure_columns()` already exists in `LeadAI/db.py` and adds these on startup —
**no manual migration needed.**

Surfaced as `delivery_status` and `delivery_error` on `MessageOut`, so the
dashboard can render an undelivered message with a warning badge and a retry
button instead of showing it as ordinary sent text. The reply endpoint also
returns a `delivery` block on `ConversationDetail`.

Both are optional fields, so an existing frontend that ignores them is
unaffected — but rendering the badge is the whole point, so it is worth wiring
up.

These fields are blanked in the public widget view (`chat.py`). A customer must
never see our provider errors.

### 5. AI replies get the same treatment

`handle_customer_turn()` passes the outbound message row into `deliver()`, so an
AI reply that Meta rejected now shows as failed in the inbox rather than sitting
there looking delivered.

---

## Files changed

```
LeadAI/routers/inbox.py               reply() now delivers + records + surfaces
LeadAI/services/conversation_flow.py  DeliveryResult, window check, _last_customer_message_at
LeadAI/models.py                      3 columns on LeadMessage
LeadAI/schemas.py                     DeliveryOut, delivery fields on MessageOut
LeadAI/serializers.py                 populate the new fields
LeadAI/routers/chat.py                blank them in the public view
scripts/diagnose_social_delivery.py   NEW — walks the outbound chain
```

---

## Verifying on your setup

```bash
python scripts/diagnose_social_delivery.py \
    --token "$LEADAI_TOKEN" \
    --conversation <a conversation that arrived from Meta>
```

Use a thread a real customer started. A conversation you created by hand has no
`external_thread_id`, so there is no address to send to and the diagnostic will
correctly stop at step 2.

It walks six checks in order and stops at the first broken link: accounts
connected and active → conversation has a route → 24-hour window open → delivery
state on recent messages → provider health → optional live send with
`--send "test message"`.

Inbound and outbound fail for entirely different reasons, which is why "leads
arrive but replies don't send" is such a common shape of bug. Inbound needs a
webhook subscription and a valid HMAC. Outbound needs a live page token, the
right permission scope, an open window, and a stored thread id. The diagnostic
gives you one answer instead of a list of things to try.

### If it still does not send after this fix

The two causes that remain, in order of likelihood:

**The window is shut.** The customer messaged more than 24 hours ago. This is
not a bug and cannot be worked around — use an approved template through
Campaigns. The API now tells you this explicitly instead of failing silently.

**The page access token lacks the send scope.** Reading messages and sending
them need different permissions. Instagram messaging needs
`instagram_manage_messages`; Messenger needs `pages_messaging`. A token granted
before you added those scopes will happily receive webhooks and refuse to send.
Reconnect the account to mint a fresh token — `channels/accounts` will show
`last_error` populated in this case.
