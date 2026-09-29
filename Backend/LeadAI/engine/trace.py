"""
Decision trace: a record of every step a turn took and why.

    trace = TurnTrace(conversation_id=..., client_id=..., channel=...)
    trace.step("retrieve", "hits=3", top_score=0.71, chunk_ids=[...])
    ...
    message.TraceJson = trace.as_json()

Two outputs from the same call:
  * a log line per step on the `leadai.trace` logger, `key=value` like the rest of the
    platform's logs, so ops can grep one conversation or one decision type;
  * a JSON document stored on the message the turn produced, so "why did the AI say
    that?" is answerable months later from the database, not from logs that rotated.

WHAT GOES IN, AND WHAT NEVER DOES
Ids, counts, scores, thresholds, enum decisions and reasons. Never message text: the
conversation is already stored in leadai_messages (tenant-scoped, access-controlled),
and log files are not. Keys that look like content (`text`, `reply`, `content`,
`message`, `question`, `prompt`) are dropped here as a second line of defence, so a
careless call site cannot leak a customer's words into a log. The platform-wide token
redaction (core/log_redaction.py) still applies on top of that.

A trace never raises. Observability must not be able to cost a customer their reply.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from ..config import settings

logger = logging.getLogger("leadai.trace")

TRACE_VERSION = 1
MAX_STEPS = 80
MAX_STR = 200
MAX_LIST = 10

# Detail keys that would carry conversation content. Dropped, never logged or stored.
_CONTENT_KEYS = frozenset(
    {"text", "reply", "content", "message", "question", "prompt", "transcript", "body", "utterance"}
)


def _flag(name: str, default: bool) -> bool:
    return bool(getattr(settings, name, default))


def _clean(value: Any) -> Any:
    """Make a detail value small, flat and JSON-safe."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, str):
        return value if len(value) <= MAX_STR else value[: MAX_STR - 1] + "…"
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_clean(v) for v in list(value)[:MAX_LIST]]
        return items + [f"+{len(value) - MAX_LIST} more"] if len(value) > MAX_LIST else items
    if isinstance(value, dict):
        return {str(k)[:40]: _clean(v) for k, v in list(value.items())[:MAX_LIST * 2]}
    return str(value)[:MAX_STR]


def _fmt(value: Any) -> str:
    """Render a value for a key=value log line, quoting anything with whitespace."""
    if isinstance(value, (list, dict)):
        import json

        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = str(value)
    return f'"{text}"' if (" " in text or not text) else text


class TurnTrace:
    """Collects the steps of one turn."""

    def __init__(
        self,
        *,
        conversation_id: str | None,
        client_id: str | None,
        channel: str | None,
        turn_id: str | None = None,
    ) -> None:
        self.trace_id = uuid.uuid4().hex[:12]
        self.conversation_id = conversation_id
        self.client_id = client_id
        self.channel = channel
        self.turn_id = turn_id
        self.steps: list[dict[str, Any]] = []
        self._t0 = time.perf_counter()
        self._log = _flag("engine_trace_log", True)
        self._store = _flag("engine_trace_store", True)

    # ------------------------------------------------------------------ record
    def step(self, name: str, decision: str, **detail: Any) -> None:
        """Record one decision. `decision` is a short verdict; `detail` is the evidence."""
        try:
            if len(self.steps) >= MAX_STEPS:
                return
            ms = int((time.perf_counter() - self._t0) * 1000)
            clean = {k: _clean(v) for k, v in detail.items() if k.lower() not in _CONTENT_KEYS}
            self.steps.append({"step": name, "decision": str(decision)[:MAX_STR], "ms": ms, **({"detail": clean} if clean else {})})
            if self._log and logger.isEnabledFor(logging.INFO):
                fields = " ".join(f"{k}={_fmt(v)}" for k, v in clean.items())
                logger.info(
                    "[LeadAI trace] t=%s conv=%s ch=%s step=%s decision=%s ms=%d%s",
                    self.trace_id,
                    (self.conversation_id or "-")[:8],
                    self.channel or "-",
                    name,
                    _fmt(str(decision)[:MAX_STR]),
                    ms,
                    f" {fields}" if fields else "",
                )
        except Exception:  # noqa: BLE001 — observability must never break a turn
            logger.debug("trace step failed", exc_info=True)

    # ------------------------------------------------------------------ output
    def as_json(self) -> dict | None:
        """The document to store on the message, or None when storage is switched off."""
        if not self._store:
            return None
        return {
            "v": TRACE_VERSION,
            "trace_id": self.trace_id,
            "turn_id": self.turn_id,
            "channel": self.channel,
            "total_ms": int((time.perf_counter() - self._t0) * 1000),
            "steps": self.steps,
        }

    def decision(self, name: str) -> str | None:
        """The decision recorded for the first step called `name`, if any."""
        return next((s["decision"] for s in self.steps if s["step"] == name), None)


def step(trace: TurnTrace | None, name: str, decision: str, **detail: Any) -> None:
    """Record on `trace` when there is one. Lets call sites stay one line and optional."""
    if trace is not None:
        trace.step(name, decision, **detail)
