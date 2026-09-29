#!/usr/bin/env python3
"""
Provision a demo tenant end to end, then prove it works.

    python demo/seed_demo_company.py --demo kestrel_homes --token "$TOKEN"
    python demo/seed_demo_company.py --demo nexa_finserv  --token "$TOKEN"

Demos available (each is a set of three files next to this script):

    nexa_finserv    digital lending NBFC   (persona Aanya)
    kestrel_homes   real-estate developer  (persona Kabir)

What it does, in order:

    1.  Find or create the company (a `Clients` row — LeadAI has no separate
        tenant table). New companies are created with every channel feature
        enabled so the demo can use WhatsApp, Instagram, Facebook, LinkedIn and voice.
    2.  Upload the knowledge base and wait for indexing to finish.
    3.  Upload the agent script and set it as the company default.
    4.  Write the five prompt overrides.
    5.  (--grant-plan) Give the company a free demo plan that includes every channel.
        Without an active plan, WhatsApp / Instagram / Facebook / LinkedIn and voice
        calls are blocked by billing. Web chat works without one.
    6.  Run a scripted multi-turn chat against the PUBLIC widget endpoint and
        print each reply with its retrieval confidence.

Step 6 is the point of this script. Seeding is easy; the thing you actually
need before a demo is evidence that retrieval is answering rather than
escalating. Each transcript includes a context-dependent follow-up ("and the
processing fee on that?") because that is the turn that used to fail — see
LeadAI/services/memory.py for why.

Nothing here is destructive. Re-running is safe: the company is looked up by
name before being created, the script is versioned rather than overwritten, and
prompt writes are idempotent PUTs. The knowledge base is the one exception —
pass --skip-kb on a re-run if you do not want a second copy of the document.

Auth: every staff endpoint needs a bearer token from your identity server, and
the token must belong to a SUPER ADMIN (role `Admin`): companies are managed by
the platform team only. Its email must be in LEADAI_BOOTSTRAP_ADMINS or already
hold a platform-admin grant.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("This script needs `requests`:  pip install requests")


HERE = Path(__file__).resolve().parent

# Every channel feature a company can have switched on (see COMPANY_GATED_FEATURES).
ALL_FEATURES = [
    "social.facebook", "social.instagram", "social.whatsapp", "social.linkedin",
    "voice_agent", "email_marketing",
]
ALL_CHANNELS = ["whatsapp", "instagram", "facebook", "linkedin"]

DEMOS = {
    "nexa_finserv": {
        "company": "Nexa Finserv",
        "email": "care@nexafinserv.example",
        "phone": "+911800419627",
        "description": (
            "Demo tenant — digital lending NBFC offering personal, home and business "
            "loans across North India."
        ),
        "files": "nexa_finserv",
        "script_name": "Nexa Finserv — Aanya (Loan Advisor)",
        "script_description": "Demo persona: Aanya, senior loan advisor. Chat + voice.",
        "persona": "Aanya",
        "voice_gender": "female",
        "voice_speaker": "priya",
        "turns": [
            "hi",
            "I'm looking at a personal loan, I'm salaried",
            "what interest rate can I get?",
            "and the processing fee on that?",
            "my take home is around 62,000 a month, I need about 6 lakh",
            "what documents will you need from me?",
            "how long does it take to get the money?",
            "can I foreclose it early without a penalty?",
            "do you do credit cards as well?",
            "ok, can someone call me tomorrow evening?",
        ],
    },
    "kestrel_homes": {
        "company": "Kestrel Homes",
        "email": "sales@kestrelhomes.example",
        "phone": "+911725550142",
        "description": (
            "Demo tenant — residential and commercial real-estate developer in Mohali, "
            "Zirakpur and New Chandigarh: apartments, plots and shops."
        ),
        "files": "kestrel_homes",
        "script_name": "Kestrel Homes — Kabir (Property Advisor)",
        "script_description": "Demo persona: Kabir, property advisor. Chat + voice.",
        "persona": "Kabir",
        # Real estate: the docs recommend 0.25-0.35, because a buyer asking a broad question
        # ("a 3BHK in Mohali") should not be escalated just because it matches several products.
        "settings": {
            "handoff_threshold": 0.3,
            "retrieval_top_k": 6,
            "widget_greeting": "Hi! I'm Kabir from Kestrel Homes. Looking for an apartment, a plot or a commercial space?",
        },
        "voice_gender": "male",
        "voice_speaker": "kabir",
        "turns": [
            "hi",
            "I'm looking for a 3BHK in Mohali",
            "what's the price?",
            "and the possession date for that?",
            "my budget is around 1.2 crore, it's for my family to live in",
            "what's the payment plan?",
            "do you help with a home loan?",
            "can I get a discount if I book this month?",
            "ok, can I visit on Saturday morning?",
            "Saturday 11am works, we will be 3 people",
        ],
    },
}


# --------------------------------------------------------------------------- #
# tiny HTTP helper
# --------------------------------------------------------------------------- #
class Api:
    def __init__(self, base: str, prefix: str, token: str | None, timeout: int = 120):
        self.root = f"{base.rstrip('/')}{prefix}"
        self.timeout = timeout
        self.s = requests.Session()
        if token:
            self.s.headers["Authorization"] = f"Bearer {token}"

    def _call(self, method: str, path: str, **kw):
        url = f"{self.root}{path}"
        r = self.s.request(method, url, timeout=self.timeout, **kw)
        if r.status_code >= 400:
            raise SystemExit(
                f"\n{method} {url} -> {r.status_code}\n{r.text[:800]}\n\n"
                "If this is a 401/403: check the bearer token, and that it belongs to a "
                "super admin (role Admin; email in LEADAI_BOOTSTRAP_ADMINS)."
            )
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return r.text

    get = lambda self, p, **k: self._call("GET", p, **k)          # noqa: E731
    post = lambda self, p, **k: self._call("POST", p, **k)        # noqa: E731
    put = lambda self, p, **k: self._call("PUT", p, **k)          # noqa: E731
    patch = lambda self, p, **k: self._call("PATCH", p, **k)      # noqa: E731
    delete = lambda self, p, **k: self._call("DELETE", p, **k)    # noqa: E731


def say(step: str, detail: str = "") -> None:
    print(f"  {step:<34}{detail}", flush=True)


def head(title: str) -> None:
    print(f"\n{title}\n" + "-" * 72, flush=True)


# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #
def ensure_company(api: Api, demo: dict) -> dict:
    head("1. Company")
    for c in api.get("/companies") or []:
        if (c.get("name") or "").strip().lower() == demo["company"].lower():
            say("already exists", f"{c['name']}  id={c['id']}")
            return c

    company = api.post(
        "/companies",
        json={
            "name": demo["company"],
            "email": demo["email"],
            "phone_number": demo["phone"],
            "description": demo["description"],
            "permissions": ALL_FEATURES,
        },
    )
    say("created", f"{company['name']}  id={company['id']}")
    say("features enabled", ", ".join(ALL_FEATURES))
    return company


def apply_settings(api: Api, company_id: str, demo: dict) -> None:
    settings = demo.get("settings")
    if not settings:
        return
    head("1b. Company settings")
    out = api.put(f"/companies/{company_id}/settings", params={"client_id": company_id}, json=settings)
    say("handoff threshold", str(out.get("effective_handoff_threshold")))
    say("retrieval top-k", str(out.get("effective_retrieval_top_k")))


def upload_knowledge(api: Api, company_id: str, demo: dict, skip: bool) -> None:
    head("2. Knowledge base")
    if skip:
        say("skipped", "--skip-kb")
        return
    kb_file = HERE / f"{demo['files']}_knowledge_base.md"
    if not kb_file.exists():
        raise SystemExit(f"Missing {kb_file}")

    params = {"client_id": company_id}
    # Replace, don't duplicate: a second copy of the same document doubles every chunk
    # and skews retrieval. Remove any earlier upload of this file first.
    for old in api.get("/knowledge/documents", params=params) or []:
        if old.get("file_name") == kb_file.name:
            api.delete(f"/knowledge/documents/{old['id']}", params=params)
            say("replaced earlier upload", f"id={old['id']}")
    with kb_file.open("rb") as fh:
        doc = api.post(
            "/knowledge/documents",
            params=params,
            files={"file": (kb_file.name, fh, "text/markdown")},
        )
    say("uploaded", f"{kb_file.name}  id={doc.get('id')}  status={doc.get('status')}")

    # Poll rather than sleeping a fixed interval: embedding takes ~2s with an OpenAI
    # key and ~0.2s on the offline fallback.
    for _ in range(30):
        stats = api.get("/knowledge/stats", params=params) or {}
        chunks = stats.get("chunks") or 0
        if chunks:
            say("indexed", f"{chunks} chunks, {stats.get('documents', '?')} document(s), "
                           f"embeddings={stats.get('embedding_model')}")
            return
        time.sleep(1)
    say("warning", "no chunks reported after 30s — check worker logs")


def upload_script(api: Api, company_id: str, demo: dict) -> dict:
    head("3. Agent script")
    script_file = HERE / f"{demo['files']}_agent_script.xml"
    if not script_file.exists():
        raise SystemExit(f"Missing {script_file}")

    params = {"client_id": company_id}
    body = {
        "name": demo["script_name"],
        "description": demo["script_description"],
        "channel": "all",
        "language": "en-IN",
        "script_xml": script_file.read_text(encoding="utf-8"),
        "is_default": True,
        "voice_gender": demo["voice_gender"],
        "voice_speaker": demo["voice_speaker"],
        "multi_stt": True,
    }
    existing = next(
        (x for x in api.get("/scripts", params=params) or [] if x.get("name") == demo["script_name"]),
        None,
    )
    if existing:
        script = api.patch(f"/scripts/{existing['id']}", params=params, json=body)
        say("updated in place", f"v{script.get('version')}  id={script['id']}")
    else:
        script = api.post("/scripts", params=params, json=body)
        say("created", f"v{script.get('version')}  id={script['id']}")

    api.post(f"/scripts/{script['id']}/set-default", params=params)
    say("set as default", "channel=all")

    # The preview endpoint renders the exact prompt the model will receive.
    for channel in ("chat", "voice"):
        pv = api.post(f"/scripts/{script['id']}/preview", params={**params, "channel": channel})
        say(f"preview ({channel})", f"{pv.get('character_count')} chars, {len(pv.get('sections') or [])} sections")

    return script


def write_prompts(api: Api, company_id: str, demo: dict) -> None:
    head("4. Prompt overrides")
    data = json.loads((HERE / f"{demo['files']}_prompts.json").read_text(encoding="utf-8"))
    params = {"client_id": company_id}
    for key, content in data.items():
        if key.startswith("_"):
            continue
        api.put(f"/prompts/{key}", params=params, json={"content": content})
        say(key, f"{len(content)} chars")


def grant_demo_plan(api: Api, company_id: str, demo: dict) -> None:
    head("5. Demo plan (all channels)")
    plan_name = f"Demo — all channels ({demo['company']})"
    plans = api.get("/admin/billing/plans") or []
    plan = next((p for p in plans if p.get("name") == plan_name and p.get("is_active")), None)
    if plan is None:
        plan = api.post(
            "/admin/billing/plans",
            json={
                "name": plan_name,
                "plan_type": "standard",
                "included_minutes": 500,
                "validity_days": 30,
                "price": 0,
                "addon_channels": ALL_CHANNELS,
                # Visible to this company only, so it never shows up for other tenants.
                "target_client_id": company_id,
                "description": "Free demo plan: 500 voice minutes and every social channel.",
            },
        )
        say("plan created", f"id={plan['id']}")
    else:
        say("plan exists", f"id={plan['id']}")

    summary = next(
        (s for s in api.get("/admin/billing/clients-summary") or [] if s.get("client_id") == company_id),
        None,
    )
    if summary and summary.get("active_recharge"):
        say("already has a plan", summary["active_recharge"].get("plan_name_snapshot", ""))
        return
    r = api.post("/admin/billing/recharge-client", json={"client_id": company_id, "plan_template_id": plan["id"]})
    say("plan granted", f"{r.get('purchased_minutes')} min, channels={r.get('active_channels')}, expires {r.get('expires_at')}")


def run_demo_chat(base: str, prefix: str, company_id: str, demo: dict) -> None:
    head("6. Live chat smoke test  (public widget endpoints, no auth)")
    pub = Api(base, f"{prefix}/public", token=None)
    persona = demo["persona"]

    session = pub.post(
        "/chat/start",
        json={
            "company": company_id,
            "display_name": "Demo Customer",
            "phone": "+919876543210",
            "channel": "web",
            "language": "en-IN",
        },
    )
    token = session["session_token"]
    say("session", session["conversation_id"])
    print()

    chat = requests.Session()
    chat.headers["X-Chat-Session"] = token
    low_confidence = 0
    last = {}

    for turn in demo["turns"]:
        r = chat.post(
            f"{base.rstrip('/')}{prefix}/public/chat/messages",
            json={"message": turn},
            timeout=120,
        )
        if r.status_code >= 400:
            print(f"  FAILED {turn!r} -> {r.status_code} {r.text[:300]}")
            break
        d = r.json()
        last = d
        conf = d.get("confidence", 0)
        flag = ""
        if d.get("handed_off_to_human"):
            flag = "  [HANDED OFF]"
            low_confidence += 1
        elif conf < 0.35:
            flag = "  [low confidence]"
            low_confidence += 1

        print(f"  customer  {turn}")
        print(f"  {persona:<9} {d.get('reply', '')}")
        print(f"            conf={conf:.2f}  score={d.get('lead_score')}  status={d.get('lead_status')}{flag}\n")

    head("Result")
    print(f"  final lead: status={last.get('lead_status')} score={last.get('lead_score')}")
    if low_confidence == 0:
        print("  All turns answered from the knowledge base. Ready to demo.\n")
    else:
        print(
            f"  {low_confidence} turn(s) escalated or scored low.\n"
            "  Usually one of: the KB never finished indexing (re-check step 2),\n"
            "  OPENAI_API_KEY is unset or cannot call embeddings (offline retrieval),\n"
            "  or the question genuinely is not covered — which is correct behaviour\n"
            "  (a discount request should be handed to a human).\n"
            "  Inspect with: POST /api/leadai/knowledge/test\n"
        )


# --------------------------------------------------------------------------- #
def main() -> None:
    p = argparse.ArgumentParser(description="Seed a LeadAI demo tenant.")
    p.add_argument("--demo", choices=sorted(DEMOS), default="nexa_finserv")
    p.add_argument("--base", default=os.getenv("LEADAI_BASE", "http://localhost:5050"))
    p.add_argument("--prefix", default=os.getenv("LEADAI_API_PREFIX", "/api/leadai"))
    p.add_argument("--token", default=os.getenv("LEADAI_TOKEN"), help="Super-admin bearer token")
    p.add_argument("--skip-kb", action="store_true", help="Don't re-upload the knowledge base")
    p.add_argument("--grant-plan", action="store_true", help="Give the company a free all-channel demo plan")
    p.add_argument("--chat-only", action="store_true", help="Only run the smoke test")
    args = p.parse_args()
    demo = DEMOS[args.demo]

    if not args.token:
        sys.exit("Need a super-admin token: --token '...' or export LEADAI_TOKEN=...")

    api = Api(args.base, args.prefix, args.token)

    health = api.get("/health")
    print(f"\nConnected to {args.base}{args.prefix}  —  {health}")

    company = ensure_company(api, demo)
    cid = company["id"]

    if not args.chat_only:
        apply_settings(api, cid, demo)
        upload_knowledge(api, cid, demo, args.skip_kb)
        upload_script(api, cid, demo)
        write_prompts(api, cid, demo)
        if args.grant_plan:
            grant_demo_plan(api, cid, demo)

    run_demo_chat(args.base, args.prefix, cid, demo)

    print(f"  Company id for the widget embed and for /voice calls:\n    {cid}\n")


if __name__ == "__main__":
    main()
