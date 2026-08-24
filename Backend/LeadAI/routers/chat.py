"""
The public, customer-facing chat channel — where leads are generated.

AUTH MODEL (important, and different from the rest of the app)
-------------------------------------------------------------
These endpoints are reached by an end customer on a public website. They have no
identity-server account, so they are exempt from TokenValidationMiddleware (the
integration hook adds this prefix to PUBLIC_PATHS) and instead carry a
short-lived `X-Chat-Session` token that is bound to exactly ONE conversation id.
That token grants:
    read/write on its own conversation — and nothing else.
It is not a user token, carries no role, and cannot reach any staff endpoint.

WHY THIS WRITES TO THE SAME TABLES AS EVERY OTHER CHANNEL
---------------------------------------------------------
There is no `web_chats` table. A widget conversation, a WhatsApp conversation and
a phone call all land in `leadai_conversations` + `leadai_messages`. Adding Meta
WhatsApp/Instagram webhooks later is therefore an ADAPTER — translate the payload,
call `_handle_customer_turn()` — not a second pipeline with its own qualification
logic that will inevitably drift.

RATE LIMITING
-------------
Per-conversation, in the shared cache. A public endpoint that runs an LLM call
per request is an obvious cost-amplification target, so the limit is enforced
before any retrieval or generation happens.
"""
from __future__ import annotations

import logging
import random

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from Domain.models import Client

from .. import activity
from ..activity import A
from ..config import settings
from ..db import get_leadai_db
from ..models import (
    Lead,
    LeadCompanySettings,
    LeadConversation,
    LeadCustomer,
    LeadMessage,
    utcnow,
)
from ..schemas import (
    ChatReply,
    ChatSend,
    ChatSession,
    ChatStart,
    MessageOut,
    PublicCompanyOut,
)
from ..security import (
    create_chat_token,
    decode_chat_token,
    encrypt_pii,
    phone_fingerprint,
)
from ..serializers import message_out
from ..services import ai_engine, cache, conversation_flow, script_engine

try:
    from Websockets.connection import manager as ws_manager, _fire_and_forget
except Exception:  # noqa: BLE001
    ws_manager = None  # type: ignore[assignment]
    def _fire_and_forget(coro):  # type: ignore[misc]
        pass

logger = logging.getLogger(__name__)

# NOTE the prefix: everything under /public is unauthenticated by design.
router = APIRouter(prefix="/public", tags=["LeadAI • Public chat (widget)"])


# ---------------------------------------------------------------------------
# session resolution
# ---------------------------------------------------------------------------
def _session(
    x_chat_session: str | None = Header(default=None, alias="X-Chat-Session"),
    db: Session = Depends(get_leadai_db),
) -> LeadConversation:
    if not x_chat_session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Start a chat first")
    claims = decode_chat_token(x_chat_session)
    if not claims:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This chat session has expired")

    conversation = db.get(LeadConversation, claims["sub"])
    if not conversation or conversation.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    # Belt and braces: the token names its company, so a token cannot be replayed
    # against a conversation that was somehow re-parented.
    if claims.get("cid") and claims["cid"] != conversation.ClientId:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid chat session")
    return conversation


def _company(db: Session, client_id: str) -> Client:
    client = db.get(Client, client_id)
    if not client or client.IsDeleted or not client.IsActive:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This assistant is unavailable")
    return client


def _widget_settings(db: Session, client_id: str) -> LeadCompanySettings | None:
    return (
        db.query(LeadCompanySettings)
        .filter(
            LeadCompanySettings.ClientId == client_id,
            LeadCompanySettings.IsDeleted == False,  # noqa: E712
        )
        .one_or_none()
    )


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
@router.get(
    "/companies",
    response_model=list[PublicCompanyOut],
    summary="Companies with a live assistant (for the demo widget picker)",
)
def public_companies(db: Session = Depends(get_leadai_db)):
    """Only name/description/greeting are exposed — never contact details,
    counts, or settings. This endpoint is world-readable."""
    rows = (
        db.query(Client)
        .filter(Client.IsActive == True, Client.IsDeleted == False)  # noqa: E712
        .order_by(Client.Name.asc())
        .all()
    )
    out: list[PublicCompanyOut] = []
    for client in rows:
        cfg = _widget_settings(db, client.Id)
        if cfg is not None and not cfg.WidgetEnabled:
            continue
        out.append(
            PublicCompanyOut(
                id=client.Id,
                name=client.Name,
                description=client.Description,
                widget_greeting=cfg.WidgetGreeting if cfg else None,
            )
        )
    return out


# ---------------------------------------------------------------------------
# session lifecycle
# ---------------------------------------------------------------------------
@router.post(
    "/chat/start",
    response_model=ChatSession,
    status_code=status.HTTP_201_CREATED,
    summary="Start a chat and receive a session token",
)
def start_chat(
    payload: ChatStart,
    request: Request,
    db: Session = Depends(get_leadai_db),
):
    # Accept either the company id or its name, so an embed snippet can use a
    # human-readable identifier.
    client = (
        db.query(Client)
        .filter(
            Client.Id == payload.company,
            Client.IsActive == True,  # noqa: E712
            Client.IsDeleted == False,  # noqa: E712
        )
        .one_or_none()
    )
    if client is None:
        client = (
            db.query(Client)
            .filter(
                Client.Name.ilike(payload.company),
                Client.IsActive == True,  # noqa: E712
                Client.IsDeleted == False,  # noqa: E712
            )
            .first()
        )
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No company found for that link")

    cfg = _widget_settings(db, client.Id)
    if cfg is not None and not cfg.WidgetEnabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This assistant is currently disabled")

    # Coarse abuse guard on session creation, keyed on caller IP.
    ip = request.client.host if request.client else "unknown"
    if cache.incr(f"leadai:chatstart:{ip}", ttl=3600) > 60:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many chat sessions — try again later"
        )

    customer = LeadCustomer(
        ClientId=client.Id,
        PublicRef=f"Customer #{random.randint(10000, 99999)}",
        DisplayName=payload.display_name,
        PhoneEnc=encrypt_pii(payload.phone),
        EmailEnc=encrypt_pii(payload.email),
        WhatsAppEnc=encrypt_pii(payload.whatsapp or payload.phone),
        InstagramEnc=encrypt_pii(payload.instagram),
        PhoneHash=phone_fingerprint(payload.phone),
        CreatedBy="widget",
    )
    db.add(customer)
    db.flush()

    language = payload.language or (cfg.DefaultLanguage if cfg else None) or settings.default_language
    script = script_engine.resolve_script(db, client.Id, channel="chat")

    conversation = LeadConversation(
        ClientId=client.Id,
        CustomerId=customer.Id,
        Channel=payload.channel,
        Status="open",
        ScriptId=getattr(script, "Id", None),
        Language=language,
        LastMessageAt=utcnow(),
        CreatedBy="widget",
    )
    db.add(conversation)
    db.flush()

    db.add(Lead(ClientId=client.Id, ConversationId=conversation.Id, CreatedBy="widget"))

    greeting = (cfg.WidgetGreeting if cfg and cfg.WidgetGreeting else None) or (
        f"Hi! I'm the {client.Name} assistant. What can I help you with today?"
    )
    db.add(
        LeadMessage(
            ClientId=client.Id,
            ConversationId=conversation.Id,
            Sender="ai",
            Content=greeting,
            Confidence=1.0,
            ModelUsed="greeting-template",
            CreatedBy="widget",
        )
    )
    conversation.MessageCount = 1

    activity.log(
        db,
        action=A.CHAT_STARTED,
        client_id=client.Id,
        actor_email="widget",
        actor_role="customer",
        entity_type="conversation",
        entity_id=conversation.Id,
        message=f"New {payload.channel} chat started ({customer.PublicRef})",
        meta={
            "channel": payload.channel,
            "language": language,
            "script_id": getattr(script, "Id", None),
            "contact_supplied": bool(payload.phone or payload.email),
        },
        request=request,
    )
    db.commit()

    return ChatSession(
        session_token=create_chat_token(conversation.Id, client.Id),
        conversation_id=conversation.Id,
        company=client.Name,
        greeting=greeting,
        expires_in_minutes=settings.chat_session_minutes,
    )


@router.get(
    "/chat/messages",
    response_model=list[MessageOut],
    summary="Conversation history (poll this to receive agent replies)",
)
def history(
    conversation: LeadConversation = Depends(_session),
    db: Session = Depends(get_leadai_db),
):
    """The widget polls this so that after a handoff, messages typed by a human
    agent in the staff inbox appear to the customer. `system` rows are filtered
    out — the customer should not see internal call/assignment notes."""
    rows = (
        db.query(LeadMessage)
        .filter(
            LeadMessage.ConversationId == conversation.Id,
            LeadMessage.IsDeleted == False,  # noqa: E712
            LeadMessage.Sender != "system",
        )
        .order_by(LeadMessage.CreatedAt.asc())
        .all()
    )
    # Confidence/sources are internal signals; blank them for the public view.
    out = []
    for row in rows:
        dto = message_out(row)
        dto.confidence = None
        dto.sources = None
        dto.model_used = None
        dto.sender_email = None
        # Internal operational state — a customer must not see our provider
        # errors or the fact that a message failed to send.
        dto.delivery_status = None
        dto.delivery_error = None
        out.append(dto)
    return out


# ---------------------------------------------------------------------------
# the main turn handler
# ---------------------------------------------------------------------------
@router.post(
    "/chat/messages",
    response_model=ChatReply,
    summary="Send a customer message and get the AI reply",
)
def send_message(
    payload: ChatSend,
    request: Request,
    conversation: LeadConversation = Depends(_session),
    db: Session = Depends(get_leadai_db),
):
    # Rate limit BEFORE any retrieval or LLM call — that is the expensive part.
    if (
        cache.incr(f"leadai:chatrate:{conversation.Id}", ttl=60)
        > settings.chat_rate_limit_per_min
    ):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Slow down a moment, then try again"
        )

    client = _company(db, conversation.ClientId)
    return _handle_customer_turn(
        db, client, conversation, payload.message.strip(), request=request
    )


def _handle_customer_turn(
    db: Session,
    client: Client,
    conversation: LeadConversation,
    text: str,
    request: Request | None = None,
) -> ChatReply:
    """Thin adapter over the shared, channel-agnostic pipeline.

    The pipeline itself now lives in `services/conversation_flow.py` so that the
    WhatsApp, Messenger and Instagram webhooks run the SAME code — same lead
    scoring, same handoff rules, same activity log. This function only maps the
    pipeline's TurnResult onto the widget's existing ChatReply contract, which
    is unchanged: no existing widget deployment needs a single edit.

    `deliver_reply=False` because the widget is pull-based — it polls
    /public/chat/messages and receives the reply in this response. Social
    channels pass True and the reply is pushed to the provider.
    """
    result = conversation_flow.handle_customer_turn(
        db,
        client,
        conversation,
        text,
        request=request,
        source="widget",
        deliver_reply=False,
    )
    return ChatReply(
        reply=result.reply,
        confidence=result.confidence,
        needs_human=result.needs_human,
        handed_off_to_human=result.handed_off,
        sources=result.sources,
        lead_status=result.lead_status,
        lead_score=result.lead_score,
    )


@router.post("/chat/end", summary="Customer closes the chat")
def end_chat(
    request: Request,
    conversation: LeadConversation = Depends(_session),
    db: Session = Depends(get_leadai_db),
):
    """Closing from the widget does NOT close a conversation a human is working —
    the agent still needs it in their queue."""
    if conversation.Status not in ("assigned", "needs_human"):
        conversation.Status = "closed"
        conversation.ClosedAt = utcnow()
    conversation.UpdatedAt = utcnow()
    activity.log(
        db,
        action="chat.customer_ended",
        client_id=conversation.ClientId,
        actor_email="widget",
        actor_role="customer",
        entity_type="conversation",
        entity_id=conversation.Id,
        message="Customer ended the chat",
        request=request,
    )
    db.commit()
    return {"success": True, "status": conversation.Status}
