"""A fixed hand-off line must be spoken in the CALLER's language, never in whatever language the
discarded reply happened to be in.

Reproduces a live call: a Hindi caller asked for a human. Because that path skips the LLM and
quotes the (English) knowledge base instead, ai_engine.answer() correctly reported the language
of THAT text as English — but voice_flow then reused that value for callback_line, a completely
different, hand-written line, and spoke it in English to a caller who had been in Hindi the whole
call. Same risk for unsure_line (the grounding-guard hand-off) and for the "nothing to say"
fallback. A normal answer that IS the model's own text must still follow whatever language that
text is actually in (unchanged from before this fix).

Run: python tests/test_voice_handoff_language.py
"""
import conftest_stub  # noqa: F401

import test_voice_live_call as live  # noqa: E402  (wiring helpers)
from LeadAI.services import ai_engine, voice_flow  # noqa: E402


def wire_with_translation(answer_reply, translated_query):
    """live.wire(), but the fake model gives a REAL answer to translation calls too — needed
    whenever a test relies on the ENGLISH TRANSLATION containing a phrase (like "human") that
    ai_engine.answer() checks for, since live.wire() otherwise returns the same canned text
    for every call regardless of what was asked."""
    live.wire(reply=answer_reply)

    def fake_complete(system, messages, **kw):
        live.LLM_CALLS.append({"system": system, "messages": messages, **kw})
        if "Translate" in system:
            return translated_query, {"model": "fake", "latency_ms": 1}
        return answer_reply, {"model": "fake-model", "latency_ms": 5}

    ai_engine.llm.complete = fake_complete
    voice_flow.llm.complete = fake_complete

HINDI_Q = "मुझे किसी इंसान से बात करनी है"        # "I want to talk to a human"


# --------------------------------------------------------------------------- the line-pickers
def test_callback_and_unsure_lines_exist_for_hindi_punjabi_and_default_to_english():
    for lang, must_contain in (("hi-IN", "विशेषज्ञ"), ("pa-IN", "ਮਾਹਿਰ"), ("xx-XX", "specialist")):
        assert must_contain in voice_flow.callback_line(lang), lang
    for lang, must_contain in (("hi-IN", "विशेषज्ञ"), ("pa-IN", "ਮਾਹਿਰ"), (None, "specialist")):
        assert must_contain in voice_flow.unsure_line(lang), lang


def test_the_lines_are_never_literally_the_hardcoded_english_string_for_hindi():
    # Old bug shape: a fixed English constant, unconditionally, regardless of caller language.
    assert voice_flow.callback_line("hi-IN") != voice_flow.callback_line("en-IN")
    assert voice_flow.unsure_line("hi-IN") != voice_flow.unsure_line("en-IN")


# ------------------------------------------------------------------- the reproduced live bug
def test_a_hindi_callers_request_for_a_human_is_answered_in_hindi_not_the_kb_language():
    # wants_human path: extractive fallback quotes ENGLISH knowledge, but the SPOKEN line must
    # follow the caller (Hindi), not that discarded English text's language. The English
    # translation must itself say "human" so ai_engine detects the request (see
    # ai_engine.answer: wants_human checks BOTH the raw question and its translation).
    wire_with_translation("irrelevant", "I want to talk to a human")
    db, conv, call, out = live.turn(HINDI_Q, language="hi-IN")
    assert out.language == "hi-IN"
    assert out.reply_text == voice_flow.callback_line("hi-IN")
    assert "specialist" not in out.reply_text            # not the English line


def test_an_english_callers_request_for_a_human_is_still_answered_in_english():
    live.wire()          # English question: HUMAN_REQUEST matches the raw text directly
    db, conv, call, out = live.turn("I want to talk to a human", language="en-IN")
    assert out.language == "en-IN" and out.reply_text == voice_flow.callback_line("en-IN")


def test_the_unsure_line_for_an_ungrounded_reply_also_follows_the_caller_not_the_reply():
    from LeadAI.engine import bridge

    live.wire(reply="The processing fee is Rs. 15,000.")     # a figure not in the KB
    saved = bridge.settings
    try:
        bridge.settings = type("S", (), {"engine_mode": "enforce",
                                         "__getattr__": lambda self, n: getattr(saved, n)})()
        db, conv, call, out = live.turn("प्रोसेसिंग फीस क्या है", language="hi-IN")
    finally:
        bridge.settings = saved
    assert out.language == "hi-IN" and out.reply_text == voice_flow.unsure_line("hi-IN")
    assert "15,000" not in out.reply_text


def test_a_low_confidence_reply_that_has_no_text_falls_back_to_the_callers_own_callback_line():
    live.wire(reply="", hits=False)
    db, conv, call, out = live.turn("मुझे रेट बताइए", language="hi-IN")
    assert out.language == "hi-IN" and out.reply_text == voice_flow.callback_line("hi-IN")


# ---------------------------------------------------- unaffected: a normal spoken model answer
def test_a_normal_confident_answer_still_follows_whatever_language_the_model_actually_wrote():
    # Unchanged behaviour: when we ARE speaking the model's own text, the voice follows what it
    # actually wrote (already covered by test_language_guard.py; kept here as a regression
    # tripwire specifically against this file's refactor of the same code path).
    live.wire(reply="जी हाँ, दर 8.5 प्रतिशत से शुरू होती है।")
    db, conv, call, out = live.turn("ब्याज दर क्या है", language="hi-IN")
    assert out.language == "hi-IN" and out.reply_text == "जी हाँ, दर 8.5 प्रतिशत से शुरू होती है।"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
