"""
The conversation graph.

    guard --(stopped)--> END
      └-(ok)--> answer --> verify --> decide --> END

  guard   Reads control state first. A paused/terminated conversation, or one a human
          has taken over, gets no AI reply. This is the hook the monitor agent uses to
          stop a runaway bot-to-bot loop, and it costs nothing when nobody has.
  answer  Produces the reply. The answering function is INJECTED: today it wraps the
          existing ai_engine.answer(), so behaviour matches production exactly; later
          phases swap in the tool-using version without touching the graph.
  verify  Checks that hard facts (numbers) in the reply exist in the retrieved
          sources. Observe-only by default, so it records a verdict without changing
          any reply until the evaluation shows it is safe to enforce.
  decide  Folds everything into the final reply / handoff decision, and (when
          enforcing) escalates to a human if the reply invented a figure or said in
          words that it cannot answer.

Persistence, qualification, broadcast and delivery stay in conversation_flow for now;
they move behind the graph in later phases. Keeping this graph free of I/O is what
makes it cheap to test and safe to run in shadow mode.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.graph import END, StateGraph

from . import decline, grounding
from .state import (
    CONTROL_ACTIVE,
    CONTROL_STOPPED,
    VERDICT_SUPPORTED,
    VERDICT_UNCHECKED,
    VERDICT_UNSUPPORTED,
    TurnState,
)

AnswerFn = Callable[[TurnState], dict[str, Any]]

UNSUPPORTED_HANDOFF = "Reply stated figures not found in company knowledge"
DECLINED_HANDOFF = "AI could not answer from company knowledge"


def _guard(state: TurnState) -> dict:
    control = state.get("control_status") or CONTROL_ACTIVE
    if control in CONTROL_STOPPED:
        return {"skip_reason": f"conversation {control}"}
    if state.get("human_assigned"):
        return {"skip_reason": "human took over"}
    return {"skip_reason": None}


def _after_guard(state: TurnState) -> str:
    return "skip" if state.get("skip_reason") else "answer"


def _make_answer(answer_fn: AnswerFn):
    def _answer(state: TurnState) -> dict:
        return {"result": answer_fn(state)}

    return _answer


def _verify(state: TurnState) -> dict:
    result = state.get("result") or {}
    # Full chunk text, not the 220-char citation excerpts: checking against a
    # truncated excerpt would flag true statements. When the answering function does
    # not supply it, say "unchecked" rather than guess.
    context = result.get("context")
    if not context:
        return {"verdict": VERDICT_UNCHECKED, "unsupported_figures": []}
    check = grounding.check_reply(
        result.get("reply", ""),
        list(context),
        allowed=[state.get("text", "")],
    )
    if check.supported:
        return {"verdict": VERDICT_SUPPORTED, "unsupported_figures": []}
    return {"verdict": VERDICT_UNSUPPORTED, "unsupported_figures": check.unsupported_raw}


def _make_decide(enforce: bool):
    def _decide(state: TurnState) -> dict:
        result = state.get("result") or {}
        needs_human = bool(result.get("needs_human"))
        reason = result.get("handoff_reason")
        declined = decline.is_decline(result.get("reply"))
        if enforce and state.get("verdict") == VERDICT_UNSUPPORTED:
            # Better an honest handoff than an invented figure sent to a customer.
            needs_human = True
            reason = reason or UNSUPPORTED_HANDOFF
        if enforce and declined and not needs_human:
            # The reply tells the customer a specialist will help; make that true.
            needs_human = True
            reason = reason or DECLINED_HANDOFF
        return {
            "reply": result.get("reply", ""),
            "needs_human": needs_human,
            "handoff_reason": reason,
            "declined": declined,
        }

    return _decide


def _skip(state: TurnState) -> dict:
    return {"reply": "", "needs_human": False, "handoff_reason": None, "result": {}}


def build_graph(answer_fn: AnswerFn, *, enforce: bool = False):
    """Compile the graph. `answer_fn` is called with the TurnState."""
    g = StateGraph(TurnState)
    g.add_node("guard", _guard)
    g.add_node("skip", _skip)
    g.add_node("answer", _make_answer(answer_fn))
    g.add_node("verify", _verify)
    g.add_node("decide", _make_decide(enforce))

    g.set_entry_point("guard")
    g.add_conditional_edges("guard", _after_guard, {"skip": "skip", "answer": "answer"})
    g.add_edge("skip", END)
    g.add_edge("answer", "verify")
    g.add_edge("verify", "decide")
    g.add_edge("decide", END)
    return g.compile()


def run_turn(state: TurnState, answer_fn: AnswerFn, *, enforce: bool = False) -> TurnState:
    """Run one turn through the graph and return the final state.

    enforce=False records verdicts (grounding, declined) without changing the decision;
    enforce=True lets them escalate to a human.
    """
    return build_graph(answer_fn, enforce=enforce).invoke(state)
