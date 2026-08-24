"""Read access to the request log — this is the actual monitoring view."""

from fastapi import APIRouter, Depends

from api.auth import require_api_key
from api.db import ApiRequestLog, SessionLocal

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.get("/requests")
async def recent_requests(
    limit: int = 50,
    platform: str | None = None,
    success_only: bool = False,
    client_id: str = Depends(require_api_key),
):
    session = SessionLocal()
    try:
        query = session.query(ApiRequestLog)
        if platform:
            query = query.filter(ApiRequestLog.platform == platform)
        if success_only:
            query = query.filter(ApiRequestLog.success == True)  # noqa: E712
        rows = query.order_by(ApiRequestLog.timestamp.desc()).limit(limit).all()
        return [
            {
                "id": r.id, "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "client_id": r.client_id, "platform": r.platform, "action": r.action,
                "endpoint": r.endpoint, "success": r.success, "duration_ms": r.duration_ms,
                "result_summary": r.result_summary, "error_message": r.error_message,
            }
            for r in rows
        ]
    finally:
        session.close()
