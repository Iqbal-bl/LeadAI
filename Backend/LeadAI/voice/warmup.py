"""Warm-up while a phone call rings.

A call rings for several seconds before pickup. That time is free, so use it to pay the costs
the first turn would otherwise pay in front of the caller:

  * open the language-model connection (TLS handshake included);
  * run one retrieval, which opens the embeddings path and loads the company's knowledge
    index into memory. The second live call's FIRST retrieval took 1.9 s; later ones 0.4-0.8 s.
  * compose the OPENING LINE itself. A live call spent 2.4 s on the opener's own LLM call
    AFTER the call connected — dead air the caller filled by saying "hello?" themselves,
    which the brain then answered AGAIN (see voice/brain.py's bare-greeting suppression for
    the other half of that fix). The text is cached here, keyed by CallSid, and
    voice/session.py's _opening_sync() uses it instead of composing fresh if it is there in
    time. No database write happens here — see services/voice_flow.precompute_opening().

Best effort and silent: warming is an optimisation and must never affect a call.
"""
from __future__ import annotations

import logging

from ..engine import gateway

logger = logging.getLogger(__name__)

# CallSid -> the dict precompute_opening() returned. Entries are removed when
# _opening_sync() picks one up; a call that rings but is never answered (busy,
# no-answer, voicemail) would otherwise leak one entry forever, so this is
# also bounded, same pattern as outbound/app.py's own _hangup_saved set.
_precomputed_openings: dict[str, dict] = {}
_MAX_CACHED_OPENINGS = 500


def pop_precomputed_opening(call_sid: str | None) -> dict | None:
    if not call_sid:
        return None
    return _precomputed_openings.pop(call_sid, None)


def discard_precomputed_opening(call_sid: str | None) -> None:
    """Call when a ringing call ends without ever being answered."""
    if call_sid:
        _precomputed_openings.pop(call_sid, None)


def warm(call_data: dict | None, call_sid: str | None = None) -> None:
    gateway.warm_connection()
    client_id = (call_data or {}).get("client_id")
    if not client_id:
        return
    try:
        from ..db import session
        from ..services import vectorstore

        db = session()
        try:
            vectorstore.idf_map(db, client_id)
            vectorstore.search(db, client_id, "hello", top_k=1)
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.debug("[LeadAI voice] retrieval warm-up failed", exc_info=True)

    conversation_id = (call_data or {}).get("conversation_id")
    if not call_sid or not conversation_id:
        return
    try:
        from ..db import session
        from ..services import voice_flow

        db = session()
        try:
            opening = voice_flow.precompute_opening(db, client_id, conversation_id)
        finally:
            db.close()
        if opening is not None:
            if len(_precomputed_openings) > _MAX_CACHED_OPENINGS:
                _precomputed_openings.clear()
            _precomputed_openings[call_sid] = opening
    except Exception:  # noqa: BLE001
        logger.debug("[LeadAI voice] opening-line warm-up failed", exc_info=True)
