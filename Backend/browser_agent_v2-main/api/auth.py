"""
Simple API-key auth. Keys live in .env as a comma-separated list —
API_KEYS=key1,key2. Each client (CRM, your own scripts, etc.) should get
its own key so requests can be told apart in the log (see api/db.py's
client_id field).
"""

import os

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)


def _valid_keys() -> dict[str, str]:
    """Maps key -> a friendly client_id. Format in .env:
    API_KEYS=key1:crm,key2:internal_dashboard — the part after the colon
    is what shows up in logs. If you just list bare keys with no colon,
    the key itself (truncated) is used as the client_id."""
    raw = os.environ.get("API_KEYS", "")
    keys = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            key, client_id = entry.split(":", 1)
        else:
            key, client_id = entry, entry[:8]
        keys[key] = client_id
    return keys


async def require_api_key(x_api_key: str = Security(api_key_header)) -> str:
    """FastAPI dependency — raises 401 on a bad/missing key, otherwise
    returns the client_id for that key (use this for logging)."""
    keys = _valid_keys()
    if not keys:
        raise HTTPException(
            status_code=500,
            detail="No API_KEYS configured in .env — set at least one before using this API.",
        )
    if x_api_key not in keys:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header.")
    return keys[x_api_key]
