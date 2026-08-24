"""
Request/response contracts for the Phase-2 modules.

Same convention as schemas.py: snake_case on the wire, PascalCase in the ORM,
translated in one place. Kept in a separate file purely so schemas.py stays
readable — both are imported the same way.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_serializer

ChannelName = Literal["web", "whatsapp", "messenger", "instagram", "sms", "email", "voice"]
CampaignKind = Literal["message", "call"]
CampaignPurpose = Literal[
    "promotional", "festive", "cold_outreach", "follow_up", "reactivation", "transactional"
]
AudienceType = Literal["list", "leads", "customers"]


# =========================================================================== #
# channels
# =========================================================================== #
class ChannelAccountCreate(BaseModel):
    channel: Literal["whatsapp", "messenger", "instagram"]
    name: str = Field(min_length=1, max_length=160)
    external_id: str = Field(
        min_length=1,
        max_length=120,
        description=(
            "WhatsApp: phone_number_id. Messenger: Facebook Page id. "
            "Instagram: IG professional account id. This is how inbound "
            "webhooks are routed to your company."
        ),
    )
    access_token: str = Field(
        min_length=10,
        description="Permanent system-user or page access token. Stored encrypted.",
    )
    app_secret: str | None = Field(
        default=None, description="Used to verify X-Hub-Signature-256 on webhooks."
    )
    verify_token: str | None = Field(
        default=None, description="Any string; paste the same value into Meta's webhook setup."
    )
    business_account_id: str | None = None
    display_number: str | None = None
    api_version: str = "v21.0"
    auto_reply: bool = True
    script_id: str | None = None
    default_language: str | None = None


class ChannelAccountUpdate(BaseModel):
    name: str | None = None
    access_token: str | None = None
    app_secret: str | None = None
    verify_token: str | None = None
    display_number: str | None = None
    api_version: str | None = None
    is_active: bool | None = None
    auto_reply: bool | None = None
    script_id: str | None = None
    default_language: str | None = None


class ChannelAccountOut(BaseModel):
    id: str
    client_id: str
    channel: str
    provider: str
    name: str
    external_id: str
    display_number: str | None = None
    business_account_id: str | None = None
    api_version: str
    is_active: bool
    auto_reply: bool
    script_id: str | None = None
    default_language: str | None = None
    # Secrets are NEVER returned; only whether they are set.
    has_access_token: bool = False
    has_app_secret: bool = False
    verify_token: str | None = None
    webhook_url: str | None = None
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    created_at: datetime | None = None

    @field_serializer('last_inbound_at', 'last_outbound_at', 'last_error_at', 'created_at')
    def serialize_dates(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class ChannelTestSend(BaseModel):
    to: str = Field(description="E.164 number (WhatsApp) or PSID (Messenger/Instagram)")
    message: str = Field(min_length=1, max_length=1000)
    template_name: str | None = None
    template_language: str = "en"
    template_params: list[str] = []


class ChannelStatusOut(BaseModel):
    social_enabled: bool
    web_chat_enabled: bool
    graph_version: str
    signature_verification: bool
    platform_token_configured: bool
    sms: bool
    email: bool
    dry_run: bool
    accounts: list[ChannelAccountOut] = []


# =========================================================================== #
# contact lists
# =========================================================================== #
class ContactListOut(BaseModel):
    id: str
    client_id: str
    name: str
    description: str | None = None
    source_type: str
    source_file_id: str | None = None
    total_count: int = 0
    valid_count: int = 0
    invalid_count: int = 0
    duplicate_count: int = 0
    column_map: dict[str, str] | None = None
    status: str
    status_message: str | None = None
    tags: str | None = None
    created_at: datetime | None = None
    created_by: str | None = None

    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class ContactListItemOut(BaseModel):
    id: str
    row_number: int
    name: str | None = None
    phone_masked: str | None = None
    country_code: str | None = None
    is_valid: bool
    invalid_reason: str | None = None
    fields: dict[str, Any] | None = None


class ContactListItemsOut(BaseModel):
    total_items: int
    page: int
    page_size: int
    items: list[ContactListItemOut] = []


class ContactListPreviewOut(BaseModel):
    """Returned by the dry-run parse, BEFORE anything is saved.

    The whole point: the operator sees the damage report — how many rows are
    unusable and why — while they can still fix the spreadsheet.
    """

    headers: list[str] = []
    column_map: dict[str, str] = {}
    total: int = 0
    valid: int = 0
    invalid: int = 0
    duplicates: int = 0
    warnings: list[str] = []
    sample: list[dict] = []


class ContactListFromLeads(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    status: list[str] | None = Field(default=None, description="cold|warm|hot|qualified|lost")
    min_score: int | None = Field(default=None, ge=0, le=100)
    above_threshold: bool = False
    channel: ChannelName | None = None
    created_after: datetime | None = None


# =========================================================================== #
# campaigns
# =========================================================================== #
class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    kind: CampaignKind = "message"
    channel: ChannelName = "whatsapp"
    channel_account_id: str | None = None
    purpose: CampaignPurpose = "promotional"

    audience_type: AudienceType = "list"
    list_id: str | None = None
    audience_filter: dict[str, Any] | None = None

    template_name: str | None = Field(
        default=None,
        description="Meta-approved template. Required for promotional sends "
                    "outside the 24-hour customer service window.",
    )
    template_language: str | None = "en"
    template_params: list[str] | None = None
    message_body: str | None = Field(
        default=None,
        max_length=4000,
        description="Supports {{name}}, {{first_name}}, {{company}} and any "
                    "extra column from the uploaded list.",
    )
    media_file_id: str | None = None
    script_id: str | None = Field(default=None, description="Required when kind == call")
    language: str | None = None

    scheduled_at: datetime | None = None
    concurrency: int | None = Field(default=None, ge=1, le=50)
    rate_per_minute: int | None = Field(default=None, ge=1, le=600)
    max_retries: int | None = Field(default=None, ge=0, le=5)
    respect_opt_out: bool = True
    dedupe_by_phone: bool = True
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23)
    timezone: str | None = None


class CampaignUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    message_body: str | None = None
    template_name: str | None = None
    template_language: str | None = None
    template_params: list[str] | None = None
    script_id: str | None = None
    scheduled_at: datetime | None = None
    concurrency: int | None = Field(default=None, ge=1, le=50)
    rate_per_minute: int | None = Field(default=None, ge=1, le=600)
    respect_opt_out: bool | None = None
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23)
    timezone: str | None = None


class CampaignOut(BaseModel):
    id: str
    client_id: str
    name: str
    description: str | None = None
    kind: str
    channel: str
    channel_account_id: str | None = None
    purpose: str
    audience_type: str
    list_id: str | None = None
    audience_filter: dict[str, Any] | None = None
    template_name: str | None = None
    template_language: str | None = None
    message_body: str | None = None
    script_id: str | None = None
    language: str | None = None
    status: str
    status_message: str | None = None
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    concurrency: int = 5
    rate_per_minute: int = 60
    respect_opt_out: bool = True
    quiet_hours_start: int | None = None
    quiet_hours_end: int | None = None
    timezone: str | None = None
    total_count: int = 0
    queued_count: int = 0
    sent_count: int = 0
    delivered_count: int = 0
    read_count: int = 0
    replied_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    leads_created: int = 0
    created_at: datetime | None = None
    created_by: str | None = None

    @field_serializer('scheduled_at', 'started_at', 'completed_at', 'created_at')
    def serialize_dates(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class CampaignListOut(BaseModel):
    total_items: int
    page: int
    page_size: int
    items: list[CampaignOut] = []


class RecipientOut(BaseModel):
    id: str
    name: str | None = None
    phone_masked: str | None = None
    status: str
    attempts: int = 0
    external_message_id: str | None = None
    rendered_body: str | None = None
    conversation_id: str | None = None
    call_id: str | None = None
    sent_at: datetime | None = None
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    replied_at: datetime | None = None
    failure_reason: str | None = None

    @field_serializer('sent_at', 'delivered_at', 'read_at', 'replied_at')
    def serialize_dates(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class RecipientListOut(BaseModel):
    total_items: int
    page: int
    page_size: int
    items: list[RecipientOut] = []


class CampaignPreviewOut(BaseModel):
    """What a dry run of the campaign would do — shown before Start."""

    campaign_id: str
    audience_size: int
    already_built: int
    estimated_minutes: float
    sample_messages: list[str] = []
    warnings: list[str] = []


# =========================================================================== #
# customers (CRM)
# =========================================================================== #
class AccountCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=160)
    phone: str | None = None
    email: EmailStr | None = None
    whatsapp: str | None = None
    company_name: str | None = None
    stage: str = "customer"
    owner_email: EmailStr | None = None
    product: str | None = None
    value: float = 0.0
    currency: str = "INR"
    source: str | None = None
    tags: str | None = None
    notes: str | None = None
    birthday: datetime | None = None
    anniversary: datetime | None = None
    fields: dict[str, Any] | None = None


class AccountUpdate(BaseModel):
    display_name: str | None = None
    company_name: str | None = None
    stage: str | None = None
    status: str | None = None
    owner_email: EmailStr | None = None
    product: str | None = None
    value: float | None = None
    tags: str | None = None
    notes: str | None = None
    next_follow_up_at: datetime | None = None
    birthday: datetime | None = None
    anniversary: datetime | None = None
    opt_in_whatsapp: bool | None = None
    opt_in_sms: bool | None = None
    opt_in_email: bool | None = None
    opt_in_call: bool | None = None
    do_not_disturb: bool | None = None
    fields: dict[str, Any] | None = None


class AccountOut(BaseModel):
    id: str
    client_id: str
    display_name: str
    company_name: str | None = None
    phone_masked: str | None = None
    email_masked: str | None = None
    stage: str
    status: str
    owner_email: str | None = None
    product: str | None = None
    value: float = 0.0
    currency: str = "INR"
    source: str | None = None
    tags: str | None = None
    notes: str | None = None
    opt_in_whatsapp: bool = True
    opt_in_sms: bool = True
    opt_in_email: bool = True
    opt_in_call: bool = True
    do_not_disturb: bool = False
    converted_at: datetime | None = None
    last_contacted_at: datetime | None = None
    next_follow_up_at: datetime | None = None
    birthday: datetime | None = None
    anniversary: datetime | None = None
    source_conversation_id: str | None = None
    fields: dict[str, Any] | None = None
    created_at: datetime | None = None

    @field_serializer('converted_at', 'last_contacted_at', 'next_follow_up_at', 'birthday', 'anniversary', 'created_at')
    def serialize_dates(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class AccountListOut(BaseModel):
    total_items: int
    page: int
    page_size: int
    items: list[AccountOut] = []


class AccountNoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    note_type: str = "note"


class AccountNoteOut(BaseModel):
    id: str
    note_type: str
    body: str
    author_email: str | None = None
    meta: dict | None = None
    created_at: datetime | None = None

    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class ConvertLeadRequest(BaseModel):
    conversation_id: str
    owner_email: EmailStr | None = None
    stage: str = "customer"
    value: float | None = None
    notes: str | None = None


class QuickMessageRequest(BaseModel):
    """Send one customer a single message — the 'say happy Diwali to this one
    person' path, without building a campaign."""

    channel: ChannelName = "whatsapp"
    message: str | None = Field(default=None, max_length=2000)
    template_name: str | None = None
    template_language: str = "en"
    template_params: list[str] = []
    channel_account_id: str | None = None


class OccasionOut(BaseModel):
    account_id: str
    name: str
    occasion: str
    date: str
    in_days: int


# =========================================================================== #
# files
# =========================================================================== #
class FileOut(BaseModel):
    id: str
    client_id: str
    purpose: str
    file_name: str
    content_type: str
    size_bytes: int
    bucket: str
    object_key: str
    storage_backend: str
    download_url: str | None = None
    uploaded_by: str | None = None
    linked_entity_type: str | None = None
    linked_entity_id: str | None = None
    created_at: datetime | None = None

    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class FileListOut(BaseModel):
    total_items: int
    page: int
    page_size: int
    items: list[FileOut] = []


class StorageHealthOut(BaseModel):
    backend: str
    endpoint: str | None = None
    buckets: list[str] = []
    ok: bool = True
    error: str | None = None
    path: str | None = None


# =========================================================================== #
# threshold / dashboard
# =========================================================================== #
class ThresholdSettingsIn(BaseModel):
    lead_score_threshold: int | None = Field(
        default=None, ge=0, le=100,
        description="Score at which a lead appears on the sales dashboard.",
    )
    auto_convert_threshold: int | None = Field(
        default=None, ge=0, le=100,
        description="Score at which a lead is auto-promoted to a customer. 0 disables.",
    )
    notify_on_threshold: bool | None = None
    hide_below_threshold: bool | None = None


class ThresholdSettingsOut(ThresholdSettingsIn):
    client_id: str
    effective_lead_score_threshold: int
    effective_auto_convert_threshold: int
    leads_above_threshold: int = 0
    leads_below_threshold: int = 0


class JobStatsOut(BaseModel):
    worker: str
    enabled: bool
    queue: dict[str, int] = {}
