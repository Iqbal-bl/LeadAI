"""Live-call transcript events reach the inbox once, in order, and stay put.

Reproduces what the browser's WebSocket log showed on the conversation socket: the same
finished message delivered twice, and messages that drift and interleave. Uses the real
ConnectionManager with in-memory fake sockets: no network and no database.
Run: python tests/test_transcript_ws.py
"""
import ast
import asyncio
import builtins
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.websocket_manager import ConnectionManager  # noqa: E402


class FakeSocket:
    """Stands in for a browser WebSocket: records every text frame the manager sends."""

    def __init__(self):
        self.frames: list[dict] = []

    async def accept(self): ...
    async def close(self): ...

    async def send_text(self, text: str):
        self.frames.append(json.loads(text))


async def subscribe(manager, bucket, key):
    ws = FakeSocket()
    async with manager._lock:
        await manager._unsafe_add(bucket, key, ws)
    return ws


async def settle():
    await asyncio.sleep(0.05)     # let the per-socket writer tasks drain their queues


LONG_AGENT = ("But what is there something specific you want to know about the flats "
              "because I can share prices sizes and possession dates for each tower")   # 26 words
LONG_USER = "I would like to hear more about the neighbourhood and also the schools nearby please"  # 14 words


def run(coro):
    return asyncio.run(coro)


def test_a_finished_message_is_delivered_exactly_once_as_final():
    async def go():
        m = ConnectionManager()
        ws = await subscribe(m, "leadai_conversation", "conv-1")
        await m.broadcast_transcript_to_call("conv-1", {"type": "agent", "text": LONG_AGENT}, delay=0)
        await settle()
        return ws.frames
    frames = run(go())
    finals = [f for f in frames if f["final"]]
    assert len(finals) == 1 and finals[0]["text"] == LONG_AGENT            # was sent twice
    assert all(len(f["text"].split()) < len(LONG_AGENT.split()) for f in frames if not f["final"])
    assert len({f["id"] for f in frames}) == 1                              # one message, one id
    texts = [f["text"] for f in frames]
    assert len(texts) == len(set(texts))                                    # no repeated payload at all


def test_every_update_of_a_message_keeps_the_message_start_time():
    async def go():
        m = ConnectionManager()
        ws = await subscribe(m, "leadai_conversation", "conv-1")
        await m.broadcast_transcript_to_call("conv-1", {"type": "agent", "text": LONG_AGENT}, delay=0.02)
        await settle()
        return ws.frames
    frames = run(go())
    assert len(frames) > 2
    assert len({f["timestamp"] for f in frames}) == 1       # it used to be "now" on every chunk, so the
                                                            # message drifted later than the next one


def test_a_short_message_is_a_single_final_payload():
    async def go():
        m = ConnectionManager()
        ws = await subscribe(m, "leadai_conversation", "conv-1")
        await m.broadcast_transcript_to_call("conv-1", {"type": "user", "text": "Yes it is fine"}, delay=0)
        await settle()
        return ws.frames
    frames = run(go())
    assert len(frames) == 1 and frames[0]["final"] and frames[0]["text"] == "Yes it is fine"


def test_messages_are_delivered_whole_and_in_the_order_they_were_queued():
    async def go():
        m = ConnectionManager()
        ws = await subscribe(m, "leadai_conversation", "conv-1")
        # queued back to back, the way the voice app does with create_task(...)
        await asyncio.gather(
            m.broadcast_transcript_to_call("conv-1", {"id": "A", "type": "agent", "text": LONG_AGENT}, delay=0.01),
            m.broadcast_transcript_to_call("conv-1", {"id": "B", "type": "user", "text": LONG_USER}, delay=0.01),
            m.broadcast_transcript_to_call("conv-1", {"id": "C", "type": "agent", "text": "Sure"}, delay=0.01),
        )
        await settle()
        return ws.frames
    frames = run(go())
    ids = [f["id"] for f in frames]
    assert ids == sorted(ids, key="ABC".index)                  # A fully, then B fully, then C: never interleaved
    seqs = [f["seq"] for f in frames]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)  # a client can always sort by seq
    assert [f["id"] for f in frames if f["final"]] == ["A", "B", "C"]


def test_a_status_change_reaches_the_conversation_socket_once():
    async def go():
        m = ConnectionManager()
        conv = await subscribe(m, "leadai_conversation", "conv-1")
        legacy = await subscribe(m, "transcript", "conv-1")
        # exactly what call-status does for a terminal status, addressed to the conversation
        await m.broadcast_to_call("conv-1", "completed")
        await m.broadcast_transcript_event_to_call("conv-1", {"type": "status", "status": "completed"})
        await settle()
        return conv.frames, legacy.frames
    conv_frames, legacy_frames = run(go())
    assert [f["type"] for f in conv_frames] == ["call_status"]             # was call_status AND status
    assert [f["type"] for f in legacy_frames] == ["status"]                # older clients still get theirs


def test_other_control_events_are_still_forwarded_to_the_conversation():
    async def go():
        m = ConnectionManager()
        conv = await subscribe(m, "leadai_conversation", "conv-1")
        await m.broadcast_transcript_event_to_call("conv-1", {"type": "handoff", "to": "agent"})
        await settle()
        return conv.frames
    assert run(go()) == [{"type": "handoff", "to": "agent"}]


def test_cleanup_forgets_the_per_call_ordering_state():
    async def go():
        m = ConnectionManager()
        await m.broadcast_transcript_to_call("CA1", {"type": "user", "text": "hello there"}, delay=0)
        await m.broadcast_transcript_to_call("conv-1", {"type": "user", "text": "hello there"}, delay=0,
                                             store_history=False)
        assert "CA1" in m._transcript_locks and "conv-1" in m._transcript_locks
        assert "conv-1" not in m.transcript_history                        # conversation history not kept
        await m.cleanup_call_only_connections("CA1", "conv-1")
        return m
    m = run(go())
    assert not m._transcript_locks and not m._transcript_seq and not m.transcript_history


def test_the_voice_apps_broadcast_function_only_uses_names_it_has():
    """The function swallows every error, so a missing import would silently kill the transcript."""
    tree = ast.parse((ROOT / "outbound" / "app.py").read_text(encoding="utf-8"))
    module_names = set(dir(builtins))
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module_names |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                module_names |= {n.id for n in ast.walk(t) if isinstance(n, ast.Name)}
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "broadcast_transcript")
    local = {a.arg for a in fn.args.args} | {n.id for n in ast.walk(fn)
                                            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    missing = sorted(used - local - module_names)
    assert not missing, f"broadcast_transcript uses names that are not defined: {missing}"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
