"""Minimal stubs so the LeadAI social router can be imported and exercised
without MySQL or the identity server. Only stands in for infrastructure —
the social code under test is the real thing."""
import sys, types
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# --- base.Base / database.* (normally MySQL) -> in-memory SQLite ---
Base = declarative_base()
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
SessionLocalAdmin = sessionmaker(bind=engine, autoflush=False, autocommit=False)

import core  # the real package; only its base/database/auth submodules are stubbed

base_mod = types.ModuleType("core.base"); base_mod.Base = Base
db_mod = types.ModuleType("core.database")
db_mod.engine_admin = engine; db_mod.SessionLocalAdmin = SessionLocalAdmin
db_mod.engine = engine; db_mod.SessionLocal = SessionLocalAdmin
sys.modules["core.base"] = base_mod; sys.modules["core.database"] = db_mod
core.base, core.database = base_mod, db_mod

auth_mod = types.ModuleType("core.auth")
def get_current_user(): return "tester@example.com"
async def get_current_user_websocket(ws): return "tester@example.com"
auth_mod.get_current_user = get_current_user
auth_mod.get_current_user_websocket = get_current_user_websocket
sys.modules["core.auth"] = auth_mod
core.auth = auth_mod
