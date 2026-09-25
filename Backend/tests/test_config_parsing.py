"""Env values with a trailing comment are read correctly (`.env` has no `//` comments)."""
import os

from LeadAI.config import _b, _f, _i


def _with(value, fn, default):
    os.environ["LEADAI_TEST_SETTING"] = value
    try:
        return fn("LEADAI_TEST_SETTING", default)
    finally:
        del os.environ["LEADAI_TEST_SETTING"]


def test_boolean_with_a_trailing_comment():
    assert _with("true //for lead scoring", _b, "false") is True      # used to read as False
    assert _with("false //for lead scoring", _b, "true") is False
    assert _with("true # note", _b, "false") is True
    assert _with("  yes  ", _b, "false") is True


def test_numbers_with_a_trailing_comment():
    assert _with("30 // max replies", _i, 20) == 30                   # used to fall back to 20
    assert _with("0.5 # threshold", _f, 0.4) == 0.5


def test_plain_and_broken_values():
    assert _with("20", _i, 5) == 20
    assert _with("abc", _i, 5) == 5
    assert _b("LEADAI_TEST_SETTING_UNSET", "true") is True


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
