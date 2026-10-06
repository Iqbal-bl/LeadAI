"""
Per-company knowledge base: upload, index, inspect, test, delete, re-index.

Every write goes through the same `_index()` funnel, which is what guarantees a
document is never persisted as "indexed" unless its chunks were actually embedded
and stored under the right ClientId.
"""
from __future__ import annotations

import logging
import re

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from domain.models import Client

from .. import activity
from ..activity import A
from ..config import settings
from ..db import get_leadai_db
from ..models import LeadKbChunk, LeadKbDocument, utcnow
from ..rbac import Principal, assert_owns, require, resolve_scope
from ..schemas import (
    CloudLinkCreate,
    DocumentDetailOut,
    DocumentOut,
    FaqCreate,
    KbStatsOut,
    Ok,
    RetrievalTest,
    RetrievalTestOut,
    TextCreate,
)
from ..serializers import document_detail_out, document_out
from ..services import ai_engine, cloud_fetcher, embeddings, ingest, vectorstore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/knowledge", tags=["LeadAI • Knowledge base"])


def _company_name(db: Session, client_id: str) -> str:
    client = db.get(Client, client_id)
    if not client or client.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    return client.Name


def _index(
    db: Session,
    client_id: str,
    principal: Principal,
    *,
    title: str,
    filename: str | None,
    content_type: str,
    source_type: str,
    text: str,
    tags: str | None,
    source_url: str | None = None,
    request: Request | None = None,
) -> LeadKbDocument:
    """Chunk -> embed -> persist. The single write path for the knowledge base."""
    chunks = ingest.chunk(text)
    if not chunks:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "No readable text found — the file may be a scanned image (OCR is not enabled) "
            "or contain too little content to index.",
        )

    doc = LeadKbDocument(
        ClientId=client_id,
        Title=title[:255],
        FileName=filename[:255] if filename else None,
        ContentType=content_type[:120],
        SourceType=source_type,
        SourceUrl=source_url[:500] if source_url else None,
        Status="indexing",
        CharCount=len(text),
        Tags=tags,
        RawText=text,
        CreatedBy=principal.email,
    )
    db.add(doc)
    db.flush()

    try:
        doc.ChunkCount = vectorstore.upsert(
            db, client_id, doc.Id, chunks, created_by=principal.email
        )
        doc.EmbeddingModel = embeddings.active_model()
        doc.Status = "indexed"
        doc.StatusMessage = None
    except Exception as exc:  # noqa: BLE001
        doc.Status = "failed"
        doc.StatusMessage = str(exc)[:500]
        activity.log_principal(
            db,
            principal,
            action=A.KB_INDEX_FAILED,
            client_id=client_id,
            entity_type="document",
            entity_id=doc.Id,
            message=f"Indexing failed for '{doc.Title}'",
            meta={"error": str(exc)[:300]},
            log_type="Error",
            request=request,
        )
        db.commit()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"Indexing failed: {exc}"
        ) from exc

    activity.log_principal(
        db,
        principal,
        action=A.KB_INDEXED,
        client_id=client_id,
        entity_type="document",
        entity_id=doc.Id,
        message=f"Indexed '{doc.Title}' ({doc.ChunkCount} chunks)",
        meta={
            "chunks": doc.ChunkCount,
            "chars": doc.CharCount,
            "source_type": source_type,
            "embedding_model": doc.EmbeddingModel,
        },
        request=request,
    )
    db.commit()
    db.refresh(doc)
    return doc


@router.get("/documents", response_model=list[DocumentOut], summary="List documents")
def list_documents(
    status_filter: str | None = Query(default=None, alias="status"),
    principal: Principal = Depends(require("kb.read")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    query = db.query(LeadKbDocument).filter(
        LeadKbDocument.ClientId == client_id,
        LeadKbDocument.IsDeleted == False,  # noqa: E712
    )
    if status_filter:
        query = query.filter(LeadKbDocument.Status == status_filter)
    rows = query.order_by(LeadKbDocument.CreatedAt.desc()).all()
    return [document_out(r) for r in rows]


@router.post(
    "/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document (pdf/docx/txt/md/csv/html)",
)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    tags: str | None = Query(default=None),
    principal: Principal = Depends(require("kb.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    _company_name(db, client_id)

    blob = await file.read()
    if not blob:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That file is empty")
    if len(blob) > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Files must be under {settings.max_upload_bytes // (1024 * 1024)} MB",
        )

    activity.log_principal(
        db,
        principal,
        action=A.KB_UPLOADED,
        client_id=client_id,
        entity_type="document",
        message=f"Uploading '{file.filename}'",
        meta={"bytes": len(blob), "content_type": file.content_type},
        request=request,
    )

    try:
        text = ingest.extract_text(file.filename or "upload", file.content_type or "", blob)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"Couldn't read that file: {exc}"
        ) from exc

    return document_out(
        _index(
            db,
            client_id,
            principal,
            title=file.filename or "Uploaded document",
            filename=file.filename,
            content_type=file.content_type or "application/octet-stream",
            source_type="upload",
            text=text,
            tags=tags,
            request=request,
        )
    )


@router.post(
    "/faq",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add an FAQ / pasted text block",
)
def add_faq(
    payload: FaqCreate,
    request: Request,
    principal: Principal = Depends(require("kb.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    return document_out(
        _index(
            db,
            client_id,
            principal,
            title=payload.title,
            filename=None,
            content_type="text/faq",
            source_type="faq",
            text=payload.content,
            tags=payload.tags,
            request=request,
        )
    )


@router.post(
    "/cloud-link",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Import and index a document from a Google Drive or Dropbox link",
)
@router.post(
    "/url",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
def import_cloud_link(
    payload: CloudLinkCreate,
    request: Request,
    principal: Principal = Depends(require("kb.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    _company_name(db, client_id)

    activity.log_principal(
        db,
        principal,
        action=A.KB_UPLOADED,
        client_id=client_id,
        entity_type="document",
        message=f"Fetching cloud link '{payload.title}' from {payload.url[:120]}",
        meta={"url": payload.url},
        request=request,
    )

    try:
        filename, content_type, blob = cloud_fetcher.fetch_cloud_file(payload.url, default_title=payload.title)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Could not download file from link: {exc}",
        ) from exc

    if len(blob) > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds maximum size of {settings.max_upload_bytes // (1024 * 1024)} MB",
        )

    try:
        text = ingest.extract_text(filename, content_type, blob)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Could not extract readable text from '{filename}': {exc}",
        ) from exc

    if not text or not text.strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"No readable text could be found in '{filename}'. Ensure it contains selectable text.",
        )

    full_text = text
    if payload.notes and payload.notes.strip():
        full_text = f"Context & Notes:\n{payload.notes.strip()}\n\n---\nDocument Content ({filename}):\n{text}"

    lower_url = payload.url.lower()
    if "drive.google.com" in lower_url or "docs.google.com" in lower_url or "drive.usercontent" in lower_url:
        stype = "google_drive"
    elif "dropbox.com" in lower_url:
        stype = "dropbox"
    else:
        stype = "cloud_link"

    return document_out(
        _index(
            db,
            client_id,
            principal,
            title=payload.title or filename,
            filename=filename,
            content_type=content_type,
            source_type=stype,
            text=full_text,
            tags=payload.tags,
            source_url=payload.url,
            request=request,
        )
    )


@router.post(
    "/text",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a free-text knowledge entry",
)
def add_text(
    payload: TextCreate,
    request: Request,
    principal: Principal = Depends(require("kb.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)

    # Auto-detect if content is a cloud link (Google Drive / Dropbox)
    cloud_url_match = re.search(
        r"(https?://(?:drive\.google\.com|docs\.google\.com|www\.dropbox\.com|dropbox\.com)[^\s\n\r]+)",
        payload.content,
    )
    if cloud_url_match:
        cloud_url = cloud_url_match.group(1).rstrip(">)].,\"'")
        try:
            filename, content_type, blob = cloud_fetcher.fetch_cloud_file(cloud_url, default_title=payload.title)
            extracted = ingest.extract_text(filename, content_type, blob)
            if extracted and extracted.strip():
                notes = (
                    payload.content.replace(cloud_url_match.group(0), "")
                    .replace("Google Drive Link:", "")
                    .replace("Dropbox Link:", "")
                    .strip()
                )
                full_text = extracted
                if notes:
                    full_text = f"Context & Notes:\n{notes}\n\n---\nDocument Content ({filename}):\n{extracted}"
                stype = "google_drive" if "google" in cloud_url else "dropbox"
                return document_out(
                    _index(
                        db,
                        client_id,
                        principal,
                        title=payload.title or filename,
                        filename=filename,
                        content_type=content_type,
                        source_type=stype,
                        text=full_text,
                        tags=payload.tags,
                        source_url=cloud_url,
                        request=request,
                    )
                )
        except Exception as exc:
            logger.warning("[LeadAI KB] Cloud link fetch in add_text failed, storing as text: %s", exc)

    return document_out(
        _index(
            db,
            client_id,
            principal,
            title=payload.title,
            filename=None,
            content_type="text/plain",
            source_type="text",
            text=payload.content,
            tags=payload.tags,
            request=request,
        )
    )


@router.get(
    "/documents/{document_id}",
    response_model=DocumentDetailOut,
    summary="Get document details and raw content",
)
def get_document(
    document_id: str,
    principal: Principal = Depends(require("kb.read")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    doc = db.get(LeadKbDocument, document_id)
    if not doc or doc.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    assert_owns(doc.ClientId, client_id)

    raw_text = doc.RawText
    if not raw_text:
        chunks = (
            db.query(LeadKbChunk)
            .filter(LeadKbChunk.ClientId == client_id, LeadKbChunk.DocumentId == document_id)
            .order_by(LeadKbChunk.Position.asc())
            .all()
        )
        if chunks:
            raw_text = "\n\n".join(c.ChunkText for c in chunks if c.ChunkText)

    return document_detail_out(doc, raw_text=raw_text)


@router.get(
    "/documents/{document_id}/download",
    summary="Download a document's content as a file",
)
def download_document(
    document_id: str,
    principal: Principal = Depends(require("kb.read")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    doc = db.get(LeadKbDocument, document_id)
    if not doc or doc.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    assert_owns(doc.ClientId, client_id)

    raw_text = doc.RawText
    if not raw_text:
        chunks = (
            db.query(LeadKbChunk)
            .filter(LeadKbChunk.ClientId == client_id, LeadKbChunk.DocumentId == document_id)
            .order_by(LeadKbChunk.Position.asc())
            .all()
        )
        if chunks:
            raw_text = "\n\n".join(c.ChunkText for c in chunks if c.ChunkText)

    if not raw_text:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document content is empty or unavailable")

    filename = doc.FileName or f"{doc.Title or 'document'}.txt"
    media_type = doc.ContentType or "text/plain"
    safe_filename = filename.replace('"', '\\"')

    headers = {
        "Content-Disposition": f'attachment; filename="{safe_filename}"',
        "Access-Control-Expose-Headers": "Content-Disposition",
    }
    return Response(
        content=raw_text.encode("utf-8"),
        media_type=media_type,
        headers=headers,
    )


@router.get(
    "/documents/{document_id}/chunks",
    summary="Inspect a document's chunks (debug retrieval)",
)
def document_chunks(
    document_id: str,
    limit: int = Query(default=50, le=500),
    principal: Principal = Depends(require("kb.read")),
    db: Session = Depends(get_leadai_db),
):
    """Shows exactly what the retriever sees. Invaluable when a company complains
    the bot 'doesn't know' something that is in their PDF — usually the text
    extracted badly, and this is where you find that out."""
    client_id = resolve_scope(principal)
    doc = db.get(LeadKbDocument, document_id)
    if not doc or doc.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    assert_owns(doc.ClientId, client_id)

    rows = (
        db.query(LeadKbChunk)
        .filter(LeadKbChunk.ClientId == client_id, LeadKbChunk.DocumentId == document_id)
        .order_by(LeadKbChunk.Position.asc())
        .limit(limit)
        .all()
    )
    return {
        "document_id": document_id,
        "title": doc.Title,
        "total_chunks": doc.ChunkCount,
        "chunks": [
            {
                "id": r.Id,
                "position": r.Position,
                "text": r.ChunkText,
                "token_count": r.TokenCount,
                "embedding_model": r.EmbeddingModel,
                "embedding_dim": len(r.Embedding) if r.Embedding else 0,
            }
            for r in rows
        ],
    }


@router.post(
    "/documents/{document_id}/reindex",
    response_model=DocumentOut,
    summary="Re-chunk and re-embed a document",
)
def reindex_document(
    document_id: str,
    request: Request,
    principal: Principal = Depends(require("kb.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Needed after switching embedding models (e.g. the offline fallback was
    used during an OpenAI outage): vectors from two different models are not
    comparable, so the corpus must be brought back into one vector space."""
    client_id = resolve_scope(principal)
    doc = db.get(LeadKbDocument, document_id)
    if not doc or doc.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    assert_owns(doc.ClientId, client_id)
    if not doc.RawText:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Original text is not retained for this document — re-upload it instead.",
        )

    # If the document originated from a cloud link or contains a link, attempt re-fetching
    cloud_url = doc.SourceUrl
    if not cloud_url and doc.RawText:
        m = re.search(
            r"(https?://(?:drive\.google\.com|docs\.google\.com|www\.dropbox\.com|dropbox\.com)[^\s\n\r]+)",
            doc.RawText,
        )
        if m:
            cloud_url = m.group(1).rstrip(">)].,\"'")

    if cloud_url:
        try:
            fn, ct, blob = cloud_fetcher.fetch_cloud_file(cloud_url, default_title=doc.Title)
            extracted = ingest.extract_text(fn, ct, blob)
            if extracted and len(extracted.strip()) > 30:
                doc.FileName = fn
                doc.ContentType = ct
                doc.RawText = extracted
                doc.CharCount = len(extracted)
                doc.SourceUrl = cloud_url
                if "google" in cloud_url:
                    doc.SourceType = "google_drive"
                elif "dropbox" in cloud_url:
                    doc.SourceType = "dropbox"
        except Exception as exc:
            logger.warning("[LeadAI KB] Re-fetch cloud link for doc %s skipped: %s", doc.Id, exc)

    vectorstore.delete_document(db, client_id, document_id)
    chunks = ingest.chunk(doc.RawText)
    doc.Status = "indexing"
    db.flush()

    doc.ChunkCount = vectorstore.upsert(db, client_id, doc.Id, chunks, created_by=principal.email)
    doc.EmbeddingModel = embeddings.active_model()
    doc.Status = "indexed"
    doc.UpdatedBy = principal.email
    doc.UpdatedAt = utcnow()

    activity.log_principal(
        db,
        principal,
        action=A.KB_REINDEXED,
        client_id=client_id,
        entity_type="document",
        entity_id=doc.Id,
        message=f"Re-indexed '{doc.Title}' ({doc.ChunkCount} chunks)",
        meta={"embedding_model": doc.EmbeddingModel},
        request=request,
    )
    db.commit()
    db.refresh(doc)
    return document_out(doc)


@router.delete("/documents/{document_id}", response_model=Ok, summary="Delete a document")
def delete_document(
    document_id: str,
    request: Request,
    principal: Principal = Depends(require("kb.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    doc = db.get(LeadKbDocument, document_id)
    if not doc or doc.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    assert_owns(doc.ClientId, client_id)

    removed = vectorstore.delete_document(db, client_id, document_id)
    # Chunks are hard-deleted (they are derived data and would otherwise keep
    # answering queries); the document row is soft-deleted to preserve the trail.
    doc.IsDeleted = True
    doc.Status = "deleted"
    doc.UpdatedBy = principal.email
    doc.UpdatedAt = utcnow()

    activity.log_principal(
        db,
        principal,
        action=A.KB_DELETED,
        client_id=client_id,
        entity_type="document",
        entity_id=doc.Id,
        message=f"Deleted '{doc.Title}' and {removed} chunks",
        meta={"chunks_removed": removed},
        log_type="Security",
        request=request,
    )
    db.commit()
    return Ok(message=f"Deleted '{doc.Title}' ({removed} chunks removed)")


@router.post(
    "/test",
    response_model=RetrievalTestOut,
    summary="Ask the knowledge base directly (proves tenant isolation)",
)
def test_retrieval(
    payload: RetrievalTest,
    request: Request,
    principal: Principal = Depends(require("kb.test")),
    db: Session = Depends(get_leadai_db),
):
    """The fastest way to demonstrate that Company A's assistant cannot see
    Company B's documents: run the same query under two different client_ids."""
    client_id = resolve_scope(principal)
    company_name = _company_name(db, client_id)

    result = ai_engine.answer(
        db, client_id, company_name, payload.query, history=[], channel="chat"
    )

    activity.log_principal(
        db,
        principal,
        action=A.KB_TESTED,
        client_id=client_id,
        entity_type="knowledge",
        message=f"Retrieval test: {payload.query[:120]}",
        meta={"confidence": result["confidence"], "sources": len(result["sources"])},
        request=request,
    )
    db.commit()

    return RetrievalTestOut(
        company=company_name,
        query=payload.query,
        answer=result["reply"],
        confidence=result["confidence"],
        needs_human=result["needs_human"],
        handoff_reason=result["handoff_reason"],
        model=result["model"],
        latency_ms=result["latency_ms"],
        sources=result["sources"],
    )


@router.get("/stats", response_model=KbStatsOut, summary="Knowledge base health")
def kb_stats(
    principal: Principal = Depends(require("kb.read")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    stats = vectorstore.corpus_stats(db, client_id)
    documents = (
        db.query(LeadKbDocument)
        .filter(
            LeadKbDocument.ClientId == client_id,
            LeadKbDocument.IsDeleted == False,  # noqa: E712
        )
        .count()
    )
    return KbStatsOut(
        client_id=client_id,
        documents=documents,
        chunks=stats["chunks"],
        models=stats["models"],
        needs_reindex=stats["needs_reindex"],
        backend=stats["backend"],
        embedding_backend=embeddings.backend(),
        embedding_model=embeddings.active_model(),
    )
