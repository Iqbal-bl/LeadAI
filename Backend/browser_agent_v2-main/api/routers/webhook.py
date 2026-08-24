
"""Meta Webhook Router — handles Meta webhook verification (GET) and event delivery (POST).
Integrated directly into FastAPI so webhooks and all other APIs run on the same port.
"""

import hmac
import os
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse

router = APIRouter(prefix="/meta", tags=["webhook"])


@router.get("/webhook", response_class=PlainTextResponse)
async def verify_webhook(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
):
    verify_token = os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "")
    if (
        verify_token
        and hub_mode == "subscribe"
        and hmac.compare_digest(hub_verify_token, verify_token)
    ):
        print("[webhook] Meta verification succeeded.")
        return hub_challenge

    raise HTTPException(status_code=403, detail="Verification failed.")


@router.post("/webhook", response_class=PlainTextResponse)
async def receive_webhook(request: Request):
    try:
        payload = await request.json()
        entry_count = len(payload.get("entry", [])) if isinstance(payload, dict) else 0
        print(f"[webhook] Received Meta event with {entry_count} entr{'y' if entry_count == 1 else 'ies'}.")
    except Exception:
        print("[webhook] Received a non-JSON Meta event.")

    return Response(content="EVENT_RECEIVED", media_type="text/plain")
