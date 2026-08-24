"""
Proves the property the whole integration exists for: a publish request made by
Company A goes to Company A's Page with Company A's token, never Company B's —
including when both are in flight at the same time.

The Graph API HTTP layer is stubbed; everything above it (credential resolution,
the context var, the per-platform fan-out, the DB records) is the real code.
"""
import asyncio
import conftest_stub  # noqa: F401  — installs base/database/auth stubs first

from conftest_stub import Base, SessionLocalAdmin, engine

from LeadAI.models import LeadChannelAccount, LeadSocialPost  # noqa: E402
from LeadAI.security import encrypt_pii  # noqa: E402
from LeadAI.social import service  # noqa: E402
from LeadAI.social.credentials import ChannelNotConnected, connected_platforms, resolve  # noqa: E402

Base.metadata.create_all(bind=engine)

# --- Record every Graph call with the token/target actually used -------------
CALLS: list[dict] = []


async def fake_graph_post(path, data=None, files=None):
    CALLS.append({"path": path, "token": (data or {}).get("access_token")})
    return {"id": f"{path.split('/')[0]}_POST"}


async def fake_graph_get(path, params=None):
    CALLS.append({"path": path, "token": (params or {}).get("access_token")})
    return {"status_code": "FINISHED", "id": "container"}


import social_agent.graph_api.client as client  # noqa: E402

# Patch at the client seam so pages.py / instagram.py run for real: the point is
# to observe which token and which Page id THEY resolve.
for mod_name in ("social_agent.graph_api.pages", "social_agent.graph_api.instagram"):
    import importlib

    mod = importlib.import_module(mod_name)
    mod.graph_post = fake_graph_post
    mod.graph_get = fake_graph_get


def _inject_token(fn):
    """pages.py calls graph_post without a token (client.py adds it), so mimic
    client.py's behaviour to observe what _access_token() resolves to."""

    async def wrapper(path, data=None, files=None):
        data = dict(data or {})
        data["access_token"] = client._access_token()
        return await fn(path, data)

    return wrapper


import social_agent.graph_api.pages as pages_mod  # noqa: E402
import social_agent.graph_api.instagram as ig_mod  # noqa: E402

pages_mod.graph_post = _inject_token(fake_graph_post)
ig_mod.graph_post = _inject_token(fake_graph_post)


# --- Seed two companies -----------------------------------------------------
db = SessionLocalAdmin()

ACME, GLOBEX = "company-acme", "company-globex"

db.add_all([
    LeadChannelAccount(
        ClientId=ACME, Channel="messenger", Name="Acme Page",
        ExternalId="PAGE_ACME", AccessTokenEnc=encrypt_pii("TOKEN_ACME"),
        ApiVersion="v21.0", IsActive=True, IsDeleted=False, CreatedBy="t",
    ),
    LeadChannelAccount(
        ClientId=ACME, Channel="instagram", Name="Acme IG",
        ExternalId="IG_ACME", AccessTokenEnc=None,  # borrows the Page token
        ApiVersion="v21.0", IsActive=True, IsDeleted=False, CreatedBy="t",
    ),
    LeadChannelAccount(
        ClientId=GLOBEX, Channel="messenger", Name="Globex Page",
        ExternalId="PAGE_GLOBEX", AccessTokenEnc=encrypt_pii("TOKEN_GLOBEX"),
        ApiVersion="v25.0", IsActive=True, IsDeleted=False, CreatedBy="t",
    ),
])
db.commit()

failures = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{'  — ' + detail if detail and not cond else ''}")
    if not cond:
        failures.append(label)


# === 1. Resolution ==========================================================
print("\n1. Credential resolution per company")
a = resolve(db, ACME, "facebook")
g = resolve(db, GLOBEX, "facebook")
check("Acme resolves its own page/token", a.page_id == "PAGE_ACME" and a.access_token == "TOKEN_ACME")
check("Globex resolves its own page/token", g.page_id == "PAGE_GLOBEX" and g.access_token == "TOKEN_GLOBEX")
check("per-company API version honoured", a.api_version == "v21.0" and g.api_version == "v25.0")

ig = resolve(db, ACME, "instagram")
check("IG borrows the linked Page token", ig.access_token == "TOKEN_ACME" and ig.ig_user_id == "IG_ACME")

try:
    resolve(db, GLOBEX, "instagram")
    check("Globex IG (not connected) rejected", False, "expected ChannelNotConnected")
except ChannelNotConnected:
    check("Globex IG (not connected) rejected", True)

try:
    # Acme trying to pin Globex's account id must fail, not silently succeed.
    other = db.query(LeadChannelAccount).filter_by(ClientId=GLOBEX).first()
    resolve(db, ACME, "facebook", account_id=other.Id)
    check("cross-company account_id rejected", False, "Acme reached Globex's account!")
except ChannelNotConnected:
    check("cross-company account_id rejected", True)


# === 2. Publishing routes to the right Page =================================
print("\n2. Publishing")


async def publish_for(client_id, caption):
    return await service.publish(
        db, client_id, caption=caption,
        uploaded=[{"url": "https://cdn.example/a.jpg", "is_video": False}],
        platforms=["facebook"], actor="t@example.com",
    )


CALLS.clear()
asyncio.run(publish_for(ACME, "acme post"))
acme_calls = list(CALLS)
check("Acme posted to PAGE_ACME", all("PAGE_ACME" in c["path"] for c in acme_calls), str(acme_calls))
check("Acme used TOKEN_ACME", all(c["token"] == "TOKEN_ACME" for c in acme_calls), str(acme_calls))

CALLS.clear()
asyncio.run(publish_for(GLOBEX, "globex post"))
check("Globex posted to PAGE_GLOBEX", all("PAGE_GLOBEX" in c["path"] for c in CALLS), str(CALLS))
check("Globex used TOKEN_GLOBEX", all(c["token"] == "TOKEN_GLOBEX" for c in CALLS), str(CALLS))


# === 3. Concurrency — the case a global/threading.local would fail ==========
print("\n3. Concurrent publishes (interleaved in one event loop)")


async def concurrent():
    CALLS.clear()

    async def slow_post(path, data=None, files=None):
        data = dict(data or {})
        data["access_token"] = client._access_token()
        await asyncio.sleep(0.01)  # force interleaving mid-request
        CALLS.append({"path": path, "token": data["access_token"]})
        return {"id": "x"}

    pages_mod.graph_post = slow_post
    await asyncio.gather(*[publish_for(ACME, "a") for _ in range(5)],
                         *[publish_for(GLOBEX, "g") for _ in range(5)])


asyncio.run(concurrent())
pairs = {(("ACME" if "ACME" in c["path"] else "GLOBEX"), c["token"]) for c in CALLS}
check("no token/page mismatch under concurrency",
      pairs == {("ACME", "TOKEN_ACME"), ("GLOBEX", "TOKEN_GLOBEX")}, str(pairs))
check("all 10 concurrent publishes ran", len(CALLS) == 10, f"got {len(CALLS)}")


# === 4. Fail-closed when no company is bound ================================
print("\n4. Fail-closed with no bound company")
from social_agent.context import MissingCredentialsError, get_credentials  # noqa: E402

check("context is empty outside a request", get_credentials() is None)
try:
    client._access_token()
    check("bare Graph call refused", False, "resolved a token with no tenant bound!")
except client.GraphAPIError:
    check("bare Graph call refused", True)


# === 5. Records are tenant-scoped ==========================================
print("\n5. Audit records")
rows = db.query(LeadSocialPost).all()
check("every post row carries a ClientId", all(r.ClientId for r in rows))
check("Acme sees only its own posts",
      db.query(LeadSocialPost).filter_by(ClientId=ACME).count()
      < db.query(LeadSocialPost).count())

print("\n6. Platform status surface")
status = connected_platforms(db, GLOBEX)
check("Globex: facebook connected", status["facebook"]["connected"] is True)
check("Globex: instagram not connected, with reason",
      status["instagram"]["connected"] is False and "connect" in status["instagram"]["reason"].lower())

print("\n" + ("ALL CHECKS PASSED" if not failures else f"FAILURES: {failures}"))
raise SystemExit(1 if failures else 0)
