#!/usr/bin/env python3
"""
Backfill data that the fixes in this release will produce going forward, but
which existing rows are missing.

    python scripts/backfill_profiles_and_users.py --dry-run    # show, change nothing
    python scripts/backfill_profiles_and_users.py --apply

Two independent backfills; run either or both:

  --profiles   Resolve display names for Messenger and Instagram contacts whose
               rows hold only a numeric PSID / IGSID. One Graph call per contact.

  --users      Report identity-server users who have no local directory row.
               These are users created through /user-management/create before
               the fix, when the local record was only written if an explicit
               role was passed. They exist and can log in; the dashboard simply
               cannot see them.

Run this from the project root with the app's .env loaded — it uses the same
engine and models the application does.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("backfill")


def backfill_profiles(db, apply: bool, limit: int, sleep: float) -> None:
    from LeadAI.models import LeadChannelAccount, LeadChannelIdentity, LeadCustomer
    from LeadAI.security import decrypt_pii, encrypt_pii
    from LeadAI.services import channels as ch

    print("\n\033[1mProfile names\033[0m\n" + "─" * 70)

    # Only Messenger and Instagram. WhatsApp names arrive in the webhook, so a
    # blank one there means the customer has no profile name set — there is
    # nothing to fetch and retrying would burn quota for no result.
    rows = (
        db.query(LeadChannelIdentity)
        .filter(
            LeadChannelIdentity.IsDeleted == False,  # noqa: E712
            LeadChannelIdentity.Channel.in_(("messenger", "instagram")),
            LeadChannelIdentity.ProfileName.is_(None),
        )
        .limit(limit)
        .all()
    )

    if not rows:
        print("  Nothing to do — every social identity already has a name.")
        return

    print(f"  {len(rows)} identit{'y' if len(rows) == 1 else 'ies'} with no profile name.\n")

    resolved = failed = 0
    for row in rows:
        account = db.get(LeadChannelAccount, row.ChannelAccountId)
        if account is None or not account.IsActive:
            print(f"  \033[33mskip\033[0m  {row.Channel:<10} {row.ExternalUserId:<20} account inactive")
            failed += 1
            continue

        profile = ch.fetch_profile(account, row.Channel, row.ExternalUserId)
        if not profile:
            print(f"  \033[31mmiss\033[0m  {row.Channel:<10} {row.ExternalUserId:<20} no profile returned")
            failed += 1
            time.sleep(sleep)
            continue

        name = profile.get("name") or profile.get("username")
        handle = profile.get("handle")
        print(f"  \033[32m ok \033[0m  {row.Channel:<10} {row.ExternalUserId:<20} -> {handle or name}")
        resolved += 1

        if apply:
            row.ProfileName = name
            customer = db.get(LeadCustomer, row.CustomerId)
            if customer is not None:
                if not customer.DisplayName:
                    customer.DisplayName = name
                # Replace a stored raw IGSID with the readable handle. Compare
                # against the decrypted value, since the ciphertext differs on
                # every encryption even for identical plaintext.
                if row.Channel == "instagram" and handle:
                    current = decrypt_pii(customer.InstagramEnc or "")
                    if not current or current == row.ExternalUserId:
                        customer.InstagramEnc = encrypt_pii(handle)

        time.sleep(sleep)

    if apply:
        db.commit()
        print(f"\n  Committed. resolved={resolved} failed={failed}")
    else:
        print(f"\n  DRY RUN. would resolve={resolved}, would fail={failed}")

    if failed:
        print(
            "\n  Misses are usually one of:\n"
            "    - the page token lacks instagram_manage_messages / pages_messaging\n"
            "    - the contact deleted their account or blocked the Page\n"
            "    - the conversation predates the account's current token\n"
            "  Reconnecting the account under Channels fixes the first."
        )


def backfill_users(db, apply: bool) -> None:
    from Domain.models import Client
    from LeadAI.models import ROLE_EMPLOYEE, LeadUserRole

    print("\n\033[1mUser directory\033[0m\n" + "─" * 70)

    # We cannot enumerate the identity server from here — it owns that list and
    # exposes no bulk read in this integration. What we CAN do is surface the
    # gap: every company, how many directory rows it has, and which companies
    # have none at all. A company with zero rows almost certainly has users in
    # the identity server that were created before the fix.
    companies = (
        db.query(Client)
        .filter(Client.IsDeleted == False)  # noqa: E712
        .order_by(Client.Name.asc())
        .all()
    )

    empty = []
    for company in companies:
        count = (
            db.query(LeadUserRole)
            .filter(
                LeadUserRole.ClientId == company.Id,
                LeadUserRole.IsDeleted == False,  # noqa: E712
            )
            .count()
        )
        mark = "\033[32m ok \033[0m" if count else "\033[33mnone\033[0m"
        print(f"  {mark}  {company.Name[:44]:<46} {count} user(s)")
        if not count:
            empty.append(company)

    if not empty:
        print("\n  Every company has at least one directory entry.")
        return

    print(
        f"\n  {len(empty)} compan{'y has' if len(empty) == 1 else 'ies have'} no users on record.\n"
        "\n  These are not necessarily broken — a company created but never staffed\n"
        "  looks identical. But if you know people can log in against one of these,\n"
        "  they were created before the fix and need a directory row.\n"
        "\n  Re-create the grant without touching the identity server:\n"
        "    POST /api/leadai/access/roles\n"
        f"      {{\"email\": \"person@company.com\", \"role\": \"{ROLE_EMPLOYEE}\", \"client_id\": \"<id>\"}}\n"
        "\n  That writes only leadai_user_roles. Their identity-server account,\n"
        "  password and login are untouched."
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write changes (default is dry run)")
    ap.add_argument("--profiles", action="store_true", help="Backfill social profile names")
    ap.add_argument("--users", action="store_true", help="Report missing directory rows")
    ap.add_argument("--limit", type=int, default=500, help="Max identities per run")
    ap.add_argument("--sleep", type=float, default=0.3, help="Seconds between Graph calls")
    args = ap.parse_args()

    if not args.profiles and not args.users:
        args.profiles = args.users = True

    from dotenv import load_dotenv

    load_dotenv()

    from LeadAI.db import session

    db = session()
    try:
        if not args.apply:
            print("\n\033[33mDRY RUN — nothing will be written. Re-run with --apply.\033[0m")
        if args.profiles:
            backfill_profiles(db, args.apply, args.limit, args.sleep)
        if args.users:
            backfill_users(db, args.apply)
    finally:
        db.close()
    print()


if __name__ == "__main__":
    main()
