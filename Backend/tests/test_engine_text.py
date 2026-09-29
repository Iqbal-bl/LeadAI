"""Sentence splitting must not cut a price off its "Rs." label.

Regression: the no-LLM fallback answered "The processing fee is Rs." because every full
stop was treated as a sentence end. Run: python tests/test_engine_text.py
"""
import conftest_stub  # noqa: F401

from LeadAI.engine.text import split_sentences


def test_rupee_amount_stays_with_its_label():
    text = "A one-time processing fee of Rs. 25,000 plus GST is charged. It is non-refundable."
    assert split_sentences(text) == [
        "A one-time processing fee of Rs. 25,000 plus GST is charged.",
        "It is non-refundable.",
    ]


def test_other_abbreviations_and_case():
    assert split_sentences("Ask Mr. Rao about it. Dr. Mehta agrees.") == [
        "Ask Mr. Rao about it.", "Dr. Mehta agrees."]
    assert split_sentences("Fee is RS. 500. Thanks.") == ["Fee is RS. 500.", "Thanks."]


def test_normal_sentences_still_split():
    assert split_sentences("Rates start at 8.5%. Apply today! Any questions?") == [
        "Rates start at 8.5%.", "Apply today!", "Any questions?"]


def test_empty_input():
    assert split_sentences("") == [] and split_sentences(None) == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
