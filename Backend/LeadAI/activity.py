"""
Activity / audit logging.

One helper, used by every mutating route. Two rules make the log trustworthy:

  1. It never raises. A logging failure must not roll back the business
     transaction that succeeded — so writes are wrapped and, if the caller's
     session is already poisoned, retried on a fresh session.
  2. It records the actor's ROLE at the time of the action, not just the email.
     Roles change; the log should still explain why the action was permitted.

`log()` accepts an open session and does NOT commit by default, so an action and
its audit row land in the same transaction. Pass commit=True from places that
own their transaction (background tasks, the call bridge).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from .models import LeadActivityLog

logger = logging.getLogger(__name__)

# Canonical action names. Kept as constants so the UI can filter on a stable set
# and so typos don't silently create a new action.
class A:
    # auth / rbac
    ROLE_GRANTED = "role.granted"
    ROLE_REVOKED = "role.revoked"
    ROLE_UPDATED = "role.updated"
    PERMISSION_UPDATED = "permission.updated"
    PERMISSION_DENIED = "permission.denied"

    # company
    COMPANY_CREATED = "company.created"
    COMPANY_UPDATED = "company.updated"
    COMPANY_DEACTIVATED = "company.deactivated"
    SETTINGS_UPDATED = "company.settings_updated"

    # knowledge base
    KB_UPLOADED = "kb.document_uploaded"
    KB_INDEXED = "kb.document_indexed"
    KB_INDEX_FAILED = "kb.document_index_failed"
    KB_DELETED = "kb.document_deleted"
    KB_REINDEXED = "kb.document_reindexed"
    KB_TESTED = "kb.retrieval_tested"

    # scripts / prompts
    SCRIPT_CREATED = "script.created"
    SCRIPT_UPDATED = "script.updated"
    SCRIPT_DELETED = "script.deleted"
    SCRIPT_ACTIVATED = "script.set_default"
    PROMPT_UPDATED = "prompt.updated"
    PROMPT_RESET = "prompt.reset"

    # chat / leads
    CHAT_STARTED = "chat.session_started"
    CHAT_MESSAGE = "chat.customer_message"
    AI_REPLIED = "chat.ai_replied"
    AI_HANDOFF = "chat.ai_requested_human"
    AGENT_REPLIED = "chat.agent_replied"
    LEAD_QUALIFIED = "lead.qualified"
    LEAD_ASSIGNED = "lead.assigned"
    LEAD_UNASSIGNED = "lead.unassigned"
    LEAD_STATUS_CHANGED = "lead.status_changed"
    LEAD_EXPORTED = "lead.exported"
    PII_REVEALED = "lead.pii_revealed"

    # user management
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_DELETED = "user.deleted"

    # lead threshold
    LEAD_THRESHOLD_CROSSED = "lead.threshold_crossed"
    LEAD_CONVERTED = "lead.converted"

    # social channels
    CHANNEL_CONNECTED = "channel.connected"
    CHANNEL_UPDATED = "channel.updated"
    CHANNEL_DISCONNECTED = "channel.disconnected"
    CHANNEL_INBOUND = "channel.inbound_received"
    CHANNEL_SEND_FAILED = "channel.send_failed"
    CHANNEL_OPTED_OUT = "channel.opted_out"

    # contact lists
    LIST_CREATED = "list.created"
    LIST_IMPORTED = "list.imported"
    LIST_DELETED = "list.deleted"

    # campaigns
    CAMPAIGN_CREATED = "campaign.created"
    CAMPAIGN_UPDATED = "campaign.updated"
    CAMPAIGN_BUILT = "campaign.audience_built"
    CAMPAIGN_STARTED = "campaign.started"
    CAMPAIGN_PAUSED = "campaign.paused"
    CAMPAIGN_RESUMED = "campaign.resumed"
    CAMPAIGN_CANCELLED = "campaign.cancelled"
    CAMPAIGN_COMPLETED = "campaign.completed"
    CAMPAIGN_FAILED = "campaign.failed"

    # CRM accounts
    ACCOUNT_CREATED = "account.created"
    ACCOUNT_UPDATED = "account.updated"
    ACCOUNT_DELETED = "account.deleted"
    ACCOUNT_NOTE_ADDED = "account.note_added"
    ACCOUNT_STAGE_CHANGED = "account.stage_changed"
    ACCOUNT_CONTACTED = "account.contacted"

    # files
    FILE_UPLOADED = "file.uploaded"
    FILE_DOWNLOADED = "file.downloaded"
    FILE_DELETED = "file.deleted"

    # voice
    CALL_INITIATED = "call.initiated"
    CALL_FAILED = "call.failed"
    CALL_COMPLETED = "call.completed"
    CALL_SYNCED = "call.transcript_synced"


def log(
    db: Session,
    *,
    action: str,
    client_id: str | None = None,
    actor_email: str | None = None,
    actor_role: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    message: str = "",
    meta: dict[str, Any] | None = None,
    log_type: str = "Info",
    request: Request | None = None,
    commit: bool = False,
) -> None:
    """Append one activity row. Never raises."""
    ip = user_agent = None
    if request is not None:
        try:
            raw_ip = request.headers.get("x-forwarded-for") or (
                request.client.host if request.client else None
            )
            if raw_ip and "," in raw_ip:
                raw_ip = raw_ip.split(",")[0].strip()
            ip = (raw_ip or None)
            ua = request.headers.get("user-agent")
            user_agent = ua[:300] if ua else None
        except Exception:  # noqa: BLE001
            pass

    row = LeadActivityLog(
        ClientId=client_id,
        ActorEmail=(actor_email or "system")[:200],
        ActorRole=actor_role,
        Action=action,
        LogType=log_type,
        EntityType=entity_type,
        EntityId=(str(entity_id)[:64] if entity_id else None),
        LogMessage=(message or action)[:2000],
        MetaJson=_safe_meta(meta),
        IpAddress=ip,
        UserAgent=user_agent,
        CreatedBy=(actor_email or "system")[:100],
    )
    try:
        db.add(row)
        if commit:
            db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LeadAI activity] primary write failed for %s: %s", action, exc)
        _fallback(row, action)


def log_principal(
    db: Session,
    principal,
    *,
    action: str,
    client_id: str | None = None,
    **kwargs,
) -> None:
    """Same as log() but fills actor fields from a rbac.Principal."""
    log(
        db,
        action=action,
        client_id=client_id if client_id is not None else principal.client_id,
        actor_email=principal.email,
        actor_role=principal.role,
        **kwargs,
    )


def _safe_meta(meta: dict[str, Any] | None) -> dict | None:
    """Keep the log JSON-serialisable and free of secrets/PII.

    Anything whose key looks like a credential or a raw contact detail is
    replaced — the audit log records THAT a phone number was revealed, never the
    number itself.
    """
    if not meta:
        return None
    redact = ("password", "token", "secret", "api_key", "authorization",
              "phone", "email_raw", "whatsapp", "instagram")
    out: dict[str, Any] = {}
    for key, value in meta.items():
        lowered = key.lower()
        if any(word in lowered for word in redact):
            out[key] = "[redacted]"
            continue
        try:
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[key] = value[:500] if isinstance(value, str) else value
            elif isinstance(value, (list, tuple)):
                out[key] = [str(v)[:200] for v in value[:20]]
            elif isinstance(value, dict):
                out[key] = {k: str(v)[:200] for k, v in list(value.items())[:20]}
            else:
                out[key] = str(value)[:200]
        except Exception:  # noqa: BLE001
            out[key] = "[unserialisable]"
    return out


def _fallback(row: LeadActivityLog, action: str) -> None:
    """The caller's session is unusable — write on a fresh one so the trail
    survives a failed business transaction."""
    from .db import session

    fresh = session()
    try:
        fresh.add(
            LeadActivityLog(
                ClientId=row.ClientId,
                ActorEmail=row.ActorEmail,
                ActorRole=row.ActorRole,
                Action=row.Action,
                LogType=row.LogType,
                EntityType=row.EntityType,
                EntityId=row.EntityId,
                LogMessage=row.LogMessage,
                MetaJson=row.MetaJson,
                IpAddress=row.IpAddress,
                UserAgent=row.UserAgent,
                CreatedBy=row.CreatedBy,
            )
        )
        fresh.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("[LeadAI activity] LOST audit row for %s: %s", action, exc)
        fresh.rollback()
    finally:
        fresh.close()
