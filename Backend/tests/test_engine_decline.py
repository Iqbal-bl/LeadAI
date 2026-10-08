"""Decline detector: replies that say, in words, "I can't answer this".

Positives are real replies seen in the live golden-set run. The negatives matter as much:
a false positive hands a good answer to a human. Run: python tests/test_engine_decline.py
"""
from types import SimpleNamespace

import conftest_stub  # noqa: F401

from LeadAI.engine.decline import is_decline, recent_decline_count

DECLINES = [
    "I'm sorry, but I don't have information on car loan interest rates. Would you like me to connect you with a human specialist?",
    "I'm sorry, but I don't have information on our working hours.",
    "I don't have information regarding a cashback offer of Rs. 5,000 on loan approval.",
    "I don't have that in Nexa Finserv's knowledge base yet, so I'd rather not guess.",
    "I do not have any details about gold loans.",
    "I'm unable to confirm the exact fee right now.",
    "I'm not sure about that one.",
    "That is not in our knowledge base.",
]
NOT_DECLINES = [
    "Home loan interest rates start at 8.5% per annum.",
    "Could you share your budget and timeline?",
    "I don't have your budget yet, could you share it?",
    "I don't have the exact amount you need. How much are you looking for?",
    "An advisor from Nexa will contact you shortly.",
    "Sure, happy to help with that.",
    "",
]


def test_declines_are_detected():
    for reply in DECLINES:
        assert is_decline(reply), reply


def test_normal_replies_and_questions_to_the_customer_are_not_declines():
    for reply in NOT_DECLINES:
        assert not is_decline(reply), reply


def test_none_is_safe():
    assert is_decline(None) is False


def _msg(sender, content):
    return SimpleNamespace(Sender=sender, Content=content)


def test_recent_decline_count_counts_only_the_ais_own_declines():
    history = [
        _msg("customer", "do you have gold loans"),
        _msg("ai", "I don't have any details about gold loans."),
        _msg("customer", "ok what about car loans"),
        _msg("ai", "Car loan rates start at 9%."),          # not a decline
    ]
    assert recent_decline_count(history) == 1


def test_recent_decline_count_ignores_customer_messages_even_if_they_say_similar_words():
    history = [_msg("customer", "I don't have that information either")]
    assert recent_decline_count(history) == 0


def test_recent_decline_count_only_looks_at_the_lookback_window():
    history = [_msg("ai", "I don't have information on that.")] * 3
    assert recent_decline_count(history, lookback=2) == 2


def test_recent_decline_count_is_safe_on_empty_or_missing_history():
    assert recent_decline_count([]) == 0
    assert recent_decline_count(None) == 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
