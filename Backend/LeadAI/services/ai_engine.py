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

import logging
import re

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Lead, LeadCompanySettings, LeadConversation, LeadMessage
from . import llm, memory, script_engine, vectorstore

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
                     "how do i start", "i'll take it"],
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
        out.extend(s.strip(" -•\t") for s in re.split(r"(?<=[.!?])\s+", line.strip()))
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
) -> dict:
    """Answer strictly from this company's knowledge base.

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

    # A greeting is not a knowledge question. Running "hi" through retrieval
    # scores ~0 and would escalate a customer who has not asked anything yet.
    if _is_greeting(question) and len(history) <= 2:
        greeting = script_engine.get_prompt(db, client_id, company_name, "greeting")
        logger.info(
            "[LeadAI answer] greeting detected — channel=%s prompt_key=greeting",
            channel,
        )
        return {
            "reply": (
                f"Hi! I'm the {company_name} assistant. Ask me anything about our "
                "products and I'll answer from our official information."
            ),
            "confidence": 1.0,
            "needs_human": False,
            "handoff_reason": None,
            "sources": [],
            "model": "greeting-template",
            "latency_ms": 0,
            "script_id": getattr(script, "Id", None),
            "prompt_used": greeting,
        }

    wants_human = bool(HUMAN_REQUEST.search(question or ""))

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
    search_query = memory.retrieval_query(question, history)

    hits = vectorstore.search(db, client_id, search_query, top_k=top_k)
    idf, unseen = vectorstore.idf_map(db, client_id)
    top_score = hits[0]["score"] if hits else 0.0

    # Sentence-level coverage is a sharper signal than chunk-level: a 900-char
    # chunk can dilute an exact answer that sits in a single line.
    sentence_scores = _scored_sentences(question, hits, idf, unseen)
    coverage = max(
        (vectorstore.lexical_coverage(question, s, idf, unseen) for _, s in sentence_scores[:6]),
        default=0.0,
    )
    # Blend: chunk-level retrieval strength (45%) + best-sentence coverage (55%).
    confidence = round(min(1.0, 0.45 * min(top_score / 0.6, 1.0) + 0.55 * coverage), 3)

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

        chat.append(
            {
                "role": "user",
                "content": (
                    f"Company knowledge (the ONLY source you may use):\n{context}\n\n"
                    f"Customer question: {question}"
                ),
            }
        )
        # Voice replies are capped tighter — a long answer is dead air on a call.
        reply, meta = llm.complete(
            system_prompt, chat, max_tokens=220 if channel == "voice" else 600
        )

    if reply is None:
        reply = _extractive_reply(
            company_name, question, hits, confidence, wants_human, threshold, idf, unseen
        )
        meta.setdefault("model", "builtin-extractive")

    needs_human = wants_human or confidence < threshold
    return {
        "reply": (reply or "").strip(),
        "confidence": confidence,
        "needs_human": needs_human,
        "handoff_reason": (
            "Customer asked to speak to a human"
            if wants_human
            else (f"Answer confidence {confidence} below threshold {threshold}"
                  if needs_human else None)
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
        "script_id": getattr(script, "Id", None),
    }


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
            f"Of course — I'm connecting you with a specialist from {company_name}. "
            "They'll pick up this same conversation, so you won't need to repeat anything."
        )

    if confidence < threshold or not hits:
        return (
            f"I don't have that in {company_name}'s knowledge base yet, so I'd rather not "
            "guess. I'm passing this to a specialist who can confirm the details for you. "
            "Meanwhile, is there anything else I can check?"
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


def qualify(
    db: Session,
    client_id: str,
    lead: Lead,
    messages: list[LeadMessage],
) -> Lead:
    """Recompute the lead's qualification state in place. Caller commits.

    Scoring is additive and fully explainable — the breakdown is stored on the
    row so the dashboard can show *why* a lead is hot, which is what makes a
    sales team trust the number.
    """
    customer_text = " ".join(
        m.Content for m in messages if m.Sender == "customer" and m.Content
    ).lower()

    intent = lead.Intent or "browsing"
    for label, words in INTENT_SIGNALS.items():
        if any(w in customer_text for w in words):
            intent = label
            if label == "ready_to_buy":
                break  # strongest signal wins outright

    timeline = lead.Timeline or "unknown"
    for label, words in TIMELINE_SIGNALS.items():
        if any(w in customer_text for w in words):
            timeline = label
            break

    budget = _budget_from(customer_text) or (
        lead.Budget if lead.Budget and lead.Budget != "unknown" else "unknown"
    )

    product = lead.Product or "unknown"
    detected = _detect_product(db, client_id, customer_text[-600:]) if customer_text else None
    if detected:
        product = detected

    pos = sum(customer_text.count(w) for w in POSITIVE)
    neg = sum(customer_text.count(w) for w in NEGATIVE)
    sentiment = "positive" if pos > neg else ("negative" if neg > pos else "neutral")

    turns = sum(1 for m in messages if m.Sender == "customer")

    breakdown = {
        "base": 8,
        "engagement": min(turns * 6, 24),
        "intent": {"ready_to_buy": 32, "comparing": 20, "evaluating": 16,
                   "browsing": 4}.get(intent, 0),
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
) -> tuple[str, str]:
    """Return (summary, recommended_next_step) for the agent handoff card."""
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
