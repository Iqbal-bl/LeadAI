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


def _clean_exotel_number(number: str | None) -> str | None:
    """Strip everything but digits and a leading '+' from a phone number.

    A 400 from `Calls/connect` traced back to EXOTEL_CALLER_ID being stored in a
    human-readable form (spaces/dashes, e.g. from a copy-paste) rather than the bare
    digit string the API expects. Normalising here means how the env var happens to
    be formatted can never break a real call again — this runs on every call, not
    just as a one-time fix to the .env value.
    """
    if not number:
        return number
    cleaned = "".join(ch for ch in number if ch.isdigit() or ch == "+")
    if cleaned != number:
        logger.info("[LeadAI exotel] normalised a phone number for the Exotel API (formatting only)")
    return cleaned


def place_exotel_call(
    to_number: str,
    server_url: str,
    call_type: str = "trans",
    *,
    use_pipecat: bool = False,
) -> tuple[str, str]:
    """Originate a call via Exotel. Returns (call_sid, status).

    Three shapes, chosen by config:

    * `use_pipecat=True` and EXOTEL_PIPECAT_FLOW_APP_ID set -> `Calls/connect` with
      `Url` pointing at a SECOND Exotel flow, whose Voicebot/Stream applet is
      configured (in Exotel's own App Bazaar dashboard — not in this codebase) to
      connect to `/media-stream-pipecat` instead of `/media-stream`. That is what
      routes the call onto the shared LeadAI brain (LeadAI/voice/pipeline.py),
      the same one chat uses, with OpenAI writing the replies.

    * EXOTEL_FLOW_APP_ID set (the existing, working flow) -> `Calls/connect` with
      `Url` pointing at that flow. Its Voicebot/Stream applet connects the caller's
      audio to our `/media-stream` WebSocket. This is the pre-Pipecat, currently
      live production shape, and it is unchanged.

    * no flow id -> `Calls/connect` bridging To <-> CallerId, i.e. a plain
      agent-to-customer call with no bot in the middle. Useful for the "agent
      calls this lead" button.
    """
    if not settings.exotel_enabled:
        raise CallPlacementError("Exotel credentials are not configured")

    import httpx

    data = {
        "From": _clean_exotel_number(to_number),                      # the customer we are calling
        "CallerId": _clean_exotel_number(settings.exotel_caller_id),  # our DLT-registered ExoPhone
        "CallType": call_type,
    }
    flow_app_id = settings.exotel_flow_app_id
    if use_pipecat:
        if settings.exotel_pipecat_flow_app_id:
            flow_app_id = settings.exotel_pipecat_flow_app_id
        else:
            # Routing asked for the new pipeline, but the second Exotel flow has not been
            # configured yet (EXOTEL_PIPECAT_FLOW_APP_ID). Falling back to the existing,
            # working flow is safer than failing the call outright — but it silently means
            # this call runs on the legacy loop, not Pipecat, so it must be visible in the logs.
            logger.warning(
                "[LeadAI exotel] EXOTEL_PIPECAT_FLOW_APP_ID is not set; call is routed to the "
                "legacy flow instead of Pipecat"
            )
    if flow_app_id:
        data["Url"] = (
            f"http://my.exotel.com/{settings.exotel_sid}/exoml/start_voice/{flow_app_id}"
        )
        # NOT sending StatusCallback/StatusCallbackEvents here. Exotel's own docs list
        # StatusCallbackEvents[0]=terminal as valid, in exactly this array form, but a live
        # call with BOTH that and a flow `Url` set was rejected outright: 400 "Invalid Call
        # Parameters: Invalid 'StatusCallbackEvents' specified". Whether that combination is
        # simply unsupported on this account, or something else, is unconfirmed — but it
        # blocks the call from being placed at all, which is worse than not getting this
        # webhook. Consequence: a LeadCall placed through a flow will not receive Exotel's
        # terminal-status push, so its Status field will not update from this webhook path;
        # LeadAI/voice/pipeline.py's own CallSid/time-window check does not depend on it, so
        # the call itself still works. If Exotel confirms the right way to combine the two,
        # add StatusCallback back here.
    else:
        data["To"] = _clean_exotel_number(settings.exotel_caller_id)
        # Only sent for the plain bridge-call shape (no flow, no bot): Exotel posts terminal
        # call state here, mapped onto the vocabulary /call-status already uses.
        data["StatusCallback"] = f"{server_url.rstrip('/')}/api/leadai/voice/exotel/status"
        data["StatusCallbackEvents[0]"] = "terminal"

    try:
        resp = httpx.post(f"{_exotel_base()}/Calls/connect.json", data=data, timeout=25)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Exotel's error body (the actual reason — bad CallerId, bad flow Url, quota,
        # DLT rejection...) is far more useful than the bare status code raise_for_status()
        # gives you. httpx does not include the response body in str(exc).
        detail = exc.response.text[:500] if exc.response is not None else ""
        logger.error("[LeadAI exotel] Calls/connect rejected: %s | body: %s", exc, detail)
        raise CallPlacementError(f"Exotel rejected the call: {exc} | {detail}") from exc
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
    from outbound.app import TWILIO_PHONE_NUMBER, twilio_client

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
        from outbound.app import twilio_client

        twilio_client.calls(call_sid).update(status="completed")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LeadAI twilio] hangup failed for %s: %s", call_sid, exc)
        return False


# --------------------------------------------------------------------------- #
# unified interface
# --------------------------------------------------------------------------- #
def place_call(to_number: str, server_url: str, *, use_pipecat: bool = False) -> tuple[str, str, str]:
    """Place a call with whichever carrier is configured.

    `use_pipecat` only affects Exotel (it picks which flow app id to dial — see
    place_exotel_call). Twilio ignores it: a Twilio call always hits our own
    /outbound-twiml per call, so THAT decides Pipecat vs. legacy at TwiML-generation
    time, not here (see LeadAI/voice/routing.py).

    Returns (call_sid, status, provider).
    """
    provider = settings.effective_voice_provider

    if provider == "exotel":
        try:
            sid, status = place_exotel_call(to_number, server_url, use_pipecat=use_pipecat)
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
