"""
Database layer — SQLAlchemy, defaults to a local SQLite file so this
runs with zero setup. To move to Postgres later: just change DATABASE_URL
in .env (e.g. postgresql://user:pass@host/dbname) — nothing else in this
file changes, SQLAlchemy handles the rest.

Two tables:
- ApiRequestLog: every request this API receives, for monitoring.
- ContentTopic: the DB-backed post-topic queue (replaces/complements the
  CSV queue — see content_queue/db_queue.py).
"""

import datetime
import os

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./agentic_browser.db")

# check_same_thread=False is only needed for SQLite (FastAPI's async
# request handling can touch the connection from different threads);
# harmless/ignored for other databases.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class ApiRequestLog(Base):
    __tablename__ = "api_request_log"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    client_id = Column(String, index=True)          # which API key made the call
    platform = Column(String, index=True)            # "facebook" | "instagram" | None
    action = Column(String, index=True)               # "post" | "reply_comments" | etc.
    endpoint = Column(String)                          # e.g. "/facebook/posts"
    request_summary = Column(Text)                      # sanitized JSON — never tokens/secrets
    success = Column(Boolean, default=False)
    duration_ms = Column(Integer)
    result_summary = Column(Text)
    error_message = Column(Text, nullable=True)


class ContentTopic(Base):
    __tablename__ = "content_topic"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    topic = Column(String, nullable=False)
    instructions = Column(Text, default="")
    image_url = Column(String, default="")
    video_url = Column(String, default="")
    platform = Column(String, default="facebook")   # "facebook" | "instagram"
    status = Column(String, default="pending", index=True)  # pending|posted|failed
    posted_at = Column(DateTime, nullable=True)
    result_summary = Column(Text, nullable=True)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
