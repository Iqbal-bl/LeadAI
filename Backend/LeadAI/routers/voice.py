"""
Outbound voice for leads — built ON TOP of the call pipeline that already works.

WHAT THIS DOES NOT DO
---------------------
It does not re-implement telephony, media streaming, STT or TTS. The existing
`/outbound-twiml` -> `/media-stream` -> Sarvam STT -> agent -> Sarvam TTS loop is
untouched. This router only:

  1. resolves the company's voice script + knowledge base,
  2. writes the per-call agent config into the existing `active_calls` registry,
  3. asks the carrier (Exotel, else Twilio) to place the call,
  4. records the CallSid <-> lead-conversation link,
  5. mirrors the finished transcript back into the lead and re-scores it.

See services/call_bridge.py for why that ordering matters and why the transcript
is mirrored rather than moved.

THE EXOTEL STATUS WEBHOOK
-------------------------
`/voice/exotel/status` is public (carriers cannot carry a user token) and is
therefore treated as untrusted input: it may only advance the status of a call it
can name by Sid, and it never accepts a ClientId from the payload — the company is
looked up from our own row.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from Domain.models import Client

from .. import activity
from ..activity import A
from ..config import settings
from ..db import get_leadai_db
from ..models import Lead, LeadCall, LeadConversation, LeadMessage, utcnow
from ..rbac import Principal, require, resolve_scope
from ..schemas import (
    CallOut,
    CallStart,
    CallSyncOut,
    CallTranscriptOut,
    Ok,
    VoiceTurnIn,
    VoiceTurnOut,
)
from ..serializers import call_out
from ..services import ai_engine, call_bridge, telephony

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice", tags=["LeadAI • Voice"])


def _company(db: Session, client_id: str) -> Client:
    client = db.get(Client, client_id)
    if not client or client.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    return client


def _conversation(
    db: Session, conversation_id: str, principal: Principal, client_id: str
) -> LeadConversation:
    query = db.query(LeadConversation).filter(
        LeadConversation.Id == conversation_id,
        LeadConversation.ClientId == client_id,
        LeadConversation.IsDeleted == False,  # noqa: E712
    )
    if principal.sees_only_assigned:
        query = query.filter(LeadConversation.AssignedUserEmail == principal.email.lower())
    conversation = query.one_or_none()
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


def _call(db: Session, call_id: str, client_id: str) -> LeadCall:
    call = db.get(LeadCall, call_id)
    if not call or call.IsDeleted or call.ClientId != client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found")
    return call


@router.get("/status", summary="Which voice providers are live")
def voice_status(principal: Principal = Depends(require("call.read"))):
    """Lets the UI show 'Exotel connected / simulated' instead of failing a call
    for a reason the operator can't see."""
    return telephony.status_report()


@router.post(
    "/conversations/{conversation_id}/call",
    response_model=CallOut,
    status_code=status.HTTP_201_CREATED,
    summary="Place an outbound call for this lead",
)
def start_call(
    conversation_id: str,
    payload: CallStart,
    request: Request,
    principal: Principal = Depends(require("call.initiate")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    client = _company(db, client_id)
    conversation = _conversation(db, conversation_id, principal, client_id)

    # Dialling an arbitrary number bypasses the stored-contact masking, so it
    # requires the same authority as revealing the contact.
    if payload.override_number and not principal.can("lead.reveal_pii"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Your role cannot dial a custom number for this lead",
        )

    call = call_bridge.start_call_for_conversation(
        db,
        client_id,
        client.Name,
        conversation,
        initiated_by=principal.email,
        mode=payload.mode,
        script_id=payload.script_id,
        override_number=payload.override_number,
    )

    if call.Status == "failed":
        activity.log_principal(
            db,
            principal,
            action=A.CALL_FAILED,
            client_id=client_id,
            entity_type="call",
            entity_id=call.Id,
            message=f"Call failed: {call.FailureReason}",
            meta={"conversation_id": conversation.Id, "provider": call.Provider},
            log_type="Error",
            request=request,
        )
        db.commit()
        # 201 with a failed row rather than a 4xx: the attempt is itself a record
        # the UI needs to display in the call history.
        return call_out(call)

    activity.log_principal(
        db,
        principal,
        action=A.CALL_INITIATED,
        client_id=client_id,
        entity_type="call",
        entity_id=call.Id,
        message=f"Placed {call.Mode} call via {call.Provider} ({call.PhoneMasked})",
        meta={
            "conversation_id": conversation.Id,
            "call_sid": call.CallSid,
            "provider": call.Provider,
            "script_id": call.ScriptId,
            "language": call.Language,
        },
        request=request,
    )
    db.commit()
    db.refresh(call)
    return call_out(call)


@router.get(
    "/conversations/{conversation_id}/calls",
    response_model=list[CallOut],
    summary="Call history for a lead",
)
def list_calls(
    conversation_id: str,
    principal: Principal = Depends(require("call.read")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    conversation = _conversation(db, conversation_id, principal, client_id)
    rows = (
        db.query(LeadCall)
        .filter(
            LeadCall.ConversationId == conversation.Id,
            LeadCall.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadCall.CreatedAt.desc())
        .all()
    )
    return [call_out(r) for r in rows]


@router.get(
    "/calls/by-sid/{call_sid}/transcript",
    response_model=CallTranscriptOut,
    summary="Get transcript for a single call by CallSid",
)
def get_call_transcript_by_sid(
    call_sid: str,
    principal: Principal = Depends(require("call.read")),
    db: Session = Depends(get_leadai_db),
):
    """Return the full transcript for one call, identified by its Twilio/Exotel
    CallSid. Includes call metadata and all messages (customer + AI + agent)."""
    # Find the call by CallSid
    call = (
        db.query(LeadCall)
        .filter(
            LeadCall.CallSid == call_sid,
            LeadCall.IsDeleted == False,  # noqa: E712
        )
        .first()
    )
    if not call:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found")

    # Verify tenant access
    client_id = resolve_scope(principal)
    if call.ClientId != client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found")

    # Fetch all messages for this call
    messages = (
        db.query(LeadMessage)
        .filter(
            LeadMessage.CallSid == call_sid,
            LeadMessage.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadMessage.CreatedAt.asc())
        .all()
    )

    # Fallback: fetch directly from legacy conversations table if leadai_messages is empty
    if not messages:
        from db import fetch_conversation
        _RESPONSE_TYPE_TO_SENDER = {"question": "ai", "answer": "customer", "hangup": "system"}
        turns = fetch_conversation(call_sid) or []
        for turn in turns:
            sender = _RESPONSE_TYPE_TO_SENDER.get((turn.get("response_type") or "").lower(), "system")
            content = (turn.get("response_text") or "").strip()
            if content:
                messages.append({
                    "id": turn.get("id"),
                    "sender": sender,
                    "sender_email": None,
                    "content": content,
                    "confidence": None,
                    "sources": None,
                    "model_used": None,
                    "call_sid": call_sid,
                    "created_at": turn.get("created_at"),
                })

    from ..serializers import message_out

    recording_url = f"/api/leadai/voice/recordings/{call_sid}" if call.Status == "completed" else None

    def _msg(m):
        if isinstance(m, dict):
            from ..schemas import MessageOut
            return MessageOut(**m)
        return message_out(m)

    return CallTranscriptOut(
        id=call.Id,
        call_sid=call.CallSid,
        conversation_id=call.ConversationId,
        provider=call.Provider,
        mode=call.Mode,
        status=call.Status,
        handed_off=bool(call.HandedOff),
        duration_sec=call.DurationSec or 0,
        phone_masked=call.PhoneMasked,
        language=call.Language,
        script_id=call.ScriptId,
        initiated_by_email=call.InitiatedByEmail,
        failure_reason=call.FailureReason,
        recording_url=recording_url,
        created_at=call.CreatedAt,
        messages=[_msg(m) for m in messages],
    )


@router.post("/calls/{call_id}/hangup", response_model=CallOut, summary="End a live call")
def hangup_call(
    call_id: str,
    request: Request,
    principal: Principal = Depends(require("call.initiate")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    call = _call(db, call_id, client_id)

    ok = call_bridge.hangup_call(db, call)
    activity.log_principal(
        db,
        principal,
        action=A.CALL_COMPLETED if ok else A.CALL_FAILED,
        client_id=client_id,
        entity_type="call",
        entity_id=call.Id,
        message=("Ended call" if ok else "Hangup request failed"),
        meta={"call_sid": call.CallSid, "provider": call.Provider},
        request=request,
    )
    db.commit()
    db.refresh(call)
    return call_out(call)


@router.post(
    "/calls/{call_id}/sync",
    response_model=CallSyncOut,
    summary="Pull the call transcript into the lead and re-score it",
)
def sync_call(
    call_id: str,
    request: Request,
    principal: Principal = Depends(require("call.read")),
    db: Session = Depends(get_leadai_db),
):
    """Idempotent. Call this after a call ends (or let the Exotel webhook do it)
    to mirror the transcript from the existing `conversations` table into the lead
    thread, then re-qualify from the combined chat + call history."""
    client_id = resolve_scope(principal)
    client = _company(db, client_id)
    call = _call(db, call_id, client_id)

    result = call_bridge.sync_call_transcript(db, client_id, client.Name, call)

    activity.log_principal(
        db,
        principal,
        action=A.CALL_SYNCED,
        client_id=client_id,
        entity_type="call",
        entity_id=call.Id,
        message=f"Synced {result.get('imported', 0)} transcript turns",
        meta=result,
        request=request,
    )
    db.commit()
    return CallSyncOut(**result)


@router.post(
    "/calls/{call_id}/turn",
    response_model=VoiceTurnOut,
    summary="Simulate one voice turn (testing without a carrier)",
)
def voice_turn(
    call_id: str,
    payload: VoiceTurnIn,
    request: Request,
    principal: Principal = Depends(require("call.initiate")),
    db: Session = Depends(get_leadai_db),
):
    """Runs ONE turn of the voice brain over HTTP.

    In production this loop lives inside the existing `/media-stream` handler and
    is driven by Sarvam STT frames. This endpoint exists so the full
    RAG -> answer -> handoff -> qualification chain can be exercised and demoed
    without a live carrier leg: `utterance` stands in for what STT transcribed,
    and the returned `tts.text` is what would be sent to Sarvam TTS.
    """
    client_id = resolve_scope(principal)
    client = _company(db, client_id)
    call = _call(db, call_id, client_id)
    conversation = _conversation(db, call.ConversationId, principal, client_id)

    history = (
        db.query(LeadMessage)
        .filter(
            LeadMessage.ConversationId == conversation.Id,
            LeadMessage.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadMessage.CreatedAt.asc())
        .all()
    )

    inbound = LeadMessage(
        ClientId=client_id,
        ConversationId=conversation.Id,
        Sender="customer",
        Content=payload.utterance.strip(),
        CallSid=call.CallSid,
        CreatedBy="voice",
    )
    db.add(inbound)
    db.flush()
    history.append(inbound)

    # channel="voice" selects the voice prompt template and the tighter token cap.
    result = ai_engine.answer(
        db,
        client_id,
        client.Name,
        payload.utterance,
        history=history,
        channel="voice",
        script=None,
    )

    if result["needs_human"]:
        reply_text = (
            "Let me bring in a specialist who can help with that — connecting you now."
        )
        call.HandedOff = True
        call.Status = "transferred"
        conversation.Status = "needs_human"
        conversation.HandoffReason = (result["handoff_reason"] or "")[:300]
    else:
        reply_text = result["reply"]

    outbound = LeadMessage(
        ClientId=client_id,
        ConversationId=conversation.Id,
        Sender="ai",
        Content=reply_text,
        Confidence=result["confidence"],
        SourcesJson=result["sources"],
        ModelUsed=result["model"],
        LatencyMs=result["latency_ms"],
        CallSid=call.CallSid,
        CreatedBy="voice",
    )
    db.add(outbound)
    db.flush()
    history.append(outbound)

    # Rough per-turn duration so call analytics are meaningful in simulation.
    call.DurationSec = (call.DurationSec or 0) + 18

    lead = db.query(Lead).filter(Lead.ConversationId == conversation.Id).one_or_none()
    if lead is None:
        lead = Lead(ClientId=client_id, ConversationId=conversation.Id, CreatedBy="voice")
        db.add(lead)
        db.flush()
    ai_engine.qualify(db, client_id, lead, history)
    conversation.Summary, conversation.NextStep = ai_engine.summarize(
        db, client_id, client.Name, lead, history
    )
    conversation.MessageCount = len(history)
    conversation.LastMessageAt = utcnow()

    activity.log_principal(
        db,
        principal,
        action=A.AI_REPLIED,
        client_id=client_id,
        entity_type="call",
        entity_id=call.Id,
        message=f"Voice turn (confidence {result['confidence']})",
        meta={"handed_off": call.HandedOff, "confidence": result["confidence"]},
        request=request,
    )
    db.commit()

    return VoiceTurnOut(
        reply=reply_text,
        confidence=result["confidence"],
        handed_off=bool(call.HandedOff),
        tts={
            "provider": telephony.tts_provider(),
            "language": call.Language or settings.default_language,
            "text": reply_text,
        },
        lead_status=lead.Status,
        lead_score=lead.Score or 0,
    )


# ===========================================================================
# carrier webhook (public — no user token)
# ===========================================================================


@router.post(
    "/exotel/status",
    include_in_schema=True,
    summary="Exotel status callback (public webhook)",
)
async def exotel_status(request: Request, db: Session = Depends(get_leadai_db)):
    """Terminal call state from Exotel.

    Treated as untrusted: the only thing taken from the payload is the CallSid and
    the status string. The company, conversation and lead are all resolved from our
    own `leadai_calls` row, so a forged payload cannot re-parent a call or reach
    another tenant's data. An unknown Sid is acknowledged with 200 (never retried
    into an error loop) but changes nothing.
    """
    try:
        form = dict(await request.form())
    except Exception:  # noqa: BLE001
        form = {}
    if not form:
        try:
            form = await request.json()
        except Exception:  # noqa: BLE001
            form = {}

    call_sid = (form.get("CallSid") or form.get("Sid") or "").strip()
    raw_status = form.get("Status") or form.get("CallStatus") or ""
    duration = form.get("Duration") or form.get("ConversationDuration") or 0

    if not call_sid:
        return {"success": False, "message": "No CallSid in payload"}

    call = (
        db.query(LeadCall)
        .filter(LeadCall.CallSid == call_sid, LeadCall.IsDeleted == False)  # noqa: E712
        .one_or_none()
    )
    if call is None:
        logger.info("[LeadAI exotel] status for unknown CallSid %s — ignored", call_sid)
        return {"success": True, "message": "Unknown call, ignored"}

    normalised = telephony.normalise_status(raw_status)
    call.Status = normalised
    try:
        call.DurationSec = int(float(duration or 0))
    except (TypeError, ValueError):
        pass
    call.UpdatedAt = utcnow()
    call.UpdatedBy = "exotel-webhook"

    activity.log(
        db,
        action=A.CALL_COMPLETED if normalised == "completed" else "call.status_updated",
        client_id=call.ClientId,
        actor_email="exotel-webhook",
        actor_role="system",
        entity_type="call",
        entity_id=call.Id,
        message=f"Exotel reported status '{normalised}'",
        meta={"call_sid": call_sid, "raw_status": raw_status, "duration": call.DurationSec},
        request=request,
    )

    # On a terminal status, pull the transcript and re-score immediately so the
    # sales team sees the outcome without anyone pressing a button.
    if normalised in ("completed", "failed", "no-answer", "busy", "canceled"):
        client = db.get(Client, call.ClientId)
        if client:
            try:
                call_bridge.sync_call_transcript(db, call.ClientId, client.Name, call)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[LeadAI exotel] transcript sync failed for %s: %s", call_sid, exc)

    db.commit()
    return {"success": True, "status": normalised}
