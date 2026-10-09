"""
In-App AI Token and Cost Tracking System.

Completely independent of LangSmith. Records all LLM, embedding, voice stream,
and image generation invocations with exact token counts, calculated costs,
tenant attribution (company_id), and process identifiers.

Design guarantees:
1. Non-blocking: Writes to a bounded queue (maxsize 10,000) processed by a background worker.
2. Zero-crash: Never raises an unhandled exception into the caller's request path.
3. Multi-tenant: ContextVars automatically propagate company_id and process across async turns.
4. Aggregations: Maintains daily aggregates in IST ('Asia/Kolkata') timezone.
5. Process Shutdown: Flushes on shutdown via atexit and lifespan hooks.
6. Zero PII/Content: Prompts, completions, transcripts, and customer PII are NEVER stored.
"""
from __future__ import annotations

import atexit
import logging
import os
import queue
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

from .pricing import calculate_cost

logger = logging.getLogger(__name__)

# ContextVars for multi-tenant and process propagation across async tasks
current_company_id: ContextVar[Optional[str]] = ContextVar("leadai_current_company_id", default=None)
current_process: ContextVar[Optional[str]] = ContextVar("leadai_current_process", default=None)
current_channel: ContextVar[Optional[str]] = ContextVar("leadai_current_channel", default=None)
current_conversation_id: ContextVar[Optional[str]] = ContextVar("leadai_current_conversation_id", default=None)

# Timezone for daily aggregate rollups (defaults to IST / Asia/Kolkata)
USAGE_TIMEZONE_NAME = os.getenv("AI_USAGE_TIMEZONE", "Asia/Kolkata")
try:
    USAGE_TZ = ZoneInfo(USAGE_TIMEZONE_NAME)
except Exception:
    USAGE_TZ = timezone.utc

# Bounded queue to absorb spikes without consuming unbounded RAM
_QUEUE_MAXSIZE = int(os.getenv("AI_USAGE_QUEUE_MAXSIZE", "10000"))
_usage_queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAXSIZE)
_worker_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_dropped_count = 0
_last_drop_log_time = 0.0


@contextmanager
def bind_usage_context(
    company_id: Optional[str] = None,
    process: Optional[str] = None,
    channel: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Iterator[None]:
    """Context manager to bind tenant and process metadata for downstream AI calls."""
    tokens = []
    if company_id is not None:
        tokens.append((current_company_id, current_company_id.set(company_id)))
    if process is not None:
        tokens.append((current_process, current_process.set(process)))
    if channel is not None:
        tokens.append((current_channel, current_channel.set(channel)))
    if conversation_id is not None:
        tokens.append((current_conversation_id, current_conversation_id.set(conversation_id)))

    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def get_usage_context() -> dict:
    """Return the current context dictionary containing company_id, process, channel, etc."""
    return {
        "company_id": current_company_id.get(),
        "process": current_process.get(),
        "channel": current_channel.get(),
        "conversation_id": current_conversation_id.get(),
    }


from .pricing import AI_PROCESS_CATALOGUE, PROCESS_CATALOGUE



ALLOWED_NULL_COMPANY_PROCESSES = {
    p for p, meta in AI_PROCESS_CATALOGUE.items() if not meta.get("is_customer_facing", True)
}


def record_usage(
    *,
    company_id: Optional[str] = None,
    process: Optional[str] = None,
    channel: Optional[str] = None,
    provider: str = "openai",
    model: str = "gpt-4o",
    input_tokens: int = 0,
    output_tokens: int = 0,
    is_image: bool = False,
    image_count: int = 1,
    is_estimated: bool = False,
    is_prewarm: bool = False,
    conversation_id: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> None:
    """Central non-blocking recording function for all AI invocations.

    Never raises, never blocks synchronous caller, and computes costs deterministically.
    """
    global _dropped_count, _last_drop_log_time
    if os.getenv("AI_USAGE_TRACKING_ENABLED", "true").strip().lower() in ("false", "0", "no", "off"):
        return
    try:
        # Resolve from context if not explicitly passed
        eff_company_id = company_id if company_id is not None else current_company_id.get()
        eff_process = process if process is not None else (current_process.get() or "unknown")
        eff_channel = channel if channel is not None else current_channel.get()
        eff_conversation_id = conversation_id if conversation_id is not None else current_conversation_id.get()
        eff_created_at = created_at or datetime.now(timezone.utc)

        # Runtime validation: Warn if customer-facing process lacks company_id
        if eff_company_id is None and not is_prewarm and eff_process not in ALLOWED_NULL_COMPANY_PROCESSES:
            logger.warning(
                "[AI Usage Tracker] Customer-facing process '%s' recorded with NULL company_id! (channel=%s)",
                eff_process,
                eff_channel,
            )


        total_tokens = max(0, int(input_tokens)) + max(0, int(output_tokens))

        # Calculate cost & unit prices
        cost_usd, unit_in, unit_out, price_status = calculate_cost(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            is_image=is_image,
            image_count=image_count,
        )

        event_data = {
            "client_id": eff_company_id,
            "process": eff_process,
            "channel": eff_channel,
            "provider": provider,
            "model": model,
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "total_tokens": total_tokens,
            "cost_usd": cost_usd,
            "unit_price_in": unit_in,
            "unit_price_out": unit_out,
            "price_status": price_status,
            "is_estimated": bool(is_estimated),
            "is_prewarm": bool(is_prewarm),
            "conversation_id": eff_conversation_id,
            "created_at": eff_created_at,
        }

        # Start worker thread if not already running
        _ensure_worker_started()

        try:
            _usage_queue.put_nowait(event_data)
        except queue.Full:
            _dropped_count += 1
            now = time.time()
            if now - _last_drop_log_time > 10.0:  # Rate limit drop warning to once per 10s
                logger.warning(
                    "[AI Usage Tracker] Queue is full (maxsize=%d). Dropped %d events.",
                    _QUEUE_MAXSIZE,
                    _dropped_count,
                )
                _last_drop_log_time = now
    except Exception as exc:  # noqa: BLE001 — recording must NEVER fail customer request
        logger.warning("[AI Usage Tracker] record_usage failed safely: %s", exc)


def _flush_batch(events: list[dict]) -> None:
    """Flush a batch of usage events to MySQL and update daily aggregates."""
    if not events:
        return

    from LeadAI.db import session
    from LeadAI.models_usage import LeadAIUsageDailyAggregate, LeadAIUsageEvent

    db = session()
    try:
        raw_rows = []
        # Aggregation map key: (client_id, process, model, date_str)
        agg_map: dict[tuple, dict[str, Any]] = {}

        for e in events:
            # 1. Create granular event row
            raw_rows.append(
                LeadAIUsageEvent(
                    ClientId=e["client_id"],
                    Process=e["process"],
                    Channel=e["channel"],
                    Provider=e["provider"],
                    Model=e["model"],
                    InputTokens=e["input_tokens"],
                    OutputTokens=e["output_tokens"],
                    TotalTokens=e["total_tokens"],
                    CostUsd=e["cost_usd"],
                    UnitPriceInputPer1M=e["unit_price_in"],
                    UnitPriceOutputPer1M=e["unit_price_out"],
                    PriceStatus=e["price_status"],
                    IsEstimated=e["is_estimated"],
                    IsPrewarm=e["is_prewarm"],
                    ConversationId=e["conversation_id"],
                    CreatedAt=e["created_at"],
                )
            )

            # Skip pre_warm checks from daily billing aggregations
            if e["is_prewarm"]:
                continue

            # Compute IST date string
            created_dt = e["created_at"]
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            local_dt = created_dt.astimezone(USAGE_TZ)
            usage_date = local_dt.strftime("%Y-%m-%d")

            agg_key = (e["client_id"], e["process"], e["model"], usage_date)
            if agg_key not in agg_map:
                agg_map[agg_key] = {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                    "has_unknown_price": False,
                }

            agg = agg_map[agg_key]
            agg["requests"] += 1
            agg["input_tokens"] += e["input_tokens"]
            agg["output_tokens"] += e["output_tokens"]
            agg["total_tokens"] += e["total_tokens"]
            if e["cost_usd"] is not None:
                agg["cost_usd"] += e["cost_usd"]
            if e["price_status"] == "price_unknown":
                agg["has_unknown_price"] = True

        # Insert raw rows
        db.add_all(raw_rows)

        # Upsert daily aggregates
        for (client_id, proc, mdl, dt_str), stats in agg_map.items():
            query = db.query(LeadAIUsageDailyAggregate).filter(
                LeadAIUsageDailyAggregate.Process == proc,
                LeadAIUsageDailyAggregate.Model == mdl,
                LeadAIUsageDailyAggregate.UsageDate == dt_str,
            )
            if client_id is None:
                query = query.filter(LeadAIUsageDailyAggregate.ClientId.is_(None))
            else:
                query = query.filter(LeadAIUsageDailyAggregate.ClientId == client_id)

            existing = query.first()
            if existing:
                existing.TotalRequests += stats["requests"]
                existing.TotalInputTokens += stats["input_tokens"]
                existing.TotalOutputTokens += stats["output_tokens"]
                existing.TotalTokens += stats["total_tokens"]
                existing.TotalCostUsd = round(existing.TotalCostUsd + stats["cost_usd"], 7)
                if stats["has_unknown_price"]:
                    existing.HasUnknownPrice = True
                existing.UpdatedAt = datetime.now(timezone.utc)
            else:
                db.add(
                    LeadAIUsageDailyAggregate(
                        ClientId=client_id,
                        Process=proc,
                        Model=mdl,
                        UsageDate=dt_str,
                        TotalRequests=stats["requests"],
                        TotalInputTokens=stats["input_tokens"],
                        TotalOutputTokens=stats["output_tokens"],
                        TotalTokens=stats["total_tokens"],
                        TotalCostUsd=round(stats["cost_usd"], 7),
                        HasUnknownPrice=stats["has_unknown_price"],
                        UpdatedAt=datetime.now(timezone.utc),
                    )
                )

        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("[AI Usage Tracker] Failed to flush usage batch (%d events): %s", len(events), exc)
    finally:
        db.close()


def _worker_loop() -> None:
    """Background worker loop that batches and flushes usage events."""
    while not _stop_event.is_set():
        batch = []
        try:
            # Wait up to 1.0s for the first item
            first_item = _usage_queue.get(timeout=1.0)
            batch.append(first_item)
            _usage_queue.task_done()

            # Drain up to 50 items available immediately
            while len(batch) < 50:
                try:
                    item = _usage_queue.get_nowait()
                    batch.append(item)
                    _usage_queue.task_done()
                except queue.Empty:
                    break
        except queue.Empty:
            continue

        if batch:
            _flush_batch(batch)


def _ensure_worker_started() -> None:
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _stop_event.clear()
        _worker_thread = threading.Thread(
            target=_worker_loop,
            name="LeadAI-UsageTrackerWorker",
            daemon=True,
        )
        _worker_thread.start()


def flush_usage_events() -> None:
    """Flush all currently queued usage events immediately to database (safe for tests)."""
    remaining = []
    while True:
        try:
            remaining.append(_usage_queue.get_nowait())
            _usage_queue.task_done()
        except queue.Empty:
            break
    if remaining:
        _flush_batch(remaining)


def flush_on_shutdown() -> None:
    """Flush any pending usage events during process termination."""
    _stop_event.set()
    flush_usage_events()


# Automatically register shutdown hook
atexit.register(flush_on_shutdown)


def purge_expired_raw_events(db, retention_days: int = 90) -> int:
    """Maintenance function to delete granular raw event logs older than retention period.

    Daily aggregates are left intact for historical reporting.
    """
    from datetime import timedelta
    from LeadAI.models_usage import LeadAIUsageEvent

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = db.query(LeadAIUsageEvent).filter(LeadAIUsageEvent.CreatedAt < cutoff).delete()
    db.commit()
    logger.info("[AI Usage Tracker] Purged %d raw usage events older than %d days.", deleted, retention_days)
    return deleted


try:
    from langchain_core.callbacks.base import BaseCallbackHandler
    from langchain_core.outputs import LLMResult

    class InAppUsageCallbackHandler(BaseCallbackHandler):
        """LangChain callback handler to record token usage to in-app tracker."""

        def __init__(self, process: Optional[str] = None, channel: Optional[str] = None):
            super().__init__()
            self.process = process
            self.channel = channel

        def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
            if os.getenv("AI_USAGE_TRACKING_ENABLED", "true").strip().lower() in ("false", "0", "no", "off"):
                return
            try:
                llm_output = response.llm_output or {}
                token_usage = llm_output.get("token_usage", {})
                model_name = llm_output.get("model_name", "gpt-4o")
                prompt_tokens = token_usage.get("prompt_tokens") or 0
                completion_tokens = token_usage.get("completion_tokens") or 0
                if not prompt_tokens and not completion_tokens:
                    # Check generation usage_metadata if available
                    for gen_list in response.generations:
                        for gen in gen_list:
                            msg = getattr(gen, "message", None)
                            meta = getattr(msg, "usage_metadata", None) or {}
                            if meta:
                                prompt_tokens = meta.get("input_tokens", 0)
                                completion_tokens = meta.get("output_tokens", 0)
                                break

                if prompt_tokens or completion_tokens:
                    record_usage(
                        model=model_name,
                        input_tokens=prompt_tokens,
                        output_tokens=completion_tokens,
                        process=self.process,
                        channel=self.channel,
                    )
            except Exception:  # noqa: BLE001
                pass

except ImportError:
    class InAppUsageCallbackHandler:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass

