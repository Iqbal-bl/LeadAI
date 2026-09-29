"""
Which language is a piece of text in, and is a reply in the language it should be?

Shared by the phone brain (services/voice_flow.py) and the answering code (services/ai_engine.py),
so neither has to import the other.

WHY THE SCRIPT, NOT A LABEL
Speech-to-text labels each utterance, but a burst arrives as short fragments, and short fragments
are labelled unreliably: on the third live call a merged turn that was mostly Hindi ("क्या जी? क्यों नहीं
आ रहा? Okay, okay, back end.") carried the label of its last fragment (en-IN), so the model was told
English and the voice was switched to English while speaking Hindi. The SCRIPT the words are written
in is far more reliable.

WHY EARLIER REPLIES ARE FILTERED
The fourth live call told the model, correctly, that the caller spoke Hindi ("Reply in Hindi
(Devanagari script)"), and every Hindi turn still came back in Punjabi (Gurmukhi). Measured against
the real model with a history whose earlier assistant replies were Punjabi (6 samples each):

    instruction at the START of the thread ..................... Hindi 0/6
    instruction at the END, next to the question ............... Hindi 0/6
    retry, showing the model its own wrong answer .............. Hindi 0/6
    earlier assistant replies in another language REMOVED ...... Hindi 6/6

The model imitates its own earlier replies, and each Punjabi reply added to the history made the next
one more likely, so it fed itself. Instructions and retries do not break that loop; removing the
poisoned replies does. (The caller's own lines are kept.) The reply's script is still checked
afterwards, so the voice always speaks the language the text is actually in.
"""
from __future__ import annotations

import unicodedata

# code prefix -> (English name, script description)
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


def language_name(code: str | None) -> tuple[str | None, str | None]:
    """(name, script description) for a language code such as "hi-IN", or (None, None)."""
    return _LANGUAGES.get((code or "").strip().lower().split("-")[0], (None, None))


def language_note(code: str | None) -> str:
    """An instruction naming the language to reply in. "" when the language is unknown."""
    name, script = language_name(code)
    if not name:
        return ""
    where = f" ({script})" if script else ""
    return (
        f"The caller is speaking {name}. Reply in {name}{where}, in short spoken sentences. "
        "Keep product names and words the caller used in English (such as 'BHK') in English."
    )


def reply_instruction(code: str | None) -> str:
    """A short instruction for the END of the prompt, where the model weighs it most."""
    name, script = language_name(code)
    if not name:
        return ""
    where = f" ({script})" if script else ""
    return f"(Answer in {name}{where}.)"


def drop_other_language_replies(messages: list[dict], wanted: str | None) -> list[dict]:
    """Remove ASSISTANT messages written in a different language than `wanted`.

    The caller's own messages, and replies whose language is unclear (no letters, ties), are kept.
    See the module docstring for why this, and not an instruction, is what fixes the wrong-language
    replies.
    """
    if not wanted:
        return list(messages)
    kept = []
    for m in messages:
        if m.get("role") == "assistant":
            got = detect_language(m.get("content"))
            if got and not same_language(got, wanted):
                continue
        kept.append(m)
    return kept


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


def same_language(a: str | None, b: str | None) -> bool:
    """Same language for our purposes: equal, or sharing a script (Hindi and Marathi)."""
    return bool(a and b) and _base(a) == _base(b)


def resolve_language(text: str | None, reported: str | None) -> str | None:
    """The script wins over the speech-to-text label; the label only refines within a script
    (Marathi vs Hindi share Devanagari) or fills in when the text has no letters."""
    detected = detect_language(text)
    if detected is None:
        return reported
    if reported and same_language(reported, detected):
        return reported
    return detected
