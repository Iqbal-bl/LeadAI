"""
The bridge between a LeadAI conversation and the EXISTING outbound call pipeline.

This is the heart of "calls already work in outbound — use that".

WHAT THE OUTBOUND APP ALREADY DOES
----------------------------------
`/api/make-call` places a Twilio call, stashes the agent configuration in the
module-level dict `active_calls[call_sid]`, and returns. When Twilio hits
`/outbound-twiml` and then opens `/media-stream`, the handler reads
`active_calls[call_sid]["xml_sections"]` (plus language/gender/speaker/multi_stt)
and builds a `SimpleAgent` from it. Transcript turns are written to the
`conversations` table by `save_message()`, status changes to
`calllogs`/`callstatus`, and audio to `recordings`.

WHAT THIS MODULE ADDS
---------------------
It populates `active_calls[call_sid]` from a COMPANY'S script + knowledge base
instead of from the operator's browser session, then records the link between the
resulting CallSid and the lead conversation.

Consequences:
  * no change to /media-stream, /outbound-twiml, /call-status or the SimpleAgent;
  * two companies can be on calls simultaneously with completely different
    personas, because the config is per-CallSid, which the existing code already
    assumed — it was simply only ever written from one session;
  * the call's transcript lands in `conversations` (unchanged), and
    `sync_call_transcript()` later mirrors it into `leadai_messages` so the same
    qualification + summarisation pipeline that scores a chat also scores a call.

WHY MIRROR RATHER THAN MOVE THE TRANSCRIPT
------------------------------------------
`conversations` stays the single source of truth for call audio/transcripts (the
existing reporting, CSV exports and batch flows read it). Mirroring into
`leadai_messages` is additive and idempotent: it is keyed on CallSid + content so
re-running it cannot duplicate turns.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Lead, LeadCall, LeadConversation, LeadCustomer, LeadMessage, utcnow
from ..security import decrypt_pii, mask_phone
from . import ai_engine, memory, script_engine, telephony

logger = logging.getLogger(__name__)


def _server_url() -> str:
    return os.getenv("SERVER_URL", "").rstrip("/")


def prepare_agent_context(
    db: Session,
    client_id: str,
    company_name: str,
    channel: str = "voice",
    script_id: str | None = None,
    conversation: LeadConversation | None = None,
) -> tuple[list[dict], object, dict]:
    """Build the `xml_sections` + voice settings for one company's agent.

    Returns (sections, script_row, voice_settings).

    MEMORY (added): when `conversation` is supplied, the customer's prior history
    — chat turns, earlier calls, extracted qualification facts, and summaries
    from other channels — is rendered into two extra sections and PREPENDED to
    the company script.

    Prepended rather than appended on purpose. `xml_parser.sections_to_prompt`
    concatenates in order, and LLM instruction-following degrades toward the
    middle of a long prompt; the "you have spoken to this person before" framing
    has to land before the agent reads its opening line, or it will introduce
    itself from scratch anyway. The company's own Identity section still follows
    immediately after, so persona is unaffected.

    When there is no history (a genuinely cold outbound lead) this adds nothing
    and the prompt is byte-identical to the pre-memory behaviour.
    """
    script = script_engine.resolve_script(db, client_id, channel=channel, script_id=script_id)
    sections = script_engine.sections_of(script)

    if not sections:
        # No XML script for this company: synthesise a minimal identity section
        # so the SimpleAgent still knows who it is. The knowledge base then does
        # the heavy lifting via the RAG turn handler.
        sections = [
            {
                "title": "Identity",
                "type": "identity",
                "content": [
                    {"name": "company", "value": company_name},
                    {"name": "name", "value": f"{company_name} Assistant"},
                    {
                        "name": "description",
                        "value": (
                            f"You are calling on behalf of {company_name} about the "
                            "customer's enquiry. Answer only from company knowledge."
                        ),
                    },
                ],
            },
            {
                "title": "Objective",
                "type": "text",
                "content": (
                    "Confirm the customer's interest, capture budget and timeline, and "
                    "offer to connect a specialist if anything is outside your knowledge."
                ),
            },
        ]

    # ---- cross-channel memory ------------------------------------------- #
    if conversation is not None:
        briefing = memory.voice_briefing(db, conversation)
        if briefing:
            sections = briefing + sections
            logger.info(
                "[LeadAI call] agent context includes %d memory section(s) for conv %s",
                len(briefing),
                conversation.Id,
            )

    voice = {
        "language": (getattr(script, "Language", None) or settings.default_language),
        "gender": getattr(script, "VoiceGender", None) or "female",
        "speaker": getattr(script, "VoiceSpeaker", None) or "anushka",
        "multi_stt": bool(getattr(script, "MultiStt", False)),
    }
    return sections, script, voice


def register_call_context(call_sid: str, phone_number: str, sections: list[dict], voice: dict, conversation_id: str | None = None, client_id: str | None = None) -> None:
    """Write the per-call agent config into the EXISTING active_calls registry.

    This is the single point of contact with the outbound app's mutable state,
    and it only ADDS a key for a brand-new CallSid — it never reads or mutates
    another call's entry.
    """
    from multiligual_call import active_calls

    active_calls[call_sid] = {
        "phone_number": phone_number,
        "status": "initiated",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "language": voice.get("language"),
        "gender": voice.get("gender"),
        "speaker": voice.get("speaker"),
        "multi_stt": voice.get("multi_stt", False),
        "xml_sections": sections,
        # Marker so anything inspecting active_calls can tell this call came from
        # LeadAI and which company/conversation it belongs to.
        "leadai": True,
        "conversation_id": conversation_id,
        "client_id": client_id,
    }


def start_call_for_conversation(
    db: Session,
    client_id: str,
    company_name: str,
    conversation: LeadConversation,
    initiated_by: str,
    mode: str = "ai_voice",
    script_id: str | None = None,
    override_number: str | None = None,
) -> LeadCall:
    """Place an outbound call for a lead conversation.

    The customer's phone is decrypted only inside this function, held in a local,
    used for the carrier API, and never returned to the caller — the LeadCall row
    stores a masked form for display.
    """
    customer = db.get(LeadCustomer, conversation.CustomerId)
    number = override_number or decrypt_pii(customer.PhoneEnc if customer else None)

    call = LeadCall(
        ClientId=client_id,
        ConversationId=conversation.Id,
        Mode=mode,
        Direction="outbound",
        InitiatedByEmail=initiated_by,
        CreatedBy=initiated_by,
        PhoneMasked=mask_phone(number),
    )

    if not number:
        call.Status = "failed"
        call.Provider = "none"
        call.FailureReason = "No phone number on record for this customer"
        db.add(call)
        db.flush()
        return call

    # Validate + canonicalise with the app's own validator so a bad number is
    # rejected before any carrier API call (and with the same rules as
    # /api/make-call).
    try:
        from validate_number import validate_phone_number

        number = validate_phone_number(number)
    except Exception as exc:  # noqa: BLE001
        call.Status = "failed"
        call.Provider = "none"
        call.FailureReason = f"Invalid phone number: {exc}"[:300]
        db.add(call)
        db.flush()
        return call

    # Check client billing quota before initiating call
    from . import billing
    has_quota, quota_reason, _ = billing.check_call_quota(db, client_id)
    if not has_quota:
        call.Status = "failed"
        call.Provider = "none"
        call.FailureReason = quota_reason[:300]
        db.add(call)
        db.flush()
        logger.warning(f"[LeadAI call] Quota rejected for client {client_id}: {quota_reason}")
        return call

    # `conversation` is passed so the agent boots with the customer's chat and
    # earlier-call history in its system prompt instead of a blank slate.
    sections, script, voice = prepare_agent_context(
        db,
        client_id,
        company_name,
        channel="voice",
        script_id=script_id,
        conversation=conversation,
    )
    call.ScriptId = getattr(script, "Id", None)
    call.Language = voice.get("language")

    try:
        call_sid, status, provider = telephony.place_call(number, _server_url())
    except Exception as exc:  # noqa: BLE001
        call.Status = "failed"
        call.Provider = settings.effective_voice_provider
        call.FailureReason = str(exc)[:300]
        db.add(call)
        db.flush()
        logger.error("[LeadAI call] placement failed for conv %s: %s", conversation.Id, exc)
        return call

    # Register the agent config BEFORE the carrier can hit /outbound-twiml.
    # Placement returns as soon as the carrier accepts, and the TwiML webhook
    # arrives milliseconds later — so this must not be deferred.
    register_call_context(call_sid, number, sections, voice, conversation_id=conversation.Id, client_id=client_id)

    call.CallSid = call_sid
    call.Status = status
    call.Provider = provider

    conversation.Channel = "voice" if conversation.Channel == "web" else conversation.Channel
    conversation.LastMessageAt = utcnow()

    db.add(call)
    db.add(
        LeadMessage(
            ClientId=client_id,
            ConversationId=conversation.Id,
            Sender="system",
            Content=f"Outbound {mode} call placed via {provider} ({call.PhoneMasked}).",
            CallSid=call_sid,
            CreatedBy=initiated_by,
        )
    )
    db.flush()
    return call


def hangup_call(db: Session, call: LeadCall) -> bool:
    """End a live call through whichever carrier placed it."""
    if not call.CallSid:
        return False
    # Record the reason in the outbound app's own hangup registry so its existing
    # transcript annotation ("who ended the call") stays accurate.
    try:
        from globals import call_hangup_reasons

        call_hangup_reasons[call.CallSid] = "Ended via LeadAI dashboard"
    except Exception:  # noqa: BLE001
        pass

    ok = telephony.hangup(call.CallSid, call.Provider)
    if ok:
        call.Status = "completed"
        call.UpdatedAt = utcnow()
    return ok


# --------------------------------------------------------------------------- #
# transcript sync
# --------------------------------------------------------------------------- #
# The outbound pipeline records agent turns as ResponseType "question" and
# customer turns as "answer" (see db.save_transcript_message). Map those onto
# LeadAI's sender vocabulary.
_RESPONSE_TYPE_TO_SENDER = {
    "question": "ai",
    "answer": "customer",
    "hangup": "system",
}


def sync_call_transcript(
    db: Session,
    client_id: str,
    company_name: str,
    call: LeadCall,
) -> dict:
    """Mirror a finished call's transcript into the lead conversation, then
    re-qualify and re-summarise the lead from it.

    Idempotent: turns already mirrored (same CallSid + sender + content) are
    skipped, so this can be called from a status webhook, a poll, or manually
    without creating duplicates.
    """
    if not call.CallSid:
        return {"imported": 0, "reason": "call has no CallSid yet"}

    conversation = db.get(LeadConversation, call.ConversationId)
    if not conversation:
        return {"imported": 0, "reason": "conversation missing"}

    # Read the authoritative transcript from the EXISTING conversations table.
    from db import fetch_conversation

    turns = fetch_conversation(call.CallSid) or []
    if not turns:
        return {"imported": 0, "reason": "no transcript rows yet"}

    existing = {
        (m.Sender, (m.Content or "").strip())
        for m in db.query(LeadMessage)
        .filter(
            LeadMessage.ConversationId == conversation.Id,
            LeadMessage.CallSid == call.CallSid,
        )
        .all()
    }

    imported = 0
    for turn in turns:
        sender = _RESPONSE_TYPE_TO_SENDER.get(
            (turn.get("response_type") or "").lower(), "system"
        )
        content = (turn.get("response_text") or "").strip()
        if not content or (sender, content) in existing:
            continue
        db.add(
            LeadMessage(
                ClientId=client_id,
                ConversationId=conversation.Id,
                Sender=sender,
                Content=content,
                CallSid=call.CallSid,
                CreatedAt=turn.get("created_at") or utcnow(),
                CreatedBy="call-sync",
            )
        )
        existing.add((sender, content))
        imported += 1

    if imported:
        db.flush()

    messages = (
        db.query(LeadMessage)
        .filter(LeadMessage.ConversationId == conversation.Id)
        .order_by(LeadMessage.CreatedAt.asc())
        .all()
    )
    conversation.MessageCount = len(messages)
    conversation.LastMessageAt = utcnow()

    lead = (
        db.query(Lead).filter(Lead.ConversationId == conversation.Id).one_or_none()
    )
    if lead is None:
        lead = Lead(ClientId=client_id, ConversationId=conversation.Id, CreatedBy="call-sync")
        db.add(lead)
        db.flush()

    ai_engine.qualify(db, client_id, lead, messages)
    conversation.Summary, conversation.NextStep = ai_engine.summarize(
        db, client_id, company_name, lead, messages
    )

    return {
        "imported": imported,
        "total_messages": len(messages),
        "lead_status": lead.Status,
        "lead_score": lead.Score,
    }
