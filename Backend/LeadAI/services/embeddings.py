"""
Embedding generation.

Primary path: OpenAI `text-embedding-3-small` (1536-d, cosine). Chosen because
it is cheap enough to embed a whole company handbook, multilingual enough for
Hinglish/Indic queries (which matters when the same knowledge base serves Sarvam
STT transcripts from a phone call), and dimension-reducible if storage becomes a
concern later.

Fallback path: a deterministic hashed-ngram vector. It is NOT a semantic model —
it exists so that (a) local development and CI need no API key and no network,
and (b) an OpenAI outage degrades retrieval to lexical rather than breaking the
product. Because the fallback is deterministic, a document embedded offline and
a query embedded offline still match; but a corpus embedded with OpenAI must be
re-indexed if you later switch models, which is why `EmbeddingModel` is stored
per chunk and `/knowledge/reindex` exists.

Batching: OpenAI accepts many inputs per request, so ingestion embeds in batches
of 64 to keep round-trips down on large uploads.
"""
from __future__ import annotations

import hashlib
import logging
import re

import numpy as np

from ..config import settings

logger = logging.getLogger(__name__)

LOCAL_DIM = 384
LOCAL_MODEL_NAME = "local-hashed-ngram-384"
_TOKEN_RE = re.compile(r"[a-z0-9₹]+")
_BATCH = 64


def active_model() -> str:
    return settings.openai_embed_model if settings.llm_enabled else LOCAL_MODEL_NAME


def active_dim() -> int:
    return settings.openai_embed_dim if settings.llm_enabled else LOCAL_DIM


def backend() -> str:
    return "openai" if settings.llm_enabled else "local-hashing"


# --------------------------------------------------------------------------- #
# lexical helpers (shared with the retrieval scorer)
# --------------------------------------------------------------------------- #
def stem(word: str) -> str:
    """Crude suffix stripper: 'documents'->'document', 'processing'->'process'.

    Good enough to make morphological variants match without pulling in a real
    stemmer, and it runs on every query token so it must stay cheap.
    """
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us")):
        return word[:-1]
    return word


def _grams(text: str) -> list[tuple[str, float]]:
    """Words, adjacent bigrams, and 5-char prefixes with hand-set weights.

    Bigrams are weighted above unigrams because 'interest rate' should outrank a
    passage that merely mentions 'interest'. Prefixes let 'approval' retrieve
    'approved' — without them a pure hashing vector treats them as unrelated
    tokens.
    """
    words = [stem(w) for w in _TOKEN_RE.findall(text.lower())]
    out: list[tuple[str, float]] = [(w, 1.0) for w in words]
    out += [(f"{a}_{b}", 1.4) for a, b in zip(words, words[1:])]
    out += [(f"#{w[:5]}", 0.5) for w in words if len(w) >= 6]
    return out


def local_embed(text: str) -> list[float]:
    """Signed random-projection ('hashing trick') vector, L2-normalised."""
    vec = np.zeros(LOCAL_DIM, dtype=np.float32)
    for token, weight in _grams(text):
        digest = hashlib.md5(token.encode()).digest()
        idx = int.from_bytes(digest[:4], "little") % LOCAL_DIM
        sign = 1.0 if digest[4] % 2 else -1.0
        vec[idx] += sign * weight
    norm = float(np.linalg.norm(vec))
    if norm:
        vec /= norm
    return vec.tolist()


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def _openai_embed(texts: list[str]) -> list[list[float]] | None:
    try:
        import httpx

        vectors: list[list[float]] = []
        with httpx.Client(timeout=settings.openai_timeout) as client:
            for start in range(0, len(texts), _BATCH):
                batch = texts[start : start + _BATCH]
                resp = client.post(
                    f"{settings.openai_base_url}/embeddings",
                    headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                    json={"model": settings.openai_embed_model, "input": batch},
                )
                resp.raise_for_status()
                data = resp.json()["data"]
                # The API guarantees order, but sort on index to be certain —
                # a silently mis-paired vector would be a very hard bug.
                data.sort(key=lambda item: item["index"])
                vectors.extend(item["embedding"] for item in data)
        return vectors
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LeadAI embeddings] OpenAI call failed (%s) — using local fallback", exc)
        return None


def embed(texts: list[str]) -> tuple[list[list[float]], str]:
    """Embed a batch. Returns (vectors, model_name_actually_used).

    The model name is returned rather than assumed so the caller can persist it
    per chunk: a corpus must never mix vector spaces, and storing the model is
    what lets `/knowledge/reindex` detect that it has to.
    """
    if not texts:
        return [], active_model()
    if settings.llm_enabled:
        vectors = _openai_embed(texts)
        if vectors is not None and len(vectors) == len(texts):
            return vectors, settings.openai_embed_model
    return [local_embed(t) for t in texts], LOCAL_MODEL_NAME


def embed_one(text: str) -> tuple[list[float], str]:
    vectors, model = embed([text])
    return (vectors[0] if vectors else []), model


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    """Cosine similarity, tolerant of missing/mismatched vectors.

    Dimension mismatch means the two were produced by different models (e.g.
    corpus embedded with OpenAI, query embedded by the offline fallback during an
    outage). Returning 0.0 rather than raising lets the lexical half of the
    hybrid score carry the query instead of failing the request.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(np.dot(va, vb) / denom) if denom else 0.0
