from typing import List, Optional, Literal
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer
from datetime import datetime, timezone

# -------------------------
# Auth / Users (unchanged)
# -------------------------
class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    class Config:
        from_attributes = True


# -------------------------
# Call Logs (legacy area)
# -------------------------
class CallStatusResponse(BaseModel):
    id: str
    call_status: str
    date: datetime
    model_config = ConfigDict(
        from_attributes=True,
        arbitrary_types_allowed=True
    )
    
    @field_serializer('date')
    def serialize_date(self, dt: datetime, _info):
        if dt:
            # MySQL DateTime doesn't store timezone, so add UTC if naive
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        return None

class CallLogResponse(BaseModel):
    id: str
    call_sid: str
    phone_number: str
    statuses: List[CallStatusResponse]
    recording_url: Optional[str] = None
    class Config:
        from_attributes = True

class PaginatedCallLogResponse(BaseModel):
    items: List[CallLogResponse]
    total_items: int

class CallLogsRequest(BaseModel):
    page: int = Field(1, ge=1, description="Page number, starting from 1")
    page_size: int = Field(10, ge=1, le=100, description="Number of items per page")


# -------------------------
# Conversation (unchanged)
# -------------------------
class ConversationResponse(BaseModel):
    id: str
    call_sid: str
    response_type: str
    response_text: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
    
    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime, _info):
        if dt:
            # MySQL DateTime doesn't store timezone, so add UTC if naive
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        return None


# =========================
# NEW CORE SCHEMAS
# =========================

# --- BatchExecution ---

class BatchExecutionSummary(BaseModel):
    id: str
    status: str
    completed_count: int = 0
    failed_count: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)
    
    @field_serializer('created_at', 'updated_at')
    def serialize_dt(self, dt: datetime, _info):
        if dt:
            # MySQL DateTime doesn't store timezone, so add UTC if naive
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        return None

class BatchExecutionDetail(BatchExecutionSummary):
    batch_id: str
    model_config = ConfigDict(from_attributes=True)


# --- BatchLogs ---

class BatchLogResponse(BaseModel):
    id: str
    log_type: Literal["StartBatch", "RunningBatch", "StopBatch", "CompletedBatch"]
    log_message: str
    batch_id: str
    batch_execution_id: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
    
    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime, _info):
        if dt:
            # MySQL DateTime doesn't store timezone, so add UTC if naive
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        return None


# --- CallNumber & CallNumberExecution ---

class CallNumberResponse(BaseModel):
    id: str
    phone_number: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
    
    @field_serializer('created_at')
    def serialize_dt(self, dt: datetime, _info):
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        return None


class CallNumberExecutionResponse(BaseModel):
    id: str
    call_number_id: str
    batch_execution_id: str
    call_sid: Optional[str] = None
    status: Optional[str] = None  # initiated|ringing|answered|completed|failed|busy|no-answer|canceled
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)
    
    @field_serializer('created_at', 'updated_at')
    def serialize_dt(self, dt: datetime, _info):
        if dt:
            # MySQL DateTime doesn't store timezone, so add UTC if naive
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        return None


# =========================
# Batch DTOs (API-level)
# =========================

class BatchCreateResponse(BaseModel):
    batch_id: str
    name: str
    phonenumbers: List[str]

class BatchResponse(BaseModel):
    # Core batch fields
    id: Optional[str] = None  # Batch ID from DB
    name: str
    script_path: Optional[str] = None
    total_count: Optional[int] = None

    # New: latest execution snapshot for quick UI display
    latest_execution: Optional[BatchExecutionSummary] = None

    # For convenience: include numbers if endpoint needs them
    callnumbers: Optional[List[CallNumberResponse]] = None

    # Back-compat UI helper (optional; can remove once FE migrates)
    status: Optional[str] = None
    completed_numbers: Optional[int] = None
    failed_numbers: Optional[int] = None

    # Runtime signals (not stored in DB)
    active_calls: int = 0
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True, extra='allow')



class BatchListResponse(BaseModel):
    items: List[BatchResponse]
    total_items: int


# =========================
# Start/Stop Requests
# =========================

class BatchStartRequest(BaseModel):
    batch_id: str
    concurrent_limit: int = Field(1, ge=1, le=20, description="Number of concurrent calls")
    # Support re-runs:
    # - 'all'            : run all numbers in the batch
    # - 'failed_only'    : run numbers whose latest execution is terminal and NOT completed
    # - 'pending_only'   : run numbers that have never completed (no completed exec)
    restart_mode: Literal["all", "failed_only", "pending_only"] = "all"

class BatchStopRequest(BaseModel):
    # If you want to stop the currently running execution for a batch, pass only batch_id.
    # If you support stopping a specific execution (recommended), include batch_execution_id.
    batch_id: Optional[str] = None
    batch_execution_id: Optional[str] = None


# =========================
# NEW: API Response Schemas
# =========================

class LoginSuccessResponse(BaseModel):
    msg: str
    email: str
    model_config = ConfigDict(extra='allow')

class OutboundCallInitiatedResponse(BaseModel):
    status: str
    callsid: str
    phonenumber: str
    model_config = ConfigDict(extra='allow')

class GenericMessageResponse(BaseModel):
    message: str
    callsid: Optional[str] = None
    model_config = ConfigDict(extra='allow')

class GenericDataResponse(BaseModel):
    data: str
    model_config = ConfigDict(extra='allow')

class RecordingUploadResponse(BaseModel):
    message: str
    callsid: str
    model_config = ConfigDict(extra='allow')

class SchemaInfoResponse(BaseModel):
    file_path: str
    kind: str
    default_sheet: Optional[str] = None
    model_config = ConfigDict(extra='allow')

# --- Batch Specific ---

class BatchStartResponse(BaseModel):
    batch_id: str
    batch_execution_id: str
    concurrent_limit: int
    queued: int
    running: List[str]
    stop_flag: bool
    model_config = ConfigDict(extra='allow')

class BatchStopDetails(BaseModel):
    completed_calls: List[str] = []
    failed_calls: List[str] = []
    model_config = ConfigDict(extra='allow')

class BatchStopResponse(BaseModel):
    batch_id: str
    message: str
    running_calls_canceled: int
    queued_calls_canceled: int
    total_stopped: int = 0
    final_completed: int = 0
    final_failed: int = 0
    details: Optional[BatchStopDetails] = None
    note: Optional[str] = None
    model_config = ConfigDict(extra='allow')

class BatchStatusResponse(BaseModel):
    batch_id: str
    execution_id: Optional[str] = None
    source: str
    # Fields present in 'memory' mode
    concurrent_limit: Optional[int] = None
    stop_flag: Optional[bool] = None
    running: Optional[List[str]] = None
    queued: Optional[int] = None
    # Fields present in 'database' mode
    status: Optional[str] = None
    completed_count: Optional[int] = None
    failed_count: Optional[int] = None
    model_config = ConfigDict(extra='allow')