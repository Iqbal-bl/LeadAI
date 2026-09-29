"""A Hindi caller must get a Hindi answer (fourth live call: every Hindi turn came back in Punjabi).

The instruction "Reply in Hindi (Devanagari script)" WAS sent, and the model ignored it because it
imitates its own earlier replies: the history already held Punjabi assistant messages, and each new
Punjabi reply made the next more likely. Measured on the real model (6 samples each): instruction at
the start 0/6 Hindi, instruction at the end 0/6, a retry showing its wrong answer 0/6, earlier
other-language replies REMOVED 6/6. So that is what is done, and these tests pin it. (The retry that
was built first is gone: it measured as useless and only added a model call.)

Run: python tests/test_language_guard.py
"""
import conftest_stub  # noqa: F401

import test_voice_live_call as live  # noqa: E402  (wiring helpers)
from LeadAI import models  # noqa: E402
from LeadAI.engine.trace import TurnTrace  # noqa: E402
from LeadAI.services import ai_engine, language, voice_flow  # noqa: E402

# The actual reply from the log: Devanagari for two words, then Gurmukhi (Punjabi).
LOGGED_REPLY = "जी हाँ, ਤੁਸੀਂ ਸਹੀ ਕਹਿ ਰਹੇ ਹੋ। ਤੁਸੀਂ 2 BHK ਅਪਾਰਟਮੈਂਟ ਲਈ ਸਾਈਟ ਵਿਜ਼ਿਟ ਬੁੱਕ ਕੀਤੀ ਸੀ।"
HINDI = "जी हाँ, आपने ठीक कहा। आपने 2 BHK अपार्टमेंट के लिए साइट विज़िट बुक की थी।"
ENGLISH = "Yes, you are right. You booked a site visit for the 2 BHK apartment."
QUESTION = "मुझे लगता है हमारी बात हो चुकी है इस बारे में।"
PUNJABI_1 = "ਜੀ ਹਾਂ, ਮੈਂ ਤੁਹਾਡੀ ਮਦਦ ਕਰ ਸਕਦਾ ਹਾਂ। ਤੁਸੀਂ ਕਿਸ ਬਜਟ ਵਿੱਚ ਦੇਖ ਰਹੇ ਹੋ?"
PUNJABI_2 = "ਸਾਈਟ ਵਿਜ਼ਿਟ ਹਰ ਰੋਜ਼ ਸਵੇਰੇ 10 ਵਜੇ ਤੋਂ ਸ਼ਾਮ 6 ਵਜੇ ਤੱਕ ਹੁੰਦੀ ਹੈ।"
CALLS = []


def script(reply):
    """The model returns `reply` for answers (and a stub for retrieval translation); calls are recorded."""
    live.wire()
    CALLS.clear()

    def fake(system, messages, **kw):
        if "Translate" in system:
            return "I think we already talked about this", {"model": "fake", "latency_ms": 1}
        CALLS.append({"system": system, "messages": messages, **kw})
        return reply, {"model": "fake", "latency_ms": 100}

    ai_engine.llm.complete = fake


def msg(sender, text):
    return models.LeadMessage(Sender=sender, Content=text)


POISONED = [
    msg("customer", "मुझे दो बीएचके देखना है"), msg("ai", PUNJABI_1),
    msg("customer", "साइट विज़िट कब हो सकती है"), msg("ai", PUNJABI_2),
    msg("customer", "ठीक है, रविवार शाम चार बजे रख दीजिए"), msg("ai", LOGGED_REPLY),
]


def answer(reply_language, history=None, question=QUESTION, trace=None):
    db, client, conv, call = live.setup()
    return ai_engine.answer(db, client.Id, "Kestrel Homes", question, history=history or [], channel="voice",
                            reply_language=reply_language, trace=trace)


def sent_roles_and_text():
    return [(m["role"], m["content"]) for m in CALLS[-1]["messages"]]


# --------------------------------------------------------------------------- the helpers
def test_the_reply_from_the_log_is_detected_as_punjabi():
    assert language.detect_language(LOGGED_REPLY) == "pa-IN"
    assert language.detect_language(HINDI) == "hi-IN" and language.detect_language(ENGLISH) == "en-IN"


def test_same_language_treats_scripts_sharing_a_language_family_as_equal():
    assert language.same_language("hi-IN", "hi") and language.same_language("mr-IN", "hi-IN")
    assert not language.same_language("hi-IN", "pa-IN") and not language.same_language(None, "hi-IN")


def test_the_reply_instruction_names_language_and_script_or_is_empty():
    assert language.reply_instruction("hi-IN") == "(Answer in Hindi (Devanagari script).)"
    assert language.reply_instruction("en-IN") == "(Answer in English.)"
    assert language.reply_instruction("xx-XX") == "" and language.reply_instruction(None) == ""


def test_only_assistant_replies_in_another_language_are_dropped():
    messages = [
        {"role": "user", "content": "मुझे दो बीएचके देखना है"},          # caller: kept
        {"role": "assistant", "content": PUNJABI_1},                     # other language: dropped
        {"role": "assistant", "content": HINDI},                         # right language: kept
        {"role": "assistant", "content": "8.5%"},                        # no letters, unclear: kept
        {"role": "system", "content": PUNJABI_2},                        # not an assistant message: kept
    ]
    kept = language.drop_other_language_replies(messages, "hi-IN")
    assert [m["content"] for m in kept] == ["मुझे दो बीएचके देखना है", HINDI, "8.5%", PUNJABI_2]
    assert language.drop_other_language_replies(messages, None) == messages          # nothing wanted: untouched
    assert messages[1]["content"] == PUNJABI_1                                       # the input is not modified


# ------------------------------------------------------------------- the answering code
def test_a_poisoned_history_is_cleaned_before_the_model_sees_it():
    script(HINDI)
    trace = TurnTrace(conversation_id="c", client_id="c", channel="voice")
    answer("hi-IN", history=list(POISONED), trace=trace)
    sent = sent_roles_and_text()
    assistant_lines = [t for r, t in sent if r == "assistant"]
    assert assistant_lines == []                                          # every earlier reply was Punjabi
    assert not any("ਤੁਸੀਂ" in t or "ਜੀ ਹਾਂ" in t for _, t in sent)      # not a Gurmukhi word reaches the model
    assert [t for r, t in sent if r == "user"][0] == "मुझे दो बीएचके देखना है"    # the caller's lines stay
    step = next(s for s in trace.steps if s["step"] == "language_history")
    assert step["detail"]["removed"] == 3 and "hi-IN" in step["decision"]


def test_replies_already_in_the_right_language_are_kept_and_nothing_is_traced():
    script(HINDI)
    history = [msg("customer", "मुझे दो बीएचके देखना है"), msg("ai", HINDI)]
    trace = TurnTrace(conversation_id="c", client_id="c", channel="voice")
    answer("hi-IN", history=history, trace=trace)
    assert [t for r, t in sent_roles_and_text() if r == "assistant"] == [HINDI]
    assert not any(s["step"] == "language_history" for s in trace.steps)


def test_an_english_caller_is_not_shown_earlier_hindi_or_punjabi_replies():
    script(ENGLISH)
    answer("en-IN", history=list(POISONED), question="I think we already spoke about this")
    assert [t for r, t in sent_roles_and_text() if r == "assistant"] == []


def test_the_language_instruction_is_the_last_thing_the_model_reads():
    script(HINDI)
    answer("hi-IN")
    final = CALLS[-1]["messages"][-1]["content"]
    assert final.endswith("(Answer in Hindi (Devanagari script).)") and QUESTION in final


def test_a_wrong_language_reply_is_reported_by_its_real_language_with_one_model_call_only():
    script(LOGGED_REPLY)                                          # the model still gets it wrong
    trace = TurnTrace(conversation_id="c", client_id="c", channel="voice")
    out = answer("hi-IN", trace=trace)
    assert len(CALLS) == 1                                        # no retry: it measured as useless
    assert out["reply"] == LOGGED_REPLY and out["language"] == "pa-IN"        # the voice will follow the text
    step = next(s for s in trace.steps if s["step"] == "language_guard")
    assert "pa-IN" in step["decision"] and "hi-IN" in step["decision"]


def test_a_reply_in_the_right_language_is_traced_as_nothing():
    script(HINDI)
    trace = TurnTrace(conversation_id="c", client_id="c", channel="voice")
    out = answer("hi-IN", trace=trace)
    assert out["language"] == "hi-IN" and not any(s["step"] == "language_guard" for s in trace.steps)


def test_without_a_requested_language_nothing_changes():
    script(LOGGED_REPLY)
    out = answer(None, history=list(POISONED))
    assert "(Answer in" not in CALLS[0]["messages"][-1]["content"]
    assert len([r for r, _ in sent_roles_and_text() if r == "assistant"]) == 3      # history untouched
    assert out["language"] == "pa-IN"


def test_a_reply_with_no_letters_is_left_alone():
    script("8.5%")
    out = answer("hi-IN")
    assert out["reply"] == "8.5%"


# ------------------------------------------------------------------------ the phone brain
def poisoned_call():
    db, client, conv, call = live.setup()
    for who, text in (("customer", "मुझे दो बीएचके देखना है"), ("ai", PUNJABI_1),
                      ("customer", "साइट विज़िट कब हो सकती है"), ("ai", PUNJABI_2)):
        db.add(models.LeadMessage(ClientId=client.Id, ConversationId=conv.Id, Sender=who, Content=text))
    db.commit()
    return db, client, conv, call


def test_on_a_real_poisoned_conversation_the_model_is_not_shown_the_punjabi_replies():
    script(HINDI)
    db, client, conv, call = poisoned_call()
    out = voice_flow.handle_voice_turn(db, client, conv, call, QUESTION, live_call=True,
                                       defer_scoring=True, commit=True, language="hi-IN")
    assert out.language == "hi-IN" and out.reply_text == HINDI
    assert not any("ਜੀ ਹਾਂ" in t or "ਸਾਈਟ ਵਿਜ਼ਿਟ" in t for _, t in sent_roles_and_text())
    stored = db.query(models.LeadMessage).filter_by(ConversationId=conv.Id, Sender="ai").order_by(
        models.LeadMessage.CreatedAt).all()[-1]
    assert any(s["step"] == "language_history" for s in stored.TraceJson["steps"])


def test_when_the_model_still_answers_in_another_language_the_voice_follows_the_text():
    script(LOGGED_REPLY)
    db, conv, call, out = live.turn(QUESTION, language="hi-IN")
    assert out.language == "pa-IN"                                # TTS is told Punjabi, matching what is spoken


def test_voice_flow_still_exports_the_language_helpers_callers_use():
    for name in ("detect_language", "language_note", "resolve_language", "same_language", "_LANGUAGES"):
        assert hasattr(voice_flow, name), name


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
