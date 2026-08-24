"""
Post the next topic from the DB-backed content queue (Graph API only, no
browser). Same job the scheduler runs automatically — this is for a
manual one-off run. Add topics via the API (POST /content/topics) or
directly: content_queue.db_queue.add_topic(...).

    python tasks/graph_api/post_from_queue.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent.orchestrator import run_agent_task
from content_queue import db_queue


async def main():
    topic_row = db_queue.peek_next_topic()
    if not topic_row:
        print("No topics waiting in the queue.")
        return

    task_name = "instagram_post_from_topic" if topic_row["platform"] == "instagram" else "facebook_post_from_topic"
    result = await run_agent_task(
        task_name,
        {
            "topic": topic_row["topic"],
            "instructions": topic_row["instructions"],
            "image_url": topic_row["image_url"],
        },
        verbose=True,
        use_browser=False,
    )
    print("\n" + "=" * 50)
    print(result)

    if result.get("success"):
        db_queue.mark_posted(topic_row["id"], str(result.get("result", "")))
        print(f"Marked topic {topic_row['id']} as posted.")
    else:
        db_queue.mark_failed(topic_row["id"], str(result.get("result", "")))


if __name__ == "__main__":
    asyncio.run(main())
