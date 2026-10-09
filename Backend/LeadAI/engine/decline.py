"""Did the assistant's reply say, in words, that it cannot answer?

Found by the golden-set evaluation: asked about car-loan rates the company has no data
for, the model correctly replied "I don't have information on car loan interest rates.
Would you like me to connect you with a human specialist?" — but the conversation was
never flagged for a human, because the handoff decision rests on retrieval confidence
and retrieval had matched the (unrelated) home-loan text at 0.68. The customer was
told help was coming and nobody was ever notified.

This detector closes that gap by reading the reply itself. It is deliberately narrow:
a false positive hands a perfectly good answer to a human, so it only matches phrases
that clearly say "I cannot answer this". "I don't have your budget yet, could you
share it?" is a question to the customer and must NOT match.
"""
from __future__ import annotations

import re

_DECLINE = re.compile(
    "|".join(
        [
            # "I don't have that / this / any / specific / enough ..."; deliberately
            # not "the" or "your" — "I don't have your budget" is a question.
            r"\bI\s*(?:do not|don't|dont|do n't)\s+have\s+(?:that|this|any|specific|enough)\b",
            r"\bI\s*(?:do not|don't|dont)\s+have\s+(?:more\s+|any\s+|that\s+)?(?:information|details|data)\b",
            r"\b(?:I'm|I am)\s+(?:not able|unable)\s+to\s+(?:confirm|provide|find|answer)",
            r"\bnot\s+(?:in|part of)\s+(?:our|the|my)\s+(?:knowledge|information|records)\b",
            r"\brather not guess\b",
            r"\b(?:I'm|I am)\s+not\s+(?:sure|certain)\b",
        ]
    ),
    re.IGNORECASE,
)


def is_decline(reply: str | None) -> bool:
    return bool(_DECLINE.search(reply or ""))


def recent_decline_count(history: list, lookback: int = 8) -> int:
    """How many of the AI's own last `lookback` messages already declined in words.

    Used to give a misheard word or a real gap in the knowledge base a couple of tries
    before forcing a handoff (see engine/graph.py's `_make_decide`) — the model's own
    reply on a first decline already invites another question; only a repeated decline
    means the retry genuinely isn't helping.
    """
    ai_messages = [m for m in (history or []) if (getattr(m, "Sender", None) or "") == "ai"]
    return sum(1 for m in ai_messages[-lookback:] if is_decline(getattr(m, "Content", None)))
