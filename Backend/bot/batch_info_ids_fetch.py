import logging
from typing import Dict, Any
from sqlalchemy.orm import Session
from Domain import models

logger = logging.getLogger(__name__)
class BatchIdsFetcher:
    def __init__(self, db, call_sid: str):
        self.db = db
        self.call_sid = call_sid

    async def get_ids(self) -> dict:
        """
        Returns:
          {
            "BatchExecutionId": int | None,
            "CallNumberExecutionId": int | None,
            "BatchInfoId": int | None,
            "CallNumberId": int | None,
            "CsvName": str | None
          }
        """
        try:
            # Optimization: Fetch all IDs and CsvName in a single query
            result = (
                self.db.query(
                    models.CallNumberExecution.BatchExecutionId,
                    models.CallNumberExecution.Id.label("CallNumberExecutionId"),
                    models.CallNumberExecution.CallNumberId,
                    models.CallNumber.BatchInfoId,
                    models.BatchInfo.CsvName
                )
                .join(models.CallNumber, models.CallNumberExecution.CallNumberId == models.CallNumber.Id)
                .outerjoin(models.BatchInfo, models.CallNumber.BatchInfoId == models.BatchInfo.Id)
                .filter(models.CallNumberExecution.CallSid == self.call_sid)
                .first()
            )

            if not result:
                logger.warning(f"CallNumberExecution not found for CallSid={self.call_sid}")
                return {"BatchExecutionId": None, "CallNumberExecutionId": None,
                        "BatchInfoId": None, "CallNumberId": None, "CsvName": None}

            return {
                "BatchExecutionId": result.BatchExecutionId,
                "CallNumberExecutionId": result.CallNumberExecutionId,
                "BatchInfoId": result.BatchInfoId,
                "CallNumberId": result.CallNumberId,
                "CsvName": result.CsvName,
            }
        except Exception as e:
            logger.error(f"get_ids error: {e}")
            return {"BatchExecutionId": None, "CallNumberExecutionId": None,
                    "BatchInfoId": None, "CallNumberId": None, "CsvName": None}