"""
Background jobs — a small, durable queue on the database you already run.

WHY NOT asyncio.create_task
---------------------------
Three failure modes kill in-process tasks in this application specifically:

1. `uvicorn --workers 4` means four event loops. A campaign kicked off by an
   HTTP request runs in whichever worker served it; a "pause" request usually
   lands on a different worker and cannot see the task.
2. A deploy or an OOM kill silently loses a half-sent campaign, and nothing
   records where it stopped. Re-running it double-messages 30,000 people.
3. A long fan-out inside a request worker starves the same thread pool the call
   pipeline uses, so voice latency degrades while a campaign is running.

WHY NOT CELERY (yet)
--------------------
Celery means running and monitoring a broker. At this stage the queue depth is
tens of thousands of rows a day, which MySQL handles without noticing. The
interface below (`enqueue` / `register` / `run_worker`) is the same shape as a
Celery task registry, so the swap is a one-file change when volume justifies the
operational cost. `claim()` is the only piece that would be replaced.

CORRECTNESS
-----------
Claiming is an atomic conditional UPDATE:

    UPDATE leadai_jobs SET Status='claimed', ClaimedBy=:me
    WHERE Id=:id AND Status='queued'

`rowcount == 1` means this worker won the row. Two workers racing on the same
job means exactly one gets 1 and the other gets 0. That is the whole locking
story — no advisory locks, no SELECT FOR UPDATE holding a transaction open
across a network call to Meta.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import update

from ..config import settings
from ..db import session
from ..models import LeadJob, utcnow

logger = logging.getLogger(__name__)

# kind -> handler. Handlers are sync functions taking (db, payload) and are run
# in a thread so a blocking DB session and blocking HTTP calls are both fine.
_HANDLERS: dict[str, Callable[[Any, dict], dict | None]] = {}
_worker_task: asyncio.Task | None = None
_stopping = False


def worker_id() -> str:
    return settings.worker_id or f"{socket.gethostname()}:{os.getpid()}"


def register(kind: str):
    """Decorator: @jobs.register("campaign.run")."""

    def wrap(func):
        _HANDLERS[kind] = func
        return func

    return wrap


def enqueue(
    db,
    kind: str,
    payload: dict | None = None,
    *,
    client_id: str | None = None,
    run_at: datetime | None = None,
    priority: int = 5,
    max_attempts: int | None = None,
    commit: bool = False,
) -> LeadJob:
    """Add work. Uses the CALLER'S session so the job and the row it refers to
    commit together — a campaign is never queued for a campaign row that then
    failed to save."""
    job = LeadJob(
        ClientId=client_id,
        Kind=kind,
        PayloadJson=payload or {},
        Status="queued",
        Priority=priority,
        RunAt=run_at or utcnow(),
        MaxAttempts=max_attempts or 3,
        CreatedBy="system",
    )
    db.add(job)
    db.flush()
    if commit:
        db.commit()
    return job


def cancel_kind(db, kind: str, entity_id: str) -> int:
    """Cancel queued jobs for one entity (e.g. pausing a campaign)."""
    rows = (
        db.query(LeadJob)
        .filter(
            LeadJob.Kind == kind,
            LeadJob.Status.in_(("queued", "claimed")),
            LeadJob.IsDeleted == False,  # noqa: E712
        )
        .all()
    )
    count = 0
    for job in rows:
        payload = job.PayloadJson or {}
        if entity_id in (payload.get("campaign_id"), payload.get("entity_id")):
            job.Status = "cancelled"
            job.FinishedAt = utcnow()
            count += 1
    return count


def claim(db, me: str) -> LeadJob | None:
    """Atomically take one due job. Returns None when the queue is empty."""
    now = utcnow()
    candidate = (
        db.query(LeadJob)
        .filter(
            LeadJob.Status == "queued",
            LeadJob.RunAt <= now,
            LeadJob.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadJob.Priority.asc(), LeadJob.RunAt.asc())
        .first()
    )
    if candidate is None:
        return None

    result = db.execute(
        update(LeadJob.__table__)
        .where(LeadJob.__table__.c.Id == candidate.Id)
        .where(LeadJob.__table__.c.Status == "queued")
        .values(Status="claimed", ClaimedBy=me, ClaimedAt=now, Attempts=LeadJob.__table__.c.Attempts + 1)
    )
    db.commit()
    if result.rowcount != 1:
        return None  # another worker won it
    db.expire_all()
    return db.get(LeadJob, candidate.Id)


def _run_one(job_id: str) -> None:
    """Execute a claimed job on its own session. Never raises."""
    db = session()
    try:
        job = db.get(LeadJob, job_id)
        if job is None:
            return
        handler = _HANDLERS.get(job.Kind)
        if handler is None:
            job.Status = "failed"
            job.Error = f"No handler registered for '{job.Kind}'"
            job.FinishedAt = utcnow()
            db.commit()
            return

        job.Status = "running"
        db.commit()
        try:
            result = handler(db, job.PayloadJson or {})
            job.Status = "done"
            job.ResultJson = result if isinstance(result, dict) else None
            job.Error = None
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            job = db.get(LeadJob, job_id)
            logger.error("[LeadAI jobs] %s failed: %s", job.Kind if job else job_id, exc)
            logger.debug(traceback.format_exc())
            if job is None:
                return
            job.Error = f"{exc.__class__.__name__}: {exc}"[:1000]
            if job.Attempts < job.MaxAttempts:
                # Exponential backoff: 30s, 60s, 120s…
                job.Status = "queued"
                job.RunAt = datetime.now(timezone.utc) + timedelta(
                    seconds=30 * (2 ** (job.Attempts - 1))
                )
            else:
                job.Status = "failed"
        job.FinishedAt = utcnow()
        db.commit()
    finally:
        db.close()


async def run_worker() -> None:
    """Poll loop. Started from the FastAPI startup hook when enabled."""
    global _stopping
    me = worker_id()
    logger.info("[LeadAI jobs] worker %s started (concurrency=%d)", me, settings.worker_concurrency)
    running: set[asyncio.Task] = set()

    while not _stopping:
        try:
            if len(running) >= settings.worker_concurrency:
                await asyncio.sleep(0.2)
                running = {t for t in running if not t.done()}
                continue

            db = session()
            try:
                job = claim(db, me)
                job_id = job.Id if job else None
            finally:
                db.close()

            if job_id is None:
                await asyncio.sleep(settings.worker_poll_seconds)
                running = {t for t in running if not t.done()}
                continue

            # Handlers are blocking; run them off the event loop so webhooks and
            # the media-stream websocket keep their latency.
            task = asyncio.create_task(asyncio.to_thread(_run_one, job_id))
            running.add(task)
        except asyncio.CancelledError:
            break
        except Exception as exc:  # noqa: BLE001
            logger.error("[LeadAI jobs] worker loop error: %s", exc)
            await asyncio.sleep(5)

    for task in running:
        task.cancel()
    logger.info("[LeadAI jobs] worker %s stopped", me)


def start(loop: asyncio.AbstractEventLoop | None = None) -> None:
    global _worker_task, _stopping
    if not settings.worker_enabled:
        logger.info("[LeadAI jobs] worker disabled by config")
        return
    if _worker_task is not None and not _worker_task.done():
        return
    _stopping = False
    _worker_task = asyncio.create_task(run_worker())


def stop() -> None:
    global _stopping
    _stopping = True
    if _worker_task is not None:
        _worker_task.cancel()


def reclaim_stale(minutes: int = 15) -> int:
    """Re-queue jobs whose worker died mid-flight.

    Called once at startup. A job stuck in 'claimed'/'running' for longer than
    the timeout had its process killed; without this it would sit there forever
    and the campaign would appear frozen.
    """
    db = session()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        rows = (
            db.query(LeadJob)
            .filter(
                LeadJob.Status.in_(("claimed", "running")),
                LeadJob.ClaimedAt < cutoff,
            )
            .all()
        )
        for job in rows:
            job.Status = "queued" if job.Attempts < job.MaxAttempts else "failed"
            job.Error = (job.Error or "") + " | reclaimed after worker timeout"
        db.commit()
        if rows:
            logger.warning("[LeadAI jobs] reclaimed %d stale jobs", len(rows))
        return len(rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LeadAI jobs] reclaim failed: %s", exc)
        return 0
    finally:
        db.close()


def stats(db) -> dict:
    from sqlalchemy import func

    rows = (
        db.query(LeadJob.Status, func.count(LeadJob.Id))
        .filter(LeadJob.IsDeleted == False)  # noqa: E712
        .group_by(LeadJob.Status)
        .all()
    )
    return {"worker": worker_id(), "enabled": settings.worker_enabled, "queue": dict(rows)}


@register("linkedin.process_invitations")
def handle_linkedin_invitations(db, payload: dict) -> dict:
    """Durable queue job handler for LinkedIn connection invitation sync."""
    from ..social.linkedin_bot import process_pending_invitations
    from ..models_ext import LeadChannelAccount

    company_id = payload.get("company_id")
    
    # Query active accounts
    query = db.query(LeadChannelAccount).filter(
        LeadChannelAccount.Channel == "linkedin",
        LeadChannelAccount.IsActive == True,
        LeadChannelAccount.IsDeleted == False
    )
    if company_id:
        query = query.filter(LeadChannelAccount.ClientId == company_id)
        
    accounts = query.all()
    processed_count = 0
    accepted_count = 0
    errors = []
    
    for account in accounts:
        try:
            p_cnt, a_cnt = process_pending_invitations(db, account)
            processed_count += p_cnt
            accepted_count += a_cnt
        except Exception as exc:
            logger.error("Error processing LinkedIn invitations for client %s: %s", account.ClientId, exc)
            errors.append(f"{account.ClientId}: {exc}")
            
    # If this is the global daily scheduled run, schedule the next execution for tomorrow morning
    if not company_id:
        run_at = calculate_next_morning_run()
        enqueue(db, "linkedin.process_invitations", run_at=run_at)
        logger.info("[LeadAI jobs] Scheduled next daily linkedin.process_invitations at %s", run_at)
        
    return {
        "processed_accounts": len(accounts),
        "total_processed_invitations": processed_count,
        "total_accepted_invitations": accepted_count,
        "errors": errors
    }


def calculate_next_morning_run() -> datetime:
    """Calculate the datetime (UTC, tz-naive) for the next 9:00 AM run in default_timezone."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(settings.default_timezone)
    now_local = datetime.now(tz)
    
    # Target 9:00 AM local time
    target_local = now_local.replace(hour=9, minute=0, second=0, microsecond=0)
    
    # If it is already past 9:00 AM local time today, schedule for tomorrow morning 9:00 AM
    if now_local >= target_local:
        target_local += timedelta(days=1)
        
    return target_local.astimezone(timezone.utc).replace(tzinfo=None)


def bootstrap_linkedin_job(db) -> None:
    """Ensure that the daily scheduled LinkedIn connection request job exists."""
    existing = (
        db.query(LeadJob)
        .filter(
            LeadJob.Kind == "linkedin.process_invitations",
            LeadJob.Status.in_(("queued", "claimed")),
            LeadJob.IsDeleted == False
        )
        .first()
    )
    if not existing:
        run_at = calculate_next_morning_run()
        enqueue(db, "linkedin.process_invitations", run_at=run_at)
        logger.info("[LeadAI jobs] Enqueued first run of linkedin.process_invitations at %s", run_at)
