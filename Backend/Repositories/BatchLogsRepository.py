from sqlalchemy.orm import Session
from Domain.models import BatchLogs
from .BaseRepository import DocumentBaseRepository

class BatchLogsRepository(DocumentBaseRepository[BatchLogs]):
    def __init__(self):
        super().__init__(BatchLogs)

    # Append-only log helper
    async def log(self, db: Session, *, batch_id: str, log_type: str, message: str,
                  batch_execution_id: str | None = None, created_by: str = "system") -> BatchLogs:
        return await self.add(db, {
            "LogMessage": message,
            "LogType": log_type,
            "BatchId": batch_id,
            "BatchExecutionId": batch_execution_id,
            "CreatedBy": created_by
        })
