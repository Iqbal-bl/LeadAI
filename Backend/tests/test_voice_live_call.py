"""What a LIVE phone call does, reproducing what the first real test call exposed.

The first real call (a returning customer with 96 earlier messages) was greeted with a fresh sales
pitch, asked "did you schedule anything?", got no knowledge-base match, and was answered with
"connecting you now": the call was then HUNG UP, because nothing can transfer to a human. These
tests pin the fixes:

  * a hand-off on a live call flags staff and the call CARRIES ON (only the model's own
    end-of-conversation signal ends it);
  * a request for a person promises a callback (true) and does not claim a transfer (false);
  * a reply with an invented figure is withheld;
  * voice gets chat's memory (facts and summary once the thread outgrows the window);
  * a returning customer is opened with a follow-up line, a new one with the greeting;
  * the call's lines also reach the legacy transcript table, and an AI-ended call says so.

In-memory SQLite; only retrieval and the LLM are faked. Run: python tests/test_voice_live_call.py
"""
import sys
import types

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.config import settings as real_settings  # noqa: E402
from LeadAI.engine import bridge  # noqa: E402
from LeadAI.services import ai_engine, voice_flow  # noqa: E402
from LeadAI.voice.brain import BrainReply  # noqa: E402
from LeadAI.voice.session import CallSession  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass

LLM_CALLS = []


class _Settings:
    llm_enabled = True
    llm_qualification = False
    engine_mode = "off"
    engine_events = False

    def __getattr__(self, name):
        return getattr(real_settings, name)


class _Enforce(_Settings):
    engine_mode = "enforce"


def wire(reply="Rates start at 8.5 percent.", hits=True, score=0.8, chunk="Home loan interest rates start at 8.5% per annum. The fee is Rs. 25,000."):
    LLM_CALLS.clear()
    ai_engine.settings = _Settings()
    bridge.settings = _Settings()

    def fake_complete(system, messages, **kw):
        LLM_CALLS.append({"system": system, "messages": messages, **kw})
        return reply, {"model": "fake-model", "latency_ms": 5}

    ai_engine.llm.complete = fake_complete
    voice_flow.llm.complete = fake_complete
    ai_engine.llm.complete_json = lambda *a, **k: (None, {})
    ai_engine.vectorstore.search = lambda *a, **k: (
        [{"chunk_id": "k1", "document_id": "d", "score": score, "text": chunk}] if hits else [])
    ai_engine.vectorstore.idf_map = lambda *a, **k: ({}, 1.0)
    ai_engine._detect_product = lambda *a, **k: None
    ai_engine.company_thresholds = lambda db, client_id: (0.4, 5)


def setup(earlier_messages=0, display_name=None):
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="Customer #1", DisplayName=display_name)
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="voice")
    db.add(conv)
    db.flush()
    for i in range(earlier_messages):
        db.add(models.LeadMessage(ClientId=client.Id, ConversationId=conv.Id,
                                  Sender="customer" if i % 2 == 0 else "ai", Content=f"earlier message {i}"))
    call = models.LeadCall(ClientId=client.Id, ConversationId=conv.Id, CallSid="CA900", Provider="twilio",
                           Status="in-progress")
    db.add(call)
    db.commit()
    return db, client, conv, call


def turn(utterance, live=True, language=None, **setup_kw):
    db, client, conv, call = setup(**setup_kw)
    out = voice_flow.handle_voice_turn(db, client, conv, call, utterance, live_call=live, defer_scoring=True,
                                       commit=True, language=language)
    db.refresh(conv)
    db.refresh(call)
    return db, conv, call, out


# ------------------------------------------------------------------- hand-off semantics
def test_a_customer_asking_for_a_person_gets_a_true_callback_promise_and_the_call_continues():
    wire()
    db, conv, call, out = turn("I want to talk to a human")
    assert out.reply_text == voice_flow.CALLBACK_LINE and "call you back" in out.reply_text
    assert "connecting you now" not in out.reply_text.lower()
    assert out.ends_call is False and out.handed_off is True
    assert conv.Status == "needs_human" and call.HandedOff is True
    assert call.Status != "transferred"                  # nothing was transferred


def test_low_confidence_speaks_the_models_honest_answer_flags_staff_and_keeps_the_call_going():
    wire(reply="I don't have that detail with me, but a specialist will confirm it.", hits=False)
    db, conv, call, out = turn("did you schedule anything for me")
    assert out.reply_text.startswith("I don't have that detail")     # the model's answer, not a canned line
    assert out.ends_call is False                                    # this is what the first call got wrong
    assert conv.Status == "needs_human" and "below threshold" in conv.HandoffReason
    assert call.HandedOff is True and call.Status != "transferred"


def test_only_the_models_own_end_of_conversation_signal_ends_a_live_call():
    wire(reply="Thank you for your time, goodbye! [END_CALL]")
    db, conv, call, out = turn("no that's all, thanks")
    assert out.ends_call is True and "END_CALL" not in out.reply_text
    assert out.reply_text.startswith("Thank you for your time")          # spoken in full first
    # As in chat, a finished conversation also flags an advisor follow-up.
    assert conv.Status == "needs_human" and "advisor follow-up" in conv.HandoffReason


def test_a_reply_with_an_invented_figure_is_withheld_on_a_live_call():
    wire(reply="The processing fee is Rs. 15,000.")
    bridge.settings = _Enforce()
    db, conv, call, out = turn("what is the processing fee")
    assert out.reply_text == voice_flow.UNSURE_LINE and "15,000" not in out.reply_text
    assert out.ends_call is False and conv.Status == "needs_human"


def test_the_simulated_endpoint_keeps_its_original_transfer_behaviour():
    wire(hits=False)
    db, conv, call, out = turn("do you finance a private island", live=False)
    assert out.reply_text == voice_flow.TRANSFER_LINE and out.ends_call is True
    assert call.Status == "transferred"


# ------------------------------------------------------------------------------ memory
def test_a_long_call_thread_gets_the_same_memory_note_chat_gets():
    wire()
    db, client, conv, call = setup(earlier_messages=30, display_name="Ravi Kumar")
    db.add(models.Lead(ClientId=client.Id, ConversationId=conv.Id, Product="3 BHK", Budget="RS 90 LAKH",
                       FactsJson=["City: Mohali", "Family of four"]))
    conv.Summary = "Ravi is choosing between two 3 BHK flats and wants a site visit."
    db.commit()
    voice_flow.handle_voice_turn(db, client, conv, call, "did you schedule anything?",
                                 live_call=True, defer_scoring=True, commit=True)
    notes = " ".join(m["content"] for m in LLM_CALLS[-1]["messages"] if m["role"] == "system")
    assert "Conversation state" in notes
    assert "Ravi Kumar" in notes and "Mohali" in notes and "site visit" in notes


# ------------------------------------------------------------------------ opening line
def test_a_new_customer_gets_the_greeting():
    wire(reply="Hello! I'm Kabir from Kestrel Homes. What are you looking for?")
    db, client, conv, call = setup()
    text, message_id = voice_flow.opening_line(db, client, conv, call)
    msg = db.get(models.LeadMessage, message_id)
    assert text.startswith("Hello! I'm Kabir") and msg.Sender == "ai" and msg.CallSid == "CA900"
    assert msg.TraceJson["steps"][0]["decision"].startswith("call connected: new customer")


def test_a_returning_customer_gets_a_follow_up_line_that_knows_them():
    wire(reply="Hello Ravi, it's Kabir from Kestrel Homes, following up on your 3 BHK enquiry. How can I help?")
    db, client, conv, call = setup(earlier_messages=8, display_name="Ravi Kumar")
    text, message_id = voice_flow.opening_line(db, client, conv, call)
    sent = LLM_CALLS[-1]
    assert "following up" in text
    assert "The call has just connected" in sent["messages"][-1]["content"]      # the follow-up instruction
    assert any("returning customer" in m["content"] for m in sent["messages"] if m["role"] == "system")
    assert sent["profile"] == "voice"
    msg = db.get(models.LeadMessage, message_id)
    assert msg.TraceJson["steps"][0]["decision"].startswith("call connected: returning customer")


def test_the_returning_opener_falls_back_to_a_template_if_the_model_fails():
    wire(reply="")
    db, client, conv, call = setup(earlier_messages=4)
    text, _ = voice_flow.opening_line(db, client, conv, call)
    assert "Kestrel Homes" in text and "following up" in text


def test_a_paused_conversation_is_not_greeted():
    from LeadAI.engine import control

    wire()
    db, client, conv, call = setup()
    control.set_control(db, conv, "terminated", reason="bot loop", by="monitor")
    db.commit()
    assert voice_flow.opening_line(db, client, conv, call) == ("", None)


# ------------------------------------------------- legacy call screens and hangup reason
def _fake_legacy_modules():
    store, state = types.ModuleType("outbound.call_store"), types.ModuleType("outbound.call_state")
    store.rows = []
    store.save_transcript_message = lambda sid, role, text, phone=None: store.rows.append((sid, role, text, phone))
    state.call_hangup_reasons = {}
    saved = {k: sys.modules.get(k) for k in ("outbound.call_store", "outbound.call_state")}
    sys.modules["outbound.call_store"], sys.modules["outbound.call_state"] = store, state
    return store, state, saved


def _restore(saved):
    for key, mod in saved.items():
        if mod is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = mod


def test_each_spoken_line_also_reaches_the_legacy_transcript_table():
    import asyncio

    wire()
    db, client, conv, call = setup()
    store, state, saved = _fake_legacy_modules()
    try:
        session = CallSession(client_id=client.Id, conversation_id=conv.Id, call_sid="CA900",
                              session_factory=SessionLocalAdmin, phone_number="+919876543210")
        reply = asyncio.run(session.respond("what is the interest rate"))
        assert store.rows == [("CA900", "user", "what is the interest rate", "+919876543210"),
                              ("CA900", "agent", reply.text, "+919876543210")]
    finally:
        _restore(saved)


def test_a_broken_legacy_table_never_fails_a_turn():
    import asyncio

    wire()
    db, client, conv, call = setup()
    store, state, saved = _fake_legacy_modules()
    store.save_transcript_message = lambda *a, **k: 1 / 0
    try:
        session = CallSession(client_id=client.Id, conversation_id=conv.Id, call_sid="CA900",
                              session_factory=SessionLocalAdmin)
        assert asyncio.run(session.respond("what is the interest rate")).text
    finally:
        _restore(saved)



if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
