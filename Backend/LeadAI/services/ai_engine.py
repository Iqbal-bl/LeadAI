"""
The reasoning layer: answer, qualify, summarise, decide on handoff.

THREE THINGS HAPPEN ON EVERY CUSTOMER TURN
------------------------------------------
1. ANSWER — retrieve from the company's knowledge base, then generate a reply
   grounded in what came back. The reply carries a CONFIDENCE, computed from
   retrieval quality rather than asked of the model (a model's self-reported
   confidence is worthless; retrieval coverage is measurable).

2. QUALIFY — re-derive the lead's intent/budget/timeline/product/sentiment and a
   0-100 score from the whole conversation. Recomputed from scratch each turn,
   never incremented, so a correction late in the conversation fixes the score
   instead of leaving a stale signal behind.

3. SUMMARISE — a three-line brief plus a recommended next step, so an agent
   picking up a handed-off conversation is productive in ten seconds.

THE HANDOFF RULE
----------------
Escalate to a human when EITHER:
  * the customer asks for one (regex, checked before anything else — a customer
    who says "just put me through" must not be answered with a product FAQ), or
  * confidence < threshold (per-company, default 0.40).

This is the safety property of the whole system: low retrieval coverage produces
a handoff, not a confident guess. It is why the confidence number is computed
from IDF-weighted sentence coverage rather than from the LLM.
"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy.orm import Session

from ..config import settings
from ..engine import monitor
from ..engine.text import split_sentences
from ..engine.trace import TurnTrace
from ..engine.trace import step as trace_step
from ..models import Lead, LeadCompanyDataPoint, LeadCompanySettings, LeadConversation, LeadMessage, LeadProduct
from . import language, llm, memory, reply_cleanup, script_engine, vectorstore

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# signal dictionaries
# --------------------------------------------------------------------------- #
HUMAN_REQUEST = re.compile(
    r"\b(human|agent|advisor|representative|executive|real person|"
    r"talk to (someone|a person)|speak to (someone|a person)|call me|"
    r"customer care|manager)\b",
    re.I,
)

INTENT_SIGNALS = {
    "ready_to_buy": ["apply", "sign up", "open an account", "purchase", "buy", "book",
                     "proceed", "enroll", "onboard", "close the deal", "send the link",
                     "how do i start", "i'll take it", "i will take", "i'll take",
                     "i want to take", "go ahead", "sign me up", "let's proceed",
                     "lets proceed", "let's do it", "lets do it", "i am ready",
                     "i'm ready"],
    "comparing": ["compare", " vs ", "versus", "better than", "difference between",
                  "alternative", "other options", "which one"],
    "evaluating": ["eligibility", "eligible", "documents", "requirement", "process",
                   "fee", "charges", "interest rate", "price", "cost", "premium",
                   "emi", "down payment", "how much"],
    "browsing": ["what is", "tell me about", "info", "information", "how does", "explain"],
}

TIMELINE_SIGNALS = {
    "immediate": ["today", "right now", "immediately", "asap", "urgent", "this week"],
    "this_month": ["this month", "next week", "in a few days", "soon", "shortly"],
    "next_quarter": ["next month", "next quarter", "in a couple of months",
                     "later this year", "few months"],
}

POSITIVE = ["great", "good", "perfect", "interested", "love", "nice", "thanks",
            "thank you", "awesome", "sounds good", "yes please", "excellent"]
NEGATIVE = ["expensive", "costly", "not interested", "bad", "poor", "disappointed",
            "too high", "no thanks", "annoying", "waste", "forget it"]

# Indian money formats: "₹5,00,000", "12 lakh", "8 LPA", "2cr", "50k".
MONEY = re.compile(r"(₹|rs\.?|inr)\s?([\d,.]+)\s?(lakh|lakhs|l|crore|cr|k|thousand)?", re.I)
SALARY_WORDS = re.compile(r"\b(\d+(?:\.\d+)?)\s?(lakh|lakhs|lpa|l|crore|cr|k)\b", re.I)

GREETINGS = ("hi", "hey", "hello", "namaste", "good morning", "good afternoon",
             "good evening", "hola", "yo ")

# "Please speak in Hindi" is not a knowledge question either — same principle
# as GREETINGS below. A real incident: a caller's entire turn was a request to
# switch language; it scored confidence 0.256 against the knowledge base (of
# course — it has nothing to do with the company's products) and the voice
# pipeline treated that as "not confident in an answer" and ended the call.
# Checked against both the caller's own words and the English translation
# (same reason HUMAN_REQUEST is), since the phrase could survive in either.
LANGUAGE_SWITCH_REQUEST = re.compile(
    r"\b(speak|talk|repl(?:y|ied)|continue|switch(?:ing)?(?:\s+to)?|answer)\b[^.?!]{0,20}\b"
    r"(hindi|english|punjabi|bengali|gujarati|kannada|malayalam|marathi|odia|tamil|telugu)\b"
    r"|\b(hindi|punjabi|bengali|gujarati|kannada|malayalam|marathi|odia|tamil|telugu)\s+(?:mein|me)\s+(?:baat|bol)",
    re.I,
)


def _is_language_switch_request(question: str, query_override: str | None) -> bool:
    """A short turn that's ONLY a request to change language, not a real
    question mixed in with one (a longer message mentioning a language
    alongside an actual question must still go through retrieval normally)."""
    longest = max(len(question or ""), len(query_override or ""))
    if longest >= 60:
        return False
    return bool(
        LANGUAGE_SWITCH_REQUEST.search(question or "")
        or LANGUAGE_SWITCH_REQUEST.search(query_override or "")
    )


# --------------------------------------------------------------------------- #
# per-company tuning
# --------------------------------------------------------------------------- #
def company_thresholds(db: Session, client_id: str) -> tuple[float, int]:
    """(handoff_threshold, retrieval_top_k) with company overrides applied.

    Different industries need different caution. A bank wants to escalate early;
    a furniture retailer would rather the bot keep trying. So the threshold is a
    per-company setting, not a global constant.
    """
    row = (
        db.query(LeadCompanySettings)
        .filter(
            LeadCompanySettings.ClientId == client_id,
            LeadCompanySettings.IsDeleted == False,  # noqa: E712
        )
        .one_or_none()
    )
    threshold = settings.handoff_confidence_threshold
    top_k = settings.retrieval_top_k
    if row:
        if row.HandoffThreshold is not None:
            threshold = float(row.HandoffThreshold)
        if row.RetrievalTopK:
            top_k = int(row.RetrievalTopK)
    return threshold, top_k


# --------------------------------------------------------------------------- #
# sentence-level scoring
# --------------------------------------------------------------------------- #
def _sentences(text: str) -> list[str]:
    """Split per line, then per sentence.

    Ingestion already rejoined wrapped lines, so a surviving line break is a real
    boundary (usually a heading). Splitting on it keeps a heading from being
    glued onto the front of the first real sentence of its section.
    """
    out: list[str] = []
    for line in (text or "").split("\n"):
        out.extend(s.strip(" -•\t") for s in split_sentences(line))
    return [s for s in out if s]


def _scored_sentences(
    query: str, hits: list[dict], idf: dict, unseen: float
) -> list[tuple[float, str]]:
    scored: list[tuple[float, str]] = []
    for hit in hits:
        for sentence in _sentences(hit["text"]):
            if len(sentence) < 25:
                continue
            coverage = vectorstore.lexical_coverage(query, sentence, idf, unseen)
            # Small chunk-score bonus so a sentence from a strongly-matching
            # chunk edges out an identical sentence from a weak one.
            scored.append((coverage + 0.15 * hit["score"], sentence))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored


def _best_sentences(
    query: str, hits: list[dict], idf: dict, unseen: float, limit: int = 3
) -> list[str]:
    """Keep only sentences close to the best match, so answers stay on-topic.

    A relative cutoff (70% of the top score) rather than an absolute one: what
    counts as a good match depends on the corpus, and an absolute floor either
    returns junk on sparse corpora or nothing on dense ones.
    """
    scored = _scored_sentences(query, hits, idf, unseen)
    if not scored:
        return []
    cutoff = max(scored[0][0] * 0.7, 0.2)

    picked: list[str] = []
    for score, sentence in scored:
        if score < cutoff:
            break
        # Chunk overlap means the same sentence can arrive twice, sometimes as a
        # fragment of itself; containment catches both cases.
        if any(sentence in p or p in sentence for p in picked):
            continue
        picked.append(sentence)
        if len(picked) == limit:
            break
    return picked


def _is_greeting(text: str) -> bool:
    lowered = (text or "").lower().strip(" !.?")
    return len(lowered) < 32 and any(lowered.startswith(g) for g in GREETINGS)


# --------------------------------------------------------------------------- #
# answering
# --------------------------------------------------------------------------- #
def answer(
    db: Session,
    client_id: str,
    company_name: str,
    question: str,
    history: list[LeadMessage] | None = None,
    channel: str = "chat",
    script=None,
    carryover: str = "",
    session_note: str = "",
    trace: TurnTrace | None = None,
    query_override: str | None = None,
    reply_language: str | None = None,
) -> dict:
    """Answer strictly from this company's knowledge base.

    `trace`, when given, records every decision made here (see engine/trace.py).

    `query_override` is an English version of a question asked in another language. The
    retriever only extracts keywords from Latin letters, so a Hindi or Punjabi question had
    NO keywords: its lexical coverage was always 0, its confidence could never rise above the
    embedding score alone, and nearly every non-English turn was flagged as a hand-off. When
    given, it is used for retrieval, for the confidence score and for spotting a request for
    a human; the model still sees the customer's own words.

    `reply_language` (e.g. "hi-IN") is the language the reply must be in. Earlier assistant replies
    in ANOTHER language are removed from the history the model sees, the instruction is stated at the
    end of the prompt, and the reply's script is checked (see services/language.py: the model answered
    Hindi callers in Punjabi because it imitated its own earlier Punjabi replies).

    `session_note` is an optional instruction about the state of THIS conversation
    (e.g. it is already complete), injected as its own system turn.

    `carryover` is an optional cross-channel memory digest (see services/memory.
    py). It describes the customer, never the company, and is injected as its own
    system turn — deliberately NOT merged into the knowledge context, because the
    "answer only from company knowledge" rule must keep applying to product facts
    while still letting the agent use what it knows about the person.

    Returns: reply, confidence, needs_human, handoff_reason, sources,
             model, latency_ms, script_id.
    """
    history = history or []
    threshold, top_k = company_thresholds(db, client_id)
    trace_step(trace, "thresholds", "loaded", handoff_threshold=threshold, top_k=top_k,
               history_msgs=len(history), channel=channel)

    # A greeting is not a knowledge question. Running "hi" through retrieval
    # scores ~0 and would escalate a customer who has not asked anything yet.
    if _is_greeting(question) and len(history) <= 2:
        greeting = script_engine.get_prompt(db, client_id, company_name, "greeting")
        logger.info(
            "[LeadAI answer] greeting detected — channel=%s prompt_key=greeting",
            channel,
        )
        reply, meta = None, {"model": "greeting-template", "latency_ms": 0}
        if settings.llm_enabled:
            # Use the company's own greeting prompt, so the persona introduces itself
            # ("Hi, I'm Kabir from Kestrel Homes…") and editing that prompt in the
            # dashboard actually changes what customers see. The fixed line below is
            # only the fallback when the LLM is off or the call fails.
            generated, llm_meta = llm.complete(
                greeting, [{"role": "user", "content": question}], max_tokens=120,
                profile="voice" if channel == "voice" else "chat",
            )
            generated = reply_cleanup.strip_control_tokens((generated or "").strip())
            if generated:
                reply, meta = generated, llm_meta
        trace_step(trace, "greeting", "short-circuit: no retrieval, confidence 1.0",
                   llm_used=bool(reply), model=meta.get("model", "greeting-template"),
                   latency_ms=meta.get("latency_ms", 0), prompt_key="greeting")
        return {
            "reply": reply
            or (
                f"Hi! I'm the {company_name} assistant. Ask me anything about our "
                "products and I'll answer from our official information."
            ),
            "confidence": 1.0,
            "needs_human": False,
            "handoff_reason": None,
            "sources": [],
            "model": meta.get("model", "greeting-template"),
            "latency_ms": meta.get("latency_ms", 0),
            "script_id": getattr(script, "Id", None),
            "prompt_used": greeting,
        }

    # A bare "please speak in Hindi" is not a knowledge question — see
    # LANGUAGE_SWITCH_REQUEST above. Acknowledge in whichever language the
    # caller is now speaking (already resolved by the caller of answer()) and
    # invite the real question, instead of running it through retrieval where
    # it has nothing to match and would wrongly end the call.
    if _is_language_switch_request(question, query_override) and len(history) <= 4:
        lang_name, _script_desc = language.language_name(reply_language)
        ack = (
            f"Of course, I'll continue in {lang_name}. What would you like to know?"
            if lang_name
            else "Of course — happy to continue. What would you like to know?"
        )
        logger.info(
            "[LeadAI answer] language-switch request detected — channel=%s reply_language=%s",
            channel, reply_language,
        )
        trace_step(trace, "language_switch", "short-circuit: no retrieval, confidence 1.0",
                   reply_language=reply_language)
        return {
            "reply": ack,
            "confidence": 1.0,
            "needs_human": False,
            "handoff_reason": None,
            "sources": [],
            "model": "language-switch-template",
            "latency_ms": 0,
            "script_id": getattr(script, "Id", None),
            "prompt_used": None,
        }

    scoring_q = query_override or question
    wants_human = bool(HUMAN_REQUEST.search(question or "") or HUMAN_REQUEST.search(query_override or ""))
    trace_step(trace, "human_request", "customer asked for a human" if wants_human else "no")

    # Monitor agent: classify in the background, concurrently with retrieval below, whether
    # this turn is a real knowledge question or general/non-substantive remark (the
    # generalised catch-all behind _is_greeting/_is_language_switch_request — see
    # engine/monitor.py). A request for a human is never general chit-chat, so it's excluded.
    triage_future = None if wants_human else monitor.submit(question, query_override, history, channel)

    # Retrieval runs on a HISTORY-AWARE query, not the raw utterance.
    #
    # "and what about the processing fee?" contains no product noun, so embedding
    # it alone retrieves nothing, confidence collapses, and the handoff rule
    # escalates a question that is squarely inside the knowledge base. memory.
    # retrieval_query() prepends the salient nouns from the last couple of
    # customer turns when — and only when — the utterance looks like a follow-up.
    #
    # Scoring below still uses the ORIGINAL `question`: lexical coverage measures
    # how well a retrieved sentence answers what the customer actually asked, and
    # padding that side with carried-over words would inflate confidence.
    search_query = memory.retrieval_query(scoring_q, history)

    hits = vectorstore.search(db, client_id, search_query, top_k=top_k)
    idf, unseen = vectorstore.idf_map(db, client_id)
    top_score = hits[0]["score"] if hits else 0.0
    trace_step(trace, "retrieve", f"{len(hits)} chunks",
               query_expanded=(search_query != scoring_q), translated=bool(query_override), top_k=top_k,
               top_score=top_score, chunk_ids=[h["chunk_id"] for h in hits],
               scores=[round(h["score"], 3) for h in hits])

    # Sentence-level coverage is a sharper signal than chunk-level: a 900-char
    # chunk can dilute an exact answer that sits in a single line.
    sentence_scores = _scored_sentences(scoring_q, hits, idf, unseen)
    coverage = max(
        (vectorstore.lexical_coverage(scoring_q, s, idf, unseen) for _, s in sentence_scores[:6]),
        default=0.0,
    )
    # Blend: chunk-level retrieval strength (45%) + best-sentence coverage (55%).
    confidence = round(min(1.0, 0.45 * min(top_score / 0.6, 1.0) + 0.55 * coverage), 3)
    trace_step(trace, "confidence", f"{confidence}", top_score=top_score,
               sentence_coverage=coverage, formula="0.45*min(top_score/0.6,1)+0.55*coverage",
               threshold=threshold, meets_threshold=confidence >= threshold)

    triage_verdict = monitor.resolve(triage_future)
    if triage_verdict:
        triage_mode = monitor.current_mode()
        trace_step(trace, "triage", f"{triage_verdict['category']} ({triage_verdict['confidence']})",
                   mode=triage_mode, reason=triage_verdict["reason"])
        if (
            triage_mode == "enforce"
            and triage_verdict["category"] == "general"
            and triage_verdict["confidence"] >= settings.triage_confidence_threshold
        ):
            ack, ack_meta = (
                f"No problem! Feel free to ask me anything about {company_name}'s "
                "products whenever you're ready.",
                {"model": "triage-template", "latency_ms": 0},
            )
            if settings.llm_enabled:
                # Not a product question is NOT the same as "needs no real answer" — a
                # caller asking "what did we agree on earlier" is general (no KB lookup
                # needed) but the model still has the actual history/carryover/session_note
                # to answer it from. A real incident: this short-circuit used to see only
                # the bare utterance, so it told a caller "I can't recall our earlier
                # conversation" when the carryover note it was never shown said exactly
                # that. Same context the KB-grounded path gets, minus company knowledge.
                triage_chat: list[dict] = memory.llm_window(history)
                if reply_language:
                    triage_chat = language.drop_other_language_replies(triage_chat, reply_language)
                if carryover:
                    triage_chat.insert(0, {
                        "role": "system",
                        "content": (
                            "Background on this returning customer, from earlier "
                            "conversations across other channels. Use it naturally if it "
                            f"answers what they just asked.\n{carryover}"
                        ),
                    })
                if session_note:
                    triage_chat.insert(0, {"role": "system", "content": session_note})
                triage_chat.append({
                    "role": "user",
                    "content": question + (
                        f"\n\n{language.reply_instruction(reply_language)}" if reply_language else ""
                    ),
                })
                generated, llm_meta = llm.complete(
                    f"You are a friendly assistant for {company_name}. The customer's message "
                    "is not a product question. If the conversation history or background "
                    "above answers it (for example recalling what was already discussed or "
                    "agreed), answer briefly from that. Otherwise, reply briefly and "
                    f"naturally, then gently invite them to ask about {company_name}'s "
                    "products. One or two short sentences.",
                    triage_chat,
                    max_tokens=80,
                    profile="voice" if channel == "voice" else "chat",
                )
                generated = reply_cleanup.strip_control_tokens((generated or "").strip())
                if generated:
                    ack, ack_meta = generated, llm_meta
            trace_step(trace, "triage_answer", "short-circuit: no retrieval, confidence 1.0",
                       llm_used=ack_meta.get("model") != "triage-template")
            return {
                "reply": ack,
                "confidence": 1.0,
                "needs_human": False,
                "handoff_reason": None,
                "sources": [],
                "model": ack_meta.get("model", "triage-template"),
                "latency_ms": ack_meta.get("latency_ms", 0),
                "script_id": getattr(script, "Id", None),
                "prompt_used": None,
            }

    system_prompt, script = script_engine.build_system_prompt(
        db, client_id, company_name, channel=channel, script=script, wants_human=wants_human
    )
    context = "\n\n---\n\n".join(h["text"] for h in hits)

    prompt_key = "escalation" if wants_human else ("voice" if channel == "voice" else "sales")
    logger.info(
        "[LeadAI answer] channel=%s prompt_key=%s script=%s(%s) kb_chunks=%d confidence=%.3f",
        channel,
        prompt_key,
        getattr(script, "Name", "none"),
        getattr(script, "Id", "none"),
        len(hits),
        confidence,
    )
    logger.debug(
        "[LeadAI answer] system_prompt (first 500 chars):\n%s",
        system_prompt[:500] if system_prompt else "(empty)",
    )
    logger.debug(
        "[LeadAI answer] kb_context (first 500 chars):\n%s",
        context[:500] if context else "(empty)",
    )

    reply, meta = None, {"model": "builtin-extractive", "latency_ms": 0}
    if settings.llm_enabled and not wants_human:
        # Was `history[-8:]` with an inline mapping. Routed through memory.
        # llm_window() so the chat path and any future channel window history
        # identically, and so `system` rows (audit lines like "Outbound call
        # placed…") are excluded rather than being fed back as assistant turns
        # for the model to imitate.
        chat: list[dict] = memory.llm_window(history)
        if reply_language:
            before = len(chat)
            chat = language.drop_other_language_replies(chat, reply_language)
            if len(chat) != before:
                trace_step(trace, "language_history",
                           f"removed {before - len(chat)} earlier reply(ies) not in {reply_language}",
                           removed=before - len(chat))

        if carryover:
            # Ahead of the thread, not merged into it: the model should treat this
            # as background it already possesses, not as something the customer
            # just said. Framed with an explicit "do not ask again" instruction
            # because the most common failure of a returning-customer flow is the
            # bot re-asking for a budget the customer gave it last week.
            chat.insert(
                0,
                {
                    "role": "system",
                    "content": (
                        "Background on this returning customer, from earlier "
                        "conversations across other channels. Use it naturally; do "
                        "NOT ask again for anything already stated here, and do NOT "
                        "read this list back to them.\n"
                        f"{carryover}"
                    ),
                },
            )

        if session_note:
            chat.insert(0, {"role": "system", "content": session_note})

        # When retrieval is weak the model tends to grab a figure from the nearest-looking
        # passage (a different product's price). Say so explicitly, so it declines rather
        # than guesses; the handoff flag alone does not stop a wrong answer being sent.
        weak_match = (
            "\n\n(The match with the company knowledge is weak, so read it carefully. If it "
            "answers the question in different words (for example 'public facilities' "
            "versus 'schools, hospitals and markets nearby'), answer from it. State a "
            "price, size or date ONLY if it is given for the exact product asked about, "
            "never one taken from a different product. If the knowledge really does not "
            "cover the topic, say a representative will join the conversation shortly to confirm "
            "it, and that they are welcome to ask anything else meanwhile. Never ask them "
            "to hold. Do not guess.)"
            if confidence < threshold
            else ""
        )
        trace_step(trace, "prompt", prompt_key,
                   script_id=getattr(script, "Id", None), script_name=getattr(script, "Name", None),
                   window_turns=len(chat), carryover_chars=len(carryover),
                   session_note_chars=len(session_note), context_chars=len(context),
                   weak_match_hint=bool(weak_match),
                   max_tokens=220 if channel == "voice" else 600)
        chat.append(
            {
                "role": "user",
                "content": (
                    f"Company knowledge (the ONLY source you may use):\n{context}\n\n"
                    f"Customer question: {question}{weak_match}"
                    + (f"\n\n{language.reply_instruction(reply_language)}" if reply_language else "")
                ),
            }
        )
        # Voice replies are capped tighter — a long answer is dead air on a call. The
        # profile matters even more than the token cap: without it this call falls back
        # to the "chat" profile's 45s timeout and one retry, so a slow OpenAI response
        # could leave a live caller in silence for up to ~90s instead of the ~15s the
        # voice profile allows before giving up and falling back.
        reply, meta = llm.complete(
            system_prompt, chat, max_tokens=220 if channel == "voice" else 600,
            profile="voice" if channel == "voice" else "chat",
        )
        trace_step(trace, "generate", "llm reply" if reply is not None else "llm call failed",
                   model=meta.get("model"), latency_ms=meta.get("latency_ms"),
                   prompt_tokens=meta.get("prompt_tokens"),
                   completion_tokens=meta.get("completion_tokens"),
                   attempts=meta.get("attempts"), error=meta.get("error"))
        if reply is not None and reply_language:
            _check_reply_language(reply, reply_language, trace)

    if reply is None:
        reason = (
            "human_requested" if wants_human
            else "llm_disabled" if not settings.llm_enabled
            else "llm_call_failed"
        )
        reply = _extractive_reply(
            company_name, scoring_q, hits, confidence, wants_human, threshold, idf, unseen
        )
        # The reply came from the knowledge base, not the model, so say so. This used to
        # keep the model's name from the failed call, mislabelling the stored message.
        meta["model"] = "builtin-extractive"
        trace_step(trace, "generate", "extractive fallback", reason=reason,
                   note="reply quoted from company knowledge only; cannot invent facts")

    # The model signals "this conversation is finished" with [END_CALL]. The script's
    # closing message promises an advisor follow-up, so that is a handoff too.
    ends_conversation = reply_cleanup.has_end_call_token(reply)
    needs_human = wants_human or confidence < threshold or ends_conversation
    trace_step(
        trace, "answer_decision", "hand off to human" if needs_human else "answer",
        wants_human=wants_human, low_confidence=confidence < threshold,
        ends_conversation=ends_conversation, confidence=confidence, threshold=threshold,
        reply_chars=len(reply or ""),
    )
    return {
        # [END_CALL] is a voice control token; it must never reach a customer.
        "reply": reply_cleanup.strip_control_tokens((reply or "").strip()),
        "confidence": confidence,
        "needs_human": needs_human,
        "ends_conversation": ends_conversation,
        "wants_human": wants_human,
        "handoff_reason": (
            "Customer asked to speak to a human"
            if wants_human
            else (f"Answer confidence {confidence} below threshold {threshold}"
                  if confidence < threshold
                  else ("AI completed the conversation; advisor follow-up"
                        if ends_conversation else None))
        ),
        "sources": [
            {
                "chunk_id": h["chunk_id"],
                "document_id": h["document_id"],
                "score": round(h["score"], 3),
                "excerpt": h["text"][:220],
            }
            for h in hits
        ],
        "model": meta.get("model"),
        "latency_ms": meta.get("latency_ms", 0),
        # The language the reply text is really in (by script), for whoever speaks it.
        "language": language.detect_language(reply_cleanup.strip_control_tokens(reply or "")) or reply_language,
        "script_id": getattr(script, "Id", None),
        # Full retrieved text, for the engine's grounding check. `sources` above only
        # carries 220-char excerpts, which would make true statements look unsupported.
        "context": [h["text"] for h in hits],
    }


def _check_reply_language(reply: str, wanted: str, trace) -> None:
    """Record when the reply is not in the language asked for. Observation only.

    A retry was tried and measured against the real model: it fixed 0 of 6 wrong-language replies,
    only adding a second model call. Filtering the history (above) is what works. The result's
    "language" field reports what the text really is, so the voice follows it.
    """
    got = language.detect_language(reply_cleanup.strip_control_tokens(reply))
    if got and not language.same_language(got, wanted):
        trace_step(trace, "language_guard",
                   f"reply is in {got} but {wanted} was asked for: the voice will follow the text")


def _extractive_reply(
    company_name: str,
    question: str,
    hits: list[dict],
    confidence: float,
    wants_human: bool,
    threshold: float,
    idf: dict,
    unseen: float,
) -> str:
    """No-LLM path: quote the company's own knowledge, or escalate.

    Safe by construction — it can only return sentences that exist in the
    company's documents, so it cannot fabricate a price or a policy.
    """
    if wants_human:
        return (
            f"Of course — a representative from {company_name} will join you shortly to "
            "help with this. If you have any other doubts in the meantime, feel free to ask."
        )

    if confidence < threshold or not hits:
        return (
            f"I don't have that in {company_name}'s knowledge base yet, so I'd rather not "
            "guess. A representative will join you shortly to confirm the details. If you "
            "have any other doubts, feel free to ask."
        )

    sentences = _best_sentences(question, hits, idf, unseen)
    if not sentences:
        sentences = [hits[0]["text"][:300]]

    body = " ".join(s if s.endswith((".", "!", "?")) else s + "." for s in sentences)
    tail = " Would you like me to walk you through the next step?" if confidence > 0.5 else ""
    return f"{body}{tail}"


# --------------------------------------------------------------------------- #
# qualification
# --------------------------------------------------------------------------- #
def _detect_product(db: Session, client_id: str, text: str) -> str | None:
    """Name the product from the knowledge base rather than a hardcoded list.

    This is what makes qualification work for any industry without configuration:
    the product taxonomy IS the company's own documents.
    """
    hits = vectorstore.search(db, client_id, text, top_k=1)
    if not hits or hits[0]["score"] < 0.12:
        return None
    words = vectorstore.keywords(text)
    for line in hits[0]["text"].split("\n"):
        line_words = vectorstore.keywords(line)
        if len(words & line_words) >= 2 and 8 < len(line) < 90:
            return line.strip(" -•:#").title()[:80]
    return None


def _budget_from(text: str) -> str | None:
    match = SALARY_WORDS.search(text) or MONEY.search(text)
    return match.group(0).strip().upper() if match else None


_ANALYSIS_PROMPT = """You analyse a sales conversation between a company's assistant and a customer, for the company's sales team. Judge ONLY from what the customer actually said or agreed to. Never invent facts. Reply with a single JSON object with exactly these keys:

"intent": one of
  "ready_to_buy"    - the customer has chosen a specific product/option and is giving details to proceed, asks to apply or sign up, or accepts an advisor call or next step;
  "evaluating"      - asking about rates, fees, eligibility, documents or process for something they are considering;
  "comparing"       - weighing several options or providers;
  "browsing"        - general questions, nothing chosen yet;
  "not_interested"  - declines, asks to stop, or says they are not interested.
"timeline": "immediate" (within about a week), "this_month", "next_quarter", or "unknown". Use "unknown" unless the customer said or clearly implied when they want to proceed.
"budget": the amount, income or loan size the customer stated, in their own words (for example "Rs 50 lakh loan"), else "unknown".
"product": the specific product or service the customer wants, as a short name (for example "Business Loan"), else "unknown".
"sentiment": "positive", "neutral" or "negative", about the company.
"summary": at most 3 short plain sentences for the sales rep who will take this over: who the customer is, what they want, what was established.
"next_step": one sentence with the single best action for the rep.
"facts": a JSON array of at most 12 short strings (each under 100 characters): durable things the CUSTOMER stated about themselves or their situation that a sales rep would need later, such as name, city, employer or business, income, family or property details, deadlines, constraints and preferences. If "Already known facts" are given, start from them: keep each unless the customer corrected it, and add new ones. Never include anything only the assistant said. Use [] if there are none."""

_VALID_INTENTS = {"ready_to_buy", "evaluating", "comparing", "browsing", "not_interested"}
_VALID_TIMELINES = {"immediate", "this_month", "next_quarter", "unknown"}
_VALID_SENTIMENTS = {"positive", "neutral", "negative"}


MAX_FACTS = 12
MAX_FACT_CHARS = 100


def clean_facts(raw) -> list[str]:
    """Normalise a model-supplied facts list: short single-line strings, de-duplicated."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, (str, int, float)):
            continue
        fact = re.sub(r"\s+", " ", str(item)).strip(" -•" + chr(9))[:MAX_FACT_CHARS]
        if fact and fact.lower() not in seen:
            seen.add(fact.lower())
            out.append(fact)
    return out[:MAX_FACTS]


def merge_facts(existing: list[str], proposed: list[str]) -> list[str]:
    """Combine the stored facts with the model's updated list, never losing memory.

    The model is asked for the complete updated list, so normally that list wins (it is
    how a correction replaces an old fact). But a failed or truncated answer must not
    erase what we already know, so a list that is less than half the size of the stored
    one is treated as suspect and merged instead of trusted.
    """
    if not proposed:
        return existing
    if len(proposed) * 2 < len(existing):
        combined = list(proposed)
        lowered = {f.lower() for f in combined}
        combined += [f for f in existing if f.lower() not in lowered]
        return combined[:MAX_FACTS]
    return proposed


def _data_points_instruction(data_points: list[LeadCompanyDataPoint]) -> str:
    """Extra JSON-schema instructions for this company's admin-defined fields.

    Appended to the fixed analysis prompt only when the company has any, so a
    company with none defined pays no extra tokens and sees no behaviour change.
    """
    if not data_points:
        return ""
    type_hints = {
        "text": "free text",
        "number": "a number only, no currency symbol or units",
        "boolean": "true or false",
        "date": "a date, as YYYY-MM-DD",
        "email": "an email address",
    }
    lines = []
    has_date = False
    for dp in data_points:
        if dp.DataType == "select":
            hint = f"exactly one of: {', '.join(dp.OptionsJson or [])}"
        else:
            hint = type_hints.get(dp.DataType, "free text")
        if dp.DataType == "date":
            has_date = True
        extra = f" ({dp.Description})" if dp.Description else ""
        lines.append(f'  "{dp.Key}" ["{dp.Label}"{extra}]: {hint}')
    # A model has no inherent sense of "now" — without this, "today"/"tomorrow"/
    # "next Monday" get resolved against whatever date is common in its training
    # data instead of the real one. Seen in production: a customer said "I'll
    # visit the site today" and the stored date came back as 2023.
    #
    # Also seen in production: a customer said just "Sunday" (no date), and it
    # came back as a Thursday. Giving only the ISO date ("today is 2026-10-07")
    # forces the model to work out what WEEKDAY that is before it can count
    # forward to "next Sunday" — exactly the kind of calendar arithmetic a
    # model gets wrong. Naming the weekday removes that step entirely.
    today_note = ""
    if has_date:
        today_note = (
            f"\nToday's actual date is {_today_in(settings.default_timezone)}. Resolve "
            '"today", "tomorrow", "next Monday" etc. against THIS date, never a guess. '
            "When the customer names a day of the week with no date (e.g. just \"Sunday\"), "
            "resolve it to the NEXT upcoming occurrence of that day — never one that has "
            "already passed, and never today itself unless the customer actually said "
            '"today".\n'
        )
    return (
        today_note
        + '\n"data_points": a JSON object with exactly these keys, each set from what the '
        'customer stated. Use null for any not yet known — never invent one:\n'
        + "\n".join(lines)
    )


def _today_in(tz_name: str) -> str:
    """ISO date AND its weekday name (e.g. "2026-10-07 (Wednesday)") — the
    weekday matters as much as the date itself here: resolving a bare day
    name like "Sunday" into a real date means counting forward from whatever
    weekday today is, and a model left to work that out from the ISO date
    alone gets it wrong (see _data_points_instruction's docstring)."""
    from datetime import datetime, timedelta, timezone as _tz

    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo(tz_name))
    except Exception:  # noqa: BLE001 — no tzdata: approximate IST rather than fail the turn
        now = datetime.now(_tz.utc) + timedelta(hours=5, minutes=30)
    return now.strftime("%Y-%m-%d (%A)")


def _validate_data_point_value(value, dp: LeadCompanyDataPoint):
    """None means "not extracted this turn, leave whatever is already stored"."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if dp.DataType == "number":
        try:
            return float(value) if not float(value).is_integer() else int(float(value))
        except (TypeError, ValueError):
            return None
    if dp.DataType == "boolean":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "yes", "y")
    if dp.DataType == "select":
        options = dp.OptionsJson or []
        for opt in options:
            if str(value).strip().lower() == opt.lower():
                return opt
        return None
    if dp.DataType == "email":
        text_value = str(value).strip()[:160]
        return text_value if "@" in text_value and "." in text_value.split("@")[-1] else None
    # text / date: trust the model's formatting, just bound the length.
    return str(value).strip()[:160]


def _product_catalog_instruction(catalog_names: list[str]) -> str:
    """When a company has defined its own product catalog, "product" must be
    one of THOSE names, never a free guess — a company with no catalog keeps
    today's freeform behaviour exactly (this returns "" for an empty list).
    """
    if not catalog_names:
        return ""
    names = ", ".join(f'"{n}"' for n in catalog_names)
    return (
        f'\nFor "product": this company sells a specific, named set of products. '
        f'Choose EXACTLY one of: {names} — whichever the customer most wants. Use '
        f'"unknown" if none of these fit, even if the customer mentioned something '
        f"else. Never answer with a name outside this list."
    )


def _snap_to_catalog(candidate: str, catalog_names: list[str]) -> str:
    """Case-insensitive exact match -> the catalog's own casing; no match and no
    catalog defined -> the candidate is trusted as-is (today's behaviour); no
    match but a catalog IS defined -> "unknown", never an invented name.
    """
    if not catalog_names or candidate == "unknown":
        return candidate
    for name in catalog_names:
        if candidate.strip().lower() == name.strip().lower():
            return name
    return "unknown"


def _llm_analysis(
    messages: list[LeadMessage],
    known_facts: list[str] | None = None,
    data_points: list[LeadCompanyDataPoint] | None = None,
    product_catalog: list[str] | None = None,
) -> dict | None:
    """Ask the LLM to read the conversation. Returns a validated dict, or None.

    None (LLM off, call failed, unusable output) means "use the keyword rules"; this
    function never raises, because scoring runs inside a live customer turn.
    """
    if not (settings.llm_enabled and settings.llm_qualification):
        return None
    turns = [
        m for m in messages
        if (m.Sender or "") in ("customer", "ai", "agent") and (m.Content or "").strip()
    ]
    if not any(m.Sender == "customer" for m in turns):
        return None
    transcript = "\n".join(
        f"{'Customer' if m.Sender == 'customer' else 'Assistant'}: "
        f"{reply_cleanup.strip_control_tokens(m.Content)}"
        for m in turns[-30:]
    )
    if known_facts:
        transcript += "\n\nAlready known facts: " + json.dumps(known_facts, ensure_ascii=False)
    prompt = (
        _ANALYSIS_PROMPT
        + _data_points_instruction(data_points or [])
        + _product_catalog_instruction(product_catalog or [])
    )
    try:
        data, _ = llm.complete_json(prompt, [{"role": "user", "content": transcript}])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LeadAI qualify] LLM analysis failed (%s) — using keyword rules", exc)
        return None
    if not isinstance(data, dict):
        return None

    def text(key: str, limit: int) -> str:
        value = data.get(key)
        return str(value).strip()[:limit] if isinstance(value, (str, int, float)) else ""

    def pick(key: str, allowed: set[str]) -> str:
        value = text(key, 40).lower().replace(" ", "_")
        return value if value in allowed else ""

    def fact(key: str) -> str:
        value = text(key, 120)
        return value if value and value.lower() not in ("unknown", "none", "n/a", "null") else "unknown"

    raw_values = data.get("data_points") if isinstance(data.get("data_points"), dict) else {}
    data_point_values = {
        dp.Key: validated
        for dp in (data_points or [])
        if (validated := _validate_data_point_value(raw_values.get(dp.Key), dp)) is not None
    }

    return {
        "intent": pick("intent", _VALID_INTENTS),
        "timeline": pick("timeline", _VALID_TIMELINES) or "unknown",
        "budget": fact("budget").upper(),
        "product": _snap_to_catalog(fact("product"), product_catalog or []),
        "sentiment": pick("sentiment", _VALID_SENTIMENTS),
        "summary": text("summary", 2000),
        "next_step": text("next_step", 500),
        "facts": clean_facts(data.get("facts")),
        "data_point_values": data_point_values,
    }


def qualify(
    db: Session,
    client_id: str,
    lead: Lead,
    messages: list[LeadMessage],
    trace: TurnTrace | None = None,
) -> Lead:
    """Recompute the lead's qualification state in place. Caller commits.

    Scoring is additive and fully explainable — the breakdown is stored on the
    row so the dashboard can show *why* a lead is hot, which is what makes a
    sales team trust the number.
    """
    before = {
        "intent": lead.Intent, "timeline": lead.Timeline, "sentiment": lead.Sentiment,
        "status": lead.Status, "score": lead.Score,
        "budget_known": bool(lead.Budget and lead.Budget != "unknown"),
        "product_known": bool(lead.Product and lead.Product != "unknown"),
    }
    customer_text = " ".join(
        m.Content for m in messages if m.Sender == "customer" and m.Content
    ).lower()

    # Keyword rules first: free, instant, and the fallback when the LLM is off or fails.
    # INTENT_SIGNALS is ordered strongest first, so the strongest signal present wins
    # (previously a weaker label later in the dict, e.g. "browsing", could overwrite it).
    intent = lead.Intent or "browsing"
    for label, words in INTENT_SIGNALS.items():
        if any(w in customer_text for w in words):
            intent = label
            break

    timeline = lead.Timeline or "unknown"
    for label, words in TIMELINE_SIGNALS.items():
        if any(w in customer_text for w in words):
            timeline = label
            break

    budget = _budget_from(customer_text) or (
        lead.Budget if lead.Budget and lead.Budget != "unknown" else "unknown"
    )

    product_catalog = [
        p.ProductName
        for p in db.query(LeadProduct)
        .filter(LeadProduct.ClientId == client_id, LeadProduct.IsDeleted == False)  # noqa: E712
        .all()
    ]

    product = lead.Product or "unknown"
    detected = _detect_product(db, client_id, customer_text[-600:]) if customer_text else None
    if detected:
        # A company with a defined catalog gets ONLY exact matches from the free-text
        # KB-line heuristic below — a near-miss snaps to "unknown" rather than
        # polluting Lead.Product with a name outside the company's own catalog.
        snapped = _snap_to_catalog(detected, product_catalog)
        if snapped != "unknown":
            product = snapped

    pos = sum(customer_text.count(w) for w in POSITIVE)
    neg = sum(customer_text.count(w) for w in NEGATIVE)
    sentiment = "positive" if pos > neg else ("negative" if neg > pos else "neutral")

    # Then let the LLM read the whole conversation. Keywords cannot tell that "I will
    # take the business loan" plus an amount and company details means the customer is
    # ready to proceed. Anything the model is unsure of stays as the rules found it.
    known_facts = clean_facts(lead.FactsJson)
    data_points = (
        db.query(LeadCompanyDataPoint)
        .filter(
            LeadCompanyDataPoint.ClientId == client_id,
            LeadCompanyDataPoint.IsActive == True,  # noqa: E712
            LeadCompanyDataPoint.IsDeleted == False,  # noqa: E712
        )
        .all()
    )
    analysis = _llm_analysis(messages, known_facts, data_points, product_catalog)
    if analysis:
        lead.FactsJson = merge_facts(known_facts, analysis["facts"]) or None
        if analysis["data_point_values"]:
            # Extracted values only ever ADD to or correct what's already known — a
            # turn where the customer didn't repeat something already answered must
            # not blank it out.
            merged = dict(lead.DataPointsJson or {})
            merged.update(analysis["data_point_values"])
            lead.DataPointsJson = merged
        intent = analysis["intent"] or intent
        if analysis["timeline"] != "unknown":
            timeline = analysis["timeline"]
        if analysis["budget"] != "unknown":
            budget = analysis["budget"]
        if analysis["product"] != "unknown":
            product = analysis["product"]
        sentiment = analysis["sentiment"] or sentiment
        # summarize() reuses this so one LLM call serves both.
        lead._ai_brief = (analysis["summary"], analysis["next_step"])

    turns = sum(1 for m in messages if m.Sender == "customer")

    breakdown = {
        "base": 8,
        "engagement": min(turns * 6, 24),
        "intent": {"ready_to_buy": 32, "comparing": 20, "evaluating": 16,
                   "browsing": 4, "not_interested": 0}.get(intent, 0),
        "timeline": {"immediate": 22, "this_month": 15, "next_quarter": 7}.get(timeline, 0),
        "budget_known": 12 if budget != "unknown" else 0,
        "product_known": 8 if product != "unknown" else 0,
        "sentiment": {"positive": 6, "neutral": 0, "negative": -8}[sentiment],
    }
    score = max(0, min(100, sum(breakdown.values())))

    known = sum(1 for x in (budget, timeline, product) if x != "unknown")
    # "Qualified" must mean the AI actually ESTABLISHED the facts — not that one
    # enthusiastic message scored well. Requiring all three facts plus real
    # back-and-forth is what stops the sales team chasing noise.
    if score >= 78 and known >= 3 and turns >= 3 and intent == "ready_to_buy":
        status = "qualified"
    elif score >= 62:
        status = "hot"
    elif score >= 36:
        status = "warm"
    else:
        status = "cold"

    interest = (
        product
        if product != "unknown"
        else ("General enquiry" if intent == "browsing" else intent.replace("_", " ").title())
    )

    was_qualified = lead.Status == "qualified"

    lead.Intent = intent
    lead.Timeline = timeline
    lead.Budget = budget
    lead.Product = product[:200]
    lead.Sentiment = sentiment
    lead.Interest = interest[:160]
    lead.Score = score
    lead.Status = status
    lead.ScoreBreakdown = breakdown
    if status == "qualified" and not was_qualified:
        from ..models import utcnow

        lead.QualifiedAt = utcnow()

    if analysis:
        source = "llm"
    elif not (settings.llm_enabled and settings.llm_qualification):
        source = "keyword rules (llm qualification off)"
    else:
        source = "keyword rules (llm failed or unusable)"
    after = {
        "intent": intent, "timeline": timeline, "sentiment": sentiment, "status": status,
        "score": score, "budget_known": budget != "unknown", "product_known": product != "unknown",
    }
    trace_step(
        trace, "qualify", f"{status} score={score}",
        analysis=source, customer_turns=turns, breakdown=breakdown,
        # Only labels and yes/no: amounts and names are customer data, not log material.
        after={k: v for k, v in after.items() if k != "score"},
        changed={k: [before[k], after[k]] for k in after if before.get(k) != after[k]},
        facts_stored=len(lead.FactsJson or []), signals_known=known,
        data_points_defined=len(data_points), data_points_collected=len(lead.DataPointsJson or {}),
    )
    return lead


# --------------------------------------------------------------------------- #
# summarisation
# --------------------------------------------------------------------------- #
NEXT_STEP = {
    "qualified": "Call now and close — the customer is ready to proceed.",
    "hot": "Call today with a tailored offer while interest is high.",
    "warm": "Send a comparison of the options discussed, then follow up tomorrow.",
    "cold": "Nurture with an intro email; no call needed yet.",
    "lost": "Mark closed and add to the re-engagement list.",
}


def summarize(
    db: Session,
    client_id: str,
    company_name: str,
    lead: Lead,
    messages: list[LeadMessage],
    trace: TurnTrace | None = None,
) -> tuple[str, str]:
    """Return (summary, recommended_next_step) for the agent handoff card."""
    # qualify() already had the LLM write these in the same call; reuse them.
    brief = getattr(lead, "_ai_brief", None)
    if brief and brief[0]:
        lead._ai_brief = None
        trace_step(trace, "summarize", "reused the qualification analysis (no extra llm call)")
        return brief[0][:2000], (brief[1] or NEXT_STEP.get(lead.Status, ""))[:500]

    if settings.llm_enabled and len(messages) >= 2:
        transcript = "\n".join(
            f"{m.Sender}: {m.Content}" for m in messages[-20:] if m.Content
        )
        raw, _ = llm.complete(
            "Summarise this sales conversation in at most 3 short sentences for a "
            "sales rep who is about to take it over. Then a final line starting with "
            "'Next step:' recommending the single best action. Plain language, no "
            "bullet points, no preamble.",
            [{"role": "user", "content": transcript}],
            max_tokens=250,
        )
        if raw:
            parts = raw.split("Next step:")
            summary = parts[0].strip()
            step = parts[1].strip() if len(parts) > 1 else NEXT_STEP.get(lead.Status, "")
            trace_step(trace, "summarize", "llm summary", window_msgs=min(len(messages), 20))
            return summary[:2000], step[:500]

    # Deterministic brief: assembled from extracted facts, so it is always
    # accurate even when it is terse.
    questions = [m.Content.strip() for m in messages if m.Sender == "customer" and m.Content][-4:]
    lines: list[str] = []
    if lead.Product and lead.Product != "unknown":
        lines.append(f"Customer is asking about {lead.Product}.")
    elif questions:
        lines.append(f"Customer opened with: {questions[0][:120]}")
    if lead.Budget and lead.Budget != "unknown":
        lines.append(f"Budget/income signal: {lead.Budget}.")
    if lead.Timeline and lead.Timeline != "unknown":
        lines.append(f"Timeline: {lead.Timeline.replace('_', ' ')}.")
    if len(questions) > 1:
        lines.append("Also asked: " + "; ".join(q[:70] for q in questions[1:]) + ".")
    lines.append(
        f"Sentiment is {lead.Sentiment}, lead scored {lead.Score}/100 ({lead.Status})."
    )
    trace_step(trace, "summarize", "deterministic summary from extracted facts (llm unavailable)")
    return " ".join(lines)[:2000], NEXT_STEP.get(lead.Status, "")[:500]


def agent_suggestions(lead: Lead | None, conversation: LeadConversation) -> list[str]:
    """Coaching tips shown beside a conversation an agent has just picked up."""
    if not lead:
        return []
    tips = [conversation.NextStep] if conversation.NextStep else []
    if lead.Budget == "unknown":
        tips.append("Ask about budget or monthly income to firm up eligibility.")
    if lead.Timeline == "unknown":
        tips.append("Ask when they want to move forward.")
    if lead.Sentiment == "negative":
        tips.append("Price sensitivity detected: open with the fee waiver or entry-level option.")
    if lead.Intent == "ready_to_buy":
        tips.append("Send the application link before ending the conversation.")
    return [t for t in tips if t][:4]
