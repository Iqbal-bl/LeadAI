"""
Task 2: post a local photo/video to Facebook along with a text caption.
Run this file directly — no arguments, no typing a prompt.

    python tasks/task_2_facebook_post_with_media.py

Requirements: a saved Facebook session first —
    python save_login_session.py --site facebook --url https://facebook.com

How it works: this reuses the existing "facebook_create_post" prompt
template in agent/registry.py, which already has a {media_clause}
placeholder. We build that clause here (or leave it empty for a
text-only post) and pass the absolute file path through — the agent's
`upload_file` tool attaches whatever local file path it's given to the
first file-input element it finds on the page.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent.orchestrator import run_agent_task

# --- Development-mode settings -----------------------------------------
HEADLESS = False  # legacy personal-profile browser task
VERBOSE = True
SHOW_DEBUG_BOXES = True
USER_DATA_DIR = "sessions_profiles/facebook"
# ------------------------------------------------------------------------

# --- What to post ---------------------------------------------------------
MESSAGE = "AI in medical research is revolutionizing how we treat diseases."

# Path to the photo/video on disk. Relative paths are resolved against this
# script's location, then converted to an absolute path — upload_file needs
# an absolute path since the browser process's cwd may differ from yours.
MEDIA_PATH = "media/example_photo.jpg"  # set to None for a text-only post
# ----------------------------------------------------------------------------


def _build_media_clause(media_path: str | None) -> str:
    if not media_path:
        return ""
    resolved = (Path(__file__).resolve().parent.parent.parent / media_path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(
            f"MEDIA_PATH does not exist: {resolved}\n"
            "Update MEDIA_PATH in this script to point at a real file."
        )
    return (
        f"Then click the 'Photo/video' button in the composer to open a "
        f"file picker. The actual file input element will NOT appear in "
        f"the numbered element list (it's hidden until triggered) — do "
        f"not try to find or click an index for it. Instead, immediately "
        f"call the upload_file tool with file_path=\"{resolved}\" and "
        f"leave index unset, so it auto-attaches to the file input. Wait "
        f"for the thumbnail preview to appear in the composer before "
        f"continuing. "
    )


async def main():
    media_clause = _build_media_clause(MEDIA_PATH)

    result = await run_agent_task(
        "facebook_create_post",
        {"message": MESSAGE, "media_clause": media_clause},
        headless=HEADLESS,
        verbose=VERBOSE,
        user_data_dir=USER_DATA_DIR,
        show_debug_boxes=SHOW_DEBUG_BOXES,
        recursion_limit=30,
    )
    print("\n" + "=" * 50)
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
