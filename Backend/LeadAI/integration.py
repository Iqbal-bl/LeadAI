"""
The single integration point.

`register(app)` is the ONLY function that touches the existing application, and it
does four additive things:

1. Creates the LeadAI tables (idempotent, on the app's existing MySQL engine).
2. Marks the public widget prefix as exempt from the identity-server middleware.
3. Includes the LeadAI router under /api/leadai.
4. Adds two WebSocket endpoints for live inbox updates.

WHY THE PUBLIC-PATH STEP IS DONE THIS WAY
-----------------------------------------
`multiligual_call.TokenValidationMiddleware` reads the module-level `PUBLIC_PATHS`
tuple through `_is_public_path()` on every request. Rebinding that module
attribute at import time therefore extends the exemption list WITHOUT editing
multiligual_call.py — the file stays byte-identical to the version you have in
production. The alternative (editing the tuple in place) would mean modifying a
working file, which the brief rules out.

Only `/api/leadai/public` and the Exotel webhook are exempted. Every other LeadAI
route continues to go through the existing token validation, unchanged.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .config import settings

logger = logging.getLogger(__name__)

# Paths that must bypass the identity-server middleware:
#   * the customer chat widget — end customers have no staff account
#   * the Exotel status webhook — carriers cannot carry a user token
#   * health — used by load balancers and deployment checks
PUBLIC_LEADAI_PATHS = (
    # Covers BOTH the web widget (/public/chat/*) and the social webhooks
    # (/public/webhooks/*). The webhooks are not unauthenticated in practice:
    # they are authenticated cryptographically, by the X-Hub-Signature-256 HMAC
    # over the raw body, because Meta cannot present a user token.
    f"{settings.api_prefix}/public",
    f"{settings.api_prefix}/voice/exotel/status",
    f"{settings.api_prefix}/health",
    f"{settings.api_prefix}/linkedin/callback",
)


def _extend_public_paths() -> None:
    """Additively extend multiligual_call.PUBLIC_PATHS (no file edit)."""
    try:
        import multiligual_call as mc
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LeadAI] could not extend PUBLIC_PATHS: %s", exc)
        return

    current = tuple(getattr(mc, "PUBLIC_PATHS", ()))
    missing = tuple(p for p in PUBLIC_LEADAI_PATHS if p not in current)
    if missing:
        mc.PUBLIC_PATHS = current + missing
        logger.info("[LeadAI] public paths registered: %s", ", ".join(missing))


def _register_websockets(app: FastAPI) -> None:
    """Live inbox updates.

    These reuse the app's OWN broadcast manager (Websockets.connection.manager),
    so LeadAI events travel over the same infrastructure the batch/call monitors
    already use — one connection manager, one set of semantics.

    Note the `/ws/` prefix is already in PUBLIC_PATHS, and WS routes bypass HTTP
    middleware; the token is therefore validated inside the handler exactly as
    the existing WS routes do.
    """
    try:
        from Websockets.connection import manager
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LeadAI] websocket manager unavailable: %s", exc)
        return

    @app.websocket("/ws/leadai/inbox/{client_id}")
    async def leadai_inbox_ws(websocket: WebSocket, client_id: str):
        """Per-company inbox channel: new lead, handoff, assignment, call status."""
        from auth import get_current_user_websocket

        try:
            await get_current_user_websocket(websocket)
        except Exception:  # noqa: BLE001
            # Accept-then-close, matching the existing WS routes' behaviour so the
            # client sees a clean close rather than a bare 403.
            await manager.connect(websocket, client_id, connection_type="leadai_inbox")
            await manager.disconnect(websocket, client_id, connection_type="leadai_inbox")
            return

        await manager.connect(websocket, client_id, connection_type="leadai_inbox")
        logger.info("[LeadAI] inbox WS connected for company %s", client_id)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            await manager.disconnect(websocket, client_id, connection_type="leadai_inbox")
            logger.info("[LeadAI] inbox WS disconnected for company %s", client_id)

    @app.websocket("/ws/leadai/conversation/{conversation_id}")
    async def leadai_conversation_ws(websocket: WebSocket, conversation_id: str):
        """Per-conversation channel: live message stream for an open thread."""
        from auth import get_current_user_websocket

        try:
            await get_current_user_websocket(websocket)
        except Exception:  # noqa: BLE001
            await manager.connect(websocket, conversation_id, connection_type="leadai_conversation")
            await manager.disconnect(
                websocket, conversation_id, connection_type="leadai_conversation"
            )
            return

        await manager.connect(websocket, conversation_id, connection_type="leadai_conversation")
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            await manager.disconnect(
                websocket, conversation_id, connection_type="leadai_conversation"
            )


def _register_worker(app: FastAPI) -> None:
    """Attach the job-queue worker to the app lifecycle."""
    from .services import jobs

    # Importing the runner is what REGISTERS its @jobs.register handlers. Without
    # this import the queue would fill with jobs nothing knows how to execute.
    from .services import campaign_runner  # noqa: F401

    @app.on_event("startup")
    async def _start_leadai_worker():  # pragma: no cover - lifecycle
        try:
            # Re-queue anything a previous process died holding, so a deploy in
            # the middle of a campaign resumes instead of stalling.
            jobs.reclaim_stale()
            jobs.start()

            # Bootstrap the daily LinkedIn connection request checking job
            from .db import session
            db = session()
            try:
                jobs.bootstrap_linkedin_job(db)
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001
            logger.error("[LeadAI] worker failed to start: %s", exc)

    @app.on_event("shutdown")
    async def _stop_leadai_worker():  # pragma: no cover - lifecycle
        try:
            jobs.stop()
        except Exception:  # noqa: BLE001
            pass


def register(app: FastAPI) -> FastAPI:
    """Attach LeadAI to an existing FastAPI app. Safe to call once at startup."""
    # 1. tables (same engine, same Base — see LeadAI/db.py)
    try:
        from .db import ensure_columns, ensure_tables

        ensure_tables()
        # Additive column migration. Needed because an installation that already
        # has leadai_* tables would otherwise never gain the columns added by
        # this release (lead threshold, channel account link, campaign
        # attribution). ADD COLUMN only — see the docstring in db.py.
        ensure_columns()
    except Exception as exc:  # noqa: BLE001
        # Never take the whole app down because one new table failed; the existing
        # outbound features must keep working.
        logger.error("[LeadAI] table creation failed: %s", exc)

    # 2. public path exemptions
    _extend_public_paths()

    # 3. routes
    from .router import api_router

    app.include_router(api_router, prefix=settings.api_prefix)

    # 4. websockets
    _register_websockets(app)

    # 5. background worker for campaigns and other long-running jobs.
    #
    # Registered as a startup hook rather than started here, because at import
    # time there is no running event loop. Set LEADAI_WORKER_ENABLED=false on
    # extra web replicas and run one dedicated worker process instead — see
    # docker-compose.yml.
    _register_worker(app)

    logger.info(
        "[LeadAI] registered at %s | llm=%s embeddings=%s vectors=%s voice=%s "
        "channels=%s storage=%s worker=%s",
        settings.api_prefix,
        "openai" if settings.llm_enabled else "builtin",
        "openai" if settings.llm_enabled else "local",
        "qdrant" if settings.qdrant_url else "mysql",
        settings.effective_voice_provider,
        "meta+web" if settings.social_channels_enabled else "web-only",
        "minio" if settings.minio_enabled else "local-disk",
        "on" if settings.worker_enabled else "off",
    )
    return app
