# utils.py
from Domain.models import Batch
from datetime import datetime, timezone

def standard_response(code: int, message: str, data: dict = None):
    return {"code": code, "message": message, "data": data or {}}

def build_batch_status_payload(batch: Batch, calls_data: dict = None):
    def _to_utc_iso(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    return [{
        "batch_id": batch.Id,
        "name": batch.Name,
        "status": batch.Status,
        "is_started": batch.Status in ["running", "completed", "stopped"],
        "is_completed": batch.Status == "completed",
        "total_numbers": batch.TotalCount,
        "completed_numbers": batch.CompletedCount,
        "failed_numbers": batch.FailedCount,
        "pending_numbers": batch.TotalCount - batch.CompletedCount - batch.FailedCount,
        "createdat": _to_utc_iso(batch.CreatedAt),
        "updated_at": _to_utc_iso(batch.UpdatedAt),
        "calls": calls_data or {}
    }]