"""
Logs every API call to the database — what, where, when, who, and the
result. Each router calls log_call() once per request, after the action
completes (success or failure), so it captures the real outcome, not
just "a request came in."
"""

import json
import time

from api.db import ApiRequestLog, SessionLocal


class Timer:
    """Small helper: `with Timer() as t:` then t.duration_ms after."""
    def __enter__(self):
        self._start = time.monotonic()
        return self

    def __exit__(self, *args):
        self.duration_ms = int((time.monotonic() - self._start) * 1000)


def log_call(
    client_id: str,
    platform: str | None,
    action: str,
    endpoint: str,
    request_summary: dict,
    success: bool,
    duration_ms: int,
    result_summary: str = "",
    error_message: str | None = None,
) -> None:
    # Sanitize: never log anything that looks like a secret, even if a
    # caller accidentally includes one in the request body.
    safe_summary = {
        k: v for k, v in request_summary.items()
        if "token" not in k.lower() and "key" not in k.lower() and "secret" not in k.lower()
    }
    session = SessionLocal()
    try:
        session.add(ApiRequestLog(
            client_id=client_id,
            platform=platform,
            action=action,
            endpoint=endpoint,
            request_summary=json.dumps(safe_summary, default=str)[:2000],
            success=success,
            duration_ms=duration_ms,
            result_summary=(result_summary or "")[:2000],
            error_message=(error_message or "")[:2000] if error_message else None,
        ))
        session.commit()
    finally:
        session.close()
