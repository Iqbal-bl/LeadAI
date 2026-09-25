"""The CODE defaults of every new switch must be the safe ones.

The running app reads .env, so the live values are whatever you chose. This guards what happens
on a machine with no .env (a fresh deployment, CI, a new developer): the new behaviour must
be off, except the read-only decision trace. It reads the defaults from the source, so it does
not depend on the environment. Run: python tests/test_engine_safe_defaults.py
"""
import re
from pathlib import Path

SOURCE = (Path(__file__).resolve().parent.parent / "LeadAI" / "config.py").read_text(encoding="utf-8")

SAFE = {
    "ENGINE_MODE": "off",                 # engine judges nothing
    "ENGINE_EVENTS": "false",             # no event rows
    "LEADAI_CONVERSATION_LOCK": "false",  # no extra DB connection per turn
    "VOICE_PIPELINE": "legacy",           # phone calls stay on the existing loop
    "ENGINE_TRACE_LOG": "true",           # observation only: ids, scores, reasons, no message text
    "ENGINE_TRACE_STORE": "true",
}


def default_of(name):
    match = re.search(r'\(\s*"%s"\s*,\s*"([^"]*)"' % re.escape(name), SOURCE)
    assert match, f"{name} default not found in config.py"
    return match.group(1)


def test_every_new_switch_defaults_to_the_safe_value():
    wrong = {k: (default_of(k), want) for k, want in SAFE.items() if default_of(k) != want}
    assert not wrong, f"unsafe defaults (found, expected): {wrong}"


def test_the_canary_number_list_defaults_to_nobody():
    assert default_of("VOICE_PIPECAT_NUMBERS") == ""


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
