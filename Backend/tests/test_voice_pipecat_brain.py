"""The Pipecat brain stage and the per-call session, without any phone audio.

  * The brain stage turns "the caller finished speaking" (an LLMContextFrame from Pipecat's
    aggregator) into the frames an LLM service would emit, and never lets a failure end the call.
  * The call session runs the shared brain in worker threads with their own DB sessions, and
    scores the lead only AFTER the reply, in order.

Uses Pipecat's own test harness for the stage, and in-memory SQLite (only retrieval and the LLM
are faked) for the session. Run: python tests/test_voice_pipecat_brain.py
"""
import asyncio

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from pipecat.frames.frames import (  # noqa: E402
    EndWorkerFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402
from pipecat_harness import SleepFrame, run_stage  # noqa: E402

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.config import settings as real_settings  # noqa: E402
from LeadAI.engine import control  # noqa: E402
from LeadAI.services import ai_engine  # noqa: E402
from LeadAI.voice.brain import APOLOGY, BrainReply, LeadAIBrainProcessor, last_user_text  # noqa: E402
from LeadAI.voice.session import CallSession  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def run(coro):
    return asyncio.run(coro)


def ctx(text="what is the rate", speculation=False):
    context = LLMContext([{"role": "system", "content": "be brief"}, {"role": "user", "content": text}])
    return LLMContextFrame(context=context, speculation=speculation)


def texts(frames):
    return [f.text for f in frames if isinstance(f, LLMTextFrame)]


# ------------------------------------------------------------------ the brain stage
def test_the_callers_words_come_from_the_latest_user_message():
    context = LLMContext([{"role": "user", "content": "first"}, {"role": "assistant", "content": "ok"},
                          {"role": "user", "content": [{"type": "text", "text": "second"}]}])
    assert last_user_text(context) == "second"
    assert last_user_text(LLMContext([])) == ""


def test_a_finished_turn_becomes_the_frames_an_llm_service_would_emit():
    seen = []

    async def respond(text):
        seen.append(text)
        return BrainReply(text="Rates start at 8.5 percent.")

    down, _ = run(run_stage(LeadAIBrainProcessor(respond=respond), [ctx("what is the rate")]))
    assert seen == ["what is the rate"]
    kinds = [type(f).__name__ for f in down if type(f).__name__.startswith("LLM")]
    assert kinds == ["LLMFullResponseStartFrame", "LLMTextFrame", "LLMFullResponseEndFrame"]
    assert texts(down) == ["Rates start at 8.5 percent."]
    assert not any(isinstance(f, LLMContextFrame) for f in down)        # consumed, like an LLM service


def test_a_handoff_speaks_first_then_ends_the_call():
    async def respond(text):
        return BrainReply(text="Connecting you now.", ends_call=True)

    down, _ = run(run_stage(LeadAIBrainProcessor(respond=respond), [ctx()], send_end_frame=False))
    # The line is spoken in full BEFORE the end-of-call frame, so the caller hears it.
    assert [type(f).__name__ for f in down] == [
        "LLMFullResponseStartFrame", "LLMTextFrame", "LLMFullResponseEndFrame", "EndWorkerFrame"]
    assert texts(down) == ["Connecting you now."]


def test_provisional_and_empty_turns_are_never_answered():
    calls = []

    async def respond(text):
        calls.append(text)
        return BrainReply(text="x")

    down, _ = run(run_stage(LeadAIBrainProcessor(respond=respond),
                           [ctx(speculation=True), ctx(text="   ")]))
    assert calls == [] and texts(down) == []


def test_a_failure_in_the_brain_becomes_a_spoken_apology_not_a_dead_call():
    async def respond(text):
        raise RuntimeError("database down")

    down, _ = run(run_stage(LeadAIBrainProcessor(respond=respond), [ctx()]))
    assert texts(down) == [APOLOGY]


def test_a_reply_prepared_while_the_caller_starts_talking_again_is_dropped():
    async def slow_respond(text):
        await asyncio.sleep(0.3)
        return BrainReply(text="stale answer to half a sentence")

    down, _ = run(run_stage(
        LeadAIBrainProcessor(respond=slow_respond),
        [ctx(), SleepFrame(sleep=0.05), UserStartedSpeakingFrame()],
    ))
    assert texts(down) == []


def test_the_after_reply_hook_runs_once_per_spoken_reply():
    hooked = []

    async def respond(text):
        return BrainReply(text="hi", message_id="m1")

    run(run_stage(LeadAIBrainProcessor(respond=respond, after_reply=hooked.append), [ctx()]))
    assert [r.message_id for r in hooked] == ["m1"]


# ------------------------------------------------------------------- the call session
class _Settings:
    llm_enabled = True
    llm_qualification = False
    engine_mode = "off"
    engine_events = False

    def __getattr__(self, name):
        return getattr(real_settings, name)


def wire():
    ai_engine.settings = _Settings()
    ai_engine.llm.complete = lambda *a, **k: ("Rates start at 8.5 percent.", {"model": "fake", "latency_ms": 4})
    ai_engine.llm.complete_json = lambda *a, **k: (None, {})
    ai_engine.vectorstore.search = lambda *a, **k: [
        {"chunk_id": "k1", "document_id": "d", "score": 0.8, "text": "Home loan interest rates start at 8.5% per annum."}]
    ai_engine.vectorstore.idf_map = lambda *a, **k: ({}, 1.0)
    ai_engine._detect_product = lambda *a, **k: None
    ai_engine.company_thresholds = lambda db, client_id: (0.4, 5)


def make_call():
    wire()
    db = SessionLocalAdmin()
    client = Client(Name="Nexa Finserv")
    db.add(client)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="Customer #1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="voice")
    db.add(conv)
    db.flush()
    call = models.LeadCall(ClientId=client.Id, ConversationId=conv.Id, CallSid="CA777", Provider="twilio",
                           Status="in_progress")
    db.add(call)
    db.commit()
    session = CallSession(client_id=client.Id, conversation_id=conv.Id, call_sid="CA777",
                          session_factory=SessionLocalAdmin)
    return db, conv, session


def test_a_turn_replies_and_stores_both_sides_but_does_not_score_yet():
    db, conv, session = make_call()
    reply = run(session.respond("what is the interest rate"))
    assert reply.text == "Rates start at 8.5 percent." and not reply.ends_call and reply.message_id
    msgs = db.query(models.LeadMessage).filter_by(ConversationId=conv.Id).order_by(models.LeadMessage.CreatedAt).all()
    assert [m.Sender for m in msgs] == ["customer", "ai"] and msgs[1].Id == reply.message_id
    assert db.query(models.Lead).filter_by(ConversationId=conv.Id).count() == 0      # scoring deferred


def test_scoring_runs_after_the_reply_in_order_and_joins_the_decision_trace():
    db, conv, session = make_call()

    async def scenario():
        session.start()
        first = await session.respond("what is the interest rate")
        session.schedule_scoring(first)
        second = await session.respond("and the processing fee")
        session.schedule_scoring(second)
        await session.close()                       # drains the queue, in order
        return first, second

    first, second = run(scenario())
    db.expire_all()
    lead = db.query(models.Lead).filter_by(ConversationId=conv.Id).one()
    assert lead.Score > 0 and db.get(models.LeadConversation, conv.Id).Summary
    steps = [s["step"] for s in db.get(models.LeadMessage, second.message_id).TraceJson["steps"]]
    assert "scoring" in steps and "post_turn" in steps and "qualify" in steps   # one record, both phases


def test_a_handoff_on_a_live_call_flags_staff_but_does_not_hang_up():
    # The first real call was hung up on when nothing matched the knowledge base. Nothing can
    # transfer to a human yet, so the call carries on and staff are flagged.
    db, conv, session = make_call()
    ai_engine.vectorstore.search = lambda *a, **k: []          # nothing known: low confidence
    reply = run(session.respond("do you finance a private island"))
    assert reply.text and not reply.ends_call and "connecting you now" not in reply.text
    db.expire_all()
    assert db.get(models.LeadConversation, conv.Id).Status == "needs_human"


def test_the_opening_greeting_is_spoken_and_stored():
    db, conv, session = make_call()
    reply = run(session.opening())
    assert reply.text and not reply.skipped
    msg = db.get(models.LeadMessage, reply.message_id)
    assert msg.Sender == "ai" and msg.CallSid == "CA777" and msg.TraceJson["steps"][0]["step"] == "opening"


def test_a_terminated_conversation_says_nothing_and_ends_the_call():
    db, conv, session = make_call()
    control.set_control(db, conv, "terminated", reason="bot loop", by="monitor")
    db.commit()
    reply = run(session.respond("hello?"))
    assert reply.skipped and reply.ends_call and reply.text == ""
    assert run(session.opening()).skipped


def test_a_call_with_no_leadai_conversation_fails_loudly():
    db, conv, session = make_call()
    orphan = CallSession(client_id=session.client_id, conversation_id=conv.Id, call_sid="CA-UNKNOWN",
                         session_factory=SessionLocalAdmin)
    try:
        run(orphan.respond("hi"))
    except LookupError:
        return
    raise AssertionError("expected LookupError")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
