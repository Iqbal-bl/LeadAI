"""DB-backed content topic queue — what a CRM should call to feed topics
in, instead of editing a local CSV."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import require_api_key
from api.db import ContentTopic, SessionLocal
from api.request_logging import Timer, log_call
from content_queue import db_queue

router = APIRouter(prefix="/content/topics", tags=["content"])


class TopicRequest(BaseModel):
    topic: str
    instructions: str = ""
    image_url: str = ""
    video_url: str = ""
    platform: str = "facebook"  # "facebook" | "instagram"


@router.post("")
async def add_topic(body: TopicRequest, client_id: str = Depends(require_api_key)):
    with Timer() as t:
        topic_id = db_queue.add_topic(
            body.topic, body.instructions, body.image_url, body.video_url, body.platform
        )
    log_call(client_id, body.platform, "add_topic", "/content/topics", body.model_dump(),
              True, t.duration_ms, f"topic_id={topic_id}")
    return {"id": topic_id, "status": "pending"}


@router.get("")
async def list_topics(status: str = "pending", limit: int = 50):
    session = SessionLocal()
    try:
        rows = (
            session.query(ContentTopic)
            .filter(ContentTopic.status == status)
            .order_by(ContentTopic.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id, "topic": r.topic, "instructions": r.instructions,
                "image_url": r.image_url, "video_url": r.video_url,
                "platform": r.platform, "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        session.close()
