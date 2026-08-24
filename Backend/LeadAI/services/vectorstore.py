"""
Retrieval: a hybrid (dense + sparse) search scoped to one company.

WHY HYBRID rather than pure vector search
-----------------------------------------
Dense cosine alone under-performs badly on this workload for two reasons:

  1. Length dilution. A four-word question against a 900-character chunk yields a
     small dot product even on an exact hit, so a short off-topic chunk can
     outrank the paragraph that literally contains the answer.
  2. Rare-token blindness. Embeddings smooth over exactly the tokens that decide
     a B2B question — a policy number, "CLIA", "Dubai", a product name. Two
     passages about "coverage" look similar even when only one mentions travel
     insurance.

So the score is:

    score = Wv * normalised_cosine  +  (1 - Wv) * idf_weighted_lexical_coverage

`idf_weighted_lexical_coverage` is the share of the QUESTION's meaning the
passage covers, where each query term is weighted by its inverse document
frequency within that company's corpus. This is the piece that makes an
out-of-scope question score low instead of latching onto incidental word
overlap: a term the corpus has never seen is treated as maximally informative,
so failing to match it is what sinks the score — and a low score is what
triggers the human handoff rather than a confident hallucination.

TENANT ISOLATION
----------------
The ClientId filter is applied INSIDE this module, in both backends, and is a
required positional argument on every function. A caller cannot forget it, and
there is no code path that searches without it. That is the whole reason
retrieval is centralised here instead of being written inline in the routers.

BACKENDS
--------
* Default: embeddings live in `leadai_kb_chunks.Embedding` (JSON) and are scored
  in-process with numpy. Zero extra infrastructure; fine to a few tens of
  thousands of chunks per company.
* Optional: set QDRANT_URL to offload the dense half to Qdrant with a
  `client_id` payload filter. The sparse half still runs over MySQL, so
  behaviour is identical either way.
"""
from __future__ import annotations

import logging
import math
import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import LeadKbChunk
from .embeddings import cosine, embed, embed_one, stem

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9₹]+")

# --------------------------------------------------------------------------- #
# optional Qdrant backend
# --------------------------------------------------------------------------- #
_qdrant = None
if settings.qdrant_url:  # pragma: no cover - optional dependency
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as qmodels

        from .embeddings import active_dim

        _qdrant = QdrantClient(url=settings.qdrant_url)
        names = {c.name for c in _qdrant.get_collections().collections}
        if settings.qdrant_collection not in names:
            _qdrant.create_collection(
                collection_name=settings.qdrant_collection,
                vectors_config=qmodels.VectorParams(
                    size=active_dim(), distance=qmodels.Distance.COSINE
                ),
            )
        logger.info("[LeadAI] Qdrant vector backend active: %s", settings.qdrant_collection)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LeadAI] Qdrant unavailable (%s) — using MySQL + numpy", exc)
        _qdrant = None


def backend() -> str:
    return "qdrant" if _qdrant else "mysql+numpy"


# --------------------------------------------------------------------------- #
# lexical scoring
# --------------------------------------------------------------------------- #
STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "do", "does", "did", "of", "for", "to", "in",
    "on", "at", "and", "or", "my", "your", "i", "you", "we", "it", "this", "that", "with", "how",
    "what", "can", "could", "would", "should", "me", "please", "tell", "about", "any", "much",
    "there", "be", "have", "has", "will", "if", "am", "get", "want", "long", "need", "know",
    "per", "from", "into", "over", "than", "then", "also", "just", "like", "very", "some",
    "give", "make", "take", "our", "their", "his", "her", "one", "two", "many", "yes", "no",
    # Temporal words are qualification signals (timeline), not retrieval terms.
    # Leaving them in makes "can I get this today" retrieve on "today".
    "today", "tomorrow", "yesterday", "now", "soon", "asap", "currently", "immediately",
}


def keywords(text: str) -> set[str]:
    return {
        stem(w)
        for w in _TOKEN_RE.findall((text or "").lower())
        if w not in STOPWORDS and len(w) > 2
    }


def term_matches(term: str, passage_terms: set[str]) -> bool:
    """Exact match, or a shared 5-character prefix ('approval' ~ 'approved')."""
    if term in passage_terms:
        return True
    if len(term) < 5:
        return False
    head = term[:5]
    return any(other.startswith(head) for other in passage_terms if len(other) >= 5)


# ClientId -> (chunk_count_when_built, idf map, weight for unseen terms)
_IDF_CACHE: dict[str, tuple[int, dict[str, float], float]] = {}


def idf_map(db: Session, client_id: str) -> tuple[dict[str, float], float]:
    """Inverse document frequency across ONE company's chunks.

    Cache key includes the chunk count, so any upload/delete invalidates it
    automatically without needing explicit cache busting.
    """
    texts = db.scalars(
        select(LeadKbChunk.ChunkText).where(
            LeadKbChunk.ClientId == client_id,
            LeadKbChunk.IsDeleted == False,  # noqa: E712
        )
    ).all()
    total = len(texts)

    cached = _IDF_CACHE.get(client_id)
    if cached and cached[0] == total:
        return cached[1], cached[2]

    frequency: dict[str, int] = {}
    for text in texts:
        for term in keywords(text):
            frequency[term] = frequency.get(term, 0) + 1

    idf = {term: math.log(1 + total / (1 + df)) for term, df in frequency.items()}
    unseen = math.log(1 + total) if total else 1.0
    _IDF_CACHE[client_id] = (total, idf, unseen)
    return idf, unseen


def invalidate_idf(client_id: str) -> None:
    _IDF_CACHE.pop(client_id, None)


def lexical_coverage(
    query: str,
    passage: str,
    idf: dict[str, float] | None = None,
    unseen: float = 1.0,
) -> float:
    """Share of the question's weighted meaning this passage covers (0..1)."""
    q = keywords(query)
    if not q:
        return 0.0
    p = keywords(passage)

    if idf is None:
        # Unweighted fallback; denominator capped at three content words so a
        # long rambling question isn't automatically low-coverage.
        return min(1.0, sum(1 for t in q if term_matches(t, p)) / min(len(q), 3))

    matched = total = 0.0
    for term in q:
        weight = idf.get(term, unseen)
        total += weight
        if term_matches(term, p):
            matched += weight
    return matched / total if total else 0.0


# --------------------------------------------------------------------------- #
# write path
# --------------------------------------------------------------------------- #
def upsert(
    db: Session,
    client_id: str,
    document_id: str,
    chunks: list[str],
    created_by: str = "system",
) -> int:
    """Embed and persist chunks for one document. Returns the count written."""
    if not chunks:
        return 0

    vectors, model = embed(chunks)
    rows: list[LeadKbChunk] = []

    for position, (text, vector) in enumerate(zip(chunks, vectors)):
        row = LeadKbChunk(
            ClientId=client_id,
            DocumentId=document_id,
            Position=position,
            ChunkText=text,
            TokenCount=max(1, len(text) // 4),
            # When Qdrant owns the vectors we still keep them in MySQL: it makes
            # re-indexing into a different store possible without re-embedding
            # (and re-embedding a large corpus costs real money).
            Embedding=vector,
            EmbeddingModel=model,
            VectorRef=str(uuid.uuid4()) if _qdrant else None,
            CreatedBy=created_by,
        )
        db.add(row)
        rows.append(row)
    db.flush()

    if _qdrant:  # pragma: no cover - optional dependency
        try:
            from qdrant_client.http import models as qmodels

            _qdrant.upsert(
                collection_name=settings.qdrant_collection,
                points=[
                    qmodels.PointStruct(
                        id=row.VectorRef,
                        vector=row.Embedding,
                        payload={
                            "client_id": client_id,
                            "document_id": document_id,
                            "chunk_id": row.Id,
                            "text": row.ChunkText,
                        },
                    )
                    for row in rows
                ],
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[LeadAI] Qdrant upsert failed (%s) — MySQL copy still authoritative", exc)

    invalidate_idf(client_id)
    return len(rows)


def delete_document(db: Session, client_id: str, document_id: str) -> int:
    """Hard-delete a document's chunks from both stores."""
    rows = (
        db.query(LeadKbChunk)
        .filter(LeadKbChunk.ClientId == client_id, LeadKbChunk.DocumentId == document_id)
        .all()
    )
    refs = [r.VectorRef for r in rows if r.VectorRef]
    for row in rows:
        db.delete(row)

    if _qdrant and refs:  # pragma: no cover
        try:
            from qdrant_client.http import models as qmodels

            _qdrant.delete(
                collection_name=settings.qdrant_collection,
                points_selector=qmodels.PointIdsList(points=refs),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[LeadAI] Qdrant delete failed: %s", exc)

    invalidate_idf(client_id)
    return len(rows)


# --------------------------------------------------------------------------- #
# read path
# --------------------------------------------------------------------------- #
def search(
    db: Session,
    client_id: str,
    query: str,
    top_k: int | None = None,
    min_score: float = 0.05,
) -> list[dict]:
    """Hybrid retrieval within one company. Highest score first.

    Each hit: chunk_id, document_id, text, score, vector_score, lexical_score.
    """
    top_k = top_k or settings.retrieval_top_k
    if not query or not query.strip():
        return []

    qvec, _ = embed_one(query)
    idf, unseen = idf_map(db, client_id)
    wv = settings.hybrid_vector_weight

    if _qdrant:  # pragma: no cover - optional dependency
        return _search_qdrant(db, client_id, query, qvec, top_k, min_score, idf, unseen, wv)

    rows = db.scalars(
        select(LeadKbChunk).where(
            LeadKbChunk.ClientId == client_id,
            LeadKbChunk.IsDeleted == False,  # noqa: E712
        )
    ).all()

    scored: list[dict] = []
    for row in rows:
        vector_score = cosine(qvec, row.Embedding)
        lexical = lexical_coverage(query, row.ChunkText, idf, unseen)
        scored.append(
            {
                "chunk_id": row.Id,
                "document_id": row.DocumentId,
                "text": row.ChunkText,
                # Cosine is normalised against 0.25 because real matches on
                # short-query/long-chunk pairs cluster well below 1.0; without
                # this the dense half contributes almost nothing.
                "score": round(
                    wv * min(vector_score / 0.25, 1.0) + (1 - wv) * lexical, 4
                ),
                "vector_score": round(vector_score, 4),
                "lexical_score": round(lexical, 4),
            }
        )

    scored.sort(key=lambda item: item["score"], reverse=True)
    return [item for item in scored[:top_k] if item["score"] > min_score]


def _search_qdrant(  # pragma: no cover - optional dependency
    db: Session,
    client_id: str,
    query: str,
    qvec: list[float],
    top_k: int,
    min_score: float,
    idf: dict[str, float],
    unseen: float,
    wv: float,
) -> list[dict]:
    global _qdrant
    from qdrant_client.http import models as qmodels

    try:
        # Over-fetch, then re-rank with the lexical half. Qdrant only knows the
        # dense score, and the sparse signal is what fixes its ranking mistakes.
        hits = _qdrant.search(
            collection_name=settings.qdrant_collection,
            query_vector=qvec,
            limit=max(top_k * 3, 12),
            query_filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="client_id", match=qmodels.MatchValue(value=client_id)
                    )
                ]
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[LeadAI] Qdrant search failed (%s) — falling back to MySQL", exc)
        saved, _qdrant = _qdrant, None
        try:
            return search(db, client_id, query, top_k, min_score)
        finally:
            _qdrant = saved

    scored = []
    for hit in hits:
        text = (hit.payload or {}).get("text", "")
        lexical = lexical_coverage(query, text, idf, unseen)
        scored.append(
            {
                "chunk_id": (hit.payload or {}).get("chunk_id"),
                "document_id": (hit.payload or {}).get("document_id"),
                "text": text,
                "score": round(wv * min(float(hit.score) / 0.25, 1.0) + (1 - wv) * lexical, 4),
                "vector_score": round(float(hit.score), 4),
                "lexical_score": round(lexical, 4),
            }
        )
    scored.sort(key=lambda item: item["score"], reverse=True)
    return [item for item in scored[:top_k] if item["score"] > min_score]


def corpus_stats(db: Session, client_id: str) -> dict:
    rows = db.scalars(
        select(LeadKbChunk.EmbeddingModel).where(LeadKbChunk.ClientId == client_id)
    ).all()
    models: dict[str, int] = {}
    for model in rows:
        models[model or "unknown"] = models.get(model or "unknown", 0) + 1
    return {
        "chunks": len(rows),
        "models": models,
        # More than one model means mixed vector spaces: cosine between them is
        # meaningless, so the UI should prompt for a re-index.
        "needs_reindex": len(models) > 1,
        "backend": backend(),
    }
