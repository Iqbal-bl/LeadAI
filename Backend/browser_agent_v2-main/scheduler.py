"""
The 24/7 unattended runner. Three Graph API-only jobs (no browser at
all): consume the content queue, reply to comments, reply to messages.
Each runs on its own jittered interval — natural-looking cadence, not
disguise; there's no detection risk to manage since this is official API
access to your own Page.

    python scheduler.py

Each run's result is appended to results.jsonl, one JSON object per line.
"""

import asyncio
import json
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from agent.orchestrator import run_agent_task
from content_queue import db_queue

RESULTS_LOG = Path("results.jsonl")


def _log_result(result: dict):
    with RESULTS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


async def job_post_from_queue():
    topic_row = db_queue.peek_next_topic()
    if not topic_row:
        print("[queue] No topics waiting — skipping this run.")
        return
    task_name = "instagram_post_from_topic" if topic_row["platform"] == "instagram" else "facebook_post_from_topic"
    result = await run_agent_task(
        task_name,
        {
            "topic": topic_row["topic"],
            "instructions": topic_row["instructions"],
            "image_url": topic_row["image_url"],
        },
        use_browser=False,
    )
    _log_result(result)
    if result.get("success"):
        db_queue.mark_posted(topic_row["id"], str(result.get("result", "")))
    else:
        db_queue.mark_failed(topic_row["id"], str(result.get("result", "")))


async def job_reply_to_comments():
    result = await run_agent_task("facebook_reply_to_comments", {}, use_browser=False)
    _log_result(result)


async def job_reply_to_instagram_comments():
    result = await run_agent_task("instagram_reply_to_comments", {}, use_browser=False)
    _log_result(result)


async def job_reply_to_messages():
    result = await run_agent_task("facebook_reply_to_messages", {}, use_browser=False)
    _log_result(result)


async def job_reply_to_instagram_messages():
    result = await run_agent_task("instagram_reply_to_messages", {}, use_browser=False)
    _log_result(result)


def main():
    scheduler = AsyncIOScheduler()

    # jitter spreads actual run times +/- N seconds around the interval,
    # so runs aren't at a suspiciously exact clock tick — not evasion,
    # just avoids a bursty/robotic posting rhythm.
    scheduler.add_job(job_post_from_queue, IntervalTrigger(hours=4, jitter=600))
    scheduler.add_job(job_reply_to_comments, IntervalTrigger(minutes=15, jitter=120))
    scheduler.add_job(job_reply_to_instagram_comments, IntervalTrigger(minutes=15, jitter=120))
    scheduler.add_job(job_reply_to_messages, IntervalTrigger(minutes=10, jitter=90))
    scheduler.add_job(job_reply_to_instagram_messages, IntervalTrigger(minutes=10, jitter=90))

    scheduler.start()
    print("Scheduler started — posting, comment replies, and message replies running unattended. Ctrl+C to stop.")
    try:
        asyncio.get_event_loop().run_forever()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
