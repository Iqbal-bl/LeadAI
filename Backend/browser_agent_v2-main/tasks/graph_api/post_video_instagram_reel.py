"""
Publish a Reel to Instagram (Graph API only, no browser).

    python tasks/graph_api/post_video_instagram_reel.py

Edit TOPIC / INSTRUCTIONS / VIDEO_URL below before running. VIDEO_URL
MUST be a direct, publicly reachable link to the actual video file
(e.g. ending in .mp4) — a page URL that just displays/hosts the video
(like a viewer/landing page) will fail with a media-container error.
Meta's server fetches this URL itself; it needs the raw file, not a
webpage. Video processing is slow — this can take a couple minutes.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent.orchestrator import run_agent_task

TOPIC = "Wind Energy"
INSTRUCTIONS = "Keep the caption detailed about the topic, add a fun fact, and mention its benefits."
VIDEO_URL = "https://res.cloudinary.com/j3r6dmqk/video/upload/v1786170130/307864_medium_tokztq.mp4"  # must be a DIRECT file link


async def main():
    result = await run_agent_task(
        "instagram_post_reel",
        {"topic": TOPIC, "instructions": INSTRUCTIONS, "video_url": VIDEO_URL},
        verbose=True,
        use_browser=False,
        recursion_limit=60,
    )
    print("\n" + "=" * 50)
    print(result)


if __name__ == "__main__":
    asyncio.run(main())