"""Decision trace: every step is recorded with its reason, and never with message text.

Unit tests for the collector, then real turns through conversation_flow on in-memory SQLite
(only the network is faked). Run: python tests/test_engine_trace.py
"""
import json
import logging

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.config import settings as real_settings  # noqa: E402
from LeadAI.engine import trace as trace_mod  # noqa: E402
from LeadAI.engine.trace import TurnTrace  # noqa: E402
from LeadAI.services import ai_engine, conversation_flow  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


class _Settings:
    llm_enabled = True
    llm_qualification = False
    engine_mode = "off"
    engine_events = False
    engine_trace_log = True
    engine_trace_store = True

    def __getattr__(self, name):
        return getattr(real_settings, name)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def capture_trace_logs():
    handler = _Capture()
    lg = logging.getLogger("leadai.trace")
    lg.setLevel(logging.INFO)
    lg.addHandler(handler)
    return lg, handler


# ------------------------------------------------------------------ the collector
def _t(**kw):
    return TurnTrace(conversation_id="abcdef123456", client_id="c1", channel="web", **kw)


def test_a_step_records_decision_evidence_and_timing():
    t = _t()
    t.step("retrieve", "3 chunks", top_score=0.71234567, chunk_ids=["a", "b"], meets=True)
    doc = t.as_json()
    step = doc["steps"][0]
    assert step["step"] == "retrieve" and step["decision"] == "3 chunks" and step["ms"] >= 0
    assert step["detail"] == {"top_score": 0.7123, "chunk_ids": ["a", "b"], "meets": True}
    assert doc["v"] == 1 and doc["channel"] == "web" and doc["total_ms"] >= 0
    json.dumps(doc)                                     # must be storable as JSON


def test_content_keys_are_dropped_so_message_text_cannot_leak():
    t = _t()
    t.step("x", "y", text="my number is 9876543210", reply="secret", question="q", content="c",
           message="m", prompt="p", transcript="t", safe=1)
    assert t.steps[0]["detail"] == {"safe": 1}


def test_values_are_bounded():
    t = _t()
    t.step("x", "y", long="z" * 1000, many=list(range(50)), nested={"k": "v" * 500})
    d = t.steps[0]["detail"]
    assert len(d["long"]) <= trace_mod.MAX_STR
    assert len(d["many"]) == trace_mod.MAX_LIST + 1 and d["many"][-1].startswith("+")
    assert len(d["nested"]["k"]) <= trace_mod.MAX_STR


def test_each_step_is_logged_as_key_value_on_its_own_logger():
    lg, handler = capture_trace_logs()
    try:
        t = _t()
        t.step("confidence", "0.62", threshold=0.4, note="two words")
    finally:
        lg.removeHandler(handler)
    line = handler.lines[0]
    assert line.startswith("[LeadAI trace] t=") and "conv=abcdef12" in line and "ch=web" in line
    assert "step=confidence" in line and "decision=0.62" in line and "threshold=0.4" in line
    assert 'note="two words"' in line


def test_log_and_store_can_be_switched_off_independently():
    saved = trace_mod.settings

    class Off(_Settings):
        engine_trace_log = False
        engine_trace_store = False

    trace_mod.settings = Off()
    lg, handler = capture_trace_logs()
    try:
        t = _t()
        t.step("a", "b")
        assert t.as_json() is None and handler.lines == [] and len(t.steps) == 1
    finally:
        lg.removeHandler(handler)
        trace_mod.settings = saved


def test_a_trace_never_raises():
    class Weird:
        def __str__(self):
            raise RuntimeError("boom")

    t = _t()
    t.step("x", "y", weird=Weird())          # unprintable value
    t.step("x", "y")                         # and it keeps working afterwards
    trace_mod.step(None, "x", "y")           # the optional helper tolerates no trace


def test_step_count_is_capped():
    t = _t()
    for i in range(trace_mod.MAX_STEPS + 20):
        t.step("s", str(i))
    assert len(t.steps) == trace_mod.MAX_STEPS


# ------------------------------------------------------------- real turns, end to end
CTX = "Home loan interest rates start at 8.5% per annum."


def wire(llm_reply="Rates start at 8.5%.", llm_on=True, score=0.8):
    ai_engine.settings = _Settings()
    ai_engine.settings.llm_enabled = llm_on
    ai_engine.llm.complete = lambda *a, **k: (
        (llm_reply, {"model": "fake-model", "latency_ms": 7, "prompt_tokens": 50,
                     "completion_tokens": 9, "attempts": 1})
        if llm_reply is not None else (None, {"model": "fake-model", "latency_ms": 3, "error": "503"})
    )
    ai_engine.llm.complete_json = lambda *a, **k: (None, {})
    ai_engine.vectorstore.search = lambda *a, **k: (
        [{"chunk_id": "k1", "document_id": "d", "score": score, "text": CTX}] if score else [])
    ai_engine.vectorstore.idf_map = lambda *a, **k: ({}, 1.0)
    ai_engine._detect_product = lambda *a, **k: None
    ai_engine.company_thresholds = lambda db, client_id: (0.4, 5)


def setup():
    db = SessionLocalAdmin()
    client = Client(Name="Nexa Finserv")
    db.add(client)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="Customer #1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="web")
    db.add(conv)
    db.commit()
    return db, client, conv


def turn(text, **wire_kw):
    wire(**wire_kw)
    db, client, conv = setup()
    conversation_flow.handle_customer_turn(db, client, conv, text)
    db.refresh(conv)
    ai = db.query(models.LeadMessage).filter_by(ConversationId=conv.Id, Sender="ai").one()
    return db, conv, ai, {s["step"]: s for s in ai.TraceJson["steps"]}


def names(ai):
    return [s["step"] for s in ai.TraceJson["steps"]]


def test_a_normal_turn_records_every_decision_in_order():
    db, conv, ai, steps = turn("what is the interest rate on a home loan")
    order = names(ai)
    expected = ["receive", "memory", "phone_capture", "state_note", "thresholds", "human_request",
                "retrieve", "confidence", "prompt", "generate", "answer_decision", "engine",
                "qualify", "summarize", "threshold", "handoff", "commit"]
    positions = [order.index(n) for n in expected]
    assert positions == sorted(positions), order
    assert steps["retrieve"]["detail"]["chunk_ids"] == ["k1"]
    assert steps["confidence"]["detail"]["threshold"] == 0.4 and steps["confidence"]["detail"]["meets_threshold"]
    assert steps["generate"]["decision"] == "llm reply" and steps["generate"]["detail"]["model"] == "fake-model"
    assert steps["generate"]["detail"]["prompt_tokens"] == 50
    assert steps["answer_decision"]["decision"] == "answer"
    assert steps["handoff"]["decision"] == "none needed"
    assert steps["qualify"]["detail"]["analysis"].startswith("keyword rules")
    assert ai.TraceJson["total_ms"] >= 0 and ai.TraceJson["turn_id"] is None


def test_low_confidence_records_why_it_handed_off():
    db, conv, ai, steps = turn("do you finance a private island", score=0.0)
    decision = steps["answer_decision"]
    assert decision["decision"] == "hand off to human"
    assert decision["detail"]["low_confidence"] is True and decision["detail"]["wants_human"] is False
    assert steps["handoff"]["decision"].startswith("raised") and conv.Status == "needs_human"


def test_a_customer_asking_for_a_human_is_recorded_as_such():
    db, conv, ai, steps = turn("I want to talk to a human")
    assert steps["human_request"]["decision"] == "customer asked for a human"
    assert steps["generate"]["detail"]["reason"] == "human_requested"


def test_a_failed_model_call_is_traced_and_the_message_is_labelled_correctly():
    db, conv, ai, steps = turn("what is the rate", llm_reply=None)
    assert steps["generate"]["decision"] == "extractive fallback"
    assert steps["generate"]["detail"]["reason"] == "llm_call_failed"
    assert ai.ModelUsed == "builtin-extractive"          # used to keep the failed call's model name


def test_llm_off_is_traced_with_its_reason():
    db, conv, ai, steps = turn("what is the rate", llm_on=False)
    assert steps["generate"]["detail"]["reason"] == "llm_disabled"


def test_greeting_short_circuit_is_recorded():
    db, conv, ai, steps = turn("hi")
    assert steps["greeting"]["decision"].startswith("short-circuit") and "retrieve" not in steps


def test_a_stopped_conversation_records_why_the_ai_stayed_silent():
    from LeadAI.engine import control

    wire()
    db, client, conv = setup()
    control.set_control(db, conv, "terminated", reason="bot loop", by="monitor")
    db.commit()
    conversation_flow.handle_customer_turn(db, client, conv, "hello again")
    msg = db.query(models.LeadMessage).filter_by(ConversationId=conv.Id, Sender="customer").one()
    steps = {s["step"]: s for s in msg.TraceJson["steps"]}
    assert "terminated" in steps["control"]["decision"]
    assert steps["control"]["detail"] == {"reason": "bot loop", "set_by": "monitor"}
    assert "generate" not in steps


def test_neither_the_stored_trace_nor_the_logs_contain_the_customers_words():
    secret = "my number is 9876543210 and my secret code is PURPLE-ELEPHANT-77"
    lg, handler = capture_trace_logs()
    try:
        db, conv, ai, steps = turn(secret)
    finally:
        lg.removeHandler(handler)
    stored = json.dumps(ai.TraceJson)
    assert handler.lines, "expected trace log lines"
    for blob in (stored, "\n".join(handler.lines)):
        assert "9876543210" not in blob and "PURPLE-ELEPHANT" not in blob and "secret code" not in blob
    assert steps["phone_capture"]["decision"] == "phone number saved"    # the fact, never the number


def test_qualify_records_what_changed_without_amounts():
    db, conv, ai, steps = turn("I need a home loan of 50 lakh urgently, ready to apply today")
    q = steps["qualify"]["detail"]
    assert "intent" in q["after"] and q["after"]["budget_known"] in (True, False)
    assert "50" not in json.dumps(q)                    # amounts are customer data
    assert any(k in q["changed"] for k in ("intent", "status", "budget_known"))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
