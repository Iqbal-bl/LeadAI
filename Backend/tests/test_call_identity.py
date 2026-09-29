"""The voice agent introduces itself as itself, not as the customer it is calling.

Reproduces the live call where the agent said "Hello! I am Manmeet Kaur. This conversation so
far: The customer is looking for a 2 BHK…": for a returning customer LeadAI prepends a
"Caller Context" section that contains the CUSTOMER's name and a chat summary, and the old
identity scan read that before the script's own Identity section.
No database or network. Run: python tests/test_call_identity.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from outbound.call_identity import extract_call_identity, has_caller_context  # noqa: E402
from outbound.xml_parser import parse_xml_to_sections  # noqa: E402

KESTREL = parse_xml_to_sections((ROOT / "demo" / "kestrel_homes_agent_script.xml").read_text(encoding="utf-8"))

# The two sections LeadAI/services/memory.py voice_briefing() prepends, with the same text shape
# as the live call (the digest starts with the customer's name and an earlier-chat summary).
BRIEFING = [
    {"id": "caller_context", "title": "Caller Context", "type": "text",
     "content": "You have spoken with this person before. Do NOT introduce yourself as if this is a "
                "first contact. Name: Manmeet Kaur. This conversation so far: The customer is looking "
                "for a 2 BHK apartment in Mohali. Agreed next step: Confirm the site visit details."},
    {"id": "recent_messages", "title": "Recent Messages From This Customer", "type": "data",
     "content": [{"name": "turn_1", "label": "Customer — Instagram", "value": "I am looking for flat in mohali"}]},
]


def test_returning_customer_does_not_change_who_the_agent_is():
    name, company, topic = extract_call_identity(BRIEFING + KESTREL)   # briefing FIRST, as in production
    assert name == "Kabir"
    assert company == "Kestrel Homes"
    assert "Manmeet" not in name and "conversation so far" not in name


def test_a_qualification_field_called_purpose_is_not_the_reason_for_the_call():
    _, _, topic = extract_call_identity(BRIEFING + KESTREL)
    assert topic is None       # was "Whether it is for the customer's own use or an investment."


def test_same_result_with_and_without_the_briefing():
    assert extract_call_identity(BRIEFING + KESTREL) == extract_call_identity(KESTREL)


def test_caller_context_is_detected():
    assert has_caller_context(BRIEFING + KESTREL) and not has_caller_context(KESTREL)


def test_identity_section_wins_even_when_it_is_not_first():
    other = {"id": "notes", "title": "Notes", "type": "text", "content": "name: Somebody Else"}
    assert extract_call_identity([other] + KESTREL)[0] == "Kabir"


# --- the shapes older scripts and the UI send must keep working ------------------------------- #
def test_legacy_shapes_still_work():
    raw = [{"type": "text", "rawContent": "name: Riya\ncompany: Prime Bank"}]
    assert extract_call_identity(raw)[:2] == ("Riya", "Prime Bank")
    listed = [{"type": "identity", "content": [{"name": "agent name", "value": "Asha"},
                                                {"name": "company_name", "value": "Acme"}]}]
    assert extract_call_identity(listed)[:2] == ("Asha", "Acme")
    assert extract_call_identity(["name: Ravi\ncompany: Globex"])[:2] == ("Ravi", "Globex")


def test_a_real_call_topic_is_still_used():
    with_topic = [{"type": "identity", "content": [{"name": "name", "value": "Riya"},
                                                    {"name": "topic", "value": "your card activation"}]}]
    assert extract_call_identity(with_topic)[2] == "your card activation"
    # `purpose` counts as the reason for the call only inside the Identity section
    purpose_in_identity = [{"type": "identity", "content": [{"name": "name", "value": "Riya"},
                                                             {"name": "purpose", "value": "loan follow-up"}]}]
    assert extract_call_identity(purpose_in_identity)[2] == "loan follow-up"


def test_no_identity_at_all_falls_back_to_a_generic_agent():
    assert extract_call_identity([]) == ("Agent", None, None)
    assert extract_call_identity(None) == ("Agent", None, None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
