"""Golden-set evaluation of the answering pipeline.

Answers a fixed set of questions against a fixed knowledge base and scores the result.
It exists so that every later change (new engine, tools, verification, a different
model or prompt) is measured against the same yardstick instead of judged by feel.

Modes
  default   LLM off. Exercises retrieval, confidence and the extractive fallback.
            Deterministic, free, needs no key. This is what CI runs.
  --live    Uses the configured OpenAI key, so it measures what customers actually get.
            Costs a few cents. Non-deterministic, so it is reported, not asserted.

Only retrieval is faked (a keyword retriever over the fixture KB, built on the real
`vectorstore` scoring helpers). Confidence, thresholds, prompts and handoff rules are
the production code.

Metrics
  answer_correct   answerable question -> reply contains an expected fact, no handoff
  abstain          unanswerable question -> flagged for a human (system-level)
  safe_on_unknown  unanswerable question -> escalated OR the reply itself declines
                   (abstain minus safe_on_unknown = says "I don't know" but leaves
                   the conversation un-flagged, so no human ever follows up)
  forbid_clean     replies that must NOT contain a specific wrong figure, and do not
  no_false_figure  share of ALL replies whose numbers are all present in the sources
  *_engine         the same, after the engine (ENGINE_MODE=enforce) has judged the reply:
                   abstain_engine should rise, and answer_correct_engine must NOT fall
                   below answer_correct (the engine may never escalate a good answer)
  human_handoff    "talk to a human" -> escalated
  greeting_ok      greeting -> answered, no handoff

Run:  PYTHONPATH=. python tests/eval/run_eval.py [--live] [--write-baseline]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import conftest_stub  # noqa: E402,F401  — core.* stubs; must precede LeadAI imports
from conftest_stub import Base, SessionLocalAdmin, engine  # noqa: E402

from LeadAI import models  # noqa: E402
from LeadAI.config import settings as real_settings  # noqa: E402
from LeadAI.engine import bridge, grounding  # noqa: E402
from LeadAI.services import ai_engine, vectorstore  # noqa: E402

GOLDEN = json.loads((HERE / "golden_set.json").read_text(encoding="utf-8"))
BASELINE_PATH = HERE / "baseline.json"
CLIENT_ID = "eval-client"
KB = {c["id"]: c["text"] for c in GOLDEN["kb"]}


def _ensure_tables() -> None:
    for table in Base.metadata.sorted_tables:
        try:
            table.create(bind=engine, checkfirst=True)
        except Exception:  # noqa: BLE001
            pass


def _fixture_idf() -> tuple[dict[str, float], float]:
    """The same IDF formula as vectorstore.idf_map, over the fixture KB."""
    import math

    freq: dict[str, int] = {}
    for text in KB.values():
        for term in vectorstore.keywords(text):
            freq[term] = freq.get(term, 0) + 1
    total = len(KB)
    return (
        {t: math.log(1 + total / (1 + df)) for t, df in freq.items()},
        math.log(1 + total),
    )


def _fake_search(db, client_id, query, top_k=5):
    idf, unseen = _fixture_idf()
    scored = []
    for chunk_id, text in KB.items():
        coverage = vectorstore.lexical_coverage(query, text, idf, unseen)
        if coverage > 0:
            # Real scores are embedding cosine similarity (~0.3-0.8); map coverage onto
            # that range so the confidence blend behaves like production.
            scored.append(
                {"chunk_id": chunk_id, "document_id": "doc", "score": 0.85 * coverage, "text": text}
            )
    scored.sort(key=lambda h: h["score"], reverse=True)
    return scored[:top_k]


class _Settings:
    def __init__(self, llm_enabled: bool):
        self.llm_enabled = llm_enabled

    def __getattr__(self, name):
        return getattr(real_settings, name)


def _history(pairs):
    return [models.LeadMessage(Sender=s, Content=t) for s, t in pairs or []]


DECLINE = re.compile(
    r"don't have|do not have|not sure|rather not guess|specialist|advisor|can't confirm|unable to",
    re.I,
)


def _contains_any(text: str, needles: list[str]) -> bool:
    low = text.lower()
    return any(n.lower() in low for n in needles)


def run(live: bool = False) -> dict:
    _ensure_tables()
    ai_engine.settings = _Settings(llm_enabled=live and real_settings.llm_enabled)
    ai_engine.vectorstore.search = _fake_search
    ai_engine.vectorstore.idf_map = lambda db, client_id: _fixture_idf()
    ai_engine.company_thresholds = lambda db, client_id: (real_settings.handoff_confidence_threshold, 5)

    rows = []
    for case in GOLDEN["cases"]:
        db = SessionLocalAdmin()
        history = _history(case.get("history"))
        out = ai_engine.answer(
            db, CLIENT_ID, GOLDEN["company"], case["q"], history=history, channel="chat"
        )
        reply, kind = out["reply"], case["kind"]
        source_texts = [KB[s["chunk_id"]] for s in out["sources"] if s["chunk_id"] in KB]
        allowed = [case["q"], *[t for _, t in case.get("history") or []]]
        figures_ok = grounding.check_reply(reply, source_texts, allowed=allowed)

        def verdict(needs_human: bool) -> bool:
            if kind == "answer":
                return _contains_any(reply, case["expect_any"]) and not needs_human
            if kind in ("unknown", "human"):
                return bool(needs_human)
            return not needs_human and bool(reply)  # greeting

        judged = bridge.apply(
            out, text=case["q"], client_id=CLIENT_ID, conversation_id=case["id"],
            channel="chat", mode="enforce",
        )
        ok = verdict(out["needs_human"])
        ok_engine = verdict(judged["needs_human"])
        forbid_ok = not _contains_any(reply, case.get("forbid", []))
        safe = bool(out["needs_human"]) or bool(DECLINE.search(reply))
        rows.append(
            {
                "id": case["id"], "kind": kind, "ok": ok, "ok_engine": ok_engine, "forbid_ok": forbid_ok,
                "safe": safe, "figures_ok": figures_ok.supported, "unsupported": figures_ok.unsupported_raw,
                "confidence": out["confidence"], "needs_human": out["needs_human"], "reply": reply,
            }
        )

    def rate(kind, key="ok"):
        sel = [r for r in rows if r["kind"] == kind]
        return round(sum(r[key] for r in sel) / len(sel), 3) if sel else None

    forbid_rows = [r for r, c in zip(rows, GOLDEN["cases"]) if c.get("forbid")]
    metrics = {
        "answer_correct": rate("answer"),
        "abstain": rate("unknown"),
        "safe_on_unknown": round(
            sum(r["safe"] and r["forbid_ok"] for r in rows if r["kind"] == "unknown")
            / sum(1 for r in rows if r["kind"] == "unknown"),
            3,
        ),
        "abstain_engine": rate("unknown", "ok_engine"),
        "answer_correct_engine": rate("answer", "ok_engine"),
        "human_handoff": rate("human"),
        "greeting_ok": rate("greeting"),
        "forbid_clean": round(sum(r["forbid_ok"] for r in forbid_rows) / len(forbid_rows), 3),
        "no_false_figure": round(sum(r["figures_ok"] for r in rows) / len(rows), 3),
    }
    return {"live": live, "metrics": metrics, "rows": rows}


def _print(report: dict) -> None:
    print(f"\nMode: {'LIVE LLM' if report['live'] else 'extractive (no LLM)'}\n")
    for r in report["rows"]:
        flag = "ok  " if r["ok_engine"] and r["forbid_ok"] and r["figures_ok"] else "FAIL"
        flag = flag if r["ok"] == r["ok_engine"] else flag + "*"
        extra = f"  unsupported={r['unsupported']}" if r["unsupported"] else ""
        print(f"  [{flag}] {r['id']:<14} conf={r['confidence']:<5} human={str(r['needs_human']):<5}{extra}")
        if flag == "FAIL":
            print(f"         -> {r['reply'][:150]!r}")
    print("\nMetrics:")
    for name, value in report["metrics"].items():
        print(f"  {name:<16} {value}")


if __name__ == "__main__":
    live = "--live" in sys.argv
    report = run(live=live)
    _print(report)
    if "--write-baseline" in sys.argv and not live:
        BASELINE_PATH.write_text(json.dumps(report["metrics"], indent=2) + "\n", encoding="utf-8")
        print(f"\nBaseline written to {BASELINE_PATH}")
