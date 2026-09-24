"""
Router node for blog generation workflow.
Decides whether research is needed (closed_book, hybrid, open_book) and generates queries.
"""
import os
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ...config import settings
from .schemas import RouterDecision
from .state import State

ROUTER_SYSTEM = """
You are a routing module for a high-quality technical and business blog planner.
Decide whether web research is needed BEFORE planning.

Modes:
- closed_book (needs_research=false):
  Evergreen topics where correctness does not depend on recent facts (concepts, foundational strategies, best practices).

- hybrid (needs_research=true):
  Mostly evergreen but needs up-to-date examples, recent statistics, modern tools, or current industry frameworks to be compelling.

- open_book (needs_research=true):
  Volatile or time-sensitive topics: weekly roundups, recent news, breakthroughs, pricing updates, regulation changes.

If needs_research=true:
- Output 3 to 6 high-signal, specific search queries.
- Avoid overly generic queries like "AI" or "Lead Gen". Focus on concrete subtopics.
"""


def _get_llm():
    api_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY")
    return ChatOpenAI(
        model=settings.openai_model or "gpt-4o-mini",
        api_key=api_key,
        temperature=0.2,
    )


def router_node(state: State) -> dict:
    """Decide whether the topic requires external research and generate search queries."""
    topic = state["topic"]
    llm = _get_llm()
    decider = llm.with_structured_output(RouterDecision)
    
    decision: RouterDecision = decider.invoke(
        [
            SystemMessage(content=ROUTER_SYSTEM),
            HumanMessage(
                content=f"Topic: {topic}\nAs-of date: {state.get('as_of', '2026-09-01')}"
            ),
        ]
    )

    if decision.mode == "open_book":
        recency_days = 7
    elif decision.mode == "hybrid":
        recency_days = 45
    else:
        recency_days = 3650

    return {
        "needs_research": decision.needs_research,
        "mode": decision.mode,
        "queries": decision.queries,
        "recency_days": recency_days,
    }


def route_next(state: State) -> str:
    """Routing edge after router node."""
    if state.get("needs_research"):
        return "research"
    return "orchestrator"
