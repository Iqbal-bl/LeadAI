"""
Task 1: YouTube video metrics for the query "python".
Run this file directly — no arguments, no typing a prompt.

    python tasks/task_1.py

To make a new task: copy this file (task_2.py, task_3.py, ...), change
TASK_NAME/TASK_ARGS, and add the matching entry in agent/registry.py if
it's a genuinely new kind of task rather than a new query for this one.
"""

import asyncio
import sys
from pathlib import Path

# Let this script import from the project root even when run directly
# from inside tasks/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.orchestrator import run_agent_task

# --- Development-mode settings -----------------------------------------
# While building/debugging: see the browser, see every step.
# Before deploying for unattended/24-7 use: flip both.
HEADLESS = False
VERBOSE = True

# Draws live numbered boxes directly on the page in the actual browser
# window, so you can watch what the agent's element-indexing sees.
# Nothing is saved or sent anywhere — it's purely visual, and independent
# of ENABLE_VISION in agent/graph.py (which controls whether a screenshot
# is sent to the LLM). Requires HEADLESS = False to see anything.
SHOW_DEBUG_BOXES = True
# ------------------------------------------------------------------------

TASK_NAME = "youtube_video_metrics"
TASK_ARGS = {"query": "python"}

# Optional persistent login (see save_login_session.py):
#   - user_data_dir: RECOMMENDED for Google/LinkedIn/Facebook — a real
#     Chrome profile, e.g. "sessions_profiles/youtube"
#   - storage_state_path: simpler cookie-export option for less strict sites
USER_DATA_DIR = None
STORAGE_STATE_PATH = None


async def main():
    result = await run_agent_task(
        TASK_NAME,
        TASK_ARGS,
        headless=HEADLESS,
        verbose=VERBOSE,
        storage_state_path=STORAGE_STATE_PATH,
        user_data_dir=USER_DATA_DIR,
        show_debug_boxes=SHOW_DEBUG_BOXES,
    )
    print("\n" + "=" * 50)
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
