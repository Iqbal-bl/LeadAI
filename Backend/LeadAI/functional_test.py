"""
Functional smoke test.

Stubs ONLY the outbound app's infrastructure modules (MySQL engine, twilio
client, xml_parser is real) so the LeadAI logic can be exercised on SQLite
without a database server. Everything under LeadAI/ is the real code.
"""
import os
import sys
import types

sys.path.insert(0, "/home/claude/work/build")
os.environ.setdefault("LEADAI_BOOTSTRAP_ADMINS", "root@platform.io")

# ---- stub `database` (MySQL) with SQLite -----------------------------------
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

engine = create_engine("sqlite:///./_test_leadai.db", connect_args={"check_same_thread": False})
SessionLocalAdmin = sessionmaker(bind=engine, autoflush=False, autocommit=False)

db_stub = types.ModuleType("database")
db_stub.engine_admin = engine
db_stub.SessionLocalAdmin = SessionLocalAdmin
sys.modules["database"] = db_stub

# ---- stub `auth.get_current_user` -----------------------------------------
CURRENT_USER = {"email": "root@platform.io"}
auth_stub = types.ModuleType("auth")
auth_stub.get_current_user = lambda: CURRENT_USER["email"]
auth_stub.get_current_user_websocket = lambda ws: CURRENT_USER["email"]
sys.modules["auth"] = auth_stub

# ---- stub `multiligual_call` (twilio + SCRIPTS_DIR) ----------------------
mc_stub = types.ModuleType("multiligual_call")
mc_stub.SCRIPTS_DIR = "/home/claude/work/build/scripts"
mc_stub.active_calls = {}
mc_stub.TWILIO_PHONE_NUMBER = "+10000000000"
mc_stub.PUBLIC_PATHS = ("/docs", "/")
class _FakeCalls:
    def create(self, **kw):
        return types.SimpleNamespace(sid="CA" + "0" * 30)
    def __call__(self, sid):
        return types.SimpleNamespace(update=lambda **kw: None)
mc_stub.twilio_client = types.SimpleNamespace(calls=_FakeCalls())
sys.modules["multiligual_call"] = mc_stub

# ---- stub `globals`, `validate_number`, `db` (outbound) ------------------
g = types.ModuleType("globals"); g.call_hangup_reasons = {}
sys.modules["globals"] = g

vn = types.ModuleType("validate_number")
vn.validate_phone_number = lambda n: n if n.startswith("+") else "+91" + n
sys.modules["validate_number"] = vn

outbound_db = types.ModuleType("db")
outbound_db.fetch_conversation = lambda sid: [
    {"response_type": "question", "response_text": "Hello, this is Acme Bank calling.", "created_at": None},
    {"response_type": "answer", "response_text": "Yes I want to apply for a home loan today, budget 50 lakh.", "created_at": None},
]
sys.modules["db"] = outbound_db

# ---- real Base + Domain models -------------------------------------------
from base import Base
from Domain import models as domain_models
from LeadAI import models as leadai_models

ok = 0
for _t in Base.metadata.sorted_tables:
    try:
        _t.create(bind=engine, checkfirst=True)
        ok += 1
    except Exception as e:
        # Mirrors db.connect_db(): the outbound Domain models have a pre-existing
        # duplicate index name on batchinfo. Tolerated there, tolerated here.
        print(f"   (skipped pre-existing table {_t.name}: {e.__class__.__name__})")
print(f"tables: {ok} created; leadai tables = "
      f"{len([t for t in Base.metadata.tables if t.startswith('leadai')])}")

# ===========================================================================
# Build the app
# ===========================================================================
from fastapi import FastAPI
from fastapi.testclient import TestClient

from LeadAI.config import settings
from LeadAI.router import api_router

app = FastAPI()
app.include_router(api_router, prefix=settings.api_prefix)
client = TestClient(app)
P = settings.api_prefix

def check(name, resp, expect=200):
    ok = resp.status_code == expect
    print(f"{'PASS' if ok else 'FAIL'} {name} -> {resp.status_code}")
    if not ok:
        print("   ", resp.text[:600])
        raise SystemExit(1)
    return resp.json() if resp.text else None

# ---- health ---------------------------------------------------------------
h = check("health", client.get(f"{P}/health"))
print("   llm:", h["llm"], "| embeddings:", h["embeddings"], "| vectors:", h["vector_store"])

# ---- bootstrap admin + me ------------------------------------------------
me = check("me (bootstrap Admin)", client.get(f"{P}/access/me"))
print("   role:", me["role"], "| permissions:", len(me["permissions"]))
assert me["role"] == "Admin"

# ---- create two companies (tenant isolation test) -----------------------
acme = check("create company Acme", client.post(f"{P}/companies", json={
    "name": "Acme Bank", "email": "ops@acmebank.com",
    "description": "Retail banking", "admin_email": "admin@acmebank.com",
    "admin_name": "Acme Admin"}), 201)
globex = check("create company Globex", client.post(f"{P}/companies", json={
    "name": "Globex Realty", "description": "Real estate",
    "admin_email": "admin@globexrealty.com"}), 201)
ACME, GLOBEX = acme["id"], globex["id"]

check("list companies", client.get(f"{P}/companies"))

# ---- knowledge base per company -----------------------------------------
check("acme kb faq", client.post(f"{P}/knowledge/faq?client_id={ACME}", json={
    "title": "Home Loan FAQ",
    "content": (
        "Home Loan Eligibility\n"
        "Acme Bank home loans require a minimum monthly income of 40,000 rupees and a "
        "CIBIL score above 720. The maximum loan amount is 5 crore.\n\n"
        "Interest Rates\n"
        "The home loan interest rate starts at 8.35 percent per annum for salaried "
        "applicants. Processing fee is 0.5 percent of the loan amount, capped at 10,000 rupees.\n\n"
        "Documents Required\n"
        "You must submit PAN card, Aadhaar, last three salary slips, six months bank "
        "statement and the property sale agreement.\n\n"
        "Approval Timeline\n"
        "Approval is usually completed within 7 working days of document submission."
    )}), 201)

check("globex kb faq", client.post(f"{P}/knowledge/faq?client_id={GLOBEX}", json={
    "title": "Property Listings",
    "content": (
        "Available Projects\n"
        "Globex Realty sells 2BHK and 3BHK apartments in Pune and Mumbai. The Pune "
        "Skyline project starts at 85 lakh rupees for a 2BHK unit.\n\n"
        "Booking Process\n"
        "A booking amount of 2 lakh rupees reserves the unit for 30 days. Possession "
        "for Pune Skyline is scheduled for December 2027.\n\n"
        "Amenities\n"
        "All Globex projects include a clubhouse, swimming pool and covered parking."
    )}), 201)

stats = check("acme kb stats", client.get(f"{P}/knowledge/stats?client_id={ACME}"))
print("   acme chunks:", stats["chunks"], "| model:", stats["embedding_model"])

# ---- TENANT ISOLATION: same query, two companies ------------------------
q = {"query": "what is the home loan interest rate?"}
a = check("acme retrieval", client.post(f"{P}/knowledge/test?client_id={ACME}", json=q))
g_ = check("globex retrieval (should NOT know)", client.post(f"{P}/knowledge/test?client_id={GLOBEX}", json=q))
print(f"   ACME  conf={a['confidence']} needs_human={a['needs_human']}")
print(f"      -> {a['answer'][:150]}")
print(f"   GLOBEX conf={g_['confidence']} needs_human={g_['needs_human']}")
print(f"      -> {g_['answer'][:150]}")
assert a["confidence"] > g_["confidence"], "tenant isolation failed: globex scored >= acme"
assert g_["needs_human"], "globex should escalate — it has no home loan knowledge"
print("   ISOLATION OK: Acme answers, Globex escalates")

# ---- dynamic per-company script -----------------------------------------
import os as _os
script_files = [f for f in _os.listdir("/home/claude/work/build/scripts") if f.endswith(".xml")]
print("   scripts on disk:", script_files[:4])
imp = check("import disk script for acme", client.post(f"{P}/scripts/import?client_id={ACME}",
    json={"filename": script_files[0]}), 201)
print("   imported:", imp["name"], "sections:", imp["section_count"])

xml = """<?xml version="1.0" encoding="UTF-8"?>
<script>
  <section title="Identity" type="identity">
    <field name="name" value="Riya"/>
    <field name="company" value="Globex Realty"/>
    <field name="description" value="You help buyers find apartments in Pune and Mumbai."/>
  </section>
  <section title="Objective" type="text">Qualify the buyer's budget and preferred city, then offer a site visit.</section>
</script>"""
gs = check("create globex script", client.post(f"{P}/scripts?client_id={GLOBEX}", json={
    "name": "Globex Buyer Flow", "channel": "all", "language": "en-IN",
    "script_xml": xml, "is_default": True, "voice_speaker": "anushka"}), 201)
print("   globex script sections:", gs["section_count"])

active = check("globex active script", client.get(f"{P}/scripts/active?client_id={GLOBEX}&channel=chat"))
assert active["id"] == gs["id"]
prev = check("preview prompt", client.post(f"{P}/scripts/{gs['id']}/preview?client_id={GLOBEX}&channel=voice"))
print("   rendered voice prompt chars:", prev["character_count"])
assert "Globex Realty" in prev["system_prompt"]

check("list prompts", client.get(f"{P}/prompts?client_id={ACME}"))
check("update prompt", client.put(f"{P}/prompts/sales?client_id={ACME}",
    json={"content": "You are Acme Bank's senior loan advisor. Answer only from company knowledge."}))

# ---- PUBLIC CHAT: lead generation ---------------------------------------
pc = check("public companies", client.get(f"{P}/public/companies"))
print("   public companies:", [c["name"] for c in pc])

sess = check("start chat", client.post(f"{P}/public/chat/start", json={
    "company": ACME, "display_name": "Rahul Sharma",
    "phone": "+919876543210", "email": "rahul@example.com"}), 201)
TOKEN = sess["session_token"]
CONV = sess["conversation_id"]
print("   conversation:", CONV, "| greeting:", sess["greeting"][:60])
HDR = {"X-Chat-Session": TOKEN}

turns = [
    "hi",
    "what is the home loan interest rate and processing fee?",
    "what documents do I need to apply?",
    "I earn 12 LPA and want to apply today for a 50 lakh home loan",
]
for t in turns:
    r = check(f"chat: {t[:42]}", client.post(f"{P}/public/chat/messages", headers=HDR, json={"message": t}))
    print(f"   conf={r['confidence']:.2f} lead={r['lead_status']}/{r['lead_score']} human={r['needs_human']}")
    print(f"      -> {r['reply'][:130]}")

hist = check("chat history", client.get(f"{P}/public/chat/messages", headers=HDR))
print("   history messages:", len(hist))
assert all(m["confidence"] is None for m in hist), "public history must not leak confidence"

# escalation
esc = check("chat: ask for human", client.post(f"{P}/public/chat/messages", headers=HDR,
    json={"message": "I want to talk to a real person please"}))
print("   escalated:", esc["handed_off_to_human"], "|", esc["reply"][:110])
assert esc["handed_off_to_human"]

# ---- STAFF INBOX + RBAC -------------------------------------------------
inbox = check("inbox list", client.get(f"{P}/inbox?client_id={ACME}"))
print("   inbox items:", inbox["total_items"])
conv = inbox["items"][0]
print("   customer_ref:", conv["customer_ref"], "| masked phone:", conv["customer_phone_masked"])
print("   lead:", conv["lead"]["status"], conv["lead"]["score"], "| product:", conv["lead"]["product"])
print("   summary:", (conv["summary"] or "")[:140])
assert conv["customer_phone_masked"] and "9876543210" not in conv["customer_phone_masked"]

queue = check("handoff queue", client.get(f"{P}/inbox/queue?client_id={ACME}"))
print("   queue depth:", len(queue))

detail = check("conversation detail", client.get(f"{P}/inbox/{CONV}?client_id={ACME}"))
print("   suggestions:", detail["suggestions"])
print("   score breakdown:", detail["lead"]["score_breakdown"])

# grant an agent role, then act as that agent
check("grant agent role", client.post(f"{P}/access/roles?client_id={ACME}", json={
    "user_email": "agent@acmebank.com", "role": "agent", "full_name": "Asha Agent",
    "client_id": ACME}), 201)
check("assign to agent", client.post(f"{P}/inbox/{CONV}/assign?client_id={ACME}",
    json={"user_email": "agent@acmebank.com"}))
check("reveal contact (admin)", client.get(f"{P}/inbox/{CONV}/contact?client_id={ACME}"))

# --- switch identity to the agent ---
CURRENT_USER["email"] = "agent@acmebank.com"
me2 = check("me as agent", client.get(f"{P}/access/me"))
print("   agent role:", me2["role"], "| perms:", len(me2["permissions"]))
assert me2["role"] == "agent" and me2["client_id"] == ACME

agent_inbox = check("agent inbox (own leads only)", client.get(f"{P}/inbox"))
print("   agent sees:", agent_inbox["total_items"], "conversation(s)")
assert agent_inbox["total_items"] == 1

r = client.get(f"{P}/inbox/{CONV}/contact")
print(f"{'PASS' if r.status_code == 403 else 'FAIL'} agent BLOCKED from PII reveal -> {r.status_code}")
assert r.status_code == 403

r = client.post(f"{P}/companies", json={"name": "Rogue Corp"})
print(f"{'PASS' if r.status_code == 403 else 'FAIL'} agent BLOCKED from creating company -> {r.status_code}")
assert r.status_code == 403

r = client.get(f"{P}/inbox?client_id={GLOBEX}")
print(f"{'PASS' if r.status_code == 403 else 'FAIL'} agent BLOCKED from other company -> {r.status_code}")
assert r.status_code == 403

check("agent replies", client.post(f"{P}/inbox/{CONV}/reply", json={
    "message": "Hi Rahul, Asha here from Acme Bank. I can get your 50 lakh loan approved this week."}))

# customer sees the agent reply
after = check("customer sees agent reply", client.get(f"{P}/public/chat/messages", headers=HDR))
assert any(m["sender"] == "agent" for m in after)
print("   customer now sees", len(after), "messages incl. agent reply")

# ---- VOICE: reuse existing pipeline -------------------------------------
vs = check("voice status", client.get(f"{P}/voice/status"))
print("   voice:", vs)

call = check("place call for lead", client.post(f"{P}/voice/conversations/{CONV}/call",
    json={"mode": "ai_voice"}), 201)
print("   call:", call["provider"], call["status"], "| sid:", call["call_sid"], "| masked:", call["phone_masked"])
CALLID = call["id"]
print("   active_calls registry populated:", list(mc_stub.active_calls.keys()))
assert mc_stub.active_calls, "existing active_calls registry was not populated"
ctx = list(mc_stub.active_calls.values())[0]
print("   agent context keys:", sorted(ctx.keys()))
assert ctx["xml_sections"], "no script sections handed to the existing pipeline"

turn = check("voice turn", client.post(f"{P}/voice/calls/{CALLID}/turn",
    json={"utterance": "Yes, what is the processing fee on the home loan?"}))
print("   voice reply:", turn["reply"][:120])
print("   tts:", turn["tts"]["provider"], "| lead:", turn["lead_status"], turn["lead_score"])

sync = check("sync call transcript", client.post(f"{P}/voice/calls/{CALLID}/sync"))
print("   synced:", sync)

check("list calls", client.get(f"{P}/voice/conversations/{CONV}/calls"))

# ---- ANALYTICS + ACTIVITY ----------------------------------------------
CURRENT_USER["email"] = "root@platform.io"
an = check("analytics", client.get(f"{P}/analytics?client_id={ACME}"))
print(f"   leads={an['total_leads']} hot={an['hot']} qualified={an['qualified']} "
      f"needs_human={an['needs_human']} calls={an['calls']} containment={an['ai_containment_rate']}%")
print("   channels:", an["channels"], "| agents:", len(an["agents"]))
check("funnel", client.get(f"{P}/analytics/funnel?client_id={ACME}"))

acts = check("activity log", client.get(f"{P}/activity?client_id={ACME}&page_size=200"))
print("   activity rows:", acts["total_items"])
from collections import Counter
print("   actions:", dict(Counter(a["action"] for a in acts["items"]).most_common(12)))

sec = check("security-only activity", client.get(f"{P}/activity?client_id={ACME}&log_type=Security"))
print("   security rows:", sec["total_items"], [a["action"] for a in sec["items"]])
assert any(a["action"] == "lead.pii_revealed" for a in sec["items"]), "PII reveal not audited"

pii_row = [a for a in sec["items"] if a["action"] == "lead.pii_revealed"][0]
print("   PII audit meta (must be redacted):", pii_row["meta"])

check("activity actions list", client.get(f"{P}/activity/actions?client_id={ACME}"))
check("permission catalogue", client.get(f"{P}/access/permissions"))
check("assignable users", client.get(f"{P}/access/assignable-users?client_id={ACME}"))

print("\n" + "=" * 70)
print("ALL FUNCTIONAL TESTS PASSED")
print("=" * 70)
