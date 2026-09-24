# Fix: social profile names, and the missing user directory

Two unrelated reports, two unrelated causes.

---

## Part 1 — Revealing a contact showed a numeric id, not a name

**Symptom.** Reveal a Facebook or Instagram lead's contact details and you get
something like `7891234567890123` instead of a person.

**Cause.** The name was never fetched. This is not a bug in the reveal endpoint,
which was faithfully returning what it had.

### Why WhatsApp worked and Messenger/Instagram did not

The three channels deliver inbound events differently:

**WhatsApp** puts the sender's name in the webhook body, in a `contacts` array
alongside the messages. `channels.normalise()` already reads it:

```python
profiles = {c.get("wa_id"): (c.get("profile") or {}).get("name")
            for c in value.get("contacts", []) or []}
```

**Messenger and Instagram send no name at all.** The webhook carries an opaque
PSID or IGSID — a long numeric string — and nothing else about the person. The
name and handle live behind a *separate* Graph API call that nobody was making.

So `LeadChannelIdentity.ProfileName` stayed null, `LeadCustomer.DisplayName`
stayed null, and `LeadCustomer.InstagramEnc` was populated with the raw IGSID:

```python
InstagramEnc=encrypt_pii(
    external_user_id if account.Channel == "instagram" else None
),
```

That line is the direct source of the number you were seeing.

### What was changed

**`channels.fetch_profile()`** — new. Calls
`GET /{PSID}?fields=name,username,profile_pic` with the page token and returns
whichever fields the platform gave, plus a display-ready `handle`
(`@username` on Instagram, the person's name on Messenger).

Returns `{}` on any failure. A missing display name must never break message
handling, which is the actual job.

**`resolve_social_conversation()`** calls it when no name is known — so **one
Graph call per new social contact, not one per message.** An existing identity
that already has a name costs nothing.

**Storage changed** so Instagram stores the readable handle rather than the raw
id, falling back to the id only when the lookup failed. Existing customers get
their name and handle backfilled on their next message.

**`ContactReveal` gained `display_name` and `social_identities`**, the latter a
list of `SocialIdentityOut` with `channel`, `handle`, `profile_name`,
`external_user_id`, `profile_url` and `opted_out`. The raw id is still returned
— it is what you quote to Meta support — but it is no longer the only thing
there. `profile_url` gives a clickable `instagram.com/username` or `m.me/PSID`.

A note on privacy: Meta only returns a profile for someone who has messaged the
Page, which is the same condition under which we hold a conversation with them
at all. There is no way to enumerate strangers with this.

### Existing rows

New conversations resolve automatically. For history:

```bash
python scripts/backfill_profiles_and_users.py --profiles --dry-run
python scripts/backfill_profiles_and_users.py --profiles --apply
```

Rate-limited (default 0.3s between calls) and capped at 500 identities per run.

It deliberately skips WhatsApp: names arrive in the webhook there, so a blank
one means the customer has no profile name set. There is nothing to fetch and
retrying would burn quota for no result.

**If lookups come back empty**, it is nearly always token scope. Reading
messages and reading profiles need different permissions — Instagram needs
`instagram_manage_messages`, Messenger needs `pages_messaging`. A token minted
before those scopes were added receives webhooks perfectly well and returns
nothing on a profile lookup. Reconnect the account to mint a fresh one.

---

## Part 2 — Users created in the identity server left no local record

**Symptom.** Create a user, and the company details screen cannot show who
belongs to that company.

**Cause.** One conditional. In `user_management.create_user()`, the entire local
write — the `leadai_user_roles` row, the activity log, the commit — was wrapped
in:

```python
if payload.role:
```

`role` is optional in `UserManagementCreate`. Create a user without one and the
identity server got the account while this application recorded **nothing**. The
user could log in and was invisible to the dashboard.

There was also no endpoint that answered "who is in company X". The existing
`/user-management/employees` returns members of the *caller's own* company via
`resolve_scope`, which is right for a company admin managing their team but
cannot serve a platform-admin company-details screen.

### What was changed

**Every user created through the endpoint now gets a directory row.** A user
created with no explicit role is recorded as an employee — the least privilege
any member holds. Recording someone is a directory concern; granting them
capability is a separate decision made by `ROLE_PERMISSIONS`. Conflating the two
is what caused the gap.

The activity log entry records `role_explicit`, so you can tell a deliberate
employee grant from a defaulted one.

**New: `GET /api/leadai/companies/{id}/users`** returning `CompanyUsersOut`:

```json
{
  "company_id": "...",
  "company_name": "Nexa Finserv",
  "total": 4,
  "admins":    [{ "email": "...", "name": "...", "role": "company_admin", ... }],
  "managers":  [...],
  "employees": [...],
  "users":     [ ...all of them, flat... ]
}
```

Grouped *and* flat, because the question a details screen asks is "who is the
admin here, and who else is there" — not "give me an unordered list I have to
bucket myself".

Users with a **global** role (`ClientId` NULL) are included and flagged
`is_global: true`. A platform admin can act on a company without being a member
of it, and hiding that from the screen would misrepresent who has access.

Access is scoped: a company admin can only read their own company; a platform
admin can read any.

### Existing users

```bash
python scripts/backfill_profiles_and_users.py --users
```

This reports rather than repairs, and the distinction matters. The identity
server owns the user list and exposes no bulk read in this integration, so
nothing here can enumerate the accounts that exist. What it can do is show every
company and its directory count, and flag the ones with zero.

A company with no users is not necessarily broken — one created but never
staffed looks identical. But if you know people log in against one of these,
they predate the fix.

Repair is one call per person, and it does **not** touch the identity server:

```
POST /api/leadai/access/roles
  {"email": "person@company.com", "role": "employee", "client_id": "<id>"}
```

That writes only `leadai_user_roles`. Their account, password and login are
untouched — you are filling in the local record that should have been written at
creation time.

---

## Files changed

```
LeadAI/services/channels.py           NEW fetch_profile()
LeadAI/services/conversation_flow.py  profile lookup + handle storage + backfill
LeadAI/routers/inbox.py               reveal returns names and social identities
LeadAI/routers/user_management.py     always write a directory row
LeadAI/routers/companies.py           NEW GET /{id}/users
LeadAI/schemas.py                     SocialIdentityOut, CompanyUserOut, CompanyUsersOut
scripts/backfill_profiles_and_users.py  NEW
```

No migration needed — no new columns. Both fixes use tables and columns that
already exist; they were simply never populated.

## Frontend work this enables

**Inbox / reveal.** Show `display_name` as the heading and each
`social_identities[].handle` as the contact line, with `profile_url` as a link.
Keep `external_user_id` available but secondary — small text, or behind a copy
button for support tickets.

**Company details.** Call `GET /companies/{id}/users` and render `admins`,
`managers` and `employees` as sections. Badge anyone with `is_global: true`
distinctly so a company admin understands why a platform admin appears in their
list.
