"""
Batch Data Fetcher
Fetch claim data for AI agents using CallNumberExecution → CallNumber → BatchInfo
"""

import logging
from typing import Dict, Any
from sqlalchemy.orm import Session
from Domain import models

logger = logging.getLogger(__name__)

class BatchDataFetcher:
    """Fetches claim (batch info) data for a specific callSid."""

    def __init__(self, db: Session, call_sid: str):
        self.db = db
        self.call_sid = call_sid

    async def get_claim_data(self) -> Dict[str, Any]:
        """
        Resolve the BatchInfo record associated with this callSid.
        Path:
        CallNumberExecution.CallSid → CallNumberExecution.CallNumberId → CallNumber.BatchInfoId → BatchInfo row
        """
        try:
            # Optimization: Fetch BatchInfo in a single query using Joins
            # CallSid -> CallNumberExecution -> CallNumber -> BatchInfo
            batch_info = (
                self.db.query(models.BatchInfo)
                .join(models.CallNumber, models.CallNumber.BatchInfoId == models.BatchInfo.Id)
                .join(models.CallNumberExecution, models.CallNumberExecution.CallNumberId == models.CallNumber.Id)
                .filter(models.CallNumberExecution.CallSid == self.call_sid)
                .first()
            )

            if not batch_info:
                logger.warning(f"BatchInfo not found for CallSid={self.call_sid}")
                return {}

            # Step 4️⃣ - Convert BatchInfo ORM object to dict
            exclude_fields = {'Id', 'CreatedBy', 'CreatedAt', 'IsDeleted'}
            claim_data = {
                col.name: getattr(batch_info, col.name)
                for col in models.BatchInfo.__table__.columns
                if col.name not in exclude_fields and getattr(batch_info, col.name) is not None
            }

            logger.info(f"✅ Loaded {len(claim_data)} claim fields for CallSid={self.call_sid}")
            return claim_data

        except Exception as e:
            logger.error(f"Error fetching claim data for CallSid={self.call_sid}: {e}")
            return {}