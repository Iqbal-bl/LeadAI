"""
Researcher node for blog generation workflow.
Performs web research via TavilySearch and synthesizes structured evidence items.
"""
import json
import os
from datetime import date, timedelta
from typing import List, Optional
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ...config import settings
from .schemas import EvidenceItem, EvidencePack
from .state import State

RESEARCH_SYSTEM = """
You are a research synthesizer for technical, B2B, and professional writing.

Given raw web search results, produce a deduplicated list of EvidenceItem objects.
Rules:
- Only include items with a non-empty URL.
- Prefer relevant and authoritative sources (industry leaders, documentation, case studies, reputable research).
- Extract/normalize published_at as ISO (YYYY-MM-DD) if available.
- Keep snippets concise, factual, and informative.
- Deduplicate by URL.
"""


def _get_llm():
    api_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY")
    return ChatOpenAI(
        model=settings.openai_model or "gpt-4o-mini",
        api_key=api_key,
        temperature=0.1,
    )


def _tavily_search(query: str, max_results: int = 5) -> List[dict]:
    tavily_key = os.getenv("TAVILY_API_KEY")
    if not tavily_key:
        return []

    try:
        from langchain_tavily import TavilySearch
        tool = TavilySearch(max_results=max_results)
        results = tool.invoke({"query": query})

        if isinstance(results, str):
            try:
                results = json.loads(results)
            except json.JSONDecodeError:
                return []

        normalized: List[dict] = []
        for r in results or []:
            if isinstance(r, dict):
                normalized.append({
                    "title": r.get("title") or "",
                    "url": r.get("url") or "",
                    "snippet": r.get("content") or r.get("snippet") or "",
                    "published_at": r.get("published_date") or r.get("published_at"),
                    "source": r.get("source"),
                })
        return normalized
    except Exception as exc:
        print(f"[Researcher] Tavily search error for '{query}': {exc}")
        return []


def _iso_to_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def research_node(state: State) -> dict:
    """Execute research on queries and produce structured evidence."""
    queries = (state.get("queries", []) or [])[:6]
    if not queries:
        return {"evidence": []}

    raw_results: List[dict] = []
    for query in queries:
        raw_results.extend(_tavily_search(query, max_results=5))

    if not raw_results:
        return {"evidence": []}

    llm = _get_llm()
    extractor = llm.with_structured_output(EvidencePack)

    try:
        pack: EvidencePack = extractor.invoke(
            [
                SystemMessage(content=RESEARCH_SYSTEM),
                HumanMessage(
                    content=(
                        f"As-of date: {state.get('as_of', '2026-09-01')}\n"
                        f"Recency days: {state.get('recency_days', 3650)}\n\n"
                        f"Raw results:\n{raw_results[:20]}"
                    )
                ),
            ]
        )
        evidence = pack.evidence or []
    except Exception as exc:
        print(f"[Researcher] Evidence extraction error: {exc}")
        evidence = [
            EvidenceItem(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("snippet", ""),
                published_at=r.get("published_at"),
                source=r.get("source"),
            )
            for r in raw_results if r.get("url")
        ]

    # Deduplicate by URL
    dedup = {}
    for item in evidence:
        if item.url:
            dedup[item.url] = item
    evidence = list(dedup.values())

    # Mode-based recency filtering
    mode = state.get("mode", "closed_book")
    if mode == "open_book" and state.get("as_of"):
        try:
            as_of_date = date.fromisoformat(state["as_of"][:10])
            cutoff = as_of_date - timedelta(days=int(state.get("recency_days", 7)))
            evidence = [
                e for e in evidence
                if not e.published_at or (_iso_to_date(e.published_at) and _iso_to_date(e.published_at) >= cutoff)
            ]
        except Exception:
            pass

    return {"evidence": evidence}
