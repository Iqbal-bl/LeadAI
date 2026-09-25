"""Regression guard: the golden-set metrics must not fall below the recorded baseline.

Deterministic (LLM off), so it is safe in CI. When a change improves a metric, refresh
the baseline on purpose:  PYTHONPATH=. python tests/eval/run_eval.py --write-baseline
Run: python tests/test_eval_baseline.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "eval"))
import run_eval  # noqa: E402

BASELINE = json.loads((Path(__file__).resolve().parent / "eval" / "baseline.json").read_text())


def test_no_metric_regresses_below_baseline():
    metrics = run_eval.run(live=False)["metrics"]
    regressed = {k: (metrics[k], floor) for k, floor in BASELINE.items() if metrics[k] < floor}
    assert not regressed, f"metrics fell below baseline (now, baseline): {regressed}"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
