"""
LeadAI — extension tables (Phase 2 features).

Same rules as models.py: every table is new, every name is prefixed `leadai_`,
every tenant-owned row carries `ClientId`, and the audit quintet comes from
LeadAIBase. Nothing in models.py or Domain/models.py is modified.

What lives here
---------------
1. SOCIAL CHANNELS   — leadai_channel_accounts / leadai_channel_identities.
   A "channel account" is one connected WhatsApp number, Facebook Page or
   Instagram professional account. A "channel identity" maps an external id
   (wa_id / PSID / IGSID) to a LeadCustomer so a returning contact resumes its
   own conversation instead of creating a duplicate lead.

2. CAMPAIGNS         — leadai_contact_lists / leadai_contact_list_items /
   leadai_campaigns / leadai_campaign_recipients.
   Lists are the reusable audience (uploaded XLSX/CSV/DOCX, or built from
   existing leads/customers). A campaign binds a list to an action (bulk
   message on some channel, or bulk AI voice call) and fans out one recipient
   row per target. The recipient row is the unit of retry, so a campaign can be
   paused, resumed and restarted without re-sending to anyone.

3. CRM / CUSTOMERS   — leadai_accounts / leadai_account_notes.
   A lead that converts becomes an ACCOUNT. Accounts are separate from
   leadai_customers on purpose: `leadai_customers` is the anonymous contact
   behind a conversation, an account is a commercial relationship with owner,
   lifecycle stage, value and consent flags.

4. FILE STORE        — leadai_files. Metadata for objects in MinIO. The bytes
   never touch MySQL; this row holds the bucket/key plus who uploaded it.

5. JOBS              — leadai_jobs. A tiny DB-backed queue so background work
   (campaign fan-out, KB indexing) survives a worker restart and does not run
   twice when uvicorn runs with --workers > 1.
"""
from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from .models import LeadAIBase, utcnow

# ===========================================================================
# 1. Social channels
# ===========================================================================

# Channel identifiers used across the whole platform. `web` is the existing
# widget and stays fully supported — social channels are added ALONGSIDE it.
CHANNEL_WEB = "web"
CHANNEL_WHATSAPP = "whatsapp"
CHANNEL_MESSENGER = "messenger"
CHANNEL_INSTAGRAM = "instagram"
CHANNEL_VOICE = "voice"
CHANNEL_SMS = "sms"
CHANNEL_EMAIL = "email"

INBOUND_CHANNELS = (CHANNEL_WEB, CHANNEL_WHATSAPP, CHANNEL_MESSENGER, CHANNEL_INSTAGRAM)
OUTBOUND_CHANNELS = (
    CHANNEL_WHATSAPP,
    CHANNEL_MESSENGER,
    CHANNEL_INSTAGRAM,
    CHANNEL_SMS,
    CHANNEL_EMAIL,
    CHANNEL_VOICE,
)


class LeadChannelAccount(LeadAIBase):
    """One connected social account belonging to one company.

    Credentials are stored encrypted (Fernet, same helper as customer PII) in
    `AccessTokenEnc`. `ExternalId` is the stable id Meta sends on every webhook
    payload — the phone_number_id for WhatsApp Cloud API, the Page id for
    Messenger, the IG professional account id for Instagram. That is how an
    inbound webhook is routed to the right tenant WITHOUT trusting anything in
    the request body: look up ExternalId -> ClientId.
    """

    __tablename__ = "leadai_channel_accounts"
    __table_args__ = (
        UniqueConstraint("Channel", "ExternalId", name="uq_leadai_channel_external"),
        Index("ix_leadai_chanacct_client", "ClientId", "Channel"),
    )

    ClientId = Column(String(36), nullable=False)
    Channel = Column(String(20), nullable=False)          # whatsapp|messenger|instagram
    Provider = Column(String(30), nullable=False, default="meta")  # meta|twilio|360dialog
    Name = Column(String(160), nullable=False, default="")  # human label in the UI
    ExternalId = Column(String(120), nullable=False)      # phone_number_id / page_id / ig_id
    BusinessAccountId = Column(String(120), nullable=True)  # WABA id / business id
    DisplayNumber = Column(String(40), nullable=True)     # +9198… shown in the UI
    AccessTokenEnc = Column(Text, nullable=True)          # encrypted page/system token
    AppSecretEnc = Column(Text, nullable=True)            # encrypted, for X-Hub signature
    VerifyToken = Column(String(120), nullable=True)      # webhook handshake value
    ApiVersion = Column(String(12), nullable=False, default="v21.0")
    IsActive = Column(Boolean, default=True)
    AutoReply = Column(Boolean, default=True)             # let the AI answer inbound
    ScriptId = Column(String(36), nullable=True)          # channel-specific script
    DefaultLanguage = Column(String(20), nullable=True)
    # Health/observability so the UI can show "connected / failing".
    LastInboundAt = Column(DateTime, nullable=True)
    LastOutboundAt = Column(DateTime, nullable=True)
    LastErrorAt = Column(DateTime, nullable=True)
    LastError = Column(String(500), nullable=True)
    MetaJson = Column(JSON, nullable=True)

    # ---- which Meta integration this account was connected through --------- #
    # "facebook"  : Instagram API with Facebook Login. The IG account is linked
    #               to a Page, the token is a PAGE token, and calls go to
    #               graph.facebook.com. Also the value for whatsapp/messenger.
    # "instagram" : Instagram API with Instagram Login. Standalone professional
    #               account, no Page anywhere, the token is an INSTAGRAM USER
    #               token, and calls go to graph.instagram.com.
    #
    # This is not cosmetic. An Instagram User token sent to graph.facebook.com
    # fails with "Cannot parse access token", which reads like a bad credential
    # rather than a wrong host. Routing is driven off this column.
    LoginType = Column(String(20), nullable=False, default="facebook")

    # The app this account was connected through. A standalone Instagram
    # connection uses a DIFFERENT Meta app from the Facebook one - Meta allows
    # only one API setup per app - so the id is stored per account rather than
    # read from a single global setting.
    AppId = Column(String(120), nullable=True)

    # Instagram User tokens expire after 60 days and must be refreshed while
    # still valid and at least 24h old. Miss that window and the company has to
    # re-authorise from scratch, so the expiry is tracked explicitly rather than
    # inferred. Null for Page tokens, which do not expire this way.
    TokenExpiresAt = Column(DateTime, nullable=True)
    TokenRefreshedAt = Column(DateTime, nullable=True)


class LeadChannelIdentity(LeadAIBase):
    """Maps an external social id to a LeadCustomer inside one company.

    Without this table every inbound WhatsApp message would create a new
    customer and a new lead. With it, the same wa_id always resolves to the same
    customer, and their open conversation is resumed.
    """

    __tablename__ = "leadai_channel_identities"
    __table_args__ = (
        UniqueConstraint(
            "ChannelAccountId", "ExternalUserId", name="uq_leadai_identity_account_user"
        ),
        Index("ix_leadai_identity_client_channel", "ClientId", "Channel"),
        Index("ix_leadai_identity_customer", "CustomerId"),
    )

    ClientId = Column(String(36), nullable=False)
    ChannelAccountId = Column(String(36), nullable=False)
    Channel = Column(String(20), nullable=False)
    ExternalUserId = Column(String(120), nullable=False)   # wa_id / PSID / IGSID
    CustomerId = Column(String(36), nullable=False)
    ConversationId = Column(String(36), nullable=True)     # current open thread
    ProfileName = Column(String(160), nullable=True)
    # WhatsApp/Messenger only allow free-form replies inside a 24h window after
    # the user's last message; outside it a pre-approved template is required.
    LastUserMessageAt = Column(DateTime, nullable=True)
    OptedOut = Column(Boolean, default=False)              # STOP / unsubscribe
    OptedOutAt = Column(DateTime, nullable=True)


class LeadChannelEvent(LeadAIBase):
    """Raw inbound webhook envelope, kept for idempotency and debugging.

    Meta retries webhooks aggressively; `ExternalMessageId` is unique so a
    replayed delivery is dropped instead of producing a duplicate message and a
    duplicate AI reply (which would double the LLM bill and confuse the lead
    score).
    """

    __tablename__ = "leadai_channel_events"
    __table_args__ = (
        UniqueConstraint("ExternalMessageId", name="uq_leadai_chanevent_extid"),
        Index("ix_leadai_chanevent_client_created", "ClientId", "CreatedAt"),
    )

    ClientId = Column(String(36), nullable=True)
    ChannelAccountId = Column(String(36), nullable=True)
    Channel = Column(String(20), nullable=True)
    Direction = Column(String(10), nullable=False, default="inbound")
    ExternalMessageId = Column(String(160), nullable=False)
    EventType = Column(String(40), nullable=True)   # message|status|reaction|unknown
    Status = Column(String(30), nullable=True)      # processed|ignored|failed
    PayloadJson = Column(JSON, nullable=True)
    Error = Column(String(500), nullable=True)


# ===========================================================================
# 2. Campaigns
# ===========================================================================


class LeadContactList(LeadAIBase):
    """A reusable audience.

    SourceType tells you where the rows came from:
      upload    — an XLSX/CSV/DOCX the user dropped in (file kept in MinIO)
      leads     — snapshot of a lead filter at build time
      customers — snapshot of the CRM accounts
      manual    — typed in
    """

    __tablename__ = "leadai_contact_lists"
    __table_args__ = (Index("ix_leadai_list_client", "ClientId", "CreatedAt"),)

    ClientId = Column(String(36), nullable=False)
    Name = Column(String(200), nullable=False)
    Description = Column(String(500), nullable=True)
    SourceType = Column(String(20), nullable=False, default="upload")
    SourceFileId = Column(String(36), nullable=True)     # leadai_files.Id
    SourceFilterJson = Column(JSON, nullable=True)       # the filter, for rebuilds
    TotalCount = Column(Integer, default=0)
    ValidCount = Column(Integer, default=0)
    InvalidCount = Column(Integer, default=0)
    DuplicateCount = Column(Integer, default=0)
    ColumnMapJson = Column(JSON, nullable=True)          # {"phone": "Mobile No", ...}
    Status = Column(String(24), nullable=False, default="ready")  # parsing|ready|failed
    StatusMessage = Column(String(500), nullable=True)
    Tags = Column(String(300), nullable=True)


class LeadContactListItem(LeadAIBase):
    """One target in a list. Contact fields are encrypted like every other PII."""

    __tablename__ = "leadai_contact_list_items"
    __table_args__ = (
        Index("ix_leadai_listitem_list", "ListId", "IsValid"),
        Index("ix_leadai_listitem_client", "ClientId"),
        Index("ix_leadai_listitem_hash", "ListId", "PhoneHash"),
    )

    ClientId = Column(String(36), nullable=False)
    ListId = Column(String(36), nullable=False)
    RowNumber = Column(Integer, default=0)
    Name = Column(String(160), nullable=True)
    PhoneEnc = Column(Text, nullable=True)
    EmailEnc = Column(Text, nullable=True)
    WhatsAppEnc = Column(Text, nullable=True)
    PhoneHash = Column(String(64), nullable=True)
    PhoneMasked = Column(String(40), nullable=True)   # what the UI shows
    CountryCode = Column(String(8), nullable=True)
    # Link back into the platform when the row matched something we already know.
    CustomerId = Column(String(36), nullable=True)
    AccountId = Column(String(36), nullable=True)
    IsValid = Column(Boolean, default=True)
    InvalidReason = Column(String(200), nullable=True)
    # Extra spreadsheet columns survive here so a template can say {{policy_no}}.
    FieldsJson = Column(JSON, nullable=True)


class LeadCampaign(LeadAIBase):
    """A bulk send. One row drives one fan-out.

    Kind:
      message — text/template on a social or SMS channel
      call    — outbound AI voice through the EXISTING batch/Twilio/Exotel path
    """

    __tablename__ = "leadai_campaigns"
    __table_args__ = (
        Index("ix_leadai_campaign_client_status", "ClientId", "Status"),
        Index("ix_leadai_campaign_scheduled", "ScheduledAt"),
    )

    ClientId = Column(String(36), nullable=False)
    Name = Column(String(200), nullable=False)
    Description = Column(String(500), nullable=True)
    Kind = Column(String(20), nullable=False, default="message")   # message|call
    Channel = Column(String(20), nullable=False, default="whatsapp")
    ChannelAccountId = Column(String(36), nullable=True)
    Purpose = Column(String(30), nullable=False, default="promotional")
    # promotional | festive | cold_outreach | follow_up | reactivation | transactional

    # Audience: exactly one of these is authoritative, resolved at build time.
    ListId = Column(String(36), nullable=True)
    AudienceType = Column(String(20), nullable=False, default="list")
    # list | leads | customers
    AudienceFilterJson = Column(JSON, nullable=True)

    # Content
    TemplateName = Column(String(160), nullable=True)      # Meta-approved template
    TemplateLanguage = Column(String(20), nullable=True)
    TemplateParamsJson = Column(JSON, nullable=True)       # positional param mapping
    MessageBody = Column(Text, nullable=True)              # free-form, {{placeholders}}
    MediaFileId = Column(String(36), nullable=True)        # leadai_files.Id
    ScriptId = Column(String(36), nullable=True)           # for Kind == call
    Language = Column(String(20), nullable=True)

    # Execution controls
    Status = Column(String(24), nullable=False, default="draft")
    # draft|scheduled|queued|running|paused|completed|failed|cancelled
    ScheduledAt = Column(DateTime, nullable=True)
    StartedAt = Column(DateTime, nullable=True)
    CompletedAt = Column(DateTime, nullable=True)
    Concurrency = Column(Integer, default=5)          # simultaneous sends/calls
    RatePerMinute = Column(Integer, default=60)       # carrier-friendly throttle
    MaxRetries = Column(Integer, default=1)
    RespectOptOut = Column(Boolean, default=True)
    DedupeByPhone = Column(Boolean, default=True)
    QuietHoursStart = Column(Integer, nullable=True)  # local hour, 0-23 (TRAI/DND)
    QuietHoursEnd = Column(Integer, nullable=True)
    TimeZone = Column(String(60), nullable=True, default="Asia/Kolkata")

    # Live counters, denormalised so the dashboard never aggregates 100k rows.
    TotalCount = Column(Integer, default=0)
    QueuedCount = Column(Integer, default=0)
    SentCount = Column(Integer, default=0)
    DeliveredCount = Column(Integer, default=0)
    ReadCount = Column(Integer, default=0)
    RepliedCount = Column(Integer, default=0)
    FailedCount = Column(Integer, default=0)
    SkippedCount = Column(Integer, default=0)
    LeadsCreated = Column(Integer, default=0)
    StatusMessage = Column(String(500), nullable=True)


class LeadCampaignRecipient(LeadAIBase):
    """One target inside one campaign. The unit of work, retry and reporting."""

    __tablename__ = "leadai_campaign_recipients"
    __table_args__ = (
        Index("ix_leadai_recipient_campaign_status", "CampaignId", "Status"),
        Index("ix_leadai_recipient_client", "ClientId"),
        Index("ix_leadai_recipient_extid", "ExternalMessageId"),
        UniqueConstraint("CampaignId", "DedupeKey", name="uq_leadai_recipient_dedupe"),
    )

    ClientId = Column(String(36), nullable=False)
    CampaignId = Column(String(36), nullable=False)
    ListItemId = Column(String(36), nullable=True)
    CustomerId = Column(String(36), nullable=True)
    AccountId = Column(String(36), nullable=True)
    ConversationId = Column(String(36), nullable=True)
    CallId = Column(String(36), nullable=True)          # leadai_calls.Id when Kind==call

    Name = Column(String(160), nullable=True)
    PhoneEnc = Column(Text, nullable=True)
    PhoneMasked = Column(String(40), nullable=True)
    DedupeKey = Column(String(80), nullable=False)      # phone hash or email hash
    FieldsJson = Column(JSON, nullable=True)

    Status = Column(String(20), nullable=False, default="queued")
    # queued|sending|sent|delivered|read|replied|failed|skipped|opted_out
    Attempts = Column(Integer, default=0)
    ExternalMessageId = Column(String(160), nullable=True)
    RenderedBody = Column(Text, nullable=True)
    SentAt = Column(DateTime, nullable=True)
    DeliveredAt = Column(DateTime, nullable=True)
    ReadAt = Column(DateTime, nullable=True)
    RepliedAt = Column(DateTime, nullable=True)
    FailureReason = Column(String(400), nullable=True)


# ===========================================================================
# 3. CRM — customers / accounts (converted leads)
# ===========================================================================


class LeadAccount(LeadAIBase):
    """A converted lead: someone who is now a customer of the company.

    Distinct from LeadCustomer (the anonymous person in a conversation). An
    account owns lifecycle, value, owner and — importantly — CONSENT, which is
    what every outbound campaign must check before contacting them.
    """

    __tablename__ = "leadai_accounts"
    __table_args__ = (
        Index("ix_leadai_account_client_stage", "ClientId", "Stage"),
        Index("ix_leadai_account_owner", "OwnerEmail"),
        Index("ix_leadai_account_phonehash", "ClientId", "PhoneHash"),
    )

    ClientId = Column(String(36), nullable=False)
    CustomerId = Column(String(36), nullable=True)      # origin contact
    SourceConversationId = Column(String(36), nullable=True)
    SourceLeadId = Column(String(36), nullable=True)

    DisplayName = Column(String(160), nullable=False, default="Customer")
    CompanyName = Column(String(200), nullable=True)
    PhoneEnc = Column(Text, nullable=True)
    EmailEnc = Column(Text, nullable=True)
    WhatsAppEnc = Column(Text, nullable=True)
    PhoneHash = Column(String(64), nullable=True)
    PhoneMasked = Column(String(40), nullable=True)
    EmailMasked = Column(String(160), nullable=True)

    Stage = Column(String(24), nullable=False, default="customer")
    # customer|onboarding|active|at_risk|churned|vip
    Status = Column(String(20), nullable=False, default="active")
    OwnerEmail = Column(String(200), nullable=True)
    Product = Column(String(200), nullable=True)
    Value = Column(Float, default=0.0)
    Currency = Column(String(8), default="INR")
    Source = Column(String(40), nullable=True)          # channel the lead came from
    Tags = Column(String(300), nullable=True)
    Notes = Column(Text, nullable=True)

    # Consent / preference — checked by campaign_runner before every send.
    OptInWhatsApp = Column(Boolean, default=True)
    OptInSms = Column(Boolean, default=True)
    OptInEmail = Column(Boolean, default=True)
    OptInCall = Column(Boolean, default=True)
    DoNotDisturb = Column(Boolean, default=False)

    ConvertedAt = Column(DateTime, default=utcnow)
    LastContactedAt = Column(DateTime, nullable=True)
    NextFollowUpAt = Column(DateTime, nullable=True)
    Birthday = Column(DateTime, nullable=True)          # for festive/greeting sends
    Anniversary = Column(DateTime, nullable=True)
    FieldsJson = Column(JSON, nullable=True)


class LeadAccountNote(LeadAIBase):
    """Timeline entry on an account: note, call log, meeting, campaign touch."""

    __tablename__ = "leadai_account_notes"
    __table_args__ = (Index("ix_leadai_accnote_account", "AccountId", "CreatedAt"),)

    ClientId = Column(String(36), nullable=False)
    AccountId = Column(String(36), nullable=False)
    NoteType = Column(String(24), nullable=False, default="note")
    # note|call|meeting|message|campaign|stage_change
    Body = Column(Text, nullable=False, default="")
    AuthorEmail = Column(String(200), nullable=True)
    MetaJson = Column(JSON, nullable=True)


# ===========================================================================
# 4. File store (MinIO)
# ===========================================================================


class LeadFile(LeadAIBase):
    """Metadata row for one object in MinIO. Bytes never enter MySQL."""

    __tablename__ = "leadai_files"
    __table_args__ = (
        Index("ix_leadai_file_client_purpose", "ClientId", "Purpose"),
        UniqueConstraint("Bucket", "ObjectKey", name="uq_leadai_file_object"),
    )

    ClientId = Column(String(36), nullable=False)
    Purpose = Column(String(30), nullable=False, default="general")
    # general|kb|campaign_list|campaign_media|recording|export|avatar
    FileName = Column(String(255), nullable=False)
    ContentType = Column(String(160), nullable=False, default="application/octet-stream")
    SizeBytes = Column(Integer, default=0)
    Bucket = Column(String(80), nullable=False)
    ObjectKey = Column(String(500), nullable=False)
    Checksum = Column(String(64), nullable=True)        # sha256, for dedupe
    StorageBackend = Column(String(20), nullable=False, default="minio")  # minio|local
    PublicUrl = Column(String(700), nullable=True)
    UploadedByEmail = Column(String(200), nullable=True)
    LinkedEntityType = Column(String(40), nullable=True)
    LinkedEntityId = Column(String(64), nullable=True)
    MetaJson = Column(JSON, nullable=True)


# ===========================================================================
# 5. Background jobs
# ===========================================================================


class LeadJob(LeadAIBase):
    """Durable work item.

    Why a table and not just asyncio.create_task: with more than one uvicorn
    worker, an in-process task runs N times or dies with the process that owned
    it. A row plus an atomic claim (UPDATE ... WHERE Status='queued') gives
    exactly-once-ish semantics with no broker to operate. Swap the claim for
    Celery/RQ later without touching any caller.
    """

    __tablename__ = "leadai_jobs"
    __table_args__ = (
        Index("ix_leadai_job_status_runat", "Status", "RunAt"),
        Index("ix_leadai_job_client_kind", "ClientId", "Kind"),
    )

    ClientId = Column(String(36), nullable=True)
    Kind = Column(String(40), nullable=False)
    # campaign.run | campaign.recipient | kb.index | list.parse | export.leads
    PayloadJson = Column(JSON, nullable=True)
    Status = Column(String(20), nullable=False, default="queued")
    # queued|claimed|running|done|failed|cancelled
    Priority = Column(Integer, default=5)
    Attempts = Column(Integer, default=0)
    MaxAttempts = Column(Integer, default=3)
    RunAt = Column(DateTime, default=utcnow)
    ClaimedAt = Column(DateTime, nullable=True)
    ClaimedBy = Column(String(80), nullable=True)      # worker id
    FinishedAt = Column(DateTime, nullable=True)
    ResultJson = Column(JSON, nullable=True)
    Error = Column(String(1000), nullable=True)


ALL_LEADAI_EXT_TABLES = (
    LeadChannelAccount,
    LeadChannelIdentity,
    LeadChannelEvent,
    LeadContactList,
    LeadContactListItem,
    LeadCampaign,
    LeadCampaignRecipient,
    LeadAccount,
    LeadAccountNote,
    LeadFile,
    LeadJob,
)
