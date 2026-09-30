"""
One voice turn, end to end, for every voice front end.

Two callers use this:
  * the simulated turn endpoint (POST /voice/calls/{id}/turn), which used to carry its own
    copy of this pipeline;
  * the Pipecat pipeline (LeadAI/voice), which drives real phone audio.

Both therefore get the same brain as chat: the same retrieval and confidence rules, the
same handoff decision, the same engine judge (ENGINE_MODE), the same decision trace, and the
same pause/terminate control. The transport (HTTP, or a phone line) is the only thing that
differs.

TWO PHASES
On a call, every second of silence is felt. Scoring the lead (and summarising the
conversation) can take an LLM round trip, and nothing about it changes what the caller
hears next. So the turn is split:

  handle_voice_turn(..., defer_scoring=True)   persist, decide, reply. Fast.
  score_conversation(...)                      qualify + summarise + threshold. Run it AFTER
                                               the reply has gone out, in its own session.

With defer_scoring=False (the simulated endpoint) both phases run inline, as before.

Commit: handle_voice_turn never commits unless asked (`commit=True`), so the HTTP endpoint
can add its audit row and commit once, exactly as it always did.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from domain.models import Client

from .. import activity
from ..engine import bridge as engine_bridge
from ..engine import control as engine_control
from ..engine.trace import TurnTrace
from ..engine.trace import step as trace_step
from ..models import Lead, LeadCall, LeadConversation, LeadMessage, utcnow
from . import ai_engine, llm, memory, reply_cleanup, script_engine
from .language import (  # noqa: F401  (re-exported)
    _LANGUAGES,
    detect_language,
    language_note,
    resolve_language,
    same_language,
)
from .conversation_flow import _event, apply_threshold

logger = logging.getLogger(__name__)

# Spoken when a turn hands off to a human. It replaces the model's reply: on a call the
# customer must never hear a half-answer followed by silence.
TRANSFER_LINE = "Let me bring in a specialist who can help with that — connecting you now."

# Spoken on a LIVE call when the customer asks for a person. There is no live transfer to a
# human yet, so promising "connecting you now" and then ending the call (as the first version
# did) leaves the customer with a dropped call. Say what will really happen, and keep the call
# going: staff are flagged to follow up.
#
# Localized like closing_line() below, and for the same reason: a live call caught this fixed
# English line being spoken to a caller mid-Hindi-conversation. Both this and UNSURE_LINE
# REPLACE the model's own reply (which may be in any language, or absent), so — unlike a normal
# answer, where the spoken language follows whatever the model actually wrote — these two must
# always follow the CALLER's language explicitly; nothing here comes from the model to follow.
_CALLBACK = {
    "hi": "ज़रूर। मैं हमारी टीम से किसी विशेषज्ञ से आपको जल्द ही कॉल बैक करवाता हूँ। क्या आप कुछ बताना चाहेंगे जो मैं उन्हें बता दूँ?",
    "pa": "ਜ਼ਰੂਰ। ਮੈਂ ਸਾਡੀ ਟੀਮ ਦੇ ਕਿਸੇ ਮਾਹਿਰ ਤੋਂ ਤੁਹਾਨੂੰ ਜਲਦੀ ਹੀ ਕਾਲ ਬੈਕ ਕਰਵਾਵਾਂਗਾ। ਕੀ ਤੁਸੀਂ ਕੁਝ ਦੱਸਣਾ ਚਾਹੋਗੇ ਜੋ ਮੈਂ ਉਹਨਾਂ ਨੂੰ ਦੱਸ ਦੇਵਾਂ?",
    "en": ("Of course. I'll have a specialist from our team call you back shortly. "
           "Is there anything you'd like me to pass on to them?"),
}
# Spoken when the engine refuses a reply because it stated a figure the company's knowledge
# does not contain: better an honest hand-off than a wrong price on the phone.
_UNSURE = {
    "hi": "मैं चाहता हूँ कि आपको सही जानकारी मिले, इसलिए एक विशेषज्ञ इसकी पुष्टि करके आपको कॉल करेंगे। क्या मैं किसी और चीज़ में मदद कर सकता हूँ?",
    "pa": "ਮੈਂ ਚਾਹੁੰਦਾ ਹਾਂ ਕਿ ਤੁਹਾਨੂੰ ਸਹੀ ਜਾਣਕਾਰੀ ਮਿਲੇ, ਇਸ ਲਈ ਇੱਕ ਮਾਹਿਰ ਇਸਦੀ ਪੁਸ਼ਟੀ ਕਰਕੇ ਤੁਹਾਨੂੰ ਕਾਲ ਕਰਨਗੇ। ਕੀ ਮੈਂ ਕਿਸੇ ਹੋਰ ਚੀਜ਼ ਵਿੱਚ ਮਦਦ ਕਰ ਸਕਦਾ ਹਾਂ?",
    "en": ("I want to be sure I give you the right details, so I'll have a specialist confirm that "
           "and call you back. Is there anything else I can help with?"),
}


def callback_line(language: str | None) -> str:
    return _CALLBACK.get((language or "").strip().lower().split("-")[0], _CALLBACK["en"])


def unsure_line(language: str | None) -> str:
    return _UNSURE.get((language or "").strip().lower().split("-")[0], _UNSURE["en"])

# Rough per-turn duration, ONLY for the simulated endpoint, so call analytics mean
# something without a carrier. Real calls use the carrier's measured duration.
SIMULATED_TURN_SECONDS = 18


@dataclass
class VoiceTurnResult:
    reply_text: str                       # what is spoken ("" when the AI stays silent)
    result: dict                          # the answer: confidence, sources, needs_human, ...
    handed_off: bool
    ends_call: bool = False               # hang up (or transfer) once the reply has been spoken
    skipped: bool = False                 # paused/terminated: nothing was answered
    superseded: bool = False              # the caller spoke again first: nothing was saved
    language: str | None = None           # the language this turn was answered in (e.g. "hi-IN")
    outbound_id: str | None = None        # the AI message row, for the later scoring phase
    history: list = field(default_factory=list)
    trace: TurnTrace | None = None


# ------------------------------------------------------------------------ farewells
# "Okay, bye." on the second live call: Smart Turn waited 3 s, the brain then spent 2.9 s on a
# knowledge-base lookup and a model call, and the caller had hung up before hearing the reply.
# A short goodbye needs neither. It gets a fixed closing line, instantly, and the call ends.
# Goodbye words: anywhere in a short utterance.
_FAREWELL = re.compile(
    r"\b(bye|goodbye|good bye|see you|talk (to you )?later|take care|"
    r"have a (great|good|nice|lovely) day|alvida|dhanyavaad|dhanyawad|shukriya)\b",
    re.IGNORECASE,
)
# Closing phrases: only when they END the utterance. "That's all the flats you have?" is a
# question, not a goodbye.
_FAREWELL_END = re.compile(
    r"\b(that'?s (all|it)|nothing else|no more questions|ok(ay)?,? done|"
    r"thanks?,? (bye|that'?s all))\W*$",
    re.IGNORECASE,
)
_FAREWELL_NATIVE = ("बाय", "अलविदा", "धन्यवाद", "शुक्रिया", "ਧੰਨਵਾਦ", "ਅਲਵਿਦਾ", "ਬਾਏ")
_MAX_FAREWELL_WORDS = 6     # longer than this and "bye" is part of something else

_CLOSING = {
    "hi": "धन्यवाद! आपका दिन शुभ हो।",
    "pa": "ਧੰਨਵਾਦ! ਤੁਹਾਡਾ ਦਿਨ ਵਧੀਆ ਰਹੇ।",
    "en": "Thank you for your time. Have a great day!",
}


def is_farewell(text: str | None) -> bool:
    """True for a SHORT goodbye. Deliberately conservative: a bare "thanks" or "good day" can
    open or continue a conversation, so neither counts on its own."""
    text = (text or "").strip()
    if not text or "?" in text or len(text.split()) > _MAX_FAREWELL_WORDS:
        return False
    return bool(_FAREWELL.search(text) or _FAREWELL_END.search(text)
                or any(tok in text for tok in _FAREWELL_NATIVE))


def closing_line(language: str | None) -> str:
    return _CLOSING.get((language or "").strip().lower().split("-")[0], _CLOSING["en"])


# ------------------------------------------------------------------------ language
# Language detection and the reply-language check live in services/language.py (shared with the
# answering code); re-exported here so callers keep using voice_flow.detect_language etc.


def opening_language(db: Session, conversation: LeadConversation) -> str | None:
    """The language a returning customer has been using, from their last few messages."""
    recent = [m.Content for m in load_history(db, conversation.Id)
              if m.Sender == "customer" and (m.Content or "").strip()][-3:]
    return detect_language(" ".join(recent)) if recent else None


def _has_non_latin_letters(text: str) -> bool:
    return any(ch.isalpha() and ord(ch) > 127 for ch in text or "")


def english_query(utterance: str, trace: TurnTrace | None = None) -> str | None:
    """An English search query for a question asked in another script, else None.

    The knowledge base is written in English and the retriever only extracts keywords from
    Latin letters, so a Hindi or Punjabi question scored as confidence ~0 and was flagged
    as a hand-off on nearly every turn. One short model call (about half a second on a warm
    connection) fixes that, and only for non-Latin turns.
    """
    if not _has_non_latin_letters(utterance):
        return None
    text, meta = llm.complete(
        "Translate the customer's spoken words into ONE short English sentence, to be used as "
        "a search query over a property company's knowledge base. Output ONLY that sentence.",
        [{"role": "user", "content": utterance}],
        temperature=0.0, max_tokens=40, profile="voice",
    )
    text = (text or "").strip().strip('"').strip()
    trace_step(trace, "query_translation",
               "translated for retrieval" if text else "translation unavailable; using the original",
               latency_ms=(meta or {}).get("latency_ms"), error=(meta or {}).get("error"))
    return text or None


def load_history(db: Session, conversation_id: str) -> list[LeadMessage]:
    return (
        db.query(LeadMessage)
        .filter(
            LeadMessage.ConversationId == conversation_id,
            LeadMessage.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadMessage.CreatedAt.asc())
        .all()
    )


def _get_or_create_lead(db: Session, client_id: str, conversation_id: str, actor: str) -> Lead:
    lead = db.query(Lead).filter(Lead.ConversationId == conversation_id).one_or_none()
    if lead is None:
        lead = Lead(ClientId=client_id, ConversationId=conversation_id, CreatedBy=actor)
        db.add(lead)
        db.flush()
    return lead


def score_conversation(
    db: Session,
    client: Client,
    conversation: LeadConversation,
    *,
    history: list[LeadMessage] | None = None,
    trace: TurnTrace | None = None,
    request=None,
    actor: str = "voice",
) -> Lead:
    """Qualify the lead, refresh the summary and evaluate the dashboard threshold.

    Caller commits. Used inline by the simulated endpoint and, for real calls, after the
    reply has been spoken. It reloads the history when not given one, so it is safe to run
    later in a fresh session.
    """
    history = history if history is not None else load_history(db, conversation.Id)
    lead = _get_or_create_lead(db, client.Id, conversation.Id, actor)
    ai_engine.qualify(db, client.Id, lead, history, trace=trace)
    conversation.Summary, conversation.NextStep = ai_engine.summarize(
        db, client.Id, client.Name, lead, history, trace=trace
    )
    conversation.MessageCount = len(history)
    conversation.LastMessageAt = utcnow()
    apply_threshold(db, client, conversation, lead, request, trace=trace)
    return lead


def handle_voice_turn(
    db: Session,
    client: Client,
    conversation: LeadConversation,
    call: LeadCall,
    utterance: str,
    *,
    trace: TurnTrace | None = None,
    simulate_duration: bool = False,
    defer_scoring: bool = False,
    commit: bool = False,
    request=None,
    actor: str = "voice",
    live_call: bool = False,
    language: str | None = None,
    superseded: Callable[[], bool] | None = None,
) -> VoiceTurnResult:
    """Process one thing the caller said. See the module docstring.

    live_call=True is a real phone call. The difference is what a hand-off means. The
    simulated endpoint keeps the original behaviour (a fixed "connecting you now" line and
    the call marked transferred). On a real call nothing can actually transfer, so a hand-off
    FLAGS the conversation for staff and the call carries on; only the model's own
    end-of-conversation signal ends it.

    `language` is the caller's language as reported by speech-to-text (e.g. "hi-IN").

    `superseded` says whether the caller has already spoken again. On a call people talk in
    bursts, and a reply prepared for a sentence they have since added to is never spoken. The
    second live call saved such replies (and scored the lead on them), so the model "remembered"
    saying things the caller never heard. If superseded when the reply is ready, the whole turn
    is rolled back: nothing is stored, and the caller's words are merged into their next turn.
    """
    client_id = client.Id
    utterance = (utterance or "").strip()
    language = resolve_language(utterance, language)
    history = load_history(db, conversation.Id)
    trace = trace or TurnTrace(conversation_id=conversation.Id, client_id=client_id, channel="voice")
    trace_step(trace, "receive", "voice turn", msg_chars=len(utterance),
               history_msgs=len(history), call_status=call.Status,
               control_status=engine_control.get_control(conversation))

    # Cross-channel carry-over, only at the start of a thread (as in chat): a returning
    # customer is not met as a stranger.
    carryover = memory.customer_memory(db, conversation) if len(history) <= 2 else ""

    inbound = LeadMessage(
        ClientId=client_id,
        ConversationId=conversation.Id,
        Sender="customer",
        Content=utterance,
        CallSid=call.CallSid,
        CreatedBy=actor,
    )
    db.add(inbound)
    db.flush()
    history.append(inbound)
    _event(db, "turn.received", conversation, speaker="customer", text=utterance,
           turn_id=call.CallSid, source="voice")

    # Stopped from outside (paused / terminated) by staff or the monitor agent: keep what
    # the caller said, say nothing, and end the call. Same rule as chat.
    if engine_control.is_stopped(conversation):
        status = engine_control.get_control(conversation)
        trace_step(trace, "control", f"stopped: {status}; AI silent, call should end",
                   reason=conversation.ControlReason, set_by=conversation.ControlBy)
        _event(db, "turn.skipped", conversation, speaker="system", reason=f"conversation {status}")
        inbound.TraceJson = trace.as_json()
        conversation.MessageCount = len(history)
        if commit:
            db.commit()
        return VoiceTurnResult("", {}, handed_off=False, ends_call=True, skipped=True,
                               history=history, trace=trace)

    if live_call and is_farewell(utterance):
        # A short goodbye: no retrieval, no model call, a fixed localized closing, and the
        # call ends. The lead is still scored afterwards, as for any turn.
        result = {
            "reply": closing_line(language), "confidence": 1.0, "needs_human": False,
            "ends_conversation": True, "wants_human": False, "handoff_reason": None,
            "sources": [], "model": "closing-template", "latency_ms": 0,
        }
        note = None
        trace_step(trace, "farewell", "caller is saying goodbye: fixed closing line, no retrieval, no model call",
                   language=language)
    else:
        # Once the thread outgrows the model's window, re-supply what is already established
        # (facts, summary), exactly as chat does. Without it a returning caller's earlier
        # details were invisible to the phone brain.
        state_note = memory.thread_state_note(db, conversation, history)
        existing_lead = db.query(Lead).filter(Lead.ConversationId == conversation.Id).one_or_none()
        datapoints_note = memory.missing_data_points_note(db, client_id, existing_lead)
        trace_step(trace, "memory", "context for the model",
                   carryover_chars=len(carryover), state_note_chars=len(state_note),
                   thread_truncated=memory.thread_is_truncated(history))

        lang_note = language_note(language)
        trace_step(trace, "language", f"caller is speaking {language}" if lang_note else "language unknown",
                   code=language)
        english = english_query(utterance, trace)

        if superseded is not None and superseded():
            # Already stale before the expensive call even starts — the caller spoke again,
            # or (a live hangup mid-turn) the call itself ended, while the cheap prep above was
            # still running. A live call was seen paying for a full retrieve+generate cycle
            # (several seconds, two OpenAI calls) for a reply that was always going to be
            # discarded at the existing post-generate check below.
            db.rollback()
            trace_step(trace, "superseded", "the caller spoke again first: reply dropped, nothing saved")
            return VoiceTurnResult("", {}, handed_off=False, superseded=True, history=history, trace=trace)

        # channel="voice" selects the voice prompt template and the tighter token cap.
        result = ai_engine.answer(
            db, client_id, client.Name, utterance, history=history, channel="voice",
            script=None, trace=trace, carryover=carryover,
            session_note="\n\n".join(n for n in (state_note, lang_note, datapoints_note) if n),
            query_override=english,
            reply_language=language,
        )
        # The same judge chat uses (grounding + "declined in words"); a no-op unless ENGINE_MODE.
        result = engine_bridge.apply(
            result, text=utterance, client_id=client_id, conversation_id=conversation.Id,
            channel="voice",
        )
        note = result.get("engine")
        trace_step(
            trace, "engine", "off" if not note else f"{note['mode']}: verdict={note['verdict']}",
            **({} if not note else {
                "unsupported_figures": note["unsupported_count"],
                "declined_in_words": note["declined"], "escalation": note["escalation"]}),
        )


    if superseded is not None and superseded():
        # The caller spoke again while this was being prepared. Discard everything (the
        # inbound message, events and counters are all still uncommitted), and let the
        # caller's words be merged into their next turn.
        db.rollback()
        trace_step(trace, "superseded", "the caller spoke again first: reply dropped, nothing saved")
        return VoiceTurnResult("", {}, handed_off=False, superseded=True, history=history, trace=trace)

    # Two different questions get answered here, and they must not be conflated:
    #   caller_language  what the CALLER is speaking — always correct, used for any line WE
    #                    write ourselves (callback_line, unsure_line).
    #   language         what the text actually being SPOKEN is written in — follows the
    #                    model's own reply when that is what gets spoken, but must NOT be
    #                    trusted for a fixed line we substitute instead. A live call caught
    #                    exactly this: the extractive fallback (human-request path) quotes
    #                    English knowledge-base text, result["language"] correctly reported
    #                    "en-IN" for THAT text, and callback_line was then spoken in English
    #                    to a Hindi caller because `language` had been overwritten before the
    #                    substitution below ever ran.
    caller_language = language
    language = result.get("language") or language

    handed_off = bool(result["needs_human"])
    if handed_off and live_call:
        # No live transfer exists, so do not pretend and do not hang up. Flag the
        # conversation for staff; keep the call going with something true to say.
        if result.get("wants_human"):
            reply_text = callback_line(caller_language)
            language = caller_language
            why = "customer asked for a person: promise a callback"
        elif (note or {}).get("unsupported_count"):
            reply_text = unsure_line(caller_language)
            language = caller_language
            why = "reply stated a figure not in company knowledge: withheld"
        else:
            reply_text = (result["reply"] or "").strip()
            if reply_text:
                why = "not confident: speaking the model's honest answer"
            else:
                reply_text = callback_line(caller_language)
                language = caller_language
                why = "not confident and nothing to say: promise a callback"
        trace_step(trace, "voice_handoff", f"flagged for a human follow-up; the call continues ({why})",
                   reason=result["handoff_reason"])
        call.HandedOff = True
        # An assignee already means a human owns this conversation (same invariant
        # inbox.set_status enforces) — a low-confidence turn on a live call must not
        # knock it back into the unclaimed needs_human queue out from under them.
        if conversation.Status != "assigned":
            conversation.Status = "needs_human"
        conversation.HandoffReason = (result["handoff_reason"] or "")[:300]
        _event(db, "handoff.requested", conversation, speaker="ai",
               reason=conversation.HandoffReason, confidence=result["confidence"])
    elif handed_off:
        trace_step(trace, "voice_handoff",
                   "transferring: the spoken reply is replaced by a fixed transfer line",
                   reason=result["handoff_reason"])
        reply_text = TRANSFER_LINE
        call.HandedOff = True
        call.Status = "transferred"
        if conversation.Status != "assigned":
            conversation.Status = "needs_human"
        conversation.HandoffReason = (result["handoff_reason"] or "")[:300]
        _event(db, "handoff.requested", conversation, speaker="ai",
               reason=conversation.HandoffReason, confidence=result["confidence"])
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
        CreatedBy=actor,
    )
    db.add(outbound)
    db.flush()
    history.append(outbound)
    _event(db, "turn.replied", conversation, speaker="ai", text=reply_text,
           confidence=result["confidence"], latency_ms=result["latency_ms"],
           model=result["model"], sources=len(result["sources"]), needs_human=handed_off,
           **engine_bridge.audit_meta(result))

    if simulate_duration:
        call.DurationSec = (call.DurationSec or 0) + SIMULATED_TURN_SECONDS

    if defer_scoring:
        trace_step(trace, "scoring", "deferred until after the reply is spoken")
        conversation.MessageCount = len(history)
    else:
        score_conversation(db, client, conversation, history=history, trace=trace,
                           request=request, actor=actor)

    trace_step(trace, "commit", "voice turn committed", handed_off=handed_off,
               reply_chars=len(reply_text or ""))
    outbound.TraceJson = trace.as_json()
    if commit:
        if superseded is not None and superseded():
            db.rollback()
            trace_step(trace, "superseded", "the caller spoke again first: reply dropped, nothing saved")
            return VoiceTurnResult("", {}, handed_off=False, superseded=True, history=history, trace=trace)
        db.commit()

    return VoiceTurnResult(
        reply_text=reply_text,
        result=result,
        language=language,
        handed_off=handed_off,
        # A live call ends only when the conversation is really over; a hand-off flags staff.
        ends_call=(bool(result.get("ends_conversation")) if live_call
                   else handed_off or bool(result.get("ends_conversation"))),
        outbound_id=outbound.Id,
        history=history,
        trace=trace,
    )


def run_deferred_scoring(db: Session, client_id: str, conversation_id: str, message_id: str | None) -> None:
    """Phase 2 for a real call, in its own session, after the reply has been spoken.

    Appends its steps to the AI message's trace, so "why did the AI say that" and "how was
    the lead scored" read as one record. Never raises: a scoring failure must not end a call.
    """
    try:
        client = db.get(Client, client_id)
        conversation = db.get(LeadConversation, conversation_id)
        if client is None or conversation is None:
            return
        trace = TurnTrace(conversation_id=conversation_id, client_id=client_id, channel="voice")
        trace_step(trace, "post_turn", "scoring after the reply was spoken")
        score_conversation(db, client, conversation, trace=trace)
        message = db.get(LeadMessage, message_id) if message_id else None
        if message is not None and message.TraceJson and trace.as_json():
            merged = dict(message.TraceJson)
            merged["steps"] = list(merged.get("steps", [])) + trace.as_json()["steps"]
            message.TraceJson = merged
        db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("[LeadAI voice] deferred scoring failed for conv %s", conversation_id, exc_info=True)
        db.rollback()


def _returning_opener(
    db: Session, client: Client, conversation: LeadConversation, language: str | None = None
) -> tuple[str, dict]:
    """The opening line for someone we have already spoken to: a follow-up, not a fresh pitch.

    The first live call opened a customer with 96 earlier messages using the "I can help you
    with apartments..." greeting, and they answered "actually, we've spoken before". So a
    returning customer gets a short line that knows it.
    """
    system, _script = script_engine.build_system_prompt(
        db, client.Id, client.Name, channel="voice", script=None, wants_human=False
    )
    digest = memory.customer_memory(db, conversation, include_current=True)
    messages = []
    if digest:
        messages.append({"role": "system", "content": (
            "Background on this returning customer, from earlier chats and calls. Use it "
            "naturally; do NOT ask again for anything already stated here.\n" + digest)})
    lang_note = language_note(language)
    if lang_note:
        messages.append({"role": "system", "content": lang_note})
    # "At most 20 words": the first version ran to 23 words (about ten seconds of speech) and the
    # caller waited it out in silence.
    messages.append({"role": "user", "content": (
        "The call has just connected. Speak ONLY your opening line: greet them by name if you "
        "know it, say you are following up on their earlier enquiry (name the topic if known), "
        "and ask how you can help. At most 20 words, spoken aloud.")})
    text, meta = llm.complete(system, messages, profile="voice", max_tokens=90)
    text = reply_cleanup.strip_control_tokens((text or "").strip())
    if not text:
        text = f"Hello! This is {client.Name}, following up on your earlier enquiry. How can I help you today?"
        meta = {**(meta or {}), "model": "opening-template"}
    return text, meta or {}


def opening_line(
    db: Session,
    client: Client,
    conversation: LeadConversation,
    call: LeadCall,
    *,
    actor: str = "voice",
    commit: bool = True,
    language: str | None = None,
) -> tuple[str, str | None]:
    """What the AI says when the call connects. Returns (text, message_id).

    `language` is the language the returning customer has been using (see opening_language),
    so someone who chats in Hindi is not greeted in English.

    A brand-new customer gets the company's own greeting prompt (forced through the
    greeting branch even if the conversation has system rows). A RETURNING customer gets a
    follow-up line that uses what we know about them. Either way it is stored as an AI
    message, so the transcript is complete.
    """
    if engine_control.is_stopped(conversation):
        return "", None
    trace = TurnTrace(conversation_id=conversation.Id, client_id=client.Id, channel="voice")
    prior = [m for m in load_history(db, conversation.Id)
             if (m.Sender or "") in ("customer", "ai", "agent") and (m.Content or "").strip()]
    if prior:
        trace_step(trace, "opening", "call connected: returning customer, speaking a follow-up line",
                   earlier_messages=len(prior))
        text, meta = _returning_opener(db, client, conversation, language)
        model, latency, confidence, sources = meta.get("model"), meta.get("latency_ms", 0), 1.0, []
        trace_step(trace, "generate", "llm opener" if meta.get("model") != "opening-template" else "template opener",
                   model=model, latency_ms=latency, error=meta.get("error"))
    else:
        trace_step(trace, "opening", "call connected: new customer, speaking the greeting")
        result = ai_engine.answer(
            db, client.Id, client.Name, "hello", history=[], channel="voice", script=None, trace=trace
        )
        text, model, latency = result["reply"], result["model"], result["latency_ms"]
        confidence, sources = result["confidence"], result["sources"]
    message = LeadMessage(
        ClientId=client.Id,
        ConversationId=conversation.Id,
        Sender="ai",
        Content=text,
        Confidence=confidence,
        SourcesJson=sources,
        ModelUsed=model,
        LatencyMs=latency,
        CallSid=call.CallSid,
        CreatedBy=actor,
        TraceJson=trace.as_json(),
    )
    db.add(message)
    db.flush()
    conversation.MessageCount = (conversation.MessageCount or 0) + 1
    conversation.LastMessageAt = utcnow()
    if commit:
        db.commit()
    return text, message.Id
