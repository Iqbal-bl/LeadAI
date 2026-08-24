"""
Public API for the CRAG module. Everything else in crag/ is an
implementation detail — this is the only file other code (in this project
or a completely different one) should ever import from.

    from crag.engine import CragEngine

    engine = CragEngine(knowledge_dir="knowledge/company_docs")
    answer = await engine.answer("What's your pricing for the enterprise tier?")

Fully portable: this module has zero dependency on anything
browser/agent-related in the rest of this project. Drop the whole crag/
folder into an unrelated project and it works unchanged.
"""

from .crag_graph import build_crag_graph
from .vector_store import build_vector_store


class CragEngine:
    def __init__(
        self,
        knowledge_dir: str,
        persist_dir: str = ".crag_index",
        force_rebuild: bool = False,
        allow_web_search: bool = False,
    ):
        store = build_vector_store(knowledge_dir, persist_dir, force_rebuild=force_rebuild)
        retriever = store.as_retriever(search_kwargs={"k": 4})
        self._graph = build_crag_graph(retriever, allow_web_search=allow_web_search)

    async def answer(self, question: str) -> str:
        result = await self._graph.ainvoke(
            {
                "question": question,
                "retrieved_docs": [],
                "relevant_docs": [],
                "used_web_search": False,
                "answer": "",
            }
        )
        return result["answer"]
