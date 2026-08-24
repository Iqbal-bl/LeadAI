"""
Tiny cache facade.

Redis when REDIS_URL is set (shared across uvicorn workers — which matters,
because a per-process rate limit is no rate limit at all once you scale out),
otherwise an in-process dict so the app boots with nothing installed.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from ..config import settings

logger = logging.getLogger(__name__)

_local: dict[str, tuple[float | None, Any]] = {}
_lock = threading.Lock()
_redis = None

if settings.redis_url:  # pragma: no cover - optional dependency
    try:
        import redis  # type: ignore

        _redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        _redis.ping()
        logger.info("[LeadAI] Redis cache active")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LeadAI] Redis unavailable (%s) — using in-process cache", exc)
        _redis = None


def backend() -> str:
    return "redis" if _redis else "memory"


def set_value(key: str, value: Any, ttl: int | None = None) -> None:
    if _redis:
        try:
            payload = json.dumps(value)
            if ttl:
                _redis.set(key, payload, ex=ttl)
            else:
                _redis.set(key, payload)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("[LeadAI cache] redis set failed: %s", exc)
    with _lock:
        _local[key] = ((time.time() + ttl) if ttl else None, value)


def get_value(key: str) -> Any | None:
    if _redis:
        try:
            raw = _redis.get(key)
            return json.loads(raw) if raw else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("[LeadAI cache] redis get failed: %s", exc)
    with _lock:
        item = _local.get(key)
        if not item:
            return None
        expires, value = item
        if expires and expires < time.time():
            _local.pop(key, None)
            return None
        return value


def incr(key: str, ttl: int = 60) -> int:
    """Atomic-ish counter used for rate limiting. Returns the new count."""
    if _redis:
        try:
            count = _redis.incr(key)
            if count == 1:
                _redis.expire(key, ttl)
            return int(count)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[LeadAI cache] redis incr failed: %s", exc)
    with _lock:
        item = _local.get(key)
        now = time.time()
        if item and item[0] and item[0] < now:
            item = None
        current = (item[1] if item else 0) + 1
        expires = item[0] if item else now + ttl
        _local[key] = (expires, current)
        # Opportunistic sweep so a long-lived process doesn't leak keys.
        if len(_local) > 10000:
            for k in [k for k, v in _local.items() if v[0] and v[0] < now]:
                _local.pop(k, None)
        return current


def clear_prefix(prefix: str) -> None:
    if _redis:
        try:
            for key in _redis.scan_iter(f"{prefix}*"):
                _redis.delete(key)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("[LeadAI cache] redis clear failed: %s", exc)
    with _lock:
        for key in [k for k in _local if k.startswith(prefix)]:
            _local.pop(key, None)
