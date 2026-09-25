"""Keeps secrets out of the logs.

Some libraries log full URLs, and Meta / Instagram put the access token in the URL
query (`...?fields=name&access_token=IGAA...`). uvicorn also logs WebSocket URLs that
carry the login token (`?token=eyJ...`). Anything that reaches a log line is copied
into log files and terminals, so it is scrubbed here, in one place, for every logger.

`install()` wraps `logging.Formatter.format`, which every handler uses, so it also
covers exception messages and tracebacks. Call it once, before the app starts.
"""
import logging
import re

_REDACTED = "[REDACTED]"

# name=value pairs in URLs / query strings / form bodies.
_PARAM = re.compile(
    r"(?<![A-Za-z0-9])"
    r"((?:access_token|refresh_token|id_token|fb_exchange_token|verify_token|client_secret|"
    r"app_secret|api_key|apikey|token)=)[^&\s\"'<>)]+",
    re.IGNORECASE,
)
# The "Authorization: Bearer xxx" form.
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
# Tokens recognisable by shape, wherever they appear.
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")
_KEY_SHAPES = re.compile(
    r"\b(?:IGA[A-Za-z0-9_-]{20,}|EAA[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,})"
)


def redact(text: str) -> str:
    """Return `text` with tokens and secrets replaced by [REDACTED]."""
    if not text:
        return text
    text = _PARAM.sub(lambda m: m.group(1) + _REDACTED, text)
    text = _BEARER.sub(lambda m: m.group(1) + _REDACTED, text)
    text = _JWT.sub(_REDACTED, text)
    return _KEY_SHAPES.sub(_REDACTED, text)


_installed = False


def install() -> None:
    """Redact every formatted log line, for all loggers and handlers. Safe to call twice."""
    global _installed
    if _installed:
        return
    original_format = logging.Formatter.format

    def format_redacted(self, record):
        return redact(original_format(self, record))

    logging.Formatter.format = format_redacted
    _installed = True
