"""
LeadAI — Token & AI Usage Tracking Data Models.

Additive and multi-tenant:
- `leadai_ai_usage_events`: Raw granular usage event row per LLM, embedding, voice stream, or image generation.
  Strictly stores ONLY token metrics, provider, model, process, and cost. Prompts, completions, and customer content are NEVER stored.
- `leadai_ai_usage_daily_aggregates`: Daily aggregate rollups per (ClientId, Process, Model, UsageDate) computed in IST.

Multi-tenant scoping:
- Tenant calls store tenant's `ClientId`.
- System/platform calls (e.g. daily topic picker, pre-warm ping, system warmup) store `ClientId = NULL`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

try:
    from core.base import Base
except ImportError:
    from core.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LeadAIUsageEvent(Base):
    """Granular event log of an AI model invocation."""

    __tablename__ = "leadai_ai_usage_events"
    __table_args__ = (
        Index("ix_leadai_usage_client_created", "ClientId", "CreatedAt"),
        Index("ix_leadai_usage_client_proc_created", "ClientId", "Process", "CreatedAt"),
        Index("ix_leadai_usage_created_at", "CreatedAt"),
    )

    Id = Column(String(36), primary_key=True, default=_uuid, unique=True, nullable=False)
    ClientId = Column(String(36), nullable=True, index=True)  # NULL for System/Platform
    Process = Column(String(64), nullable=False, index=True)
    Channel = Column(String(32), nullable=True)  # chat, voice, blog, social, knowledge_base, system
    Provider = Column(String(32), nullable=False, default="openai")  # openai, sarvam, sentence-transformers
    Model = Column(String(64), nullable=False)
    InputTokens = Column(Integer, default=0, nullable=False)
    OutputTokens = Column(Integer, default=0, nullable=False)
    TotalTokens = Column(Integer, default=0, nullable=False)
    CostUsd = Column(Float, nullable=True)  # NULL if price unknown
    UnitPriceInputPer1M = Column(Float, nullable=True)
    UnitPriceOutputPer1M = Column(Float, nullable=True)
    PriceStatus = Column(String(20), default="ok", nullable=False)  # ok, price_unknown, free
    IsEstimated = Column(Boolean, default=False, nullable=False)  # For dropped voice streams
    IsPrewarm = Column(Boolean, default=False, nullable=False)  # Synthetic prewarm checks
    ConversationId = Column(String(64), nullable=True)
    CreatedAt = Column(DateTime, default=utcnow, index=True, nullable=False)


class LeadAIUsageDailyAggregate(Base):
    """Daily aggregated token and cost totals aggregated in IST (Asia/Kolkata)."""

    __tablename__ = "leadai_ai_usage_daily_aggregates"
    __table_args__ = (
        UniqueConstraint("ClientId", "Process", "Model", "UsageDate", name="uq_leadai_usage_daily_agg"),
        Index("ix_leadai_usage_daily_client_date", "ClientId", "UsageDate"),
    )

    Id = Column(String(36), primary_key=True, default=_uuid, unique=True, nullable=False)
    ClientId = Column(String(36), nullable=True, index=True)  # NULL for System/Platform
    Process = Column(String(64), nullable=False, index=True)
    Model = Column(String(64), nullable=False)
    UsageDate = Column(String(10), nullable=False, index=True)  # 'YYYY-MM-DD'
    TotalRequests = Column(Integer, default=0, nullable=False)
    TotalInputTokens = Column(Integer, default=0, nullable=False)
    TotalOutputTokens = Column(Integer, default=0, nullable=False)
    TotalTokens = Column(Integer, default=0, nullable=False)
    TotalCostUsd = Column(Float, default=0.0, nullable=False)
    HasUnknownPrice = Column(Boolean, default=False, nullable=False)
    UpdatedAt = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


ALL_LEADAI_USAGE_TABLES = (
    LeadAIUsageEvent,
    LeadAIUsageDailyAggregate,
)
