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
from ..schemas import ProductListOut, ProductOut
from ..services import ingest, objectstore
from .knowledge import _index

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/products", tags=["LeadAI • Products"])


def _product_out(product: LeadProduct) -> ProductOut:
    return ProductOut(
        id=str(product.Id),
        client_id=str(product.ClientId),
        product_name=product.ProductName,
        product_type=product.ProductType,
        knowledge_base_file=product.KnowledgeBaseFile,
        kb_document_id=str(product.KbDocumentId) if product.KbDocumentId else None,
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
    product_type: str = Form(..., description="Category or type of the product"),
    file: UploadFile | None = File(default=None, description="Knowledge base file (PDF, DOCX, TXT, CSV)"),
    principal: Principal = Depends(require("product.manage", "kb.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    p_name = product_name.strip()
    p_type = product_type.strip()

    if not p_name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Product name cannot be empty.")
    if not p_type:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Product type cannot be empty.")

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
                    tags=f"product,{p_name},{p_type}",
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
        ProductType=p_type,
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
        message=f"Added product '{p_name}' ({p_type})",
        meta={"product_name": p_name, "product_type": p_type, "kb_file": kb_filename},
        request=request,
    )

    db.commit()
    db.refresh(product)
    return _product_out(product)


@router.get(
    "",
    response_model=ProductListOut,
    summary="List products for the authenticated company",
)
def list_products(
    search: str | None = Query(default=None, description="Search by product name or type"),
    product_type: str | None = Query(default=None, description="Filter by product type"),
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
            (LeadProduct.ProductName.ilike(term)) | (LeadProduct.ProductType.ilike(term))
        )

    if product_type:
        query = query.filter(LeadProduct.ProductType == product_type.strip())

    rows = query.order_by(LeadProduct.CreatedAt.desc()).all()
    items = [_product_out(r) for r in rows]
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

    return _product_out(product)


@router.put(
    "/{product_id}",
    response_model=ProductOut,
    summary="Update a product (optionally replacing its knowledge base file)",
)
async def update_product(
    product_id: str,
    request: Request,
    product_name: str | None = Form(default=None),
    product_type: str | None = Form(default=None),
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
    if product_type is not None and product_type.strip():
        product.ProductType = product_type.strip()

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
                tags=f"product,{product.ProductName},{product.ProductType}",
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
        meta={"product_name": product.ProductName, "product_type": product.ProductType},
        request=request,
    )

    db.commit()
    db.refresh(product)
    return _product_out(product)


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
