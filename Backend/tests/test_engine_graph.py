"""Conversation graph: guard, answer, verify, decide.

The answering function is injected, so this needs no database, no LLM and no network.
Run: python tests/test_engine_graph.py
"""
import conftest_stub  # noqa: F401

from LeadAI.engine.graph import UNSUPPORTED_HANDOFF, run_turn
from LeadAI.engine.state import VERDICT_SUPPORTED, VERDICT_UNCHECKED, VERDICT_UNSUPPORTED

CONTEXT = ["The processing fee is Rs. 25,000. The rate starts at 8.5%."]


class Answerer:
    def __init__(self, **result):
        self.calls = 0
        self.result = {"reply": "Hello", "confidence": 0.9, "needs_human": False,
                       "handoff_reason": None, **result}

    def __call__(self, state):
        self.calls += 1
        return dict(self.result)


def _turn(answerer, **state):
    return run_turn({"text": "what is the fee?", "client_id": "c1", "conversation_id": "v1", **state},
                    answerer)


def test_normal_turn_passes_the_answer_through():
    a = Answerer(reply="The fee is Rs. 25,000.", context=CONTEXT)
    out = _turn(a)
    assert a.calls == 1
    assert out["reply"] == "The fee is Rs. 25,000." and out["needs_human"] is False
    assert out["verdict"] == VERDICT_SUPPORTED


def test_paused_conversation_gets_no_reply_and_no_model_call():
    a = Answerer()
    out = _turn(a, control_status="paused")
    assert a.calls == 0 and out["reply"] == "" and out["skip_reason"] == "conversation paused"


def test_terminated_conversation_gets_no_reply():
    a = Answerer()
    out = _turn(a, control_status="terminated")
    assert a.calls == 0 and out["skip_reason"] == "conversation terminated"


def test_human_takeover_keeps_the_ai_silent():
    a = Answerer()
    out = _turn(a, human_assigned=True)
    assert a.calls == 0 and out["skip_reason"] == "human took over" and out["reply"] == ""


def test_active_is_the_default():
    a = Answerer()
    assert _turn(a)["skip_reason"] is None and a.calls == 1


def test_no_context_means_unchecked_not_a_false_alarm():
    out = _turn(Answerer(reply="The fee is Rs. 99."))
    assert out["verdict"] == VERDICT_UNCHECKED and out["reply"] == "The fee is Rs. 99."


def test_unsupported_figure_is_recorded_but_not_blocked_by_default():
    out = _turn(Answerer(reply="The fee is Rs. 15,000.", context=CONTEXT))
    assert out["verdict"] == VERDICT_UNSUPPORTED and out["unsupported_figures"] == ["15,000"]
    assert out["needs_human"] is False and out["reply"] == "The fee is Rs. 15,000."


def test_enforced_grounding_escalates_instead_of_sending_an_invented_figure():
    a = Answerer(reply="The fee is Rs. 15,000.", context=CONTEXT)
    out = run_turn({"text": "fee?", "client_id": "c1", "conversation_id": "v1"}, a, enforce_grounding=True)
    assert out["verdict"] == VERDICT_UNSUPPORTED
    assert out["needs_human"] is True and out["handoff_reason"] == UNSUPPORTED_HANDOFF


def test_existing_handoff_reason_is_kept_when_enforcing():
    a = Answerer(reply="Rs. 15,000", context=CONTEXT, needs_human=True, handoff_reason="low confidence")
    out = run_turn({"text": "fee?", "client_id": "c1", "conversation_id": "v1"}, a, enforce_grounding=True)
    assert out["handoff_reason"] == "low confidence"


def test_number_the_customer_said_is_not_flagged():
    a = Answerer(reply="A budget of 50 lakh is fine.", context=CONTEXT)
    out = _turn(a, text="my budget is 50 lakh")
    assert out["verdict"] == VERDICT_SUPPORTED


def test_engine_keeps_no_state_between_turns():
    a = Answerer(reply="Hi")
    _turn(a, control_status="paused")
    out = _turn(a)                      # same answerer, next turn, no longer paused
    assert out["skip_reason"] is None and out["reply"] == "Hi"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
