# Domain/models.py
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Index
)
from sqlalchemy.orm import relationship
from base import Base  # keep your existing Base import

# ------------------------------------------------------------
# Common abstract base with your audit columns
# ------------------------------------------------------------
class BaseDocument(Base):
    __abstract__ = True
    Id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), unique=True, nullable=False)
    CreatedBy = Column(String(100), nullable=False, default='system')
    CreatedAt = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    UpdatedBy = Column(String(100), nullable=True)
    UpdatedAt = Column(DateTime, nullable=True)
    IsDeleted = Column(Boolean, default=False)


# ------------------------------------------------------------
# Users (unchanged)
# ------------------------------------------------------------
class User(BaseDocument):
    __tablename__ = "users"
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    password = Column(String(100), nullable=False)


# ------------------------------------------------------------
# (Legacy) CallLogs/CallStatus (unchanged if still used elsewhere)
# ------------------------------------------------------------
class CallLogs(BaseDocument):
    __tablename__ = "calllogs"
    CallSid = Column(String(100), unique=True, nullable=False)
    PhoneNumber = Column(String(50), nullable=False)
    callstatus = relationship("CallStatus", uselist=False, back_populates="calllogs")


class CallStatus(BaseDocument):
    __tablename__ = "callstatus"
    Status = Column(String(50), nullable=False)
    CallLogsId = Column(String(36), ForeignKey("calllogs.Id"), nullable=False)
    calllogs = relationship("CallLogs", back_populates="callstatus")


# ------------------------------------------------------------
# NEW CORE TABLES
# ------------------------------------------------------------

class Client(BaseDocument):
    """
    Clients table:
    Id, Name, PhoneNumber, Email, Description, IsActive, audit cols
    """
    __tablename__ = "Clients"  # Capital C, plural

    Name = Column(String(100), nullable=False)
    PhoneNumber = Column(String(50), nullable=True)
    Email = Column(String(100), nullable=True)
    Description = Column(String(500), nullable=True)
    IsActive = Column(Boolean, default=True)


class Batch(BaseDocument):
    """
    New batches table:
    Id, Name, Email, ScriptPath, TotalCount, UserId, ClientId, Created/Updated/IsDeleted
    (No Status/CompletedCount/FailedCount here anymore.)
    """
    __tablename__ = "batches"

    Name = Column(String(100), nullable=False)
    Email = Column(String(100), nullable=True)  # Allow null for backward compatibility
    ScriptPath = Column(String(255), nullable=True)
    TotalCount = Column(Integer, nullable=True)  # allow null until you backfill
    UserId = Column(String(100), nullable=True) # e.g. active|paused|stopped
    ClientId = Column(String(36), nullable=True)  # Link to Client table (no FK constraint in this backend)
    
    # Relationships
    client = relationship("Client", primaryjoin="Batch.ClientId == Client.Id", foreign_keys=[ClientId], lazy="selectin")  # Fetch client info for filename
    callnumbers = relationship("CallNumber", back_populates="batch", lazy="selectin")
    executions = relationship("BatchExecution", back_populates="batch", lazy="selectin")
    logs = relationship("BatchLogs", back_populates="batch", lazy="selectin")
    batchinfo = relationship("BatchInfo", back_populates="batch", lazy="selectin")

    __table_args__ = (
        Index("ix_batches_email", "Email"),
        Index("ix_batches_createdat", "CreatedAt"),
        Index("ix_batches_clientid", "ClientId"),
    )



class BatchExecution(BaseDocument):
    """
    batchexecution:
    Id, BatchId(FK), Status, CompletedCount, FailedCount, audit cols
    """
    __tablename__ = "batchexecution"

    BatchId = Column(String(36), ForeignKey("batches.Id"), nullable=False, index=True)

    BatchState = Column(String(50), nullable=True, default="started") 
    Status = Column(String(50), nullable=False)  # e.g. pending|running|stopped|completed|failed|cancelled
    CompletedCount = Column(Integer, nullable=True, default=0)
    FailedCount = Column(Integer, nullable=True, default=0)
    BatchCsvLink = Column(String(500), nullable=True)  # URL to batch-level CSV report
    # Relationships
    batch = relationship("Batch", back_populates="executions")
    call_executions = relationship("CallNumberExecution", back_populates="batchexecution", lazy="selectin")
    logs = relationship("BatchLogs", back_populates="batchexecution", lazy="selectin")

    __table_args__ = (
        Index("ix_batchexec_batchid_createdat", "BatchId", "CreatedAt"),
        Index("ix_batchexec_status", "Status"),
    )


class BatchLogs(BaseDocument):
    """
    batchlogs:
    Id, LogMessage, LogType, BatchId, BatchExecutionId, audit cols
    LogType: StartBatch | RunningBatch | StopBatch | CompletedBatch
    """
    __tablename__ = "batchlogs"

    LogMessage = Column(String(2000), nullable=False)
    LogType = Column(String(50), nullable=False)
    BatchId = Column(String(36), ForeignKey("batches.Id"), nullable=False, index=True)
    BatchExecutionId = Column(String(36), ForeignKey("batchexecution.Id"), nullable=True, index=True)

    # Relationships
    batch = relationship("Batch", back_populates="logs")
    batchexecution = relationship("BatchExecution", back_populates="logs")

    __table_args__ = (
        Index("ix_batchlogs_createdat", "CreatedAt"),
        Index("ix_batchlogs_logtype", "LogType"),
    )


class CallNumber(BaseDocument):
    """
    callnumbers:
    Id, PhoneNumber, BatchId, Created/Updated/IsDeleted, BatchInfoId (optional, per your note)
    NOTE: No per-call CallSid/Status here anymore (moved to CallNumberExecution).
    """
    __tablename__ = "callnumbers"

    PhoneNumber = Column(String(50), nullable=False)
    BatchId = Column(String(36), ForeignKey("batches.Id"), nullable=False, index=True)

    # Optional extra column you mentioned (keep nullable in case you backfill later)
    BatchInfoId = Column(String(36), ForeignKey("batchinfo.Id"), nullable=True, index=True)

    batch = relationship("Batch", back_populates="callnumbers")
    executions = relationship("CallNumberExecution", back_populates="callnumber", lazy="selectin")
    batchinfo = relationship("BatchInfo", lazy="selectin")

    __table_args__ = (
        Index("ix_callnumbers_phonenumber", "PhoneNumber"),
        Index("ix_callnumbers_createdat", "CreatedAt"),
    )


class CallNumberExecution(BaseDocument):
    """
    callnumberexecution:
    Id, CallNumberId(FK), CallSid, Status, BatchExecutionId(FK), audit cols
    """
    __tablename__ = "callnumberexecution"

    CallNumberId = Column(String(36), ForeignKey("callnumbers.Id"), nullable=False, index=True)
    CallSid = Column(String(100), nullable=True, index=True)
    Status = Column(String(50), nullable=True)  # e.g. initiated|ringing|answered|completed|failed|busy|no-answer|canceled
    BatchExecutionId = Column(String(36), ForeignKey("batchexecution.Id"), nullable=False, index=True)

    callnumber = relationship("CallNumber", back_populates="executions")
    batchexecution = relationship("BatchExecution", back_populates="call_executions")

    __table_args__ = (
        Index("ix_cne_callnumberid_createdat", "CallNumberId", "CreatedAt"),
        Index("ix_cne_status", "Status"),
    )


# ------------------------------------------------------------
# Optional: Conversations / Recordings (unchanged)
# ------------------------------------------------------------
class Conversation(BaseDocument):
    __tablename__ = "conversations"
    CallSid = Column(String(100), nullable=False, index=True)
    ResponseType = Column(String(50), nullable=False)
    ResponseText = Column(String(2000), nullable=False)


class Recordings(BaseDocument):
    __tablename__ = "recordings"
    CallSid = Column(String(100), nullable=False, index=True)
    RecordingUrl = Column(String(255), nullable=False)


# ------------------------------------------------------------
# NEW BATCH INFO TABLES FOR AI PROMPTS AND OUTPUT EXTRACTION
# ------------------------------------------------------------

class BatchInfo(BaseDocument):
    """
    batchinfo:
    Stores batch-related data for AI prompts
    """
    __tablename__ = "batchinfo"

    BatchId = Column(String(36), ForeignKey("batches.Id"), nullable=False, index=True)
    CsvName = Column(String(255), nullable=True)
    Callers_Name = Column(String(255), nullable=True)
    Callers_Intent = Column(String(255), nullable=True)
    Company_Name = Column(String(255), nullable=True)
    NPI = Column(String(50), nullable=True)
    Tax_ID = Column(String(50), nullable=True)
    CLIA_Number = Column(String(50), nullable=True)
    Callback_Number = Column(String(50), nullable=True)
    Provider_Address = Column(String(500), nullable=True)
    Patient_FirstName = Column(String(100), nullable=True)
    Patient_LastName = Column(String(100), nullable=True)
    Subscriber_ID = Column(String(100), nullable=True)
    Claims_Address = Column(String(500), nullable=True)
    Subscriber_FirstName = Column(String(100), nullable=True)
    Subscriber_LastName = Column(String(100), nullable=True)
    Relationship = Column(String(100), nullable=True)
    DOB = Column(DateTime, nullable=True)
    DOS = Column(DateTime, nullable=True)
    Charge = Column(String(50), nullable=True)
    Insurance_Payment = Column(String(50), nullable=True)
    Submission_Method = Column(String(100), nullable=True)
    Appeal_Submission_Date = Column(DateTime, nullable=True)
    Claim_Appeal = Column(String(100), nullable=True)
    Provider_Fax = Column(String(50), nullable=True)
    Provider_Email = Column(String(255), nullable=True)
    Unresponded_Claim = Column(String(100), nullable=True)
    Unresponded_Appeal = Column(String(100), nullable=True)
    Prefered_Payer_ID = Column(String(100), nullable=True)
    Prefered_Claims_Address = Column(String(500), nullable=True)
    Claims_TFL = Column(String(100), nullable=True)
    Processing_Delays = Column(String(100), nullable=True)
    Eligible = Column(String(100), nullable=True)
    CS_Agent_Name = Column(String(255), nullable=True)
    Call_Ref = Column(String(100), nullable=True)
    Department_Phone_Number = Column(String(50), nullable=True)
    Status = Column(String(100), nullable=True)
    Appeals_Address = Column(String(500), nullable=True)
    Received_date = Column(DateTime, nullable=True)
    Processing_Period = Column(String(100), nullable=True)
    Other_information = Column(String(1000), nullable=True)
    Processed_Date = Column(DateTime, nullable=True)
    Denial_reason = Column(String(500), nullable=True)
    Claim_Number = Column(String(100), nullable=True)
    Appealable = Column(String(100), nullable=True)
    Appeals_TFL = Column(String(100), nullable=True)
    Appeals_Submission_Method = Column(String(100), nullable=True)
    Online_Portal = Column(String(100), nullable=True)
    Appeals_Fax_Number = Column(String(50), nullable=True)
    Corrected_claims = Column(String(100), nullable=True)
    Paid_date = Column(DateTime, nullable=True)
    PaymentAmount = Column(String(50), nullable=True)
    Payment_Method = Column(String(100), nullable=True)
    Paid_To = Column(String(255), nullable=True)
    Check_Cashed = Column(String(100), nullable=True)
    Pay_to_address = Column(String(500), nullable=True)
    Check_Number = Column(String(100), nullable=True)
    Bank_Account = Column(String(100), nullable=True)
    Deposit_Date = Column(DateTime, nullable=True)
    Appeal_Ticket = Column(String(100), nullable=True)
    PR_Amount = Column(String(50), nullable=True)
    PR_Type = Column(String(100), nullable=True)
    Bulk_Payment = Column(String(100), nullable=True)
    Payer_ID = Column(String(100), nullable=True)
    Payer_Name = Column(String(255), nullable=True)
    Payer_Phone_Number = Column(String(50), nullable=True)
    Group_ID = Column(String(100), nullable=True)
    # Relationships
    batch = relationship("Batch", back_populates="batchinfo")
    batchinfooutputs = relationship("BatchInfoOutput", back_populates="batchinfo", lazy="selectin")

    __table_args__ = (
        Index("ix_batchinfo_batchid", "BatchId"),
        Index("ix_batchinfo_createdat", "CreatedAt"),
    )


class BatchInfoOutput(BaseDocument):
    """
    batchinfooutput:
    Stores extracted values from AI conversations
    """
    __tablename__ = "batchinfooutput"

    BatchInfoId = Column(String(36), ForeignKey("batchinfo.Id"), nullable=False, index=True)
    BatchExecutionId = Column(String(36), ForeignKey("batchexecution.Id"), nullable=False, index=True)
    CallNumberExecutionId = Column(String(36), ForeignKey("callnumberexecution.Id"), nullable=False, index=True)
    CsvName = Column(String(255), nullable=True)
    Callers_Name = Column(String(255), nullable=True)
    Callers_Intent = Column(String(255), nullable=True)
    Company_Name = Column(String(255), nullable=True)
    NPI = Column(String(50), nullable=True)
    Tax_ID = Column(String(50), nullable=True)
    CLIA_Number = Column(String(50), nullable=True)
    Callback_Number = Column(String(50), nullable=True)
    Payer_ID = Column(String(100), nullable=True)
    Provider_Address = Column(String(500), nullable=True)
    Patient_FirstName = Column(String(100), nullable=True)
    Patient_LastName = Column(String(100), nullable=True)
    Subscriber_ID = Column(String(100), nullable=True)
    Payer_Phone_Number = Column(String(50), nullable=True)
    Payer_Name = Column(String(255), nullable=True)
    Claims_Address = Column(String(500), nullable=True)
    Subscriber_FirstName = Column(String(100), nullable=True)
    Subscriber_LastName = Column(String(100), nullable=True)
    Relationship = Column(String(100), nullable=True)
    DOB = Column(DateTime, nullable=True)
    DOS = Column(DateTime, nullable=True)
    Charge = Column(String(50), nullable=True)
    Insurance_Payment = Column(String(50), nullable=True)
    Submission_Method = Column(String(100), nullable=True)
    Appeal_Submission_Date = Column(DateTime, nullable=True)
    Claim_Appeal = Column(String(100), nullable=True)
    Provider_Fax = Column(String(50), nullable=True)
    Provider_Email = Column(String(255), nullable=True)
    Unresponded_Claim = Column(String(100), nullable=True)
    Unresponded_Appeal = Column(String(100), nullable=True)
    Prefered_Payer_ID = Column(String(100), nullable=True)
    Prefered_Claims_Address = Column(String(500), nullable=True)
    Claims_TFL = Column(String(100), nullable=True)
    Processing_Delays = Column(String(100), nullable=True)
    Eligible = Column(String(100), nullable=True)
    CS_Agent_Name = Column(String(255), nullable=True)
    Call_Ref = Column(String(100), nullable=True)
    Department_Phone_Number = Column(String(50), nullable=True)
    Status = Column(String(100), nullable=True)
    Appeals_Address = Column(String(500), nullable=True)
    Received_date = Column(DateTime, nullable=True)
    Processing_Period = Column(String(100), nullable=True)
    Other_information = Column(String(1000), nullable=True)
    Processed_Date = Column(DateTime, nullable=True)
    Denial_reason = Column(String(500), nullable=True)
    Claim_Number = Column(String(100), nullable=True)
    Appealable = Column(String(100), nullable=True)
    Appeals_TFL = Column(String(100), nullable=True)
    Appeals_Submission_Method = Column(String(100), nullable=True)
    Online_Portal = Column(String(100), nullable=True)
    Appeals_Fax_Number = Column(String(50), nullable=True)
    Corrected_claims = Column(String(100), nullable=True)
    Paid_date = Column(DateTime, nullable=True)
    PaymentAmount = Column(String(50), nullable=True)
    Payment_Method = Column(String(100), nullable=True)
    Paid_To = Column(String(255), nullable=True)
    Check_Cashed = Column(String(100), nullable=True)
    Pay_to_address = Column(String(500), nullable=True)
    Check_Number = Column(String(100), nullable=True)
    Bank_Account = Column(String(100), nullable=True)
    Deposit_Date = Column(DateTime, nullable=True)
    Appeal_Ticket = Column(String(100), nullable=True)
    PR_Amount = Column(String(50), nullable=True)
    PR_Type = Column(String(100), nullable=True)
    Bulk_Payment = Column(String(100), nullable=True)
    CsvLink = Column(String(500), nullable=True)
    Group_ID = Column(String(100), nullable=True)
    # Relationships
    batchinfo = relationship("BatchInfo", back_populates="batchinfooutputs")

    __table_args__ = (
        Index("ix_batchinfooutput_batchinfoid", "BatchInfoId"),
        Index("ix_batchinfooutput_batchexecutionid", "BatchExecutionId"),
        Index("ix_batchinfooutput_callexecutionid", "CallNumberExecutionId"),
        Index("ix_batchinfooutput_createdat", "CreatedAt"),
    )