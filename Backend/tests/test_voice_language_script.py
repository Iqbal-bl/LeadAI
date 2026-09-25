"""Language on a call is judged by the script the words were written in, not by fragment labels.

The third live call: a merged turn that was mostly Hindi, "क्या जी? क्यों नहीं आ रहा? Okay, okay,
back end.", carried the speech-to-text label of its LAST fragment (en-IN). The model was told
English, and the voice was switched to English while speaking Hindi text. Short fragments are
labelled unreliably; the script is not.

Also pinned: a returning customer is opened in the language they have been using (the caller on
that call got an English opener and then asked "can you speak Hindi?"), and the opener stays short.

Run: python tests/test_voice_language_script.py
"""
import asyncio

import conftest_stub  # noqa: F401

import test_voice_live_call as live  # noqa: E402  (wiring helpers)
from LeadAI import models  # noqa: E402
from LeadAI.services import voice_flow  # noqa: E402
from LeadAI.voice import pipeline  # noqa: E402
from LeadAI.voice.brain import BrainReply  # noqa: E402
from LeadAI.voice.session import CallSession  # noqa: E402

MERGED = "क्या जी? क्यों नहीं आ रहा? Okay, okay, back end."        # from the log


# ------------------------------------------------------------------- script detection
def test_the_script_decides_the_language():
    d = voice_flow.detect_language
    assert d("मोहाली में दो बीएचके फ्लैट की कीमत क्या है") == "hi-IN"
    assert d("what is the price of a two bhk") == "en-IN"
    assert d("ਮੋਹਾਲੀ ਵਿੱਚ ਫਲੈਟ ਦੀ ਕੀਮਤ ਕੀ ਹੈ") == "pa-IN"
    assert d("இது எவ்வளவு") == "ta-IN" and d("ఇది ఎంత") == "te-IN" and d("এটা কত") == "bn-IN"


def test_a_mixed_utterance_takes_the_script_with_the_most_letters():
    assert voice_flow.detect_language(MERGED) == "hi-IN"                 # the log's merged turn
    assert voice_flow.detect_language("हमने site visit की है") == "hi-IN"            # 3 Hindi words, 2 English
    assert voice_flow.detect_language("हमने site visit schedule की है") is None      # 3 v 3: a tie
    assert voice_flow.detect_language("हमने एक साइट विज़िट शेड्यूल की है, तो क्या वो postpone हो सकता है") == "hi-IN"


def test_unclear_text_has_no_language():
    for text in ("", "   ", "123 456", "?!", None):
        assert voice_flow.detect_language(text) is None
    assert voice_flow.detect_language("नमस्ते hello") is None                 # one word each: a tie


def test_the_script_beats_the_speech_to_text_label():
    r = voice_flow.resolve_language
    assert r(MERGED, "en-IN") == "hi-IN"                    # the bug: label said English
    assert r("Okay, okay, back end.", "hi-IN") == "en-IN"   # and the reverse
    assert r("what is the rate", None) == "en-IN"


def test_the_label_only_refines_within_a_script():
    r = voice_flow.resolve_language
    assert r("तुम्ही कसे आहात", "mr-IN") == "mr-IN"          # Marathi shares Devanagari with Hindi
    assert r("आप कैसे हैं", "hi-IN") == "hi-IN"
    assert r("ଆପଣ କେମିତି ଅଛନ୍ତି", "or-IN") == "or-IN"


def test_with_no_letters_the_label_is_used():
    assert voice_flow.resolve_language("123", "ta-IN") == "ta-IN"
    assert voice_flow.resolve_language("", None) is None


# ------------------------------------------------------------- inside a live turn
def test_the_merged_hindi_turn_is_answered_in_hindi_whatever_the_last_fragment_was_labelled():
    live.wire()
    db, conv, call, out = live.turn(MERGED, language="en-IN")           # the label from the log
    assert out.language == "hi-IN"
    system = " ".join(m["content"] for m in live.LLM_CALLS[-1]["messages"] if m["role"] == "system")
    assert "The caller is speaking Hindi" in system and "Devanagari" in system


def test_the_session_reports_the_resolved_language_on_the_reply_so_the_voice_matches_the_text():
    live.wire()
    db, client, conv, call = live.setup()
    session = CallSession(client_id=client.Id, conversation_id=conv.Id, call_sid="CA900",
                          session_factory=live.SessionLocalAdmin)
    session.set_language("en-IN")                                       # the misleading label
    reply = asyncio.run(session.respond(MERGED))
    assert reply.language == "hi-IN"


# ------------------------------------------------------------------------ the opener
def _customer_says(db, conv, *texts):
    for t in texts:
        db.add(models.LeadMessage(ClientId=conv.ClientId, ConversationId=conv.Id, Sender="customer", Content=t))
        db.add(models.LeadMessage(ClientId=conv.ClientId, ConversationId=conv.Id, Sender="ai", Content="ok"))
    db.commit()


def test_a_customer_who_chats_in_hindi_is_opened_in_hindi():
    live.wire(reply="नमस्ते मनमीत, मैं केस्ट्रल होम्स से काबिर बोल रहा हूँ। आपको किस बारे में मदद चाहिए?")
    db, client, conv, call = live.setup(display_name="Manmeet")
    _customer_says(db, conv, "मुझे दो बीएचके देखना है", "मोहाली में कीमत क्या है", "साइट विज़िट कब हो सकती है")
    assert voice_flow.opening_language(db, conv) == "hi-IN"
    text, message_id = voice_flow.opening_line(db, client, conv, call, language="hi-IN")
    system = " ".join(m["content"] for m in live.LLM_CALLS[-1]["messages"] if m["role"] == "system")
    assert "The caller is speaking Hindi" in system and text.startswith("नमस्ते")


def test_an_english_speaking_customer_is_opened_in_english_and_a_new_one_has_no_language():
    live.wire()
    db, client, conv, call = live.setup()
    assert voice_flow.opening_language(db, conv) is None                # nothing said yet
    _customer_says(db, conv, "what is the price of a two bhk", "is there a site visit on saturday")
    assert voice_flow.opening_language(db, conv) == "en-IN"


def test_only_the_last_few_messages_decide_the_openers_language():
    live.wire()
    db, client, conv, call = live.setup()
    _customer_says(db, conv, "मुझे दो बीएचके देखना है", "मोहाली में कीमत क्या है",       # older, Hindi
                   "what is the price", "is there a site visit", "can I book for saturday")   # recent, English
    assert voice_flow.opening_language(db, conv) == "en-IN"


def test_the_opener_is_asked_to_be_short_and_the_prompt_carries_no_internal_notes():
    live.wire()
    db, client, conv, call = live.setup()
    _customer_says(db, conv, "what is the price of a two bhk")
    voice_flow.opening_line(db, client, conv, call, language="en-IN")
    prompt = LLM_LAST_USER()
    assert "At most 20 words" in prompt
    assert "first version" not in prompt and "23" not in prompt          # history stays in code comments


def LLM_LAST_USER():
    return live.LLM_CALLS[-1]["messages"][-1]["content"]


def test_the_session_reports_the_openers_language_so_the_voice_can_be_switched_first():
    live.wire(reply="नमस्ते!")
    db, client, conv, call = live.setup()
    _customer_says(db, conv, "मुझे दो बीएचके देखना है", "मोहाली में कीमत क्या है")
    session = CallSession(client_id=client.Id, conversation_id=conv.Id, call_sid="CA900",
                          session_factory=live.SessionLocalAdmin)
    opening = asyncio.run(session.opening())
    assert opening.language == "hi-IN" and opening.text


def test_opening_frames_switch_the_voice_before_speaking_only_when_there_is_a_language():
    from pipecat.frames.frames import TextFrame, TTSSpeakFrame

    services = pipeline.Services(stt=None, tts=None, language_frame=lambda code: TextFrame(text=f"LANG:{code}"))
    frames = pipeline.opening_frames(BrainReply(text="नमस्ते", language="hi-IN"), services)
    assert [type(f).__name__ for f in frames] == ["TextFrame", "TTSSpeakFrame"] and frames[0].text == "LANG:hi-IN"
    assert isinstance(frames[1], TTSSpeakFrame) and frames[1].text == "नमस्ते"

    assert [type(f).__name__ for f in pipeline.opening_frames(BrainReply(text="Hello"), services)] == ["TTSSpeakFrame"]
    assert pipeline.opening_frames(BrainReply(text=""), services) == []                  # nothing to say
    no_switch = pipeline.Services(stt=None, tts=None)                                     # e.g. test stand-ins
    assert [type(f).__name__ for f in pipeline.opening_frames(BrainReply(text="Hi", language="hi-IN"), no_switch)] == ["TTSSpeakFrame"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
