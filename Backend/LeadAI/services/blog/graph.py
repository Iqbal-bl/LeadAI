"""
LangGraph compilation for the LeadAI Blog Generation Workflow.
"""
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .planner import orchestrator_node
from .reducer import decide_images, generate_and_place_images, merge_content
from .researcher import research_node
from .router import route_next, router_node
from .state import State
from .worker import worker_node


def fanout(state: State):
    """Distribute planned tasks to parallel workers."""
    plan = state.get("plan")
    if plan is None:
        raise ValueError("Plan is missing before fanout.")

    return [
        Send(
            "worker",
            {
                "topic": state["topic"],
                "mode": state.get("mode", "closed_book"),
                "as_of": state.get("as_of", "2026-09-01"),
                "recency_days": state.get("recency_days", 3650),
                "evidence": [
                    e.model_dump() for e in state.get("evidence", [])
                ],
                "plan": plan.model_dump(),
                "task": task.model_dump(),
            },
        )
        for task in plan.tasks
    ]


# Build StateGraph
builder = StateGraph(State)

# Add Nodes
builder.add_node("router", router_node)
builder.add_node("research", research_node)
builder.add_node("orchestrator", orchestrator_node)
builder.add_node("worker", worker_node)
builder.add_node("merge_content", merge_content)
builder.add_node("decide_images", decide_images)
builder.add_node("generate_and_place_images", generate_and_place_images)

# Flow Edges
builder.add_edge(START, "router")
builder.add_conditional_edges(
    "router",
    route_next,
    {
        "research": "research",
        "orchestrator": "orchestrator",
    },
)

builder.add_edge("research", "orchestrator")
builder.add_conditional_edges("orchestrator", fanout)
builder.add_edge("worker", "merge_content")
builder.add_edge("merge_content", "decide_images")
builder.add_edge("decide_images", "generate_and_place_images")
builder.add_edge("generate_and_place_images", END)

# Compile Graph
blog_graph = builder.compile()
