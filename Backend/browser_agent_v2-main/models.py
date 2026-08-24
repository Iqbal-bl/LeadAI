"""
Central place for ALL model construction — chat/text generation,
grading, embeddings. Everything else in the project (crag/, agent/)
imports from here rather than constructing a provider's client directly.
Swap providers or models by editing .env, not by hunting through files.
"""

import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

# --- Text/chat generation (agent reasoning, CRAG grading, CRAG answers) ---
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "groq").lower()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

# --- Vision (agent/graph.py's ENABLE_VISION) — only OpenAI supports this
# cleanly here; Groq's vision lineup is preview-tier, see get_model()'s
# check below.
VISION_MODEL = os.environ.get("VISION_MODEL", "gpt-4o-mini")

# --- Embeddings (crag/vector_store.py) — separate from chat generation
# since they're a genuinely different capability with different provider
# tradeoffs (e.g. staying on Gemini for embeddings while using Groq for
# chat is a completely reasonable combination).
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "google").lower()
GOOGLE_EMBEDDING_MODEL = os.environ.get("GOOGLE_EMBEDDING_MODEL", "gemini-embedding-001")
OPENAI_EMBEDDING_MODEL = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")


def get_text_model(temperature: float = 0, max_tokens: int = 1024):
    """Returns the configured provider's chat model, tools NOT bound —
    used directly by crag/grader.py and crag/crag_graph.py."""
    if LLM_PROVIDER == "openai":
        return ChatOpenAI(model=OPENAI_MODEL, temperature=temperature, max_tokens=max_tokens)
    if LLM_PROVIDER == "groq":
        if not os.environ.get("GROQ_API_KEY"):
            raise ValueError("GROQ_API_KEY is not set in .env")
        from langchain_groq import ChatGroq
        return ChatGroq(model=GROQ_MODEL, temperature=temperature, max_tokens=max_tokens)
    raise ValueError("LLM_PROVIDER must be 'groq' or 'openai'")


def get_model(tools, vision: bool = False):
    """Returns the chat model WITH tools bound — used by agent/graph.py."""
    if vision and LLM_PROVIDER == "groq":
        raise ValueError("Groq is configured for text-only tasks; set LLM_PROVIDER=openai for vision.")
    # parallel_tool_calls=False: forces one action per turn — without this
    # the model can batch multiple DOM-mutating calls into one turn, which
    # LangGraph's ToolNode then runs CONCURRENTLY via asyncio.gather. Two
    # simultaneous programmatic inputs against a live page can race
    # against the site's own JS in ways a real sequential user never
    # would. One action per turn also means get_state() is always
    # re-checked before the next move, instead of acting on a stale
    # snapshot.
    if vision:
        return ChatOpenAI(model=VISION_MODEL, max_tokens=1024).bind_tools(
            tools, parallel_tool_calls=False
        )
    return get_text_model(max_tokens=1024).bind_tools(tools, parallel_tool_calls=False)


def get_embedding_model():
    """Returns the configured provider's embedding model — used by
    crag/vector_store.py. IMPORTANT: switching this isn't just a config
    change — old vectors in .crag_index were computed with the OLD
    model and aren't comparable (different model = different vector
    space). Delete .crag_index (or pass force_rebuild=True to
    CragEngine) after changing this."""
    if EMBEDDING_PROVIDER == "google":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        return GoogleGenerativeAIEmbeddings(model=GOOGLE_EMBEDDING_MODEL)
    if EMBEDDING_PROVIDER == "openai":
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(model=OPENAI_EMBEDDING_MODEL)
    raise ValueError("EMBEDDING_PROVIDER must be 'google' or 'openai'")
