from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, update
from Domain.models import BatchExecution
from .BaseRepository import DocumentBaseRepository
from Domain import models
class BatchExecutionRepository(DocumentBaseRepository[BatchExecution]):
    def __init__(self):
        super().__init__(BatchExecution)

    # Create a new run (append-only)
    async def create_running(self, db: Session, batch_id: str, created_by: str = "system", restart_mode: str = "all") -> BatchExecution:
        # Map restart mode to batch state description
        batch_state_map = {
            "all": "all restarted",
            "failed_only": "failed one restarted",
            "pending_only": "pending restarted"
        }

        # Check if there's already a running execution for this batch
        existing = await self.running_for_batch(db, batch_id)
        if existing:
            print(f"📋 Using existing BatchExecution {existing.Id} for batch {batch_id}")
            # Update the restart mode if it changed
            new_batch_state = batch_state_map.get(restart_mode, "active")
            if existing.BatchState != new_batch_state:
                existing.BatchState = new_batch_state
                existing.UpdatedBy = created_by
                existing.UpdatedAt = datetime.now(timezone.utc) # Ensure UTC timestamp on update
                db.commit()
            return existing

        return await self.add(db, {
            "BatchId": batch_id,
            "Status": "running",
            "CompletedCount": 0,
            "FailedCount": 0,
            "BatchState": batch_state_map.get(restart_mode, "active"),
            "CreatedBy": created_by
        })

    # Set status on this specific execution id
    async def set_status(self, db: Session, exec_id: str, status: str, updated_by: str = "system") -> Optional[BatchExecution]:
        row = await self.get_by_id(db, exec_id)
        if not row:
            return None
        row.Status = status
        row.UpdatedBy = updated_by
        row.UpdatedAt = datetime.now(timezone.utc) # Ensure UTC timestamp is set
        return await self.update(db, row)

    # Atomic counters (safe under concurrency)
    async def increment_completed(self, db: Session, exec_id: str, by: int = 1) -> None:
        db.execute(
            update(BatchExecution)
            .where(BatchExecution.Id == exec_id, BatchExecution.IsDeleted == False)
            .values(
                CompletedCount=BatchExecution.CompletedCount + by,
                # UpdatedBy=updated_by,
                UpdatedAt=datetime.now(timezone.utc), # Ensure UTC timestamp
            )
        )
        db.commit()

    async def increment_failed(self, db: Session, exec_id: str, by: int = 1) -> None:
        db.execute(
            update(BatchExecution)
            .where(BatchExecution.Id == exec_id, BatchExecution.IsDeleted == False)
            .values(
                FailedCount=BatchExecution.FailedCount + by,
                # UpdatedBy=updated_by,
                UpdatedAt=datetime.now(timezone.utc), # Ensure UTC timestamp
            )
        )
        db.commit()

    # Convenience lookups
    async def latest_for_batch(self, db: Session, batch_id: str) -> Optional[BatchExecution]:
        return (db.query(BatchExecution)
                  .filter(BatchExecution.IsDeleted == False,
                          BatchExecution.BatchId == batch_id)
                  .order_by(BatchExecution.CreatedAt.desc())
                  .first())

    async def running_for_batch(self, db: Session, batch_id: str) -> Optional[BatchExecution]:
        return (db.query(BatchExecution)
                  .filter(BatchExecution.IsDeleted == False,
                          BatchExecution.BatchId == batch_id,
                          BatchExecution.Status == "running")
                  .order_by(BatchExecution.CreatedAt.desc())
                  .first())
    
    async def recompute_counters_from_db(self, db: Session, execution_id: str) -> None:
        print(f"🔍 RECOMPUTE for exec_id={execution_id}")
        CNE = models.CallNumberExecution

        completed = db.query(func.count(CNE.Id)).filter(
            CNE.BatchExecutionId == execution_id,
            CNE.IsDeleted == False,
            CNE.Status == "completed"
        ).scalar() or 0

        failed = db.query(func.count(CNE.Id)).filter(
            CNE.BatchExecutionId == execution_id,
            CNE.IsDeleted == False,
            CNE.Status.in_(["busy", "failed", "canceled", "no-answer"])
        ).scalar() or 0

        print(f"📊 FOUND completed={completed}, failed={failed}")

        bx = db.query(models.BatchExecution).filter(
            models.BatchExecution.Id == execution_id,
            models.BatchExecution.IsDeleted == False
        ).first()
        if bx:
            bx.CompletedCount = completed
            bx.FailedCount = failed
            bx.UpdatedAt = datetime.now(timezone.utc)
            print(f"✅ Updated BatchExecution {execution_id} with new counts")
            # bx.UpdatedBy = "system" # Removed as this method should not set UpdatedBy
        else:
            print(f"❌ BatchExecution {execution_id} NOT FOUND during recompute")