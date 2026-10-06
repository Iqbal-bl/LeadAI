"""Company-admin-defined data points ("what should the AI collect for us?").

One company-wide set (not per script — every script/channel asks toward the
same fields). Reuses the same script.read/script.manage permissions as
scripts/prompts: same tier of "how the AI behaves for us" configuration.

Extraction and proactive asking live in services/ai_engine.py (qualify) and
services/memory.py (missing_data_points_note) — this router only owns the
admin-facing CRUD.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import activity
from ..activity import A
from ..db import get_leadai_db
from ..models import LeadCompanyDataPoint, utcnow
from ..rbac import Principal, assert_owns, require, resolve_scope
from ..schemas import DataPointCreate, DataPointOut, DataPointUpdate, Ok
from ..serializers import data_point_out

router = APIRouter(prefix="/data-points", tags=["LeadAI • Company data points"])


def _load(db: Session, data_point_id: str, client_id: str) -> LeadCompanyDataPoint:
    row = db.get(LeadCompanyDataPoint, data_point_id)
    if row is None or row.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Data point not found")
    assert_owns(row.ClientId, client_id)
    return row


@router.get("", response_model=list[DataPointOut], summary="List a company's data points")
def list_data_points(
    include_inactive: bool = False,
    principal: Principal = Depends(require("script.read")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    query = db.query(LeadCompanyDataPoint).filter(
        LeadCompanyDataPoint.ClientId == client_id,
        LeadCompanyDataPoint.IsDeleted == False,  # noqa: E712
    )
    if not include_inactive:
        query = query.filter(LeadCompanyDataPoint.IsActive == True)  # noqa: E712
    rows = query.order_by(
        LeadCompanyDataPoint.DisplayOrder.asc(), LeadCompanyDataPoint.CreatedAt.asc()
    ).all()
    return [data_point_out(r) for r in rows]


@router.post(
    "",
    response_model=DataPointOut,
    status_code=status.HTTP_201_CREATED,
    summary="Define a new data point for the AI to collect",
)
def create_data_point(
    payload: DataPointCreate,
    request: Request,
    principal: Principal = Depends(require("script.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    if payload.data_type == "select" and (not payload.options or len(payload.options) < 2):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "A select data point needs at least two options.",
        )
    existing = (
        db.query(LeadCompanyDataPoint)
        .filter(
            LeadCompanyDataPoint.ClientId == client_id,
            LeadCompanyDataPoint.Key == payload.key,
            LeadCompanyDataPoint.IsDeleted == False,  # noqa: E712
        )
        .one_or_none()
    )
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A data point with key '{payload.key}' already exists."
        )

    row = LeadCompanyDataPoint(
        ClientId=client_id,
        Key=payload.key,
        Label=payload.label,
        DataType=payload.data_type,
        OptionsJson=payload.options if payload.data_type == "select" else None,
        Description=payload.description,
        Required=payload.required,
        DisplayOrder=payload.display_order,
        CreatedBy=principal.email,
    )
    db.add(row)
    db.flush()

    activity.log_principal(
        db, principal, action=A.DATA_POINT_CREATED, client_id=client_id,
        entity_type="data_point", entity_id=row.Id,
        message=f"Defined data point '{row.Label}' ({row.DataType})", request=request,
    )
    db.commit()
    db.refresh(row)
    return data_point_out(row)


@router.patch("/{data_point_id}", response_model=DataPointOut, summary="Update a data point")
def update_data_point(
    data_point_id: str,
    payload: DataPointUpdate,
    request: Request,
    principal: Principal = Depends(require("script.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    row = _load(db, data_point_id, client_id)
    changed: list[str] = []

    new_type = payload.data_type or row.DataType
    new_options = payload.options if payload.options is not None else row.OptionsJson
    if new_type == "select" and (not new_options or len(new_options) < 2):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "A select data point needs at least two options.",
        )

    for field, column in (
        ("label", "Label"),
        ("data_type", "DataType"),
        ("description", "Description"),
        ("required", "Required"),
        ("display_order", "DisplayOrder"),
        ("is_active", "IsActive"),
    ):
        value = getattr(payload, field)
        if value is not None:
            setattr(row, column, value)
            changed.append(field)

    if payload.options is not None:
        row.OptionsJson = payload.options
        changed.append("options")
    if row.DataType != "select":
        row.OptionsJson = None

    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()

    activity.log_principal(
        db, principal, action=A.DATA_POINT_UPDATED, client_id=client_id,
        entity_type="data_point", entity_id=row.Id,
        message=f"Updated data point '{row.Label}'", meta={"changed_fields": changed},
        request=request,
    )
    db.commit()
    db.refresh(row)
    return data_point_out(row)


@router.delete("/{data_point_id}", response_model=Ok, summary="Delete a data point")
def delete_data_point(
    data_point_id: str,
    request: Request,
    principal: Principal = Depends(require("script.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    row = _load(db, data_point_id, client_id)
    row.IsDeleted = True
    row.IsActive = False
    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()

    activity.log_principal(
        db, principal, action=A.DATA_POINT_DELETED, client_id=client_id,
        entity_type="data_point", entity_id=row.Id,
        message=f"Deleted data point '{row.Label}'", request=request,
    )
    db.commit()
    return Ok(message="Data point deleted")
