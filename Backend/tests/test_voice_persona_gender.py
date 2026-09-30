"""A real call: the company script was named "Ritu" (a female persona, VoiceGender
"female", Sarvam speaker "ritu") and answered in Hindi with masculine verb forms
("main chahta hoon" instead of "chahti hoon"). The model was never actually told the
persona's gender — it only ever saw the name "Ritu" inside the script header text
and had to infer gender agreement from that alone, which it got wrong. VoiceGender
already existed on the script (used to pick the TTS voice in call_bridge.py) but
never reached the system prompt that generates the words being spoken.

Run: python tests/test_voice_persona_gender.py
"""
import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.services import script_engine  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def _make_script(db, client_id, gender):
    script = models.LeadCompanyScript(
        ClientId=client_id, Name="Kestrel Homes — Ritu (Property Advisor)",
        Channel="voice", VoiceGender=gender, IsDefault=True, IsActive=True,
    )
    db.add(script)
    db.commit()
    return script


def test_a_female_persona_is_told_to_use_feminine_hindi_verb_forms():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    _make_script(db, client.Id, "female")

    prompt, script = script_engine.build_system_prompt(db, client.Id, client.Name, channel="voice")
    assert "female assistant" in prompt
    assert "chahti hoon" in prompt
    assert "chahta hoon" not in prompt.split("never")[0]  # only named as the form to AVOID


def test_a_male_persona_is_told_to_use_masculine_hindi_verb_forms():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    _make_script(db, client.Id, "male")

    prompt, script = script_engine.build_system_prompt(db, client.Id, client.Name, channel="voice")
    assert "male assistant" in prompt
    assert "chahta hoon" in prompt


def test_the_gender_note_is_never_added_to_chat_only_voice():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    _make_script(db, client.Id, "female")

    prompt, script = script_engine.build_system_prompt(db, client.Id, client.Name, channel="chat")
    assert "assistant" not in prompt or "female assistant" not in prompt
    assert "chahti hoon" not in prompt


def test_no_script_at_all_adds_no_gender_note():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    # No LeadCompanyScript row exists for this client at all.
    prompt, script = script_engine.build_system_prompt(db, client.Id, client.Name, channel="voice")
    assert script is None
    assert "chahti hoon" not in prompt and "chahta hoon" not in prompt


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
