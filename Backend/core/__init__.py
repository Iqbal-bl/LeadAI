"""Shared infrastructure used by every other package. No business logic lives here.

    paths.py              where things live on disk (BASE_DIR, scripts/, temp/, .env)
    config_guard.py       startup check for missing / insecure environment settings
    base.py               the SQLAlchemy declarative Base every ORM model inherits
    database.py           MySQL engine and session factory
    auth.py               JWT helpers and the get_current_user dependencies
    token_validation.py   validates identity-server tokens (OIDC / JWKS)
    storage.py            MinIO / S3 object storage for call recordings
    swagger.py            adds Bearer-token auth to the /docs page
    websocket_manager.py  the shared WebSocket broadcast manager
"""
