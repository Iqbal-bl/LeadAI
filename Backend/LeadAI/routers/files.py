"""
Document store + lead-threshold settings.

FILES
-----
Every uploaded artefact in the platform — knowledge-base sources, campaign
lists, campaign media, exports — gets a row in `leadai_files` and an object in
MinIO. Two rules:

  * Downloads are presigned and time-limited, never public. The bucket policy
    stays private; a link is minted per request and expires.
  * A file is scoped to a company. `assert_owns` runs on every read, so a valid
    file id from another tenant returns 404, not 403 — an id in another company
    should be indistinguishable from one that does not exist.

THRESHOLD
---------
The lead-score threshold lives here rather than in companies.py because it is a
new, self-contained control surface with its own dashboard tiles. It answers
one question: at what score does a lead stop being the AI's problem and become
the sales team's?
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import activity
from ..activity import A
from ..config import settings
from ..db import get_leadai_db
from ..models import Lead, LeadCompanySettings, LeadFile, utcnow
from ..rbac import Principal, assert_owns, scoped
from ..schemas import Ok
from ..schemas_ext import (
    FileListOut,
    FileOut,
    JobStatsOut,
    StorageHealthOut,
    ThresholdSettingsIn,
    ThresholdSettingsOut,
)
from ..serializers_ext import file_out
from ..services import conversation_flow, jobs, objectstore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["LeadAI • Document store"])
threshold_router = APIRouter(prefix="/threshold", tags=["LeadAI • Lead threshold"])


def _file(db: Session, file_id: str, client_id: str) -> LeadFile:
    row = db.get(LeadFile, file_id)
    if row is None or row.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    assert_owns(row.ClientId, client_id)
    return row


@router.get("/health", response_model=StorageHealthOut, summary="Object store health")
def storage_health(
    # `scoped()` always yields a (principal, client_id) pair. The client id is
    # unused here — health is process-wide — but the dependency still enforces
    # that the caller is authenticated and bound to some tenant.
    scope: tuple[Principal, str] = Depends(scoped("company.read")),
):
    return StorageHealthOut(**objectstore.health())


@router.post(
    "",
    response_model=FileOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document",
)
def upload_file(
    request: Request,
    file: UploadFile = File(...),
    purpose: str = Form(default="general"),
    linked_entity_type: str | None = Form(default=None),
    linked_entity_id: str | None = Form(default=None),
    scope: tuple[Principal, str] = Depends(scoped("file.manage", "kb.manage")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    blob = file.file.read()
    if not blob:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file is empty.")
    if len(blob) > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Files must be under {settings.max_upload_bytes // (1024 * 1024)} MB.",
        )

    try:
        stored = objectstore.put_bytes(
            blob,
            client_id=client_id,
            purpose=purpose,
            filename=file.filename or "upload",
            content_type=file.content_type or "application/octet-stream",
        )
    except objectstore.StorageError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    row = LeadFile(
        ClientId=client_id,
        Purpose=purpose,
        FileName=file.filename or "upload",
        ContentType=file.content_type or "application/octet-stream",
        SizeBytes=stored.size,
        Bucket=stored.bucket,
        ObjectKey=stored.key,
        Checksum=stored.checksum,
        StorageBackend=stored.backend,
        UploadedByEmail=principal.email,
        LinkedEntityType=linked_entity_type,
        LinkedEntityId=linked_entity_id,
        CreatedBy=principal.email,
    )
    db.add(row)
    db.flush()
    activity.log_principal(
        db, principal, action=A.FILE_UPLOADED, client_id=client_id,
        entity_type="file", entity_id=row.Id,
        message=f"Uploaded '{row.FileName}' ({row.SizeBytes:,} bytes)",
        meta={"purpose": purpose, "backend": stored.backend}, request=request,
    )
    db.commit()
    return file_out(row, objectstore.presigned_url(row.Bucket, row.ObjectKey, download_name=row.FileName))


@router.get("", response_model=FileListOut, summary="List documents")
def list_files(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    purpose: str | None = None,
    scope: tuple[Principal, str] = Depends(scoped("file.read", "kb.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    query = db.query(LeadFile).filter(
        LeadFile.ClientId == client_id,
        LeadFile.IsDeleted == False,  # noqa: E712
    )
    if purpose:
        query = query.filter(LeadFile.Purpose == purpose)
    total = query.count()
    rows = (
        query.order_by(LeadFile.CreatedAt.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    # Presigning is a local HMAC, not a network call, so doing it per row is cheap.
    return FileListOut(
        total_items=total, page=page, page_size=page_size,
        items=[
            file_out(r, objectstore.presigned_url(r.Bucket, r.ObjectKey, download_name=r.FileName))
            for r in rows
        ],
    )


# Literal paths first — `/{file_id}` would otherwise swallow "jobs".
@router.get("/jobs/stats", response_model=JobStatsOut, summary="Background queue depth")
def job_stats(
    scope: tuple[Principal, str] = Depends(scoped("analytics.read")),
    db: Session = Depends(get_leadai_db),
):
    """Operational visibility: if `queued` climbs and `done` does not, the
    worker is down and campaigns are silently stalled."""
    return JobStatsOut(**jobs.stats(db))


@router.get("/{file_id}", response_model=FileOut, summary="File metadata + fresh link")
def get_file(
    file_id: str,
    scope: tuple[Principal, str] = Depends(scoped("file.read", "kb.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    row = _file(db, file_id, client_id)
    return file_out(row, objectstore.presigned_url(row.Bucket, row.ObjectKey, download_name=row.FileName))


@router.get("/{file_id}/download", summary="Stream the file through the API")
def download_file(
    file_id: str,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("file.read", "kb.read")),
    db: Session = Depends(get_leadai_db),
):
    """Proxy download.

    Prefer the presigned URL — it goes straight from the browser to MinIO and
    does not occupy an application worker. This endpoint exists for the local
    backend (which cannot presign) and for clients that cannot follow a
    cross-origin link.
    """
    import io

    principal, client_id = scope
    row = _file(db, file_id, client_id)
    try:
        blob = objectstore.get_bytes(row.Bucket, row.ObjectKey)
    except objectstore.StorageError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    activity.log_principal(
        db, principal, action=A.FILE_DOWNLOADED, client_id=client_id,
        entity_type="file", entity_id=row.Id,
        message=f"Downloaded '{row.FileName}'", request=request,
    )
    db.commit()
    return StreamingResponse(
        io.BytesIO(blob),
        media_type=row.ContentType,
        headers={"Content-Disposition": f'attachment; filename="{row.FileName}"'},
    )


@router.delete("/{file_id}", response_model=Ok, summary="Delete a document")
def delete_file(
    file_id: str,
    request: Request,
    hard: bool = Query(default=False, description="Also remove the object from storage"),
    scope: tuple[Principal, str] = Depends(scoped("file.manage", "kb.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Soft delete by default.

    The metadata row is what a campaign's provenance trail points at; removing
    it outright would leave "imported from a file" with no file. `hard=true`
    additionally purges the bytes, for a genuine erasure request.
    """
    principal, client_id = scope
    row = _file(db, file_id, client_id)
    row.IsDeleted = True
    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()
    if hard:
        objectstore.delete(row.Bucket, row.ObjectKey)
    activity.log_principal(
        db, principal, action=A.FILE_DELETED, client_id=client_id,
        entity_type="file", entity_id=row.Id,
        message=f"Deleted '{row.FileName}'" + (" (purged from storage)" if hard else ""),
        log_type="Warning" if hard else "Info", request=request,
    )
    db.commit()
    return Ok(message="File deleted")


# =========================================================================== #
# lead threshold
# =========================================================================== #
def _threshold_payload(db: Session, client_id: str) -> ThresholdSettingsOut:
    """
    Build the threshold DTO for one tenant.

    Deliberately a plain function rather than the route handler itself: the PUT
    endpoint wants to return the same payload after saving, and calling a
    handler whose arguments are `Depends(...)` defaults only works by accident.
    Keeping the logic here means both routes share one code path.
    """
    from sqlalchemy import func

    cfg = conversation_flow.company_settings(db, client_id)
    effective = conversation_flow.lead_threshold(db, client_id)
    above = (
        db.query(func.count(Lead.Id))
        .filter(
            Lead.ClientId == client_id,
            Lead.IsDeleted == False,  # noqa: E712
            Lead.Score >= effective,
        )
        .scalar()
        or 0
    )
    total = (
        db.query(func.count(Lead.Id))
        .filter(Lead.ClientId == client_id, Lead.IsDeleted == False)  # noqa: E712
        .scalar()
        or 0
    )
    return ThresholdSettingsOut(
        client_id=client_id,
        lead_score_threshold=cfg.LeadScoreThreshold if cfg else None,
        auto_convert_threshold=cfg.AutoConvertThreshold if cfg else None,
        notify_on_threshold=bool(cfg.NotifyOnThreshold) if cfg else True,
        hide_below_threshold=bool(cfg.HideBelowThreshold) if cfg else False,
        effective_lead_score_threshold=effective,
        effective_auto_convert_threshold=(
            cfg.AutoConvertThreshold
            if cfg is not None and cfg.AutoConvertThreshold is not None
            else settings.auto_convert_threshold
        ),
        leads_above_threshold=above,
        leads_below_threshold=max(0, total - above),
    )


@threshold_router.get("", response_model=ThresholdSettingsOut, summary="Current threshold")
def get_threshold(
    scope: tuple[Principal, str] = Depends(scoped("settings.manage", "analytics.read")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    return _threshold_payload(db, client_id)


@threshold_router.put("", response_model=ThresholdSettingsOut, summary="Set the threshold")
def set_threshold(
    payload: ThresholdSettingsIn,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("settings.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Change the score at which leads reach the dashboard.

    Applies to FUTURE evaluations. Existing `IsAboveThreshold` flags are
    recomputed in bulk below so the dashboard is consistent immediately —
    otherwise lowering the bar would leave older qualifying leads invisible
    until their next message.
    """
    principal, client_id = scope
    cfg = conversation_flow.company_settings(db, client_id)
    if cfg is None:
        cfg = LeadCompanySettings(ClientId=client_id, CreatedBy=principal.email)
        db.add(cfg)
        db.flush()

    data = payload.model_dump(exclude_unset=True)
    if "lead_score_threshold" in data:
        cfg.LeadScoreThreshold = data["lead_score_threshold"]
    if "auto_convert_threshold" in data:
        cfg.AutoConvertThreshold = data["auto_convert_threshold"]
    if "notify_on_threshold" in data and data["notify_on_threshold"] is not None:
        cfg.NotifyOnThreshold = data["notify_on_threshold"]
    if "hide_below_threshold" in data and data["hide_below_threshold"] is not None:
        cfg.HideBelowThreshold = data["hide_below_threshold"]
    cfg.UpdatedBy = principal.email
    cfg.UpdatedAt = utcnow()

    conversation_flow.invalidate_threshold_cache(client_id)
    effective = (
        cfg.LeadScoreThreshold
        if cfg.LeadScoreThreshold is not None
        else settings.lead_score_threshold
    )
    # Two bulk UPDATEs, not a per-row loop: this must stay fast with 100k leads.
    db.query(Lead).filter(
        Lead.ClientId == client_id, Lead.Score >= effective
    ).update({"IsAboveThreshold": True}, synchronize_session=False)
    db.query(Lead).filter(
        Lead.ClientId == client_id, Lead.Score < effective
    ).update({"IsAboveThreshold": False}, synchronize_session=False)

    activity.log_principal(
        db, principal, action=A.SETTINGS_UPDATED, client_id=client_id,
        entity_type="settings", entity_id=cfg.Id,
        message=f"Lead threshold set to {effective}",
        meta=data, request=request,
    )
    db.commit()
    return _threshold_payload(db, client_id)
