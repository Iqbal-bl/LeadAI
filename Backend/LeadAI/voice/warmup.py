"""Warm-up while a phone call rings.

A call rings for several seconds before pickup. That time is free, so use it to pay the costs
the first turn would otherwise pay in front of the caller:

  * open the language-model connection (TLS handshake included);
  * run one retrieval, which opens the embeddings path and loads the company's knowledge
    index into memory. The second live call's FIRST retrieval took 1.9 s; later ones 0.4-0.8 s.

Best effort and silent: warming is an optimisation and must never affect a call.
"""
from __future__ import annotations

import logging

from ..engine import gateway

logger = logging.getLogger(__name__)


def warm(call_data: dict | None) -> None:
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
