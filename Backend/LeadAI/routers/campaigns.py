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

import logging
from datetime import datetime

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
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import activity
from ..activity import A
from ..config import settings
from ..db import get_leadai_db
from ..models import (
    LeadCampaign,
    LeadCampaignRecipient,
    LeadChannelAccount,
    LeadContactList,
    LeadContactListItem,
    LeadFile,
    utcnow,
)
from ..rbac import Principal, assert_owns, scoped
from ..schemas import Ok
from ..schemas_ext import (
    CampaignCreate,
    CampaignListOut,
    CampaignOut,
    CampaignPreviewOut,
    CampaignUpdate,
    ContactListFromLeads,
    ContactListItemsOut,
    ContactListOut,
    ContactListPreviewOut,
    RecipientListOut,
)
from ..serializers_ext import (
    campaign_out,
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
    from ..models import Lead, LeadConversation, LeadCustomer
    from ..security import decrypt_pii, encrypt_pii, mask_phone, phone_fingerprint

    principal, client_id = scope
    query = (
        db.query(Lead, LeadConversation, LeadCustomer)
        .join(LeadConversation, LeadConversation.Id == Lead.ConversationId)
        .join(LeadCustomer, LeadCustomer.Id == LeadConversation.CustomerId)
        .filter(Lead.ClientId == client_id, Lead.IsDeleted == False)  # noqa: E712
    )
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
    items, total, valid = [], 0, 0
    for index, (lead, conversation, customer) in enumerate(query.yield_per(500), start=1):
        phone = decrypt_pii(customer.PhoneEnc)
        email = decrypt_pii(customer.EmailEnc)
        fingerprint = phone_fingerprint(phone)
        total += 1
        duplicate = bool(fingerprint and fingerprint in seen)
        if fingerprint:
            seen.add(fingerprint)
        usable = bool(phone or email) and not duplicate
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
        ScheduledAt=payload.scheduled_at,
        Concurrency=payload.concurrency or settings.campaign_default_concurrency,
        RatePerMinute=payload.rate_per_minute or settings.campaign_default_rate_per_minute,
        MaxRetries=payload.max_retries if payload.max_retries is not None else settings.campaign_max_retries,
        RespectOptOut=payload.respect_opt_out,
        DedupeByPhone=payload.dedupe_by_phone,
        QuietHoursStart=payload.quiet_hours_start,
        QuietHoursEnd=payload.quiet_hours_end,
        TimeZone=payload.timezone or settings.default_timezone,
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
    if row.Status in campaign_runner.TERMINAL_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Campaign is {row.Status}.")

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

    run_at = row.ScheduledAt if row.ScheduledAt and row.ScheduledAt > datetime.utcnow() else None
    row.Status = "scheduled" if run_at else "queued"
    row.StatusMessage = (
        f"Scheduled for {run_at:%d %b %H:%M}" if run_at else "Queued — starting shortly"
    )
    jobs.enqueue(
        db, "campaign.run", {"campaign_id": row.Id},
        client_id=client_id, run_at=run_at, priority=3,
    )
    activity.log_principal(
        db, principal, action=A.CAMPAIGN_STARTED, client_id=client_id,
        entity_type="campaign", entity_id=row.Id,
        message=f"Started campaign '{row.Name}' to {built} recipients",
        meta={"recipients": built, "channel": row.Channel, "kind": row.Kind},
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


@router.post("/{campaign_id}/retry-failed", response_model=CampaignOut, summary="Requeue failures")
def retry_failed(
    campaign_id: str,
    request: Request,
    scope: tuple[Principal, str] = Depends(scoped("campaign.send")),
    db: Session = Depends(get_leadai_db),
):
    """Re-queue only the failed rows. Successful sends are never repeated."""
    principal, client_id = scope
    row = _campaign(db, campaign_id, client_id)
    count = (
        db.query(LeadCampaignRecipient)
        .filter(
            LeadCampaignRecipient.CampaignId == row.Id,
            LeadCampaignRecipient.Status == "failed",
        )
        .update(
            {"Status": "queued", "FailureReason": None, "Attempts": 0},
            synchronize_session=False,
        )
    )
    if count:
        row.Status = "queued"
        row.StatusMessage = f"Retrying {count} failed recipients"
        jobs.enqueue(db, "campaign.run", {"campaign_id": row.Id}, client_id=client_id, priority=3)
    activity.log_principal(
        db, principal, action=A.CAMPAIGN_RESUMED, client_id=client_id,
        entity_type="campaign", entity_id=row.Id,
        message=f"Retrying {count} failed recipients", request=request,
    )
    db.commit()
    return campaign_out(row)
