"""
CRAG tool — company Q&A grounded ONLY in knowledge/company_docs by default
(no web-search fallback), per project policy: replies must never draw on
outside/general knowledge. Set CRAG_ALLOW_WEB_SEARCH=true in .env to
re-enable the fallback for other use cases.
"""

import os

from langchain_core.tools import tool

_crag_engine = None


def _get_crag_engine():
    # Import AND build lazily — chromadb pulls in a heavy chain
    # (OpenTelemetry -> grpc -> a native DLL) that can fail to load on
    # some machines; no reason to pay that cost for tasks that never
    # call this tool at all.
    global _crag_engine
    if _crag_engine is None:
        from social_agent.crag.engine import CragEngine
        allow_web_search = os.environ.get("CRAG_ALLOW_WEB_SEARCH", "false").lower() == "true"
        _crag_engine = CragEngine(
            knowledge_dir="knowledge/company_docs",
            allow_web_search=allow_web_search,
        )
    return _crag_engine


@tool
async def answer_company_question(question: str) -> str:
    """Answer a factual question about the company/product/pricing using
    ONLY the company knowledge base — never guess from general knowledge.
    If the knowledge base doesn't cover it, this says so honestly."""
    engine = _get_crag_engine()
    return await engine.answer(question)


CRAG_TOOLS = [answer_company_question]
