"""Who the voice agent is, and what the call is about, read from the script sections.

The voice app opens every call with a greeting built from three facts: the agent's name,
the company, and (optionally) the reason for the call. They are read out of the sections
the call was started with, in whatever shape those arrive (see `extract_call_identity`).

Two things must never be mistaken for the agent's identity:

  * The caller-context sections LeadAI prepends for a returning customer ("Caller Context",
    "Recent Messages From This Customer"). They contain the CUSTOMER's name and a summary of
    the earlier chat. Scanned as if they were the script, they made the agent introduce
    itself as the customer and read the summary aloud.
  * A qualification field that merely happens to be called `purpose` ("is it for own use or
    an investment?"). That is a question to ask the customer, not the reason for the call.
"""
from __future__ import annotations

import re

from outbound.xml_parser import sections_to_prompt

# Sections LeadAI injects for a returning customer (LeadAI/services/memory.py). They describe
# the customer, never the agent.
BRIEFING_SECTION_IDS = {"caller_context", "recent_messages"}

_NAME_KEYS = {"name", "agent name", "agent_name", "agentname"}
_COMPANY_KEYS = {"company", "company name", "company_name", "organisation", "organization"}
_TOPIC_KEYS = {"topic", "call_topic", "call topic", "call_reason", "call reason"}
# `purpose` means "reason for the call" only where the script describes the caller itself.
_PURPOSE_KEY = "purpose"

_NAME_RE = re.compile(r"(?:agent\s*)?name\s*[:=]\s*([^\n,;]+)", re.IGNORECASE)
_COMPANY_RE = re.compile(r"company\s*[:=]\s*([^\n,;]+)", re.IGNORECASE)
_TOPIC_RE = re.compile(r"(?:topic|call[_\s]topic|call[_\s]reason)\s*[:=]\s*([^\n,;]+)", re.IGNORECASE)
_TOPIC_WITH_PURPOSE_RE = re.compile(
    r"(?:topic|call[_\s]topic|call[_\s]reason|purpose)\s*[:=]\s*([^\n,;]+)", re.IGNORECASE
)


def is_briefing_section(section) -> bool:
    return isinstance(section, dict) and section.get("id") in BRIEFING_SECTION_IDS


def has_caller_context(xml_sections) -> bool:
    """True when this call carries the earlier-conversation briefing."""
    return any(is_briefing_section(s) for s in (xml_sections or []))


def extract_call_identity(xml_sections) -> tuple[str, str | None, str | None]:
    """Return (agent_name, company_name, call_topic). Company and topic may be None.

    Reads every shape the sections arrive in: a `rawContent` string, a `content` list of
    {name, value} fields, or a `content` string ("name: Riya\\ncompany: Prime"), and, only
    if something is still missing, the rendered prompt. Identity sections are read first,
    and the caller-context sections are skipped throughout.
    """
    found: dict[str, str | None] = {"agent": None, "company": None, "topic": None}

    def scan_text(text: str, *, allow_purpose: bool) -> None:
        if not text:
            return
        if found["agent"] is None and (m := _NAME_RE.search(text)):
            found["agent"] = m.group(1).strip()
        if found["company"] is None and (m := _COMPANY_RE.search(text)):
            found["company"] = m.group(1).strip()
        if found["topic"] is None:
            topic_re = _TOPIC_WITH_PURPOSE_RE if allow_purpose else _TOPIC_RE
            if m := topic_re.search(text):
                found["topic"] = m.group(1).strip()

    sections = [s for s in (xml_sections or []) if not is_briefing_section(s)]
    # The Identity section is the authority on who the agent is: read it before the rest.
    sections.sort(key=lambda s: 0 if isinstance(s, dict) and s.get("type") == "identity" else 1)

    for section in sections:
        if not isinstance(section, dict):
            if isinstance(section, str):
                scan_text(section, allow_purpose=False)
            continue

        is_identity = section.get("type") == "identity"
        scan_text(section.get("rawContent") or "", allow_purpose=is_identity)

        content = section.get("content")
        if isinstance(content, str):
            scan_text(content, allow_purpose=is_identity)
        elif isinstance(content, list):
            for field in content:
                if not isinstance(field, dict):
                    continue
                fname = (field.get("name") or field.get("label") or "").strip().lower()
                fval = field.get("value")
                if not fval:
                    continue
                if fname in _NAME_KEYS and found["agent"] is None:
                    found["agent"] = str(fval).strip()
                elif fname in _COMPANY_KEYS and found["company"] is None:
                    found["company"] = str(fval).strip()
                elif found["topic"] is None and (
                    fname in _TOPIC_KEYS or (fname == _PURPOSE_KEY and is_identity)
                ):
                    found["topic"] = str(fval).strip()

    # Last resort: the rendered prompt (what the model sees), still without the briefing
    # and without treating a bare `purpose:` line as the reason for the call.
    if found["agent"] is None or found["company"] is None or found["topic"] is None:
        try:
            scan_text(sections_to_prompt(sections), allow_purpose=False)
        except Exception:  # noqa: BLE001 — a rendering problem must never stop a call
            pass

    # Only the agent name gets a default; company and topic stay None so the greeting
    # simply leaves those parts out.
    return found["agent"] or "Agent", found["company"], found["topic"]
