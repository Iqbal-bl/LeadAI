"""
A simple CSV queue of post topics: {topic, instructions} rows, consumed
one at a time, oldest first. Single-consumer only — safe for one
scheduler, not for multiple running concurrently.
"""

import csv
from pathlib import Path

QUEUE_PATH = Path("content_queue/topics.csv")


def peek_next_topic() -> dict | None:
    """Returns the oldest row without removing it — call this, attempt
    the post, and only call remove_next_topic() once it succeeds."""
    if not QUEUE_PATH.exists():
        return None
    with QUEUE_PATH.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[0] if rows else None


def remove_next_topic() -> None:
    """Removes the oldest row — call after a successful post so the same
    topic isn't posted again next run."""
    if not QUEUE_PATH.exists():
        return
    with QUEUE_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    if not rows:
        return
    rows.pop(0)
    with QUEUE_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def add_topic(topic: str, instructions: str, image_url: str = "") -> None:
    """Appends a new topic to the end of the queue. image_url is
    optional — leave empty to auto-generate an image at post time."""
    is_new = not QUEUE_PATH.exists()
    with QUEUE_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["topic", "instructions", "image_url"])
        if is_new:
            writer.writeheader()
        writer.writerow({"topic": topic, "instructions": instructions, "image_url": image_url})
