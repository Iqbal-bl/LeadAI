"""
Products router — Client-facing product management with attached Knowledge Base files.

Reuses the application's established Knowledge Base file handling:
  - Enforces max upload byte limits from config
  - Persists file blobs in the tenant-partitioned object store (MinIO / disk fallback)
  - Extracts text using `ingest.extract_text()`
  - Chunks, embeds, and indexes into `LeadKbDocument` and vectorstore via `_index()`
  - Links the indexed KB document directly to the product catalog entry
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy.orm import Session

from .. import activity
from ..activity import A
from ..config import settings
from ..db import get_leadai_db
from ..models import LeadKbDocument, LeadProduct, utcnow
from ..rbac import Principal, require, resolve_scope
from ..schemas import BindExistingKbRequest, BoundKbDocOut, ProductListOut, ProductOut
from ..services import ingest, objectstore
from .knowledge import _index

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/products", tags=["LeadAI • Products"])


def _product_out(
    product: LeadProduct,
    db: Session | None = None,
    doc_map: dict[str, LeadKbDocument] | None = None,
) -> ProductOut:
    bound_ids: list[str] = []
    raw_bound = product.BoundKbDocumentIds
    if isinstance(raw_bound, list):
        bound_ids = [str(bid) for bid in raw_bound if bid]
    elif isinstance(raw_bound, str) and raw_bound.strip():
        import json
        try:
            parsed = json.loads(raw_bound)
            if isinstance(parsed, list):
                bound_ids = [str(bid) for bid in parsed if bid]
        except Exception:
            pass

    bound_docs: list[BoundKbDocOut] = []
    if doc_map is None and db is not None:
        needed_ids = set()
        if product.KbDocumentId:
            needed_ids.add(str(product.KbDocumentId))
        needed_ids.update(bound_ids)
        if needed_ids:
            found = (
                db.query(LeadKbDocument)
                .filter(
                    LeadKbDocument.Id.in_(needed_ids),
                    LeadKbDocument.ClientId == product.ClientId,
                    LeadKbDocument.IsDeleted == False,  # noqa: E712
                )
                .all()
            )
            doc_map = {str(d.Id): d for d in found}
        else:
            doc_map = {}

    if doc_map is not None:
        if product.KbDocumentId and str(product.KbDocumentId) in doc_map:
            pdoc = doc_map[str(product.KbDocumentId)]
            bound_docs.append(
                BoundKbDocOut(
                    id=str(pdoc.Id),
                    title=pdoc.Title,
                    file_name=pdoc.FileName or product.KnowledgeBaseFile,
                    content_type=pdoc.ContentType,
                    chunk_count=pdoc.ChunkCount or 0,
                    status=pdoc.Status or "indexed",
                    is_primary=True,
                    created_at=pdoc.CreatedAt,
                )
            )
        for bid in bound_ids:
            if bid in doc_map and bid != str(product.KbDocumentId):
                bdoc = doc_map[bid]
                bound_docs.append(
                    BoundKbDocOut(
                        id=str(bdoc.Id),
                        title=bdoc.Title,
                        file_name=bdoc.FileName,
                        content_type=bdoc.ContentType,
                        chunk_count=bdoc.ChunkCount or 0,
                        status=bdoc.Status or "indexed",
                        is_primary=False,
                        created_at=bdoc.CreatedAt,
                    )
                )

    return ProductOut(
        id=str(product.Id),
        client_id=str(product.ClientId),
        product_name=product.ProductName,
        product_description=product.ProductDescription or None,
        knowledge_base_file=product.KnowledgeBaseFile,
        kb_document_id=str(product.KbDocumentId) if product.KbDocumentId else None,
        bound_kb_document_ids=bound_ids,
        bound_kb_documents=bound_docs,
        created_at=product.CreatedAt,
        created_by=product.CreatedBy,
        updated_at=product.UpdatedAt,
        updated_by=product.UpdatedBy,
        is_deleted=bool(product.IsDeleted),
    )


@router.post(
    "",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a product with an attached knowledge base file",
)
async def create_product(
    request: Request,
    product_name: str = Form(..., description="Name of the product"),
    product_description: str | None = Form(default=None, description="Description of the product"),
    file: UploadFile | None = File(default=None, description="Knowledge base file (PDF, DOCX, TXT, CSV)"),
    principal: Principal = Depends(require("product.manage", "kb.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    p_name = product_name.strip()
    p_desc = product_description.strip() if product_description and product_description.strip() else None

    if not p_name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Product name cannot be empty.")

    kb_filename: str | None = None
    kb_document_id: str | None = None

    # Knowledge Base File Handling (matching application's knowledge.py & files.py flow)
    if file and file.filename:
        blob = await file.read()
        if not blob:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The uploaded file is empty.")
        if len(blob) > settings.max_upload_bytes:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"File exceeds maximum allowed size ({settings.max_upload_bytes // (1024 * 1024)} MB).",
            )

        # 1. Store file blob into the tenant-partitioned object store
        try:
            objectstore.put_bytes(
                blob,
                client_id=client_id,
                purpose="kb",
                filename=file.filename,
                content_type=file.content_type or "application/octet-stream",
            )
        except Exception as store_exc:
            logger.warning("[Products] objectstore put_bytes failed: %s", store_exc)

        # 2. Ingest & extract text
        try:
            extracted_text = ingest.extract_text(
                file.filename or "product_doc",
                file.content_type or "",
                blob,
            )
        except Exception as ingest_exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Could not read content from '{file.filename}': {ingest_exc}",
            ) from ingest_exc

        # 3. Index into company knowledge base so AI retrieval can reference this product
        if extracted_text and extracted_text.strip():
            try:
                kb_doc = _index(
                    db,
                    client_id,
                    principal,
                    title=f"Product: {p_name} ({file.filename})",
                    filename=file.filename,
                    content_type=file.content_type or "application/octet-stream",
                    source_type="upload",
                    text=extracted_text,
                    tags=f"product,{p_name},{p_desc or ''}"[:250],
                    request=request,
                )
                kb_document_id = str(kb_doc.Id)
            except Exception as index_exc:
                logger.error("[Products] KB indexing failed for product '%s': %s", p_name, index_exc)
                # Keep file reference even if vector indexing failed
        kb_filename = file.filename

    product = LeadProduct(
        ClientId=client_id,
        ProductName=p_name,
        ProductDescription=p_desc,
        KnowledgeBaseFile=kb_filename,
        KbDocumentId=kb_document_id,
        CreatedBy=principal.email,
    )
    db.add(product)

    activity.log_principal(
        db,
        principal,
        action=A.PRODUCT_CREATED,
        client_id=client_id,
        entity_type="product",
        entity_id=product.Id,
        message=f"Added product '{p_name}'",
        meta={"product_name": p_name, "product_description": p_desc, "kb_file": kb_filename},
        request=request,
    )

    db.commit()
    db.refresh(product)
    return _product_out(product, db)


@router.get(
    "",
    response_model=ProductListOut,
    summary="List products for the authenticated company",
)
def list_products(
    search: str | None = Query(default=None, description="Search by product name or description"),
    principal: Principal = Depends(require("product.read", "kb.read")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)

    query = (
        db.query(LeadProduct)
        .filter(
            LeadProduct.ClientId == client_id,
            LeadProduct.IsDeleted == False,  # noqa: E712
        )
    )

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            (LeadProduct.ProductName.ilike(term))
            | (LeadProduct.ProductDescription.ilike(term))
        )

    rows = query.order_by(LeadProduct.CreatedAt.desc()).all()

    # Pre-fetch all KB documents in a single roundtrip
    needed_ids: set[str] = set()
    for r in rows:
        if r.KbDocumentId:
            needed_ids.add(str(r.KbDocumentId))
        raw_b = r.BoundKbDocumentIds
        if isinstance(raw_b, list):
            needed_ids.update(str(bid) for bid in raw_b if bid)

    doc_map: dict[str, LeadKbDocument] = {}
    if needed_ids:
        docs = (
            db.query(LeadKbDocument)
            .filter(
                LeadKbDocument.Id.in_(needed_ids),
                LeadKbDocument.ClientId == client_id,
                LeadKbDocument.IsDeleted == False,  # noqa: E712
            )
            .all()
        )
        doc_map = {str(d.Id): d for d in docs}

    items = [_product_out(r, db, doc_map=doc_map) for r in rows]
    return ProductListOut(total=len(items), items=items)


@router.get(
    "/{product_id}",
    response_model=ProductOut,
    summary="Get details of one product",
)
def get_product(
    product_id: str,
    principal: Principal = Depends(require("product.read", "kb.read")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)

    product = (
        db.query(LeadProduct)
        .filter(
            LeadProduct.Id == product_id,
            LeadProduct.ClientId == client_id,
            LeadProduct.IsDeleted == False,  # noqa: E712
        )
        .first()
    )
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")

    return _product_out(product, db)


@router.put(
    "/{product_id}",
    response_model=ProductOut,
    summary="Update a product (optionally replacing its knowledge base file)",
)
async def update_product(
    product_id: str,
    request: Request,
    product_name: str | None = Form(default=None),
    product_description: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    principal: Principal = Depends(require("product.manage", "kb.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)

    product = (
        db.query(LeadProduct)
        .filter(
            LeadProduct.Id == product_id,
            LeadProduct.ClientId == client_id,
            LeadProduct.IsDeleted == False,  # noqa: E712
        )
        .first()
    )
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")

    if product_name is not None and product_name.strip():
        product.ProductName = product_name.strip()
    if product_description is not None:
        product.ProductDescription = product_description.strip()

    # If new knowledge base file is uploaded, process and re-index
    if file and file.filename:
        blob = await file.read()
        if not blob:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The uploaded file is empty.")
        if len(blob) > settings.max_upload_bytes:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"File exceeds maximum allowed size ({settings.max_upload_bytes // (1024 * 1024)} MB).",
            )

        try:
            objectstore.put_bytes(
                blob,
                client_id=client_id,
                purpose="kb",
                filename=file.filename,
                content_type=file.content_type or "application/octet-stream",
            )
        except Exception as store_exc:
            logger.warning("[Products] objectstore put_bytes failed: %s", store_exc)

        try:
            extracted_text = ingest.extract_text(
                file.filename or "product_doc",
                file.content_type or "",
                blob,
            )
        except Exception as ingest_exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Could not read content from '{file.filename}': {ingest_exc}",
            ) from ingest_exc

        if extracted_text and extracted_text.strip():
            kb_doc = _index(
                db,
                client_id,
                principal,
                title=f"Product: {product.ProductName} ({file.filename})",
                filename=file.filename,
                content_type=file.content_type or "application/octet-stream",
                source_type="upload",
                text=extracted_text,
                tags=f"product,{product.ProductName},{product.ProductDescription or ''}"[:250],
                request=request,
            )
            product.KbDocumentId = str(kb_doc.Id)
        product.KnowledgeBaseFile = file.filename

    product.UpdatedAt = utcnow()
    product.UpdatedBy = principal.email

    activity.log_principal(
        db,
        principal,
        action=A.PRODUCT_UPDATED,
        client_id=client_id,
        entity_type="product",
        entity_id=product.Id,
        message=f"Updated product '{product.ProductName}'",
        meta={"product_name": product.ProductName, "product_description": product.ProductDescription},
        request=request,
    )

    db.commit()
    db.refresh(product)
    return _product_out(product, db)


@router.delete(
    "/{product_id}",
    summary="Delete (soft-delete) a product and optionally remove its KB reference",
)
def delete_product(
    product_id: str,
    request: Request,
    principal: Principal = Depends(require("product.manage", "kb.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)

    product = (
        db.query(LeadProduct)
        .filter(
            LeadProduct.Id == product_id,
            LeadProduct.ClientId == client_id,
            LeadProduct.IsDeleted == False,  # noqa: E712
        )
        .first()
    )
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")

    product.IsDeleted = True
    product.UpdatedAt = utcnow()
    product.UpdatedBy = principal.email

    # Also soft-delete the linked KB document so AI retrieval stops citing it
    if product.KbDocumentId:
        kb_doc = db.get(LeadKbDocument, product.KbDocumentId)
        if kb_doc and kb_doc.ClientId == client_id:
            kb_doc.IsDeleted = True
            kb_doc.UpdatedBy = principal.email
            kb_doc.UpdatedAt = utcnow()

    activity.log_principal(
        db,
        principal,
        action=A.PRODUCT_DELETED,
        client_id=client_id,
        entity_type="product",
        entity_id=product.Id,
        message=f"Deleted product '{product.ProductName}'",
        request=request,
    )

    db.commit()
    return {"success": True, "message": f"Product '{product.ProductName}' deleted."}


@router.post(
    "/{product_id}/bind-existing-kb",
    response_model=ProductOut,
    summary="Bind an existing company knowledge base document to an existing product",
)
def bind_existing_kb(
    product_id: str,
    payload: BindExistingKbRequest,
    request: Request,
    principal: Principal = Depends(require("product.manage", "kb.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    product = (
        db.query(LeadProduct)
        .filter(
            LeadProduct.Id == product_id,
            LeadProduct.ClientId == client_id,
            LeadProduct.IsDeleted == False,  # noqa: E712
        )
        .first()
    )
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")

    doc_id_str = payload.kb_document_id.strip()
    kb_doc = (
        db.query(LeadKbDocument)
        .filter(
            LeadKbDocument.Id == doc_id_str,
            LeadKbDocument.ClientId == client_id,
            LeadKbDocument.IsDeleted == False,  # noqa: E712
        )
        .first()
    )
    if not kb_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knowledge base document not found in company repository.")

    current_bounds = list(product.BoundKbDocumentIds or [])
    if doc_id_str != str(product.KbDocumentId) and doc_id_str not in current_bounds:
        current_bounds.append(doc_id_str)
        product.BoundKbDocumentIds = current_bounds

    if not product.KbDocumentId:
        product.KbDocumentId = doc_id_str
        product.KnowledgeBaseFile = kb_doc.FileName or kb_doc.Title

    # Append product tag to kb_doc so vector retrieval links it
    product_tag = f"product,{product.ProductName}"
    if kb_doc.Tags:
        if product.ProductName.lower() not in kb_doc.Tags.lower():
            kb_doc.Tags = f"{kb_doc.Tags},{product_tag}"
    else:
        kb_doc.Tags = product_tag

    product.UpdatedAt = utcnow()
    product.UpdatedBy = principal.email

    activity.log_principal(
        db,
        principal,
        action=A.PRODUCT_UPDATED,
        client_id=client_id,
        entity_type="product",
        entity_id=product.Id,
        message=f"Bound knowledge base document '{kb_doc.Title}' to product '{product.ProductName}'",
        meta={"product_name": product.ProductName, "kb_document_id": doc_id_str, "kb_title": kb_doc.Title},
        request=request,
    )

    db.commit()
    db.refresh(product)
    return _product_out(product, db)


@router.post(
    "/{product_id}/bind-kb",
    response_model=ProductOut,
    summary="Bind another knowledge base (by existing document ID or uploading a new file) to a product",
)
async def bind_kb(
    product_id: str,
    request: Request,
    kb_document_id: str | None = Form(default=None, description="Existing KB document ID to bind"),
    file: UploadFile | None = File(default=None, description="New file to upload and bind"),
    principal: Principal = Depends(require("product.manage", "kb.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    product = (
        db.query(LeadProduct)
        .filter(
            LeadProduct.Id == product_id,
            LeadProduct.ClientId == client_id,
            LeadProduct.IsDeleted == False,  # noqa: E712
        )
        .first()
    )
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")

    current_bounds = list(product.BoundKbDocumentIds or [])
    bound_doc_title: str = ""

    if kb_document_id and kb_document_id.strip():
        doc_id = kb_document_id.strip()
        kb_doc = (
            db.query(LeadKbDocument)
            .filter(
                LeadKbDocument.Id == doc_id,
                LeadKbDocument.ClientId == client_id,
                LeadKbDocument.IsDeleted == False,  # noqa: E712
            )
            .first()
        )
        if not kb_doc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Knowledge base document not found in company repository.")

        bound_doc_title = kb_doc.Title
        if doc_id != str(product.KbDocumentId) and doc_id not in current_bounds:
            current_bounds.append(doc_id)
            product.BoundKbDocumentIds = current_bounds

        if not product.KbDocumentId:
            product.KbDocumentId = doc_id
            product.KnowledgeBaseFile = kb_doc.FileName or kb_doc.Title

        product_tag = f"product,{product.ProductName}"
        if kb_doc.Tags:
            if product.ProductName.lower() not in kb_doc.Tags.lower():
                kb_doc.Tags = f"{kb_doc.Tags},{product_tag}"
        else:
            kb_doc.Tags = product_tag

    elif file and file.filename:
        blob = await file.read()
        if not blob:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The uploaded file is empty.")
        if len(blob) > settings.max_upload_bytes:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"File exceeds maximum allowed size ({settings.max_upload_bytes // (1024 * 1024)} MB).",
            )

        try:
            objectstore.put_bytes(
                blob,
                client_id=client_id,
                purpose="kb",
                filename=file.filename,
                content_type=file.content_type or "application/octet-stream",
            )
        except Exception as store_exc:
            logger.warning("[Products] objectstore put_bytes failed: %s", store_exc)

        try:
            extracted_text = ingest.extract_text(
                file.filename or "product_doc",
                file.content_type or "",
                blob,
            )
        except Exception as ingest_exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Could not read content from '{file.filename}': {ingest_exc}",
            ) from ingest_exc

        if not extracted_text or not extracted_text.strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No readable text found in file.")

        kb_doc = _index(
            db,
            client_id,
            principal,
            title=f"Product: {product.ProductName} ({file.filename})",
            filename=file.filename,
            content_type=file.content_type or "application/octet-stream",
            source_type="upload",
            text=extracted_text,
            tags=f"product,{product.ProductName}",
            request=request,
        )
        new_id = str(kb_doc.Id)
        bound_doc_title = kb_doc.Title
        if new_id not in current_bounds:
            current_bounds.append(new_id)
            product.BoundKbDocumentIds = current_bounds

        if not product.KbDocumentId:
            product.KbDocumentId = new_id
            product.KnowledgeBaseFile = file.filename
    else:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Please provide either an existing kb_document_id or an uploaded file to bind.",
        )

    product.UpdatedAt = utcnow()
    product.UpdatedBy = principal.email

    activity.log_principal(
        db,
        principal,
        action=A.PRODUCT_UPDATED,
        client_id=client_id,
        entity_type="product",
        entity_id=product.Id,
        message=f"Bound additional knowledge base '{bound_doc_title}' to product '{product.ProductName}'",
        meta={"product_name": product.ProductName, "bound_title": bound_doc_title},
        request=request,
    )

    db.commit()
    db.refresh(product)
    return _product_out(product, db)


@router.delete(
    "/{product_id}/bind-kb/{kb_document_id}",
    response_model=ProductOut,
    summary="Unbind a knowledge base document from a product",
)
def unbind_kb(
    product_id: str,
    kb_document_id: str,
    request: Request,
    principal: Principal = Depends(require("product.manage", "kb.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    product = (
        db.query(LeadProduct)
        .filter(
            LeadProduct.Id == product_id,
            LeadProduct.ClientId == client_id,
            LeadProduct.IsDeleted == False,  # noqa: E712
        )
        .first()
    )
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")

    doc_id_str = str(kb_document_id).strip()
    current_bounds = list(product.BoundKbDocumentIds or [])
    was_bound = False

    if doc_id_str in current_bounds:
        current_bounds.remove(doc_id_str)
        product.BoundKbDocumentIds = current_bounds
        was_bound = True

    if str(product.KbDocumentId) == doc_id_str:
        was_bound = True
        if current_bounds:
            next_primary_id = current_bounds[0]
            next_doc = db.get(LeadKbDocument, next_primary_id)
            product.KbDocumentId = next_primary_id
            product.KnowledgeBaseFile = next_doc.FileName if next_doc else None
        else:
            product.KbDocumentId = None
            product.KnowledgeBaseFile = None

    if not was_bound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knowledge base document is not bound to this product.")

    product.UpdatedAt = utcnow()
    product.UpdatedBy = principal.email

    activity.log_principal(
        db,
        principal,
        action=A.PRODUCT_UPDATED,
        client_id=client_id,
        entity_type="product",
        entity_id=product.Id,
        message=f"Unbound knowledge base document {doc_id_str} from product '{product.ProductName}'",
        meta={"product_name": product.ProductName, "unbound_kb_document_id": doc_id_str},
        request=request,
    )

    db.commit()
    db.refresh(product)
    return _product_out(product, db)
