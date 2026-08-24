# Repositories/BaseRepository.py
from typing import Any, TypeVar, Generic, List, Optional, Sequence, Dict, Type, Union, Tuple
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import BinaryExpression
from Domain.models import BaseDocument
import logging
from sqlalchemy.orm import Session
from Domain import models

TModel = TypeVar("TModel", bound=BaseDocument)

FilterType = Union[
    BinaryExpression,                         # e.g. Model.Field == value
    Sequence[BinaryExpression],               # list/tuple of expressions
    Dict[str, Any],                           # {"Field": value}
    None
]

class DocumentBaseRepository(Generic[TModel]):
    def __init__(self, model: Type[TModel]):
        self.model: Type[TModel] = model

    # ---------- CRUD ----------
    async def add(self, db: Session, document_data: dict) -> TModel:
        try:
            document = self.model(**document_data)
            db.add(document)
            db.commit()
            db.refresh(document)
            return document
        except IntegrityError:
            db.rollback()
            raise ValueError("Document with provided data already exists.")
        except Exception as ex:
            db.rollback()
            logging.exception("Error adding document")
            raise Exception(f"Error occurred while adding document: {ex}")

    async def update(self, db: Session, document: TModel) -> TModel:
        try:
            existing = db.query(self.model).filter(self.model.Id == document.Id).first()
            if not existing:
                raise ValueError(f"Document with ID {document.Id} not found.")
            # copy non-None, non-SA fields
            for k, v in document.__dict__.items():
                if k != "_sa_instance_state" and v is not None:
                    setattr(existing, k, v)
            db.commit()
            db.refresh(existing)
            return existing
        except Exception as ex:
            db.rollback()
            logging.exception("Error updating document")
            raise Exception(f"Error occurred while updating document: {ex}")

    async def soft_delete(self, db: Session, document_id: str) -> None:
        try:
            doc = db.query(self.model).filter(self.model.Id == document_id).first()
            if not doc:
                raise ValueError(f"Document with ID {document_id} not found.")
            doc.IsDeleted = True
            db.commit()
        except Exception as ex:
            db.rollback()
            logging.exception("Error soft-deleting document")
            raise Exception(f"Error occurred while soft deleting document: {ex}")

    async def hard_delete(self, db: Session, document_id: str) -> None:
        try:
            doc = db.query(self.model).filter(self.model.Id == document_id).first()
            if not doc:
                raise ValueError(f"Document with ID {document_id} not found.")
            db.delete(doc)
            db.commit()
        except Exception as ex:
            db.rollback()
            logging.exception("Error hard-deleting document")
            raise Exception(f"Error occurred while hard deleting document: {ex}")

    # ---------- Query helpers ----------
    def _apply_filter(self, query, filter_predicate: FilterType):
        if filter_predicate is None:
            return query.filter(self.model.IsDeleted == False)
        if isinstance(filter_predicate, dict):
            query = query.filter(self.model.IsDeleted == False)
            for field, value in filter_predicate.items():
                query = query.filter(getattr(self.model, field) == value)
            return query
        if isinstance(filter_predicate, (list, tuple)):
            return query.filter(self.model.IsDeleted == False, *filter_predicate)
        # single expression
        return query.filter(self.model.IsDeleted == False, filter_predicate)

    async def get(self, db: Session, filter_predicate: FilterType = None) -> List[TModel]:
        try:
            q = db.query(self.model)
            q = self._apply_filter(q, filter_predicate)
            return q.all()
        except Exception as ex:
            logging.exception("Error fetching documents")
            raise Exception(f"Error occurred while fetching documents: {ex}")

    async def get_one(self, db: Session, filter_predicate: FilterType = None) -> Optional[TModel]:
        try:
            q = db.query(self.model)
            q = self._apply_filter(q, filter_predicate)
            return q.first()
        except Exception:
            return None

    async def get_by_id(self, db: Session, document_id: str) -> Optional[TModel]:
        try:
            return db.query(self.model).filter(
                self.model.Id == document_id,
                self.model.IsDeleted == False
            ).first()
        except Exception:
            return None

    async def list(
        self,
        db: Session,
        filter_predicate: FilterType = None,
        order_by=None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[TModel]:
        try:
            q = db.query(self.model)
            q = self._apply_filter(q, filter_predicate)
            if order_by is not None:
                q = q.order_by(order_by)
            if offset:
                q = q.offset(offset)
            if limit:
                q = q.limit(limit)
            return q.all()
        except Exception as ex:
            logging.exception("Error listing documents")
            raise Exception(f"Error occurred while listing documents: {ex}")

    async def delete_all(self, db: Session) -> None:
        try:
            docs = db.query(self.model).all()
            for d in docs:
                db.delete(d)
            db.commit()
        except Exception as ex:
            db.rollback()
            logging.exception("Error deleting all documents")
            raise Exception(f"Error occurred while deleting documents: {ex}")

    async def get_call_logs_with_statuses(self, db: Session, page: int = 1, page_size: int = 10) -> dict:
        try:
            offset = (page - 1) * page_size
            
            # Get total count excluding call logs that exist in CallNumberExecution table
            total_count = (
                db.query(self.model)
                .outerjoin(models.CallNumberExecution, self.model.CallSid == models.CallNumberExecution.CallSid)
                .filter(
                    self.model.IsDeleted == False,
                    models.CallNumberExecution.CallSid.is_(None)
                )
                .count()
            )

            # Get paginated logs excluding those that exist in CallNumberExecution table
            paginated_logs = (
                db.query(self.model)
                .outerjoin(models.CallNumberExecution, self.model.CallSid == models.CallNumberExecution.CallSid)
                .filter(
                    self.model.IsDeleted == False,
                    models.CallNumberExecution.CallSid.is_(None)
                )
                .order_by(self.model.CreatedAt.desc())
                .offset(offset)
                .limit(page_size)
                .all()
            )
            paginated_ids = [log.Id for log in paginated_logs]

            if not paginated_ids:
                return {"items": [], "total_items": total_count}

            result = db.query(
                self.model,
                models.CallStatus,
                models.Recordings
            ).outerjoin(
                models.CallStatus,
                (models.CallStatus.CallLogsId == self.model.Id)
            ).outerjoin(
                models.Recordings,
                (models.Recordings.CallSid == self.model.CallSid)
            ).filter(
                self.model.Id.in_(paginated_ids),
                self.model.IsDeleted == False
            ).order_by(
                self.model.CreatedAt.desc(),
                models.CallStatus.CreatedAt.asc()
            ).all()

            organized = {}
            for log, status, recording in result:
                if log.Id not in organized:
                    organized[log.Id] = {
                        "log": log,
                        "statuses": [],
                        "recordingurl": recording.RecordingUrl if recording and not recording.IsDeleted else None
                    }

                if status and not status.IsDeleted:
                    organized[log.Id]["statuses"].append(status)

            # Sort organized items by CreatedAt DESC to ensure most recent calls appear first
            organized_items = list(organized.values())
            organized_items.sort(key=lambda x: x["log"].CreatedAt, reverse=True)

            return {
                "items": organized_items,
                "total_items": total_count
            }

        except Exception as ex:
            raise Exception(f"Error occurred while fetching call logs with statuses and recordings: {str(ex)}")