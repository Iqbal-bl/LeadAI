"""
Web-search fallback for when retrieved knowledge-base chunks are graded
irrelevant. Uses Tavily (built specifically for this: clean, LLM-ready
search results rather than raw HTML to parse yourself). Swap this file's
implementation if you'd rather use a different provider — nothing else in
crag/ needs to know or care.
"""

import os

from tavily import TavilyClient


def web_search(query: str, max_results: int = 3) -> str:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return "(Web search unavailable — TAVILY_API_KEY not set in .env.)"

    client = TavilyClient(api_key=api_key)
    response = client.search(query=query, max_results=max_results)
    snippets = [r.get("content", "") for r in response.get("results", [])]
    return "\n\n".join(snippets) if snippets else "(No web search results found.)"
