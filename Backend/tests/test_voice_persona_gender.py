"""A real call: the company script was named "Ritu" (a female persona) and
answered in Hindi with masculine verb forms ("main chahta hoon" instead of
"chahti hoon"). The model was never actually told the persona's gender — it
only ever saw the name "Ritu" inside the script header text and had to infer
gender agreement from that alone, which it got wrong.

Gender used to live on LeadCompanyScript.VoiceGender (company-admin
editable). It has since moved to LeadCompanySettings.VoiceGender —
platform-level, super-admin only (see rbac.super_admin() and
routers/companies.py's /voice-settings endpoint) — because a company admin
must not control this, only the platform team. The gender note is therefore
now independent of which script (if any) is in force.

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


def _make_company_voice_settings(db, client_id, gender):
    db.add(models.LeadCompanySettings(ClientId=client_id, VoiceGender=gender))
    db.commit()


def _make_script(db, client_id):
    script = models.LeadCompanyScript(
        ClientId=client_id, Name="Kestrel Homes — Ritu (Property Advisor)",
        Channel="voice", IsDefault=True, IsActive=True,
    )
    db.add(script)
    db.commit()
    return script


def test_a_female_persona_is_told_to_use_feminine_hindi_verb_forms():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    _make_company_voice_settings(db, client.Id, "female")
    _make_script(db, client.Id)

    prompt, script = script_engine.build_system_prompt(db, client.Id, client.Name, channel="voice")
    assert "female assistant" in prompt
    assert "chahti hoon" in prompt
    assert "chahta hoon" not in prompt.split("never")[0]  # only named as the form to AVOID


def test_a_male_persona_is_told_to_use_masculine_hindi_verb_forms():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    _make_company_voice_settings(db, client.Id, "male")
    _make_script(db, client.Id)

    prompt, script = script_engine.build_system_prompt(db, client.Id, client.Name, channel="voice")
    assert "male assistant" in prompt
    assert "chahta hoon" in prompt


def test_the_gender_note_is_never_added_to_chat_only_voice():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    _make_company_voice_settings(db, client.Id, "female")
    _make_script(db, client.Id)

    prompt, script = script_engine.build_system_prompt(db, client.Id, client.Name, channel="chat")
    assert "assistant" not in prompt or "female assistant" not in prompt
    assert "chahti hoon" not in prompt


def test_no_company_voice_settings_at_all_still_defaults_to_female():
    """The gender note is company-wide now, not tied to whether a script
    exists — a voice call with no LeadCompanySettings row at all still gets
    correct gender agreement, defaulting to the platform default (female),
    rather than silently skipping the note."""
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    # No LeadCompanySettings row, no LeadCompanyScript row, for this client.
    prompt, script = script_engine.build_system_prompt(db, client.Id, client.Name, channel="voice")
    assert script is None
    assert "female assistant" in prompt
    assert "chahti hoon" in prompt


def test_a_company_admin_cannot_set_gender_via_the_script_payload():
    """ScriptCreate/ScriptUpdate no longer accept voice_gender at all — gender
    moved to a super-admin-only company setting, see routers/companies.py."""
    from LeadAI import schemas

    assert "voice_gender" not in schemas.ScriptCreate.model_fields
    assert "voice_gender" not in schemas.ScriptUpdate.model_fields
    assert "voice_gender" not in schemas.ScriptOut.model_fields


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
