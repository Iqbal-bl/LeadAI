"""
Unified entrypoint — ONE FastAPI app that exposes every API.

Base    : the multilingual (Sarvam) voice-agent app from `multiligual_call.py`
          (all /api/* control endpoints, /outbound-twiml, /call-status,
          /media-stream, /ws/transcript, auth, script management, CORS, /docs).
Added   : the batch-calling control plane (`batching.router`) plus the batch /
          live-monitoring WebSockets, repointed to run on the Sarvam stack.

On any route collision the multilingual app wins, because its routes are
registered first (it is imported, fully built, before we add anything here).

Run:  python main.py
  or: uvicorn main:app --host 0.0.0.0 --port 5050
"""
import asyncio
import os
import logging

# Validate the environment BEFORE importing anything that reads it. multiligual_
# call.py builds its SessionMiddleware with `os.getenv("SESSION_SECRET",
# "demo-secret-key")` at import time, so a check placed after that import would
# be reporting on a secret that has already been baked into the middleware.
# In production a critical issue exits 78 here; in development it warns.
from config_guard import validate as _validate_config

_validate_config()

from fastapi import WebSocket, WebSocketDisconnect  # noqa: E402

# Import the complete, already-built Sarvam app. This pulls in db.py (users +
# transcripts) and, transitively via batching below, the Domain/ ORM used for
# batches — both ride on the single MySQL engine in database.py.
from multiligual_call import app

# Batch calling control plane + the shared websocket broadcast manager.
from batching import router as batch_router, service  # noqa: F401  (service kept for parity)
from Websockets.connection import manager, set_event_loop

logger = logging.getLogger(__name__)


@app.on_event("startup")
async def _capture_event_loop():
    set_event_loop(asyncio.get_running_loop())

# ---------------------------------------------------------------------------
# Batch control plane:  /api/batches , /api/batches/{id}/start|stop|status
# ---------------------------------------------------------------------------
app.include_router(batch_router, prefix="/api", tags=["Batches"])


# ---------------------------------------------------------------------------
# Full WebSocket surface (parity with voice_agent.py), all driven by the shared
# Websockets.connection.manager:
#   /ws/transcript/{call_sid}  -> live transcript      (defined in multiligual_call.py)
#   /ws/batch/{batch_id}       -> batch status
#   /ws/activecalls            -> active call count
#   /ws/runningbatch           -> running batch ids
#   /ws/{callsid}              -> general/status channel  (REGISTERED LAST below)
#
# Route order matters: the single-segment catch-all /ws/{callsid} must be
# registered AFTER /ws/activecalls and /ws/runningbatch, or it would shadow
# them. Because main.py imports the app (with /ws/transcript already attached)
# and then adds these, the ordering is guaranteed.
# ---------------------------------------------------------------------------
@app.websocket("/ws/batch/{batch_id}")
async def ws_batch(websocket: WebSocket, batch_id: str):
    """Live batch status (counters, running/queued, completion)."""
    await manager.connect(websocket, batch_id, connection_type="batch")
    logger.info(f"Batch WS connected: {batch_id}")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket, batch_id, connection_type="batch")
        logger.info(f"Batch WS disconnected: {batch_id}")


@app.websocket("/ws/activecalls")
async def ws_activecalls(websocket: WebSocket):
    """Live count of active calls across all running batches."""
    await manager.connect(websocket, key="global_active_calls", connection_type="activecalls")
    logger.info("Active-calls WS connected")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket, "global_active_calls", connection_type="activecalls")
        logger.info("Active-calls WS disconnected")


@app.websocket("/ws/runningbatch")
async def ws_runningbatch(websocket: WebSocket):
    """Live list of currently running batch IDs."""
    await manager.connect(websocket, "global_running_batches", connection_type="runningbatch")
    logger.info("Running-batch WS connected")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket, "global_running_batches", connection_type="runningbatch")
        logger.info("Running-batch WS disconnected")


# General/status channel (voice_agent.py's /ws/{callsid}). Registered LAST so
# the single-segment match can't shadow the specific routes above. This is the
# endpoint the existing client uses to receive call status lines.
@app.websocket("/ws/{callsid}")
async def ws_general(websocket: WebSocket, callsid: str):
    """General per-call channel (status updates broadcast via broadcast_to_call)."""
    if not callsid or callsid.lower() in ("undefined", "null"):
        # Starlette needs an accept before close to avoid a 403; reject cleanly.
        await manager.connect(websocket, callsid, connection_type="general")
        await manager.disconnect(websocket, callsid, connection_type="general")
        logger.warning(f"[ws/general] rejected invalid callsid={callsid!r}")
        return
    await manager.connect(websocket, callsid, connection_type="general")
    logger.info(f"General WS connected for call: {callsid}")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket, callsid, connection_type="general")
        logger.info(f"General WS disconnected for call: {callsid}")


# ---------------------------------------------------------------------------
# LeadAI platform (ADDITIVE — nothing above this line is modified).
#
# Registers, under /api/leadai:
#   • per-company knowledge bases (RAG: OpenAI embeddings + hybrid retrieval)
#   • per-company dynamic scripts (same XML dialect as scripts/*.xml)
#   • the customer chat widget + lead generation/qualification pipeline
#   • the staff inbox with RBAC, PII masking and activity logging
#   • outbound lead calls, which REUSE the existing Twilio/Exotel + Sarvam
#     pipeline in multiligual_call.py rather than duplicating it
#
# Registration is wrapped: if LeadAI fails to load for any reason, the existing
# voice-agent and batch-calling APIs continue to serve normally.
# ---------------------------------------------------------------------------
try:
    from LeadAI.integration import register as register_leadai

    register_leadai(app)
except Exception as _leadai_exc:  # noqa: BLE001
    logger.error(f"[LeadAI] not registered ({_leadai_exc.__class__.__name__}): {_leadai_exc}")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "5050"))
    logger.info(f"Starting unified voice-agent app on :{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, timeout_keep_alive=75)
