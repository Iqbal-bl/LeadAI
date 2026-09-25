"""
Grounding check: is every hard fact in a reply actually present in its sources?

The costliest hallucination in a sales assistant is an invented number: a price, a
percentage, a date, a phone number. Prose can be off-tone and do little harm; a
made-up "8.5% interest" is a commitment. So this checks numbers, deterministically
and in microseconds, which makes it usable on the live voice path where an LLM
verifier would add seconds.

It is used two ways:
  * as the eval metric (how often does the assistant state a figure it cannot see?);
  * as the core of the engine's verify step (Phase 3), where an unsupported figure
    blocks or rewrites the reply.

It is deliberately conservative about what it flags. A number the CUSTOMER just said
is fine to repeat back, and small bare integers ("2 options", "step 3") are not
facts worth blocking on.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 1,25,000 / 125,000 / 8.5 / 8.5% / 30 — digits with optional separators and decimals.
_NUMBER = re.compile(
    r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(%|(?:percent|lakhs?|crores?|cr|k)(?![a-z]))?", re.I
)

# Spoken quantity words are normalised so "1.25 crore" and "1.25 Cr" compare equal.
_UNIT_ALIASES = {
    "percent": "%",
    "lakhs": "lakh",
    "crores": "crore",
    "cr": "crore",
}

# Bare integers below this are ordinals and counts ("2 minutes", "3 steps"), not
# commitments. Anything with a unit (%, lakh, crore, k) is always checked.
_MIN_BARE_INT = 10


@dataclass(frozen=True)
class Figure:
    value: str          # canonical: commas stripped, trailing .0 dropped, unit appended
    raw: str            # as it appeared


def _canon(number: str, unit: str | None) -> str:
    num = number.replace(",", "")
    if "." in num:
        num = num.rstrip("0").rstrip(".")
    unit = (unit or "").lower()
    unit = _UNIT_ALIASES.get(unit, unit)
    return f"{num}{unit}"


def extract_figures(text: str) -> list[Figure]:
    """Every checkable number in `text`, in order of appearance."""
    figures: list[Figure] = []
    for m in _NUMBER.finditer(text or ""):
        number, unit = m.group(1), m.group(2)
        if not unit and "." not in number:
            digits = number.replace(",", "")
            if digits.isdigit() and int(digits) < _MIN_BARE_INT:
                continue
        figures.append(Figure(_canon(number, unit), m.group(0).strip()))
    return figures


@dataclass(frozen=True)
class GroundingResult:
    supported: bool
    unsupported: list[Figure]

    @property
    def unsupported_raw(self) -> list[str]:
        return [f.raw for f in self.unsupported]


def check_reply(reply: str, sources: list[str], allowed: list[str] | None = None) -> GroundingResult:
    """Flag figures in `reply` that appear in neither `sources` nor `allowed`.

    `allowed` is text whose numbers the assistant may legitimately repeat: the
    customer's own message, the pinned facts, the company name.
    """
    known: set[str] = set()
    for chunk in [*sources, *(allowed or [])]:
        for fig in extract_figures(chunk):
            known.add(fig.value)
            # "8.5%" in the source supports a bare "8.5" in the reply and vice versa.
            known.add(fig.value.rstrip("%"))
    # Bare integers that the extractor skipped in sources still count as known.
    for chunk in [*sources, *(allowed or [])]:
        for m in _NUMBER.finditer(chunk or ""):
            known.add(_canon(m.group(1), None))

    bad = [
        f
        for f in extract_figures(reply)
        if f.value not in known and f.value.rstrip("%") not in known
    ]
    return GroundingResult(supported=not bad, unsupported=bad)
