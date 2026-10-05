"""{agent} and {company} are dynamic tokens a script or prompt can use
instead of hard-coding a literal name. Real problem this fixes: a script's
Identity section literally said "I'm Kabir from Kestrel Homes" — renaming
the persona meant hunting down every script/prompt that spelled the old
name out by hand, with nowhere to change it once. agent_name itself is a
company-admin setting (branding, not a voice parameter) — unlike
VoiceGender/VoiceSpeed/VoiceSpeaker, which are super-admin only.

Run: python tests/test_dynamic_variables.py
"""
import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.rbac import ROLE_PERMISSIONS, Principal  # noqa: E402
from LeadAI.routers import companies  # noqa: E402
from LeadAI.schemas import CompanySettingsIn  # noqa: E402
from LeadAI.services import script_engine  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def _company_admin(client_id):
    return Principal(email="admin@kestrel.test", role="company_admin", client_id=client_id,
                     permissions=set(ROLE_PERMISSIONS["company_admin"]))


def test_apply_dynamic_variables_substitutes_both_tokens():
    out = script_engine.apply_dynamic_variables(
        "Hi, I'm {agent} from {company}.", "Kestrel Homes", "Ritu",
    )
    assert out == "Hi, I'm Ritu from Kestrel Homes."


def test_apply_dynamic_variables_falls_back_when_agent_was_never_set():
    out = script_engine.apply_dynamic_variables("Hi, I'm {agent}.", "Kestrel Homes", None)
    assert out == "Hi, I'm our assistant."


def test_agent_name_is_none_until_a_company_admin_sets_one():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Dynamic")
    db.add(client)
    db.commit()
    assert script_engine.agent_name(db, client.Id) is None


def test_a_company_admin_can_set_agent_name_through_the_ordinary_settings_endpoint():
    """Unlike voice_gender/voice_speed/voice_speaker, this is NOT locked to
    super_admin() — a company admin picking their persona's name is
    branding, not a technical voice parameter."""
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Dynamic 2")
    db.add(client)
    db.commit()

    out = companies.update_settings(
        client.Id, CompanySettingsIn(agent_name="Ritu"),
        request=None, principal=_company_admin(client.Id), db=db,
    )
    assert out.agent_name == "Ritu"
    assert script_engine.agent_name(db, client.Id) == "Ritu"


def test_get_prompt_substitutes_agent_in_a_customised_prompt():
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Dynamic 3")
    db.add(client)
    db.flush()
    db.add(models.LeadCompanySettings(ClientId=client.Id, AgentName="Ritu"))
    db.add(models.LeadCompanyPrompt(ClientId=client.Id, PromptKey="greeting",
                                    Content="Hello, {agent} here from {company}!"))
    db.commit()

    out = script_engine.get_prompt(db, client.Id, client.Name, "greeting")
    assert out == "Hello, Ritu here from Kestrel Dynamic 3!"


def test_build_system_prompt_substitutes_agent_inside_the_scripts_own_content():
    """The actual bug: the persona's name was baked as literal text into the
    script's Identity section. With {agent} used there instead, renaming
    the persona (AgentName) updates what the model is told without
    touching the script at all."""
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Dynamic 4")
    db.add(client)
    db.flush()
    db.add(models.LeadCompanySettings(ClientId=client.Id, AgentName="Ritu"))
    script = models.LeadCompanyScript(
        ClientId=client.Id, Name="Property Advisor", Channel="voice", IsDefault=True, IsActive=True,
        SectionsJson=[
            {
                "title": "Identity", "type": "identity",
                "content": [
                    {"name": "name", "value": "{agent}"},
                    {"name": "company", "value": "{company}"},
                    {"name": "description", "value": "You are {agent}, {company}'s property advisor."},
                ],
            },
        ],
    )
    db.add(script)
    db.commit()

    prompt, _script = script_engine.build_system_prompt(db, client.Id, client.Name, channel="voice")
    assert "{agent}" not in prompt and "{company}" not in prompt
    assert "Ritu" in prompt and "Kestrel Dynamic 4" in prompt


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
