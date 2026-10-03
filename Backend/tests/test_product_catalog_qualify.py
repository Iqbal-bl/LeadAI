"""Lead.Product used to be whatever string the AI felt like guessing — the
qualification LLM call just invented a short product name ("Business Loan") with
no connection to anything the company actually sells. Now that companies can define
a real product catalog (LeadProduct, with its own attached knowledge base file),
qualification must pick EXACTLY one of the company's own defined names, or
"unknown" — never an invented one, and never a near-miss from the catalog.

Run: python tests/test_product_catalog_qualify.py
"""
import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.config import settings as real_settings  # noqa: E402
from LeadAI.services import ai_engine  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


class _Settings:
    llm_enabled = True
    llm_qualification = True

    def __getattr__(self, name):
        return getattr(real_settings, name)


def _wire(product_from_llm):
    ai_engine.settings = _Settings()
    ai_engine.llm.complete_json = lambda *a, **k: ({
        "intent": "evaluating", "timeline": "unknown", "budget": "unknown",
        "product": product_from_llm, "sentiment": "neutral", "summary": "s",
        "next_step": "n", "facts": [],
    }, {})
    ai_engine.vectorstore.search = lambda *a, **k: []   # keyword heuristic finds nothing
    ai_engine._detect_product = lambda *a, **k: None


def _setup(product=None, catalog=()):
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    for name in catalog:
        db.add(models.LeadProduct(ClientId=client.Id, ProductName=name, ProductDescription="loan"))
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="C1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="chat")
    db.add(conv)
    db.flush()
    lead = models.Lead(ClientId=client.Id, ConversationId=conv.Id, Product=product or "unknown")
    msg = models.LeadMessage(ClientId=client.Id, ConversationId=conv.Id, Sender="customer",
                             Content="Tell me about your home loan options")
    db.add(msg)
    db.commit()
    return db, client, lead, msg


# =========================================================================== #
# unit: the instruction builder and the snap-to-catalog validator
# =========================================================================== #
def test_no_catalog_means_no_instruction_and_no_behaviour_change():
    assert ai_engine._product_catalog_instruction([]) == ""
    assert ai_engine._snap_to_catalog("Anything At All", []) == "Anything At All"


def test_catalog_instruction_lists_every_name_and_the_unknown_escape_hatch():
    instruction = ai_engine._product_catalog_instruction(["Home Loan", "Personal Loan"])
    assert '"Home Loan"' in instruction and '"Personal Loan"' in instruction
    assert '"unknown"' in instruction


def test_snap_matches_case_insensitively_to_the_catalogs_own_casing():
    assert ai_engine._snap_to_catalog("home loan", ["Home Loan", "Personal Loan"]) == "Home Loan"


def test_snap_rejects_a_name_outside_the_catalog():
    assert ai_engine._snap_to_catalog("Car Loan", ["Home Loan", "Personal Loan"]) == "unknown"


# =========================================================================== #
# integration: qualify()
# =========================================================================== #
def test_qualify_constrains_product_to_the_defined_catalog():
    db, client, lead, msg = _setup(catalog=["Home Loan", "2BHK Apartment"])
    _wire(product_from_llm="home loan")   # the model's own casing/wording
    ai_engine.qualify(db, client.Id, lead, [msg])
    assert lead.Product == "Home Loan"    # canonical catalog casing, not the model's


def test_qualify_never_leaks_a_name_outside_the_catalog_into_lead_product():
    db, client, lead, msg = _setup(catalog=["Home Loan", "2BHK Apartment"])
    _wire(product_from_llm="Car Loan")    # not one of this company's products
    ai_engine.qualify(db, client.Id, lead, [msg])
    assert lead.Product == "unknown"


def test_a_company_with_no_catalog_keeps_the_old_freeform_behaviour():
    db, client, lead, msg = _setup(catalog=[])   # no catalog defined at all
    _wire(product_from_llm="Business Loan")
    ai_engine.qualify(db, client.Id, lead, [msg])
    assert lead.Product == "Business Loan"        # accepted as-is, exactly as before


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
