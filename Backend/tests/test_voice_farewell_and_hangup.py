"""The ending of the second live call: a goodbye the caller never heard, blamed on the wrong side.

The log showed:
  16:43:17.7  the caller says "Okay, bye."
  16:43:20.5  Smart Turn judged it unfinished and gave up after its 3 s fallback silence
  16:43:23.4  the brain finished a knowledge-base lookup and a model call (2.9 s) for a goodbye
  16:43:23.56 Twilio: call completed. The CALLER had already hung up.
  ...         and the hangup reason said "AI ended the call", because it was written when the AI
              decided to end the call, before anyone knew who would hang up first.

Fixes pinned here: a short goodbye on a live call gets an instant fixed closing line (no retrieval,
no model call); the hangup reason is written only when the AI really hangs up, right before it does;
Smart Turn's fallback silence is 1.5 s, not 3 s.

Run: python tests/test_voice_farewell_and_hangup.py
"""
import asyncio
import sys
import types

import conftest_stub  # noqa: F401

import test_voice_live_call as live  # noqa: E402  (its wiring helpers; also installs the fakes)
from LeadAI import models  # noqa: E402
from LeadAI.engine import control  # noqa: E402
from LeadAI.services import ai_engine, voice_flow  # noqa: E402
from LeadAI.voice import pipeline  # noqa: E402
from LeadAI.voice.brain import BrainReply  # noqa: E402
from LeadAI.voice.session import CallSession  # noqa: E402

SEARCHES = []


def wire_counting():
    live.wire()
    SEARCHES.clear()
    inner = ai_engine.vectorstore.search

    def counting_search(*a, **k):
        SEARCHES.append(1)
        return inner(*a, **k)

    ai_engine.vectorstore.search = counting_search


# ------------------------------------------------------------------ what counts as a goodbye
def test_short_goodbyes_are_recognised_in_english_and_hindi():
    for text in ("Okay, bye.", "Okay, done.", "Thanks, that's all", "धन्यवाद, बाय", "Have a great day",
                 "Ok bye bye", "That's it.", "ਧੰਨਵਾਦ", "talk to you later"):
        assert voice_flow.is_farewell(text), text


def test_things_that_only_look_like_goodbyes_are_not():
    for text in ("Good day.", "thanks", "thank you", "okay", "Okay, bye, but what is the price?",
                 "that's all the flats you have?", "that's all the flats you have",
                 "bye the way what is the price of a 3 BHK in Mohali", "what is the price", ""):
        assert not voice_flow.is_farewell(text), text


# ------------------------------------------------------------------- the live-call fast path
def test_a_goodbye_gets_an_instant_closing_line_with_no_lookup_and_no_model_call():
    wire_counting()
    db, conv, call, out = live.turn("Okay, bye.")
    assert out.reply_text == "Thank you for your time. Have a great day!"
    assert out.ends_call is True and out.handed_off is False
    assert SEARCHES == [] and live.LLM_CALLS == []                # the whole point: nothing slow
    assert conv.Status == "open"                                  # a goodbye is not a hand-off
    msgs = db.query(models.LeadMessage).filter_by(ConversationId=conv.Id).order_by(models.LeadMessage.CreatedAt).all()
    assert [m.Sender for m in msgs] == ["customer", "ai"] and msgs[1].ModelUsed == "closing-template"
    assert [s["step"] for s in msgs[1].TraceJson["steps"] if s["step"] == "farewell"] == ["farewell"]
    assert out.outbound_id == msgs[1].Id                          # the lead is still scored afterwards


def test_the_closing_line_is_in_the_callers_language():
    wire_counting()
    assert live.turn("धन्यवाद, बाय", language="hi-IN")[3].reply_text == "धन्यवाद! आपका दिन शुभ हो।"
    assert live.turn("ਧੰਨਵਾਦ", language="pa-IN")[3].reply_text.startswith("ਧੰਨਵਾਦ")
    assert live.turn("bye", language="ta-IN")[3].reply_text.startswith("Thank you")    # no line for Tamil: English


def test_a_question_that_contains_bye_is_answered_normally():
    wire_counting()
    db, conv, call, out = live.turn("Okay, bye, but what is the price?")
    assert SEARCHES and live.LLM_CALLS                            # went through the real brain
    assert out.reply_text != "Thank you for your time. Have a great day!"


def test_the_simulated_endpoint_is_unchanged_a_goodbye_still_goes_through_the_brain():
    wire_counting()
    db, conv, call, out = live.turn("Okay, bye.", live=False)
    assert SEARCHES and live.LLM_CALLS


def test_a_paused_conversation_stays_silent_even_for_a_goodbye():
    wire_counting()
    db, client, conv, call = live.setup()
    control.set_control(db, conv, "paused", reason="review", by="monitor")
    db.commit()
    out = voice_flow.handle_voice_turn(db, client, conv, call, "bye", live_call=True, defer_scoring=True, commit=True)
    assert out.skipped and out.reply_text == ""


# ------------------------------------------------------------------- who hung up
def _fake_outbound(status):
    app, state = types.ModuleType("outbound.app"), types.ModuleType("outbound.call_state")
    app.active_calls = {"CA900": {"status": status}}
    state.call_hangup_reasons = {}
    saved = {k: sys.modules.get(k) for k in ("outbound.app", "outbound.call_state")}
    sys.modules["outbound.app"], sys.modules["outbound.call_state"] = app, state
    return state, saved


def _restore(saved):
    for key, mod in saved.items():
        if mod is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = mod


def _session():
    db, client, conv, call = live.setup()
    return CallSession(client_id=client.Id, conversation_id=conv.Id, call_sid="CA900",
                       session_factory=live.SessionLocalAdmin)


def test_deciding_to_end_the_call_writes_nothing_by_itself():
    state, saved = _fake_outbound("in-progress")
    try:
        session = _session()
        session.after_reply(BrainReply(text="bye", ends_call=True))
        assert state.call_hangup_reasons == {}                    # the first fix wrote it here: too early
    finally:
        _restore(saved)


def test_the_reason_is_written_when_the_ai_really_hangs_up_a_live_call():
    state, saved = _fake_outbound("in-progress")
    try:
        session = _session()
        session.after_reply(BrainReply(text="bye", ends_call=True))
        session.note_ai_hangup()
        assert state.call_hangup_reasons["CA900"] == "AI ended the call (end of conversation)"
        session.after_reply(BrainReply(text="", skipped=True))
        session.note_ai_hangup()
        assert "paused or terminated" in state.call_hangup_reasons["CA900"]
    finally:
        _restore(saved)


def test_if_the_caller_hung_up_first_the_ai_is_not_blamed():
    for terminal in ("completed", "no-answer", "canceled"):
        state, saved = _fake_outbound(terminal)                   # the status webhook already said so
        try:
            session = _session()
            session.after_reply(BrainReply(text="Thank you, goodbye", ends_call=True))
            session.note_ai_hangup()
            assert state.call_hangup_reasons == {}                # left to say "Caller hung up"
        finally:
            _restore(saved)


def test_a_hangup_the_ai_did_not_decide_is_never_labelled_as_the_ais():
    state, saved = _fake_outbound("in-progress")
    try:
        session = _session()
        session.after_reply(BrainReply(text="Rates start at 8.5 percent."))   # a normal reply
        session.note_ai_hangup()                                              # e.g. the line dropped
        assert state.call_hangup_reasons == {}
    finally:
        _restore(saved)


def test_the_serializer_reports_the_moment_it_hangs_up_before_sending_the_request():
    from pipecat.serializers.twilio import TwilioFrameSerializer

    order = []

    async def fake_parent_hangup(self):
        order.append("request sent")

    saved = TwilioFrameSerializer._hang_up_call
    TwilioFrameSerializer._hang_up_call = fake_parent_hangup
    try:
        cls = pipeline._hangup_aware_serializer()
        serializer = cls(stream_sid="MZ1", call_sid="CA1", account_sid="AC1", auth_token="t",
                         on_ai_hangup=lambda: order.append("reason recorded"))
        asyncio.run(serializer._hang_up_call())
        assert order == ["reason recorded", "request sent"]

        order.clear()
        broken = cls(stream_sid="MZ1", call_sid="CA1", account_sid="AC1", auth_token="t",
                     on_ai_hangup=lambda: 1 / 0)                  # bookkeeping must never stop the hang-up
        asyncio.run(broken._hang_up_call())
        assert order == ["request sent"]
    finally:
        TwilioFrameSerializer._hang_up_call = saved


# --------------------------------------------------------------- end-of-turn patience
def test_smart_turns_fallback_silence_is_1_5_seconds_not_3():
    from pipecat.frames.frames import Frame  # noqa: F401
    from pipecat.processors.frame_processor import FrameProcessor

    class Pass(FrameProcessor):
        pass

    class Session:
        async def respond(self, t):
            return BrainReply()

        def after_reply(self, r):
            pass

        def set_language(self, c):
            pass

        def supersede(self):
            pass

    _pipe, _brain, aggs = pipeline.assemble(
        transport_in=Pass(), transport_out=Pass(),
        services=pipeline.Services(stt=Pass(), tts=Pass()), session=Session())
    analyzer = aggs.user()._params.user_turn_strategies.stop[0]._turn_analyzer
    assert pipeline.SMART_TURN_STOP_SECONDS == 1.5 and analyzer._params.stop_secs == 1.5


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
