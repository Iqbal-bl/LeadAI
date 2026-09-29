"""Which pipeline should this call use?

The legacy loop in outbound/app.py stays the default. Moving phone calls to a new pipeline
is done gradually (canary), never all at once:

    VOICE_PIPELINE=legacy    (default) every call uses /media-stream
    VOICE_PIPELINE=canary    only numbers in VOICE_PIPECAT_NUMBERS use Pipecat
    VOICE_PIPELINE=pipecat   every LeadAI call uses Pipecat

Only calls placed through LeadAI qualify: they carry the company and conversation the new
pipeline needs. Anything else (batch calls, calls placed from the legacy UI) stays on the
legacy loop whatever the setting, so a misconfiguration cannot strand a call that has no
LeadAI context.
"""
from __future__ import annotations

from ..config import settings

LEGACY_PATH = "/media-stream"
PIPECAT_PATH = "/media-stream-pipecat"


def _digits(number: str | None) -> str:
    """Last 10 digits: '+91 98765-43210', '919876543210' and '9876543210' all match."""
    return "".join(ch for ch in (number or "") if ch.isdigit())[-10:]


def _allowlist() -> set[str]:
    raw = getattr(settings, "voice_pipecat_numbers", "") or ""
    return {d for d in (_digits(n) for n in raw.split(",")) if d}


def use_pipecat(call_data: dict | None) -> bool:
    mode = (getattr(settings, "voice_pipeline", "legacy") or "legacy").strip().lower()
    if mode not in ("canary", "pipecat"):
        return False
    call_data = call_data or {}
    if not (call_data.get("leadai") and call_data.get("client_id") and call_data.get("conversation_id")):
        return False
    if mode == "pipecat":
        return True
    return _digits(call_data.get("phone_number")) in _allowlist()


def stream_path(call_data: dict | None) -> str:
    return PIPECAT_PATH if use_pipecat(call_data) else LEGACY_PATH


def use_pipecat_for(*, client_id: str, conversation_id: str, phone_number: str | None) -> bool:
    """Same decision as use_pipecat(), built from the fields available at CALL-PLACEMENT time.

    Twilio never needs this: it always hits /outbound-twiml per call, so that handler makes
    this same decision later, when Twilio asks. Exotel does not hit a per-call HTTP endpoint of
    ours (its flow is configured once, in Exotel's own dashboard), so for Exotel the decision has
    to be made HERE, before dialling, to choose which of the two Exotel flows to call (see
    telephony.place_exotel_call). Same rules either way — one function, so they can never drift
    apart.
    """
    return use_pipecat({
        "leadai": True, "client_id": client_id, "conversation_id": conversation_id,
        "phone_number": phone_number,
    })
