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
import unicodedata
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
from .conversation_flow import _event, apply_threshold

logger = logging.getLogger(__name__)

# Spoken when a turn hands off to a human. It replaces the model's reply: on a call the
# customer must never hear a half-answer followed by silence.
TRANSFER_LINE = "Let me bring in a specialist who can help with that — connecting you now."

# Spoken on a LIVE call when the customer asks for a person. There is no live transfer to a
# human yet, so promising "connecting you now" and then ending the call (as the first version
# did) leaves the customer with a dropped call. Say what will really happen, and keep the call
# going: staff are flagged to follow up.
CALLBACK_LINE = (
    "Of course. I'll have a specialist from our team call you back shortly. "
    "Is there anything you'd like me to pass on to them?"
)
# Spoken when the engine refuses a reply because it stated a figure the company's knowledge
# does not contain: better an honest hand-off than a wrong price on the phone.
UNSURE_LINE = (
    "I want to be sure I give you the right details, so I'll have a specialist confirm that "
    "and call you back. Is there anything else I can help with?"
)

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
# Speech-to-text reports the language of every utterance. Nothing told the model, so on the
# second live call it answered a Hindi caller in Punjabi. The note below is added to every
# turn, and the pipeline switches the text-to-speech language to match.
_LANGUAGES = {
    "hi": ("Hindi", "Devanagari script"),
    "en": ("English", None),
    "pa": ("Punjabi", "Gurmukhi script"),
    "bn": ("Bengali", "Bengali script"),
    "gu": ("Gujarati", "Gujarati script"),
    "kn": ("Kannada", "Kannada script"),
    "ml": ("Malayalam", "Malayalam script"),
    "mr": ("Marathi", "Devanagari script"),
    "od": ("Odia", "Odia script"),
    "or": ("Odia", "Odia script"),
    "ta": ("Tamil", "Tamil script"),
    "te": ("Telugu", "Telugu script"),
}


def language_note(code: str | None) -> str:
    """An instruction naming the language to reply in. "" when the language is unknown."""
    name, script = _LANGUAGES.get((code or "").strip().lower().split("-")[0], (None, None))
    if not name:
        return ""
    where = f" ({script})" if script else ""
    return (
        f"The caller is speaking {name}. Reply in {name}{where}, in short spoken sentences. "
        "Keep product names and words the caller used in English (such as 'BHK') in English."
    )


# Which language is a piece of text in? Speech-to-text labels each utterance, but a burst arrives
# as short fragments, and short fragments are labelled unreliably: on the third live call a merged
# turn that was mostly Hindi ("क्या जी? क्यों नहीं आ रहा? Okay, okay, back end.") carried the label of
# its last fragment (en-IN), so the model was told English and the voice was switched to English
# while speaking Hindi. The SCRIPT the words were written in is far more reliable.
_SCRIPTS = (
    (0x0900, 0x097F, "hi-IN"),   # Devanagari (Hindi; Marathi shares it, see _SAME_SCRIPT)
    (0x0980, 0x09FF, "bn-IN"),
    (0x0A00, 0x0A7F, "pa-IN"),   # Gurmukhi
    (0x0A80, 0x0AFF, "gu-IN"),
    (0x0B00, 0x0B7F, "od-IN"),
    (0x0B80, 0x0BFF, "ta-IN"),
    (0x0C00, 0x0C7F, "te-IN"),
    (0x0C80, 0x0CFF, "kn-IN"),
    (0x0D00, 0x0D7F, "ml-IN"),
)
_SAME_SCRIPT = {"mr": "hi", "or": "od"}   # languages that share a script with the one detected


def _script_of(ch: str) -> str | None:
    """The language code for the script of one character, or None for digits, spaces, punctuation.

    Combining marks (Hindi vowel signs, the virama) count: str.isalpha() is False for them, so
    counting only "letters" under-counted Indic text and made a mostly-Hindi sentence look Latin.
    """
    if unicodedata.category(ch)[0] not in ("L", "M"):
        return None
    cp = ord(ch)
    if cp < 0x250:
        return "en-IN" if ch.isalpha() else None
    return next((c for lo, hi, c in _SCRIPTS if lo <= cp <= hi), None)


def detect_language(text: str | None) -> str | None:
    """The language of `text` judged by script, or None when it is unclear (no words, or a tie).

    WORDS are counted, each by its own script: "क्या जी? क्यों नहीं आ रहा? Okay, okay, back end."
    is six Hindi words against four English ones, so Hindi.
    """
    counts: dict[str, int] = {}
    for token in (text or "").split():
        letters: dict[str, int] = {}
        for ch in token:
            code = _script_of(ch)
            if code:
                letters[code] = letters.get(code, 0) + 1
        if letters:
            word = max(letters, key=letters.get)
            counts[word] = counts.get(word, 0) + 1
    if not counts:
        return None
    top = max(counts.values())
    leaders = [c for c, n in counts.items() if n == top]
    return leaders[0] if len(leaders) == 1 else None


def _base(code: str | None) -> str:
    return _SAME_SCRIPT.get((code or "").lower().split("-")[0], (code or "").lower().split("-")[0])


def resolve_language(text: str | None, reported: str | None) -> str | None:
    """The script wins over the speech-to-text label; the label only refines within a script
    (Marathi vs Hindi share Devanagari) or fills in when the text has no letters."""
    detected = detect_language(text)
    if detected is None:
        return reported
    if reported and _base(reported) == _base(detected):
        return reported
    return detected


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
        trace_step(trace, "memory", "context for the model",
                   carryover_chars=len(carryover), state_note_chars=len(state_note),
                   thread_truncated=memory.thread_is_truncated(history))

        lang_note = language_note(language)
        trace_step(trace, "language", f"caller is speaking {language}" if lang_note else "language unknown",
                   code=language)
        english = english_query(utterance, trace)

        # channel="voice" selects the voice prompt template and the tighter token cap.
        result = ai_engine.answer(
            db, client_id, client.Name, utterance, history=history, channel="voice",
            script=None, trace=trace, carryover=carryover,
            session_note="\n\n".join(n for n in (state_note, lang_note) if n),
            query_override=english,
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

    handed_off = bool(result["needs_human"])
    if handed_off and live_call:
        # No live transfer exists, so do not pretend and do not hang up. Flag the
        # conversation for staff; keep the call going with something true to say.
        if result.get("wants_human"):
            reply_text, why = CALLBACK_LINE, "customer asked for a person: promise a callback"
        elif (note or {}).get("unsupported_count"):
            reply_text, why = UNSURE_LINE, "reply stated a figure not in company knowledge: withheld"
        else:
            reply_text = (result["reply"] or "").strip() or CALLBACK_LINE
            why = "not confident: speaking the model's honest answer"
        trace_step(trace, "voice_handoff", f"flagged for a human follow-up; the call continues ({why})",
                   reason=result["handoff_reason"])
        call.HandedOff = True
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
