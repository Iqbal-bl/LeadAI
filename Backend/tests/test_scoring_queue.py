"""The chat scoring queue: one scorer per conversation, newest turn wins, one retry.
Only the scorer itself is faked.
Run: python tests/test_scoring_queue.py
"""
import conftest_stub  # noqa: F401

import threading
import time

from LeadAI.services import conversation_flow, scoring_queue

RETRY = scoring_queue.RETRY_DELAY_SECONDS
scoring_queue.RETRY_DELAY_SECONDS = 0.05


def wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def idle():
    return not scoring_queue._running and not scoring_queue._waiting


def test_submit_does_not_block_the_caller():
    gate = threading.Event()
    conversation_flow.run_deferred_scoring = lambda *a: gate.wait(2) or True
    t0 = time.time()
    scoring_queue.submit("c", "conv-block", "m1")
    assert time.time() - t0 < 0.2
    gate.set()
    assert wait(idle)


def test_a_conversation_is_never_scored_twice_at_once_and_the_newest_turn_wins():
    active, peak, seen = [0], [0], []
    gate = threading.Event()

    def fake(client_id, conversation_id, message_id):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        seen.append(message_id)
        gate.wait(2)
        active[0] -= 1
        return True

    conversation_flow.run_deferred_scoring = fake
    scoring_queue.submit("c", "conv-serial", "m1")
    assert wait(lambda: seen == ["m1"])
    scoring_queue.submit("c", "conv-serial", "m2")     # waits behind m1
    scoring_queue.submit("c", "conv-serial", "m3")     # replaces m2
    gate.set()
    assert wait(idle)
    assert seen == ["m1", "m3"], seen                  # m2 coalesced away
    assert peak[0] == 1


def test_different_conversations_score_in_parallel():
    started = []
    gate = threading.Event()

    def fake(client_id, conversation_id, message_id):
        started.append(conversation_id)
        gate.wait(2)
        return True

    conversation_flow.run_deferred_scoring = fake
    scoring_queue.submit("c", "conv-a", "m")
    scoring_queue.submit("c", "conv-b", "m")
    assert wait(lambda: sorted(started) == ["conv-a", "conv-b"])
    gate.set()
    assert wait(idle)


def test_a_failure_is_retried_once_then_given_up():
    calls = []
    conversation_flow.run_deferred_scoring = lambda c, conv, m: calls.append(m) or False
    scoring_queue.submit("c", "conv-fail", "m1")
    assert wait(idle)
    assert calls == ["m1", "m1"]


def test_a_retry_that_succeeds_stops():
    calls = []

    def fake(c, conv, m):
        calls.append(m)
        return len(calls) > 1

    conversation_flow.run_deferred_scoring = fake
    scoring_queue.submit("c", "conv-retry", "m1")
    assert wait(idle)
    assert calls == ["m1", "m1"]


if __name__ == "__main__":
    real = conversation_flow.run_deferred_scoring
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    conversation_flow.run_deferred_scoring = real
