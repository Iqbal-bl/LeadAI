"""Grounding check: numbers in a reply must come from the sources.

Pure functions, no fakes needed. Run: python tests/test_engine_grounding.py
"""
import conftest_stub  # noqa: F401

from LeadAI.engine.grounding import check_reply, extract_figures

KB = [
    "The home loan interest rate starts at 8.5% per annum. Processing fee is Rs. 25,000.",
    "Loan amounts range from 10 lakh to 5 crore. Call us on 1800123456 between 9 and 6.",
]


def _vals(text):
    return [f.value for f in extract_figures(text)]


def test_extraction_normalises_formats():
    assert _vals("Rs. 1,25,000") == ["125000"]
    assert _vals("₹125000") == ["125000"]
    assert _vals("8.50%") == ["8.5%"]
    assert _vals("8.5 percent") == ["8.5%"]
    assert _vals("1.25 crore") == _vals("1.25 Cr") == ["1.25crore"]


def test_small_bare_integers_are_not_checked():
    assert _vals("We offer 2 options in 3 steps") == []
    assert _vals("in 15 days") == ["15"]


def test_supported_reply_passes():
    r = check_reply("The rate starts at 8.5% and the fee is Rs. 25000.", KB)
    assert r.supported and r.unsupported == []


def test_invented_price_is_caught():
    r = check_reply("The processing fee is Rs. 15,000.", KB)
    assert not r.supported and r.unsupported_raw == ["15,000"]


def test_invented_rate_is_caught_even_if_close():
    r = check_reply("Rates start at 7.9%.", KB)
    assert not r.supported and "7.9%" in r.unsupported_raw


def test_phone_number_must_come_from_the_source():
    assert check_reply("Call 1800123456.", KB).supported
    assert not check_reply("Call 9876543210.", KB).supported


def test_customer_stated_number_can_be_repeated_back():
    r = check_reply("Got it, a budget of 50 lakh works.", KB, allowed=["my budget is 50 lakh"])
    assert r.supported
    assert not check_reply("Got it, a budget of 50 lakh works.", KB).supported


def test_no_numbers_is_trivially_supported():
    assert check_reply("Happy to help, let me connect you with an advisor.", KB).supported
    assert check_reply("", []).supported


def test_bare_number_matches_percent_in_source_and_the_reverse():
    assert check_reply("about 8.5 per annum", KB).supported
    assert check_reply("Loan amounts range from 10 lakh to 5 crore.", KB).supported


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
