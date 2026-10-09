"""Monitor agent (LeadAI/engine/monitor.py): triage classification run concurrently
with retrieval, gated by TRIAGE_MODE (off/observe/enforce). Run: python tests/test_monitor_triage.py
"""
from types import SimpleNamespace

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.config import settings as real_settings  # noqa: E402
from LeadAI.engine import gateway as engine_gateway  # noqa: E402
from LeadAI.engine import monitor as monitor_mod  # noqa: E402
from LeadAI.services import ai_engine, conversation_flow, scoring_queue  # noqa: E402

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
    triage_mode = "off"
    triage_timeout_seconds = 2.0
    triage_confidence_threshold = 0.7

    def __getattr__(self, name):
        return getattr(real_settings, name)


CTX = "Home loan interest rates start at 8.5% per annum."


def wire(llm_reply="Rates start at 8.5%.", llm_on=True, score=0.8,
         triage_mode="off", triage_json=None, triage_raises=False):
    settings_obj = _Settings()
    settings_obj.triage_mode = triage_mode
    ai_engine.settings = settings_obj
    ai_engine.settings.llm_enabled = llm_on
    monitor_mod.settings = settings_obj   # monitor.py imports settings independently
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

    def _fake_complete_json(*a, **k):
        if triage_raises:
            raise RuntimeError("boom")
        if triage_json is None:
            return None, {}
        return dict(triage_json), {"model": "fake-triage", "latency_ms": 2}

    engine_gateway.complete_json = _fake_complete_json


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
    scoring_queue.wait_idle()
    db.expire_all()
    db.refresh(conv)
    ai = db.query(models.LeadMessage).filter_by(ConversationId=conv.Id, Sender="ai").one()
    return db, conv, ai, {s["step"]: s for s in ai.TraceJson["steps"]}


# --------------------------------------------------------------- classify_turn (unit)
def test_the_prompt_treats_recall_questions_about_this_conversation_as_general():
    """A real incident: 'what plot did we finalize and what's our budget' was
    classified as a knowledge_question, so it went to KB retrieval instead of
    conversation memory — the model then guessed figures to answer a question
    the knowledge base has no way to know, and the grounding check (correctly)
    flagged those guessed figures and ended the call. The classifier must be
    told this class of question is general, not a product lookup, so it is
    answered from history/carryover instead (see the short-circuit path)."""
    prompt = monitor_mod.SYSTEM_PROMPT.lower()
    assert "recall" in prompt or "already said" in prompt or "already agreed" in prompt
    assert "finalize" in prompt or "budget" in prompt or "chose" in prompt


def test_classify_turn_returns_the_verdict_on_a_clean_json_reply():
    wire(triage_json={"category": "general", "confidence": 0.92, "reason": "small talk"})
    verdict = monitor_mod.classify_turn("hey there", None, [], "chat")
    assert verdict == {"category": "general", "confidence": 0.92, "reason": "small talk"}


def test_classify_turn_is_none_when_the_model_returns_nothing():
    wire(triage_json=None)
    assert monitor_mod.classify_turn("what is the rate", None, [], "chat") is None


def test_classify_turn_is_none_on_an_unrecognised_category():
    wire(triage_json={"category": "maybe", "confidence": 0.5, "reason": "?"})
    assert monitor_mod.classify_turn("hmm", None, [], "chat") is None


def test_classify_turn_never_raises_even_when_the_model_call_blows_up():
    wire(triage_raises=True)
    assert monitor_mod.classify_turn("hi", None, [], "chat") is None


def test_submit_returns_none_immediately_when_triage_is_off():
    wire(triage_mode="off", triage_json={"category": "general", "confidence": 0.9, "reason": "x"})
    assert monitor_mod.submit("hi", None, [], "chat") is None


# ---------------------------------------------------------- end to end, through answer()
def test_triage_off_by_default_leaves_behaviour_and_trace_unchanged():
    db, conv, ai, steps = turn("what is the interest rate on a home loan")
    assert "triage" not in steps and "triage_answer" not in steps
    assert "retrieve" in steps and "generate" in steps


def test_observe_mode_traces_the_verdict_without_changing_the_reply():
    db, conv, ai, steps = turn(
        "what is the interest rate on a home loan",
        triage_mode="observe",
        triage_json={"category": "knowledge_question", "confidence": 0.95, "reason": "asks about rates"},
    )
    assert steps["triage"]["detail"]["mode"] == "observe"
    assert steps["triage"]["decision"].startswith("knowledge_question")
    # Observe never changes the outcome: retrieval and generation still ran normally.
    assert "generate" in steps and "triage_answer" not in steps
    assert steps["answer_decision"]["decision"] == "answer"


def test_observe_mode_with_a_general_verdict_still_goes_through_retrieval():
    db, conv, ai, steps = turn(
        "haha okay just testing this thing out",
        triage_mode="observe",
        triage_json={"category": "general", "confidence": 0.88, "reason": "chit-chat"},
    )
    assert steps["triage"]["decision"].startswith("general")
    assert "generate" in steps                    # observe mode never short-circuits


def test_enforce_mode_with_a_confident_general_verdict_skips_the_kb_answer():
    """The generalised fix for the Hindi incident: anything the monitor is confident is
    NOT a product question skips KB retrieval's grounded generation entirely."""
    db, conv, ai, steps = turn(
        "haha okay just testing this thing out",
        triage_mode="enforce",
        triage_json={"category": "general", "confidence": 0.9, "reason": "chit-chat"},
        score=0.0,   # KB would have scored low confidence and escalated, same shape as the real incident
        llm_reply="Sure, happy to chat!",
    )
    assert steps["triage_answer"]["decision"].startswith("short-circuit")
    assert "generate" not in steps and "prompt" not in steps
    assert "answer_decision" not in steps          # that step belongs to the KB path we skipped
    assert ai.Content == "Sure, happy to chat!"    # the short acknowledgement, not a KB-grounded reply
    assert conv.Status != "needs_human"


def test_enforce_mode_below_the_confidence_bar_does_not_short_circuit():
    db, conv, ai, steps = turn(
        "what is the interest rate on a home loan",
        triage_mode="enforce",
        triage_json={"category": "general", "confidence": 0.3, "reason": "unsure"},
    )
    assert "triage_answer" not in steps
    assert "generate" in steps


def test_a_genuine_knowledge_question_still_goes_through_retrieval_even_in_enforce_mode():
    db, conv, ai, steps = turn(
        "what is the interest rate on a home loan",
        triage_mode="enforce",
        triage_json={"category": "knowledge_question", "confidence": 0.97, "reason": "asks about rates"},
    )
    assert "triage_answer" not in steps
    assert steps["generate"]["detail"]["model"] == "fake-model"


def test_a_request_for_a_human_is_never_sent_through_triage():
    db, conv, ai, steps = turn(
        "I want to talk to a human",
        triage_mode="enforce",
        triage_json={"category": "general", "confidence": 0.99, "reason": "should never be asked"},
    )
    assert "triage" not in steps and "triage_answer" not in steps


def test_a_classifier_timeout_in_enforce_mode_falls_open_to_normal_retrieval():
    db, conv, ai, steps = turn(
        "what is the interest rate on a home loan",
        triage_mode="enforce",
        triage_raises=True,
    )
    assert "triage" not in steps          # no verdict was ever produced
    assert "generate" in steps and ai.Content


# --------------------------------------- the short-circuit reply sees real context
def test_the_short_circuit_reply_can_see_conversation_history_and_carryover():
    """A real incident: a caller asked 'what did we finalize earlier' — general,
    not a product question, correctly short-circuited — but the reply came from a
    bare prompt that never saw the conversation, so the AI said it couldn't recall
    something it actually had in carryover. The short-circuit's own LLM call must
    receive the same history/carryover/session_note the KB-grounded path gets."""
    wire(triage_mode="enforce",
         triage_json={"category": "general", "confidence": 0.9, "reason": "recap question"})
    captured = {}

    def _capture_complete(system, messages, **kw):
        captured["system"] = system
        captured["messages"] = messages
        return "You already picked the 3 BHK and confirmed a 1.1 crore budget.", {
            "model": "fake-model", "latency_ms": 5,
        }

    ai_engine.llm.complete = _capture_complete

    history = [
        SimpleNamespace(Sender="customer", Content="I'll take the UNIT-MARKER-ALPHA apartment."),
        SimpleNamespace(Sender="ai", Content="Great, I've noted UNIT-MARKER-ALPHA."),
    ]
    reply = ai_engine.answer(
        None, "c1", "Nexa Finserv", "can you remind me what we finalized earlier",
        history=history, channel="voice",
        carryover="Customer confirmed a budget of CARRYOVER-MARKER-BETA.",
        session_note="This conversation already has a qualified lead.",
    )
    assert reply["confidence"] == 1.0 and reply["needs_human"] is False
    joined = " ".join(m.get("content", "") for m in captured["messages"])
    # Both sources reached the model as context, not just the bare utterance — each
    # marker is independent so a regression in either path is caught on its own.
    assert "UNIT-MARKER-ALPHA" in joined       # from conversation history
    assert "CARRYOVER-MARKER-BETA" in joined   # from carryover
    assert any(m["role"] == "user" and "remind me what we finalized" in m["content"]
               for m in captured["messages"])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
