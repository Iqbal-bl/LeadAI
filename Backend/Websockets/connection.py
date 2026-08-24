# connect.py
import asyncio, json, uuid, logging
from datetime import datetime, timezone
from typing import Dict, Set, List
from fastapi import WebSocket

logger = logging.getLogger(__name__)

_loop: asyncio.AbstractEventLoop | None = None


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Call once at startup (inside an async context) so sync code can schedule broadcasts."""
    global _loop
    _loop = loop
    logger.info("[WS] event loop captured for cross-thread broadcasts")


def _fire_and_forget(coro) -> None:
    """Schedule a coroutine on the main event loop from a sync/worker thread."""
    if _loop is None or _loop.is_closed():
        logger.warning("[WS] no event loop available — broadcast dropped")
        return
    try:
        future = asyncio.run_coroutine_threadsafe(coro, _loop)
        logger.debug("[WS] broadcast scheduled: %s", coro)
    except Exception as exc:
        logger.warning("[WS] failed to schedule broadcast: %s", exc)


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Dict[str, Set[WebSocket]]] = {
            "transcript": {},     # {callsid: {ws, ...}}
            "general":   {},      # {callsid: {ws, ...}}
            "batch":     {},      # {batch_id: {ws, ...}}
            "runningbatch": {     # {"global_running_batches": {ws, ...}}
                "global_running_batches": set()
            },
            "activecalls": {      # {"global_active_calls": {ws, ...}}
                "global_active_calls": set()
            },
        }
        self.transcript_history: Dict[str, List[dict]] = {} # {callsid: [msg_dict, ...]}
        self._outbox: Dict[WebSocket, asyncio.Queue[str]] = {}
        self._writers: Dict[WebSocket, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    # ---------- internal helpers ----------
    async def _ensure_writer(self, ws: WebSocket):
        if ws in self._writers:
            return
        q = self._outbox.setdefault(ws, asyncio.Queue(maxsize=2000))
        async def _drain():
            try:
                while True:
                    msg = await q.get()
                    await ws.send_text(msg)
            except asyncio.CancelledError:
                # normal shutdown; don’t treat as error
                pass
            except Exception:
                await self._unsafe_remove(ws)
        self._writers[ws] = asyncio.create_task(_drain())

    async def _unsafe_add(self, bucket: str, key: str, ws: WebSocket):
        self.active_connections.setdefault(bucket, {})
        self.active_connections[bucket].setdefault(key, set()).add(ws)
        await self._ensure_writer(ws)

    async def _unsafe_remove(self, ws: WebSocket):
        for bucket, mapping in self.active_connections.items():
            for key, conns in list(mapping.items()):
                if ws in conns:
                    conns.discard(ws)
                    if not conns:
                        mapping.pop(key, None)
        if ws in self._writers:
            self._writers[ws].cancel()
            self._writers.pop(ws, None)
        self._outbox.pop(ws, None)
        try:
            await ws.close()
        except Exception:
            pass

    async def _snapshot_connections(self, bucket: str, key: str) -> List[WebSocket]:
        conns = self.active_connections.get(bucket, {}).get(key, set())
        return list(conns)

    async def _enqueue_text(self, ws: WebSocket, text: str):
        q = self._outbox.get(ws)
        if not q:
            return
        try:
            q.put_nowait(text)
        except asyncio.QueueFull:
            try:
                _ = q.get_nowait()
            except Exception:
                pass
            try:
                q.put_nowait(text)
            except Exception:
                pass

    async def _enqueue_json(self, ws: WebSocket, data: dict):
        text = json.dumps(data, separators=(",", ":"))
        await self._enqueue_text(ws, text)

    # ---------- public API ----------
    async def connect(self, websocket: WebSocket, key: str, connection_type: str = "general"):
        await websocket.accept()
        async with self._lock:
            # Auto-map global channels
            if connection_type == "runningbatch":
                actual_key = "global_running_batches"
                # Send current running batch IDs immediately when connecting
                await self._unsafe_add(connection_type, actual_key, websocket)  # Add first
                from batching import service
                running_batch_ids = [bid for bid, state in service.active_batches.items()
                                    if state.task and not state.task.done() and not state.stop_flag]
                payload = {
                    "type": "running_batches",
                    "running_batch_ids": running_batch_ids,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                await self._enqueue_json(websocket, payload)
                return  # Don't add again
            elif connection_type == "activecalls":
                actual_key = "global_active_calls"
                # Send current active call count immediately when connecting
                await self._unsafe_add(connection_type, actual_key, websocket)  # Add first
                from batching import service
                current_count = sum(len(state.running_calls) for state in service.active_batches.values())
                payload = {
                    "type": "active_call_count",
                    "active_call_count": current_count,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                await self._enqueue_json(websocket, payload) 
                return  # Don't add again
            elif connection_type == "transcript":
                actual_key = key
                await self._unsafe_add(connection_type, actual_key, websocket)
                # Replay history for this call
                history = self.transcript_history.get(key, [])
                if history:
                    for msg in history:
                        await self._enqueue_json(websocket, msg)
                return
            else:
                actual_key = key
            await self._unsafe_add(connection_type, actual_key, websocket)
            logger.info("[WS] connected: type=%s key=%s", connection_type, actual_key)

    async def disconnect(self, websocket: WebSocket, key: str, connection_type: str = "general"):
        async with self._lock:
            await self._unsafe_remove(websocket)

    # PERSONAL: plain text (unchanged semantics)
    async def send_personal_message(self, message: str, websocket: WebSocket):
        await self._enqueue_text(websocket, message)

    # BATCH: send EXACTLY the dict you pass (no extra keys)
    async def broadcast_to_batch(self, batch_id: str, data: dict):
        receivers = await self._snapshot_connections("batch", batch_id)
        for ws in receivers:
            await self._enqueue_json(ws, data)

    # RUNNING BATCH IDS: keep your original shape (with 'timestamp')
    async def broadcast_running_batch_ids(self, running_batch_ids: List[str]):
        receivers = await self._snapshot_connections("runningbatch", "global_running_batches")
        payload = {
            "type": "running_batches",
            "running_batch_ids": running_batch_ids,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        for ws in receivers:
            await self._enqueue_json(ws, payload)

    # CALL STATUS: plain text "<status> | YYYY-MM-DD HH:MM:SS" in Asia/Kolkata
    async def broadcast_to_call(self, callsid: str, message: str):
        receivers = await self._snapshot_connections("general", callsid)
        local_time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        line = f"{message} | {local_time_str}"
        for ws in receivers:
            await self._enqueue_text(ws, line)

        # Also broadcast to LeadAI conversation subscribers (inbox UI)
        leadai_receivers = await self._snapshot_connections("leadai_conversation", callsid)
        for ws in leadai_receivers:
            await self._enqueue_json(ws, {"type": "call_status", "status": message, "timestamp": local_time_str})

    async def broadcast_transcript_event_to_call(self, callsid: str, data: dict):
        """Send one non-streamed control event to transcript subscribers."""
        receivers = await self._snapshot_connections("transcript", callsid)
        for ws in receivers:
            await self._enqueue_json(ws, data)

        # Also broadcast to LeadAI conversation subscribers (inbox UI)
        leadai_receivers = await self._snapshot_connections("leadai_conversation", callsid)
        for ws in leadai_receivers:
            await self._enqueue_json(ws, data)

    # TRANSCRIPT: EXACT JSON {id, type, text, timestamp} - Chunk-based for scalability
    async def broadcast_transcript_to_call(self, callsid: str, message: dict, delay: float = 0.03):
        full_text = (message.get("text") or "").strip()
        msg_type  = (message.get("type") or "user").strip()
        msg_id    = message.get("id") or str(uuid.uuid4())
        words = full_text.split()
        if not words:
            return

        receivers = await self._snapshot_connections("transcript", callsid)
        leadai_receivers = await self._snapshot_connections("leadai_conversation", callsid)
        
        # Store final message in history
        final_payload = {
            "id": msg_id,
            "type": msg_type,
            "text": full_text,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        self.transcript_history.setdefault(callsid, []).append(final_payload)

        if not receivers and not leadai_receivers:
            return

        # Chunk-based streaming (5 words per chunk) - 5x fewer messages than word-by-word
        chunk_size = 5
        for i in range(0, len(words), chunk_size):
            streamed = " ".join(words[:i + chunk_size])
            payload = {
                "id": msg_id,
                "type": msg_type,
                "text": streamed,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            for ws in receivers:
                await self._enqueue_json(ws, payload)
            for ws in leadai_receivers:
                await self._enqueue_json(ws, payload)
            await asyncio.sleep(delay)

        # Final consolidated send
        for ws in receivers:
            await self._enqueue_json(ws, final_payload)
        for ws in leadai_receivers:
            await self._enqueue_json(ws, final_payload)

    async def broadcast_active_call_count(self, active_call_count: int):
        """Broadcast real-time active call count to all connected clients"""
        # Use the "activecalls" bucket for active call count broadcasts
        receivers = await self._snapshot_connections("activecalls", "global_active_calls")
        payload = {
            "type": "active_call_count",
            "active_call_count": active_call_count,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        for ws in receivers:
            await self._enqueue_json(ws, payload)

    async def cleanup_call_only_connections(self, callsid: str):
        # Clear history
        self.transcript_history.pop(callsid, None)
        for bucket in ("general", "transcript"):
            receivers = await self._snapshot_connections(bucket, callsid)
            for ws in receivers:
                await self.disconnect(ws, callsid, connection_type=bucket)

    async def cleanup_batch_only_connections(self, batch_id: str):
        receivers = await self._snapshot_connections("batch", batch_id)
        for ws in receivers:
            await self.disconnect(ws, batch_id, connection_type="batch")

    # LEADAI: per-conversation live message stream
    async def broadcast_to_leadai_conversation(self, conversation_id: str, data: dict):
        receivers = await self._snapshot_connections("leadai_conversation", conversation_id)
        logger.info("[WS] broadcast_to_leadai_conversation conv=%s receivers=%d type=%s",
                     conversation_id, len(receivers), data.get("type"))
        for ws in receivers:
            await self._enqueue_json(ws, data)

    # LEADAI: per-company inbox channel (new lead, handoff, assignment, message)
    async def broadcast_to_leadai_inbox(self, client_id: str, data: dict):
        receivers = await self._snapshot_connections("leadai_inbox", client_id)
        logger.info("[WS] broadcast_to_leadai_inbox client=%s receivers=%d type=%s",
                     client_id, len(receivers), data.get("type"))
        for ws in receivers:
            await self._enqueue_json(ws, data)


manager = ConnectionManager()
