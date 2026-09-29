"""LLM gateway: retry only on transient failures, per-channel profiles, trace hooks.

The network is faked (gateway._post). Run: python tests/test_engine_gateway.py
"""
import conftest_stub  # noqa: F401  — installs core.* stubs before LeadAI imports

import httpx

from LeadAI.config import settings as real_settings
from LeadAI.engine import gateway
from LeadAI.services import llm


class _Settings:
    llm_enabled = True
    openai_api_key = "test-key"
    openai_base_url = "http://fake"
    openai_model = "fake-model"
    openai_timeout = 5.0

    def __getattr__(self, name):
        return getattr(real_settings, name)


class _NoKey(_Settings):
    llm_enabled = False


def _response(status, body=None):
    req = httpx.Request("POST", "http://fake/chat/completions")
    return httpx.Response(status, json=body or {}, request=req)


def _ok(text="hello"):
    return _response(
        200,
        {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}},
    )


class _Net:
    """Scripted gateway._post: each call pops the next outcome (a response or an exception)."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.payloads = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.payloads.append({"json": json, "timeout": timeout})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _run(net, settings=None, **kwargs):
    saved = (gateway._post, gateway.settings, gateway.time.sleep)
    gateway._post, gateway.settings, gateway.time.sleep = net, settings or _Settings(), lambda s: None
    gateway.clear_trace_hooks()
    try:
        return gateway.complete("sys", [{"role": "user", "content": "hi"}], **kwargs)
    finally:
        gateway._post, gateway.settings, gateway.time.sleep = saved


def test_no_key_returns_none_without_calling_the_network():
    net = _Net()
    text, meta = _run(net, _NoKey())
    assert text is None and meta["provider"] == "builtin-extractive" and net.payloads == []


def test_success_returns_text_and_token_counts():
    text, meta = _run(_Net(_ok("hello")))
    assert text == "hello"
    assert meta["attempts"] == 1 and meta["prompt_tokens"] == 7 and meta["completion_tokens"] == 3
    assert "error" not in meta


def test_transient_503_is_retried_once():
    net = _Net(_response(503), _ok("recovered"))
    text, meta = _run(net)
    assert text == "recovered" and meta["attempts"] == 2 and "error" not in meta


def test_rate_limit_429_is_retried():
    text, meta = _run(_Net(_response(429), _ok("ok")))
    assert text == "ok" and meta["attempts"] == 2


def test_bad_key_401_is_not_retried():
    net = _Net(_response(401), _ok("never reached"))
    text, meta = _run(net)
    assert text is None and meta["attempts"] == 1 and "401" in meta["error"]
    assert len(net.payloads) == 1


def test_read_timeout_is_not_retried():
    net = _Net(httpx.ReadTimeout("slow"), _ok("never reached"))
    text, meta = _run(net)
    assert text is None and meta["attempts"] == 1


def test_connect_error_is_retried():
    text, meta = _run(_Net(httpx.ConnectError("down"), _ok("back")))
    assert text == "back" and meta["attempts"] == 2


def test_retries_are_bounded():
    net = _Net(_response(503), _response(503), _ok("too late"))
    text, meta = _run(net)
    assert text is None and meta["attempts"] == 2


def test_voice_profile_is_tight_and_never_retries():
    net = _Net(_response(503), _ok("never reached"))
    text, meta = _run(net, profile="voice")
    assert text is None and meta["attempts"] == 1 and meta["profile"] == "voice"
    sent = net.payloads[0]
    assert sent["json"]["max_tokens"] == 220 and sent["timeout"] == 15.0


def test_explicit_arguments_override_the_profile():
    net = _Net(_ok())
    _run(net, profile="voice", max_tokens=50, temperature=0.9)
    assert net.payloads[0]["json"]["max_tokens"] == 50 and net.payloads[0]["json"]["temperature"] == 0.9


def test_json_mode_sets_response_format():
    net = _Net(_ok("{}"))
    _run(net, json_mode=True)
    assert net.payloads[0]["json"]["response_format"] == {"type": "json_object"}


def test_trace_hook_sees_every_call_and_cannot_break_it():
    seen = []
    saved = (gateway._post, gateway.settings)
    gateway._post, gateway.settings = _Net(_ok("traced")), _Settings()
    gateway.clear_trace_hooks()
    gateway.add_trace_hook(lambda meta, system, messages, reply: seen.append((meta["model"], reply)))
    gateway.add_trace_hook(lambda *a: 1 / 0)  # a broken observer must not affect the reply
    try:
        text, _ = gateway.complete("sys", [])
    finally:
        gateway._post, gateway.settings = saved
        gateway.clear_trace_hooks()
    assert text == "traced" and seen == [("fake-model", "traced")]


def test_a_stale_pooled_connection_is_retried_once_even_on_the_voice_profile():
    # A reused keep-alive connection the server already closed: the request never ran, so one
    # immediate retry on a fresh connection is safe, and it is not counted as a failed attempt.
    text, meta = _run(_Net(httpx.RemoteProtocolError("Server disconnected"), _ok("fresh")), profile="voice")
    assert text == "fresh" and meta["attempts"] == 1


def test_a_stale_connection_is_retried_only_once():
    net = _Net(httpx.RemoteProtocolError("gone"), httpx.RemoteProtocolError("gone again"), _ok("never"))
    text, meta = _run(net, profile="voice")
    assert text is None and len(net.payloads) == 2


def test_every_model_call_shares_one_keep_alive_connection_pool():
    # Opening a fresh connection (TLS handshake included) per call cost ~1-2 s from India to a US
    # endpoint on the first live call. The pool is shared, thread-safe, and expires idle connections
    # before the provider does.
    assert gateway.shared_client() is gateway.shared_client()
    assert gateway.KEEPALIVE_SECONDS < 60


def test_warming_the_connection_never_raises_and_does_nothing_without_a_key():
    saved = gateway.settings
    try:
        gateway.settings = _NoKey()
        gateway.warm_connection()                       # no key: returns without a request
        gateway.settings = _Settings()
        gateway.settings.openai_base_url = "http://127.0.0.1:1"      # unreachable
        gateway.warm_connection()                       # a failing warm-up is swallowed
    finally:
        gateway.settings = saved


def test_llm_wrapper_keeps_its_signature_and_delegates():
    saved = (gateway._post, gateway.settings)
    gateway._post, gateway.settings = _Net(_ok("via wrapper")), _Settings()
    try:
        text, meta = llm.complete("sys", [], temperature=0.1, max_tokens=99)
    finally:
        gateway._post, gateway.settings = saved
    assert text == "via wrapper" and meta["profile"] == "chat"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
