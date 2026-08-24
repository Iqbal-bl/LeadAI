"""
Post a video to the Facebook Page (Graph API only, no browser).

    python tasks/graph_api/post_video_facebook.py

Edit TOPIC / INSTRUCTIONS / VIDEO_URL below before running. VIDEO_URL
must be either a direct public URL to the video file, or a local file
path — see graph_api/pages.py's create_video_post.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent.orchestrator import run_agent_task

TOPIC = "Wind Energy"
INSTRUCTIONS = "Keep the caption detailed about the topic, add a fun fact, and mention its benefits."
VIDEO_URL = "https://res.cloudinary.com/j3r6dmqk/video/upload/v1786170130/307864_medium_tokztq.mp4"  # local path also works


async def main():
    result = await run_agent_task(
        "facebook_post_video",
        {"topic": TOPIC, "instructions": INSTRUCTIONS, "video_url": VIDEO_URL},
        verbose=True,
        use_browser=False,
        recursion_limit=60,
    )
    print("\n" + "=" * 50)
    print(result)


if __name__ == "__main__":
    asyncio.run(main())