# tools/

One-off maintenance scripts, run by hand from the `Backend/` folder. They are not imported by the app.

| File | What it does |
|---|---|
| `backfill_profiles_and_users.py` | Fills in missing Messenger / Instagram contact names and reports identity-server users with no local directory row. `--dry-run` first, then `--apply` |
| `diagnose_social_delivery.py` | Walks the outbound chain to find why an inbox reply did not reach WhatsApp / Messenger / Instagram |
| `normalize_conversations_sql.py` | Rewrites a MySQL dump of the `conversations` table into a clean insert file |
