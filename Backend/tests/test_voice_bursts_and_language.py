"""Phone calls in real life: people talk in bursts, and in more than one language.

The second live call showed three problems, pinned here:

  1. BURSTS. The caller spoke one long thought in pieces. Three turns fired within four seconds;
     two replies were prepared, SAVED and scored, and never spoken (the caller had spoken again),
     so the model "remembered" saying things the caller never heard, and only the last fragment was
     answered. Now a superseded turn is discarded (nothing saved) and its words are merged into the
     next turn, which is answered once.
  2. LANGUAGE. A Hindi caller was answered in Punjabi. The model is now told the caller's
     language (speech-to-text reports it) and the voice is switched to match.
  3. HINDI RETRIEVAL. The retriever only extracts keywords from Latin letters, so a Hindi
     question had none, scored confidence ~0, and was flagged as a hand-off almost every turn.
     Non-Latin questions are translated to an English search query first.

Brain stage: Pipecat's harness. Everything else: in-memory SQLite, fake retrieval and LLM.
Run: python tests/test_voice_bursts_and_language.py
"""
import asyncio
import sys
import threading
import types

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from pipecat.frames.frames import (  # noqa: E402
    InterruptionFrame,
    LLMContextFrame,
    LLMTextFrame,
    TextFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402
from pipecat.transcriptions.language import Language  # noqa: E402
from pipecat_harness import SleepFrame, run_stage  # noqa: E402

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.config import settings as real_settings  # noqa: E402
from LeadAI.engine import bridge  # noqa: E402
from LeadAI.services import ai_engine, voice_flow  # noqa: E402
from LeadAI.voice import warmup  # noqa: E402
from LeadAI.voice.brain import BrainReply, LeadAIBrainProcessor  # noqa: E402
from LeadAI.voice.pipeline import LanguageTracker  # noqa: E402
from LeadAI.voice.session import CallSession  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def run(coro):
    return asyncio.run(coro)


def ctx(text):
    return LLMContextFrame(context=LLMContext([{"role": "user", "content": text}]))


def spoken(frames):
    return [f.text for f in frames if isinstance(f, LLMTextFrame)]


# ============================================================ 1. bursts: the brain stage
def _make_respond(first_delay=0.3):
    calls = []

    async def respond(text):
        calls.append(text)
        if len(calls) == 1:
            await asyncio.sleep(first_delay)          # the slow first reply, overtaken by more speech
        return BrainReply(text=f"ok: {text}", message_id=f"m{len(calls)}")

    return calls, respond


def test_a_thought_spoken_in_pieces_is_answered_once_with_all_of_it():
    calls, respond = _make_respond()
    superseded = []
    brain = LeadAIBrainProcessor(respond=respond, on_supersede=lambda: superseded.append(1))
    down, _ = run(run_stage(brain, [ctx("the site visit is on Saturday"), SleepFrame(sleep=0.05),
                                    UserStartedSpeakingFrame(), SleepFrame(sleep=0.05),
                                    ctx("at four in the evening")]))
    assert calls == ["the site visit is on Saturday", "the site visit is on Saturday at four in the evening"]
    assert spoken(down) == ["ok: the site visit is on Saturday at four in the evening"]   # ONE reply
    assert superseded == [1]                                     # the first turn was told to discard


def test_an_interruption_that_cancels_the_stage_still_carries_the_words_forward():
    # Pipecat cancels a stage that is mid-reply when the caller interrupts.
    calls, respond = _make_respond(first_delay=2.0)
    superseded = []
    brain = LeadAIBrainProcessor(respond=respond, on_supersede=lambda: superseded.append(1))
    down, _ = run(run_stage(brain, [ctx("part one"), SleepFrame(sleep=0.1), InterruptionFrame(),
                                    SleepFrame(sleep=0.1), ctx("part two")]))
    assert calls == ["part one", "part one part two"]
    assert spoken(down) == ["ok: part one part two"] and superseded == [1]


def test_a_turn_the_brain_says_was_superseded_is_never_spoken():
    async def respond(text):
        return BrainReply(superseded=True)

    down, _ = run(run_stage(LeadAIBrainProcessor(respond=respond), [ctx("hello")]))
    assert spoken(down) == []


def test_speech_switches_to_the_language_of_the_reply_only_when_it_changes():
    replies = iter([("Namaste", "hi-IN"), ("Aur batayein", "hi-IN"), ("Sure thing", "en-IN")])

    async def respond(text):
        reply, lang = next(replies)
        return BrainReply(text=reply, language=lang)

    brain = LeadAIBrainProcessor(respond=respond, language_frame=lambda code: TextFrame(text=f"LANG:{code}"))
    down, _ = run(run_stage(brain, [ctx("a"), SleepFrame(sleep=0.3), ctx("b"), SleepFrame(sleep=0.3), ctx("c")]))
    markers = [f.text for f in down if isinstance(f, TextFrame) and not isinstance(f, LLMTextFrame)]
    assert markers == ["LANG:hi-IN", "LANG:en-IN"]           # not repeated for the second Hindi reply
    assert spoken(down) == ["Namaste", "Aur batayein", "Sure thing"]


def test_the_language_tracker_reports_each_transcripts_language_and_forwards_the_frame():
    heard = []
    frames = [TranscriptionFrame(text="namaste", user_id="u", timestamp="t", language=Language.HI_IN)]
    down, _ = run(run_stage(LanguageTracker(on_language=heard.append), frames))
    assert heard == ["hi-IN"] and [f.text for f in down if isinstance(f, TranscriptionFrame)] == ["namaste"]


# ================================================================ database-backed pieces
LLM_CALLS = []
SEARCHED = []


class _Settings:
    llm_enabled = True
    llm_qualification = False
    engine_mode = "off"
    engine_events = False

    def __getattr__(self, name):
        return getattr(real_settings, name)


def wire(reply="Rates start at 8.5 percent.", translation=None, chunk="Two BHK flats in Mohali start at Rs 60 lakh."):
    LLM_CALLS.clear()
    SEARCHED.clear()
    ai_engine.settings = _Settings()
    bridge.settings = _Settings()

    def fake_complete(system, messages, **kw):
        LLM_CALLS.append({"system": system, "messages": messages, **kw})
        if "Translate" in system:
            return translation, {"model": "fake", "latency_ms": 3}
        return reply, {"model": "fake-model", "latency_ms": 5}

    ai_engine.llm.complete = fake_complete
    ai_engine.llm.complete_json = lambda *a, **k: (None, {})

    def fake_search(db, client_id, query, top_k=5, **kw):
        SEARCHED.append(query)
        return [{"chunk_id": "k1", "document_id": "d", "score": 0.8, "text": chunk}]

    ai_engine.vectorstore.search = fake_search
    ai_engine.vectorstore.idf_map = lambda *a, **k: ({}, 1.0)
    ai_engine._detect_product = lambda *a, **k: None
    ai_engine.company_thresholds = lambda db, client_id: (0.4, 5)


def setup():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="Customer #1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="voice")
    db.add(conv)
    db.flush()
    call = models.LeadCall(ClientId=client.Id, ConversationId=conv.Id, CallSid="CA910", Provider="twilio",
                           Status="in-progress")
    db.add(call)
    db.commit()
    return db, client, conv, call


def turn(utterance, **kw):
    db, client, conv, call = setup()
    out = voice_flow.handle_voice_turn(db, client, conv, call, utterance, live_call=True,
                                       defer_scoring=True, commit=True, **kw)
    db.refresh(conv)
    return db, conv, out


# ---------------------------------------------------------------- 1b. nothing saved
def test_a_superseded_turn_saves_nothing_at_all():
    wire()
    db, client, conv, call = setup()
    out = voice_flow.handle_voice_turn(db, client, conv, call, "the site visit is on Saturday",
                                       live_call=True, defer_scoring=True, commit=True,
                                       superseded=lambda: True)
    assert out.superseded and out.reply_text == ""
    assert db.query(models.LeadMessage).filter_by(ConversationId=conv.Id).count() == 0   # no inbound, no reply
    db.refresh(conv)
    assert conv.Status == "open" and not conv.MessageCount


def test_a_turn_that_is_not_superseded_saves_normally():
    wire()
    db, conv, out = turn("what is the rate", superseded=lambda: False)
    assert not out.superseded and out.reply_text
    assert db.query(models.LeadMessage).filter_by(ConversationId=conv.Id).count() == 2


def test_the_session_discards_a_turn_when_the_brain_supersedes_it():
    wire()
    db, client, conv, call = setup()
    session = CallSession(client_id=client.Id, conversation_id=conv.Id, call_sid="CA910",
                          session_factory=SessionLocalAdmin)
    token = threading.Event()
    token.set()                                                # the caller already spoke again
    reply = session._respond_sync("half a thought", token)
    assert reply.superseded and reply.text == ""
    assert db.query(models.LeadMessage).filter_by(ConversationId=conv.Id).count() == 0

    session._respond_sync("a whole thought", threading.Event())         # an unset token: saved as usual
    assert db.query(models.LeadMessage).filter_by(ConversationId=conv.Id).count() == 2


def test_supersede_sets_only_the_turn_in_flight():
    wire()
    db, client, conv, call = setup()
    session = CallSession(client_id=client.Id, conversation_id=conv.Id, call_sid="CA910",
                          session_factory=SessionLocalAdmin)
    session.supersede()                                        # nothing in flight: harmless
    asyncio.run(session.respond("what is the rate"))
    assert session._token is not None and not session._token.is_set()   # the finished turn was not cancelled
    session.supersede()
    assert session._token.is_set()


# ------------------------------------------------------------------------ 2. language
def test_the_model_is_told_the_callers_language_and_script():
    wire()
    turn("मुझे रेट बताइए", language="hi-IN")
    system = " ".join(m["content"] for m in LLM_CALLS[-1]["messages"] if m["role"] == "system")
    assert "The caller is speaking Hindi" in system and "Devanagari" in system


def test_language_notes_for_each_language_and_for_unknown():
    assert "Punjabi" in voice_flow.language_note("pa-IN") and "Gurmukhi" in voice_flow.language_note("pa-IN")
    assert voice_flow.language_note("en-IN").startswith("The caller is speaking English")
    assert voice_flow.language_note("hi") and voice_flow.language_note("HI-in")
    assert voice_flow.language_note("unknown") == "" and voice_flow.language_note(None) == ""


def test_the_session_passes_the_tracked_language_to_the_brain_and_back_on_the_reply():
    wire()
    db, client, conv, call = setup()
    session = CallSession(client_id=client.Id, conversation_id=conv.Id, call_sid="CA910",
                          session_factory=SessionLocalAdmin)
    session.set_language("hi-IN")
    session.set_language(None)                                 # an empty report never erases it
    reply = asyncio.run(session.respond("मुझे रेट बताइए"))
    assert reply.language == "hi-IN"
    assert any("speaking Hindi" in m["content"] for m in LLM_CALLS[-1]["messages"] if m["role"] == "system")


# --------------------------------------------------------------- 3. Hindi retrieval
def test_a_hindi_question_is_searched_and_scored_in_english():
    wire(translation="What is the price of a 2 BHK flat in Mohali?")
    db, conv, out = turn("मोहाली में दो बीएचके फ्लैट की कीमत क्या है", language="hi-IN")
    assert SEARCHED == ["What is the price of a 2 BHK flat in Mohali?"]        # the English query went to retrieval
    # The model still sees the customer's OWN words, not just the translation.
    assert "मोहाली" in LLM_CALLS[-1]["messages"][-1]["content"]
    assert out.result["confidence"] > 0.4 and not out.handed_off                # scored on the English keywords


def test_a_hindi_request_for_a_person_is_recognised():
    wire(translation="I want to talk to a human agent")
    db, conv, out = turn("मुझे किसी इंसान से बात करनी है", language="hi-IN")
    assert out.reply_text == voice_flow.CALLBACK_LINE and conv.Status == "needs_human"


def test_english_questions_cost_no_translation_call():
    wire()
    turn("what is the price of a two bhk", language="en-IN")
    assert not any("Translate" in c["system"] for c in LLM_CALLS)


def test_if_translation_fails_the_original_words_are_used():
    wire(translation=None)
    turn("मोहाली में कीमत क्या है", language="hi-IN")
    assert SEARCHED == ["मोहाली में कीमत क्या है"]


def test_translation_is_traced_without_the_words():
    wire(translation="price of a flat")
    db, conv, out = turn("मोहाली में कीमत क्या है", language="hi-IN")
    steps = {s["step"]: s for s in out.trace.steps}
    assert steps["query_translation"]["decision"] == "translated for retrieval"
    assert "मोहाली" not in str(out.trace.steps) and "price of a flat" not in str(out.trace.steps)


# ------------------------------------------------------------------------ warm-up
def test_warmup_opens_the_connection_and_runs_one_retrieval_and_never_raises():
    seen = []
    saved = (warmup.gateway.warm_connection, ai_engine.vectorstore.search)
    warmup.gateway.warm_connection = lambda: seen.append("connection")
    ai_engine.vectorstore.search = lambda db, client_id, query, top_k=5, **k: seen.append(("search", client_id)) or []
    try:
        warmup.warm(None)                                       # no call context: only the connection
        warmup.warm({"client_id": "c1"})
        warmup.warm({"client_id": "c1", "x": 1})
    finally:
        warmup.gateway.warm_connection, ai_engine.vectorstore.search = saved
    assert seen.count("connection") == 3 and ("search", "c1") in seen


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
