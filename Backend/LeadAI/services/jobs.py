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
from sqlalchemy.orm import Session

from ..config import settings
from ..db import session
from ..models import LeadJob, utcnow
from .. import activity
from ..activity import A

_utcnow = utcnow

_utcnow = utcnow

logger = logging.getLogger(__name__)

# kind -> handler. Handlers are sync functions taking (db, payload) and are run
# in a thread so a blocking DB session and blocking HTTP calls are both fine.
_HANDLERS: dict[str, Callable[[Any, dict], dict | None]] = {}
_worker_task: asyncio.Task | None = None
_stopping = False
_main_loop: asyncio.AbstractEventLoop | None = None


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
    global _stopping, _main_loop
    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        pass
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
    global _worker_task, _stopping, _main_loop
    if not settings.worker_enabled:
        logger.info("[LeadAI jobs] worker disabled by config")
        return
    if _worker_task is not None and not _worker_task.done():
        return
    _stopping = False
    try:
        _main_loop = loop or asyncio.get_running_loop()
    except RuntimeError:
        _main_loop = loop
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
            if p_cnt > 0 or a_cnt > 0:
                activity.log(
                    db,
                    action=A.LINKEDIN_INVITATION_ACCEPTED if a_cnt > 0 else A.LINKEDIN_SCHEDULER_TICK,
                    client_id=account.ClientId,
                    actor_email="scheduler",
                    entity_type="linkedin",
                    log_type="Info",
                    message=f"LinkedIn Invitation Sync: Processed {p_cnt} received invitation(s), accepted {a_cnt}",
                    meta={"processed": p_cnt, "accepted": a_cnt},
                    commit=True,
                )
        except Exception as exc:
            logger.error("Error processing LinkedIn invitations for client %s: %s", account.ClientId, exc)
            errors.append(f"{account.ClientId}: {exc}")
            activity.log(
                db,
                action=A.LINKEDIN_AUTH_FAILED,
                client_id=account.ClientId,
                actor_email="scheduler",
                entity_type="linkedin",
                log_type="Error",
                message=f"LinkedIn Invitation Sync failed: {exc}",
                meta={"error": str(exc)},
                commit=True,
            )
            
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


def calculate_next_periodic_run(base_minutes: int = 60, jitter_minutes: int = 15, min_minutes: int = 30) -> datetime:
    """Calculate the datetime (UTC, tz-naive) for the next run with randomized human-like jitter."""
    import random
    offset_seconds = (base_minutes * 60) + random.randint(-jitter_minutes * 60, jitter_minutes * 60)
    # Ensure minimum delay threshold
    offset_seconds = max(min_minutes * 60, offset_seconds)
    now_utc = datetime.now(timezone.utc)
    target_utc = now_utc + timedelta(seconds=offset_seconds)
    return target_utc.replace(tzinfo=None)


@register("linkedin.sync_comments")
def handle_linkedin_sync_comments(db: Session, payload: dict) -> dict:
    """Recurring job handler to scan LinkedIn posts for comments and generate AI replies safely."""
    from ..social.linkedin_bot import fetch_recent_posts_and_comments_browser
    from ..models_ext import LeadChannelAccount
    import asyncio

    company_id = payload.get("company_id")
    query = db.query(LeadChannelAccount).filter(
        LeadChannelAccount.Channel == "linkedin",
        LeadChannelAccount.IsActive == True,
        LeadChannelAccount.IsDeleted == False
    )
    if company_id:
        query = query.filter(LeadChannelAccount.ClientId == company_id)

    accounts = query.all()
    results = {}
    for account in accounts:
        try:
            if _main_loop is not None and _main_loop.is_running() and not _main_loop.is_closed():
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        fetch_recent_posts_and_comments_browser(db, account, limit_posts=2, is_background_job=True),
                        _main_loop,
                    )
                    res = future.result(timeout=240)
                except RuntimeError as r_err:
                    if "closed" in str(r_err).lower():
                        res = asyncio.run(fetch_recent_posts_and_comments_browser(db, account, limit_posts=2, is_background_job=True))
                    else:
                        raise
            else:
                res = asyncio.run(fetch_recent_posts_and_comments_browser(db, account, limit_posts=2, is_background_job=True))
            results[account.ClientId] = res
            
            # Log activity if comments were harvested or replied
            if isinstance(res, dict) and res.get("status") not in ("skipped", "deferred"):
                activity.log(
                    db,
                    action=A.LINKEDIN_COMMENT_HARVESTED,
                    client_id=account.ClientId,
                    actor_email="scheduler",
                    entity_type="linkedin",
                    log_type="Info",
                    message=f"LinkedIn Comments & AI Auto-Reply Scan: {res.get('processed_comments', 0)} comments processed, {res.get('replied_count', 0)} replies sent",
                    meta=res,
                    commit=True,
                )
        except Exception as exc:
            logger.warning("[LeadAI jobs] LinkedIn comment sync error for client %s: %s", account.ClientId, exc)
            results[account.ClientId] = {"error": str(exc)}
            activity.log(
                db,
                action=A.LINKEDIN_COMMENT_HARVESTED,
                client_id=account.ClientId,
                actor_email="scheduler",
                entity_type="linkedin",
                log_type="Warning",
                message=f"LinkedIn comment scan failed: {exc}",
                meta={"error": str(exc)},
                commit=True,
            )

    # Schedule next check in ~3 hours (120-210 minutes) with wide human jitter (safe anti-bot cadence).
    # If deferred due to active live messaging, retry in 5 minutes so it runs as soon as messaging is idle!
    if not company_id:
        has_deferred = any(isinstance(r, dict) and r.get("status") == "deferred" for r in results.values())
        if has_deferred:
            run_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)
            logger.info("[LeadAI jobs] Live messaging active during comment sync. Rescheduling quick retry in 5 mins at %s", run_at)
        else:
            run_at = calculate_next_periodic_run(base_minutes=180, jitter_minutes=30, min_minutes=120)
            logger.info("[LeadAI jobs] Scheduled next periodic linkedin.sync_comments at %s", run_at)
        enqueue(db, "linkedin.sync_comments", run_at=run_at)

    return {"synced_accounts": len(accounts), "details": results}


def calculate_next_random_schedule(runs_per_day: int = 3, active_hours_start: int = 9, active_hours_end: int = 19) -> datetime:
    """Calculate the next randomized execution time during active business hours (e.g. 9 AM - 7 PM)."""
    import random
    now_utc = datetime.now(timezone.utc)
    active_window_hours = max(1.0, float(active_hours_end - active_hours_start))
    base_interval_minutes = int((active_window_hours * 60) / max(1, runs_per_day))
    
    # Add random humanized jitter (+/- 25%)
    jitter_range = max(10, int(base_interval_minutes * 0.25))
    offset_minutes = base_interval_minutes + random.randint(-jitter_range, jitter_range)
    offset_minutes = max(15, offset_minutes)
    
    candidate = now_utc + timedelta(minutes=offset_minutes)
    return candidate.replace(tzinfo=None)


@register("linkedin.auto_search_and_connect")
def handle_linkedin_auto_search_and_connect(db: Session, payload: dict) -> dict:
    """
    Periodic job to search LinkedIn for target candidate profiles matching the company's
    configured keywords and send personalized connection requests automatically with random scheduling.
    """
    from ..models_ext import LeadChannelAccount
    from ..social import linkedin_bot
    from . import billing as billing_svc
    import random
    import asyncio

    company_id = payload.get("company_id")
    query = db.query(LeadChannelAccount).filter(
        LeadChannelAccount.Channel == "linkedin",
        LeadChannelAccount.IsActive == True,
        LeadChannelAccount.IsDeleted == False
    )
    if company_id:
        query = query.filter(LeadChannelAccount.ClientId == company_id)

    accounts = query.all()
    results = {}

    for account in accounts:
        meta = account.MetaJson or {}
        auto_cfg = meta.get("linkedin_auto_connect") or {}
        
        # If running globally (not a targeted manual trigger), skip accounts where auto_connect is disabled
        if not company_id and not auto_cfg.get("enabled", False):
            continue

        allowed, reason = billing_svc.check_channel_access(db, account.ClientId, "linkedin")
        if not allowed:
            logger.info("[LeadAI jobs] Skipping LinkedIn auto-connect for %s: %s", account.ClientId, reason)
            continue

        keywords = auto_cfg.get("target_keywords") or auto_cfg.get("keywords")
        if not keywords:
            prompt = auto_cfg.get("target_prompt")
            if prompt:
                try:
                    if _main_loop is not None and _main_loop.is_running() and not _main_loop.is_closed():
                        keywords = asyncio.run_coroutine_threadsafe(
                            linkedin_bot.generate_search_keywords(prompt),
                            _main_loop
                        ).result(timeout=30)
                    else:
                        keywords = asyncio.run(linkedin_bot.generate_search_keywords(prompt))
                    auto_cfg["target_keywords"] = keywords
                except Exception as kw_err:
                    logger.warning("Failed to auto-generate search keywords: %s", kw_err)
        
        if not keywords:
            logger.info("[LeadAI jobs] LinkedIn auto-connect skipped for %s (no keywords set)", account.ClientId)
            continue

        if not account.LinkedinCookieEnc and not (account.LinkedinUsernameEnc and account.LinkedinPasswordEnc):
            logger.warning("[LeadAI jobs] LinkedIn credentials missing for account %s", account.ClientId)
            continue

        limit = min(15, max(1, int(auto_cfg.get("profiles_per_run", 5))))
        custom_message_template = auto_cfg.get("custom_message") or ""

        try:
            # 1. Search candidate profiles
            if _main_loop is not None and _main_loop.is_running() and not _main_loop.is_closed():
                search_batch = asyncio.run_coroutine_threadsafe(
                    linkedin_bot.search_profiles_api(account, keywords, limit=max(25, limit * 3)),
                    _main_loop
                ).result(timeout=60)
            else:
                search_batch = asyncio.run(linkedin_bot.search_profiles_api(account, keywords, limit=max(25, limit * 3)))
            
            contacted_ids = set(auto_cfg.get("contacted_profile_ids", []))
            fresh_profiles = [p for p in search_batch if p.get("public_id") and p.get("public_id") not in contacted_ids]
            
            targets = fresh_profiles[:limit]
            
            sent_count = 0
            failed_count = 0
            
            sent_profiles_info = []
            for profile in targets:
                pid = profile.get("public_id")
                name = profile.get("name", "") or profile.get("full_name", "") or pid or "Candidate"
                headline = profile.get("headline", "") or profile.get("occupation", "") or ""
                profile_url = profile.get("profile_url") or (f"https://www.linkedin.com/in/{pid}" if pid and not pid.startswith("urn:") else "")
                first_name = name.split()[0] if name else "there"
                
                # Format message template if provided
                msg = None
                if custom_message_template.strip():
                    msg = custom_message_template.replace("{firstName}", first_name).replace("{name}", name)
                
                # Send connection invitation
                if _main_loop is not None and _main_loop.is_running() and not _main_loop.is_closed():
                    res = asyncio.run_coroutine_threadsafe(
                        linkedin_bot.send_connection_invitations_api(account, [profile], message=msg),
                        _main_loop
                    ).result(timeout=60)
                else:
                    res = asyncio.run(linkedin_bot.send_connection_invitations_api(account, [profile], message=msg))
                
                res_detail = res.get(pid, {})
                success = res_detail.get("success", False)
                res_msg = res_detail.get("message", "Sent" if success else "Failed")

                if success:
                    sent_count += 1
                    contacted_ids.add(pid)
                    sent_profiles_info.append({
                        "name": name,
                        "public_id": pid,
                        "headline": headline,
                        "profile_url": profile_url,
                        "status": "sent",
                        "message": res_msg,
                    })
                    activity.log(
                        db,
                        action=A.LINKEDIN_CONNECTION_SENT,
                        client_id=account.ClientId,
                        actor_email="scheduler",
                        entity_type="linkedin",
                        entity_id=pid,
                        log_type="Info",
                        message=f"Sent connection invitation to {name}" + (f" ({headline})" if headline else ""),
                        meta={
                            "name": name,
                            "public_id": pid,
                            "headline": headline,
                            "profile_url": profile_url,
                            "keywords": keywords,
                            "invitation_message": msg,
                            "mode": "auto_pilot",
                        },
                        commit=True,
                    )
                else:
                    failed_count += 1
                    activity.log(
                        db,
                        action=A.LINKEDIN_CONNECTION_FAILED,
                        client_id=account.ClientId,
                        actor_email="scheduler",
                        entity_type="linkedin",
                        entity_id=pid,
                        log_type="Warning",
                        message=f"Failed to send connection invitation to {name}: {res_msg}",
                        meta={
                            "name": name,
                            "public_id": pid,
                            "headline": headline,
                            "profile_url": profile_url,
                            "keywords": keywords,
                            "error": res_msg,
                            "mode": "auto_pilot",
                        },
                        commit=True,
                    )
                
                # Anti-bot human delay
                time.sleep(random.uniform(5.0, 10.0))
            
            updated_contacted = list(contacted_ids)[-2000:]
            
            now_iso = datetime.now(timezone.utc).isoformat()
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            last_date_str = auto_cfg.get("today_date", "")
            if last_date_str != today_str:
                auto_cfg["today_date"] = today_str
                auto_cfg["total_sent_today"] = sent_count
            else:
                auto_cfg["total_sent_today"] = int(auto_cfg.get("total_sent_today", 0)) + sent_count

            auto_cfg["total_sent_all_time"] = int(auto_cfg.get("total_sent_all_time", 0)) + sent_count
            auto_cfg["contacted_profile_ids"] = updated_contacted
            auto_cfg["last_run_at"] = now_iso
            auto_cfg["last_run_status"] = "success" if failed_count == 0 else "partial_success" if sent_count > 0 else "failed"
            auto_cfg["last_run_detail"] = f"Sent {sent_count} invitation{'s' if sent_count != 1 else ''} ({failed_count} skipped/failed) for query: '{keywords}'"
            
            # Calculate next scheduled run
            runs_per_day = int(auto_cfg.get("runs_per_day", 3))
            next_run_dt = calculate_next_random_schedule(
                runs_per_day=runs_per_day,
                active_hours_start=int(auto_cfg.get("active_hours_start", 9)),
                active_hours_end=int(auto_cfg.get("active_hours_end", 19)),
            )
            auto_cfg["next_run_at"] = next_run_dt.isoformat()
            
            meta["linkedin_auto_connect"] = auto_cfg
            account.MetaJson = meta
            account.UpdatedAt = utcnow()
            db.commit()

            results[account.ClientId] = {
                "sent": sent_count,
                "failed": failed_count,
                "targets_found": len(search_batch),
                "next_run_at": auto_cfg["next_run_at"],
            }
            activity.log(
                db,
                action=A.LINKEDIN_AUTO_SEARCH_COMPLETED,
                client_id=account.ClientId,
                actor_email="scheduler",
                entity_type="linkedin",
                log_type="Info" if sent_count > 0 else "Warning",
                message=f"LinkedIn Auto-Pilot: Sent {sent_count} invitation(s) ({failed_count} skipped/failed) for keywords '{keywords}'",
                meta={
                    "sent_count": sent_count,
                    "failed_count": failed_count,
                    "keywords": keywords,
                    "sent_profiles": sent_profiles_info,
                    "next_run_at": auto_cfg["next_run_at"],
                    "total_today": auto_cfg.get("total_sent_today", 0),
                    "total_all_time": auto_cfg.get("total_sent_all_time", 0),
                },
                commit=True,
            )
        except Exception as exc:
            logger.error("Error in LinkedIn auto-connect for client %s: %s", account.ClientId, exc)
            auto_cfg["last_run_status"] = "error"
            auto_cfg["last_run_detail"] = str(exc)
            meta["linkedin_auto_connect"] = auto_cfg
            account.MetaJson = meta
            db.commit()
            results[account.ClientId] = {"error": str(exc)}
            activity.log(
                db,
                action=A.LINKEDIN_CONNECTION_FAILED,
                client_id=account.ClientId,
                actor_email="scheduler",
                entity_type="linkedin",
                log_type="Error",
                message=f"LinkedIn Auto-Pilot run failed: {exc}",
                meta={"error": str(exc)},
                commit=True,
            )

    # If global recurring run, schedule the next iteration
    if not company_id:
        run_at = calculate_next_periodic_run(base_minutes=90, jitter_minutes=25, min_minutes=45)
        enqueue(db, "linkedin.auto_search_and_connect", run_at=run_at)
        logger.info("[LeadAI jobs] Scheduled next global periodic linkedin.auto_search_and_connect at %s", run_at)

    return {"processed_accounts": len(accounts), "details": results}


def bootstrap_linkedin_job(db) -> None:
    """Ensure that recurring LinkedIn automation jobs exist."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # 1. Connection requests sync (~90-120 mins)
    existing_invites = (
        db.query(LeadJob)
        .filter(
            LeadJob.Kind == "linkedin.process_invitations",
            LeadJob.Status.in_(("queued", "claimed")),
            LeadJob.IsDeleted == False
        )
        .first()
    )
    if not existing_invites:
        run_at = now + timedelta(seconds=25)
        enqueue(db, "linkedin.process_invitations", run_at=run_at)
        logger.info("[LeadAI jobs] Enqueued first run of linkedin.process_invitations at %s", run_at)

    # 2. Comments & AI Replies scanner (~3 hours, safe anti-detection cadence)
    existing_comments = (
        db.query(LeadJob)
        .filter(
            LeadJob.Kind == "linkedin.sync_comments",
            LeadJob.Status.in_(("queued", "claimed")),
            LeadJob.IsDeleted == False
        )
        .first()
    )
    if not existing_comments:
        run_at = now + timedelta(minutes=180)
        enqueue(db, "linkedin.sync_comments", run_at=run_at)
        logger.info("[LeadAI jobs] Enqueued first run of linkedin.sync_comments at %s", run_at)

    # 3. Auto-Pilot Search & Connect Scheduler (~90 mins with jitter)
    existing_auto_connect = (
        db.query(LeadJob)
        .filter(
            LeadJob.Kind == "linkedin.auto_search_and_connect",
            LeadJob.Status.in_(("queued", "claimed")),
            LeadJob.IsDeleted == False
        )
        .first()
    )
    if not existing_auto_connect:
        run_at = now + timedelta(minutes=45)
        enqueue(db, "linkedin.auto_search_and_connect", run_at=run_at)
        logger.info("[LeadAI jobs] Enqueued first run of linkedin.auto_search_and_connect at %s", run_at)




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
    from domain.models import Client

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
            activity.log(
                db,
                action=A.BLOG_TOPIC_DISCOVERED,
                client_id=client_id,
                actor_email="scheduler",
                entity_type="blog",
                log_type="Info",
                message=f"Auto-Blog Scheduler: Selected topic '{topic}' and enqueued article generation",
                meta={"topic": topic, "keywords": keywords, "mode": bs.Mode, "schedule_time": bs.ScheduleTime},
                commit=True,
            )
        except Exception as exc:
            db.rollback()
            err_msg = f"Failed to schedule daily blog for {client_id}: {exc}"
            logger.error(f"[LeadAI jobs] {err_msg}")
            errors.append(err_msg)
            activity.log(
                db,
                action=A.BLOG_ARTICLE_FAILED,
                client_id=client_id,
                actor_email="scheduler",
                entity_type="blog",
                log_type="Error",
                message=f"Auto-Blog Scheduler failed for client {client_id}: {exc}",
                meta={"error": str(exc)},
                commit=True,
            )

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
        client_id=client_id,
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

    try:
        article = ArticleService.generate_and_save(
            db=db,
            client_id=client_id,
            req=req,
            company_name=company_name
        )
        return {
            "articles_count": 1,
            "article_ids": [article.id],
            "first_article_id": article.id,
            "status": article.status,
            "title": article.title or topic,
        }
    except Exception as exc:
        logger.error(f"[LeadAI jobs] Blog generation failed for {client_id}: {exc}")
        activity.log(
            db,
            action=A.BLOG_ARTICLE_FAILED,
            client_id=client_id,
            actor_email="scheduler",
            entity_type="blog",
            log_type="Error",
            message=f"Auto-Blog generation failed for topic '{topic}': {exc}",
            meta={"topic": topic, "error": str(exc)},
            commit=True,
        )
        return {"error": str(exc)}


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


@register("usage.retention_cleanup")
def handle_usage_retention_cleanup(db: Session, payload: dict) -> dict:
    """Daily background job to purge granular raw usage events older than 90 days.
    
    Leaves daily aggregate rows intact forever for historical reporting.
    Reschedules itself for the next daily tick (24 hours).
    """
    from core.usage_tracker import purge_expired_raw_events

    retention_days = int(payload.get("retention_days", 90))
    purged_count = purge_expired_raw_events(db, retention_days=retention_days)

    # Re-queue next retention cleanup tick in 24 hours
    now = utcnow()
    now_naive = now.replace(tzinfo=None) if now.tzinfo else now
    has_future_retention = (
        db.query(LeadJob)
        .filter(
            LeadJob.Kind == "usage.retention_cleanup",
            LeadJob.Status == "queued",
            LeadJob.RunAt > now_naive,
            LeadJob.IsDeleted == False,
        )
        .first()
    )
    if not has_future_retention:
        next_tick = (now + timedelta(hours=24)).replace(tzinfo=None)
        enqueue(db, "usage.retention_cleanup", payload={"retention_days": retention_days}, run_at=next_tick, commit=True)
        db.commit()
        logger.info(f"[LeadAI jobs] Scheduled next usage.retention_cleanup at {next_tick}")

    return {
        "purged_count": purged_count,
        "retention_days": retention_days,
    }


def bootstrap_usage_retention_job(db: Session) -> None:
    """Ensure that the recurring usage data retention cleanup job exists and is queued."""
    existing = (
        db.query(LeadJob)
        .filter(
            LeadJob.Kind == "usage.retention_cleanup",
            LeadJob.Status.in_(("queued", "claimed", "running")),
            LeadJob.IsDeleted == False,
        )
        .first()
    )
    if not existing:
        run_at = utcnow().replace(tzinfo=None)
        enqueue(db, "usage.retention_cleanup", payload={"retention_days": 90}, run_at=run_at, commit=True)
        db.commit()
        logger.info("[LeadAI jobs] Enqueued first run of usage.retention_cleanup at %s", run_at)




