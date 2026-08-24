"""
Telephony adapters.

The outbound app's live, working call path is Twilio + a `/media-stream`
WebSocket into Sarvam STT/TTS. That path is NOT touched. This module adds Exotel
as a peer carrier and presents both behind one interface so the LeadAI routers do
not care which is in use.

WHY EXOTEL FOR THE INDIA LEG
----------------------------
Exotel terminates on Indian carriers with a local caller ID (DLT-registered),
which matters for both answer rates and TRAI compliance; Twilio's Indian
termination is more restricted. So the intended production shape is:

    Exotel  -> carrier leg + media stream
    Sarvam  -> STT (speech_to_text / streaming) and TTS (bulbul)
    OpenAI  -> embeddings + reply generation over the company's RAG index

Exotel's bidirectional media streaming ("Voicebot" / Stream applet) delivers
8 kHz mono PCM base64 frames over a WebSocket — the SAME shape the existing
`/media-stream` handler already consumes from Twilio. That is the key
integration insight: the audio pipeline is carrier-agnostic, so pointing an
Exotel flow at the existing WebSocket reuses the entire STT -> LLM -> TTS loop
without rewriting it. Only the call-origination REST call and the status webhook
differ, and both are isolated in this file.

MODES
-----
* `exotel`    — real Exotel `Calls/connect` (needs EXOTEL_* env vars)
* `twilio`    — delegate to the outbound app's own twilio_client, unchanged
* `simulated` — no carrier configured: return a synthetic CallSid so the whole
                lead/qualify/handoff flow can be exercised end to end offline
"""
from __future__ import annotations

import logging
import uuid

from ..config import settings

logger = logging.getLogger(__name__)


class CallPlacementError(RuntimeError):
    """Carrier refused the call."""


# --------------------------------------------------------------------------- #
# Exotel
# --------------------------------------------------------------------------- #
def _exotel_base() -> str:
    # Exotel authenticates with API key/token in the URL's basic-auth position.
    return (
        f"https://{settings.exotel_api_key}:{settings.exotel_api_token}"
        f"@{settings.exotel_subdomain}/v1/Accounts/{settings.exotel_sid}"
    )


def place_exotel_call(
    to_number: str,
    server_url: str,
    call_type: str = "trans",
) -> tuple[str, str]:
    """Originate a call via Exotel. Returns (call_sid, status).

    Two shapes, chosen by config:

    * EXOTEL_FLOW_APP_ID set -> `Calls/connect` with `Url` pointing at the Exotel
      *flow* (App Bazaar applet chain). The flow contains the Voicebot/Stream
      applet that connects the caller's audio to our `/media-stream` WebSocket.
      This is the production shape for an AI-answered call.

    * no flow id -> `Calls/connect` bridging To <-> CallerId, i.e. a plain
      agent-to-customer call with no bot in the middle. Useful for the "agent
      calls this lead" button.
    """
    if not settings.exotel_enabled:
        raise CallPlacementError("Exotel credentials are not configured")

    import httpx

    data = {
        "From": to_number,                      # the customer we are calling
        "CallerId": settings.exotel_caller_id,  # our DLT-registered ExoPhone
        "CallType": call_type,
        # Exotel posts terminal call state here. We map it onto the same status
        # vocabulary the existing /call-status handler already uses.
        "StatusCallback": f"{server_url.rstrip('/')}/api/leadai/voice/exotel/status",
        "StatusCallbackEvents[0]": "terminal",
    }
    if settings.exotel_flow_app_id:
        data["Url"] = (
            f"http://my.exotel.com/{settings.exotel_sid}/exoml/start_voice/"
            f"{settings.exotel_flow_app_id}"
        )
    else:
        data["To"] = settings.exotel_caller_id

    try:
        resp = httpx.post(f"{_exotel_base()}/Calls/connect.json", data=data, timeout=25)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise CallPlacementError(f"Exotel rejected the call: {exc}") from exc

    body = (resp.json() or {}).get("Call", {})
    sid = body.get("Sid") or ""
    status = (body.get("Status") or "in-progress").lower()
    if not sid:
        raise CallPlacementError("Exotel returned no call Sid")
    return sid, status


def hangup_exotel_call(call_sid: str) -> bool:
    if not settings.exotel_enabled:
        return False
    try:
        import httpx

        resp = httpx.post(
            f"{_exotel_base()}/Calls/{call_sid}/hangup.json", timeout=15
        )
        return resp.status_code < 400
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LeadAI exotel] hangup failed for %s: %s", call_sid, exc)
        return False


# Exotel's terminal statuses -> the vocabulary already used in `callstatus`.
EXOTEL_STATUS_MAP = {
    "completed": "completed",
    "failed": "failed",
    "busy": "busy",
    "no-answer": "no-answer",
    "no_answer": "no-answer",
    "in-progress": "in-progress",
    "ringing": "ringing",
    "canceled": "canceled",
    "cancelled": "canceled",
}


def normalise_status(raw: str | None) -> str:
    return EXOTEL_STATUS_MAP.get((raw or "").strip().lower(), (raw or "unknown").lower())


# --------------------------------------------------------------------------- #
# Twilio (delegates to the app's existing, working client)
# --------------------------------------------------------------------------- #
def place_twilio_call(to_number: str, server_url: str) -> tuple[str, str]:
    """Reuse the outbound app's own Twilio client and webhook wiring verbatim.

    Deliberately mirrors /api/make-call's `calls.create(...)` arguments — same
    TwiML url, same status callback, same recording callbacks — so a LeadAI call
    is indistinguishable downstream from a normal outbound call and lands in
    `calllogs`, `conversations` and `recordings` exactly as before.
    """
    from multiligual_call import TWILIO_PHONE_NUMBER, twilio_client

    try:
        call = twilio_client.calls.create(
            to=to_number,
            from_=TWILIO_PHONE_NUMBER,
            url=f"{server_url}/outbound-twiml",
            status_callback=f"{server_url}/call-status",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            record=True,
            recording_status_callback=f"{server_url}/twilio/recording-callback",
            recording_status_callback_event=["completed", "absent"],
        )
    except Exception as exc:  # noqa: BLE001
        raise CallPlacementError(f"Twilio rejected the call: {exc}") from exc
    return call.sid, "initiated"


def hangup_twilio_call(call_sid: str) -> bool:
    try:
        from multiligual_call import twilio_client

        twilio_client.calls(call_sid).update(status="completed")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LeadAI twilio] hangup failed for %s: %s", call_sid, exc)
        return False


# --------------------------------------------------------------------------- #
# unified interface
# --------------------------------------------------------------------------- #
def place_call(to_number: str, server_url: str) -> tuple[str, str, str]:
    """Place a call with whichever carrier is configured.

    Returns (call_sid, status, provider).
    """
    provider = settings.effective_voice_provider

    if provider == "exotel":
        try:
            sid, status = place_exotel_call(to_number, server_url)
            return sid, status, "exotel"
        except CallPlacementError as exc:
            # Falling back rather than failing: an Exotel outage should not take
            # the product down when a working Twilio leg exists.
            logger.error("[LeadAI voice] exotel failed (%s) — trying twilio", exc)

    try:
        sid, status = place_twilio_call(to_number, server_url)
        return sid, status, "twilio"
    except CallPlacementError as exc:
        logger.error("[LeadAI voice] twilio failed too: %s", exc)

    # Nothing configured (or both refused): simulate so the lead pipeline,
    # transcript view and handoff logic remain demonstrable.
    sid = f"SIM{uuid.uuid4().hex[:28]}"
    logger.warning("[LeadAI voice] no carrier available — simulating call %s", sid)
    return sid, "simulated", "simulated"


def hangup(call_sid: str, provider: str) -> bool:
    if provider == "exotel":
        return hangup_exotel_call(call_sid)
    if provider == "twilio":
        return hangup_twilio_call(call_sid)
    return True


def stt_provider() -> str:
    """Sarvam handles STT for Indic + Hinglish speech; the outbound app's
    sarvam_stt.SarvamSTTManager already owns the streaming session."""
    return "sarvam" if settings.sarvam_api_key else "unavailable"


def tts_provider() -> str:
    return "sarvam" if settings.sarvam_api_key else "unavailable"


def status_report() -> dict:
    return {
        "voice_provider": settings.effective_voice_provider,
        "exotel_configured": settings.exotel_enabled,
        "stt": stt_provider(),
        "tts": tts_provider(),
        "default_language": settings.default_language,
    }
