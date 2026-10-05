"""Bulk lead import, auto-classified into one draft campaign per product.

THE FLOW
--------
1. GET  /leads/import/schema  -> the columns a company can fill in: the fixed
   set (name/phone/email/whatsapp/product) plus whatever data points the
   company has defined (see routers/data_points.py). instagram_id/facebook_id
   were deliberately dropped: a cold import can never carry a real IGSID/PSID
   (Meta only hands that over once the person has already messaged the
   connected account), so the column only ever produced a value the Send API
   rejects — better to not offer it than let it fail silently at send time.
   The frontend renders this as a template/instructions so an uploaded file
   follows the expected format.
2. POST /leads/import          -> parses the file with the SAME engine
   contact-list uploads already use (services/audience.py — fuzzy column
   matching, phone normalisation, dedup), creates a real LeadCustomer/
   LeadConversation/Lead per valid row, then groups the new leads by Product
   (matched against the company's LeadProduct catalog exactly like
   ai_engine.qualify() does) into ONE DRAFT LeadCampaign per product, plus one
   "Unknown Product" campaign for anything that didn't match. Draft, never
   auto-started — an operator still presses Start, with whatever concurrency/
   retry mode they choose, exactly like any other campaign.

Channel + call escalation: a company can ask for leads to be worked over chat,
over voice, or both. "Both" means a chat campaign where, once the AI judges a
call would help, it ASKS for consent first — never dials without an explicit
yes from the customer (see LeadConversation.CallConsentStatus and the
handling in services/conversation_flow.py).
"""
from __future__ import annotations

import logging
import random
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from .. import activity
from ..activity import A
from ..db import get_leadai_db
from ..models import (
    Lead,
    LeadCompanyDataPoint,
    LeadContactList,
    LeadContactListItem,
    LeadConversation,
    LeadCustomer,
    LeadProduct,
    utcnow,
)
from ..models_ext import LeadCampaign, LeadFile
from ..rbac import Principal, require, resolve_scope
from ..schemas_ext import LeadImportBatchOut, LeadImportResultOut, LeadImportRowError, LeadImportSchemaField, LeadImportSchemaOut
from ..security import encrypt_pii, phone_fingerprint
from ..services import ai_engine, audience, objectstore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leads", tags=["LeadAI • Lead import & batching"])

# The fixed baseline every import can use, regardless of what the company has
# defined as its own data points. "At least one contact method" is enforced
# by services/audience.parse_contacts(), not per-field here.
FIXED_SCHEMA_FIELDS: list[dict] = [
    {"key": "name", "label": "Name", "data_type": "text", "required": False},
    {"key": "phone", "label": "Phone Number", "data_type": "text", "required": False},
    {"key": "email", "label": "Email", "data_type": "email", "required": False},
    {"key": "whatsapp", "label": "WhatsApp Number", "data_type": "text", "required": False},
    {"key": "product", "label": "Product", "data_type": "text", "required": False},
]
# instagram_id/facebook_id were dropped from the template: a cold import can
# never carry a real IGSID/PSID. Meta only hands that id over once the person
# has already messaged the connected account — a value filled in by hand is
# always either a username (which the Send API rejects) or a placeholder,
# and the row previously failed silently at send time instead of at import.


def _company_data_points(db: Session, client_id: str) -> list[LeadCompanyDataPoint]:
    return (
        db.query(LeadCompanyDataPoint)
        .filter(
            LeadCompanyDataPoint.ClientId == client_id,
            LeadCompanyDataPoint.IsActive == True,  # noqa: E712
            LeadCompanyDataPoint.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadCompanyDataPoint.DisplayOrder.asc())
        .all()
    )


@router.get(
    "/import/schema",
    response_model=LeadImportSchemaOut,
    summary="Expected columns for a lead import file",
)
def import_schema(
    principal: Principal = Depends(require("campaign.read", "campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    fields = [LeadImportSchemaField(**f, source="fixed") for f in FIXED_SCHEMA_FIELDS]
    for dp in _company_data_points(db, client_id):
        fields.append(
            LeadImportSchemaField(
                key=dp.Key, label=dp.Label, data_type=dp.DataType, required=bool(dp.Required),
                source="data_point",
            )
        )
    return LeadImportSchemaOut(fields=fields, sample_csv_header=",".join(f.label for f in fields))


def _classify_product(raw: str | None, catalog_names: list[str]) -> str:
    """Unlike ai_engine._snap_to_catalog (which trusts a freeform guess AS-IS
    when the company has no catalog at all, to keep qualify() backward
    compatible for companies that haven't adopted Products yet), batching has
    no freeform fallback: grouping only means something against the company's
    OWN catalog. No catalog, or no match in it, both mean "unknown" — never a
    batch per arbitrary spreadsheet string.
    """
    if not raw or not catalog_names:
        return "unknown"
    return ai_engine._snap_to_catalog(raw, catalog_names)


@router.post(
    "/import",
    response_model=LeadImportResultOut,
    status_code=status.HTTP_201_CREATED,
    summary="Import leads from a file, auto-classified into one draft batch per product",
)
async def import_leads(
    request: Request,
    file: UploadFile = File(..., description="CSV/XLSX/XLS/DOCX — see GET /leads/import/schema"),
    channel: str = Form(..., pattern="^(chat|call|both)$"),
    chat_channel: str | None = Form(None, pattern="^(whatsapp|instagram|messenger)$"),
    chat_channel_account_id: str | None = Form(None),
    voice_script_id: str | None = Form(None),
    call_escalation: bool = Form(False, description="Only meaningful when channel='both': ask consent before calling"),
    principal: Principal = Depends(require("campaign.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)

    if channel in ("chat", "both") and not chat_channel:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "chat_channel is required when channel includes chat.")
    if channel in ("chat", "both") and not chat_channel_account_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "chat_channel_account_id is required when channel includes chat.")
    if call_escalation and channel != "both":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "call_escalation only applies when channel is 'both'.")

    blob = await file.read()
    if not blob:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The uploaded file is empty.")

    result = audience.parse_contacts(file.filename or "import.csv", file.content_type or "", blob)
    if not result.rows:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "; ".join(result.warnings) or "No rows could be read from this file.")

    # instagram_id/facebook_id are no longer part of the import schema (see
    # FIXED_SCHEMA_FIELDS) — a cold import can never carry a real IGSID/PSID,
    # only a username/placeholder that Meta's Send API rejects at send time.
    # Any such column in an old-template file a caller still has lying around
    # is simply ignored now, rather than recording a bogus identity from it.

    # Archive the original upload so "what was actually imported" is answerable
    # later, same as a contact-list upload already does. Best-effort — storage
    # failing must not lose the import itself.
    source_file_id: str | None = None
    try:
        stored = objectstore.put_bytes(
            blob, client_id=client_id, purpose="campaign_list",
            filename=file.filename or "import", content_type=file.content_type or "application/octet-stream",
        )
        file_row = LeadFile(
            ClientId=client_id, Purpose="campaign_list", FileName=file.filename or "import",
            ContentType=file.content_type or "application/octet-stream", SizeBytes=stored.size,
            Bucket=stored.bucket, ObjectKey=stored.key, Checksum=stored.checksum,
            StorageBackend=stored.backend, UploadedByEmail=principal.email,
            LinkedEntityType="lead_import", CreatedBy=principal.email,
        )
        db.add(file_row)
        db.flush()
        source_file_id = file_row.Id
    except objectstore.StorageError as exc:
        logger.warning("[LeadAI leads] import source file not archived: %s", exc)

    data_points = _company_data_points(db, client_id)
    dp_by_label = {dp.Label.strip().lower(): dp for dp in data_points}
    products = db.query(LeadProduct).filter(
        LeadProduct.ClientId == client_id, LeadProduct.IsDeleted == False,  # noqa: E712
    ).all()
    product_id_by_name = {p.ProductName: p.Id for p in products}
    catalog_names = list(product_id_by_name)

    primary_channel = "voice" if channel == "call" else chat_channel

    invalid_rows: list[LeadImportRowError] = []
    # product name -> list[(lead, customer)]
    groups: dict[str, list[tuple[Lead, LeadCustomer]]] = defaultdict(list)

    for row in result.rows:
        if not row.is_valid:
            invalid_rows.append(LeadImportRowError(row_number=row.row_number, reason=row.invalid_reason or "invalid"))
            continue

        customer = None
        fingerprint = phone_fingerprint(row.phone) if row.phone else None
        if fingerprint:
            customer = (
                db.query(LeadCustomer)
                .filter(LeadCustomer.ClientId == client_id, LeadCustomer.PhoneHash == fingerprint,
                        LeadCustomer.IsDeleted == False)  # noqa: E712
                .first()
            )
        if customer is None:
            # PublicRef is the masked placeholder the inbox shows BEFORE an
            # operator explicitly reveals PII (see ConversationOut's own
            # docstring: "the name is released only by the audited
            # /inbox/{id}/contact call"). It must never be the real name —
            # every other customer-creation path in this codebase uses a
            # random placeholder for exactly this reason; this one didn't,
            # so an imported lead's real name was showing in the inbox with
            # no reveal step at all.
            customer = LeadCustomer(
                ClientId=client_id, PublicRef=f"Lead #{random.randint(10000, 99999)}",
                DisplayName=row.name, PhoneEnc=encrypt_pii(row.phone), PhoneHash=fingerprint,
                EmailEnc=encrypt_pii(row.email), WhatsAppEnc=encrypt_pii(row.whatsapp),
                CreatedBy=principal.email,
            )
            db.add(customer)
            db.flush()
        elif row.name and not customer.DisplayName:
            customer.DisplayName = row.name

        # Re-importing the same file (a frontend error + retry is exactly how
        # this was found) must not fork a second conversation and a second
        # Lead for someone already imported — same customer, same channel,
        # still open: continue THAT thread instead of duplicating it.
        conversation = (
            db.query(LeadConversation)
            .filter(
                LeadConversation.ClientId == client_id, LeadConversation.CustomerId == customer.Id,
                LeadConversation.Channel == primary_channel, LeadConversation.Status == "open",
                LeadConversation.IsDeleted == False,  # noqa: E712
            )
            .order_by(LeadConversation.CreatedAt.desc())
            .first()
        )
        existing_lead = None
        if conversation is None:
            conversation = LeadConversation(
                ClientId=client_id, CustomerId=customer.Id, Channel=primary_channel,
                Status="open", CreatedBy=principal.email,
            )
            db.add(conversation)
            db.flush()
        else:
            existing_lead = (
                db.query(Lead)
                .filter(Lead.ConversationId == conversation.Id, Lead.IsDeleted == False)  # noqa: E712
                .first()
            )

        product_name = _classify_product(row.product, catalog_names)

        data_point_values: dict = {}
        for header, value in (row.fields or {}).items():
            dp = dp_by_label.get(str(header).strip().lower())
            if dp is None:
                continue
            validated = ai_engine._validate_data_point_value(value, dp)
            if validated is not None:
                data_point_values[dp.Key] = validated

        if existing_lead is not None:
            lead = existing_lead
            lead.Product = product_name
            if data_point_values:
                lead.DataPointsJson = {**(lead.DataPointsJson or {}), **data_point_values}
        else:
            lead = Lead(
                ClientId=client_id, ConversationId=conversation.Id,
                Product=product_name, DataPointsJson=data_point_values or None,
                CreatedBy=principal.email,
            )
            db.add(lead)
            db.flush()
        groups[product_name].append((lead, customer))

    db.commit()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    batches_out: list[LeadImportBatchOut] = []
    for product_name, pairs in groups.items():
        label = "Unknown Product" if product_name == "unknown" else product_name
        batch_name = f"{label} — Import {today}"

        contact_list = LeadContactList(
            ClientId=client_id, Name=batch_name, SourceType="leads", Status="ready",
            SourceFileId=source_file_id, TotalCount=len(pairs), ValidCount=len(pairs),
            CreatedBy=principal.email,
        )
        db.add(contact_list)
        db.flush()
        for index, (lead, customer) in enumerate(pairs, start=1):
            db.add(
                LeadContactListItem(
                    ClientId=client_id, ListId=contact_list.Id, RowNumber=index,
                    Name=customer.DisplayName or customer.PublicRef,
                    PhoneEnc=customer.PhoneEnc, PhoneHash=customer.PhoneHash,
                    CustomerId=customer.Id, IsValid=True, CreatedBy=principal.email,
                )
            )

        campaign = LeadCampaign(
            ClientId=client_id, Name=batch_name,
            Kind="call" if primary_channel == "voice" else "message",
            Channel=primary_channel,
            ChannelAccountId=None if primary_channel == "voice" else chat_channel_account_id,
            AudienceType="list", ListId=contact_list.Id,
            Status="draft", ScriptId=voice_script_id,
            ProductId=product_id_by_name.get(product_name),
            CreatedVia="import",
            CallEscalationEnabled=bool(call_escalation and channel == "both"),
            CreatedBy=principal.email,
        )
        db.add(campaign)
        db.flush()

        activity.log_principal(
            db, principal, action=A.CAMPAIGN_CREATED, client_id=client_id,
            entity_type="campaign", entity_id=campaign.Id,
            message=f"Auto-classified from import: '{batch_name}' ({len(pairs)} leads)",
            meta={"product": product_name, "lead_count": len(pairs), "created_via": "import"},
            request=request,
        )
        batches_out.append(
            LeadImportBatchOut(campaign_id=campaign.Id, name=batch_name, product=label, lead_count=len(pairs))
        )

    db.commit()

    return LeadImportResultOut(
        total=result.total, valid=result.valid, invalid=len(invalid_rows), duplicates=result.duplicates,
        invalid_rows=invalid_rows, batches=batches_out,
    )
