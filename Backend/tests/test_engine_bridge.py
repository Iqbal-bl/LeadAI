"""Engine bridge: off / observe / enforce, and never breaking a reply.

Run: python tests/test_engine_bridge.py
"""
import conftest_stub  # noqa: F401

from LeadAI.engine import bridge, graph
from LeadAI.engine.graph import DECLINED_HANDOFF, UNSUPPORTED_HANDOFF

CTX = ["The processing fee is Rs. 25,000."]
KW = dict(text="fee?", client_id="c1", conversation_id="v1", channel="web")


def _result(reply, **extra):
    return {"reply": reply, "confidence": 0.9, "needs_human": False, "handoff_reason": None,
            "sources": [], "context": CTX, **extra}


def test_off_returns_the_very_same_result():
    r = _result("I don't have that.")
    assert bridge.apply(r, mode="off", **KW) is r


def test_observe_records_but_never_changes_the_decision():
    r = _result("I'm sorry, I don't have information on that.")
    out = bridge.apply(r, mode="observe", **KW)
    assert out["needs_human"] is False and out["reply"] == r["reply"]
    # "escalation" in observe mode means: the engine WOULD have stepped in.
    assert out["engine"]["declined"] is True and out["engine"]["escalation"] is True
    assert r == _result("I'm sorry, I don't have information on that.")  # input untouched


def test_enforce_escalates_a_decline_that_production_left_unflagged():
    out = bridge.apply(_result("I don't have information on car loans."), mode="enforce", **KW)
    assert out["needs_human"] is True and out["handoff_reason"] == DECLINED_HANDOFF
    assert out["engine"]["escalation"] is True


def test_enforce_escalates_an_invented_figure():
    out = bridge.apply(_result("The fee is Rs. 15,000."), mode="enforce", **KW)
    assert out["needs_human"] is True and out["handoff_reason"] == UNSUPPORTED_HANDOFF
    assert out["engine"]["verdict"] == "unsupported" and out["engine"]["unsupported_count"] == 1


def test_enforce_leaves_a_good_answer_alone():
    out = bridge.apply(_result("The fee is Rs. 25,000."), mode="enforce", **KW)
    assert out["needs_human"] is False and out["engine"]["escalation"] is False


def test_an_existing_handoff_reason_is_preserved():
    r = _result("I don't have that.", needs_human=True, handoff_reason="low confidence")
    out = bridge.apply(r, mode="enforce", **KW)
    assert out["handoff_reason"] == "low confidence"


def test_an_engine_failure_falls_back_to_the_original_reply():
    saved = graph.run_turn
    graph.run_turn = lambda *a, **k: 1 / 0
    try:
        r = _result("Hello")
        assert bridge.apply(r, mode="enforce", **KW) is r
    finally:
        graph.run_turn = saved


def test_mode_comes_from_settings_and_bad_values_mean_off():
    class S:
        engine_mode = "observe"

    saved = bridge.settings
    try:
        bridge.settings = S()
        assert bridge.current_mode() == "observe"
        S.engine_mode = "ENFORCE "
        assert bridge.current_mode() == "enforce"
        S.engine_mode = "banana"
        assert bridge.current_mode() == "off"
    finally:
        bridge.settings = saved


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
