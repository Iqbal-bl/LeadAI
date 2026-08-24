# --------------------------------------------------
# agentic_browser_v2 / crag\crag_graph.py
# --------------------------------------------------
"""
The CRAG pipeline: retrieve -> grade each chunk -> branch -> optionally
web search -> generate. A separate small LangGraph from the browser
agent's — no dependency on Playwright/tools/anything browser-related.
"""

from typing import TypedDict

from langgraph.graph import END, StateGraph

from .grader import build_grader, grade_document
from .web_search import web_search

from models import get_text_model


class CragState(TypedDict):
    question: str
    retrieved_docs: list[str]
    relevant_docs: list[str]
    used_web_search: bool
    answer: str


def _extract_text(content) -> str:
    """Gemini responses can be a plain string or a list of content blocks
    — normalize to plain text either way."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content)


def build_crag_graph(retriever, allow_web_search: bool = False):
    grader = build_grader()
    answer_model = get_text_model(temperature=0.2)

    async def retrieve(state: CragState):
        docs = await retriever.ainvoke(state["question"])
        return {"retrieved_docs": [d.page_content for d in docs]}

    async def grade(state: CragState):
        relevant = []
        for doc_text in state["retrieved_docs"]:
            is_relevant = await grade_document(grader, state["question"], doc_text)
            if is_relevant:
                relevant.append(doc_text)
        return {"relevant_docs": relevant}

    def route(state: CragState):
        # Only falls back to web search when NOTHING relevant was found
        # locally (some chunks graded irrelevant is normal for top-k
        # retrieval, not a real gap) — and only if allow_web_search is on
        # at all. Default is off: replies must stay grounded in the local
        # knowledge base only, never outside/general knowledge.
        if len(state["relevant_docs"]) == 0 and allow_web_search:
            return "web_search"
        return "generate"

    async def do_web_search(state: CragState):
        result_text = web_search(state["question"])
        combined = [*state["relevant_docs"], result_text]
        return {"relevant_docs": combined, "used_web_search": True}

    async def generate(state: CragState):
        context = "\n\n---\n\n".join(state["relevant_docs"]) or "(No relevant information found.)"
        prompt = (  
            "Answer the question using ONLY the context below. If the "
            "context doesn't actually contain the answer, say plainly that "
            "you don't have that information rather than guessing — this "
            "matters a lot for anything like pricing, contracts, or "
            "commitments; a wrong guess is worse than admitting you don't "
            "know.\n\n"
            f"Context:\n{context}\n\nQuestion: {state['question']}\n\nAnswer:"
        )
        response = await answer_model.ainvoke(prompt)
        return {"answer": _extract_text(response.content)}

    graph = StateGraph(CragState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("grade", grade)
    graph.add_node("web_search", do_web_search)
    graph.add_node("generate", generate)

    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges("grade", route, {"web_search": "web_search", "generate": "generate"})
    graph.add_edge("web_search", "generate")
    graph.add_edge("generate", END)

    return graph.compile()
