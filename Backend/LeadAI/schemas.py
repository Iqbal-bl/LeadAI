"""
Request/response contracts.

Naming note: the outbound ORM uses PascalCase columns, but the API speaks
snake_case because that is what the Angular client already consumes elsewhere.
The translation happens in serializers.py, deliberately in one place.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_serializer

# ===========================================================================
# generic
# ===========================================================================


class Ok(BaseModel):
    success: bool = True
    message: str = "OK"


class Paged(BaseModel):
    total_items: int
    page: int
    page_size: int


# ===========================================================================
# companies (tenants) — backed by the existing `Clients` table
# ===========================================================================


class CompanyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: str | None = Field(default=None, max_length=100)
    phone_number: str | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=500)
    # Optionally grant the first company admin in the same call.
    admin_email: EmailStr | None = None
    admin_name: str | None = None
    # Services/features to enable for the company (e.g. ["instagram", "facebook", "linkedin", "voice_agent"])
    services: list[str] | None = Field(default=None, description="Features/services to enable. If omitted or null, features default to unenabled/disabled.")



class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    email: str | None = None
    phone_number: str | None = None
    description: str | None = None
    is_active: bool | None = None


class CompanyUserOut(BaseModel):
    """One person with access to a company."""

    id: str
    user_id: str | None = None      # identity-server id, when we captured it
    email: str
    name: str | None = None
    role: str
    is_active: bool = True
    # True when the grant is global (ClientId NULL): a platform admin who can act
    # on this company without being a member of it. Worth showing distinctly, so
    # a company admin is not surprised by who appears in their list.
    is_global: bool = False
    created_at: datetime | None = None

    @field_serializer("created_at")
    def serialize_created_at(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class CompanyUsersOut(BaseModel):
    """The company's people, grouped for a details screen and also flat."""

    company_id: str
    company_name: str
    total: int
    admins: list[CompanyUserOut] = []
    managers: list[CompanyUserOut] = []
    employees: list[CompanyUserOut] = []
    users: list[CompanyUserOut] = []


class CompanyOut(BaseModel):
    id: str
    name: str
    email: str | None = None
    phone_number: str | None = None
    description: str | None = None
    is_active: bool
    created_at: datetime | None = None
    # rollups
    user_count: int = 0
    document_count: int = 0
    chunk_count: int = 0
    script_count: int = 0
    conversation_count: int = 0

    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class CompanySettingsIn(BaseModel):
    handoff_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    retrieval_top_k: int | None = Field(default=None, ge=1, le=20)
    default_language: str | None = None
    auto_assign_enabled: bool | None = None
    auto_call_on_hot_lead: bool | None = None
    widget_enabled: bool | None = None
    widget_greeting: str | None = Field(default=None, max_length=500)


class CompanySettingsOut(CompanySettingsIn):
    client_id: str
    effective_handoff_threshold: float
    effective_retrieval_top_k: int


class ServiceItemOut(BaseModel):
    key: str
    is_enabled: bool


class CompanyServicesOut(BaseModel):
    company_id: str
    company_name: str
    services: list[ServiceItemOut]


class ServiceStatusPatch(BaseModel):
    key: str
    is_enabled: bool


class CompanyServicesPatchIn(BaseModel):
    services: list[ServiceStatusPatch]




# ===========================================================================
# RBAC
# ===========================================================================

RoleName = Literal["Admin", "company_admin", "manager", "employee"]


class RoleGrant(BaseModel):
    user_email: EmailStr
    role: RoleName
    full_name: str | None = None
    # Ignored for Admin (which is always global); otherwise defaults to
    # the caller's resolved company.
    client_id: str | None = None


class RoleUpdate(BaseModel):
    role: RoleName | None = None
    full_name: str | None = None
    is_active: bool | None = None


class RoleOut(BaseModel):
    id: str
    user_email: str
    full_name: str | None = None
    role: str
    client_id: str | None = None
    client_name: str | None = None
    is_active: bool
    created_at: datetime | None = None

    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class MeOut(BaseModel):
    email: str
    full_name: str | None = None
    role: str
    client_id: str | None = None
    client_name: str | None = None
    permissions: list[str]
    accessible_companies: list[CompanyOut] = []


class PermissionCatalogOut(BaseModel):
    permissions: dict[str, str]
    role_permissions: dict[str, list[str]]


class RolePermissionOut(BaseModel):
    role: str
    permission_key: str
    description: str | None = None
    is_granted: bool
    is_default: bool


class RolePermissionsOut(BaseModel):
    role: str
    permissions: list[RolePermissionOut]
    effective_permissions: list[str]


class RolePermissionUpdate(BaseModel):
    permission_key: str
    is_granted: bool


class RolePermissionsBulkUpdate(BaseModel):
    permissions: list[RolePermissionUpdate]


# ===========================================================================
# knowledge base
# ===========================================================================


class DocumentOut(BaseModel):
    id: str
    title: str
    file_name: str | None = None
    content_type: str
    source_type: str
    status: str
    status_message: str | None = None
    chunk_count: int
    char_count: int
    embedding_model: str | None = None
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


class FaqCreate(BaseModel):
    title: str = Field(default="FAQ", max_length=255)
    content: str = Field(min_length=10)
    tags: str | None = None


class TextCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=10)
    tags: str | None = None


class RetrievalTest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    top_k: int | None = Field(default=None, ge=1, le=20)


class SourceOut(BaseModel):
    chunk_id: str | None = None
    document_id: str | None = None
    score: float
    excerpt: str


class RetrievalTestOut(BaseModel):
    company: str
    query: str
    answer: str
    confidence: float
    needs_human: bool
    handoff_reason: str | None = None
    model: str | None = None
    latency_ms: int = 0
    sources: list[SourceOut] = []


class KbStatsOut(BaseModel):
    client_id: str
    documents: int
    chunks: int
    models: dict[str, int]
    needs_reindex: bool
    backend: str
    embedding_backend: str
    embedding_model: str


# ===========================================================================
# scripts + prompts
# ===========================================================================

Channel = Literal["all", "chat", "voice"]


class ScriptCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=500)
    channel: Channel = "all"
    language: str = "en-IN"
    script_xml: str = Field(min_length=10)
    is_default: bool = False
    voice_gender: str | None = None
    voice_speaker: str | None = None
    multi_stt: bool = False


class ScriptUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = None
    channel: Channel | None = None
    language: str | None = None
    script_xml: str | None = None
    is_default: bool | None = None
    is_active: bool | None = None
    voice_gender: str | None = None
    voice_speaker: str | None = None
    multi_stt: bool | None = None


class ScriptOut(BaseModel):
    id: str
    name: str
    slug: str | None = None
    description: str | None = None
    channel: str
    language: str
    version: int
    is_default: bool
    is_active: bool
    voice_gender: str | None = None
    voice_speaker: str | None = None
    multi_stt: bool
    section_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_serializer('created_at', 'updated_at')
    def serialize_dates(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class ScriptDetail(ScriptOut):
    script_xml: str | None = None
    sections: list[dict] = []
    rendered_prompt: str | None = None


class ScriptImportRequest(BaseModel):
    filename: str = Field(description="A file present in the app's scripts/ folder")


class PromptOut(BaseModel):
    key: str
    content: str
    is_customised: bool = False
    updated_at: datetime | None = None

    @field_serializer('updated_at')
    def serialize_updated_at(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class PromptUpdate(BaseModel):
    content: str = Field(min_length=10)


# ===========================================================================
# customer chat (public widget)
# ===========================================================================


class ChatStart(BaseModel):
    company: str = Field(description="Company id (or name slug) to talk to")
    display_name: str | None = None
    phone: str | None = None
    email: str | None = None
    whatsapp: str | None = None
    instagram: str | None = None
    channel: Literal["web", "whatsapp", "instagram"] = "web"
    language: str | None = None


class ChatSession(BaseModel):
    session_token: str
    conversation_id: str
    company: str
    greeting: str
    expires_in_minutes: int


class ChatSend(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ChatReply(BaseModel):
    reply: str
    confidence: float
    needs_human: bool
    handed_off_to_human: bool
    sources: list[SourceOut] = []
    lead_status: str
    lead_score: int


class PublicCompanyOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    widget_greeting: str | None = None


# ===========================================================================
# inbox / conversations / leads
# ===========================================================================


class MessageOut(BaseModel):
    id: str
    sender: str
    sender_email: str | None = None
    content: str
    confidence: float | None = None
    sources: list | None = None
    model_used: str | None = None
    call_sid: str | None = None
    # Null for inbound messages and for web/voice channels, where there is
    # nothing to push. Non-null on social outbound: sent | failed | skipped.
    delivery_status: str | None = None
    delivery_error: str | None = None
    created_at: datetime | None = None

    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class LeadOut(BaseModel):
    status: str
    score: int
    interest: str
    intent: str
    budget: str
    timeline: str
    product: str
    sentiment: str
    score_breakdown: dict[str, Any] | None = None
    qualified_at: datetime | None = None

    @field_serializer('qualified_at')
    def serialize_qualified_at(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class ConversationOut(BaseModel):
    id: str
    client_id: str
    channel: str
    status: str
    customer_ref: str
    # Populated only for roles holding lead.reveal_pii; masked otherwise.
    customer_name: str | None = None
    customer_phone_masked: str | None = None
    summary: str | None = None
    next_step: str | None = None
    assigned_user_email: str | None = None
    handoff_reason: str | None = None
    language: str | None = None
    message_count: int = 0
    last_message_at: datetime | None = None
    created_at: datetime | None = None
    lead: LeadOut | None = None

    @field_serializer('last_message_at', 'created_at')
    def serialize_dates(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class DeliveryOut(BaseModel):
    """Outcome of pushing a message to a social channel.

    Returned by the inbox reply endpoint so the dashboard can distinguish
    "saved and sent" from "saved but the customer never got it". Optional on
    ConversationDetail, so a client that ignores it is unaffected.
    """

    status: str                       # sent | failed | skipped | not_applicable
    delivered: bool = False
    channel: str | None = None
    message_id: str | None = None
    error: str | None = None
    detail: str | None = None         # operator-facing explanation


class ConversationDetail(ConversationOut):
    messages: list[MessageOut] = []
    suggestions: list[str] = []
    calls: list["CallOut"] = []
    # Populated only by POST /{id}/reply. Null elsewhere.
    delivery: DeliveryOut | None = None


class ChatConversationDetail(ConversationOut):
    """Chat conversation: messages only, calls reduced to a flag."""
    messages: list[MessageOut] = []
    suggestions: list[str] = []
    has_calls: bool = False
    call_count: int = 0


class CallConversationDetail(ConversationOut):
    """Call conversation: multiple calls, each with its own transcript."""
    suggestions: list[str] = []
    calls: list["CallWithTranscript"] = []


class CallWithTranscript(BaseModel):
    """A single call with its metadata and transcript messages."""
    id: str
    call_sid: str | None = None
    provider: str
    mode: str
    status: str
    handed_off: bool
    duration_sec: int
    phone_masked: str | None = None
    language: str | None = None
    script_id: str | None = None
    initiated_by_email: str | None = None
    failure_reason: str | None = None
    recording_url: str | None = None
    created_at: datetime | None = None
    messages: list[MessageOut] = []

    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class CallTranscriptOut(BaseModel):
    """Single call transcript with metadata — fetched by CallSid."""
    id: str
    call_sid: str | None = None
    conversation_id: str
    provider: str
    mode: str
    status: str
    handed_off: bool
    duration_sec: int
    phone_masked: str | None = None
    language: str | None = None
    script_id: str | None = None
    initiated_by_email: str | None = None
    failure_reason: str | None = None
    recording_url: str | None = None
    created_at: datetime | None = None
    messages: list[MessageOut] = []

    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class ConversationListOut(Paged):
    items: list[ConversationOut] = []


class AssignRequest(BaseModel):
    user_email: EmailStr | None = Field(
        default=None, description="Omit to unassign the conversation"
    )


class StatusRequest(BaseModel):
    status: Literal["open", "needs_human", "assigned", "closed"]


class AgentReply(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class SocialIdentityOut(BaseModel):
    """One social identity behind a customer.

    `handle` is what an agent should actually read: '@nikhil.arora' on
    Instagram, or the person's name on Messenger. `external_user_id` is the raw
    PSID / IGSID — kept because it is what you quote to Meta support, but it is
    never the primary display value.
    """

    channel: str
    handle: str | None = None          # '@username' or the display name
    profile_name: str | None = None    # real name, when the platform gives one
    external_user_id: str | None = None
    profile_url: str | None = None
    opted_out: bool = False
    last_message_at: datetime | None = None

    @field_serializer("last_message_at")
    def serialize_last_message_at(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class ContactReveal(BaseModel):
    phone: str | None = None
    email: str | None = None
    whatsapp: str | None = None
    instagram: str | None = None
    # Added so a reveal on a Messenger or Instagram lead shows a person rather
    # than a 16-digit id. Populated from leadai_channel_identities.
    display_name: str | None = None
    social_identities: list[SocialIdentityOut] = []
    revealed_at: datetime
    warning: str = "This reveal has been recorded in the activity log."

    @field_serializer('revealed_at')
    def serialize_revealed_at(self, dt: datetime, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


# ===========================================================================
# voice / calls
# ===========================================================================


class CallStart(BaseModel):
    mode: Literal["ai_voice", "agent"] = "ai_voice"
    script_id: str | None = None
    override_number: str | None = Field(
        default=None,
        description="Call this number instead of the customer's stored one "
                    "(requires lead.reveal_pii)",
    )


class CallOut(BaseModel):
    id: str
    conversation_id: str
    call_sid: str | None = None
    provider: str
    mode: str
    status: str
    handed_off: bool
    duration_sec: int
    phone_masked: str | None = None
    language: str | None = None
    script_id: str | None = None
    initiated_by_email: str | None = None
    failure_reason: str | None = None
    created_at: datetime | None = None

    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class CallSyncOut(BaseModel):
    imported: int
    total_messages: int | None = None
    lead_status: str | None = None
    lead_score: int | None = None
    reason: str | None = None


class VoiceTurnIn(BaseModel):
    """One turn of the voice loop, for simulation/testing without a carrier.

    In production the real loop runs inside the existing /media-stream handler;
    this endpoint exercises the same brain over HTTP so the flow can be tested.
    """

    utterance: str = Field(min_length=1, max_length=1000)


class VoiceTurnOut(BaseModel):
    reply: str
    confidence: float
    handed_off: bool
    tts: dict
    lead_status: str
    lead_score: int


# ===========================================================================
# analytics + activity
# ===========================================================================


class AnalyticsOut(BaseModel):
    client_id: str
    leads_today: int
    total_leads: int
    cold: int
    warm: int
    hot: int
    qualified: int
    assigned: int
    unassigned: int
    needs_human: int
    closed: int
    calls: int
    completed_calls: int
    failed_calls: int
    avg_call_duration: float
    conversion_rate: float
    avg_lead_score: float
    ai_containment_rate: float
    documents: int
    chunks: int
    daily: list[dict] = []
    agents: list[dict] = []
    channels: dict[str, int] = {}


class ActivityOut(BaseModel):
    id: str
    client_id: str | None = None
    actor_email: str | None = None
    actor_role: str | None = None
    action: str
    log_type: str
    entity_type: str | None = None
    entity_id: str | None = None
    message: str
    meta: dict | None = None
    ip_address: str | None = None
    created_at: datetime | None = None

    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class ActivityListOut(Paged):
    items: list[ActivityOut] = []


class HealthOut(BaseModel):
    status: str
    llm: str
    llm_model: str
    embeddings: str
    embedding_model: str
    vector_store: str
    telephony: dict
    tables: dict | None = None


# ===========================================================================
# user management
# ===========================================================================


class UserManagementCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    role: str | None = Field(default=None, description="LeadAI role to grant (e.g. agent, manager)")
    client_id: str | None = Field(default=None, description="Company to associate the role with")
    client_name: str | None = None
    send_email_confirmation: bool = False


class UserManagementUpdate(BaseModel):
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=6, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    role: str | None = Field(default=None, description="LeadAI role to grant (e.g. employee, manager)")
    client_id: str | None = None
    client_name: str | None = None


class UserManagementOut(BaseModel):
    id: str
    email: str
    name: str | None = None
    email_confirmed: bool = False
    role_granted: str | None = None
    client_id: str | None = None


class MemberCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(
        default="employee",
        description="Role to grant: employee, manager",
    )
    send_email_confirmation: bool = False


class MemberOut(BaseModel):
    id: str
    email: str
    name: str | None = None
    role: str
    client_id: str
    is_active: bool = True
    created_at: datetime | None = None

    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime | None, _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class MemberUpdate(BaseModel):
    full_name: str | None = None
    role: RoleName | None = None
    is_active: bool | None = None


class MemberListOut(BaseModel):
    total: int
    items: list[MemberOut]


ConversationDetail.model_rebuild()
ChatConversationDetail.model_rebuild()
CallConversationDetail.model_rebuild()
CallWithTranscript.model_rebuild()
CallTranscriptOut.model_rebuild()
