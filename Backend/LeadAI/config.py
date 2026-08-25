"""
LeadAI runtime configuration.

Read straight from the same `.env` the outbound app already loads, so there is
one config surface for the whole service. Every external dependency degrades
gracefully:

  * OPENAI_API_KEY missing  -> local hashed-ngram embeddings + deterministic
                               (extractive) answering. RAG still works.
  * EXOTEL_* missing        -> outbound voice falls back to the Twilio leg that
                               is already wired up in multiligual_call.py.
  * SARVAM_API_KEY missing  -> the existing sarvam_stt/sarvam_tts modules
                               already handle their own absence.
  * REDIS_URL missing       -> in-process cache.

Nothing here mutates or overrides the outbound app's own settings.
"""
import os
from functools import lru_cache


def _b(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class LeadAISettings:
    """Plain object (not pydantic-settings) to avoid adding a dependency the
    outbound requirements.txt doesn't already carry."""

    # ---- API surface -------------------------------------------------------
    api_prefix: str = os.getenv("LEADAI_API_PREFIX", "/api/leadai")

    # ---- LLM + embeddings (OpenAI) ----------------------------------------
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY") or None
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_embed_model: str = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
    openai_embed_dim: int = _i("OPENAI_EMBED_DIM", 1536)
    openai_timeout: float = _f("OPENAI_TIMEOUT", 45.0)

    # ---- RAG tuning -------------------------------------------------------
    retrieval_top_k: int = _i("LEADAI_RETRIEVAL_TOP_K", 5)
    handoff_confidence_threshold: float = _f("LEADAI_HANDOFF_THRESHOLD", 0.40)
    chunk_max_chars: int = _i("LEADAI_CHUNK_MAX_CHARS", 900)
    chunk_overlap: int = _i("LEADAI_CHUNK_OVERLAP", 150)
    # Weighting of the hybrid score: vector similarity vs lexical (IDF) coverage.
    hybrid_vector_weight: float = _f("LEADAI_HYBRID_VECTOR_WEIGHT", 0.55)

    # ---- Vector store -----------------------------------------------------
    # "mysql" keeps embeddings in the leadai_kb_chunks table (default, zero
    # infra). "qdrant" offloads to a real ANN index when QDRANT_URL is set.
    qdrant_url: str | None = os.getenv("QDRANT_URL") or None
    qdrant_collection: str = os.getenv("QDRANT_COLLECTION", "leadai_company_knowledge")

    # ---- Cache ------------------------------------------------------------
    redis_url: str | None = os.getenv("REDIS_URL") or None

    # ---- PII encryption ---------------------------------------------------
    # Fernet key. Derived from JWT_SECRET_KEY when unset so the demo boots.
    pii_key: str | None = os.getenv("LEADAI_PII_KEY") or None

    # ---- Chat widget session tokens --------------------------------------
    chat_jwt_secret: str = os.getenv(
        "LEADAI_CHAT_JWT_SECRET",
        os.getenv("JWT_SECRET_KEY", os.getenv("SESSION_SECRET", "demo-secret-key")),
    )
    chat_jwt_algorithm: str = os.getenv("LEADAI_CHAT_JWT_ALG", "HS256")
    chat_session_minutes: int = _i("LEADAI_CHAT_SESSION_MINUTES", 1440)
    chat_jwt_leeway: int = _i("LEADAI_CHAT_JWT_LEEWAY", 30)
    chat_rate_limit_per_min: int = _i("LEADAI_CHAT_RATE_LIMIT", 30)

    # ---- Telephony: Exotel ------------------------------------------------
    exotel_sid: str | None = os.getenv("EXOTEL_SID") or None
    exotel_api_key: str | None = os.getenv("EXOTEL_API_KEY") or None
    exotel_api_token: str | None = os.getenv("EXOTEL_API_TOKEN") or None
    exotel_caller_id: str | None = os.getenv("EXOTEL_CALLER_ID") or None
    exotel_subdomain: str = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")
    exotel_flow_app_id: str | None = os.getenv("EXOTEL_FLOW_APP_ID") or None

    # Which carrier the lead-AI voice endpoints should use.
    #   "auto"   -> exotel when credentials exist, else the existing twilio leg
    #   "exotel" -> force exotel
    #   "twilio" -> force the already-working outbound twilio pipeline
    voice_provider: str = os.getenv("LEADAI_VOICE_PROVIDER", "auto").lower()

    # ---- Sarvam (STT/TTS) -------------------------------------------------
    sarvam_api_key: str | None = os.getenv("SARVAM_API_KEY") or None
    default_language: str = os.getenv("LEADAI_DEFAULT_LANGUAGE", "en-IN")

    # ---- Files ------------------------------------------------------------
    max_upload_bytes: int = _i("LEADAI_MAX_UPLOAD_BYTES", 15 * 1024 * 1024)

    # ---- Behaviour flags --------------------------------------------------
    # When true, a Admin may read across every company.
    strict_tenant_isolation: bool = _b("LEADAI_STRICT_TENANT_ISOLATION", "true")

    # =======================================================================
    # Social channels (Meta: WhatsApp Cloud API, Messenger, Instagram)
    # =======================================================================
    # These are the PLATFORM-level fallbacks. Per-company credentials live in
    # `leadai_channel_accounts` and always win — env vars only exist so a single
    # -tenant deployment or a local test can work without touching the DB.
    meta_app_secret: str | None = os.getenv("META_APP_SECRET") or None
    meta_app_id: str | None = os.getenv("META_APP_ID") or None
    facebook_redirect_uri: str | None = os.getenv("FACEBOOK_REDIRECT_URI") or None
    meta_verify_token: str = os.getenv("META_VERIFY_TOKEN", "leadai-verify")
    meta_graph_version: str = os.getenv("META_GRAPH_VERSION", "v21.0")
    meta_graph_base: str = os.getenv("META_GRAPH_BASE", "https://graph.facebook.com")

    # ---- standalone Instagram (Instagram API with Instagram Login) --------- #
    # These come from a SECOND Meta app configured with "API setup with
    # Instagram Login". Meta permits only one API setup per app, so these are
    # necessarily different values from META_APP_ID / META_APP_SECRET above —
    # reusing the Facebook ones is the most common reason the flow fails with an
    # opaque OAuth error.
    instagram_app_id: str | None = os.getenv("INSTAGRAM_APP_ID") or None
    instagram_app_secret: str | None = os.getenv("INSTAGRAM_APP_SECRET") or None
    instagram_redirect_uri: str | None = os.getenv("INSTAGRAM_REDIRECT_URI") or None
    # Instagram webhooks are verified with the Instagram app secret, not the
    # Facebook one. Falls back to META_APP_SECRET only so an existing
    # Page-linked setup keeps working unchanged.
    instagram_verify_token: str = os.getenv(
        "INSTAGRAM_VERIFY_TOKEN", os.getenv("META_VERIFY_TOKEN", "leadai-verify")
    )
    instagram_graph_base: str = os.getenv(
        "INSTAGRAM_GRAPH_BASE", "https://graph.instagram.com"
    )
    instagram_graph_version: str = os.getenv("INSTAGRAM_GRAPH_VERSION", "v23.0")
    meta_access_token: str | None = os.getenv("META_ACCESS_TOKEN") or None
    whatsapp_phone_number_id: str | None = os.getenv("WHATSAPP_PHONE_NUMBER_ID") or None
    # Reject webhook payloads whose X-Hub-Signature-256 does not verify. Leave
    # ON in production; turning it off is only for local tunnelling experiments.
    meta_verify_signatures: bool = _b("META_VERIFY_SIGNATURES", "true")
    # Meta only permits free-form replies within 24h of the user's last message.
    meta_session_window_hours: int = _i("META_SESSION_WINDOW_HOURS", 24)
    # Master switch — lets you keep web chat only until the Meta app is approved.
    social_channels_enabled: bool = _b("LEADAI_SOCIAL_CHANNELS_ENABLED", "true")
    web_chat_enabled: bool = _b("LEADAI_WEB_CHAT_ENABLED", "true")

    # ---- SMS / email fallback for campaigns -------------------------------
    twilio_sms_from: str | None = os.getenv("TWILIO_SMS_FROM") or None
    smtp_host: str | None = os.getenv("SMTP_HOST") or None
    smtp_port: int = _i("SMTP_PORT", 587)
    smtp_user: str | None = os.getenv("SMTP_USER") or None
    smtp_password: str | None = os.getenv("SMTP_PASSWORD") or None
    smtp_from: str | None = os.getenv("SMTP_FROM") or None
    smtp_use_tls: bool = _b("SMTP_USE_TLS", "true")

    # =======================================================================
    # Lead threshold
    # =======================================================================
    # Global default for "score at which a lead reaches the sales dashboard".
    # Overridden per company by LeadCompanySettings.LeadScoreThreshold.
    lead_score_threshold: int = _i("LEADAI_LEAD_THRESHOLD", 50)
    # 0 disables automatic lead -> CRM account promotion.
    auto_convert_threshold: int = _i("LEADAI_AUTO_CONVERT_THRESHOLD", 0)

    # =======================================================================
    # Campaigns
    # =======================================================================
    campaign_max_recipients: int = _i("LEADAI_CAMPAIGN_MAX_RECIPIENTS", 100_000)
    campaign_default_concurrency: int = _i("LEADAI_CAMPAIGN_CONCURRENCY", 5)
    campaign_default_rate_per_minute: int = _i("LEADAI_CAMPAIGN_RATE_PER_MIN", 60)
    campaign_max_retries: int = _i("LEADAI_CAMPAIGN_MAX_RETRIES", 1)
    campaign_batch_size: int = _i("LEADAI_CAMPAIGN_BATCH_SIZE", 200)
    # Quiet hours (local) applied when a campaign does not set its own. India's
    # TRAI rules bar promotional contact outside 09:00–21:00.
    quiet_hours_start: int = _i("LEADAI_QUIET_HOURS_START", 9)
    quiet_hours_end: int = _i("LEADAI_QUIET_HOURS_END", 21)
    default_timezone: str = os.getenv("LEADAI_TIMEZONE", "Asia/Kolkata")
    campaign_dry_run: bool = _b("LEADAI_CAMPAIGN_DRY_RUN", "false")

    # =======================================================================
    # Background worker
    # =======================================================================
    # Only ONE process should drain the leadai_jobs queue when you scale uvicorn
    # horizontally; set LEADAI_WORKER_ENABLED=false on the extra web pods and
    # run a dedicated worker container instead (see docker-compose).
    worker_enabled: bool = _b("LEADAI_WORKER_ENABLED", "true")
    worker_poll_seconds: float = _f("LEADAI_WORKER_POLL_SECONDS", 3.0)
    worker_concurrency: int = _i("LEADAI_WORKER_CONCURRENCY", 4)
    worker_id: str = os.getenv("LEADAI_WORKER_ID", "") or ""

    # =======================================================================
    # Object storage (MinIO) — document store for KB files, campaign lists,
    # campaign media and exports. Reuses the same server the outbound app
    # already uses for call recordings, in separate buckets.
    # =======================================================================
    minio_endpoint: str | None = os.getenv("MINIO_ENDPOINT") or None
    minio_public_endpoint: str | None = os.getenv("MINIO_PUBLIC_ENDPOINT") or None
    minio_access_key: str | None = os.getenv("MINIO_ACCESS_KEY") or None
    minio_secret_key: str | None = os.getenv("MINIO_SECRET_KEY") or None
    minio_region: str = os.getenv("MINIO_REGION", "us-east-1")
    minio_secure: bool = _b("MINIO_SECURE", "false")
    minio_bucket_documents: str = os.getenv("MINIO_BUCKET_DOCUMENTS", "leadai-documents")
    minio_bucket_campaigns: str = os.getenv("MINIO_BUCKET_CAMPAIGNS", "leadai-campaigns")
    minio_bucket_exports: str = os.getenv("MINIO_BUCKET_EXPORTS", "leadai-exports")
    minio_presign_seconds: int = _i("MINIO_PRESIGN_SECONDS", 3600)
    # Where files go when MinIO is not configured, so dev still works.
    local_storage_dir: str = os.getenv("LEADAI_LOCAL_STORAGE_DIR", "./storage_local")

    @property
    def minio_enabled(self) -> bool:
        return bool(self.minio_endpoint and self.minio_access_key and self.minio_secret_key)

    @property
    def meta_enabled(self) -> bool:
        """Platform-level Meta fallback. Per-company accounts work regardless."""
        return bool(self.meta_access_token and self.social_channels_enabled)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def exotel_enabled(self) -> bool:
        return bool(self.exotel_sid and self.exotel_api_key and self.exotel_api_token)

    @property
    def effective_voice_provider(self) -> str:
        if self.voice_provider == "exotel":
            return "exotel"
        if self.voice_provider == "twilio":
            return "twilio"
        return "exotel" if self.exotel_enabled else "twilio"


@lru_cache
def get_settings() -> LeadAISettings:
    return LeadAISettings()


settings = get_settings()
