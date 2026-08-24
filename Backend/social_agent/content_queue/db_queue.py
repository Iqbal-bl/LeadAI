"""
DB-backed post topic queue — the recommended path now that api/ exists.
Same function shape as csv_queue.py (peek/remove/add) so scheduler.py can
use either. Unlike the CSV, this is safe for multiple writers (e.g. a
CRM posting topics via the API while the scheduler consumes them).
"""

from api.db import ContentTopic, SessionLocal


def peek_next_topic() -> dict | None:
    """Returns the oldest pending row without changing its status."""
    session = SessionLocal()
    try:
        row = (
            session.query(ContentTopic)
            .filter(ContentTopic.status == "pending")
            .order_by(ContentTopic.created_at.asc())
            .first()
        )
        if not row:
            return None
        return {
            "id": row.id,
            "topic": row.topic,
            "instructions": row.instructions,
            "image_url": row.image_url or "",
            "video_url": row.video_url or "",
            "platform": row.platform,
        }
    finally:
        session.close()


def mark_posted(topic_id: int, result_summary: str = "") -> None:
    session = SessionLocal()
    try:
        row = session.get(ContentTopic, topic_id)
        if row:
            row.status = "posted"
            row.result_summary = result_summary
            import datetime
            row.posted_at = datetime.datetime.utcnow()
            session.commit()
    finally:
        session.close()


def mark_failed(topic_id: int, result_summary: str = "") -> None:
    session = SessionLocal()
    try:
        row = session.get(ContentTopic, topic_id)
        if row:
            row.status = "failed"
            row.result_summary = result_summary
            session.commit()
    finally:
        session.close()


def add_topic(
    topic: str,
    instructions: str = "",
    image_url: str = "",
    video_url: str = "",
    platform: str = "facebook",
) -> int:
    """Adds a topic to the queue, returns its id."""
    session = SessionLocal()
    try:
        row = ContentTopic(
            topic=topic, instructions=instructions, image_url=image_url,
            video_url=video_url, platform=platform,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id
    finally:
        session.close()
