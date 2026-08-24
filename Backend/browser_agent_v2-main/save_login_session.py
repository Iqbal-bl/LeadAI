"""
Log into a site once, manually, using a real persistent Chrome profile —
then reuse that same profile on every future run, so the agent starts up
already logged in. This is the recommended approach for strict,
bot-detecting sites (Google/YouTube, LinkedIn, Facebook).

Usage:
    python save_login_session.py --site linkedin --url https://www.linkedin.com/login
    python save_login_session.py --site youtube --url https://accounts.google.com
    python save_login_session.py --site facebook --url https://www.facebook.com/login

Why a persistent Chrome profile instead of exporting cookies:
Restoring cookies into a fresh automated browser (the storage_state
approach) can still get flagged by strict sites even with valid cookies —
that's the "browser or app may not be secure" block. A persistent profile
avoids this because a human does the actual logging in, once, inside a
profile that looks and behaves like a normal everyday Chrome install; there
is no separate "automation just logged in" moment for the site to catch.

Requirements:
    playwright install chrome     # installs real Chrome, if not already present

What happens:
    1. A real Chrome window opens at the URL you gave, using a dedicated
       profile folder just for this site (sessions_profiles/<site>/).
    2. You log in by hand — password, 2FA, CAPTCHA, whatever it needs.
    3. Once logged in, come back here and press Enter — the profile folder
       now holds your session permanently. Nothing else to export/save.

Any task can reuse it — pass user_data_dir="sessions_profiles/<site>" when
building a BrowserController (see tasks/task_1.py for an example).

SECURITY NOTE: sessions_profiles/ contains a full logged-in browser
profile — treat it exactly like a password. Never commit it to version
control (already covered in .gitignore). If a profile stops working (site
logged you out), just re-run this script for that site.

A PRACTICAL CAVEAT: some sites' Terms of Service restrict automated
interaction with logged-in accounts (this varies by site and by what the
automation actually does — reading vs. posting/messaging/connecting).
Worth checking the specific site's terms for what you plan to automate,
since account restriction/suspension is a real risk on platforms like
LinkedIn if automated actions look scripted.
"""

import argparse
import asyncio
from pathlib import Path

from browser.session import BrowserSession

PROFILES_DIR = Path("sessions_profiles")


async def save_login_session(site: str, url: str, browser_engine: str = "chromium"):
    profile_dir = PROFILES_DIR / site

    session = BrowserSession(user_data_dir=str(profile_dir), browser_engine=browser_engine)
    await session.start(headless=False)  # must be visible — you're logging in
    await session.page.goto(url, wait_until="domcontentloaded")

    print(f"\nA browser window has opened at:\n  {url}")
    print("Log in manually now (password, 2FA, CAPTCHA — whatever it needs).")
    input("Once you're fully logged in, come back here and press Enter... ")

    print(f"\nSession saved automatically to {profile_dir}/")
    print("Keep this folder private — it contains a live logged-in browser profile.")

    await session.stop()


def main():
    parser = argparse.ArgumentParser(
        description="Log into a site once manually using a persistent browser profile."
    )
    parser.add_argument("--site", required=True, help="Short name for this profile, e.g. 'linkedin'")
    parser.add_argument("--url", required=True, help="Login page URL to open")
    parser.add_argument("--engine", choices=["chromium", "firefox"], default="firefox")
    args = parser.parse_args()

    asyncio.run(save_login_session(args.site, args.url, browser_engine=args.engine))


if __name__ == "__main__":
    main()
