"""
Cross-channel conversation memory.

WHY THIS MODULE EXISTS
----------------------
Before this file, memory in the platform was correct but *siloed*:

  * Web/social chat  -> `handle_customer_turn` loaded every LeadMessage for the
    CURRENT conversation and handed the last 8 to the LLM. Good within a thread.
  * Voice            -> `SimpleAgent.history` kept the turns of the CURRENT call
    in process memory, compressing older turns past 40. Good within a call.
  * Between them     -> nothing. An outbound call placed on a lead who had just
    spent ten minutes in the chat widget started from a blank slate, and the
    customer had to repeat everything. That is the single most visible defect in
    a demo.

There were three concrete gaps:

  1. `call_bridge.prepare_agent_context()` built the agent prompt from the
     company script only. It never read `leadai_messages`.
  2. Retrieval in `ai_engine.answer()` embedded the RAW customer utterance. A
     follow-up like "and what about the interest rate?" carries no nouns, so
     cosine similarity against the knowledge base scores near zero, confidence
     collapses, and the bot escalates to a human on a question it could answer.
  3. A returning customer on a DIFFERENT channel (chatted on the website in
     March, WhatsApps in April) opened a fresh conversation with no carry-over.

This module supplies the three missing pieces, and only those. It reads; it
never writes. Nothing here can corrupt a conversation.

    thread_history()     -> messages for one conversation (the existing behaviour,
                            centralised so every caller windows it identically)
    customer_memory()    -> a compact digest of everything known about this
                            customer across every channel and conversation
    retrieval_query()    -> a self-contained search string built from the current
                            utterance plus recent context, for the vector store
    voice_briefing()     -> the digest rendered as an XML-parser section, ready to
                            splice into the agent's `xml_sections`

DESIGN NOTES
------------
* Budgets are characters, not tokens, deliberately. Token counting needs the
  tokeniser for whichever model is configured (Sarvam on voice, OpenAI on chat),
  and being 15% off on a 1,500-character budget costs nothing. Being wrong about
  which tokeniser to load costs an import error at call time.
* The digest is capped hard. A voice system prompt that grows without bound is
  how a call agent starts taking four seconds to produce its first syllable.
* Everything degrades to empty string. No branch here may raise into a live call.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import Lead, LeadConversation, LeadCustomer, LeadMessage

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# tunables
# --------------------------------------------------------------------------- #
LLM_WINDOW_TURNS = 12          # raw turns handed to the chat LLM (was 8)
CARRYOVER_DAYS = 120           # how far back a prior conversation still counts
CARRYOVER_MAX_CONVERSATIONS = 4
DIGEST_MAX_CHARS = 1400        # hard cap on the cross-channel digest
VOICE_RECENT_TURNS = 8         # verbatim turns quoted into the voice briefing
VOICE_TURN_MAX_CHARS = 180     # per-turn truncation inside the briefing

_CHANNEL_LABEL = {
    "web": "website chat",
    "whatsapp": "WhatsApp",
    "messenger": "Facebook Messenger",
    "instagram": "Instagram DM",
    "voice": "phone call",
}

# Words that carry no retrieval signal. Used only to decide whether an utterance
# is self-contained enough to embed on its own.
_STOP = frozenset(
    """
    a an and are as at be but by can could do does did for from had has have how
    i if in is it its me my no not of on or our so than that the their them then
    there these they this to too us was we were what when where which who why
    will with would you your yours ok okay yes yeah sure please thanks thank
    """.split()
)

# A follow-up is a short utterance that leans on something already said.
_FOLLOWUP_HINT = re.compile(
    r"^\s*(and|also|what about|how about|ok(ay)?|but|then|so)\b"
    r"|\b(that one|this one|it|they|those|the same|same thing|as well)\b",
    re.IGNORECASE,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _label(channel: str | None) -> str:
    return _CHANNEL_LABEL.get((channel or "").lower(), channel or "chat")


def _clip(text: str | None, limit: int) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


# =========================================================================== #
# 1. within-thread history
# =========================================================================== #
def thread_history(db: Session, conversation_id: str) -> list[LeadMessage]:
    """Every non-deleted message in one conversation, oldest first.

    Identical to the query that was previously inlined in
    `conversation_flow.handle_customer_turn`. Centralised so the voice path and
    the chat path can never window history differently.
    """
    return (
        db.query(LeadMessage)
        .filter(
            LeadMessage.ConversationId == conversation_id,
            LeadMessage.IsDeleted == False,  # noqa: E712
        )
        .order_by(LeadMessage.CreatedAt.asc())
        .all()
    )


def llm_window(history: list[LeadMessage], turns: int = LLM_WINDOW_TURNS) -> list[dict]:
    """Map LeadMessage rows onto OpenAI chat-format dicts.

    `system` messages ("Outbound call placed…") are dropped: they are audit
    trail for staff, not conversation the model should imitate.
    """
    usable = [m for m in history if (m.Sender or "") in ("customer", "ai", "agent")]
    return [
        {
            "role": "user" if m.Sender == "customer" else "assistant",
            "content": m.Content or "",
        }
        for m in usable[-turns:]
        if (m.Content or "").strip()
    ]


# =========================================================================== #
# 2. cross-channel customer memory
# =========================================================================== #
def _prior_conversations(
    db: Session, customer_id: str | None, exclude_conversation_id: str | None
) -> list[LeadConversation]:
    if not customer_id:
        return []
    cutoff = _utcnow() - timedelta(days=CARRYOVER_DAYS)
    q = (
        db.query(LeadConversation)
        .filter(
            LeadConversation.CustomerId == customer_id,
            LeadConversation.IsDeleted == False,  # noqa: E712
        )
        .filter(LeadConversation.LastMessageAt >= cutoff)
    )
    if exclude_conversation_id:
        q = q.filter(LeadConversation.Id != exclude_conversation_id)
    return (
        q.order_by(LeadConversation.LastMessageAt.desc())
        .limit(CARRYOVER_MAX_CONVERSATIONS)
        .all()
    )


def customer_memory(
    db: Session,
    conversation: LeadConversation,
    *,
    include_current: bool = False,
) -> str:
    """A compact prose digest of what is already known about this customer.

    Assembled from three sources, in descending order of usefulness:

      1. The lead record  — qualification fields the AI already extracted
         (interest, budget, timeline, product, score). These are the highest
         signal-per-character text in the system.
      2. Prior conversation summaries — one line per earlier thread, tagged with
         the channel it happened on, so the agent can say "when we spoke on
         WhatsApp last week" rather than guessing.
      3. The current thread's summary, if the caller asks for it.

    Returns "" when there is nothing worth carrying. Callers must treat empty as
    "no memory available" and behave exactly as they did before this module.
    """
    try:
        parts: list[str] = []

        lead = (
            db.query(Lead)
            .filter(Lead.ConversationId == conversation.Id, Lead.IsDeleted == False)  # noqa: E712
            .one_or_none()
        )
        if lead is not None:
            facts = [
                ("Interest", getattr(lead, "Interest", None)),
                ("Product discussed", getattr(lead, "Product", None)),
                ("Budget", getattr(lead, "Budget", None)),
                ("Timeline", getattr(lead, "Timeline", None)),
                ("Intent", getattr(lead, "Intent", None)),
            ]
            known = [f"{k}: {_clip(str(v), 120)}" for k, v in facts if v]
            if known:
                parts.append("Known from earlier: " + "; ".join(known) + ".")
            if getattr(lead, "Score", None):
                parts.append(f"Current lead score: {lead.Score}/100 ({lead.Status}).")

        customer = db.get(LeadCustomer, conversation.CustomerId) if conversation.CustomerId else None
        name = getattr(customer, "Name", None)
        if name:
            parts.append(f"Customer name: {_clip(name, 60)}.")

        for prior in _prior_conversations(db, conversation.CustomerId, conversation.Id):
            summary = _clip(prior.Summary, 220)
            if not summary:
                continue
            when = prior.LastMessageAt or prior.CreatedAt
            stamp = when.strftime("%d %b") if when else "earlier"
            parts.append(f"Earlier on {_label(prior.Channel)} ({stamp}): {summary}")

        if include_current and conversation.Summary:
            parts.append(f"This conversation so far: {_clip(conversation.Summary, 260)}")
            if conversation.NextStep:
                parts.append(f"Agreed next step: {_clip(conversation.NextStep, 160)}")

        digest = " ".join(p for p in parts if p).strip()
        return _clip(digest, DIGEST_MAX_CHARS)
    except Exception as exc:  # noqa: BLE001
        # Memory is an enhancement. If it fails, the turn still has to happen.
        logger.warning("[LeadAI memory] digest failed for conv %s: %s", conversation.Id, exc)
        return ""


# =========================================================================== #
# 3. history-aware retrieval query
# =========================================================================== #
def _content_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z\u0900-\u097F]{3,}", text or "") if w.lower() not in _STOP]


def retrieval_query(question: str, history: list[LeadMessage] | None = None) -> str:
    """Build a self-contained string to embed against the knowledge base.

    THE PROBLEM THIS SOLVES
    Retrieval previously embedded the customer's raw utterance. Consider:

        customer: "Tell me about the salaried personal loan"
        ai:       "...12.99% onwards, up to ₹25 lakh..."
        customer: "and what about processing fees for that?"

    The third message contains no product noun. Embedded alone it retrieves
    almost nothing, `confidence` lands near 0.1, and the handoff rule fires — the
    bot escalates a question that is squarely inside the knowledge base. Worse,
    it does this on the SECOND question of every conversation, which is exactly
    when a demo audience is watching.

    THE FIX
    When the utterance looks like a follow-up (short, or opening with a
    connective, or leaning on a pronoun), prepend the content words from the last
    couple of customer turns. The result is still a natural-language string, so
    it embeds well and the lexical-coverage scorer in `vectorstore` still works
    unchanged. When the utterance is already self-contained, it is returned
    untouched — no behaviour change on the common path.
    """
    question = (question or "").strip()
    if not question:
        return question

    own_words = _content_words(question)
    self_contained = len(own_words) >= 3 and not _FOLLOWUP_HINT.search(question)
    if self_contained or not history:
        return question

    prior_customer = [
        m.Content for m in history if (m.Sender == "customer") and (m.Content or "").strip()
    ]
    # Drop the current utterance if the caller already appended it to history.
    prior_customer = [c for c in prior_customer if c.strip() != question][-2:]
    if not prior_customer:
        return question

    context_words: list[str] = []
    for utterance in prior_customer:
        for word in _content_words(utterance):
            if word.lower() not in {w.lower() for w in context_words}:
                context_words.append(word)

    if not context_words:
        return question

    expanded = f"{' '.join(context_words[:12])} {question}"
    logger.debug("[LeadAI memory] retrieval query expanded: %r -> %r", question, expanded)
    return expanded


# =========================================================================== #
# 4. voice briefing
# =========================================================================== #
def voice_briefing(db: Session, conversation: LeadConversation) -> list[dict]:
    """Render this customer's history as XML-parser sections for a voice call.

    Returns a list in the same shape `xml_parser.parse_xml_to_sections` produces,
    so it can be concatenated onto a company script's sections and rendered by
    `sections_to_prompt` with no special-casing anywhere.

    Two sections are produced:

      * "Caller Context" (type=text)   — the cross-channel digest.
      * "Recent Messages" (type=data)  — the last few turns verbatim, so the
        agent can reference a specific thing the customer said rather than
        paraphrasing a summary.

    Returns [] when there is no history, in which case the call behaves exactly
    as it did before this module existed.
    """
    try:
        history = thread_history(db, conversation.Id)
        digest = customer_memory(db, conversation, include_current=True)

        recent = [
            m
            for m in history
            if (m.Sender or "") in ("customer", "ai", "agent") and (m.Content or "").strip()
        ][-VOICE_RECENT_TURNS:]

        if not digest and not recent:
            return []

        sections: list[dict] = []

        context_text = (
            "You have spoken with this person before. Do NOT introduce yourself as if this "
            "is a first contact, and do NOT ask for information they have already given. "
            "Open by referring naturally to the earlier conversation, then continue from "
            "there. " + (digest or "")
        ).strip()

        sections.append(
            {
                "id": "caller_context",
                "title": "Caller Context",
                "type": "text",
                "content": _clip(context_text, DIGEST_MAX_CHARS + 300),
            }
        )

        if recent:
            fields = []
            for i, m in enumerate(recent, start=1):
                who = "Customer" if m.Sender == "customer" else "You (earlier)"
                fields.append(
                    {
                        "name": f"turn_{i}",
                        "label": f"{who} — {_label(m.Channel if hasattr(m, 'Channel') else conversation.Channel)}",
                        "value": _clip(m.Content, VOICE_TURN_MAX_CHARS),
                    }
                )
            sections.append(
                {
                    "id": "recent_messages",
                    "title": "Recent Messages From This Customer",
                    "type": "data",
                    "content": fields,
                }
            )

        logger.info(
            "[LeadAI memory] voice briefing built for conv %s — digest=%d chars, turns=%d",
            conversation.Id,
            len(digest),
            len(recent),
        )
        return sections
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LeadAI memory] voice briefing failed for %s: %s", conversation.Id, exc)
        return []
