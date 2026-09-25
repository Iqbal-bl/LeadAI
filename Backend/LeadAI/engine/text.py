"""Small text helpers shared by the engine and the answering code."""
from __future__ import annotations

import re

# A full stop after these is part of the word, not the end of a sentence. Splitting
# "fee of Rs. 25,000" after "Rs." cuts the price off its label, and a customer then
# reads "the processing fee is Rs." — which is exactly what the no-LLM fallback did.
_ABBREVIATIONS = (
    "Rs", "Mr", "Mrs", "Ms", "Dr", "Sr", "Jr", "No", "Nos", "vs", "approx",
    "Pvt", "Ltd", "Inc", "Co", "e.g", "i.e",
)

_NOT_AFTER_ABBREVIATION = "".join(rf"(?<!\b{re.escape(a)}\.)" for a in _ABBREVIATIONS)

# Whitespace that follows . ! or ? and does not follow an abbreviation.
SENTENCE_BOUNDARY = re.compile(rf"(?<=[.!?]){_NOT_AFTER_ABBREVIATION}\s+", re.IGNORECASE)


def split_sentences(text: str) -> list[str]:
    """Split one line of text into sentences without breaking "Rs. 25,000"."""
    return [s for s in SENTENCE_BOUNDARY.split((text or "").strip()) if s]
