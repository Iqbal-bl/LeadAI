from typing import Optional, Dict
from sqlalchemy.orm import Session
from Domain.models import CallNumberExecution
from .BaseRepository import DocumentBaseRepository

class CallNumberExecutionRepository(DocumentBaseRepository[CallNumberExecution]):
    def __init__(self):
        super().__init__(CallNumberExecution)

    # Create a new attempt row for this run (append-only)
    async def create_for_call(self, db: Session, *, call_number_id: str,
                              batch_execution_id: str, status: str = "queued",
                              call_sid: str | None = None, created_by: str = "system") -> CallNumberExecution:
        return await self.add(db, {
            "CallNumberId": call_number_id,
            "BatchExecutionId": batch_execution_id,
            "Status": status,
            "CallSid": call_sid,
            "CreatedBy": created_by
        })

    # Fetch the (most recent) execution by Twilio SID
    async def get_by_sid(self, db: Session, call_sid: str) -> Optional[CallNumberExecution]:
        return (db.query(CallNumberExecution)
                  .filter(CallNumberExecution.IsDeleted == False,
                          CallNumberExecution.CallSid == call_sid)
                  .order_by(CallNumberExecution.CreatedAt.desc())
                  .first())

    # Update final status for that specific attempt by SID
    async def set_final_status_by_sid(self, db: Session, *, call_sid: str, final_status: str,
                                      updated_by: str = "system") -> Optional[CallNumberExecution]:
        row = await self.get_by_sid(db, call_sid)
        if not row:
            return None
        row.Status = final_status
        row.UpdatedBy = updated_by
        return await self.update(db, row)

    # Get latest attempt for a given CallNumber
    async def latest_for_callnumber(self, db: Session, call_number_id: str) -> Optional[CallNumberExecution]:
        return (db.query(CallNumberExecution)
                  .filter(CallNumberExecution.IsDeleted == False,
                          CallNumberExecution.CallNumberId == call_number_id)
                  .order_by(CallNumberExecution.CreatedAt.desc())
                  .first())

    # Map of CallNumberId -> latest status for a given execution (dashboard helper)
    async def latest_status_map_for_execution(self, db: Session, batch_execution_id: str) -> Dict[str, str]:
        rows = (db.query(CallNumberExecution)
                  .filter(CallNumberExecution.IsDeleted == False,
                          CallNumberExecution.BatchExecutionId == batch_execution_id)
                  .all())
        latest = {}
        for r in rows:
            prev = latest.get(r.CallNumberId)
            if (not prev) or (r.CreatedAt > prev.CreatedAt):
                latest[r.CallNumberId] = r
        return {cn_id: r.Status for cn_id, r in latest.items() if r}
