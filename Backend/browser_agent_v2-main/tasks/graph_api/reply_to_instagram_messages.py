"""
Reply to every unread Instagram DM conversation (Graph API only, no browser).

    python tasks/graph_api/reply_to_instagram_messages.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent.orchestrator import run_agent_task


async def main():
    result = await run_agent_task(
        "instagram_reply_to_messages", {}, verbose=True, use_browser=False, recursion_limit=60
    )
    print("\n" + "=" * 50)
    print(result)


if __name__ == "__main__":
    asyncio.run(main())