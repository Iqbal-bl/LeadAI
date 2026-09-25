"""[END_CALL] is a voice control token; customers must never see it."""
from LeadAI.services.reply_cleanup import (
    closing_reply, has_end_call_token, is_acknowledgement, strip_control_tokens,
)


def test_token_removed_from_the_reply_seen_in_production():
    raw = ("An advisor from Nexa Finserv will take over this conversation. Expect a callback "
           "during business hours, Monday to Saturday, from 9:30 AM to 7:00 PM. \n\n[END_CALL]")
    out = strip_control_tokens(raw)
    assert "END_CALL" not in out
    assert out.endswith("7:00 PM.")            # no dangling whitespace either


def test_variants_and_normal_text():
    assert strip_control_tokens("Thanks!\n[end_call]") == "Thanks!"
    assert strip_control_tokens("Bye [ END_CALL ] now") == "Bye  now"
    assert strip_control_tokens("Plain reply") == "Plain reply"
    assert strip_control_tokens("") == "" and strip_control_tokens(None) == ""


def test_signal_detection():
    assert has_end_call_token("Goodbye\n[END_CALL]")
    assert not has_end_call_token("Goodbye")


def test_acknowledgements_are_recognised_and_real_messages_are_not():
    for text in ["Ok", "yes", "Yes!", "ok thanks", "Thank you", "okay 👍", "haan theek hai", "Thanks, bye"]:
        assert is_acknowledgement(text), text
    for text in ["What documents do I need?", "Yes tell me the rate", "ok but I want 1 crore",
                 "I have already tell you everything", "", "?", "ok ok ok ok ok"]:
        assert not is_acknowledgement(text), text


def test_closing_reply_language():
    assert closing_reply("Nexa", "ok").startswith("Thank you! An advisor from Nexa")
    assert closing_reply("Nexa", "haan theek hai").startswith("Shukriya! Nexa ka advisor")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
