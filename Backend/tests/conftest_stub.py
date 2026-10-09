"""Minimal stubs so the LeadAI social router can be imported and exercised
without MySQL or the identity server. Only stands in for infrastructure —
the social code under test is the real thing."""
import os
import sys, types

# The local .env is shared with the live dev server, and gets flipped to
# ENGINE_MODE=enforce / TRIAGE_MODE=enforce for real-call testing. Set before
# LeadAI.config's load_dotenv() runs (override=False, so a value already in
# os.environ wins) so the suite stays hermetic regardless of what the running
# server was last configured to. A test that wants enforce mode on purpose
# sets its own fake settings object, same as it already does for every other
# config value.
os.environ["ENGINE_MODE"] = "off"
os.environ["TRIAGE_MODE"] = "off"

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

# --- base.Base / database.* (normally MySQL) -> in-memory SQLite ---
Base = declarative_base()
# StaticPool: one shared connection, so requests served on other threads (FastAPI runs
# sync endpoints in a thread pool) see the same in-memory database.
engine = create_engine(
    "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
SessionLocalAdmin = sessionmaker(bind=engine, autoflush=False, autocommit=False)

import core  # the real package; only its base/database/auth submodules are stubbed

base_mod = types.ModuleType("core.base"); base_mod.Base = Base
db_mod = types.ModuleType("core.database")
db_mod.engine_admin = engine; db_mod.SessionLocalAdmin = SessionLocalAdmin
db_mod.engine = engine; db_mod.SessionLocal = SessionLocalAdmin
def get_dynamic_db():
    db = SessionLocalAdmin()
    try:
        yield db
    finally:
        db.close()

def get_db_from_headers(*a, **k):
    db = SessionLocalAdmin()
    try:
        yield db
    finally:
        db.close()

db_mod.get_dynamic_db = get_dynamic_db
db_mod.get_db_from_headers = get_db_from_headers
sys.modules["core.base"] = base_mod; sys.modules["core.database"] = db_mod
core.base, core.database = base_mod, db_mod

auth_mod = types.ModuleType("core.auth")
def get_current_user(): return "tester@example.com"
def get_current_user_optional(): return "tester@example.com"
def get_current_client_admin(): return "tester@example.com"
def get_current_super_admin(): return "superadmin@example.com"
def get_current_user_email(): return "tester@example.com"
def get_password_hash(p): return "mock_hash"
def validate_twilio_request(r): return True
def create_stream_token(*a, **k): return "mock_token"
def verify_stream_token(*a, **k): return {"call_sid": "mock_call"}
async def get_current_user_websocket(ws): return "tester@example.com"
auth_mod.get_current_user = get_current_user
auth_mod.get_current_user_optional = get_current_user_optional
auth_mod.get_current_client_admin = get_current_client_admin
auth_mod.get_current_super_admin = get_current_super_admin
auth_mod.get_current_user_email = get_current_user_email
auth_mod.get_password_hash = get_password_hash
auth_mod.validate_twilio_request = validate_twilio_request
auth_mod.create_stream_token = create_stream_token
auth_mod.verify_stream_token = verify_stream_token
auth_mod.get_current_user_websocket = get_current_user_websocket
auth_mod.create_access_token = lambda subject, *a, **k: f"mock_token_{subject}"
auth_mod._decode_token = lambda token, *a, **k: {"sub": "tester@example.com", "type": "access"}
sys.modules["core.auth"] = auth_mod
core.auth = auth_mod
