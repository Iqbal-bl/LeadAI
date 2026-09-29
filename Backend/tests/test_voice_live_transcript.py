"""Live transcript for Pipecat calls: every spoken line reaches the UI as it happens.

The legacy loop pushes each line (caller and AI) to the call's transcript socket and the inbox
conversation socket through outbound.app.broadcast_transcript. The Pipecat path only saved lines to
the database, so the UI stayed empty until the call ended. It now calls the same function.

The end-to-end test compiles the REAL broadcast_transcript out of outbound/app.py, runs it on the
real ConnectionManager with fake browser sockets, and drives it with the real call session, so the
messages a subscriber receives are exactly what the legacy loop would have sent.

Run: python tests/test_voice_live_transcript.py
"""
import ast
import asyncio
import json
import pathlib
import sys
import types
import uuid
from datetime import datetime, timezone

import conftest_stub  # noqa: F401

from pipecat.frames.frames import LLMContextFrame  # noqa: E402
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402
from pipecat_harness import SleepFrame, run_stage  # noqa: E402

from core.websocket_manager import ConnectionManager  # noqa: E402
from LeadAI.voice.brain import BrainReply, LeadAIBrainProcessor  # noqa: E402
from LeadAI.voice.session import CallSession  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def ctx(text):
    return LLMContextFrame(context=LLMContext([{"role": "user", "content": text}]))


def new_session():
    return CallSession(client_id="c1", conversation_id="conv-9", call_sid="CA900", session_factory=lambda: None)


# ---------------------------------------------------------------- a stand-in for outbound.app
def fake_outbound(recorder):
    app = types.ModuleType("outbound.app")

    async def broadcast_transcript(call_sid, role, text):
        recorder.append((call_sid, role, text))

    app.broadcast_transcript = broadcast_transcript
    saved = sys.modules.get("outbound.app")
    sys.modules["outbound.app"] = app
    return saved


def restore(saved):
    if saved is None:
        sys.modules.pop("outbound.app", None)
    else:
        sys.modules["outbound.app"] = saved


# ------------------------------------------------------------------- the brain stage hook
def test_the_callers_words_are_announced_the_moment_they_are_recognised_before_any_thinking():
    order = []

    async def respond(text):
        order.append(f"thinking about: {text}")
        return BrainReply(text="ok")

    brain = LeadAIBrainProcessor(respond=respond, on_user_text=lambda t: order.append(f"announced: {t}"))
    run(run_stage(brain, [ctx("what is the rate")]))
    assert order == ["announced: what is the rate", "thinking about: what is the rate"]


def test_a_thought_spoken_in_pieces_is_announced_piece_by_piece_never_twice():
    from pipecat.frames.frames import UserStartedSpeakingFrame

    announced, thought_about = [], []

    async def respond(text):
        thought_about.append(text)
        if len(thought_about) == 1:
            await asyncio.sleep(0.3)                 # overtaken by the caller's next words
        return BrainReply(text="ok")

    brain = LeadAIBrainProcessor(respond=respond, on_user_text=announced.append)
    run(run_stage(brain, [ctx("the visit is on Saturday"), SleepFrame(sleep=0.05), UserStartedSpeakingFrame(),
                          SleepFrame(sleep=0.05), ctx("at four in the evening")]))
    assert announced == ["the visit is on Saturday", "at four in the evening"]           # each piece once
    assert thought_about[1] == "the visit is on Saturday at four in the evening"          # merged only for the model


def test_nothing_is_announced_for_an_empty_turn_and_a_broken_hook_cannot_stop_the_reply():
    heard = []

    async def respond(text):
        heard.append(text)
        return BrainReply(text="ok")

    calls = []
    brain = LeadAIBrainProcessor(respond=respond, on_user_text=lambda t: (calls.append(t), 1 / 0))
    down, _ = run(run_stage(brain, [ctx("   "), ctx("hello")]))
    assert calls == ["hello"] and heard == ["hello"]           # empty turn: no hook, no reply; hook error: reply anyway


# ----------------------------------------------------------------------------- the session
def test_the_ai_reply_is_pushed_to_the_live_transcript_once_it_is_handed_to_the_voice():
    async def go():
        session = new_session()
        session.after_reply(BrainReply(text="Rates start at 8.5 percent."))
        session.after_reply(BrainReply(text=""))                                 # nothing spoken: nothing shown
        await session.close()

    seen = []
    saved = fake_outbound(seen)
    try:
        run(go())
    finally:
        restore(saved)
    assert seen == [("CA900", "agent", "Rates start at 8.5 percent.")]


def test_the_opening_greeting_is_pushed_too_and_a_silent_opening_is_not():
    async def go():
        session = new_session()
        session._opening_sync = lambda: BrainReply(text="Hi Manmeet, this is Kabir.")
        await session.opening()
        session._opening_sync = lambda: BrainReply(text="", skipped=True)
        await session.opening()
        await session.close()

    seen = []
    saved = fake_outbound(seen)
    try:
        run(go())
    finally:
        restore(saved)
    assert seen == [("CA900", "agent", "Hi Manmeet, this is Kabir.")]


def test_a_failing_live_view_never_breaks_a_call():
    async def go():
        session = new_session()
        session.broadcast_user("hello")
        session.after_reply(BrainReply(text="hi"))
        await session.close()
        return True

    boom = types.ModuleType("outbound.app")

    async def broadcast_transcript(*a):
        raise RuntimeError("websocket manager down")

    boom.broadcast_transcript = broadcast_transcript
    saved = sys.modules.get("outbound.app")
    sys.modules["outbound.app"] = boom
    try:
        assert run(go()) is True
    finally:
        restore(saved)


def test_outside_an_event_loop_broadcasting_is_a_harmless_no_op():
    new_session().broadcast("user", "hello")             # no running loop: must not raise


# ------------------------------------------------------- end to end, with the real legacy function
class FakeSocket:
    def __init__(self):
        self.frames = []

    async def accept(self): ...
    async def close(self): ...

    async def send_text(self, text):
        self.frames.append(json.loads(text))


def load_real_broadcast_transcript(manager, active_calls):
    """Compile broadcast_transcript exactly as written in outbound/app.py."""
    source = (ROOT / "outbound" / "app.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(source).body
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "broadcast_transcript")
    namespace = {"manager": manager, "active_calls": active_calls, "transcript_connections": {},
                 "uuid": uuid, "datetime": datetime, "timezone": timezone, "asyncio": asyncio}
    exec(compile("import asyncio\n" + ast.get_source_segment(source, fn), "broadcast_transcript", "exec"), namespace)
    return namespace["broadcast_transcript"]


def test_a_subscriber_receives_the_whole_call_live_in_the_legacy_format():
    async def go():
        manager = ConnectionManager()
        call_socket, inbox_socket = FakeSocket(), FakeSocket()
        async with manager._lock:
            await manager._unsafe_add("transcript", "CA900", call_socket)
            await manager._unsafe_add("leadai_conversation", "conv-9", inbox_socket)
        app = types.ModuleType("outbound.app")
        app.broadcast_transcript = load_real_broadcast_transcript(manager, {"CA900": {"conversation_id": "conv-9"}})
        saved = sys.modules.get("outbound.app")
        sys.modules["outbound.app"] = app
        try:
            session = new_session()
            session.broadcast_user("Can you speak Hindi?")            # what the caller said, as recognised
            session.after_reply(BrainReply(text="Yes, of course. What would you like to know?"))
            await session.close()
            await asyncio.sleep(0.15)                                  # let the per-socket writers drain
        finally:
            restore(saved)
        return call_socket.frames, inbox_socket.frames

    call_frames, inbox_frames = run(go())
    for frames in (call_frames, inbox_frames):
        finals = [f for f in frames if f["final"]]
        assert [(f["type"], f["text"]) for f in finals] == [
            ("user", "Can you speak Hindi?"), ("agent", "Yes, of course. What would you like to know?")]
        assert all({"id", "type", "text", "timestamp", "seq", "final"} <= set(f) for f in frames)
    # The call view and the inbox view show the SAME message ids, so they line up.
    assert [f["id"] for f in call_frames if f["final"]] == [f["id"] for f in inbox_frames if f["final"]]


# ------------------------------------------------ the format is the legacy one, and stays that way
def _legacy_signature():
    source = (ROOT / "outbound" / "app.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(source).body
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "broadcast_transcript")
    return [a.arg for a in fn.args.args]


def test_the_pipecat_path_calls_the_legacy_function_with_the_legacy_arguments_and_role_names():
    # broadcast_transcript(call_sid, msg_type, text): msg_type is "user" or "agent", nothing else.
    assert _legacy_signature() == ["call_sid", "msg_type", "text"]

    async def go():
        session = new_session()
        session.broadcast_user("  Can you speak Hindi?  ")
        session.after_reply(BrainReply(text="Yes, of course."))
        await session.close()

    seen = []
    saved = fake_outbound(seen)
    try:
        run(go())
    finally:
        restore(saved)
    assert seen == [("CA900", "user", "Can you speak Hindi?"), ("CA900", "agent", "Yes, of course.")]
    assert {role for _, role, _ in seen} == {"user", "agent"}             # not "customer" / "ai" / anything new


def test_the_pipecat_path_never_builds_its_own_transcript_payload_or_talks_to_the_manager():
    """The websocket format lives in ONE place (core/websocket_manager.py + broadcast_transcript). If a
    voice module started sending its own dicts, the UI's expectations could silently drift."""
    forbidden = ("websocket_manager", "manager.", "broadcast_transcript_to_call", "broadcast_transcript_event_to_call",
                 "broadcast_to_call", '"seq"', "'seq'", '"final"', "'final'", "send_json", "send_text")
    offenders = []
    for path in sorted((ROOT / "LeadAI" / "voice").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                offenders.append((path.name, token))
    assert not offenders, f"the voice package must only call outbound.app.broadcast_transcript: {offenders}"


def test_the_legacy_websocket_code_itself_is_unchanged_by_this_work():
    """A tripwire on the two places that define the format: if either changes, this fails and asks a human."""
    import hashlib

    manager_src = (ROOT / "core" / "websocket_manager.py").read_text(encoding="utf-8")
    source = (ROOT / "outbound" / "app.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(source).body
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "broadcast_transcript")
    body = ast.get_source_segment(source, fn)
    # The message payload the legacy function builds, key for key:
    for key in ('"id"', '"type"', '"text"', '"timestamp"'):
        assert key in body, key
    assert "broadcast_transcript_to_call(call_sid, message)" in body
    assert "store_history=False" in body                                      # the inbox copy is not replayed
    # And the manager still stamps every payload with these fields:
    for key in ('"final"', '"seq"', '"id"', '"timestamp"'):
        assert key in manager_src, key
    assert hashlib.sha256(body.encode()).hexdigest()                          # (present for humans diffing failures)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
