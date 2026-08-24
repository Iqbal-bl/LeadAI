"""
LEGACY — personal Facebook PROFILE browser automation, not the Page.
The Page runs on Graph API now (see graph_api/, scheduler.py). This
stays for personal-profile testing/reference only.

Example workflow: a full Facebook "daily routine" made of several small,
independently-bounded skills chained together — not one giant task.

    python workflows/facebook_daily_routine.py

Each step below is its own fresh run_agent_task() call (fresh message
history, own recursion budget), but they all share ONE BrowserController —
so the login/session carries over between steps without re-authenticating,
while the LLM's memory of "what have I done so far" never has to span the
whole routine. If step 3 misbehaves, steps 1-2's results are unaffected and
still sitting in `results`, and you can see exactly which step failed.

Requirements: a saved Facebook session first —
    python save_login_session.py --site facebook --url https://facebook.com
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.orchestrator import run_agent_task
from browser.browser import BrowserController

# --- Development-mode settings -----------------------------------------
HEADLESS = False
VERBOSE = True
SHOW_DEBUG_BOXES = True
USER_DATA_DIR = "sessions_profiles/facebook"
# ------------------------------------------------------------------------

RESULTS_LOG = Path("workflow_results.jsonl")


async def run_facebook_daily_routine():
    if not Path(USER_DATA_DIR).exists():
        print(f"[error] Profile directory '{USER_DATA_DIR}' not found.")
        print("Run: python save_login_session.py --site facebook --url https://facebook.com")
        return

    browser = BrowserController(user_data_dir=USER_DATA_DIR, show_debug_boxes=SHOW_DEBUG_BOXES)
    await browser.start(headless=HEADLESS)

    results = []

    try:
        # --- Step 1: like a few feed posts ---
        results.append(await run_agent_task(
            "facebook_like_feed_posts",
            {"count": 3, "topic_clause": ""},
            browser=browser, verbose=VERBOSE, recursion_limit=30,
        ))

        # --- Step 2: send a couple of friend requests ---
        results.append(await run_agent_task(
            "facebook_send_friend_requests",
            {"count": 2},
            browser=browser, verbose=VERBOSE, recursion_limit=30,
        ))

        # --- Step 3: accept pending friend requests ---
        results.append(await run_agent_task(
            "facebook_accept_friend_requests",
            {"count": 5},
            browser=browser, verbose=VERBOSE, recursion_limit=30,
        ))

        # --- Step 4: reply to unread messages ---
        results.append(await run_agent_task(
            "facebook_reply_messenger",
            {"count": 3, "reply_instructions": "Thanks for reaching out! I'll get back to you properly soon."},
            browser=browser, verbose=VERBOSE, recursion_limit=40,
        ))

        # --- Step 5: create a post (with optional media) ---
        media_path = Path("media/example_photo.jpg").resolve()
        media_clause = (
            f"Then click the 'Photo/video' button in the composer to open "
            f"a file picker. The actual file input will NOT appear in the "
            f"numbered element list (it's hidden until triggered) — do not "
            f"try to find or click an index for it. Instead, immediately "
            f"call the upload_file tool with file_path=\"{media_path}\" and "
            f"leave index unset. Wait for the thumbnail preview to appear "
            f"before continuing. "
            if media_path.exists() else ""
        )
        results.append(await run_agent_task(
            "facebook_create_post",
            {
                "message": "AI in medical research is revolutionizing how we treat diseases.",
                "media_clause": media_clause,
            },
            browser=browser, verbose=VERBOSE, recursion_limit=30,
        ))
    finally:
        await browser.stop()

    summary = {
        "workflow": "facebook_daily_routine",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "steps": results,
        "overall_success": all(r.get("success") for r in results),
    }

    with RESULTS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")

    print("\n" + "=" * 60)
    for i, r in enumerate(results, 1):
        status = "OK" if r.get("success") else "FAILED"
        print(f"Step {i} [{r.get('task')}]: {status} — {r.get('result') or r.get('message')}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_facebook_daily_routine())
