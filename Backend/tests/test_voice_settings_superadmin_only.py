"""Pitch, speed and gender for AI calls must be settable by a super admin
only, never a company admin (explicit product requirement) — and the two
that actually take effect on a real call (speed, gender; pitch is dropped,
see models.py's LeadCompanySettings comment: Sarvam's live TTS model,
bulbul:v3, ignores pitch entirely) must actually reach the call pipeline,
not just sit in a settings row nobody reads.

Covers three links in the chain:
  1. PUT /companies/{id}/voice-settings actually writes VoiceGender/VoiceSpeed.
  2. call_bridge.prepare_agent_context() reads them back for a real call.
  3. voice.pipeline.build_services() turns them into the Sarvam TTS kwargs.

(The super-admin-only GUARD on the endpoint itself is covered by
test_super_admin_only.py's systematic LOCKED-endpoint sweep.)

Run: python tests/test_voice_settings_superadmin_only.py
"""
import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.rbac import P, Principal  # noqa: E402
from LeadAI.routers import companies  # noqa: E402
from LeadAI.schemas import VoiceSettingsIn  # noqa: E402
from LeadAI.services import call_bridge  # noqa: E402
from LeadAI.voice import pipeline  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def _superadmin():
    return Principal(email="root@platform.test", role="Admin", client_id=None, permissions=set(P))


def test_the_endpoint_writes_gender_and_speed_and_they_come_back_on_read():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Voice")
    db.add(client)
    db.commit()

    out = companies.update_voice_settings(
        client.Id, VoiceSettingsIn(voice_gender="male", voice_speed=0.9),
        request=None, principal=_superadmin(), db=db,
    )
    assert out.voice_gender == "male"
    assert out.voice_speed == 0.9

    read_back = companies.get_settings(client.Id, principal=_superadmin(), db=db)
    assert read_back.voice_gender == "male"
    assert read_back.voice_speed == 0.9


def test_prepare_agent_context_reads_the_companys_voice_settings_not_the_script():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Voice 2")
    db.add(client)
    db.flush()
    db.add(models.LeadCompanySettings(ClientId=client.Id, VoiceGender="male", VoiceSpeed=0.8))
    db.commit()

    _sections, _script, voice = call_bridge.prepare_agent_context(
        db, client.Id, client.Name, channel="voice",
    )
    assert voice["gender"] == "male"
    assert voice["pace"] == 0.8


def test_prepare_agent_context_falls_back_to_platform_defaults_with_no_settings_row():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Voice 3")
    db.add(client)
    db.commit()

    _sections, _script, voice = call_bridge.prepare_agent_context(
        db, client.Id, client.Name, channel="voice",
    )
    assert voice["gender"] == "female"
    assert voice["pace"] == 1.1


def test_build_services_passes_the_companys_pace_through_to_sarvam_tts_kwargs():
    captured = {}

    class _FakeSettings:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _FakeService:
        Settings = _FakeSettings

        def __init__(self, **kwargs):
            pass

    import sys
    import types

    stt_mod = types.ModuleType("pipecat.services.sarvam.stt")
    stt_mod.SarvamSTTService = _FakeService
    tts_mod = types.ModuleType("pipecat.services.sarvam.tts")
    tts_mod.SarvamTTSService = _FakeService
    saved = (sys.modules.get("pipecat.services.sarvam.stt"), sys.modules.get("pipecat.services.sarvam.tts"))
    sys.modules["pipecat.services.sarvam.stt"] = stt_mod
    sys.modules["pipecat.services.sarvam.tts"] = tts_mod
    import os
    saved_key = os.environ.get("SARVAM_API_KEY")
    os.environ["SARVAM_API_KEY"] = "test-key"
    try:
        pipeline.build_services({"speaker": "anushka", "pace": 0.8, "gender": "male"})
    finally:
        if saved[0] is None:
            sys.modules.pop("pipecat.services.sarvam.stt", None)
        else:
            sys.modules["pipecat.services.sarvam.stt"] = saved[0]
        if saved[1] is None:
            sys.modules.pop("pipecat.services.sarvam.tts", None)
        else:
            sys.modules["pipecat.services.sarvam.tts"] = saved[1]
        if saved_key is None:
            os.environ.pop("SARVAM_API_KEY", None)
        else:
            os.environ["SARVAM_API_KEY"] = saved_key

    assert captured.get("pace") == 0.8


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
