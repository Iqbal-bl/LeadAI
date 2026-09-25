"""Answer-quality guards in ai_engine.answer():

  * When retrieval is weak, the model is told not to state a figure it cannot see for the
    exact product asked about (it used to grab another product's price).
  * A greeting is written by the company's own greeting prompt, so the persona shows up,
    with the fixed line only as a fallback.

Only the network is faked (retrieval and the LLM). Run: python tests/test_answer_guard.py
"""
import conftest_stub  # noqa: F401  — installs core.base/core.database/core.auth stubs first
from conftest_stub import Base, SessionLocalAdmin, engine

from LeadAI import models  # noqa: E402,F401
from LeadAI.config import settings as real_settings  # noqa: E402
from LeadAI.services import ai_engine  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass

CALLS: list[dict] = []


class _Settings:
    llm_enabled = True

    def __getattr__(self, name):
        return getattr(real_settings, name)


def fake_complete(system, messages, **kwargs):
    CALLS.append({"system": system, "messages": messages})
    return "Hello, I'm Kabir from Kestrel Homes.", {"model": "fake", "latency_ms": 1}


ai_engine.settings = _Settings()
ai_engine.llm.complete = fake_complete
ai_engine.vectorstore.search = lambda *a, **k: []            # nothing retrieved: confidence 0
ai_engine.vectorstore.idf_map = lambda *a, **k: ({}, 1.0)


def ask(question, threshold, history=None):
    CALLS.clear()
    ai_engine.company_thresholds = lambda db, client_id: (threshold, 5)
    db = SessionLocalAdmin()
    return ai_engine.answer(db, "c1", "Kestrel Homes", question, history=history or [], channel="chat")


def test_weak_retrieval_tells_the_model_not_to_guess_a_figure():
    ask("what is the price of a 3BHK?", threshold=0.4)
    last_user = CALLS[-1]["messages"][-1]["content"]
    assert "The match with the company knowledge is weak" in last_user
    assert "Customer question: what is the price of a 3BHK?" in last_user


def test_good_retrieval_is_left_alone():
    ask("what is the price of a 3BHK?", threshold=0.0)         # confidence 0 is not below 0
    assert "weak" not in CALLS[-1]["messages"][-1]["content"]


def test_greeting_uses_the_company_prompt_and_the_llm():
    out = ask("hi", threshold=0.4)
    assert out["reply"] == "Hello, I'm Kabir from Kestrel Homes."
    assert out["model"] == "fake" and out["confidence"] == 1.0 and out["needs_human"] is False
    assert len(CALLS) == 1 and CALLS[0]["messages"] == [{"role": "user", "content": "hi"}]


def test_greeting_falls_back_to_the_fixed_line_when_the_llm_fails():
    saved = ai_engine.llm.complete
    ai_engine.llm.complete = lambda *a, **k: (None, {"model": "fake", "latency_ms": 0})
    try:
        out = ask("hello", threshold=0.4)
        assert out["reply"].startswith("Hi! I'm the Kestrel Homes assistant.")
    finally:
        ai_engine.llm.complete = saved


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
