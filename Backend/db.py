"""
MySQL-backed persistence for the unified voice-agent app.

This now uses the SAME ORM tables as voice_agent.py (Domain/models.py) — exact
table names and column names — instead of the previous bespoke phone_call_*
tables. The public helper functions keep their original signatures so the
call sites in multiligual_call.py do not change (no logic change):

  Users         -> `users`        (models.User)
  Transcript    -> `conversations`(models.Conversation: CallSid/ResponseType/ResponseText)
  Call status   -> `calllogs` + `callstatus` (models.CallLogs / models.CallStatus)
  Recordings    -> `recordings`   (models.Recordings: CallSid/RecordingUrl)

connect_db() creates ALL Domain tables (including the batch tables
callnumberexecution, batchexecution, ...) so batch calling works against the
same single MySQL database.

ResponseType convention matches voice_agent.py:
  user speech  -> "answer"
  agent speech -> "question"
"""
import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy import func

# Reuse the already-configured MySQL engine + session factory (database.py).
from database import engine_admin, SessionLocalAdmin
from base import Base
from Domain import models  # noqa: F401  (registers all tables on Base.metadata)

logger = logging.getLogger(__name__)

# Map the Sarvam call-loop's role names onto voice_agent's ResponseType values.
_ROLE_TO_RESPONSE_TYPE = {"user": "answer", "agent": "question"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def connect_db():
    """Create every Domain table (users, conversations, calllogs, callstatus,
    recordings, and all batch tables) if missing.

    Tables are created INDIVIDUALLY (not via a single create_all) so that a
    failure on one already-existing table — e.g. a duplicate-index-name clash
    on `batchinfo` under case-insensitive MySQL — does not abort creation of the
    others (callnumbers / callnumberexecution were being skipped this way).
    checkfirst=True still makes each create a no-op when the table exists."""
    created, skipped = 0, 0
    for table in Base.metadata.sorted_tables:
        try:
            table.create(bind=engine_admin, checkfirst=True)
            created += 1
        except Exception as e:
            skipped += 1
            logger.warning(f"Table '{table.name}' create skipped ({e.__class__.__name__})")
    logger.info(f"✅ MySQL connected; Domain tables ready (ok={created}, skipped={skipped})")


def get_db():
    """Backward-compat shim: callers only use this for a truthiness check."""
    return engine_admin


def close_db():
    try:
        engine_admin.dispose()
        logger.info("🛑 MySQL engine disposed")
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Users  (table: users  -> models.User)
# ----------------------------------------------------------------------------
def count_users() -> int:
    db = SessionLocalAdmin()
    try:
        return int(db.query(func.count(models.User.Id)).scalar() or 0)
    finally:
        db.close()


def find_user_by_email(email: str):
    """Return a dict with at least {'email', 'password'} or None."""
    db = SessionLocalAdmin()
    try:
        user = (
            db.query(models.User)
            .filter(models.User.email == email, models.User.IsDeleted == False)
            .first()
        )
        if not user:
            return None
        return {"id": user.Id, "email": user.email, "password": user.password}
    finally:
        db.close()


def create_user(email: str, hashed_password: str):
    """Insert a user. `username` is required+unique, so we derive a guaranteed
    unique handle from a uuid."""
    db = SessionLocalAdmin()
    try:
        user = models.User(
            Id=str(uuid.uuid4()),
            username=("u_" + uuid.uuid4().hex)[:50],
            email=email,
            password=hashed_password,
            CreatedBy="system",
            CreatedAt=_utcnow(),
            IsDeleted=False,
        )
        db.add(user)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ----------------------------------------------------------------------------
# Transcript  (table: conversations -> models.Conversation)
# ----------------------------------------------------------------------------
def save_transcript_message(call_sid: str, role: str, content: str, phone_number: str = None):
    """Append one conversation turn. `role` ("user"/"agent") maps to voice_agent's
    ResponseType ("answer"/"question"). `phone_number` is accepted for signature
    compatibility (the conversations table has no phone column)."""
    db = SessionLocalAdmin()
    try:
        row = models.Conversation(
            CallSid=call_sid,
            ResponseType=_ROLE_TO_RESPONSE_TYPE.get(role, role),
            ResponseText=content,
            CreatedBy="system",
            CreatedAt=_utcnow(),
            IsDeleted=False,
        )
        db.add(row)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ----------------------------------------------------------------------------
# Call status  (tables: calllogs + callstatus -> models.CallLogs / CallStatus)
# Mirrors voice_agent.py's callstatus_webhook: ensure a CallLogs row exists for
# the CallSid, then append a CallStatus row.
# ----------------------------------------------------------------------------
def update_call_status(call_sid: str, status: str, phone_number: str = None):
    db = SessionLocalAdmin()
    try:
        call_log = (
            db.query(models.CallLogs)
            .filter(models.CallLogs.CallSid == call_sid)
            .first()
        )
        if not call_log:
            call_log = models.CallLogs(
                Id=str(uuid.uuid4()),
                CallSid=call_sid,
                PhoneNumber=phone_number or "",
                CreatedBy="system",
                CreatedAt=_utcnow(),
                IsDeleted=False,
            )
            db.add(call_log)
            db.flush()  # populate call_log.Id for the FK below

        db.add(models.CallStatus(
            Id=str(uuid.uuid4()),
            Status=status,
            CallLogsId=call_log.Id,
            CreatedBy="system",
            CreatedAt=_utcnow(),
            IsDeleted=False,
        ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ----------------------------------------------------------------------------
# Recordings  (table: recordings -> models.Recordings)
# ----------------------------------------------------------------------------
def save_recording(
    call_sid: str,
    recording_sid: str = None,
    recording_url: str = None,
    recording_duration=None,
    recording_channels: str = None,
    recording_source: str = None,
    recording_status: str = None,
    recording_minio_url: str = None,
):
    """Persist a recording row. The MinIO archive URL is preferred (the audio is
    deleted from Twilio after archival); falls back to the Twilio URL. Extra
    Twilio metadata params are accepted for signature compatibility — the
    recordings table stores only CallSid + RecordingUrl, matching voice_agent."""
    url = recording_minio_url or recording_url
    if not url:
        return
    db = SessionLocalAdmin()
    try:
        db.add(models.Recordings(
            Id=str(uuid.uuid4()),
            CallSid=call_sid,
            RecordingUrl=url,
            CreatedBy="system",
            CreatedAt=_utcnow(),
            IsDeleted=False,
        ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ----------------------------------------------------------------------------
# Read / delete helpers (field names aligned to voice_agent's schema.py DTOs)
# ----------------------------------------------------------------------------
def fetch_conversation(call_sid: str):
    """Ordered transcript for one call, shaped like voice_agent's
    ConversationResponse: id, call_sid, response_type, response_text, created_at."""
    db = SessionLocalAdmin()
    try:
        rows = (
            db.query(models.Conversation)
            .filter(models.Conversation.CallSid == call_sid,
                    models.Conversation.IsDeleted == False)
            .order_by(models.Conversation.CreatedAt.asc())
            .all()
        )
        return [
            {
                "id": r.Id,
                "call_sid": r.CallSid,
                "response_type": r.ResponseType,
                "response_text": r.ResponseText,
                "created_at": r.CreatedAt,
            }
            for r in rows
        ]
    finally:
        db.close()


def list_call_logs(page: int = 1, page_size: int = 20):
    """Paginated call logs with their status history + recording URL, shaped like
    voice_agent's PaginatedCallLogResponse / CallLogResponse."""
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 200))
    offset = (page - 1) * page_size
    db = SessionLocalAdmin()
    try:
        total = int(db.query(func.count(models.CallLogs.Id))
                    .filter(models.CallLogs.IsDeleted == False).scalar() or 0)
        logs = (
            db.query(models.CallLogs)
            .filter(models.CallLogs.IsDeleted == False)
            .order_by(models.CallLogs.CreatedAt.desc())
            .limit(page_size).offset(offset)
            .all()
        )
        items = []
        for log in logs:
            statuses = (
                db.query(models.CallStatus)
                .filter(models.CallStatus.CallLogsId == log.Id,
                        models.CallStatus.IsDeleted == False)
                .order_by(models.CallStatus.CreatedAt.asc())
                .all()
            )
            rec = (
                db.query(models.Recordings)
                .filter(models.Recordings.CallSid == log.CallSid,
                        models.Recordings.IsDeleted == False)
                .order_by(models.Recordings.CreatedAt.desc())
                .first()
            )
            items.append({
                "id": log.Id,
                "call_sid": log.CallSid,
                "phone_number": log.PhoneNumber,
                "statuses": [
                    {"id": s.Id, "call_status": s.Status, "date": s.CreatedAt}
                    for s in statuses
                ],
                "recording_url": rec.RecordingUrl if rec else None,
            })
        return {"items": items, "total_items": total}
    finally:
        db.close()


def list_recordings(page: int = 1, page_size: int = 20):
    """Paginated recordings (newest first): id, call_sid, recording_url, created_at."""
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 200))
    offset = (page - 1) * page_size
    db = SessionLocalAdmin()
    try:
        total = int(db.query(func.count(models.Recordings.Id))
                    .filter(models.Recordings.IsDeleted == False).scalar() or 0)
        rows = (
            db.query(models.Recordings)
            .filter(models.Recordings.IsDeleted == False)
            .order_by(models.Recordings.CreatedAt.desc())
            .limit(page_size).offset(offset)
            .all()
        )
        return {
            "items": [
                {"id": r.Id, "call_sid": r.CallSid,
                 "recording_url": r.RecordingUrl, "created_at": r.CreatedAt}
                for r in rows
            ],
            "total_items": total,
        }
    finally:
        db.close()


def purge_call_logs():
    """Delete ALL call logs + their statuses (mirrors voice_agent's
    delete-call-logs, which clears callstatus then calllogs)."""
    db = SessionLocalAdmin()
    try:
        status_deleted = db.query(models.CallStatus).delete()
        logs_deleted = db.query(models.CallLogs).delete()
        db.commit()
        return {"callstatus_deleted": int(status_deleted or 0),
                "calllogs_deleted": int(logs_deleted or 0)}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
