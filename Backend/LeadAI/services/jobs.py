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
import time
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
    """Atomically take one due job that THIS worker actually knows how to handle."""
    now = utcnow()
    known_kinds = list(_HANDLERS.keys())
    if not known_kinds:
        return None

    candidate = (
        db.query(LeadJob)
        .filter(
            LeadJob.Status == "queued",
            LeadJob.Kind.in_(known_kinds),
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

    _last_bootstrap = 0.0

    while not _stopping:
        try:
            # Self-healing periodic bootstrap check every 30s
            now_ts = time.time()
            if now_ts - _last_bootstrap > 30.0:
                _last_bootstrap = now_ts
                db_boot = session()
                try:
                    bootstrap_blog_job(db_boot)
                    bootstrap_linkedin_job(db_boot)
                except Exception as b_err:
                    logger.debug("[LeadAI jobs] Periodic bootstrap check error: %s", b_err)
                finally:
                    db_boot.close()

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
    
    from . import billing as billing_svc

    for account in accounts:
        allowed, reason = billing_svc.check_channel_access(db, account.ClientId, "linkedin")
        if not allowed:
            logger.info("[LeadAI jobs] Skipping LinkedIn sync for client %s: %s", account.ClientId, reason)
            continue

        try:
            p_cnt, a_cnt = process_pending_invitations(db, account)
            processed_count += p_cnt
            accepted_count += a_cnt
        except Exception as exc:
            logger.error("Error processing LinkedIn invitations for client %s: %s", account.ClientId, exc)
            errors.append(f"{account.ClientId}: {exc}")
            
    # If this is the global scheduled run, schedule the next execution in ~2 hours with jitter
    if not company_id:
        run_at = calculate_next_periodic_run(base_minutes=120, jitter_minutes=15)
        enqueue(db, "linkedin.process_invitations", run_at=run_at)
        logger.info("[LeadAI jobs] Scheduled next periodic linkedin.process_invitations at %s", run_at)
        
    return {
        "processed_accounts": len(accounts),
        "total_processed_invitations": processed_count,
        "total_accepted_invitations": accepted_count,
        "errors": errors
    }


def calculate_next_periodic_run(base_minutes: int = 120, jitter_minutes: int = 15) -> datetime:
    """Calculate the datetime (UTC, tz-naive) for the next run (~2 hours with randomized human-like jitter)."""
    import random
    offset_seconds = (base_minutes * 60) + random.randint(-jitter_minutes * 60, jitter_minutes * 60)
    # Ensure minimum delay is at least 60 minutes
    offset_seconds = max(3600, offset_seconds)
    now_utc = datetime.now(timezone.utc)
    target_utc = now_utc + timedelta(seconds=offset_seconds)
    return target_utc.replace(tzinfo=None)


def bootstrap_linkedin_job(db) -> None:
    """Ensure that the recurring LinkedIn connection request job exists."""
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
        run_at = calculate_next_periodic_run(base_minutes=120, jitter_minutes=15)
        enqueue(db, "linkedin.process_invitations", run_at=run_at)
        logger.info("[LeadAI jobs] Enqueued first run of linkedin.process_invitations at %s", run_at)


# ===========================================================================
# Blog & Multi-Channel Content Automation Jobs
# ===========================================================================

@register("blog.daily_scheduler")
def handle_blog_daily_scheduler(db: Session, payload: dict) -> dict:
    """
    Recurring scheduler worker that scans all companies enabled for automated blog creation,
    picks trending topics, and queues generation runs.
    """
    from ..models_blog import LeadBlogSettings
    from .blog.topic_picker import TopicPickerService
    from Domain.models import Client

    now = utcnow()
    now_hour_min = now.strftime("%H:%M")
    today_date_str = now.strftime("%Y-%m-%d")

    # Find all companies with active auto-blogging
    settings_rows = (
        db.query(LeadBlogSettings)
        .filter(
            LeadBlogSettings.IsAutoBlogEnabled == True,
            LeadBlogSettings.IsDeleted == False,
        )
        .all()
    )

    scheduled_count = 0
    skipped_count = 0
    errors = []

    for bs in settings_rows:
        client_id = bs.ClientId
        client = db.query(Client).filter(Client.Id == client_id, Client.IsDeleted == False).first()
        company_name = client.Name if client else "Your Organization"

        # Check if a blog.generate job is currently queued or running for this company
        existing_active_gen = (
            db.query(LeadJob)
            .filter(
                LeadJob.Kind == "blog.generate",
                LeadJob.ClientId == client_id,
                LeadJob.Status.in_(("queued", "claimed", "running")),
                LeadJob.IsDeleted == False,
            )
            .first()
        )
        if existing_active_gen:
            logger.debug(f"[LeadAI jobs] Skipping client {client_id}: blog.generate job is already active ({existing_active_gen.Id})")
            skipped_count += 1
            continue

        # Check schedule time (stored as HH:MM in UTC)
        if bs.ScheduleTime:
            try:
                parts = bs.ScheduleTime.strip().split(":")
                sched_hour, sched_min = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
                now_minutes = now.hour * 60 + now.minute
                sched_minutes = sched_hour * 60 + sched_min

                # 1. Only execute if current UTC time is at or after scheduled time
                if now_minutes < sched_minutes:
                    logger.debug(
                        f"[LeadAI jobs] Skipping client {client_id}: scheduled for {bs.ScheduleTime} UTC "
                        f"({sched_hour:02d}:{sched_min:02d}), current UTC is {now_hour_min}"
                    )
                    continue

                # 2. Check if already executed for this scheduled time slot today
                today_sched_dt = datetime(now.year, now.month, now.day, sched_hour, sched_min)
                if bs.LastRunAt and bs.LastRunAt >= today_sched_dt:
                    skipped_count += 1
                    continue
            except Exception as e:
                logger.warning(f"[LeadAI jobs] Invalid schedule_time '{bs.ScheduleTime}' for client {client_id}: {e}")

        try:
            # Pick trending topic & keywords for company
            topic, keywords = TopicPickerService.pick_daily_topic(db, client_id, bs)
            
            # Enqueue generation job
            enqueue(
                db,
                "blog.generate",
                payload={
                    "client_id": client_id,
                    "company_name": company_name,
                    "topic": topic,
                    "keywords": keywords,
                    "mode": bs.Mode,
                    "tone": bs.Tone,
                    "target_audience": bs.TargetAudience,
                    "target_words": bs.TargetWords,
                    "include_images": bs.IncludeImages,
                    "num_images": bs.NumImages,
                    "cta_text": bs.CtaText,
                    "cta_url": bs.CtaUrl,
                    "target_channels": bs.TargetChannels or ["wordpress"],
                    "admin_email": bs.AdminNotificationEmail,
                },
                client_id=client_id,
                commit=True,
            )

            bs.LastRunAt = now
            bs.UpdatedAt = now
            db.commit()
            scheduled_count += 1
            logger.info(f"[LeadAI jobs] Scheduled daily blog generation for client {client_id} (topic: '{topic}') at {now_hour_min} UTC")
        except Exception as exc:
            db.rollback()
            err_msg = f"Failed to schedule daily blog for {client_id}: {exc}"
            logger.error(f"[LeadAI jobs] {err_msg}")
            errors.append(err_msg)

    # Re-queue next scheduler tick in 5 minutes (ensure only 1 tick exists)
    has_future_scheduler = (
        db.query(LeadJob)
        .filter(
            LeadJob.Kind == "blog.daily_scheduler",
            LeadJob.Status.in_(("queued", "claimed")),
            LeadJob.IsDeleted == False,
        )
        .first()
    )
    if not has_future_scheduler:
        next_tick = (now + timedelta(minutes=5)).replace(tzinfo=None)
        enqueue(db, "blog.daily_scheduler", run_at=next_tick, commit=True)
        db.commit()
        logger.info(f"[LeadAI jobs] Scheduled next blog.daily_scheduler run at {next_tick}")

    return {
        "active_companies": len(settings_rows),
        "scheduled_today": scheduled_count,
        "already_ran_today": skipped_count,
        "errors": errors,
    }


@register("blog.generate")
def handle_blog_generate(db: Session, payload: dict) -> dict:
    """Job handler executing LangGraph blog generation and routing to auto-publish or approval email."""
    from .blog.article_service import ArticleService
    from .blog.schemas import GenerateBlogRequest

    client_id = payload.get("client_id")
    company_name = payload.get("company_name", "Your Organization")
    topic = payload.get("topic")

    if not client_id or not topic:
        return {"error": "Missing client_id or topic"}

    req = GenerateBlogRequest(
        topic=topic,
        keywords=payload.get("keywords") or [],
        tone=payload.get("tone") or "thought_leadership",
        target_audience=payload.get("target_audience") or "Business Leaders",
        target_words=payload.get("target_words") or 1000,
        include_images=payload.get("include_images", True),
        num_images=payload.get("num_images") or 1,
        cta_text=payload.get("cta_text") or "Book Free Strategy Session Today",
        cta_url=payload.get("cta_url") or "#strategy-session",
        target_channels=payload.get("target_channels") or ["wordpress"],
        generation_mode="daily_scheduler",
        admin_reviewer_email=payload.get("admin_email"),
    )

    article_resp = ArticleService.generate_and_save(
        db=db,
        client_id=client_id,
        req=req,
        company_name=company_name
    )

    return {
        "article_id": article_resp.id,
        "status": article_resp.status,
        "title": article_resp.title,
        "requires_approval": article_resp.requires_approval,
    }


@register("blog.publish")
def handle_blog_publish(db: Session, payload: dict) -> dict:
    """Job handler publishing an approved article to WordPress, LinkedIn, Facebook, Instagram."""
    from .blog.publisher_service import PublisherService
    from ..models_blog import LeadArticle

    article_id = payload.get("article_id")
    client_id = payload.get("client_id")
    target_channels = payload.get("target_channels")

    if not article_id:
        return {"error": "Missing article_id"}

    article = db.query(LeadArticle).filter(
        LeadArticle.Id == article_id,
        LeadArticle.IsDeleted == False,
    ).first()

    if not article:
        return {"error": f"Article {article_id} not found"}

    results = PublisherService.publish_to_all_channels(
        db=db,
        article=article,
        target_channels=target_channels,
        actor=payload.get("actor", "system")
    )

    return {
        "article_id": article.Id,
        "status": article.Status,
        "results": results
    }


def bootstrap_blog_job(db: Session) -> None:
    """Ensure that the recurring blog daily scheduler job exists and is queued."""
    # 1. Cancel any stale/failed scheduler ticks so they don't pile up
    failed_sched_jobs = (
        db.query(LeadJob)
        .filter(
            LeadJob.Kind == "blog.daily_scheduler",
            LeadJob.Status.in_(("failed", "queued")),
            LeadJob.IsDeleted == False,
        )
        .all()
    )
    # Keep at most one queued scheduler job
    kept_one = False
    for sj in failed_sched_jobs:
        if sj.Status == "queued" and not kept_one:
            kept_one = True
            continue
        sj.Status = "cancelled"
    db.commit()

    # 2. Auto-recover any blog generation jobs failed due to worker missing handler
    failed_gen_jobs = (
        db.query(LeadJob)
        .filter(
            LeadJob.Kind == "blog.generate",
            LeadJob.Status == "failed",
            LeadJob.IsDeleted == False,
        )
        .all()
    )
    for fj in failed_gen_jobs:
        if "No handler registered" in (fj.Error or ""):
            fj.Status = "queued"
            fj.Attempts = 0
            fj.Error = None
            fj.RunAt = utcnow().replace(tzinfo=None)
            db.commit()

    # 3. Ensure a single recurring scheduler tick is queued
    existing = (
        db.query(LeadJob)
        .filter(
            LeadJob.Kind == "blog.daily_scheduler",
            LeadJob.Status.in_(("queued", "claimed", "running")),
            LeadJob.IsDeleted == False,
        )
        .first()
    )
    if not existing:
        run_at = utcnow().replace(tzinfo=None)
        enqueue(db, "blog.daily_scheduler", run_at=run_at, commit=True)
        db.commit()
        logger.info("[LeadAI jobs] Enqueued first run of blog.daily_scheduler at %s", run_at)


