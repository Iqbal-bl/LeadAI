"""Secrets must never reach a log line. Uses made-up tokens shaped like the real ones."""
import io
import logging

from core.log_redaction import install, redact

IG = "IGAAfake" + "A1b2C3d4E5f6G7h8I9j0" * 3
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ0ZXN0LXVzZXIifQ.c2lnbmF0dXJlLXNpZ25hdHVyZQ"


def test_query_param_tokens_are_redacted():
    url = f"GET https://graph.instagram.com/v23.0/123?fields=name%2Cusername&access_token={IG} HTTP/1.1"
    out = redact(url)
    assert IG not in out
    assert "fields=name%2Cusername" in out          # the rest of the URL stays readable
    assert "access_token=[REDACTED]" in out


def test_websocket_login_token_is_redacted():
    line = f'"WebSocket /ws/leadai/conversation/abc?token={JWT}" [accepted]'
    out = redact(line)
    assert JWT not in out and "/ws/leadai/conversation/abc?token=[REDACTED]" in out


def test_bearer_header_and_bare_tokens():
    assert JWT not in redact(f"Authorization: Bearer {JWT}")
    assert IG not in redact(f"token was {IG} for user")
    assert "sk-" + "x" * 30 not in redact("key sk-" + "x" * 30)


def test_normal_lines_are_unchanged():
    line = "[LeadAI] delivered on instagram conv=e11b1f61 provider_id=aWdfZAG1faXRl status_code=200"
    assert redact(line) == line
    assert redact("error_code=403 Forbidden for url https://api.openai.com/v1/embeddings") == \
        "error_code=403 Forbidden for url https://api.openai.com/v1/embeddings"


def test_installed_formatter_redacts_messages_and_tracebacks():
    install()
    install()  # calling twice must not double-wrap
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    log = logging.getLogger("test_log_redaction")
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    try:
        log.info("HTTP Request: GET https://x/y?access_token=%s", IG)
        try:
            raise RuntimeError(f"403 for url https://x/y?access_token={IG}")
        except RuntimeError:
            log.exception("call failed")
    finally:
        log.removeHandler(handler)
    text = stream.getvalue()
    assert IG not in text
    assert text.count("[REDACTED]") >= 3   # message, exception line, traceback message


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
