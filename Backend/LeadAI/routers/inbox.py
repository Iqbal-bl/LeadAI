"""
The staff inbox: list, read, assign, reply, close, and reveal contact details.

WHERE RBAC ACTUALLY BITES
-------------------------
Three separate restrictions apply here, and they are enforced at different layers
on purpose:

  1. PERMISSION (route level) — can this role reply/assign/reveal at all?
  2. TENANT SCOPE (query level) — every query is filtered by the resolved
     ClientId, so a company's inbox is a company's inbox.
  3. ROW-LEVEL VISIBILITY (query level) — an `agent` sees only conversations
     assigned to them. This is the reason `_visible()` exists and why nothing in
     this module fetches a conversation by primary key without going through
     `_load()`.

Note the deliberate use of 404 rather than 403 in `_load()`: telling an agent
"that conversation exists but isn't yours" leaks the existence of other leads.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from Domain.models import Client

from .. import activity
from ..activity import A
from ..db import get_leadai_db
from ..models import (
    Lead,
    LeadCall,
    LeadChannelIdentity,
    LeadConversation,
    LeadCustomer,
    LeadMessage,
    LeadUserRole,
    utcnow,
)
from ..rbac import Principal, require, resolve_scope
from ..schemas import (
    AgentReply,
    AssignRequest,
    CallConversationDetail,
    ContactReveal,
    ChatConversationDetail,
    ConversationDetail,
    ConversationListOut,
    ConversationOut,
    DeliveryOut,
    Ok,
    SocialIdentityOut,
    StatusRequest,
)
from ..security import decrypt_pii
from ..serializers import (
    call_conversation_detail,
    chat_conversation_detail,
    conversation_detail,
    conversation_out,
)
from ..services import ai_engine, conversation_flow

try:
    from Websockets.connection import manager as ws_manager, _fire_and_forget
except Exception:  # noqa: BLE001
    ws_manager = None  # type: ignore[assignment]
    def _fire_and_forget(coro):  # type: ignore[misc]
        pass

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/inbox", tags=["LeadAI • Inbox & leads"])


def _company(db: Session, client_id: str) -> Client:
    client = db.get(Client, client_id)
    if not client or client.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    return client


def _visible(db: Session, principal: Principal, client_id: str):
    """Base query with tenant scope AND row-level visibility applied."""
    query = db.query(LeadConversation).filter(
        LeadConversation.ClientId == client_id,
        LeadConversation.IsDeleted == False,  # noqa: E712
    )
    if principal.sees_only_assigned:
        query = query.filter(LeadConversation.AssignedUserEmail == principal.email.lower())
    return query


def _load(
    db: Session, conversation_id: str, principal: Principal, client_id: str
) -> LeadConversation:
    conversation = (
        _visible(db, principal, client_id)
        .filter(LeadConversation.Id == conversation_id)
        .one_or_none()
    )
    if conversation is None:
        # 404, not 403 — see module docstring.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


# ===========================================================================
# listing
# ===========================================================================


@router.get(
    "",
    response_model=ConversationListOut,
    summary="List conversations (filtered by role, company and query)",
)
def list_conversations(
    status_filter: str | None = Query(
        default=None, alias="status", pattern="^(open|needs_human|assigned|closed)$"
    ),
    lead_status: str | None = Query(
        default=None, pattern="^(cold|warm|hot|qualified|lost)$"
    ),
    channel: str | None = Query(
        default=None,
        pattern="^(web|whatsapp|messenger|instagram|voice|sms|email)$",
        description="Filter by the channel the conversation arrived on.",
    ),
    assigned_to: str | None = Query(
        default=None, description="An email, or 'me', or 'unassigned'"
    ),
    search: str | None = Query(default=None, description="Matches customer ref or summary"),
    min_score: int | None = Query(default=None, ge=0, le=100),
    above_threshold: bool | None = Query(
        default=None,
        description=(
            "true  = only leads at or above the company's score threshold "
            "(what the sales dashboard shows by default); "
            "false = only the ones still below it; "
            "omit  = the company's HideBelowThreshold setting decides."
        ),
    ),
    campaign_id: str | None = Query(
        default=None, description="Only conversations produced by this campaign."
    ),
    sort: str = Query(default="recent", pattern="^(recent|score|oldest)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    principal: Principal = Depends(require("lead.read.all", "lead.read.assigned")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    query = _visible(db, principal, client_id)

    # An agent's view is already pinned to themselves, so the assigned_to filter
    # is only meaningful for roles that can see the whole company.
    if not principal.sees_only_assigned and assigned_to:
        if assigned_to == "unassigned":
            query = query.filter(LeadConversation.AssignedUserEmail.is_(None))
        elif assigned_to == "me":
            query = query.filter(
                LeadConversation.AssignedUserEmail == principal.email.lower()
            )
        else:
            query = query.filter(LeadConversation.AssignedUserEmail == assigned_to.lower())

    if status_filter:
        query = query.filter(LeadConversation.Status == status_filter)
    if channel:
        query = query.filter(LeadConversation.Channel == channel)

    if campaign_id:
        query = query.filter(LeadConversation.CampaignId == campaign_id)

    # --- lead-score threshold ------------------------------------------------
    # `IsAboveThreshold` is denormalised onto the lead row and indexed, so this
    # stays a single indexed predicate instead of a per-row settings lookup.
    # An explicit query parameter always wins; when it is omitted, the company's
    # HideBelowThreshold setting decides what the default view shows.
    effective_above = above_threshold
    if effective_above is None:
        from ..services import conversation_flow

        cfg = conversation_flow.company_settings(db, client_id)
        if cfg is not None and cfg.HideBelowThreshold:
            effective_above = True

    needs_lead_join = (
        lead_status or min_score is not None or effective_above is not None
    )
    if needs_lead_join:
        query = query.join(Lead, Lead.ConversationId == LeadConversation.Id)
        if lead_status:
            query = query.filter(Lead.Status == lead_status)
        if min_score is not None:
            query = query.filter(Lead.Score >= min_score)
        if effective_above is not None:
            query = query.filter(Lead.IsAboveThreshold == bool(effective_above))

    if search:
        term = f"%{search.lower()}%"
        query = query.join(LeadCustomer, LeadCustomer.Id == LeadConversation.CustomerId).filter(
            or_(
                LeadCustomer.PublicRef.ilike(term),
                LeadConversation.Summary.ilike(term),
            )
        )

    total = query.count()

    if sort == "score":
        # Sorting by lead score needs the join even when not filtering on it.
        if not needs_lead_join:
            query = query.join(Lead, Lead.ConversationId == LeadConversation.Id)
        query = query.order_by(Lead.Score.desc(), LeadConversation.LastMessageAt.desc())
    elif sort == "oldest":
        query = query.order_by(LeadConversation.CreatedAt.asc())
    else:
        query = query.order_by(LeadConversation.LastMessageAt.desc())

    rows = query.limit(page_size).offset((page - 1) * page_size).all()

    return ConversationListOut(
        total_items=total,
        page=page,
        page_size=page_size,
        items=[conversation_out(db, c, principal) for c in rows],
    )


@router.get(
    "/queue",
    response_model=list[ConversationOut],
    summary="Conversations waiting for a human, hottest first",
)
def handoff_queue(
    limit: int = Query(default=25, le=100),
    principal: Principal = Depends(require("lead.read.all", "lead.read.assigned")),
    db: Session = Depends(get_leadai_db),
):
    """The work queue an agent lands on: the AI has escalated these and nobody has
    picked them up. Ordered by lead score so the most valuable is worked first."""
    client_id = resolve_scope(principal)
    rows = (
        _visible(db, principal, client_id)
        .join(Lead, Lead.ConversationId == LeadConversation.Id)
        .filter(LeadConversation.Status == "needs_human")
        .order_by(Lead.Score.desc(), LeadConversation.LastMessageAt.asc())
        .limit(limit)
        .all()
    )
    return [conversation_out(db, c, principal) for c in rows]


@router.get(
    "/{conversation_id}",
    response_model=ChatConversationDetail,
    summary="Chat conversation with messages (call info reduced to flag)",
)
def get_conversation(
    conversation_id: str,
    principal: Principal = Depends(require("lead.read.all", "lead.read.assigned")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    conversation = _load(db, conversation_id, principal, client_id)
    return chat_conversation_detail(db, conversation, principal)


@router.get(
    "/{conversation_id}/call",
    response_model=CallConversationDetail,
    summary="Call conversation with full transcript and call metadata",
)
def get_call_conversation(
    conversation_id: str,
    principal: Principal = Depends(require("lead.read.all", "lead.read.assigned")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    conversation = _load(db, conversation_id, principal, client_id)
    return call_conversation_detail(db, conversation, principal)


# ===========================================================================
# actions
# ===========================================================================


@router.post(
    "/{conversation_id}/assign",
    response_model=ConversationDetail,
    summary="Assign (or unassign) a conversation",
)
def assign(
    conversation_id: str,
    payload: AssignRequest,
    request: Request,
    principal: Principal = Depends(require("lead.assign")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    conversation = _load(db, conversation_id, principal, client_id)
    previous = conversation.AssignedUserEmail

    if payload.user_email is None:
        conversation.AssignedUserEmail = None
        conversation.AssignedAt = None
        # Unassigning returns it to the queue rather than to 'open': the reason it
        # needed a human has not gone away.
        if conversation.Status == "assigned":
            conversation.Status = "needs_human"
        action, message = A.LEAD_UNASSIGNED, "Unassigned conversation"
    else:
        target = payload.user_email.lower()
        # The assignee must hold an active grant in THIS company — otherwise a
        # lead could be parked with someone who cannot open it.
        grant = (
            db.query(LeadUserRole)
            .filter(
                LeadUserRole.UserEmail == target,
                LeadUserRole.ClientId == client_id,
                LeadUserRole.IsActive == True,  # noqa: E712
                LeadUserRole.IsDeleted == False,  # noqa: E712
            )
            .first()
        )
        if not grant:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "That user does not have access to this company",
            )
        conversation.AssignedUserEmail = target
        conversation.AssignedAt = utcnow()
        conversation.Status = "assigned"
        action, message = A.LEAD_ASSIGNED, f"Assigned conversation to {target}"

        db.add(
            LeadMessage(
                ClientId=client_id,
                ConversationId=conversation.Id,
                Sender="system",
                Content=f"Conversation assigned to {target} by {principal.email}.",
                CreatedBy=principal.email,
            )
        )

    conversation.UpdatedBy = principal.email
    conversation.UpdatedAt = utcnow()

    activity.log_principal(
        db,
        principal,
        action=action,
        client_id=client_id,
        entity_type="conversation",
        entity_id=conversation.Id,
        message=message,
        meta={"from": previous, "to": conversation.AssignedUserEmail},
        request=request,
    )
    db.commit()
    db.refresh(conversation)
    return conversation_detail(db, conversation, principal)


@router.post(
    "/{conversation_id}/claim",
    response_model=ConversationDetail,
    summary="Claim an unassigned conversation for myself",
)
def claim(
    conversation_id: str,
    request: Request,
    principal: Principal = Depends(require("lead.reply")),
    db: Session = Depends(get_leadai_db),
):
    """Self-service pickup from the queue. An agent cannot ASSIGN (that needs
    lead.assign) but must be able to take unclaimed work, or the queue only moves
    when a manager is online."""
    client_id = resolve_scope(principal)
    conversation = (
        db.query(LeadConversation)
        .filter(
            LeadConversation.Id == conversation_id,
            LeadConversation.ClientId == client_id,
            LeadConversation.IsDeleted == False,  # noqa: E712
        )
        .one_or_none()
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    if conversation.AssignedUserEmail and conversation.AssignedUserEmail != principal.email.lower():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Already being handled by {conversation.AssignedUserEmail}",
        )

    conversation.AssignedUserEmail = principal.email.lower()
    conversation.AssignedAt = utcnow()
    conversation.Status = "assigned"
    conversation.UpdatedBy = principal.email
    conversation.UpdatedAt = utcnow()

    activity.log_principal(
        db,
        principal,
        action=A.LEAD_ASSIGNED,
        client_id=client_id,
        entity_type="conversation",
        entity_id=conversation.Id,
        message=f"{principal.email} claimed this conversation",
        request=request,
    )
    db.commit()
    db.refresh(conversation)
    return conversation_detail(db, conversation, principal)


@router.post(
    "/{conversation_id}/reply",
    response_model=ConversationDetail,
    summary="Send an agent message into the conversation",
)
def reply(
    conversation_id: str,
    payload: AgentReply,
    request: Request,
    principal: Principal = Depends(require("lead.reply")),
    db: Session = Depends(get_leadai_db),
):
    """A human types into the SAME conversation the AI was using, so the customer
    experiences one continuous thread. Replying implicitly takes ownership —
    otherwise two agents can silently answer the same customer."""
    client_id = resolve_scope(principal)
    client = _company(db, client_id)
    conversation = _load(db, conversation_id, principal, client_id)

    agent_message = LeadMessage(
        ClientId=client_id,
        ConversationId=conversation.Id,
        Sender="agent",
        SenderEmail=principal.email,
        Content=payload.message.strip(),
        CreatedBy=principal.email,
    )
    db.add(agent_message)
    db.flush()
    conversation.LastMessageAt = utcnow()
    if conversation.Status in ("open", "needs_human"):
        conversation.Status = "assigned"
        conversation.AssignedUserEmail = conversation.AssignedUserEmail or principal.email.lower()
        conversation.AssignedAt = conversation.AssignedAt or utcnow()

    messages = (
        db.query(LeadMessage)
        .filter(
            LeadMessage.ConversationId == conversation.Id,
            LeadMessage.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadMessage.CreatedAt.asc())
        .all()
    )
    conversation.MessageCount = len(messages)

    # Re-summarise so the handoff card stays current for whoever reads it next.
    lead = db.query(Lead).filter(Lead.ConversationId == conversation.Id).one_or_none()
    if lead:
        conversation.Summary, conversation.NextStep = ai_engine.summarize(
            db, client_id, client.Name, lead, messages
        )

    activity.log_principal(
        db,
        principal,
        action=A.AGENT_REPLIED,
        client_id=client_id,
        entity_type="conversation",
        entity_id=conversation.Id,
        message=f"Agent replied: {payload.message[:160]}",
        request=request,
    )
    db.commit()
    db.refresh(conversation)

    # ---- deliver to the customer's actual channel -------------------------
    #
    # THE BUG THIS FIXES
    # Everything above persists the reply and shows it in the dashboard. Nothing
    # above SENT it. For the web widget that was fine — the widget polls
    # /public/chat/messages, so a persisted row reaches the customer on its own.
    # For WhatsApp, Messenger and Instagram it was not: those are push channels,
    # and the message has to be handed to the Graph API explicitly.
    #
    # So inbound worked (webhook -> handle_customer_turn -> deliver), AI replies
    # worked (same path), and human replies from the inbox went nowhere. The
    # agent saw their message in the thread and reasonably assumed the customer
    # had it too.
    #
    # Delivery happens AFTER the commit, deliberately. Sending first and then
    # failing to commit would put a message on the customer's phone that does not
    # exist in our database — unrecoverable. This ordering can at worst leave a
    # persisted message undelivered, which is visible, retryable, and now
    # explicitly recorded on the row.
    delivery = conversation_flow.deliver(
        db, conversation, payload.message.strip(), message=agent_message
    )
    if delivery.status != "not_applicable":
        db.commit()

    if delivery.needs_attention:
        activity.log_principal(
            db,
            principal,
            action=A.AGENT_REPLIED,
            client_id=client_id,
            entity_type="conversation",
            entity_id=conversation.Id,
            message=f"Reply NOT delivered on {conversation.Channel}: {delivery.error}",
            meta={"delivery_status": delivery.status, "channel": conversation.Channel},
            log_type="Warning",
            request=request,
        )
        db.commit()
        logger.warning(
            "[LeadAI inbox] agent reply undelivered conv=%s channel=%s status=%s: %s",
            conversation.Id, conversation.Channel, delivery.status, delivery.error,
        )

    # Broadcast agent reply to the conversation WebSocket channel
    logger.info("[LeadAI] inbox broadcast: conv=%s ws_manager=%s", conversation.Id, ws_manager is not None)
    if ws_manager is not None:
        _fire_and_forget(
            ws_manager.broadcast_to_leadai_conversation(
                conversation.Id,
                {
                    "type": "message",
                    "sender": "agent",
                    "sender_email": principal.email,
                    "content": payload.message.strip(),
                    "conversation_id": conversation.Id,
                    "delivery_status": delivery.status,
                },
            )
        )

    detail = conversation_detail(db, conversation, principal)
    # Surfaced so the dashboard can show an undelivered reply as undelivered.
    # Optional field, so an existing client that ignores it is unaffected.
    detail.delivery = DeliveryOut(
        status=delivery.status,
        delivered=delivery.delivered,
        channel=conversation.Channel,
        message_id=delivery.message_id,
        error=delivery.error,
        detail=delivery.detail,
    )
    return detail


@router.post(
    "/{conversation_id}/status",
    response_model=ConversationDetail,
    summary="Change conversation status",
)
def set_status(
    conversation_id: str,
    payload: StatusRequest,
    request: Request,
    principal: Principal = Depends(require("lead.status")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    conversation = _load(db, conversation_id, principal, client_id)
    previous = conversation.Status

    conversation.Status = payload.status
    conversation.ClosedAt = utcnow() if payload.status == "closed" else None
    conversation.UpdatedBy = principal.email
    conversation.UpdatedAt = utcnow()

    activity.log_principal(
        db,
        principal,
        action=A.LEAD_STATUS_CHANGED,
        client_id=client_id,
        entity_type="conversation",
        entity_id=conversation.Id,
        message=f"Status {previous} -> {payload.status}",
        meta={"from": previous, "to": payload.status},
        request=request,
    )
    db.commit()
    db.refresh(conversation)
    return conversation_detail(db, conversation, principal)


@router.get(
    "/{conversation_id}/contact",
    response_model=ContactReveal,
    summary="Reveal the customer's real contact details (audited)",
)
def reveal_contact(
    conversation_id: str,
    request: Request,
    principal: Principal = Depends(require("lead.reveal_pii")),
    db: Session = Depends(get_leadai_db),
):
    """Admin-only, and every call writes a Security-level audit row naming the
    actor. This is the ONLY endpoint that decrypts customer PII into a response.

    The audit row records the customer and the actor but NEVER the revealed
    values — activity._safe_meta redacts contact-shaped keys, so the log proves
    what happened without becoming a second copy of the data it protects.
    """
    client_id = resolve_scope(principal)
    conversation = _load(db, conversation_id, principal, client_id)
    customer = db.get(LeadCustomer, conversation.CustomerId)
    if not customer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer record not found")

    activity.log_principal(
        db,
        principal,
        action=A.PII_REVEALED,
        client_id=client_id,
        entity_type="customer",
        entity_id=customer.Id,
        message=(
            f"{principal.email} revealed contact details for {customer.PublicRef} "
            f"(conversation {conversation.Id})"
        ),
        meta={"customer_ref": customer.PublicRef, "conversation_id": conversation.Id},
        log_type="Security",
        request=request,
    )
    db.commit()

    identities = (
        db.query(LeadChannelIdentity)
        .filter(
            LeadChannelIdentity.CustomerId == customer.Id,
            LeadChannelIdentity.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadChannelIdentity.CreatedAt.asc())
        .all()
    )

    social = []
    for ident in identities:
        handle = ident.ProfileName
        profile_url = None
        if ident.Channel == "instagram" and handle:
            # Only a resolved username makes a working link; a raw IGSID does not.
            profile_url = f"https://instagram.com/{handle.lstrip('@')}"
        elif ident.Channel == "messenger" and ident.ExternalUserId:
            profile_url = f"https://m.me/{ident.ExternalUserId}"
        social.append(
            SocialIdentityOut(
                channel=ident.Channel,
                handle=handle or ident.ExternalUserId,
                profile_name=ident.ProfileName,
                external_user_id=ident.ExternalUserId,
                profile_url=profile_url,
                opted_out=bool(ident.OptedOut),
                last_message_at=ident.LastUserMessageAt,
            )
        )

    return ContactReveal(
        phone=decrypt_pii(customer.PhoneEnc),
        email=decrypt_pii(customer.EmailEnc),
        whatsapp=decrypt_pii(customer.WhatsAppEnc),
        instagram=decrypt_pii(customer.InstagramEnc),
        display_name=customer.DisplayName,
        social_identities=social,
        revealed_at=datetime.now(timezone.utc),
    )


@router.post(
    "/{conversation_id}/requalify",
    response_model=ConversationDetail,
    summary="Force re-scoring of this lead",
)
def requalify(
    conversation_id: str,
    request: Request,
    principal: Principal = Depends(require("lead.status")),
    db: Session = Depends(get_leadai_db),
):
    """Useful after a knowledge-base change: product detection reads the corpus,
    so a lead scored before an upload may now resolve its product correctly."""
    client_id = resolve_scope(principal)
    client = _company(db, client_id)
    conversation = _load(db, conversation_id, principal, client_id)

    messages = (
        db.query(LeadMessage)
        .filter(
            LeadMessage.ConversationId == conversation.Id,
            LeadMessage.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadMessage.CreatedAt.asc())
        .all()
    )
    lead = db.query(Lead).filter(Lead.ConversationId == conversation.Id).one_or_none()
    if lead is None:
        lead = Lead(ClientId=client_id, ConversationId=conversation.Id, CreatedBy=principal.email)
        db.add(lead)
        db.flush()

    before = (lead.Status, lead.Score)
    ai_engine.qualify(db, client_id, lead, messages)
    conversation.Summary, conversation.NextStep = ai_engine.summarize(
        db, client_id, client.Name, lead, messages
    )

    activity.log_principal(
        db,
        principal,
        action="lead.requalified",
        client_id=client_id,
        entity_type="lead",
        entity_id=lead.Id,
        message=f"Re-qualified: {before[0]}({before[1]}) -> {lead.Status}({lead.Score})",
        meta={"before_status": before[0], "before_score": before[1],
              "after_status": lead.Status, "after_score": lead.Score},
        request=request,
    )
    db.commit()
    db.refresh(conversation)
    return conversation_detail(db, conversation, principal)


@router.get("/export/leads", summary="Export leads as JSON rows")
def export_leads(
    lead_status: str | None = Query(default=None),
    limit: int = Query(default=1000, le=5000),
    request: Request = None,
    principal: Principal = Depends(require("lead.export")),
    db: Session = Depends(get_leadai_db),
):
    """Export is a separate permission from read, and is audited: a bulk pull of
    an entire lead list is exactly the action you want a record of.

    Contact details are NOT included — masked phone only. Exporting raw PII in
    bulk would defeat the reveal audit trail.
    """
    client_id = resolve_scope(principal)
    query = (
        db.query(LeadConversation, Lead)
        .join(Lead, Lead.ConversationId == LeadConversation.Id)
        .filter(
            LeadConversation.ClientId == client_id,
            LeadConversation.IsDeleted == False,  # noqa: E712
        )
    )
    if principal.sees_only_assigned:
        query = query.filter(LeadConversation.AssignedUserEmail == principal.email.lower())
    if lead_status:
        query = query.filter(Lead.Status == lead_status)

    rows = query.order_by(Lead.Score.desc()).limit(limit).all()

    items = []
    for conversation, lead in rows:
        customer = db.get(LeadCustomer, conversation.CustomerId)
        items.append(
            {
                "conversation_id": conversation.Id,
                "customer_ref": customer.PublicRef if customer else None,
                "channel": conversation.Channel,
                "conversation_status": conversation.Status,
                "assigned_to": conversation.AssignedUserEmail,
                "lead_status": lead.Status,
                "score": lead.Score,
                "interest": lead.Interest,
                "intent": lead.Intent,
                "budget": lead.Budget,
                "timeline": lead.Timeline,
                "product": lead.Product,
                "sentiment": lead.Sentiment,
                "summary": conversation.Summary,
                "next_step": conversation.NextStep,
                "created_at": conversation.CreatedAt,
                "last_message_at": conversation.LastMessageAt,
            }
        )

    activity.log_principal(
        db,
        principal,
        action=A.LEAD_EXPORTED,
        client_id=client_id,
        entity_type="lead_export",
        message=f"Exported {len(items)} leads",
        meta={"count": len(items), "lead_status_filter": lead_status},
        log_type="Security",
        request=request,
    )
    db.commit()
    return {"total_items": len(items), "items": items}
