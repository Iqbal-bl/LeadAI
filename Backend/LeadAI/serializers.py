"""
ORM -> DTO conversion, and the one place the PII masking rule is enforced.

THE MASKING RULE
----------------
Every role, admins included, sees `Customer #48192`, a masked phone, and the full
conversation. Nobody sees the customer's real name, number, email or social handle
in a list or detail response. A role holding `lead.reveal_pii` (company_admin,
Admin) can reveal them, via the explicit /contact endpoint that writes an audit row.

The rule is implemented HERE rather than in each router so there is no route that
can accidentally forget it: every conversation response is built by these two
functions, and both take the Principal.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from domain.models import Client

from .models import (
    Lead,
    LeadCall,
    LeadChannelIdentity,
    LeadCompanyScript,
    LeadConversation,
    LeadCustomer,
    LeadKbChunk,
    LeadKbDocument,
    LeadMessage,
    LeadUserRole,
)
from .schemas import (
    ActivityOut,
    CallConversationDetail,
    CallOut,
    CallWithTranscript,
    ChatConversationDetail,
    CompanyOut,
    ConversationDetail,
    ConversationOut,
    DataPointOut,
    DocumentDetailOut,
    DocumentOut,
    LeadOut,
    OriginAttributionOut,
    MessageOut,
    PromptOut,
    RoleOut,
    ScriptDetail,
    ScriptOut,
)
from .security import decrypt_pii, mask_phone
from .services import ai_engine, script_engine
from .services import recordings as recording_service


# ===========================================================================
# companies
# ===========================================================================
def company_out(db: Session, client: Client, with_counts: bool = True) -> CompanyOut:
    counts = {"user_count": 0, "document_count": 0, "chunk_count": 0,
              "script_count": 0, "conversation_count": 0}
    if with_counts:
        counts = {
            "user_count": db.query(LeadUserRole)
            .filter(LeadUserRole.ClientId == client.Id,
                    LeadUserRole.IsDeleted == False).count(),  # noqa: E712
            "document_count": db.query(LeadKbDocument)
            .filter(LeadKbDocument.ClientId == client.Id,
                    LeadKbDocument.IsDeleted == False).count(),  # noqa: E712
            "chunk_count": db.query(LeadKbChunk)
            .filter(LeadKbChunk.ClientId == client.Id,
                    LeadKbChunk.IsDeleted == False).count(),  # noqa: E712
            "script_count": db.query(LeadCompanyScript)
            .filter(LeadCompanyScript.ClientId == client.Id,
                    LeadCompanyScript.IsDeleted == False).count(),  # noqa: E712
            "conversation_count": db.query(LeadConversation)
            .filter(LeadConversation.ClientId == client.Id,
                    LeadConversation.IsDeleted == False).count(),  # noqa: E712
        }
    has_sub = None
    if with_counts:
        from .services import billing as billing_svc
        active = billing_svc.get_active_recharge(db, client.Id)
        has_sub = bool(active and active.Status == "active")

    return CompanyOut(
        id=client.Id,
        name=client.Name,
        email=client.Email,
        phone_number=client.PhoneNumber,
        description=client.Description,
        is_active=bool(client.IsActive),
        created_at=client.CreatedAt,
        has_active_subscription=has_sub,
        **counts,
    )


# ===========================================================================
# RBAC
# ===========================================================================
def role_out(db: Session, row: LeadUserRole) -> RoleOut:
    client_name = None
    if row.ClientId:
        client = db.get(Client, row.ClientId)
        client_name = client.Name if client else None
    return RoleOut(
        id=row.Id,
        user_email=row.UserEmail,
        full_name=row.FullName,
        role=row.Role,
        client_id=row.ClientId,
        client_name=client_name,
        is_active=bool(row.IsActive),
        created_at=row.CreatedAt,
    )


# ===========================================================================
# knowledge base
# ===========================================================================
def document_out(row: LeadKbDocument) -> DocumentOut:
    return DocumentOut(
        id=row.Id,
        title=row.Title,
        file_name=row.FileName,
        content_type=row.ContentType,
        source_type=row.SourceType,
        source_url=row.SourceUrl,
        status=row.Status,
        status_message=row.StatusMessage,
        chunk_count=row.ChunkCount or 0,
        char_count=row.CharCount or 0,
        embedding_model=row.EmbeddingModel,
        tags=row.Tags,
        created_at=row.CreatedAt,
        created_by=row.CreatedBy,
    )


def document_detail_out(row: LeadKbDocument, raw_text: str | None = None) -> DocumentDetailOut:
    base = document_out(row)
    return DocumentDetailOut(
        **base.model_dump(),
        raw_text=raw_text if raw_text is not None else row.RawText,
    )


# ===========================================================================
# scripts + prompts
# ===========================================================================
def script_out(row: LeadCompanyScript) -> ScriptOut:
    sections = script_engine.sections_of(row)
    return ScriptOut(
        id=row.Id,
        name=row.Name,
        slug=row.Slug,
        description=row.Description,
        channel=row.Channel,
        language=row.Language,
        version=row.Version,
        is_default=bool(row.IsDefault),
        is_active=bool(row.IsActive),
        multi_stt=bool(row.MultiStt),
        section_count=len(sections),
        created_at=row.CreatedAt,
        updated_at=row.UpdatedAt,
    )


def data_point_out(row) -> DataPointOut:
    return DataPointOut(
        id=row.Id,
        key=row.Key,
        label=row.Label,
        data_type=row.DataType,
        options=row.OptionsJson,
        description=row.Description,
        required=bool(row.Required),
        display_order=row.DisplayOrder or 0,
        is_active=bool(row.IsActive),
        created_at=row.CreatedAt,
    )


def script_detail(row: LeadCompanyScript, include_prompt: bool = True) -> ScriptDetail:
    sections = script_engine.sections_of(row)
    base = script_out(row)
    return ScriptDetail(
        **base.model_dump(),
        script_xml=row.ScriptXml,
        sections=sections,
        rendered_prompt=(
            script_engine.sections_to_system_prompt(sections) if include_prompt else None
        ),
    )


def prompt_out(key: str, content: str, is_customised: bool, updated_at=None) -> PromptOut:
    return PromptOut(
        key=key, content=content, is_customised=is_customised, updated_at=updated_at
    )


# ===========================================================================
# conversations
# ===========================================================================
def message_out(row: LeadMessage) -> MessageOut:
    return MessageOut(
        id=row.Id,
        sender=row.Sender,
        sender_email=row.SenderEmail,
        content=row.Content,
        confidence=row.Confidence,
        sources=row.SourcesJson,
        model_used=row.ModelUsed,
        call_sid=row.CallSid,
        delivery_status=getattr(row, "DeliveryStatus", None),
        delivery_error=getattr(row, "DeliveryError", None),
        created_at=row.CreatedAt,
    )


def lead_out(row: Lead | None) -> LeadOut | None:
    if row is None:
        return None
    return LeadOut(
        status=row.Status,
        score=row.Score or 0,
        interest=row.Interest or "unknown",
        intent=row.Intent or "browsing",
        budget=row.Budget or "unknown",
        timeline=row.Timeline or "unknown",
        product=row.Product or "unknown",
        sentiment=row.Sentiment or "neutral",
        score_breakdown=row.ScoreBreakdown,
        qualified_at=row.QualifiedAt,
        data_points=row.DataPointsJson,
    )


def call_out(row: LeadCall, recording_url: str | None = None) -> CallOut:
    return CallOut(
        id=row.Id,
        conversation_id=row.ConversationId,
        call_sid=row.CallSid,
        provider=row.Provider,
        mode=row.Mode,
        status=row.Status,
        handed_off=bool(row.HandedOff),
        duration_sec=row.DurationSec or 0,
        phone_masked=row.PhoneMasked,
        language=row.Language,
        script_id=row.ScriptId,
        initiated_by_email=row.InitiatedByEmail,
        failure_reason=row.FailureReason,
        recording_url=recording_url,
        created_at=row.CreatedAt,
    )


def customer_number(customer: LeadCustomer | None) -> str | None:
    """The customer's phone number, from whichever field holds one.

    A number can sit in `PhoneEnc` or, for a WhatsApp contact, only in `WhatsAppEnc`.
    Reading just the first left such customers with no number anywhere in the inbox.
    """
    if not customer:
        return None
    for encrypted in (customer.PhoneEnc, customer.WhatsAppEnc):
        value = decrypt_pii(encrypted) if encrypted else None
        if value:
            return value
    return None


def resolve_display_name(
    db: Session, customer: LeadCustomer | None, conversation: LeadConversation | None = None
) -> str | None:
    """The best name we have for a customer, whichever channel they came through.

    Order: the name saved on the customer, then the profile name on the channel
    identity the conversation arrived on (then any other identity of theirs), then
    the stored Instagram handle. Messenger and Instagram send only an opaque id, so
    the name usually lives on the identity row, not on the customer.
    """
    if not customer:
        return None
    if customer.DisplayName:
        return customer.DisplayName

    identities = (
        db.query(LeadChannelIdentity)
        .filter(
            LeadChannelIdentity.CustomerId == customer.Id,
            LeadChannelIdentity.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadChannelIdentity.CreatedAt.asc())
        .all()
    )
    account_id = getattr(conversation, "ChannelAccountId", None)
    identities.sort(key=lambda i: 0 if account_id and i.ChannelAccountId == account_id else 1)
    for identity in identities:
        name = identity.ProfileName or identity.ExternalUsername
        if name:
            return name

    return decrypt_pii(customer.InstagramEnc) or None


def resolve_origin_attribution(
    db: Session, conversation: LeadConversation, customer: LeadCustomer | None
) -> OriginAttributionOut | None:
    """Resolve the specific post, campaign, ad or inbound asset that originated this lead."""
    # 1. Social post comment attribution
    if customer:
        from .models_blog import LeadSocialComment

        comment = (
            db.query(LeadSocialComment)
            .filter(
                LeadSocialComment.CustomerId == customer.Id,
                LeadSocialComment.IsDeleted == False,
            )
            .order_by(LeadSocialComment.CreatedAt.desc())
            .first()
        )
        if comment:
            title = comment.PostTitle
            if not title and comment.PostSnippet:
                title = comment.PostSnippet[:60] + ("..." if len(comment.PostSnippet) > 60 else "")
            return OriginAttributionOut(
                origin_type="post_comment",
                channel=comment.Channel or conversation.Channel or "social",
                title=title or "Social Post",
                snippet=comment.CommentText or comment.PostSnippet,
                reference_id=comment.PostUrn or comment.CommentUrn,
                url=comment.AuthorProfileUrl,
                interaction_type="comment_reply" if comment.ParentCommentUrn else "post_comment",
            )

    # 2. Outbound campaign attribution
    if conversation.CampaignId:
        from .models_ext import LeadCampaign

        campaign = db.get(LeadCampaign, conversation.CampaignId)
        if campaign:
            return OriginAttributionOut(
                origin_type="campaign",
                channel=campaign.Channel or conversation.Channel,
                title=campaign.Name,
                snippet=campaign.Purpose,
                reference_id=campaign.Id,
                interaction_type="campaign_outreach",
            )

    # 3. Voice telephony call
    if conversation.Channel == "voice":
        return OriginAttributionOut(
            origin_type="voice_call",
            channel="voice",
            title="Voice Call",
            snippet="Inbound / Outbound Phone Call",
            interaction_type="phone_call",
        )

    # 4. Web chat widget
    if conversation.Channel == "web":
        return OriginAttributionOut(
            origin_type="website",
            channel="web",
            title="Website Live Chat",
            snippet="Website Visitor",
            interaction_type="web_inbound",
        )

    # 5. Direct 1:1 message
    channel_name = (conversation.Channel or "Direct").capitalize()
    return OriginAttributionOut(
        origin_type="direct",
        channel=conversation.Channel or "direct",
        title=f"Direct {channel_name} Message",
        snippet=f"Direct 1:1 interaction on {channel_name}",
        interaction_type="direct_dm",
    )


def conversation_out(
    db: Session, conversation: LeadConversation, principal
) -> ConversationOut:
    customer = db.get(LeadCustomer, conversation.CustomerId)
    lead = (
        db.query(Lead).filter(Lead.ConversationId == conversation.Id).one_or_none()
    )

    # The masking rule: NO role, admins included, gets the customer's name from a
    # list or detail response. The name is identity, and identity is released only by
    # the explicit, audited /contact (Reveal) call. Until then everyone works with the
    # customer number (`customer_ref`, e.g. "Customer #51998"), which is always present.
    # `customer_name` stays in the response shape, always null, so clients that read
    # it keep working.
    display_name = None

    # The phone is present whenever the customer has one, for every role, but only
    # masked. The full number also requires the /contact call.
    phone_masked = mask_phone(customer_number(customer))

    return ConversationOut(
        id=conversation.Id,
        client_id=conversation.ClientId,
        channel=conversation.Channel,
        status=conversation.Status,
        customer_ref=customer.PublicRef if customer else "Customer #unknown",
        customer_name=display_name,
        customer_phone_masked=phone_masked,
        summary=conversation.Summary,
        next_step=conversation.NextStep,
        assigned_user_email=conversation.AssignedUserEmail,
        handoff_reason=conversation.HandoffReason,
        language=conversation.Language,
        message_count=conversation.MessageCount or 0,
        last_message_at=conversation.LastMessageAt,
        created_at=conversation.CreatedAt,
        lead=lead_out(lead),
        origin_attribution=resolve_origin_attribution(db, conversation, customer),
    )


def conversation_detail(
    db: Session, conversation: LeadConversation, principal
) -> ConversationDetail:
    base = conversation_out(db, conversation, principal)
    messages = (
        db.query(LeadMessage)
        .filter(
            LeadMessage.ConversationId == conversation.Id,
            LeadMessage.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadMessage.CreatedAt.asc())
        .all()
    )
    calls = (
        db.query(LeadCall)
        .filter(
            LeadCall.ConversationId == conversation.Id,
            LeadCall.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadCall.CreatedAt.desc())
        .all()
    )
    lead = db.query(Lead).filter(Lead.ConversationId == conversation.Id).one_or_none()
    # Call audio: only for roles that may read calls; one query for all the calls.
    recordings = (
        recording_service.playable_urls(db, [c.CallSid for c in calls])
        if principal.can("call.read") else {}
    )

    return ConversationDetail(
        **base.model_dump(),
        messages=[message_out(m) for m in messages],
        suggestions=ai_engine.agent_suggestions(lead, conversation),
        calls=[call_out(c, recordings.get(c.CallSid)) for c in calls],
    )


def chat_conversation_detail(
    db: Session, conversation: LeadConversation, principal
) -> ChatConversationDetail:
    """Chat-focused view: messages only, calls reduced to a count/flag."""
    base = conversation_out(db, conversation, principal)
    messages = (
        db.query(LeadMessage)
        .filter(
            LeadMessage.ConversationId == conversation.Id,
            LeadMessage.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadMessage.CreatedAt.asc())
        .all()
    )
    call_count = (
        db.query(LeadCall)
        .filter(
            LeadCall.ConversationId == conversation.Id,
            LeadCall.IsDeleted == False,  # noqa: E712
        )
        .count()
    )
    lead = db.query(Lead).filter(Lead.ConversationId == conversation.Id).one_or_none()

    return ChatConversationDetail(
        **base.model_dump(),
        messages=[message_out(m) for m in messages],
        suggestions=ai_engine.agent_suggestions(lead, conversation),
        has_calls=call_count > 0,
        call_count=call_count,
    )


def call_conversation_detail(
    db: Session, conversation: LeadConversation, principal
) -> CallConversationDetail:
    """Call-focused view: multiple calls, each with its own transcript."""
    base = conversation_out(db, conversation, principal)

    # Fetch all messages for this conversation
    all_messages = (
        db.query(LeadMessage)
        .filter(
            LeadMessage.ConversationId == conversation.Id,
            LeadMessage.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadMessage.CreatedAt.asc())
        .all()
    )

    # Group messages by CallSid
    messages_by_call: dict[str, list] = {}
    non_call_messages = []
    for m in all_messages:
        if m.CallSid:
            messages_by_call.setdefault(m.CallSid, []).append(m)
        else:
            non_call_messages.append(m)

    # Fetch all calls
    calls = (
        db.query(LeadCall)
        .filter(
            LeadCall.ConversationId == conversation.Id,
            LeadCall.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadCall.CreatedAt.desc())
        .all()
    )

    lead = db.query(Lead).filter(Lead.ConversationId == conversation.Id).one_or_none()

    # Build CallWithTranscript for each call
    recordings = (
        recording_service.playable_urls(db, [c.CallSid for c in calls])
        if principal.can("call.read") else {}
    )
    call_details = []
    for c in calls:
        call_messages = messages_by_call.get(c.CallSid, [])
        # A real, playable link, or null when the call has no recording (the old value was a
        # path to a route that does not exist, so the player always got a 404).
        recording_url = recordings.get(c.CallSid)
        call_details.append(
            CallWithTranscript(
                id=c.Id,
                call_sid=c.CallSid,
                provider=c.Provider,
                mode=c.Mode,
                status=c.Status,
                handed_off=bool(c.HandedOff),
                duration_sec=c.DurationSec or 0,
                phone_masked=c.PhoneMasked,
                language=c.Language,
                script_id=c.ScriptId,
                initiated_by_email=c.InitiatedByEmail,
                failure_reason=c.FailureReason,
                recording_url=recording_url,
                created_at=c.CreatedAt,
                messages=[message_out(m) for m in call_messages],
            )
        )

    return CallConversationDetail(
        **base.model_dump(),
        suggestions=ai_engine.agent_suggestions(lead, conversation),
        calls=call_details,
    )


# ===========================================================================
# activity
# ===========================================================================
def activity_out(row) -> ActivityOut:
    return ActivityOut(
        id=row.Id,
        client_id=row.ClientId,
        actor_email=row.ActorEmail,
        actor_role=row.ActorRole,
        action=row.Action,
        log_type=row.LogType,
        entity_type=row.EntityType,
        entity_id=row.EntityId,
        message=row.LogMessage,
        meta=row.MetaJson,
        ip_address=row.IpAddress,
        created_at=row.CreatedAt,
    )
