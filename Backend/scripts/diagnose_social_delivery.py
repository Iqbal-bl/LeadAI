#!/usr/bin/env python3
"""
Diagnose outbound social delivery — why a reply from the inbox does not reach
WhatsApp / Messenger / Instagram.

    python scripts/diagnose_social_delivery.py --token "$TOKEN" --conversation <id>

Inbound and outbound fail for completely different reasons, which is why "leads
arrive fine but replies don't send" is such a common shape of bug. Inbound needs
a webhook subscription and a correct HMAC. Outbound needs a valid page access
token, the right permission scope, an open messaging window, and a stored thread
id. This script walks the outbound chain in order and stops at the first broken
link, so you get one answer rather than a list of things to try.

Run it against a conversation that actually came in from Meta — one where a real
customer messaged you — not one you created by hand.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    sys.exit("Needs requests:  pip install requests")


OK = "\033[32m✓\033[0m"
BAD = "\033[31m✗\033[0m"
WARN = "\033[33m!\033[0m"


def line(mark: str, label: str, detail: str = "") -> None:
    print(f"  {mark} {label:<38}{detail}")


def head(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n" + "─" * 74)


def fail(msg: str, fix: str) -> None:
    print(f"\n\033[31mSTOPPED HERE\033[0m\n  {msg}\n\n\033[1mFix\033[0m\n  {fix}\n")
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.getenv("LEADAI_BASE", "http://localhost:6789"))
    ap.add_argument("--prefix", default=os.getenv("LEADAI_API_PREFIX", "/api/leadai"))
    ap.add_argument("--token", default=os.getenv("LEADAI_TOKEN"))
    ap.add_argument("--conversation", required=True, help="A conversation that arrived from Meta")
    ap.add_argument("--company", help="Company id, if your token spans several")
    ap.add_argument("--send", metavar="TEXT", help="Actually send this text as a test reply")
    args = ap.parse_args()

    if not args.token:
        sys.exit("Need --token or LEADAI_TOKEN")

    root = f"{args.base.rstrip('/')}{args.prefix}"
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {args.token}"
    params = {"company_id": args.company} if args.company else {}

    def get(path, **kw):
        r = s.get(f"{root}{path}", params={**params, **kw.pop("params", {})}, timeout=30)
        if r.status_code >= 400:
            fail(
                f"GET {path} returned {r.status_code}: {r.text[:300]}",
                "Check the token, and that its email holds a role for this company "
                "(GET /api/leadai/access/me).",
            )
        return r.json()

    # ---------------------------------------------------------------- 1
    head("1. Channel accounts connected")
    accounts = get("/channels/accounts")
    if not accounts:
        fail(
            "No channel accounts are connected.",
            "Connect a Facebook Page or Instagram account under Channels first.",
        )
    social = [a for a in accounts if a.get("channel") in ("whatsapp", "messenger", "instagram")]
    for a in accounts:
        mark = OK if a.get("is_active") else BAD
        line(mark, f"{a.get('channel')}: {a.get('name') or a.get('external_id')}",
             "active" if a.get("is_active") else "INACTIVE")
        if a.get("last_error"):
            line(WARN, "  last error", str(a["last_error"])[:70])
    if not any(a.get("is_active") for a in social):
        fail(
            "Every social account is inactive.",
            "Reconnect the account under Channels. An expired page token deactivates it.",
        )

    # ---------------------------------------------------------------- 2
    head("2. Conversation routing")
    conv = get(f"/inbox/{args.conversation}")
    channel = conv.get("channel")
    line(OK if channel else BAD, "channel", channel or "(none)")

    if channel in ("web", "voice"):
        fail(
            f"This conversation is on '{channel}', which is pull-based or spoken — "
            "there is nothing to push.",
            "Re-run against a conversation that arrived from Meta. In the inbox, "
            "filter by channel = instagram or messenger.",
        )

    acct_id = conv.get("channel_account_id")
    thread_id = conv.get("external_thread_id")
    line(OK if acct_id else BAD, "channel_account_id", acct_id or "(missing)")
    line(OK if thread_id else BAD, "external_thread_id", thread_id or "(missing)")

    if not acct_id or not thread_id:
        fail(
            "The conversation has no route back to the customer.",
            "This happens when a conversation was created manually or by an import "
            "rather than by an inbound webhook. Only threads created by a real "
            "inbound message carry the sender's thread id, and without it there is "
            "no address to send to.",
        )

    # ---------------------------------------------------------------- 3
    head("3. Meta 24-hour messaging window")
    msgs = conv.get("messages") or []
    inbound = [m for m in msgs if m.get("sender") == "customer" and m.get("created_at")]
    if not inbound:
        fail(
            "This customer has never sent an inbound message.",
            "Meta forbids free-form messages to someone who has not messaged you. "
            "Use an approved template (Campaigns) instead of an inbox reply.",
        )

    last = inbound[-1]["created_at"]
    try:
        ts = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
    except Exception:
        hours = -1

    if 0 <= hours < 24:
        line(OK, "window open", f"last inbound {hours:.1f}h ago")
    elif hours >= 24:
        line(BAD, "window CLOSED", f"last inbound {hours:.1f}h ago")
        fail(
            f"The customer last messaged {hours:.0f} hours ago, so the 24-hour window is shut.",
            "Meta rejects free-form replies outside 24h. This is the single most common "
            "cause of 'my reply didn't send' on a thread that used to work. Send an "
            "approved template, or wait for the customer to message again. After the "
            "fix in this release, the API tells you this instead of failing silently.",
        )
    else:
        line(WARN, "window", "could not parse the timestamp")

    # ---------------------------------------------------------------- 4
    head("4. Delivery state on recent outbound messages")
    outbound = [m for m in msgs if m.get("sender") in ("ai", "agent")][-6:]
    if not outbound:
        line(WARN, "no outbound messages yet", "")
    for m in outbound:
        st = m.get("delivery_status")
        mark = {"sent": OK, "failed": BAD, "skipped": WARN}.get(st, WARN)
        label = f"{m.get('sender')}: {str(m.get('content'))[:34]}"
        line(mark, label, st or "(no delivery record)")
        if m.get("delivery_error"):
            line(" ", "  error", str(m["delivery_error"])[:66])

    if outbound and all(m.get("delivery_status") is None for m in outbound):
        line(WARN, "", "")
        print(
            "  Every outbound message predates the delivery-tracking fix, or the\n"
            "  server is running the old code where inbox replies were never sent.\n"
            "  Confirm the new build is deployed, then send a fresh test reply."
        )

    # ---------------------------------------------------------------- 5
    head("5. Provider health")
    try:
        h = get("/channels/health")
        for k, v in (h or {}).items():
            line(OK if v else WARN, str(k), str(v)[:50])
    except SystemExit:
        raise
    except Exception as exc:
        line(WARN, "health endpoint unavailable", str(exc)[:50])

    # ---------------------------------------------------------------- 6
    if args.send:
        head("6. Live send test")
        r = s.post(
            f"{root}/inbox/{args.conversation}/reply",
            params=params,
            json={"message": args.send},
            timeout=60,
        )
        if r.status_code >= 400:
            fail(f"Reply returned {r.status_code}: {r.text[:400]}", "See the error above.")
        d = (r.json() or {}).get("delivery") or {}
        st = d.get("status")
        if st == "sent":
            line(OK, "DELIVERED", f"provider id {d.get('message_id')}")
            print("\n  Check the customer's device — the message should be there.\n")
        elif st == "not_applicable":
            line(WARN, "not pushed", d.get("detail") or "")
        else:
            line(BAD, f"NOT DELIVERED ({st})", d.get("error") or "")
            print(f"\n  {d.get('detail') or ''}\n")
    else:
        head("Done")
        print(
            "  Every check above passed. Re-run with --send \"test message\" to\n"
            "  attempt a real delivery and see the provider's answer.\n"
        )


if __name__ == "__main__":
    main()
