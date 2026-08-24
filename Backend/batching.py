# batching_service.py
from __future__ import annotations
import os, asyncio
from dataclasses import dataclass, field
from typing import Deque, Optional as TypingOptional, Set, Dict, List
from collections import deque
from datetime import datetime, timezone
from fastapi.responses import JSONResponse
from fastapi import APIRouter, Header, Query, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, update
from twilio.rest import Client as TwilioClient

from Domain import models
from Domain.models import Batch, CallNumber
from database import get_dynamic_db, get_db_from_headers

from Repositories.BatchExecutionRepository import BatchExecutionRepository
from Repositories.BatchLogsRepository import BatchLogsRepository
from Repositories.CallNumberExecutionRepository import CallNumberExecutionRepository

from Websockets.connection import manager  # websocket broadcasting

from bot.batch_level_csv_created import finalize_batch_with_csv
import logging
import logging
from Domain import schema
from globals import call_hangup_reasons
logger = logging.getLogger(__name__)

# ------------------------------
# Constants / defaults
# ------------------------------
TEMP_DIR = os.path.join(os.getcwd(), "temp")
os.makedirs(TEMP_DIR, exist_ok=True)

DEFAULT_QUESTIONS = [
    "Hello, this is a provider office calling to check claim status.",
    "Can you verify the claim received date?",
    "What is the current status of the claim?",
    "If denied, what is the denial reason and appeal instructions?",
    "If pending, what is the missing information?",
    "Is there any TAT (turnaround time) you can share?",
    "Thank you. Have a great day."
]

TERMINAL_FAIL_STATUSES = {"failed", "busy", "canceled", "no-answer"}

# ------------------------------
# Dataclasses / Runtime State
# ------------------------------
@dataclass
class BatchState:
    batch_id: str
    concurrent_limit: int = 1
    stop_flag: bool = False
    running_calls: Set[str] = field(default_factory=set)   # active call SIDs
    pending_numbers: Deque[str] = field(default_factory=deque)  # holds CallNumber.Id values
    task: TypingOptional[asyncio.Task] = None
    email: str = "admin@gmail.com"
    questions_file_path: TypingOptional[str] = None
    waiters: Dict[str, asyncio.Task] = field(default_factory=dict)  # callSid -> waiter task
    accounted_canceled_running: Set[str] = field(default_factory=set)
    restart_mode: str = "all"
    batch_execution_id: TypingOptional[str] = None

    # live counters for UI
    total_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    completed_calls: Set[str] = field(default_factory=set)
    failed_calls: Set[str] = field(default_factory=set)

# ------------------------------
# Service
# ------------------------------
class BatchingService:
    def __init__(self):
        # Twilio client once
        self.twilio_client = TwilioClient(
            os.getenv("TWILIO_ACCOUNT_SID"),
            os.getenv("TWILIO_AUTH_TOKEN")
        )
        # Shared runtime state (moved from globals)
        self.active_batches: Dict[str, BatchState] = {}
        self.active_calls: Dict[str, asyncio.Event] = {}     # callSid -> Event
        self.callstatus_map: Dict[str, str] = {}             # callSid -> last status
        self.call_to_batch: Dict[str, str] = {}              # callSid -> batch_id
        self.call_to_callnumber: Dict[str, str] = {}         # callSid -> CallNumber.Id
        self.call_to_execution: Dict[str, str] = {}          # callSid -> BatchExecution.Id
        self.call_to_csv: Dict[str, str] = {}                # callSid -> CSV URL
        self.call_to_config: Dict[str, dict] = {}            # callSid -> Sarvam agent config (script/lang/gender)

    # --------------------------
    async def wait_for_batch_extractions(self, batch_execution_id: str, email: str) -> None:
        """
        Wait for all completed calls in the batch execution to have their extractions saved to BatchInfoOutput.
        Uses exponential backoff polling to avoid database hammering.
        Creates its own database session.
        """
        import asyncio
        from Domain import models

        db = get_dynamic_db(email)
        try:
            # Count completed calls that should have extractions
            completed_calls = db.query(models.CallNumberExecution).filter(
                models.CallNumberExecution.BatchExecutionId == batch_execution_id,
                models.CallNumberExecution.Status == "completed",
                models.CallNumberExecution.IsDeleted == False
            ).count()

            if completed_calls == 0:
                logger.info("No completed calls to wait for extractions")
                return

            logger.info(f"Waiting for {completed_calls} completed calls to have extractions saved")

            max_wait = 25
            initial_delay = 0.5
            max_delay = 5.0
            delay = initial_delay
            elapsed = 0.0

            while elapsed < max_wait:
                # Check current extraction count - use a fresh session to see latest commits
                session = get_dynamic_db(email)
                try:
                    current_extractions = session.query(models.BatchInfoOutput).filter(
                        models.BatchInfoOutput.BatchExecutionId == batch_execution_id,
                        models.BatchInfoOutput.IsDeleted == False
                    ).count()
                except Exception as e:
                    logger.error(f"Error checking extractions: {e}")
                    current_extractions = 0
                finally:
                    session.close()

                logger.info(f"Extractions check: {current_extractions}/{completed_calls} (elapsed: {elapsed:.1f}s)")

                if current_extractions >= completed_calls:
                    logger.info(f"✅ All {current_extractions} extractions found for {completed_calls} completed calls")
                    return

                await asyncio.sleep(delay)
                elapsed += delay
                delay = min(delay * 1.5, max_delay) 

            logger.warning(f"⚠️ Timeout waiting for extractions: got {current_extractions}/{completed_calls} after {max_wait}s. Proceeding anyway.")
        finally:
            # db (the initial one) is already closed or barely used, but good to ensure cleanup if it was used for completed_calls
            if 'db' in locals() and db:
                db.close()

    # Utils
    # --------------------------
    @staticmethod
    def _file_exists(p: TypingOptional[str]) -> bool:
        try:
            return bool(p) and os.path.isfile(p)
        except Exception:
            return False

    @staticmethod
    def _validate_phonenumber(number: str):
        if not number or not isinstance(number, str):
            return JSONResponse(status_code=400, content={"errors": ["Invalid phone number"]})

    def _get_call_signal(self, call_sid: str) -> asyncio.Event:
        ev = self.active_calls.get(call_sid)
        if ev is None:
            ev = asyncio.Event()
            self.active_calls[call_sid] = ev
        return ev

    # --------------------------
    # Twilio Dial Core
    # --------------------------
    async def make_single_call_core_for_batch(
        self,
        db: Session,
        email: str,
        to_number: str,
        batch_id: str,
        batch_execution_id: str,
        questions_file_path: TypingOptional[str] = None,
        call_number_id: TypingOptional[str] = None,
    ) -> str:
        self._validate_phonenumber(to_number)

        # Public base URL of THIS unified app. Batch calls are placed onto the
        # same Sarvam webhooks the UI uses (SERVER_URL), falling back to the
        # legacy NGROK_URL if SERVER_URL is unset.
        base_url = (os.getenv("SERVER_URL") or os.getenv("NGROK_URL") or "").rstrip("/")

        # resolve questions path
        resolved_path = questions_file_path if self._file_exists(questions_file_path) else None
        if not resolved_path:
            try:
                batch_row = db.query(models.Batch).filter(
                    models.Batch.Id == batch_id,
                    models.Batch.IsDeleted == False
                ).first()
                if batch_row and self._file_exists(batch_row.ScriptPath):
                    resolved_path = batch_row.ScriptPath
            except Exception:
                pass
        if not resolved_path:
            env_path = os.getenv("INPUT_FILE")
            if self._file_exists(env_path):
                resolved_path = env_path

        questions: List[str] = []
        # if resolved_path:
        #     try:
        #         with open(resolved_path, "r", encoding="utf-8") as f:
        #             questions = [line.strip() for line in f if line.strip()]
        #     except Exception:
        #         pass
        if not questions:
            questions = list(DEFAULT_QUESTIONS)
        if not questions:
            return JSONResponse(status_code=400, content={"errors":["Questions are empty after all fallbacks"]})

        # place call
        print(f"☎️ DIAL: batch={batch_id} exec={batch_execution_id} call_number_id={call_number_id} to={to_number}")
        # Place call asynchronously (offload blocking I/O)
        def _managed_dial():
            return self.twilio_client.calls.create(
                to=to_number,
                record=True,
                from_=os.getenv("TWILIO_PHONE_NUMBER"),
                url=f"{base_url}/outbound-twiml",
                status_callback=f"{base_url}/call-status",
                recording_status_callback=f"{base_url}/twilio/recording-callback",
                status_callback_event=[
                    "initiated","ringing","answered","in-progress","completed",
                    "busy","canceled","failed","no-answer"
                ]
            )
        
        call = await asyncio.to_thread(_managed_dial)
        print(f"✅ DIAL-OK: sid={call.sid}")

        # Register the Sarvam agent config for this batch call so the unified
        # /outbound-twiml + /media-stream can build the right SimpleAgent
        # (the batch's XML script becomes the agent prompt). UI calls populate
        # this via /api/make-call instead.
        try:
            self.call_to_config[call.sid] = {
                "script_path": resolved_path,
                "language": os.getenv("BATCH_DEFAULT_LANGUAGE", "multi"),
                "gender": os.getenv("BATCH_DEFAULT_GENDER", "female"),
                "phone_number": to_number,
            }
        except Exception:
            pass

        # debug questions file
        # final_file_path = os.path.join(TEMP_DIR, f"{call.sid}.txt")
        # with open(final_file_path, "w", encoding="utf-8") as out:
        #     out.write("\n".join(questions))

        # insert CNE
        cne_repo = CallNumberExecutionRepository()
        try:
            await cne_repo.add(db, {
                "CallNumberId": call_number_id,
                "BatchExecutionId": batch_execution_id,
                "CallSid": call.sid,
                "Status": "ringing",
                "CreatedBy": "system"
            })
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"❌ Failed to insert CallNumberExecution for sid={call.sid}: {e}")

        # runtime maps
        self.call_to_callnumber[call.sid] = call_number_id or ""
        self.call_to_execution[call.sid] = batch_execution_id

        return call.sid

    # --------------------------
    # Internal helpers
    # --------------------------
    def _spawn_waiter_for_call(self, call_sid: str, state: BatchState):
        ev = self._get_call_signal(call_sid)

        async def _wait():
            try:
                await ev.wait()
            except asyncio.CancelledError:
                print(f" Waiter task cancelled for call {call_sid}")
                raise
            finally:
                self.active_calls.pop(call_sid, None)
            return call_sid

        t = asyncio.create_task(_wait())
        state.waiters[call_sid] = t

    async def _check_and_update_call_statuses(self, state: BatchState):
        if not state.running_calls or not state.batch_execution_id:
            return
        try:
            cne_repo = CallNumberExecutionRepository()
            db = get_dynamic_db(state.email)

            for call_sid in list(state.running_calls):
                try:
                    # Offload blocking fetch
                    call_obj = await asyncio.to_thread(lambda: self.twilio_client.calls(call_sid).fetch())
                    current_status = call_obj.status
                    cne = await cne_repo.get_by_sid(db, call_sid)
                    if not cne or cne.BatchExecutionId != state.batch_execution_id:
                        continue
                    if cne.Status == "ringing" and current_status == "in-progress":
                        print(f"📞 STATUS CHANGE: {call_sid} ringing → in-progress")
                        cne.Status = "in-progress"
                        cne.UpdatedAt = datetime.now(timezone.utc)
                        cne.UpdatedBy = "system"
                        await cne_repo.update(db, cne)
                        print(f"✅ CNE UPDATED: {call_sid} in-progress")
                except Exception as e:
                    print(f"⚠️ Status check error for {call_sid}: {e}")
            db.commit()
        except Exception as e:
            print(f"❌ Status monitor error: {e}")
        finally:
            db.close()

    # --------------------------
    # Core runner
    # --------------------------
    async def run_batch(self, state: BatchState, email: str):
        db = get_dynamic_db(state.email)
        bx_repo = BatchExecutionRepository()
        logs_repo = BatchLogsRepository()
        try:
            # ensure execution id
            if not state.batch_execution_id:
                bx = await bx_repo.create_running(db, batch_id=state.batch_id, created_by=email, restart_mode=state.restart_mode)
                state.batch_execution_id = bx.Id
            else:
                bx = await bx_repo.get_by_id(db, state.batch_execution_id)
                execution_id = bx.Id
                print(f"batch execution id: {execution_id}")

            batch_row = db.query(models.Batch).filter(
                models.Batch.Id == state.batch_id,
                models.Batch.IsDeleted == False
            ).first()
            batch_name = batch_row.Name if batch_row else state.batch_id

            await logs_repo.log(
                db,
                batch_id=state.batch_id,
                batch_execution_id=state.batch_execution_id,
                log_type="StartBatch",
                message=f"Batch '{batch_name}' started by {email}",
                created_by="system"
            )

            # initial broadcast
            await manager.broadcast_to_batch(state.batch_id, self._status_payload(state, "running"))
        finally:
            db.close()

        async def _start_one_number() -> bool:
            if state.stop_flag or len(state.running_calls) >= state.concurrent_limit or not state.pending_numbers:
                return False
            call_number_id = state.pending_numbers.popleft()
            db_local = get_dynamic_db(state.email)
            try:
                cn = (db_local.query(models.CallNumber)
                      .filter(models.CallNumber.Id == call_number_id,
                              models.CallNumber.BatchId == state.batch_id,
                              models.CallNumber.IsDeleted == False)
                      .first())
                if not cn:
                    return False

                print(f"⏳ START call_number_id={call_number_id} to={cn.PhoneNumber} email={state.email}")
                call_sid = await self.make_single_call_core_for_batch(
                    db=db_local,
                    email=state.email,
                    to_number=cn.PhoneNumber,
                    batch_id=state.batch_id,
                    batch_execution_id=state.batch_execution_id or "",
                    questions_file_path=state.questions_file_path,
                    call_number_id=cn.Id,
                )

                

                # immediate broadcast (new running call)
                snap = self._status_payload(state, "running")
                snap["running"] = snap.get("running", 0) + 1
                snap["running_calls"] = snap.get("running_calls", []) + [call_sid]
                snap["running_calls_with_ids"] = snap.get("running_calls_with_ids", []) + [
                    {"callSid": call_sid, "callNumberId": self.call_to_callnumber.get(call_sid)}
                ]
                await manager.broadcast_to_batch(state.batch_id, snap)

                # Broadcast updated active call count
                state.running_calls.add(call_sid)
                self.call_to_batch[call_sid] = state.batch_id
                self._spawn_waiter_for_call(call_sid, state)
                total_active_calls = sum(len(state.running_calls) for state in self.active_batches.values())
                await manager.broadcast_active_call_count(total_active_calls)
                
                return True
            except Exception as e:
                print(f"❌ _start_one_number error: {e}")
                return False
            finally:
                db_local.close()

        # initial fill
        progressed = True
        while progressed:
            progressed = await _start_one_number()

        try:
            while True:
                if (not state.running_calls and not state.pending_numbers) or \
                   (state.stop_flag and not state.running_calls and not state.pending_numbers):
                    break

                if state.stop_flag and not state.running_calls:
                    print(f"🛑 IMMEDIATE STOP: cleared for batch {state.batch_id}")
                    break

                while len(state.running_calls) < state.concurrent_limit and state.pending_numbers and not state.stop_flag:
                    started = await _start_one_number()
                    if not started:
                        break

                # opportunistic status updates
                if state.running_calls and state.batch_execution_id:
                    try:
                        from Domain.models import CallNumberExecution
                        db = get_dynamic_db(state.email)
                        needs_check = db.query(CallNumberExecution).filter(
                            CallNumberExecution.BatchExecutionId == state.batch_execution_id,
                            CallNumberExecution.IsDeleted == False,
                            CallNumberExecution.Status != "in-progress",
                            CallNumberExecution.CallSid.in_(list(state.running_calls))
                        ).first()
                        db.close()
                        if needs_check:
                            await self._check_and_update_call_statuses(state)
                    except Exception as e:
                        print(f"⚠️ optimized check failed: {e}")
                        await self._check_and_update_call_statuses(state)

                if not state.waiters:
                    await asyncio.sleep(0.2)
                    continue

                done, _ = await asyncio.wait(state.waiters.values(), return_when=asyncio.FIRST_COMPLETED, timeout=0.5)
                for t in done:
                    try:
                        ended_sid = t.result()
                        state.waiters.pop(ended_sid, None)
                        state.running_calls.discard(ended_sid)
                        self.call_to_batch.pop(ended_sid, None)
                        if not state.stop_flag:
                            await _start_one_number()
                    except asyncio.CancelledError:
                        for sid, task in list(state.waiters.items()):
                            if task == t:
                                state.waiters.pop(sid, None)
                                state.running_calls.discard(sid)
                                self.call_to_batch.pop(sid, None)
                                break
                    except Exception as e:
                        print(f"❌ waiter err: {e}")
                        for sid, task in list(state.waiters.items()):
                            if task == t:
                                state.waiters.pop(sid, None)
                                state.running_calls.discard(sid)
                                self.call_to_batch.pop(sid, None)
                                break
        except asyncio.CancelledError:
            print(f"🛑 Batch {state.batch_id} loop CANCELLED (Shutdown/Stop)")
            # Handle graceful shutdown for active items
            if state.batch_execution_id:
                db = get_dynamic_db(state.email)
                try:
                    cne_repo = CallNumberExecutionRepository()
                    bx_repo = BatchExecutionRepository()
                    
                    # 1. Handle Running Calls
                    running_sids = list(state.running_calls)
                    completed_add = 0
                    failed_add = 0
                    
                    for sid in running_sids:
                        call_hangup_reasons[sid] = "Call cancelled by stopping batch"
                        try:
                            # Check last known status from DB or memory
                            cne = await cne_repo.get_by_sid(db, sid)
                            if cne and cne.Status == "in-progress":
                                # User wants in-progress -> completed on shutdown
                                cne.Status = "completed"
                                cne.UpdatedAt = datetime.now(timezone.utc)
                                cne.UpdatedBy = "system-shutdown"
                                await cne_repo.update(db, cne)
                                completed_add += 1
                                state.completed_count += 1
                                state.completed_calls.add(sid)
                                print(f"✅ Shutdown: Marked {sid} as completed")
                            elif cne and cne.Status == "ringing":
                                # Ringing -> canceled
                                cne.Status = "canceled"
                                cne.UpdatedAt = datetime.now(timezone.utc)
                                cne.UpdatedBy = "system-shutdown"
                                await cne_repo.update(db, cne)
                                failed_add += 1
                                state.failed_count += 1
                                state.failed_calls.add(sid)
                                print(f"⚠️ Shutdown: Marked {sid} as canceled")
                        except Exception as e:
                            print(f"❌ Error cleaning up running call {sid}: {e}")

                    # 2. Handle Queued Numbers
                    queued_ids = list(state.pending_numbers)
                    for qid in queued_ids:
                        try:
                            await cne_repo.add(db, {
                                "CallNumberId": qid,
                                "BatchExecutionId": state.batch_execution_id,
                                "CallSid": None,
                                "Status": "canceled",
                                "CreatedBy": "system-shutdown"
                            })
                            failed_add += 1
                            state.failed_count += 1
                            state.failed_calls.add(f"QUEUED-{qid[:8]}")
                        except Exception as e:
                            print(f"❌ Error cleaning up queued number {qid}: {e}")

                    # Commit status updates FIRST so recompute sees them
                    db.commit()

                    # Update Batch Counts (Recompute for accuracy)
                    await bx_repo.recompute_counters_from_db(db, state.batch_execution_id)
                    
                    # Commit the updated counts
                    db.commit()
                except Exception as e:
                    print(f"❌ Error during batch shutdown cleanup: {e}")
                    db.rollback()
                finally:
                    db.close()
            # Ensure we mark as stopped for the finally block
            state.stop_flag = True
            raise
        finally:
            # Wrap cleanup in try/except to ensure we don't crash the loop without updating DB
            try:
                db = get_dynamic_db(state.email)
                bx_repo = BatchExecutionRepository()
                logs_repo = BatchLogsRepository()
                try:
                    # Determine final status
                    # If stopped manually OR cancelled (shutdown) -> "stopped"
                    # If natural finish -> "completed"
                    
                    final_status = "completed" 
                    if state.stop_flag:
                        final_status = "stopped"
                    elif not state.pending_numbers and not state.running_calls:
                        final_status = "completed"
                    
                    if state.batch_execution_id:
                        # Retry logic for status update
                        for attempt in range(3):
                            try:
                                await bx_repo.set_status(db, state.batch_execution_id, final_status)
                                break
                            except Exception as e:
                                print(f"⚠️ Failed to set batch status (attempt {attempt+1}): {e}")
                                await asyncio.sleep(1)
                        
                        batch_row = db.query(models.Batch).filter(
                            models.Batch.Id == state.batch_id,
                            models.Batch.IsDeleted == False
                        ).first()
                        batch_name = batch_row.Name if batch_row else state.batch_id
                        
                        try:
                            await logs_repo.log(
                                db,
                                batch_id=state.batch_id,
                                batch_execution_id=state.batch_execution_id,
                                log_type="CompletedBatch",
                                message=f"Batch '{batch_name}' ended with status: {final_status}",
                                created_by="system"
                            )
                        except Exception as log_err:
                            print(f"⚠️ Failed to log batch completion: {log_err}")

                    # final broadcast
                    try:
                        await manager.broadcast_to_batch(state.batch_id, self._status_payload(state, final_status))
                    except Exception as bc_err:
                        print(f"⚠️ Failed final broadcast: {bc_err}")

                    # cleanup + notify running list
                    try:
                        await asyncio.sleep(1.0)
                        await manager.cleanup_batch_only_connections(state.batch_id)
                        if state.batch_id in self.active_batches:
                            del self.active_batches[state.batch_id]
                        running_batch_ids = [bid for bid, s in self.active_batches.items()
                                             if s.task and not s.task.done() and not s.stop_flag]
                        await manager.broadcast_running_batch_ids(running_batch_ids)
                    except Exception as e:
                        print(f"❌ cleanup error: {e}")

                    try:
                        # Use exponential backoff to wait for all completed calls' extractions
                        await service.wait_for_batch_extractions(state.batch_execution_id, state.email)
                        csv_success = await finalize_batch_with_csv(
                            state.batch_id,
                            state.batch_execution_id,
                            state.email
                        )
                        if csv_success:
                            logger.info(f"✅ CSV generated for {state.batch_id}")
                        else:
                            logger.warning(f"⚠️ CSV failed for {state.batch_id}")
                    except Exception as csv_e:
                        logger.error(f" Error generating batch CSV: {csv_e}")
                finally:
                    db.close()
            except Exception as critical_e:
                print(f"🔥 CRITICAL ERROR in run_batch finally block: {critical_e}")
                import traceback; traceback.print_exc()

    # --------------------------
    # Public Ops
    # --------------------------
    async def update_counters_for_call(self, callsid: str, callstatus: str, tenant_email: str) -> None:
        db = get_dynamic_db(tenant_email)
        cne_repo = CallNumberExecutionRepository()
        bx_repo = BatchExecutionRepository()

        try:
            cne = await cne_repo.get_by_sid(db, callsid)
            if not cne:
                print(f"⚠️ No CNE for sid={callsid}")
                return

            exec_id = cne.BatchExecutionId
            batch_id = self.call_to_batch.get(callsid)
            st = self.active_batches.get(batch_id) if batch_id else None

            # OPTIMIZATION: Use the passed callstatus instead of fetching again
            current_twilio_status = callstatus
            # print(f"📞 STATUS UPDATE: sid={callsid}, status={current_twilio_status}")

            already_accounted_cancel = (
                current_twilio_status == "canceled"
                and st is not None
                and hasattr(st, "accounted_canceled_running")
                and callsid in st.accounted_canceled_running
            )

            old_status = cne.Status
            cne.Status = current_twilio_status
            cne.UpdatedAt = datetime.now(timezone.utc)
            cne.UpdatedBy = "system"
            
            # Retry logic for CNE update
            for attempt in range(3):
                try:
                    await cne_repo.update(db, cne)
                    break
                except Exception as e:
                    print(f"⚠️ Failed to update CNE status (attempt {attempt+1}): {e}")
                    await asyncio.sleep(0.5)
            
            print(f"📝 CNE updated: sid={callsid}, old={old_status}, live={current_twilio_status}")

            if current_twilio_status in {"in-progress", "answered"}:
                try:
                    db.commit()
                    print(f"✅ saved intermediate status for {callsid}")
                except Exception as e:
                    print(f"❌ save intermediate failed: {e}")
                    db.rollback()

            should_increment = False
            # Define terminal statuses
            terminal_statuses = {"completed", "busy", "failed", "canceled", "no-answer"}
            
            # Only increment if we are NOT coming from a terminal status
            # This prevents double-counting if we get duplicate webhooks or race conditions
            if old_status not in terminal_statuses:
                if not already_accounted_cancel:
                    if current_twilio_status == "completed":
                        # Retry logic for increment
                        for attempt in range(3):
                            try:
                                await bx_repo.increment_completed(db, exec_id, by=1)
                                break
                            except Exception as e:
                                print(f"⚠️ Failed to increment completed (attempt {attempt+1}): {e}")
                                await asyncio.sleep(0.5)
                                
                        should_increment = True
                        if st:
                            st.completed_count += 1
                            st.completed_calls.add(callsid)
                    elif current_twilio_status in {"busy","failed","no-answer","canceled"}:
                        # Retry logic for increment
                        for attempt in range(3):
                            try:
                                await bx_repo.increment_failed(db, exec_id, by=1)
                                break
                            except Exception as e:
                                print(f"⚠️ Failed to increment failed (attempt {attempt+1}): {e}")
                                await asyncio.sleep(0.5)
                                
                        should_increment = True
                        if st:
                            st.failed_count += 1
                            st.failed_calls.add(callsid)
                else:
                    st.accounted_canceled_running.discard(callsid)
                    print(f"ℹ️ skip double-count canceled {callsid}")
            else:
                print(f"ℹ️ SKIPPING increment: {callsid} was already {old_status} (terminal)")

            # Final commit with retry
            for attempt in range(3):
                try:
                    db.commit()
                    break
                except Exception as e:
                    print(f"⚠️ Failed to commit counters (attempt {attempt+1}): {e}")
                    db.rollback()
                    await asyncio.sleep(0.5)

            if should_increment and batch_id and st:
                if current_twilio_status in {"completed","busy","failed","canceled","no-answer"}:
                    st.running_calls.discard(callsid)
                    # Broadcast updated active call count when a call ends
                    total_active_calls = sum(len(state.running_calls) for state in self.active_batches.values())
                    await manager.broadcast_active_call_count(total_active_calls)

                await manager.broadcast_to_batch(batch_id, self._status_payload(st, "running" if not st.stop_flag else "stopped"))
        except Exception as e:
            db.rollback()
            print(f"❌ update_counters_for_call error for {callsid}: {e}")
            import traceback; traceback.print_exc()
            raise
        finally:
            db.close()

    async def schedule_batch_broadcasts_4_times(self, batch_id: str):
        state = self.active_batches.get(batch_id)
        if not state:
            print(f"❌ schedule: batch {batch_id} not found")
            return
        print(f"📡 SCHEDULING 3 BROADCASTS for batch {batch_id}")
        for i in range(3):
            await manager.broadcast_to_batch(batch_id, self._status_payload(state, "running"))
            if i < 2:
                await asyncio.sleep(3)
        print(f"✅ COMPLETED scheduled broadcasts for {batch_id}")

    # --------------------------
    # API Endpoint Helpers
    # --------------------------
    def _select_numbers_for_mode(self, db: Session, batch_id: str, restart_mode: str) -> List[str]:
        cne = models.CallNumberExecution

        latest_exec = (
            db.query(models.BatchExecution)
              .filter(models.BatchExecution.IsDeleted == False,
                      models.BatchExecution.BatchId == batch_id)
              .order_by(models.BatchExecution.CreatedAt.desc())
              .first()
        )

        base_q = db.query(models.CallNumber).filter(
            models.CallNumber.BatchId == batch_id,
            models.CallNumber.IsDeleted == False
        )

        if restart_mode == "all" or latest_exec is None:
            rows = base_q.order_by(models.CallNumber.CreatedAt.asc()).all()
            return [r.Id for r in rows]

        exec_filtered = db.query(cne).filter(
            cne.IsDeleted == False,
            cne.BatchExecutionId == latest_exec.Id
        ).subquery()

        sub_last = (
            db.query(
                exec_filtered.c.CallNumberId.label("CallNumberId"),
                func.max(exec_filtered.c.CreatedAt).label("mx")
            )
            .group_by(exec_filtered.c.CallNumberId)
            .subquery()
        )

        last_attempt = (
            base_q
            .join(sub_last, sub_last.c.CallNumberId == models.CallNumber.Id, isouter=True)
            .join(exec_filtered,
                  and_(exec_filtered.c.CallNumberId == models.CallNumber.Id,
                       exec_filtered.c.CreatedAt == sub_last.c.mx),
                  isouter=True)
        )

        if restart_mode == "failed_only":
            rows = (
                last_attempt
                .filter(exec_filtered.c.Status.in_(list(TERMINAL_FAIL_STATUSES)))
                .order_by(models.CallNumber.CreatedAt.asc())
                .all()
            )
            callnumbers = [r[0] if isinstance(r, tuple) else r for r in rows]
            return [r.Id for r in callnumbers]

        rows = (
            last_attempt
            .filter(or_(exec_filtered.c.Status.is_(None), exec_filtered.c.Status != "completed"))
            .order_by(models.CallNumber.CreatedAt.asc())
            .all()
        )
        callnumbers = [r[0] if isinstance(r, tuple) else r for r in rows]
        return [r.Id for r in callnumbers]

    def _status_payload(self, st: BatchState, status: str) -> dict:
        return {
            "type": "batch_status",
            "id": st.batch_id,
            "execution_id": st.batch_execution_id,
            "status": status,
            "concurrent_limit": st.concurrent_limit,
            "totalCount": st.total_count,
            "completedCount": st.completed_count,
            "failedCount": st.failed_count,
            "completed_calls": list(st.completed_calls),
            "failed_calls": list(st.failed_calls),
            "queued": len(st.pending_numbers),
            "running": len(st.running_calls),
            "running_calls": list(st.running_calls),
            "running_calls_with_ids": [
                {"callSid": sid, "callNumberId": self.call_to_callnumber.get(sid)}
                for sid in st.running_calls
            ],
            "csv_links_with_ids": [
                {
                    "callSid": sid,
                    "callNumberId": self.call_to_callnumber.get(sid),
                    "csvLink": csv_link
                }
                for sid, csv_link in self.call_to_csv.items()
                if self.call_to_batch.get(sid) == st.batch_id 
                and (sid in st.completed_calls or sid in st.failed_calls or sid in st.running_calls)
            ],
            "stop_flag": st.stop_flag,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    # --------------------------
    # FastAPI endpoint methods
    # --------------------------
    async def start_batch(
        self,
        batch_id: str,
        concurrent_limit: int,
        restart_mode: str,
        email: str
    ):
        if not email:
            email = "synergenadmin@yopmail.com"
        db = get_dynamic_db(email)
        try:
            batch_row = (
                db.query(models.Batch)
                .filter(models.Batch.Id == batch_id, models.Batch.IsDeleted == False)
                .first()
            )
            if not batch_row:
                return JSONResponse(status_code=404, content={"errors": ["Batch not found"]})

            script_path = batch_row.ScriptPath or None
            if script_path and not os.path.isabs(script_path):
                script_path = os.path.join(os.getcwd(), script_path)
            if script_path and not os.path.isfile(script_path):
                script_path = None

            ids = self._select_numbers_for_mode(db, batch_id, restart_mode)
            total_count = len(ids)
            if not ids:
                return JSONResponse(status_code=400, content={"errors": ["No numbers found for this selection"]})

            bx_repo = BatchExecutionRepository()
            bx = await bx_repo.create_running(db, batch_id=batch_id, created_by=email, restart_mode=restart_mode)
            batch_execution_id = bx.Id

            if batch_id in self.active_batches:
                st = self.active_batches[batch_id]
                st.concurrent_limit = concurrent_limit
                st.email = email
                st.questions_file_path = script_path
                st.total_count = total_count
                st.restart_mode = restart_mode
                st.batch_execution_id = batch_execution_id
                for cid in ids:
                    st.pending_numbers.append(cid)
                if st.task is None or st.task.done():
                    st.task = asyncio.create_task(self.run_batch(st, email=email))
            else:
                st = BatchState(
                    batch_id=batch_id,
                    concurrent_limit=concurrent_limit,
                    stop_flag=False,
                    pending_numbers=deque(ids),
                    email=email,
                    questions_file_path=script_path,
                    total_count=total_count,
                    restart_mode=restart_mode,
                    batch_execution_id=batch_execution_id
                )
                st.task = asyncio.create_task(self.run_batch(st, email=email))
                self.active_batches[batch_id] = st

            asyncio.create_task(self.schedule_batch_broadcasts_4_times(batch_id))

            running_batch_ids = [bid for bid, state in self.active_batches.items()
                                 if state.task and not state.task.done() and not state.stop_flag]
            if running_batch_ids:
                asyncio.create_task(manager.broadcast_running_batch_ids(running_batch_ids))

            return {
                "batch_id": batch_id,
                "batch_execution_id": batch_execution_id,
                "concurrent_limit": self.active_batches[batch_id].concurrent_limit,
                "queued": len(self.active_batches[batch_id].pending_numbers),
                "running": list(self.active_batches[batch_id].running_calls),
                "stop_flag": self.active_batches[batch_id].stop_flag
            }
        finally:
            db.close()

    async def stop_batch(self, batch_id: str, email: str):
        if not email:
            email = "synergenadmin@yopmail.com"

        st = self.active_batches.get(batch_id)
        
        # Fallback: if not in memory, check DB for "running" state
        if not st:
            db = get_dynamic_db(email)
            try:
                bx_repo = BatchExecutionRepository()
                running_exec = await bx_repo.running_for_batch(db, batch_id)
                if running_exec:
                    print(f"⚠️ Batch {batch_id} not in memory but running in DB (id={running_exec.Id}). Force stopping.")
                    
                    # 1. Cleanup stuck calls in DB
                    # "in-progress" -> "completed" (assume they finished but we missed the webhook)
                    db.execute(
                        update(models.CallNumberExecution)
                        .where(models.CallNumberExecution.BatchExecutionId == running_exec.Id)
                        .where(models.CallNumberExecution.Status == "in-progress")
                        .values(Status="completed", UpdatedAt=datetime.now(timezone.utc), UpdatedBy="system-cleanup")
                    )
                    
                    # "ringing", "queued" -> "canceled" (they never started or finished)
                    db.execute(
                        update(models.CallNumberExecution)
                        .where(models.CallNumberExecution.BatchExecutionId == running_exec.Id)
                        .where(models.CallNumberExecution.Status.in_(["ringing", "queued", "pending"]))
                        .values(Status="canceled", UpdatedAt=datetime.now(timezone.utc), UpdatedBy="system-cleanup")
                    )
                    db.commit()
                    
                    # 2. Recompute counters from DB to ensure accuracy
                    await bx_repo.recompute_counters_from_db(db, running_exec.Id)
                    
                    # 3. Mark batch as stopped
                    await bx_repo.set_status(db, running_exec.Id, "stopped", updated_by=email)
                    
                    # Log the forced stop
                    logs_repo = BatchLogsRepository()
                    batch_row = db.query(models.Batch).filter(models.Batch.Id == batch_id).first()
                    batch_name = batch_row.Name if batch_row else batch_id
                    
                    await logs_repo.log(
                        db,
                        batch_id=batch_id,
                        batch_execution_id=running_exec.Id,
                        log_type="StopBatch",
                        message=f"Batch '{batch_name}' force-stopped by {email} (was stuck in DB). Cleaned up call statuses.",
                        created_by="system"
                    )
                    db.commit()
                    
                    # Fetch final counts for response
                    final_exec = await bx_repo.get_by_id(db, running_exec.Id)
                    
                    return {
                        "batch_id": batch_id,
                        "message": "Batch was stuck and has been force-stopped. Call statuses cleaned up.",
                        "running_calls_canceled": 0,
                        "queued_calls_canceled": 0,
                        "total_stopped": 0,
                        "final_completed": final_exec.CompletedCount if final_exec else 0,
                        "final_failed": final_exec.FailedCount if final_exec else 0,
                        "note": "Batch was not active in memory but marked running in DB. Stuck calls were updated."
                    }
            except Exception as e:
                print(f"❌ Error checking DB for stuck batch {batch_id}: {e}")
                import traceback; traceback.print_exc()
            finally:
                db.close()

            return JSONResponse(status_code=404, content={"errors": ["Batch not found or not running"]})

        st.stop_flag = True
        if hasattr(st, "pings_task") and st.pings_task and not st.pings_task.done():
            st.pings_task.cancel()
            st.pings_task = None

        print(f"🛑 STOPPING BATCH: {batch_id} - stopping everything immediately")

        num_queued_canceled = 0
        num_running_canceled = 0

        # (A) queued numbers -> create canceled CNE
        if st.batch_execution_id and st.pending_numbers:
            db = get_dynamic_db(st.email)
            try:
                cne_repo = CallNumberExecutionRepository()
                bx_repo = BatchExecutionRepository()

                pending_ids = list(st.pending_numbers)
                print(f"🛑 STOP DEBUG: pending_ids count={len(pending_ids)}")
                CNE = models.CallNumberExecution
                existing_cne = db.query(CNE).filter(
                    CNE.BatchExecutionId == st.batch_execution_id,
                    CNE.IsDeleted == False,
                    CNE.CallNumberId.in_(pending_ids)
                ).all()
                existing_cne_ids = {cne.CallNumberId for cne in existing_cne}
                print(f"🛑 STOP DEBUG: existing_cne_ids count={len(existing_cne_ids)}")

                calls_to_cancel = [cid for cid in pending_ids if cid not in existing_cne_ids]
                print(f"🛑 STOP DEBUG: calls_to_cancel count={len(calls_to_cancel)}")
                for call_number_id in calls_to_cancel:
                    await cne_repo.add(db, {
                        "CallNumberId": call_number_id,
                        "BatchExecutionId": st.batch_execution_id,
                        "CallSid": None,  # stored as NULL
                        "Status": "canceled",
                        "CreatedBy": "system",
                    })
                    num_queued_canceled += 1
                    st.failed_calls.add(f"QUEUED-{call_number_id[:8]}")

                if num_queued_canceled > 0:
                    await bx_repo.increment_failed(db, st.batch_execution_id, by=num_queued_canceled)
                    st.failed_count += num_queued_canceled

                db.commit()
            except Exception as e:
                import traceback
                print(f"❌ stop_batch queued CNE error: {e}")
                traceback.print_exc()
                db.rollback()
            finally:
                db.close()

        # (B) running calls -> terminate
        running_call_sids = list(st.running_calls)
        print(f"🛑 STOP DEBUG: running_call_sids count={len(running_call_sids)}")
        if running_call_sids:
            print(f"🔍 RUNNING CALLS TO STOP: {len(running_call_sids)}")
            st.accounted_canceled_running.update(running_call_sids)

            if st.batch_execution_id:
                db = get_dynamic_db(st.email)
                try:
                    cne_repo = CallNumberExecutionRepository()
                    for call_sid in running_call_sids:
                        call_hangup_reasons[call_sid] = "Call cancelled by stopping batch"
                        try:
                            # Determine final status and update DB immediately
                            current_status = self.twilio_client.calls(call_sid).fetch().status
                            print(f"📞 CALL STATUS: {call_sid} is '{current_status}'")
                            final_status = current_status
                            if current_status == "ringing":
                                final_status = "canceled"
                                self.twilio_client.calls(call_sid).update(status="canceled")
                            elif current_status == "in-progress":
                                final_status = "completed"
                                self.twilio_client.calls(call_sid).update(status="completed")
                            else:
                                # For other statuses, just ensure we mark as completed if not already
                                self.twilio_client.calls(call_sid).update(status="completed")

                            cne = await cne_repo.get_by_sid(db, call_sid)
                            if cne and cne.BatchExecutionId == st.batch_execution_id:
                                if cne.Status != final_status:
                                    print(f"📝 CNE UPDATED: {call_sid} {cne.Status} → {final_status}")
                                    cne.Status = final_status
                                    cne.UpdatedAt = datetime.now(timezone.utc)
                                    cne.UpdatedBy = "system-stop"
                                    await cne_repo.update(db, cne)

                                    if final_status == "completed":
                                        st.completed_calls.add(call_sid)
                                    elif final_status in {"busy","failed","no-answer","canceled"}:
                                        st.failed_calls.add(call_sid)

                            num_running_canceled += 1
                        except Exception as e:
                            print(f"⚠️ terminate error {call_sid}: {e}")
                            try:
                                self.twilio_client.calls(call_sid).update(status="completed")
                                num_running_canceled += 1
                            except Exception as e2:
                                print(f"❌ fallback complete failed {call_sid}: {e2}")
                    db.commit()
                except Exception as e:
                    print(f"❌ optimized terminate loop error: {e}")
                    db.rollback()
                finally:
                    db.close()

            st.running_calls.clear()
            for call_sid in list(st.waiters.keys()):
                st.waiters[call_sid].cancel()
            st.waiters.clear()

            # Broadcast updated active call count after stopping
            total_active_calls = sum(len(state.running_calls) for state in self.active_batches.values())
            await manager.broadcast_active_call_count(total_active_calls)

        # (C) clear pending
        st.pending_numbers.clear()

        # (D) finalize execution
        if st.batch_execution_id:
            db = get_dynamic_db(st.email)
            try:
                logs_repo = BatchLogsRepository()
                bx_repo = BatchExecutionRepository()
                await bx_repo.set_status(db, st.batch_execution_id, "stopped", updated_by=email)
                batch_row = db.query(models.Batch).filter(
                    models.Batch.Id == batch_id,
                    models.Batch.IsDeleted == False
                ).first()
                batch_name = batch_row.Name if batch_row else batch_id
                await logs_repo.log(
                    db,
                    batch_id=batch_id,
                    batch_execution_id=st.batch_execution_id,
                    log_type="StopBatch",
                    message=f"Batch '{batch_name}' stopped by {email}",
                    created_by="system"
                )
                
                # Recompute counters to ensure accuracy
                db.commit() # Force refresh of transaction
                await bx_repo.recompute_counters_from_db(db, st.batch_execution_id)
                
                db.commit()
            except Exception as e:
                import traceback
                print(f"❌ stop_batch finalize error: {e}")
                traceback.print_exc()
                db.rollback()
            finally:
                db.close()

        # (E) final broadcast (same payload shape)
        final_payload = self._status_payload(st, "stopped")
        final_payload.update({
            "immediate_stop": True,
            "message": f"Batch stopped. Running: {num_running_canceled} marked as completed, "
                       f"Queued: {num_queued_canceled} canceled. Total completed: {st.completed_count}, "
                       f"Total failed: {st.failed_count}"
        })
        await manager.broadcast_to_batch(batch_id, final_payload)

        # (F) cleanup + running list
        try:
            await asyncio.sleep(1.0)
            await manager.cleanup_batch_only_connections(batch_id)
            if batch_id in self.active_batches:
                del self.active_batches[batch_id]
            running_batch_ids = [
                bid for bid, s in self.active_batches.items()
                if s.task and not s.task.done() and not s.stop_flag
            ]
            await manager.broadcast_running_batch_ids(running_batch_ids)
        except Exception as e:
            print(f"❌ cleanup error: {e}")

        return {
            "batch_id": batch_id,
            "message": "Batch stopped successfully",
            "running_calls_canceled": num_running_canceled,
            "queued_calls_canceled": num_queued_canceled,
            "total_stopped": num_running_canceled + num_queued_canceled,
            "final_completed": st.completed_count,
            "final_failed": st.failed_count,
            "details": {
                "completed_calls": list(st.completed_calls),
                "failed_calls": list(st.failed_calls)
            }
        }

    async def list_batches(self, db: Session):
        rows = (
            db.query(Batch)
            .filter(Batch.IsDeleted == False)
            .order_by(Batch.CreatedAt.desc())
            .all()
        )
        items = []
        bx_repo = BatchExecutionRepository()
        for b in rows:
            nums = (
                db.query(CallNumber)
                .filter(CallNumber.IsDeleted == False, CallNumber.BatchId == b.Id)
                .order_by(CallNumber.CreatedAt.asc())
                .all()
            )
            latest_exec = await bx_repo.latest_for_batch(db, b.Id)
            items.append(
                {
                    "id": b.Id,
                    "name": b.Name,
                    "email": b.Email,
                    "script_path": b.ScriptPath,
                    "total_count": b.TotalCount,
                    "latest_execution": {
                        "id": latest_exec.Id,
                        "status": latest_exec.Status,
                        "completed_count": latest_exec.CompletedCount or 0,
                        "failed_count": latest_exec.FailedCount or 0,
                        "created_at": latest_exec.CreatedAt,
                        "updated_at": latest_exec.UpdatedAt,
                    } if latest_exec else None,
                    "created_at": b.CreatedAt,
                    "callnumbers": [
                        {
                            "id": n.Id,
                            "phone_number": n.PhoneNumber,
                            "created_at": n.CreatedAt,
                        }
                        for n in nums
                    ],
                }
            )
        return {"items": items, "total_items": len(items)}

    async def batch_status(self, batch_id: str):
        st = self.active_batches.get(batch_id)
        
        # 1. If in memory, return live state
        if st:
            payload = self._status_payload(st, "active")
            print(f"📊 STATUS BATCH (MEM): Broadcasting for {batch_id} - {payload}")
            await manager.broadcast_to_batch(batch_id, payload)
            return {
                "batch_id": st.batch_id,
                "execution_id": st.batch_execution_id,
                "concurrent_limit": st.concurrent_limit,
                "stop_flag": st.stop_flag,
                "running": list(st.running_calls),
                "queued": len(st.pending_numbers),
                "source": "memory"
            }

        # 2. Fallback: Check DB for latest execution
        # This handles cases where server restarted or batch crashed/completed but we want to see status
        db = get_dynamic_db("admin@gmail.com") # Default email for read-only status check if not provided
        try:
            bx_repo = BatchExecutionRepository()
            latest_exec = await bx_repo.latest_for_batch(db, batch_id)
            
            if not latest_exec:
                 return JSONResponse(status_code=404, content={"errors": ["Batch execution not found"]})

            # Recompute counts to be sure
            await bx_repo.recompute_counters_from_db(db, latest_exec.Id)
            # Refresh to get updated counts
            db.refresh(latest_exec)
            
            status_payload = {
                "type": "batch_status",
                "id": batch_id,
                "execution_id": latest_exec.Id,
                "status": latest_exec.Status,
                "concurrent_limit": 0, # Not active
                "totalCount": 0, # We'd need to query this if needed, but for status check maybe not critical
                "completedCount": latest_exec.CompletedCount,
                "failedCount": latest_exec.FailedCount,
                "completed_calls": [], # Don't fetch all for DB fallback to avoid perf hit
                "failed_calls": [],
                "queued": 0,
                "running": 0,
                "running_calls": [],
                "running_calls_with_ids": [],
                "stop_flag": latest_exec.Status == "stopped",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "database"
            }
            
            print(f"STATUS BATCH (DB): Broadcasting for {batch_id} - {status_payload}")
            await manager.broadcast_to_batch(batch_id, status_payload)
            
            return {
                "batch_id": batch_id,
                "execution_id": latest_exec.Id,
                "status": latest_exec.Status,
                "completed_count": latest_exec.CompletedCount,
                "failed_count": latest_exec.FailedCount,
                "source": "database"
            }
        except Exception as e:
            print(f"❌ batch_status DB fallback error: {e}")
            return JSONResponse(status_code=500, content={"errors": [f"Error fetching batch status: {str(e)}"]})
        finally:
            db.close()

# ----------------------------------
# FastAPI Router (class-backed)
# ----------------------------------
service = BatchingService()
router = APIRouter()

@router.post("/batches/{batch_id}/start", response_model=schema.BatchStartResponse, summary="Start Batch", description="**Input:** Query params `concurrent_limit` (1-50), `restart_mode` (all/failed_only) + Header `email` + Path `batch_id`.<br>**Output:** JSON with execution details and queue status.<br><br>Starts or restarts a batch of outbound calls.")
async def start_batch(
    batch_id: str,
    concurrent_limit: int = Query(..., ge=1, le=50),
    restart_mode: str = Query("all", regex="^(all|failed_only|pending_only)$"),
    email: TypingOptional[str] = Header(None, alias="email"),
):
    return await service.start_batch(batch_id, concurrent_limit, restart_mode, email)

@router.post("/batches/{batch_id}/stop", response_model=schema.BatchStopResponse, summary="Stop Batch", description="**Input:** Path `batch_id` + Header `email`.<br>**Output:** JSON summary of stopped/canceled calls.<br><br>Immediately stops a running batch and cancels pending numbers.")
async def stop_batch(batch_id: str, email: TypingOptional[str] = Header(None, alias="email")):
    return await service.stop_batch(batch_id, email)

@router.get("/batches", response_model=schema.BatchListResponse, summary="List Batches", description="**Input:** Header `Authorization` (DB selection).<br>**Output:** List of batch configurations and their latest execution stats.")
async def list_batches(db: Session = Depends(get_db_from_headers)):
    return await service.list_batches(db)

@router.get("/batches/{batch_id}/status", response_model=schema.BatchStatusResponse, summary="Get Batch Status", description="**Input:** Path `batch_id`.<br>**Output:** Real-time JSON status (running/queued/completed counts) from memory or DB.<br><br>Used for polling or initial status check.")
async def batch_status(batch_id: str):
    return await service.batch_status(batch_id)