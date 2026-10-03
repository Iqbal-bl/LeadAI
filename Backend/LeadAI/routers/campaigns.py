"""
Campaign management — contact lists and bulk sends.

TWO ROUTERS IN ONE FILE
    /lists      upload and manage audiences
    /campaigns  create, preview, start, pause, monitor

THE LIFECYCLE THE FRONTEND DRIVES
---------------------------------
    POST /lists/preview        (multipart)  -> parse only, nothing saved.
                                              Shows "4,812 rows, 4,690 valid,
                                              118 duplicates" so the operator
                                              can fix the file first.
    POST /lists                (multipart)  -> save the file to MinIO + the rows
    POST /campaigns                         -> draft
    POST /campaigns/{id}/build              -> materialise recipients
    GET  /campaigns/{id}/preview            -> sample rendered messages + ETA
    POST /campaigns/{id}/start              -> hand to the job worker
    GET  /campaigns/{id}                    -> poll counters (or use the WS)
    POST /campaigns/{id}/pause | /resume | /cancel

Start is deliberately a separate call from create. A bulk send to 30,000 people
should never be one request away from a typo in a form.
"""
from __future__ import annotations

import csv
import io
import logging

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import activity
from ..activity import A
from ..config import settings
from ..db import get_leadai_db
from ..models import (
    Lead,
    LeadActivityLog,
    LeadCampaign,
    LeadCampaignExecution,
    LeadCampaignRecipient,
    LeadCampaignRecipientAttempt,
    LeadChannelAccount,
    LeadCompanyDataPoint,
    LeadContactList,
    LeadContactListItem,
    LeadFile,
    utcnow,
)
from ..rbac import Principal, assert_owns, scoped
from ..schemas import ActivityListOut, Ok
from ..schemas_ext import (
    CampaignCreate,
    CampaignExecutionListOut,
    CampaignExecutionOut,
    CampaignHistoryItemOut,
    CampaignHistoryListOut,
    CampaignListOut,
    CampaignOut,
    CampaignPreviewOut,
    CampaignRecipientAttemptListOut,
    CampaignUpdate,
    ContactListFromLeads,
    ContactListItemsOut,
    ContactListOut,
    ContactListPreviewOut,
    RecipientListOut,
)
from ..serializers import activity_out
from ..serializers_ext import (
    campaign_execution_out,
    campaign_out,
    campaign_recipient_attempt_out,
    contact_list_item_out,
    contact_list_out,
    recipient_out,
)
from ..services import audience, campaign_runner, jobs, objectstore

logger = logging.getLogger(__name__)

lists_router = APIRouter(prefix="/lists", tags=["LeadAI • Contact lists"])
router = APIRouter(prefix="/campaigns", tags=["LeadAI • Campaigns"])

ALLOWED_UPLOAD_SUFFIXES = (".csv", ".tsv", ".txt", ".xlsx", ".xls", ".xlsm", ".docx")


# =========================================================================== #
# contact lists
# =========================================================================== #
def _read_upload(file: UploadFile) -> bytes:
    name = (file.filename or "").lower()
    if not name.endswith(ALLOWED_UPLOAD_SUFFIXES):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Upload a CSV, Excel or Word file. Got '{file.filename}'.",
        )
    blob = file.file.read()
    if not blob:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file is empty.")
    if len(blob) > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File is larger than {settings.max_upload_bytes // (1024 * 1024)} MB.",
        )
    return blob


@lists_router.post(
    "/preview",
    response_model=ContactListPreviewOut,
    summary="Parse an uploaded file WITHOUT saving it",
)
def preview_list(
    file: UploadFile = File(...),
    column_map: str | None = Form(default=None, description='JSON, e.g. {"phone":"Mobile No"}'),
    region: str = Form(default="IN"),
    scope: tuple[Principal, str] = Depends(scoped("campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Dry run. Nothing is written — this is the "show me the damage" step."""
    import json

    blob = _read_upload(file)
    mapping = None
    if column_map:
        try:
            mapping = json.loads(column_map)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "column_map must be valid JSON")

    result = audience.parse_contacts(
        file.filename or "upload", file.content_type or "", blob,
        column_map=mapping, region=region,
    )
    return ContactListPreviewOut(
        headers=result.headers,
        column_map=result.column_map,
        total=result.total,
        valid=result.valid,
        invalid=result.total - result.valid,
        duplicates=result.duplicates,
        warnings=result.warnings,
        sample=result.sample,
    )


@lists_router.post(
    "",
    response_model=ContactListOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a contact list (Excel / CSV / Word)",
)
def create_list(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(...),
    description: str | None = Form(default=None),
    column_map: str | None = Form(default=None),
    region: str = Form(default="IN"),
    tags: str | None = Form(default=None),
    scope: tuple[Principal, str] = Depends(scoped("campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Parse, store the original in MinIO, and persist the normalised rows.

    The original file is kept deliberately: when someone asks in three months
    "where did this number come from", the answer has to be the actual file
    that was uploaded, not a reconstruction.
    """
    import json

    principal, client_id = scope
    blob = _read_upload(file)
    mapping = json.loads(column_map) if column_map else None

    result = audience.parse_contacts(
        file.filename or "upload", file.content_type or "", blob,
        column_map=mapping, region=region,
    )
    if result.total == 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No usable rows were found. " + (result.warnings[0] if result.warnings else ""),
        )

    file_row = None
    try:
        stored = objectstore.put_bytes(
            blob,
            client_id=client_id,
            purpose="campaign_list",
            filename=file.filename or "list",
            content_type=file.content_type or "application/octet-stream",
        )
        file_row = LeadFile(
            ClientId=client_id,
            Purpose="campaign_list",
            FileName=file.filename or "list",
            ContentType=file.content_type or "application/octet-stream",
            SizeBytes=stored.size,
            Bucket=stored.bucket,
            ObjectKey=stored.key,
            Checksum=stored.checksum,
            StorageBackend=stored.backend,
            UploadedByEmail=principal.email,
            LinkedEntityType="contact_list",
            CreatedBy=principal.email,
        )
        db.add(file_row)
        db.flush()
    except objectstore.StorageError as exc:
        # Storage is for provenance, not correctness — do not lose the import.
        logger.warning("[LeadAI lists] source file not archived: %s", exc)

    contact_list = LeadContactList(
        ClientId=client_id,
        Name=name,
        Description=description,
        SourceType="upload",
        SourceFileId=file_row.Id if file_row is not None else None,
        TotalCount=result.total,
        ValidCount=result.valid,
        InvalidCount=result.total - result.valid,
        DuplicateCount=result.duplicates,
        ColumnMapJson=result.column_map,
        Status="ready",
        StatusMessage="; ".join(result.warnings)[:500] or None,
        Tags=tags,
        CreatedBy=principal.email,
    )
    db.add(contact_list)
    db.flush()

    items = audience.to_list_items(result, client_id, contact_list.Id, principal.email)
    db.bulk_save_objects(items)  # one INSERT batch rather than N round-trips

    if file_row is not None:
        file_row.LinkedEntityId = contact_list.Id

    activity.log_principal(
        db, principal, action=A.LIST_IMPORTED, client_id=client_id,
        entity_type="contact_list", entity_id=contact_list.Id,
        message=f"Imported '{name}': {result.valid}/{result.total} usable rows",
        meta={
            "total": result.total, "valid": result.valid,
            "duplicates": result.duplicates, "file": file.filename,
        },
        request=request,
    )
    db.commit()
    return contact_list_out(contact_list)


@lists_router.post(
    "/from-leads",
    response_model=ContactListOut,
    status_code=status.HTTP_201_CREATED,
    summary="Build a list from existing leads",
)
def create_list_from_leads(
    payload: ContactListFromLeads,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Snapshot a lead filter into a reusable list.

    A snapshot, not a live query: a campaign must contact exactly the people the
    operator approved, not whoever happens to match the filter when it runs.
    """
    from ..models import Lead, LeadChannelIdentity, LeadConversation, LeadCustomer
    from ..security import decrypt_pii, encrypt_pii, mask_phone, phone_fingerprint

    principal, client_id = scope
    query = (
        db.query(Lead, LeadConversation, LeadCustomer)
        .join(LeadConversation, LeadConversation.Id == Lead.ConversationId)
        .join(LeadCustomer, LeadCustomer.Id == LeadConversation.CustomerId)
        .filter(Lead.ClientId == client_id, Lead.IsDeleted == False)  # noqa: E712
    )
    # A phone/email isn't the only usable contact detail — a resolved Instagram/
    # Messenger identity is just as contactable for a social campaign. Without
    # this, every social-only lead gets marked "No contact detail" and is
    # permanently excluded from every list built from it, on every channel.
    social_customer_ids = {
        cid
        for (cid,) in db.query(LeadChannelIdentity.CustomerId)
        .filter(
            LeadChannelIdentity.ClientId == client_id,
            LeadChannelIdentity.IsDeleted == False,  # noqa: E712
        )
        .distinct()
        .all()
    }
    if payload.status:
        query = query.filter(Lead.Status.in_(payload.status))
    if payload.min_score is not None:
        query = query.filter(Lead.Score >= payload.min_score)
    if payload.above_threshold:
        query = query.filter(Lead.IsAboveThreshold == True)  # noqa: E712
    if payload.channel:
        query = query.filter(LeadConversation.Channel == payload.channel)
    if payload.created_after:
        query = query.filter(Lead.CreatedAt >= payload.created_after)

    contact_list = LeadContactList(
        ClientId=client_id,
        Name=payload.name,
        Description=payload.description,
        SourceType="leads",
        SourceFilterJson=payload.model_dump(mode="json", exclude_none=True),
        Status="ready",
        CreatedBy=principal.email,
    )
    db.add(contact_list)
    db.flush()

    seen: set[str] = set()
    items, total, valid, duplicates = [], 0, 0, 0
    for index, (lead, conversation, customer) in enumerate(query.yield_per(500), start=1):
        phone = decrypt_pii(customer.PhoneEnc)
        email = decrypt_pii(customer.EmailEnc)
        fingerprint = phone_fingerprint(phone)
        total += 1
        duplicate = bool(fingerprint and fingerprint in seen)
        if fingerprint:
            seen.add(fingerprint)
        duplicates += 1 if duplicate else 0
        has_social_identity = customer.Id in social_customer_ids
        usable = (bool(phone or email) or has_social_identity) and not duplicate
        valid += 1 if usable else 0
        items.append(
            LeadContactListItem(
                ClientId=client_id,
                ListId=contact_list.Id,
                RowNumber=index,
                Name=customer.DisplayName or customer.PublicRef,
                PhoneEnc=customer.PhoneEnc,
                EmailEnc=customer.EmailEnc,
                WhatsAppEnc=customer.WhatsAppEnc,
                PhoneHash=fingerprint,
                PhoneMasked=mask_phone(phone),
                CustomerId=customer.Id,
                IsValid=usable,
                InvalidReason=(
                    "Duplicate" if duplicate else (None if usable else "No contact detail")
                ),
                FieldsJson={"product": lead.Product, "interest": lead.Interest,
                            "score": lead.Score, "channel": conversation.Channel},
                CreatedBy=principal.email,
            )
        )
    db.bulk_save_objects(items)
    contact_list.TotalCount, contact_list.ValidCount = total, valid
    contact_list.InvalidCount = total - valid
    contact_list.DuplicateCount = duplicates

    activity.log_principal(
        db, principal, action=A.LIST_CREATED, client_id=client_id,
        entity_type="contact_list", entity_id=contact_list.Id,
        message=f"Built list '{payload.name}' from {total} leads", request=request,
    )
    db.commit()
    return contact_list_out(contact_list)


@lists_router.get("", response_model=list[ContactListOut], summary="List audiences")
def list_lists(
    scope: tuple[Principal, str] = Depends(scoped("campaign.read", "campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    rows = (
        db.query(LeadContactList)
        .filter(
            LeadContactList.ClientId == client_id,
            LeadContactList.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadContactList.CreatedAt.desc())
        .all()
    )
    return [contact_list_out(row) for row in rows]


@lists_router.get("/{list_id}/items", response_model=ContactListItemsOut, summary="Rows in a list")
def list_items(
    list_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    only_invalid: bool = False,
    scope: tuple[Principal, str] = Depends(scoped("campaign.read", "campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    row = db.get(LeadContactList, list_id)
    if row is None or row.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "List not found")
    assert_owns(row.ClientId, client_id)

    query = db.query(LeadContactListItem).filter(
        LeadContactListItem.ListId == list_id,
        LeadContactListItem.IsDeleted == False,  # noqa: E712
    )
    if only_invalid:
        query = query.filter(LeadContactListItem.IsValid == False)  # noqa: E712
    total = query.count()
    items = (
        query.order_by(LeadContactListItem.RowNumber.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return ContactListItemsOut(
        total_items=total, page=page, page_size=page_size,
        items=[contact_list_item_out(i) for i in items],
    )


@lists_router.delete("/{list_id}", response_model=Ok, summary="Delete a list")
def delete_list(
    list_id: str,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    row = db.get(LeadContactList, list_id)
    if row is None or row.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "List not found")
    assert_owns(row.ClientId, client_id)

    in_use = (
        db.query(LeadCampaign)
        .filter(
            LeadCampaign.ListId == list_id,
            LeadCampaign.Status.in_(("running", "queued", "scheduled")),
        )
        .first()
    )
    if in_use is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"'{in_use.Name}' is currently using this list. Stop it first.",
        )

    row.IsDeleted = True
    db.query(LeadContactListItem).filter(LeadContactListItem.ListId == list_id).update(
        {"IsDeleted": True}, synchronize_session=False
    )
    activity.log_principal(
        db, principal, action=A.LIST_DELETED, client_id=client_id,
        entity_type="contact_list", entity_id=list_id,
        message=f"Deleted list '{row.Name}'", request=request,
    )
    db.commit()
    return Ok(message="List deleted")


# =========================================================================== #
# campaigns
# =========================================================================== #
def _campaign(db: Session, campaign_id: str, client_id: str) -> LeadCampaign:
    row = db.get(LeadCampaign, campaign_id)
    if row is None or row.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campaign not found")
    assert_owns(row.ClientId, client_id)
    return row


def _validate(db: Session, client_id: str, payload: CampaignCreate) -> None:
    """Fail loudly at create time rather than silently at send time."""
    if payload.kind == "call":
        if not payload.script_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "A call campaign needs a script_id — that is what the AI will say.",
            )
    else:
        if not payload.message_body and not payload.template_name:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Provide either message_body or template_name.",
            )
        if payload.channel in ("whatsapp", "messenger", "instagram"):
            if not payload.channel_account_id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Select which connected {payload.channel} account to send from.",
                )
            account = db.get(LeadChannelAccount, payload.channel_account_id)
            if account is None or account.IsDeleted or account.ClientId != client_id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel account not found")
            if payload.purpose in ("promotional", "festive", "cold_outreach") and not payload.template_name:
                # Not a hard error: the recipients may all be inside the 24h
                # window. But the operator should know why sends may bounce.
                logger.warning(
                    "[LeadAI campaigns] promotional campaign without a template — "
                    "sends outside the 24h window will be rejected by Meta"
                )

    if payload.audience_type == "list" and not payload.list_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Select a contact list.")
    if payload.list_id:
        contact_list = db.get(LeadContactList, payload.list_id)
        if contact_list is None or contact_list.IsDeleted or contact_list.ClientId != client_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact list not found")


@router.post(
    "",
    response_model=CampaignOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a campaign (draft — nothing is sent yet)",
)
def create_campaign(
    payload: CampaignCreate,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    _validate(db, client_id, payload)

    campaign_timezone = payload.timezone or settings.default_timezone
    scheduled_at = payload.scheduled_at
    if scheduled_at is not None:
        scheduled_at = campaign_runner.local_to_utc(scheduled_at, campaign_timezone)

    row = LeadCampaign(
        ClientId=client_id,
        Name=payload.name,
        Description=payload.description,
        Kind=payload.kind,
        Channel=payload.channel if payload.kind == "message" else "voice",
        ChannelAccountId=payload.channel_account_id,
        Purpose=payload.purpose,
        ListId=payload.list_id,
        AudienceType=payload.audience_type,
        AudienceFilterJson=payload.audience_filter,
        TemplateName=payload.template_name,
        TemplateLanguage=payload.template_language,
        TemplateParamsJson=payload.template_params,
        MessageBody=payload.message_body,
        MediaFileId=payload.media_file_id,
        ScriptId=payload.script_id,
        Language=payload.language,
        Status="draft",
        ScheduledAt=scheduled_at,
        Concurrency=payload.concurrency or settings.campaign_default_concurrency,
        RatePerMinute=payload.rate_per_minute or settings.campaign_default_rate_per_minute,
        MaxRetries=payload.max_retries if payload.max_retries is not None else settings.campaign_max_retries,
        RespectOptOut=payload.respect_opt_out,
        DedupeByPhone=payload.dedupe_by_phone,
        QuietHoursStart=payload.quiet_hours_start,
        QuietHoursEnd=payload.quiet_hours_end,
        TimeZone=campaign_timezone,
        CreatedBy=principal.email,
    )
    db.add(row)
    db.flush()
    activity.log_principal(
        db, principal, action=A.CAMPAIGN_CREATED, client_id=client_id,
        entity_type="campaign", entity_id=row.Id,
        message=f"Created {payload.kind} campaign '{payload.name}'",
        meta={"kind": payload.kind, "channel": row.Channel, "audience": payload.audience_type},
        request=request,
    )
    db.commit()
    return campaign_out(row)


@router.get("", response_model=CampaignListOut, summary="List campaigns")
def list_campaigns(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    kind: str | None = None,
    scope: tuple[Principal, str] = Depends(scoped("campaign.read", "campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    query = db.query(LeadCampaign).filter(
        LeadCampaign.ClientId == client_id,
        LeadCampaign.IsDeleted == False,  # noqa: E712
    )
    if status_filter:
        query = query.filter(LeadCampaign.Status == status_filter)
    if kind:
        query = query.filter(LeadCampaign.Kind == kind)
    total = query.count()
    rows = (
        query.order_by(LeadCampaign.CreatedAt.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return CampaignListOut(
        total_items=total, page=page, page_size=page_size,
        items=[campaign_out(r) for r in rows],
    )


@router.get("/{campaign_id}", response_model=CampaignOut, summary="Campaign detail + live counters")
def get_campaign(
    campaign_id: str,
    scope: tuple[Principal, str] = Depends(scoped("campaign.read", "campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    return campaign_out(_campaign(db, campaign_id, client_id))


@router.patch("/{campaign_id}", response_model=CampaignOut, summary="Edit a draft or paused campaign")
def update_campaign(
    campaign_id: str,
    payload: CampaignUpdate,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    row = _campaign(db, campaign_id, client_id)
    if row.Status == "running":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Pause the campaign before editing it — messages are going out right now.",
        )

    mapping = {
        "name": "Name", "description": "Description", "message_body": "MessageBody",
        "template_name": "TemplateName", "template_language": "TemplateLanguage",
        "template_params": "TemplateParamsJson", "script_id": "ScriptId",
        "scheduled_at": "ScheduledAt", "concurrency": "Concurrency",
        "rate_per_minute": "RatePerMinute", "respect_opt_out": "RespectOptOut",
        "quiet_hours_start": "QuietHoursStart", "quiet_hours_end": "QuietHoursEnd",
        "timezone": "TimeZone",
    }
    data = payload.model_dump(exclude_unset=True)
    for key, column in mapping.items():
        if key in data and data[key] is not None:
            setattr(row, column, data[key])
    if "scheduled_at" in data and data["scheduled_at"] is not None:
        # TimeZone may have just changed above too — convert using the final value.
        row.ScheduledAt = campaign_runner.local_to_utc(row.ScheduledAt, row.TimeZone)
    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()
    activity.log_principal(
        db, principal, action=A.CAMPAIGN_UPDATED, client_id=client_id,
        entity_type="campaign", entity_id=row.Id,
        message=f"Updated campaign '{row.Name}'", meta={"fields": list(data)},
        request=request,
    )
    db.commit()
    return campaign_out(row)


@router.post("/{campaign_id}/build", response_model=CampaignOut, summary="Resolve the audience")
def build_campaign(
    campaign_id: str,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Materialise recipient rows so the operator can see exactly who is targeted."""
    principal, client_id = scope
    row = _campaign(db, campaign_id, client_id)
    if row.Status in campaign_runner.TERMINAL_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Campaign is already {row.Status}.")
    result = campaign_runner.build_audience(db, row, principal.email)
    if result["total"] == 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "The audience resolved to zero contactable people. Check the list or filter.",
        )
    if result["total"] > settings.campaign_max_recipients:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Audience of {result['total']:,} exceeds the limit of "
            f"{settings.campaign_max_recipients:,}. Split it into several campaigns.",
        )
    return campaign_out(row)


@router.get("/{campaign_id}/preview", response_model=CampaignPreviewOut, summary="Dry run")
def preview_campaign(
    campaign_id: str,
    scope: tuple[Principal, str] = Depends(scoped("campaign.read", "campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Rendered sample messages plus a realistic ETA.

    The ETA matters operationally: at the default 60/minute, 30,000 WhatsApp
    messages take 8+ hours, which spans quiet hours. Showing that up front stops
    the "why is it not finished" support ticket.
    """
    _, client_id = scope
    row = _campaign(db, campaign_id, client_id)
    built = (
        db.query(func.count(LeadCampaignRecipient.Id))
        .filter(LeadCampaignRecipient.CampaignId == row.Id)
        .scalar()
        or 0
    )
    samples = (
        db.query(LeadCampaignRecipient)
        .filter(LeadCampaignRecipient.CampaignId == row.Id)
        .limit(3)
        .all()
    )
    rendered = [
        audience.render_template(
            row.MessageBody or f"[template: {row.TemplateName}]",
            {
                "name": r.Name or "there",
                "first_name": (r.Name or "there").split(" ")[0],
                **(r.FieldsJson or {}),
            },
        )
        for r in samples
    ]

    rate = max(1, row.RatePerMinute or 60)
    warnings: list[str] = []
    if built == 0:
        warnings.append("Audience not built yet — call POST /build first.")
    if row.Purpose in ("promotional", "festive", "cold_outreach") and not row.TemplateName:
        warnings.append(
            "No approved template selected. Meta rejects promotional messages to "
            "anyone who has not messaged you in the last 24 hours."
        )
    may_send, resume = campaign_runner.quiet_hours_check(row)
    if not may_send:
        warnings.append(f"Outside quiet hours — sending would start at {resume:%d %b %H:%M} UTC.")

    return CampaignPreviewOut(
        campaign_id=row.Id,
        audience_size=built,
        already_built=built,
        estimated_minutes=round(built / rate, 1),
        sample_messages=rendered,
        warnings=warnings,
    )


@router.post("/{campaign_id}/start", response_model=CampaignOut, summary="Start sending")
def start_campaign(
    campaign_id: str,
    request: Request,
    restart_mode: str = Query(
        default="all",
        description="all | failed_only | pending_only — which recipients this run touches. "
                    "Only matters when restarting a campaign that already ran once.",
    ),
    scope: tuple[Principal, str] = Depends(scoped("campaign.send")),
    db: Session = Depends(get_leadai_db),
):
    """Hand the campaign to the background worker.

    Note the permission: `campaign.send` is separate from `campaign.manage`, so a
    marketing user can build and stage a campaign while only a manager can
    actually pull the trigger on 30,000 messages.
    """
    principal, client_id = scope
    row = _campaign(db, campaign_id, client_id)
    if row.Status == "running":
        raise HTTPException(status.HTTP_409_CONFLICT, "Campaign is already running.")
    # "completed" is deliberately NOT blocked here — restarting a finished
    # campaign (all/failed_only/pending_only) is the entire point of
    # restart_mode. Only an explicit operator stop (cancelled) still blocks a
    # plain Start; "failed" is reserved for a future whole-campaign failure
    # state and is never actually set today, but is included for when it is.
    if row.Status in ("cancelled", "failed"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Campaign is {row.Status}.")
    if restart_mode not in campaign_runner.RESTART_MODES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"restart_mode must be one of {campaign_runner.RESTART_MODES}.",
        )

    from ..services import billing as billing_svc
    allowed, reason = billing_svc.check_channel_access(db, client_id, row.Channel)
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Cannot start campaign: {reason}")

    built = (
        db.query(func.count(LeadCampaignRecipient.Id))
        .filter(LeadCampaignRecipient.CampaignId == row.Id)
        .scalar()
        or 0
    )
    if built == 0:
        campaign_runner.build_audience(db, row, principal.email)
        built = row.TotalCount or 0
    if built == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No recipients to send to.")

    execution = campaign_runner.start_execution(db, row, restart_mode)
    if execution.TotalCount == 0:
        db.rollback()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"No recipients match restart_mode='{restart_mode}'.",
        )

    # /start is the operator pulling the trigger right now — any ScheduledAt was
    # only ever a plan for an automatic fire, and starting manually overrides it.
    row.Status = "queued"
    row.StatusMessage = "Queued — starting shortly"
    row.CompletedAt = None  # stale from a previous run — this one hasn't finished yet
    jobs.enqueue(
        db, "campaign.run", {"campaign_id": row.Id},
        client_id=client_id, run_at=None, priority=3,
    )
    activity.log_principal(
        db, principal, action=A.CAMPAIGN_STARTED, client_id=client_id,
        entity_type="campaign", entity_id=row.Id,
        message=f"Started campaign '{row.Name}' ({restart_mode}) to {execution.TotalCount} recipients",
        meta={
            "recipients": execution.TotalCount, "channel": row.Channel, "kind": row.Kind,
            "execution_id": execution.Id, "restart_mode": restart_mode,
        },
        request=request,
    )
    db.commit()
    return campaign_out(row)


@router.post("/{campaign_id}/pause", response_model=CampaignOut, summary="Pause")
def pause_campaign(
    campaign_id: str,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("campaign.send")),
    db: Session = Depends(get_leadai_db),
):
    """Stop after the in-flight message. Already-sent recipients are untouched;
    the remaining queued rows stay queued and resume where they left off."""
    principal, client_id = scope
    row = _campaign(db, campaign_id, client_id)
    if row.Status not in ("running", "queued", "scheduled"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Campaign is {row.Status}.")
    row.Status = "paused"
    row.StatusMessage = f"Paused by {principal.email}"
    jobs.cancel_kind(db, "campaign.run", row.Id)
    activity.log_principal(
        db, principal, action=A.CAMPAIGN_PAUSED, client_id=client_id,
        entity_type="campaign", entity_id=row.Id,
        message=f"Paused '{row.Name}' at {row.SentCount} sent", request=request,
    )
    db.commit()
    return campaign_out(row)


@router.post("/{campaign_id}/resume", response_model=CampaignOut, summary="Resume")
def resume_campaign(
    campaign_id: str,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("campaign.send")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    row = _campaign(db, campaign_id, client_id)
    if row.Status != "paused":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a paused campaign can resume.")
    row.Status = "queued"
    row.StatusMessage = "Resuming"
    jobs.enqueue(db, "campaign.run", {"campaign_id": row.Id}, client_id=client_id, priority=3)
    activity.log_principal(
        db, principal, action=A.CAMPAIGN_RESUMED, client_id=client_id,
        entity_type="campaign", entity_id=row.Id,
        message=f"Resumed '{row.Name}'", request=request,
    )
    db.commit()
    return campaign_out(row)


@router.post("/{campaign_id}/cancel", response_model=CampaignOut, summary="Cancel permanently")
def cancel_campaign(
    campaign_id: str,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("campaign.send")),
    db: Session = Depends(get_leadai_db),
):
    principal, client_id = scope
    row = _campaign(db, campaign_id, client_id)
    if row.Status in campaign_runner.TERMINAL_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Campaign is already {row.Status}.")
    row.Status = "cancelled"
    row.CompletedAt = utcnow()
    row.StatusMessage = f"Cancelled by {principal.email}"
    jobs.cancel_kind(db, "campaign.run", row.Id)
    db.query(LeadCampaignRecipient).filter(
        LeadCampaignRecipient.CampaignId == row.Id,
        LeadCampaignRecipient.Status == "queued",
    ).update({"Status": "skipped", "FailureReason": "Campaign cancelled"}, synchronize_session=False)
    db.query(LeadCampaignExecution).filter(
        LeadCampaignExecution.CampaignId == row.Id,
        LeadCampaignExecution.Status == "running",
    ).update({"Status": "stopped", "CompletedAt": utcnow()}, synchronize_session=False)
    activity.log_principal(
        db, principal, action=A.CAMPAIGN_CANCELLED, client_id=client_id,
        entity_type="campaign", entity_id=row.Id,
        message=f"Cancelled '{row.Name}'", log_type="Warning", request=request,
    )
    db.commit()
    return campaign_out(row)


@router.get("/{campaign_id}/recipients", response_model=RecipientListOut, summary="Per-recipient results")
def list_recipients(
    campaign_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    status_filter: str | None = Query(default=None, alias="status"),
    scope: tuple[Principal, str] = Depends(scoped("campaign.read", "campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    _campaign(db, campaign_id, client_id)
    query = db.query(LeadCampaignRecipient).filter(
        LeadCampaignRecipient.CampaignId == campaign_id
    )
    if status_filter:
        query = query.filter(LeadCampaignRecipient.Status == status_filter)
    total = query.count()
    rows = (
        query.order_by(LeadCampaignRecipient.CreatedAt.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return RecipientListOut(
        total_items=total, page=page, page_size=page_size,
        items=[recipient_out(r) for r in rows],
    )


@router.get("/{campaign_id}/history", response_model=CampaignHistoryListOut, summary="Campaign run history")
def list_campaign_history(
    campaign_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    scope: tuple[Principal, str] = Depends(scoped("campaign.read", "campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Paginated, newest-first list of run events for a campaign."""
    _, client_id = scope
    _campaign(db, campaign_id, client_id)
    query = (
        db.query(LeadActivityLog)
        .filter(
            LeadActivityLog.ClientId == client_id,
            LeadActivityLog.EntityType == "campaign",
            LeadActivityLog.EntityId == campaign_id,
        )
    )
    total = query.count()
    rows = (
        query.order_by(LeadActivityLog.CreatedAt.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return CampaignHistoryListOut(
        total_items=total,
        page=page,
        page_size=page_size,
        items=[
            CampaignHistoryItemOut(
                id=r.Id,
                action=r.Action,
                message=r.LogMessage,
                meta=r.MetaJson,
                created_at=r.CreatedAt,
                actor_email=r.ActorEmail,
                log_type=r.LogType,
            )
            for r in rows
        ],
    )


@router.get(
    "/{campaign_id}/executions",
    response_model=CampaignExecutionListOut,
    summary="Per-run history — one row per Start/Restart (BatchExecution counterpart)",
)
def list_campaign_executions(
    campaign_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    scope: tuple[Principal, str] = Depends(scoped("campaign.read", "campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    _campaign(db, campaign_id, client_id)
    query = db.query(LeadCampaignExecution).filter(LeadCampaignExecution.CampaignId == campaign_id)
    total = query.count()
    rows = (
        query.order_by(LeadCampaignExecution.StartedAt.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return CampaignExecutionListOut(
        total_items=total, page=page, page_size=page_size,
        items=[campaign_execution_out(r) for r in rows],
    )


def _execution(db: Session, campaign_id: str, execution_id: str) -> LeadCampaignExecution:
    row = db.get(LeadCampaignExecution, execution_id)
    if row is None or row.CampaignId != campaign_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Execution not found")
    return row


@router.get(
    "/{campaign_id}/executions/{execution_id}",
    response_model=CampaignExecutionOut,
    summary="One run's own summary (BatchExecution detail)",
)
def get_campaign_execution(
    campaign_id: str,
    execution_id: str,
    scope: tuple[Principal, str] = Depends(scoped("campaign.read", "campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    _, client_id = scope
    _campaign(db, campaign_id, client_id)
    return campaign_execution_out(_execution(db, campaign_id, execution_id))


@router.get(
    "/{campaign_id}/executions/{execution_id}/attempts",
    response_model=CampaignRecipientAttemptListOut,
    summary="Per-recipient outcomes for ONE run (CallNumberExecution detail)",
)
def list_campaign_execution_attempts(
    campaign_id: str,
    execution_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    status_filter: str | None = Query(default=None, alias="status"),
    scope: tuple[Principal, str] = Depends(scoped("campaign.read", "campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    """What THIS run did to each recipient it touched — frozen at the time of
    the run, so a later retry never rewrites what this listing shows."""
    _, client_id = scope
    _campaign(db, campaign_id, client_id)
    _execution(db, campaign_id, execution_id)

    query = db.query(LeadCampaignRecipientAttempt).filter(
        LeadCampaignRecipientAttempt.CampaignExecutionId == execution_id
    )
    if status_filter:
        query = query.filter(LeadCampaignRecipientAttempt.Status == status_filter)
    total = query.count()
    rows = (
        query.order_by(LeadCampaignRecipientAttempt.CreatedAt.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    recipients = {}
    if rows:
        recipient_ids = {r.RecipientId for r in rows}
        for recipient in (
            db.query(LeadCampaignRecipient).filter(LeadCampaignRecipient.Id.in_(recipient_ids)).all()
        ):
            recipients[recipient.Id] = recipient

    return CampaignRecipientAttemptListOut(
        total_items=total, page=page, page_size=page_size,
        items=[campaign_recipient_attempt_out(r, recipients.get(r.RecipientId)) for r in rows],
    )


@router.get(
    "/{campaign_id}/export",
    summary="Export recipients as CSV — status, lead score, and every data point collected",
)
def export_campaign(
    campaign_id: str,
    request: Request,
    execution_id: str | None = None,
    scope: tuple[Principal, str] = Depends(scoped("campaign.read", "campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    """Always regenerates from current data (a recipient's status/score keeps
    changing after the campaign runs) — this is not a cached download. The
    generated file is still archived each time (leadai_files, Purpose="export"),
    so "what was the output" stays answerable later even if nobody downloads it
    again (see LeadCampaign.OutputFileId / OutputGeneratedAt).
    """
    principal, client_id = scope
    campaign = _campaign(db, campaign_id, client_id)

    attempts_by_recipient: dict[str, LeadCampaignRecipientAttempt] = {}
    if execution_id:
        _execution(db, campaign.Id, execution_id)
        for attempt in (
            db.query(LeadCampaignRecipientAttempt)
            .filter(LeadCampaignRecipientAttempt.CampaignExecutionId == execution_id)
            .all()
        ):
            attempts_by_recipient[attempt.RecipientId] = attempt

    recipients = (
        db.query(LeadCampaignRecipient)
        .filter(LeadCampaignRecipient.CampaignId == campaign.Id)
        .order_by(LeadCampaignRecipient.CreatedAt.asc())
        .all()
    )
    if execution_id:
        # Only recipients this run actually touched, showing what happened on
        # THIS run — not whatever their status has since moved on to.
        recipients = [r for r in recipients if r.Id in attempts_by_recipient]
    data_points = (
        db.query(LeadCompanyDataPoint)
        .filter(LeadCompanyDataPoint.ClientId == client_id, LeadCompanyDataPoint.IsDeleted == False)  # noqa: E712
        .order_by(LeadCompanyDataPoint.DisplayOrder.asc())
        .all()
    )
    conv_ids = [r.ConversationId for r in recipients if r.ConversationId]
    leads_by_conv = {}
    if conv_ids:
        for lead in db.query(Lead).filter(Lead.ConversationId.in_(conv_ids)).all():
            leads_by_conv[lead.ConversationId] = lead

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["Name", "Phone", "Status", "Sent At", "Delivered At", "Read At", "Replied At", "Failure Reason",
         "Lead Score", "Lead Status"] + [dp.Label for dp in data_points]
    )
    for r in recipients:
        # With execution_id set, report what THIS run actually did (the frozen
        # attempt snapshot) rather than the recipient's current, possibly
        # since-overwritten state.
        snapshot = attempts_by_recipient.get(r.Id) if execution_id else None
        lead = leads_by_conv.get(r.ConversationId)
        values = (lead.DataPointsJson or {}) if lead else {}
        writer.writerow(
            [
                r.Name or "", r.PhoneMasked or "", snapshot.Status if snapshot else r.Status,
                (snapshot or r).SentAt.isoformat() if (snapshot or r).SentAt else "",
                (snapshot or r).DeliveredAt.isoformat() if (snapshot or r).DeliveredAt else "",
                (snapshot or r).ReadAt.isoformat() if (snapshot or r).ReadAt else "",
                (snapshot or r).RepliedAt.isoformat() if (snapshot or r).RepliedAt else "",
                (snapshot.FailureReason if snapshot else r.FailureReason) or "",
                lead.Score if lead else "",
                lead.Status if lead else "",
            ]
            + [values.get(dp.Key, "") for dp in data_points]
        )
    csv_bytes = buffer.getvalue().encode("utf-8")
    suffix = f" (run {execution_id[:8]})" if execution_id else ""
    filename = f"{campaign.Name}{suffix}.csv"
    # Content-Disposition is a plain HTTP header (Latin-1 only) — a campaign
    # name with an em dash or any other non-ASCII character would otherwise
    # crash the response at send time. The archived LeadFile keeps the real
    # name; only the header gets the sanitised one.
    ascii_filename = filename.encode("ascii", errors="ignore").decode("ascii").strip() or "campaign-export.csv"

    try:
        stored = objectstore.put_bytes(
            csv_bytes, client_id=client_id, purpose="export", filename=filename, content_type="text/csv",
        )
        file_row = LeadFile(
            ClientId=client_id, Purpose="export", FileName=filename, ContentType="text/csv",
            SizeBytes=stored.size, Bucket=stored.bucket, ObjectKey=stored.key, Checksum=stored.checksum,
            StorageBackend=stored.backend, UploadedByEmail=principal.email,
            LinkedEntityType="campaign", LinkedEntityId=campaign.Id, CreatedBy=principal.email,
        )
        db.add(file_row)
        db.flush()
        if not execution_id:
            # A run-scoped export is a partial slice — only a full export gets
            # to be "the" output file an operator finds via the campaign.
            campaign.OutputFileId = file_row.Id
            campaign.OutputGeneratedAt = utcnow()
        activity.log_principal(
            db, principal, action=A.CAMPAIGN_UPDATED, client_id=client_id,
            entity_type="campaign", entity_id=campaign.Id,
            message=f"Exported {len(recipients)} recipients to CSV" + (f" (run {execution_id})" if execution_id else ""),
            request=request,
        )
        db.commit()
    except objectstore.StorageError as exc:
        logger.warning("[LeadAI campaigns] export not archived: %s", exc)
        db.rollback()

    return StreamingResponse(
        io.BytesIO(csv_bytes), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{ascii_filename}"'},
    )


@router.post("/{campaign_id}/retry-failed", response_model=CampaignOut, summary="Requeue failures")
def retry_failed(
    campaign_id: str,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("campaign.send")),
    db: Session = Depends(get_leadai_db),
):
    """Re-queue only the failed rows, as a new, separately-tracked run
    (RestartMode='failed_only'). Successful sends are never repeated."""
    principal, client_id = scope
    row = _campaign(db, campaign_id, client_id)
    execution = campaign_runner.start_execution(db, row, "failed_only")
    count = execution.TotalCount
    if count:
        row.Status = "queued"
        row.StatusMessage = f"Retrying {count} failed recipients"
        jobs.enqueue(db, "campaign.run", {"campaign_id": row.Id}, client_id=client_id, priority=3)
    else:
        # Nothing to retry — don't leave a zero-work execution stuck "running"
        # forever; it would otherwise be mistaken for the active run later.
        execution.Status = "completed"
        execution.CompletedAt = utcnow()
    activity.log_principal(
        db, principal, action=A.CAMPAIGN_RESUMED, client_id=client_id,
        entity_type="campaign", entity_id=row.Id,
        message=f"Retrying {count} failed recipients",
        meta={"execution_id": execution.Id, "restart_mode": "failed_only", "count": count},
        request=request,
    )
    db.commit()
    return campaign_out(row)
