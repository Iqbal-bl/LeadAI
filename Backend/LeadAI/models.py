"""
LeadAI tables.

Design rules that keep this additive and safe:

1. NOTHING in Domain/models.py is modified. Every table here is new and its
   name is prefixed `leadai_` so it can never collide with an existing table.
2. The multi-tenant anchor is the EXISTING `Clients` table
   (Domain.models.Client). A "company" in LeadAI is a Client row — we do not
   introduce a second, competing tenant concept. Every tenant-owned row carries
   `ClientId` and every query filters on it.
3. Columns follow the outbound convention: PascalCase names and the same audit
   quintet (CreatedBy / CreatedAt / UpdatedBy / UpdatedAt / IsDeleted) via the
   local abstract base below, mirroring Domain.models.BaseDocument.
4. Foreign keys point at `Clients.Id` / `users.Id` but are declared WITHOUT
   cross-table constraints where the outbound code already avoids them
   (Batch.ClientId does the same), so nothing can fail an existing migration.
5. Voice calls reuse the outbound pipeline. `LeadCall` stores only the LINK
   between a lead conversation and the CallSid that `calllogs` / `conversations`
   / `recordings` already own — no duplicated call state.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LeadAIBase(Base):
    """Same audit shape as Domain.models.BaseDocument."""

    __abstract__ = True

    Id = Column(String(36), primary_key=True, default=_uuid, unique=True, nullable=False)
    CreatedBy = Column(String(100), nullable=False, default="system")
    CreatedAt = Column(DateTime, default=utcnow, index=True)
    UpdatedBy = Column(String(100), nullable=True)
    UpdatedAt = Column(DateTime, nullable=True)
    IsDeleted = Column(Boolean, default=False, index=True)


# ===========================================================================
# RBAC
# ===========================================================================

ROLE_ADMIN = "Admin"
ROLE_COMPANY_ADMIN = "company_admin"
ROLE_MANAGER = "manager"
ROLE_EMPLOYEE = "employee"

ALL_ROLES = (
    ROLE_ADMIN,
    ROLE_COMPANY_ADMIN,
    ROLE_MANAGER,
    ROLE_EMPLOYEE,
)


class LeadUserRole(LeadAIBase):
    """Grants a role to a user, optionally scoped to one company.

    Auth itself stays with the identity server — this table only answers
    "what may this identity DO", never "who is this". The identity is stored as
    the normalised email that TokenValidationMiddleware already resolves, so no
    change to the `users` table or the login flow is required.

    ClientId NULL + role Admin  => global scope.
    """

    __tablename__ = "leadai_user_roles"
    __table_args__ = (
        UniqueConstraint("UserEmail", "ClientId", name="uq_leadai_role_user_client"),
        Index("ix_leadai_role_useremail", "UserEmail"),
        Index("ix_leadai_role_clientid", "ClientId"),
    )

    UserEmail = Column(String(200), nullable=False)
    UserId = Column(String(36), nullable=True)      # optional link to users.Id
    FullName = Column(String(160), nullable=True)
    Role = Column(String(40), nullable=False, default=ROLE_EMPLOYEE)
    ClientId = Column(String(36), nullable=True)    # NULL => all companies
    IsActive = Column(Boolean, default=True)


class LeadRolePermission(LeadAIBase):
    """Dynamic role-permission mapping. Overrides the hardcoded defaults in rbac.py.

    When no row exists for a (Role, PermissionKey) pair the system falls back to
    the default ROLE_PERMISSIONS dictionary, so existing installations work
    unchanged. Inserting a row for a permission that was previously granted by
    default lets an admin revoke it; deleting the row restores the default.
    """

    __tablename__ = "leadai_role_permissions"
    __table_args__ = (
        UniqueConstraint("Role", "PermissionKey", name="uq_leadai_role_perm"),
        Index("ix_leadai_role_perm_role", "Role"),
    )

    Role = Column(String(40), nullable=False)
    PermissionKey = Column(String(80), nullable=False)
    IsGranted = Column(Boolean, nullable=False, default=True)
    Description = Column(String(200), nullable=True)


class LeadActivityLog(LeadAIBase):
    """Every state-changing action in LeadAI lands here.

    Deliberately shaped like Domain.models.BaseDocument + the BatchLogs pattern
    (LogMessage / LogType) so the existing log viewers feel familiar, with the
    extra columns an audit trail needs: actor, entity, before/after payload,
    IP + user agent.
    """

    __tablename__ = "leadai_activity_logs"
    __table_args__ = (
        Index("ix_leadai_activity_client_created", "ClientId", "CreatedAt"),
        Index("ix_leadai_activity_action", "Action"),
        Index("ix_leadai_activity_entity", "EntityType", "EntityId"),
        Index("ix_leadai_activity_actor", "ActorEmail"),
    )

    ClientId = Column(String(36), nullable=True)
    ActorEmail = Column(String(200), nullable=True)
    ActorRole = Column(String(40), nullable=True)
    Action = Column(String(80), nullable=False)          # e.g. lead.assigned
    LogType = Column(String(40), nullable=False, default="Info")  # Info|Warning|Error|Security
    EntityType = Column(String(60), nullable=True)       # conversation|document|script|...
    EntityId = Column(String(64), nullable=True)
    LogMessage = Column(String(2000), nullable=False, default="")
    MetaJson = Column(JSON, nullable=True)               # arbitrary before/after detail
    IpAddress = Column(String(64), nullable=True)
    UserAgent = Column(String(300), nullable=True)


# ===========================================================================
# Per-company knowledge base (RAG)
# ===========================================================================


class LeadKbDocument(LeadAIBase):
    """One uploaded/pasted source in a company's knowledge base."""

    __tablename__ = "leadai_kb_documents"
    __table_args__ = (
        Index("ix_leadai_kbdoc_clientid", "ClientId"),
        Index("ix_leadai_kbdoc_status", "Status"),
    )

    ClientId = Column(String(36), nullable=False)
    Title = Column(String(255), nullable=False, default="Untitled")
    FileName = Column(String(255), nullable=True)
    ContentType = Column(String(120), nullable=False, default="text/plain")
    SourceType = Column(String(40), nullable=False, default="upload")  # upload|faq|url|text
    SourceUrl = Column(String(500), nullable=True)
    Status = Column(String(32), nullable=False, default="pending")  # pending|indexing|indexed|failed
    StatusMessage = Column(String(500), nullable=True)
    ChunkCount = Column(Integer, default=0)
    CharCount = Column(Integer, default=0)
    EmbeddingModel = Column(String(120), nullable=True)
    Tags = Column(String(300), nullable=True)
    RawText = Column(Text, nullable=True)  # kept so a doc can be re-indexed

    chunks = relationship(
        "LeadKbChunk",
        primaryjoin="LeadKbDocument.Id == foreign(LeadKbChunk.DocumentId)",
        lazy="select",
        viewonly=True,
    )


class LeadKbChunk(LeadAIBase):
    """Retrievable passage plus its embedding vector.

    The tenant filter lives on the CHUNK, not just the document, so a retrieval
    query can never join its way out of its company.
    """

    __tablename__ = "leadai_kb_chunks"
    __table_args__ = (
        Index("ix_leadai_kbchunk_client_doc", "ClientId", "DocumentId"),
    )

    ClientId = Column(String(36), nullable=False)
    DocumentId = Column(String(36), nullable=False)
    Position = Column(Integer, default=0)
    ChunkText = Column(Text, nullable=False)
    TokenCount = Column(Integer, default=0)
    Embedding = Column(JSON, nullable=True)      # list[float]
    EmbeddingModel = Column(String(120), nullable=True)
    VectorRef = Column(String(80), nullable=True)  # qdrant point id when offloaded


# ===========================================================================
# Per-company dynamic scripts + prompts
# ===========================================================================


class LeadCompanyScript(LeadAIBase):
    """A versioned conversation script owned by one company.

    `ScriptXml` is the SAME XML dialect the outbound app already parses with
    xml_parser.parse_xml_to_sections, so a company script can drive a real
    Twilio/Exotel voice call with no translation layer. `SectionsJson` caches
    the parsed form to keep the call path off the XML parser.

    Channel lets one company run a different script for voice than for chat.
    """

    __tablename__ = "leadai_company_scripts"
    __table_args__ = (
        Index("ix_leadai_script_client_channel", "ClientId", "Channel"),
        Index("ix_leadai_script_isdefault", "IsDefault"),
    )

    ClientId = Column(String(36), nullable=False)
    Name = Column(String(160), nullable=False)
    Slug = Column(String(160), nullable=True)
    Description = Column(String(500), nullable=True)
    Channel = Column(String(20), nullable=False, default="all")  # all|chat|voice
    Language = Column(String(20), nullable=False, default="en-IN")
    Version = Column(Integer, nullable=False, default=1)
    IsDefault = Column(Boolean, default=False)
    IsActive = Column(Boolean, default=True)
    ScriptXml = Column(Text, nullable=True)
    SectionsJson = Column(JSON, nullable=True)
    # Voice knobs handed to the existing call pipeline verbatim.
    VoiceGender = Column(String(20), nullable=True)
    VoiceSpeaker = Column(String(60), nullable=True)
    MultiStt = Column(Boolean, default=False)


class LeadCompanyPrompt(LeadAIBase):
    """Editable system prompts per company. Keys mirror the pipeline stages."""

    __tablename__ = "leadai_company_prompts"
    __table_args__ = (
        UniqueConstraint("ClientId", "PromptKey", name="uq_leadai_prompt_client_key"),
        Index("ix_leadai_prompt_clientid", "ClientId"),
    )

    ClientId = Column(String(36), nullable=False)
    PromptKey = Column(String(40), nullable=False)  # greeting|sales|qualification|escalation|voice
    Content = Column(Text, nullable=False)


class LeadCompanySettings(LeadAIBase):
    """Per-company AI behaviour overrides. One row per company."""

    __tablename__ = "leadai_company_settings"
    __table_args__ = (
        UniqueConstraint("ClientId", name="uq_leadai_settings_client"),
    )

    ClientId = Column(String(36), nullable=False)
    HandoffThreshold = Column(Float, nullable=True)   # falls back to global
    RetrievalTopK = Column(Integer, nullable=True)
    DefaultLanguage = Column(String(20), nullable=True)
    AutoAssignEnabled = Column(Boolean, default=False)
    AutoCallOnHotLead = Column(Boolean, default=False)
    WidgetEnabled = Column(Boolean, default=True)
    WidgetGreeting = Column(String(500), nullable=True)

    # ---- Lead threshold -----------------------------------------------------
    # The score at or above which a lead is considered worth a human's attention
    # and is surfaced on the company dashboard / inbox default view. Below it,
    # the conversation still exists and the AI still handles it — it is simply
    # not pushed at the sales team. NULL falls back to LEADAI_LEAD_THRESHOLD.
    LeadScoreThreshold = Column(Integer, nullable=True)
    # Above this score the lead is auto-promoted to a CRM account (0/NULL = off).
    AutoConvertThreshold = Column(Integer, nullable=True)
    # Fire a websocket/inbox notification the moment a lead crosses the bar.
    NotifyOnThreshold = Column(Boolean, default=True)
    # When true, the inbox list hides sub-threshold leads unless explicitly asked
    # for with ?include_below_threshold=true.
    HideBelowThreshold = Column(Boolean, default=False)

    # ---- Outbound / campaign defaults --------------------------------------
    DefaultCampaignChannel = Column(String(20), nullable=True)
    CampaignConcurrency = Column(Integer, nullable=True)
    CampaignRatePerMinute = Column(Integer, nullable=True)
    QuietHoursStart = Column(Integer, nullable=True)   # local hour 0-23
    QuietHoursEnd = Column(Integer, nullable=True)
    TimeZone = Column(String(60), nullable=True)


# ===========================================================================
# Customers / conversations / messages / leads
# ===========================================================================


class LeadCustomer(LeadAIBase):
    """The end user talking to the AI.

    Real identifiers are encrypted at rest (Fernet). Agents only ever see
    `PublicRef`; revealing the real contact is an admin action and is written
    to leadai_activity_logs.
    """

    __tablename__ = "leadai_customers"
    __table_args__ = (
        Index("ix_leadai_customer_client_ref", "ClientId", "PublicRef"),
    )

    ClientId = Column(String(36), nullable=False)
    PublicRef = Column(String(40), nullable=False)     # "Customer #48192"
    DisplayName = Column(String(160), nullable=True)
    PhoneEnc = Column(Text, nullable=True)
    EmailEnc = Column(Text, nullable=True)
    WhatsAppEnc = Column(Text, nullable=True)
    InstagramEnc = Column(Text, nullable=True)
    # Non-reversible lookup key so a returning customer is recognised without
    # decrypting anything.
    PhoneHash = Column(String(64), nullable=True, index=True)


class LeadConversation(LeadAIBase):
    """One conversation, whatever the channel (web widget, whatsapp, voice).

    Voice calls placed through the existing outbound pipeline attach here via
    LeadCall, so a phone conversation and a chat conversation are the same
    object with the same lead attached.
    """

    __tablename__ = "leadai_conversations"
    __table_args__ = (
        Index("ix_leadai_conv_client_status", "ClientId", "Status"),
        Index("ix_leadai_conv_assigned", "AssignedUserEmail"),
        Index("ix_leadai_conv_lastmsg", "LastMessageAt"),
    )

    ClientId = Column(String(36), nullable=False)
    CustomerId = Column(String(36), nullable=False)
    Channel = Column(String(20), nullable=False, default="web")  # web|whatsapp|instagram|voice
    Status = Column(String(24), nullable=False, default="open")  # open|needs_human|assigned|closed
    ScriptId = Column(String(36), nullable=True)     # which company script drove it
    Summary = Column(Text, nullable=True)
    NextStep = Column(Text, nullable=True)
    AssignedUserEmail = Column(String(200), nullable=True)
    AssignedAt = Column(DateTime, nullable=True)
    HandoffReason = Column(String(300), nullable=True)
    Language = Column(String(20), nullable=True)
    MessageCount = Column(Integer, default=0)
    LastMessageAt = Column(DateTime, default=utcnow)
    ClosedAt = Column(DateTime, nullable=True)
    # Which connected social account this thread arrived on (NULL for web widget
    # and for voice). Lets one company run several WhatsApp numbers / Pages.
    ChannelAccountId = Column(String(36), nullable=True, index=True)
    # External thread id from the provider (wa_id / PSID / IGSID), so an inbound
    # webhook resolves straight to the open conversation.
    ExternalThreadId = Column(String(120), nullable=True, index=True)
    # Attribution: the campaign that produced this conversation, if any.
    CampaignId = Column(String(36), nullable=True, index=True)
    # Set once the lead crosses the company's threshold, so "new hot lead"
    # notifications fire exactly once instead of on every subsequent message.
    ThresholdNotifiedAt = Column(DateTime, nullable=True)


class LeadMessage(LeadAIBase):
    """A single turn. `Sender` is customer | ai | agent | system."""

    __tablename__ = "leadai_messages"
    __table_args__ = (
        Index("ix_leadai_msg_conv_created", "ConversationId", "CreatedAt"),
        Index("ix_leadai_msg_clientid", "ClientId"),
    )

    ClientId = Column(String(36), nullable=False)
    ConversationId = Column(String(36), nullable=False)
    Sender = Column(String(16), nullable=False)
    SenderEmail = Column(String(200), nullable=True)   # set when Sender == agent
    Content = Column(Text, nullable=False)
    Confidence = Column(Float, nullable=True)
    SourcesJson = Column(JSON, nullable=True)          # retrieved chunk citations
    LatencyMs = Column(Integer, nullable=True)
    ModelUsed = Column(String(120), nullable=True)
    # For voice turns: which CallSid this line came from.
    CallSid = Column(String(100), nullable=True, index=True)


class Lead(LeadAIBase):
    """Qualification state, recomputed after every inbound customer turn."""

    __tablename__ = "leadai_leads"
    __table_args__ = (
        UniqueConstraint("ConversationId", name="uq_leadai_lead_conversation"),
        Index("ix_leadai_lead_client_status", "ClientId", "Status"),
        Index("ix_leadai_lead_score", "Score"),
    )

    ClientId = Column(String(36), nullable=False)
    ConversationId = Column(String(36), nullable=False)
    Status = Column(String(20), nullable=False, default="cold")  # cold|warm|hot|qualified|lost
    Score = Column(Integer, default=0)
    Interest = Column(String(160), default="unknown")
    Intent = Column(String(60), default="browsing")
    Budget = Column(String(120), default="unknown")
    Timeline = Column(String(60), default="unknown")
    Product = Column(String(200), default="unknown")
    Sentiment = Column(String(24), default="neutral")
    ScoreBreakdown = Column(JSON, nullable=True)   # explainability for the UI
    QualifiedAt = Column(DateTime, nullable=True)
    # Threshold bookkeeping. Denormalised onto the lead so the dashboard query is
    # a single indexed WHERE instead of a join to settings per row.
    IsAboveThreshold = Column(Boolean, default=False, index=True)
    ThresholdCrossedAt = Column(DateTime, nullable=True)
    # CRM conversion.
    ConvertedAccountId = Column(String(36), nullable=True, index=True)
    ConvertedAt = Column(DateTime, nullable=True)


class LeadCall(LeadAIBase):
    """Link row between a lead conversation and a call in the EXISTING pipeline.

    The authoritative call record stays in `calllogs`/`callstatus`, the
    transcript stays in `conversations`, the audio stays in `recordings`. This
    table only records who asked for the call, why, and which conversation it
    belongs to — plus the carrier used.
    """

    __tablename__ = "leadai_calls"
    __table_args__ = (
        Index("ix_leadai_call_conv", "ConversationId"),
        Index("ix_leadai_call_callsid", "CallSid"),
        Index("ix_leadai_call_clientid", "ClientId"),
    )

    ClientId = Column(String(36), nullable=False)
    ConversationId = Column(String(36), nullable=False)
    CallSid = Column(String(100), nullable=True)
    Provider = Column(String(30), nullable=False, default="twilio")  # twilio|exotel|simulated
    Mode = Column(String(20), nullable=False, default="ai_voice")   # ai_voice|agent
    Direction = Column(String(12), nullable=False, default="outbound")
    ScriptId = Column(String(36), nullable=True)
    Language = Column(String(20), nullable=True)
    InitiatedByEmail = Column(String(200), nullable=True)
    Status = Column(String(30), nullable=False, default="initiated")
    HandedOff = Column(Boolean, default=False)
    DurationSec = Column(Integer, default=0)
    PhoneMasked = Column(String(40), nullable=True)   # "+9198*****21" for the UI
    FailureReason = Column(String(300), nullable=True)


# Phase-2 tables (channels, campaigns, CRM, files, jobs) live in models_ext to
# keep this file readable. Importing at the BOTTOM is deliberate: models_ext
# imports LeadAIBase from here, so the import must happen after that class is
# defined. The tables land on the same Base.metadata either way.
from .models_ext import (  # noqa: E402
    ALL_LEADAI_EXT_TABLES,
    CHANNEL_EMAIL,
    CHANNEL_INSTAGRAM,
    CHANNEL_MESSENGER,
    CHANNEL_SMS,
    CHANNEL_VOICE,
    CHANNEL_WEB,
    CHANNEL_WHATSAPP,
    INBOUND_CHANNELS,
    OUTBOUND_CHANNELS,
    LeadAccount,
    LeadAccountNote,
    LeadCampaign,
    LeadCampaignRecipient,
    LeadChannelAccount,
    LeadChannelEvent,
    LeadChannelIdentity,
    LeadContactList,
    LeadContactListItem,
    LeadFile,
    LeadJob,
)

ALL_LEADAI_TABLES = (
    LeadUserRole,
    LeadRolePermission,
    LeadActivityLog,
    LeadKbDocument,
    LeadKbChunk,
    LeadCompanyScript,
    LeadCompanyPrompt,
    LeadCompanySettings,
    LeadCustomer,
    LeadConversation,
    LeadMessage,
    Lead,
    LeadCall,
) + ALL_LEADAI_EXT_TABLES

# ---------------------------------------------------------------------------
# Social publishing tables. Imported at the BOTTOM because models_social.py
# imports LeadAIBase from this module — importing it at the top would be a
# circular import. By this line LeadAIBase is fully defined, so the import is
# safe, and it must happen before ensure_tables() runs for the tables to be
# registered on the metadata.
# ---------------------------------------------------------------------------
from .models_social import (  # noqa: E402
    ALL_LEADAI_SOCIAL_TABLES,
    LeadSocialPost,
    LeadSocialTopic,
)

ALL_LEADAI_TABLES = ALL_LEADAI_TABLES + ALL_LEADAI_SOCIAL_TABLES
