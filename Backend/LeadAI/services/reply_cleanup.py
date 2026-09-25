"""Clean model output, and recognise a finished conversation.

Voice scripts tell the model to end a phone call by appending the control token
`[END_CALL]`. The voice pipeline (outbound/app.py) strips it before speaking, but the
same script also drives chat and social replies, where nothing removed it, so
customers saw a literal "[END_CALL]" at the end of a message.

Once the model has signalled the end, the conversation is "completed": the customer
has been told an advisor will follow up. A bare "ok" / "yes" / "thanks" after that
should get a short courteous line, not a fresh round of qualification questions.
"""
import re

# Control tokens the scripts ask the model to emit. They are instructions to the
# system, never text for a person.
_CONTROL_TOKENS = re.compile(r"\[\s*END[_ ]CALL\s*\]", re.IGNORECASE)


def strip_control_tokens(text: str) -> str:
    """Remove control tokens and the blank space they leave behind."""
    if not text:
        return text or ""
    return _CONTROL_TOKENS.sub("", text).rstrip()


def has_end_call_token(text: str) -> bool:
    """True if the model signalled that the conversation is finished."""
    return bool(text and _CONTROL_TOKENS.search(text))


# --------------------------------------------------------------------------- #
# acknowledgements after a completed conversation
# --------------------------------------------------------------------------- #
_ENGLISH_ACK = {
    "ok", "okay", "okk", "k", "kk", "yes", "yeah", "yep", "yup", "yea", "ya", "sure",
    "fine", "alright", "cool", "great", "nice", "good", "perfect", "thanks", "thank",
    "you", "thx", "ty", "done", "got", "it", "understood", "noted", "hmm", "hm", "please",
    "bye", "goodbye", "sounds", "right",
}
# Hindi / Hinglish, written in Latin script.
_HINGLISH_ACK = {
    "ji", "haan", "han", "hanji", "haanji", "theek", "thik", "hai", "shukriya",
    "dhanyavad", "dhanyawad", "accha", "acha", "bilkul", "sahi",
}
_ACK_WORDS = _ENGLISH_ACK | _HINGLISH_ACK
_MAX_ACK_WORDS = 4


def _words(text: str) -> list[str]:
    # Letters and digits only: drops punctuation and emoji, keeps Devanagari as one word.
    return re.findall(r"[^\W_]+", (text or "").lower())


def is_acknowledgement(text: str) -> bool:
    """True for a message that only acknowledges: "ok", "yes", "ok thanks", "theek hai"."""
    words = _words(text)
    return 0 < len(words) <= _MAX_ACK_WORDS and all(w in _ACK_WORDS for w in words)


def closing_reply(company_name: str, text: str = "") -> str:
    """Fixed reply for an acknowledgement after the conversation is completed."""
    if any(w in _HINGLISH_ACK for w in _words(text)):
        return (
            f"Shukriya! {company_name} ka advisor jald hi aapse sampark karega. "
            "Koi aur sawaal ho to yahin poochh sakte hain."
        )
    return (
        f"Thank you! An advisor from {company_name} will contact you shortly. "
        "If you have any other question, just ask me here."
    )


# Injected as a system turn once a conversation is completed and the customer keeps
# talking, so the model continues from where it stopped instead of starting over.
COMPLETED_NOTE = (
    "This conversation is already complete. You gave the customer the next steps and told "
    "them an advisor will contact them. Do NOT restart the qualification questions and do "
    "NOT ask again for anything the customer already told you. Answer only what they now "
    "ask, using the company knowledge; if it is not covered, say an advisor will confirm."
)
