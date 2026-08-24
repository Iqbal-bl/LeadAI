#!/usr/bin/env python3
"""
Provision the Nexa Finserv demo tenant end to end, then prove it works.

    python demo/seed_demo_company.py --token "$TOKEN"

What it does, in order:

    1.  Find or create the company (a `Clients` row — LeadAI has no separate
        tenant table, so this company is immediately usable by the batch
        features that already reference Batch.ClientId).
    2.  Upload the knowledge base and wait for indexing to finish.
    3.  Upload the agent script and set it as the company default.
    4.  Write the five prompt overrides.
    5.  Run a scripted multi-turn chat against the PUBLIC widget endpoint and
        print each reply with its retrieval confidence.

Step 5 is the point of this script. Seeding is easy; the thing you actually
need before a demo is evidence that retrieval is answering rather than
escalating. The transcript includes a deliberate context-dependent follow-up
("and the processing fee on that?") because that is the turn that used to fail —
see LeadAI/services/memory.py for why.

Nothing here is destructive. Re-running is safe: the company is looked up by
name before being created, the script is versioned rather than overwritten, and
prompt writes are idempotent PUTs. The knowledge base is the one exception —
pass --skip-kb on a re-run if you do not want a second copy of the document.

Auth: every staff endpoint needs a bearer token from your identity server, and
the email in that token must be in LEADAI_BOOTSTRAP_ADMINS (or already hold a
platform-admin grant) for company.manage to pass.
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

COMPANY_NAME = "Nexa Finserv"
COMPANY_EMAIL = "care@nexafinserv.example"
COMPANY_PHONE = "+911800419627"
COMPANY_DESC = (
    "Demo tenant — digital lending NBFC offering personal, home and business "
    "loans across North India."
)

KB_FILE = HERE / "nexa_finserv_knowledge_base.md"
SCRIPT_FILE = HERE / "nexa_finserv_agent_script.xml"
PROMPTS_FILE = HERE / "nexa_finserv_prompts.json"

SCRIPT_NAME = "Nexa Finserv — Aanya (Loan Advisor)"

# The demo transcript. Turn 2 is the context-dependent follow-up.
DEMO_TURNS = [
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
]


# --------------------------------------------------------------------------- #
# tiny HTTP helper
# --------------------------------------------------------------------------- #
class Api:
    def __init__(self, base: str, prefix: str, token: str | None, timeout: int = 90):
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
                "If this is a 401/403: check the bearer token, and that its email "
                "is listed in LEADAI_BOOTSTRAP_ADMINS."
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


def say(step: str, detail: str = "") -> None:
    print(f"  {step:<34}{detail}", flush=True)


def head(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n" + "─" * 72, flush=True)


# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #
def ensure_company(api: Api) -> dict:
    head("1. Company")
    for c in api.get("/companies") or []:
        if (c.get("name") or "").strip().lower() == COMPANY_NAME.lower():
            say("already exists", f"{c['name']}  id={c['id']}")
            return c

    company = api.post(
        "/companies",
        json={
            "name": COMPANY_NAME,
            "email": COMPANY_EMAIL,
            "phone_number": COMPANY_PHONE,
            "description": COMPANY_DESC,
        },
    )
    say("created", f"{company['name']}  id={company['id']}")
    return company


def upload_knowledge(api: Api, company_id: str, skip: bool) -> None:
    head("2. Knowledge base")
    if skip:
        say("skipped", "--skip-kb")
        return
    if not KB_FILE.exists():
        raise SystemExit(f"Missing {KB_FILE}")

    params = {"company_id": company_id}
    with KB_FILE.open("rb") as fh:
        doc = api.post(
            "/knowledge/documents",
            params=params,
            files={"file": (KB_FILE.name, fh, "text/markdown")},
        )
    say("uploaded", f"{KB_FILE.name}  id={doc.get('id')}")

    # Indexing is asynchronous. Poll rather than sleeping a fixed interval —
    # embedding a 12KB document takes ~2s with an OpenAI key and ~0.2s on the
    # offline fallback, and a fixed sleep is wrong for one of those cases.
    for attempt in range(30):
        stats = api.get("/knowledge/stats", params=params) or {}
        chunks = stats.get("chunk_count") or stats.get("chunks") or 0
        if chunks:
            say("indexed", f"{chunks} chunks, {stats.get('document_count', '?')} document(s)")
            return
        time.sleep(1)
    say("warning", "no chunks reported after 30s — check worker logs")


def upload_script(api: Api, company_id: str) -> dict:
    head("3. Agent script")
    if not SCRIPT_FILE.exists():
        raise SystemExit(f"Missing {SCRIPT_FILE}")

    params = {"company_id": company_id}
    script = api.post(
        "/scripts",
        params=params,
        json={
            "name": SCRIPT_NAME,
            "description": "Demo persona: Aanya, senior loan advisor. Chat + voice.",
            "channel": "all",
            "language": "en-IN",
            "script_xml": SCRIPT_FILE.read_text(encoding="utf-8"),
            "is_default": True,
            "voice_gender": "female",
            "voice_speaker": "anushka",
            "multi_stt": True,
        },
    )
    say("created", f"v{script.get('version')}  id={script['id']}")

    api.post(f"/scripts/{script['id']}/set-default", params=params)
    say("set as default", "channel=all")

    # The preview endpoint renders the exact prompt the model will receive.
    # Worth printing the size: a voice prompt over ~8000 characters noticeably
    # slows the first syllable of a call.
    for channel in ("chat", "voice"):
        pv = api.post(f"/scripts/{script['id']}/preview", params={**params, "channel": channel})
        say(f"preview ({channel})", f"{pv.get('character_count')} chars, {len(pv.get('sections') or [])} sections")

    return script


def write_prompts(api: Api, company_id: str) -> None:
    head("4. Prompt overrides")
    data = json.loads(PROMPTS_FILE.read_text(encoding="utf-8"))
    params = {"company_id": company_id}
    for key, content in data.items():
        if key.startswith("_"):
            continue
        api.put(f"/prompts/{key}", params=params, json={"content": content})
        say(key, f"{len(content)} chars")


def run_demo_chat(base: str, prefix: str, company_id: str) -> None:
    head("5. Live chat smoke test  (public widget endpoints, no auth)")
    pub = Api(base, f"{prefix}/public", token=None)

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
    chat.headers["Authorization"] = f"Bearer {token}"
    low_confidence = 0

    for turn in DEMO_TURNS:
        r = chat.post(
            f"{base.rstrip('/')}{prefix}/public/chat/messages",
            json={"message": turn},
            timeout=90,
        )
        if r.status_code >= 400:
            print(f"  \033[31mFAILED\033[0m {turn!r} -> {r.status_code} {r.text[:300]}")
            break
        d = r.json()
        conf = d.get("confidence", 0)
        flag = ""
        if d.get("handed_off_to_human"):
            flag = "  \033[33m[HANDED OFF]\033[0m"
            low_confidence += 1
        elif conf < 0.35:
            flag = "  \033[33m[low confidence]\033[0m"
            low_confidence += 1

        print(f"  \033[36mcustomer\033[0m  {turn}")
        print(f"  \033[32mAanya\033[0m     {d.get('reply', '')}")
        print(
            f"            conf={conf:.2f}  score={d.get('lead_score')}  "
            f"status={d.get('lead_status')}{flag}\n"
        )

    head("Result")
    if low_confidence == 0:
        print("  All turns answered from the knowledge base. Ready to demo.\n")
    else:
        print(
            f"  {low_confidence} turn(s) escalated or scored low.\n"
            "  Usually one of: the KB never finished indexing (re-check step 2),\n"
            "  OPENAI_API_KEY is unset so you are on the offline extractive path,\n"
            "  or the question genuinely is not covered — which is correct behaviour.\n"
            "  Inspect with: POST /api/leadai/knowledge/test\n"
        )


# --------------------------------------------------------------------------- #
def main() -> None:
    p = argparse.ArgumentParser(description="Seed the Nexa Finserv demo tenant.")
    p.add_argument("--base", default=os.getenv("LEADAI_BASE", "http://localhost:5050"))
    p.add_argument("--prefix", default=os.getenv("LEADAI_API_PREFIX", "/api/leadai"))
    p.add_argument("--token", default=os.getenv("LEADAI_TOKEN"), help="Staff bearer token")
    p.add_argument("--skip-kb", action="store_true", help="Don't re-upload the knowledge base")
    p.add_argument("--chat-only", action="store_true", help="Only run the smoke test")
    args = p.parse_args()

    if not args.token:
        sys.exit("Need a staff token: --token '...' or export LEADAI_TOKEN=...")

    api = Api(args.base, args.prefix, args.token)

    health = api.get("/health")
    print(f"\nConnected to {args.base}{args.prefix}  —  {health}")

    company = ensure_company(api)
    cid = company["id"]

    if not args.chat_only:
        upload_knowledge(api, cid, args.skip_kb)
        upload_script(api, cid)
        write_prompts(api, cid)

    run_demo_chat(args.base, args.prefix, cid)

    print(f"  Company id for the widget embed and for /voice calls:\n    {cid}\n")


if __name__ == "__main__":
    main()
