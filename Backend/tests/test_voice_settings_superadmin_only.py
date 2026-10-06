"""Pitch, speed, gender AND which voice speaks must be settable by a super
admin only, never a company admin (explicit product requirement) — and the
three that actually take effect on a real call (speed, gender, speaker;
pitch is dropped, see models.py's LeadCompanySettings comment: Sarvam's
live TTS model, bulbul:v3, ignores pitch entirely) must actually reach the
call pipeline, not just sit in a settings row nobody reads.

voice_speaker used to be settable per-script by a company admin
(script.manage); that ability was removed, not just hidden, when it moved
here alongside gender/speed — see the deprecated LeadCompanyScript.
VoiceSpeaker column's comment in models.py.

Covers three links in the chain:
  1. PUT /companies/{id}/voice-settings actually writes VoiceGender/VoiceSpeed/VoiceSpeaker.
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
        client.Id, VoiceSettingsIn(voice_gender="male", voice_speed=0.9, voice_speaker="ritu"),
        request=None, principal=_superadmin(), db=db,
    )
    assert out.voice_gender == "male"
    assert out.voice_speed == 0.9
    assert out.voice_speaker == "ritu"

    read_back = companies.get_settings(client.Id, principal=_superadmin(), db=db)
    assert read_back.voice_gender == "male"
    assert read_back.voice_speed == 0.9
    assert read_back.voice_speaker == "ritu"


def test_prepare_agent_context_reads_the_companys_voice_settings_not_the_script():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Voice 2")
    db.add(client)
    db.flush()
    db.add(models.LeadCompanySettings(ClientId=client.Id, VoiceGender="male", VoiceSpeed=0.8, VoiceSpeaker="ritu",
                                      SttTtsProvider="deepgram"))
    db.commit()

    _sections, _script, voice = call_bridge.prepare_agent_context(
        db, client.Id, client.Name, channel="voice",
    )
    assert voice["gender"] == "male"
    assert voice["pace"] == 0.8
    assert voice["speaker"] == "ritu"
    assert voice["provider"] == "deepgram"


def test_a_company_admin_cannot_set_voice_speaker_via_the_script_payload():
    from LeadAI import schemas

    assert "voice_speaker" not in schemas.ScriptCreate.model_fields
    assert "voice_speaker" not in schemas.ScriptUpdate.model_fields
    assert "voice_speaker" not in schemas.ScriptOut.model_fields


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
    assert voice["speaker"] == "ritu"
    assert voice["provider"] == "sarvam"


def test_the_endpoint_writes_stt_tts_provider_and_it_comes_back_on_read():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Voice 4")
    db.add(client)
    db.commit()

    out = companies.update_voice_settings(
        client.Id, VoiceSettingsIn(stt_tts_provider="deepgram"),
        request=None, principal=_superadmin(), db=db,
    )
    assert out.stt_tts_provider == "deepgram"

    read_back = companies.get_settings(client.Id, principal=_superadmin(), db=db)
    assert read_back.stt_tts_provider == "deepgram"


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
        pipeline.build_services({"speaker": "ritu", "pace": 0.8, "gender": "male"})
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


def _with_fake_deepgram_modules(fn):
    """Injects fake pipecat.services.deepgram.{stt,tts} modules for the
    duration of `fn()`, restoring whatever (if anything) was there before."""
    import os
    import sys
    import types

    class _FakeSettings:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeSttService:
        Settings = _FakeSettings

        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeTtsService:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    stt_mod = types.ModuleType("pipecat.services.deepgram.stt")
    stt_mod.DeepgramSTTService = _FakeSttService
    tts_mod = types.ModuleType("pipecat.services.deepgram.tts")
    tts_mod.DeepgramTTSService = _FakeTtsService
    saved = (sys.modules.get("pipecat.services.deepgram.stt"), sys.modules.get("pipecat.services.deepgram.tts"))
    sys.modules["pipecat.services.deepgram.stt"] = stt_mod
    sys.modules["pipecat.services.deepgram.tts"] = tts_mod
    saved_key = os.environ.get("DEEPGRAM_API_KEY")
    os.environ["DEEPGRAM_API_KEY"] = "test-key"
    try:
        return fn(_FakeSttService, _FakeTtsService)
    finally:
        if saved[0] is None:
            sys.modules.pop("pipecat.services.deepgram.stt", None)
        else:
            sys.modules["pipecat.services.deepgram.stt"] = saved[0]
        if saved[1] is None:
            sys.modules.pop("pipecat.services.deepgram.tts", None)
        else:
            sys.modules["pipecat.services.deepgram.tts"] = saved[1]
        if saved_key is None:
            os.environ.pop("DEEPGRAM_API_KEY", None)
        else:
            os.environ["DEEPGRAM_API_KEY"] = saved_key


def test_build_services_switches_to_deepgram_when_the_company_is_set_to_it():
    def _run(_FakeSttService, _FakeTtsService):
        services = pipeline.build_services({
            "provider": "deepgram", "speaker": "ritu", "gender": "male", "pace": 0.8,
        })
        assert isinstance(services.stt, _FakeSttService)
        assert isinstance(services.tts, _FakeTtsService)
        # Sarvam's voice id/pace must never leak into Deepgram's TTS kwargs —
        # the exact failure mode the "anushka" bug taught us, for a different vendor.
        assert "voice" not in services.tts.kwargs
        assert "pace" not in services.tts.kwargs

    _with_fake_deepgram_modules(_run)


def test_build_services_stays_on_sarvam_when_no_provider_is_set():
    """Every company that existed before this feature has no SttTtsProvider
    row at all — build_services() must keep calling Sarvam for them, not
    silently switch anyone to Deepgram."""
    import os

    def _run(_FakeSttService, _FakeTtsService):
        saved_key = os.environ.get("SARVAM_API_KEY")
        os.environ["SARVAM_API_KEY"] = "test-key"
        try:
            services = pipeline.build_services({"speaker": "ritu"})
        finally:
            if saved_key is None:
                os.environ.pop("SARVAM_API_KEY", None)
            else:
                os.environ["SARVAM_API_KEY"] = saved_key
        assert not isinstance(services.stt, _FakeSttService)
        assert not isinstance(services.tts, _FakeTtsService)
        assert "sarvam" in type(services.stt).__module__

    _with_fake_deepgram_modules(_run)


def test_multi_stt_defaults_on_so_a_new_script_can_detect_hinglish():
    """A real call: a company's script was left at whatever ScriptCreate
    defaults to, and the caller code-switched (English sentences with Hindi
    words mixed in). With multi_stt off, Sarvam's STT is pinned to one
    language for the whole call and reports that SAME language for every
    utterance regardless of what was actually said — which the system prompt
    then takes at face value ("The caller is speaking en-IN. Reply in
    en-IN."), so the AI never has a reason to answer in anything but English.
    A script a company admin creates without touching this checkbox must
    default to detecting per utterance, not pinning."""
    from LeadAI import schemas

    assert schemas.ScriptCreate(name="my script", script_xml="<script></script>").multi_stt is True


def test_multi_stt_off_pins_sarvam_stt_but_on_leaves_it_to_detect():
    stt_captured, tts_captured = {}, {}

    class _FakeSttSettings:
        def __init__(self, **kwargs):
            stt_captured.update(kwargs)

    class _FakeTtsSettings:
        def __init__(self, **kwargs):
            tts_captured.update(kwargs)

    class _FakeSttService:
        Settings = _FakeSttSettings

        def __init__(self, **kwargs):
            pass

    class _FakeTtsService:
        Settings = _FakeTtsSettings

        def __init__(self, **kwargs):
            pass

    import sys
    import types

    stt_mod = types.ModuleType("pipecat.services.sarvam.stt")
    stt_mod.SarvamSTTService = _FakeSttService
    tts_mod = types.ModuleType("pipecat.services.sarvam.tts")
    tts_mod.SarvamTTSService = _FakeTtsService
    saved = (sys.modules.get("pipecat.services.sarvam.stt"), sys.modules.get("pipecat.services.sarvam.tts"))
    sys.modules["pipecat.services.sarvam.stt"] = stt_mod
    sys.modules["pipecat.services.sarvam.tts"] = tts_mod
    import os
    saved_key = os.environ.get("SARVAM_API_KEY")
    os.environ["SARVAM_API_KEY"] = "test-key"
    try:
        pipeline.build_services({"language": "en", "multi_stt": False})
        assert stt_captured.get("language") is not None   # pinned

        stt_captured.clear()
        pipeline.build_services({"language": "en", "multi_stt": True})
        assert stt_captured.get("language") is None        # left to Sarvam to detect
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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
