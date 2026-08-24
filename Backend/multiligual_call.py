import os
import json
import base64
import asyncio
import audioop
import time
import re
from datetime import datetime, timezone
from typing import Dict, Optional, List, Any



from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Form, File, UploadFile, Depends, status, Query
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import VoiceResponse, Connect
from pysilero_vad import SileroVoiceActivityDetector
from sarvam_tts import SarvamTTSManager
from sarvam_stt import SarvamSTTManager
from validate_number import validate_phone_number
from dotenv import load_dotenv
load_dotenv()

import logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
SERVER_URL = os.getenv("SERVER_URL", "")

app = FastAPI(
    title="Voice Agent API",
    version="1.0.0",
    swagger_ui_parameters={"persistAuthorization": True},
)

from swagger_schema import configure_swagger
configure_swagger(app, title="Voice Agent API", version="1.0.0", description="Voice Agent APIs with Bearer Token Authentication")

# CORS origins (shared by CORS middleware and the token middleware's
# preflight/echo handling). Read from env — comma-separated.
_CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:4300,http://localhost:4200,http://127.0.0.1:8125",
    ).split(",")
    if o.strip()
]


# ---------------------------------------------------------------------------
# Identity-server token validation (aligned to voice_agent.py).
# Every request must carry a Bearer token that the identity server's
# /oauth/check_token endpoint reports as active (see token_validation.py).
# PUBLIC_PATHS are exempt: Twilio webhooks + media WS (machine-to-machine,
# can't carry a user token), the docs, the bundled UI + static assets, and
# the auth endpoints used to obtain/refresh a token.
# ---------------------------------------------------------------------------
PUBLIC_PATHS = (
    "/docs", "/openapi.json", "/redoc",
    "/static",
    "/", "/call",
    "/call-status",
    "/outbound-twiml",
    "/twilio/recording-callback",
    "/media-stream",
    "/ws/",
    "/api/login",
    "/api/check-session",
    "/api/refresh-token",
)


def _is_public_path(path: str) -> bool:
    if path in ("/", "/call"):
        return True
    return any(path == p or path.startswith(p) for p in PUBLIC_PATHS if p not in ("/", "/call"))


class TokenValidationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/leadai/public/webhooks"):
            return await call_next(request)
        path = request.url.path
        method = request.method
        origin = request.headers.get("origin")

        # CORS preflight: always allow.
        if method.upper() == "OPTIONS":
            response = Response(status_code=200)
            if origin:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS,PUT,DELETE"
                response.headers["Access-Control-Allow-Headers"] = (
                    "Authorization, Content-Type, Email, X-Requested-With, ngrok-skip-browser-warning"
                )
                response.headers["Access-Control-Allow-Credentials"] = "true"
            return response

        # Public paths bypass token validation.
        if _is_public_path(path):
            response = await call_next(request)
            if origin:
                response.headers["Access-Control-Allow-Origin"] = origin
            return response

        # Require a Bearer token validated by the identity server.
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            response = JSONResponse(
                status_code=401,
                content={"error": ["Missing or invalid Authorization header"]},
            )
            if origin:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Credentials"] = "true"
            return response

        token = auth_header.replace("Bearer ", "").strip()
        # logger.info(f"[AUTH] {method} {path} | received token: {token}")
        validation_result = await validate_token_async(token)
        if not validation_result:
            response = JSONResponse(
                status_code=401,
                content={"error": ["Invalid or expired token"]},
            )
            if origin:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Credentials"] = "true"
            return response

        # Expose the validated identity to downstream handlers if needed.
        request.state.identity = validation_result

        response = await call_next(request)
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
        return response


# NOTE: middleware runs in REVERSE order of registration, so register the
# token middleware FIRST here — it then executes AFTER CORS, i.e. CORS sees
# the request first. (Functionally both add CORS headers; this ordering keeps
# Starlette's CORSMiddleware in charge of normal CORS.)
app.add_middleware(TokenValidationMiddleware)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", "demo-secret-key"))

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

from db import (
    connect_db,
    get_db,
    count_users,
    create_user,
    save_transcript_message,
    update_call_status,
    save_recording,
    fetch_conversation,
    list_call_logs,
    list_recordings,
    purge_call_logs,
)
from storage import minio_enabled
from token_validation import validate_token_async
from Websockets.connection import manager
from globals import call_hangup_reasons
import Domain.schema as schema
from auth import (
    get_current_user,
    get_current_user_optional,
    get_current_user_websocket,
    get_password_hash,
    validate_twilio_request,
    create_stream_token,
    verify_stream_token,
)

class CallRequest(BaseModel):
    phone_number: str = Field(..., description="Target phone number in E.164 format")
    language: str = Field("hi", description="Language code (hi, en, ta, etc.)")
    gender: str = Field("female", description="Agent gender (male/female)")
    speaker: Optional[str] = Field(None, description="Specific Sarvam speaker ID")
    multi_stt: bool = Field(False, description="Enable smart multi-lingual STT")
    sections: Optional[list] = Field(None, description="Optional XML sections to use for this call only")

class SaveXmlRequest(BaseModel):
    sections: list = Field(..., description="List of script sections")
    filename: str = Field("default.xml", description="Target filename")

class SetLanguageRequest(BaseModel):
    language: str = Field("multi", description="Default STT language")

class HangupRequest(BaseModel):
    call_sid: str = Field(..., description="Twilio Call SID to terminate")

connect_db()

def seed_db():
    """Seed the database with a default user if none exists"""
    try:
        if count_users() == 0:
            logger.info("Seeding default user...")
            email = os.getenv("DEFAULT_USER_EMAIL", "admin@voicebot.ai")
            password = os.getenv("DEFAULT_USER_PASSWORD", "admin123")
            hashed_password = get_password_hash(password)
            create_user(email, hashed_password)
            logger.info(f"Default user seeded: {email}")
        else:
            logger.info("Users already exist, skipping seeding")
    except Exception as e:
        logger.error(f"Failed to seed database: {e}")

seed_db()

active_calls: Dict[str, dict] = {}
transcript_connections: Dict[str, WebSocket] = {}

# CallSids whose hangup reason has already been persisted to the conversations
# table. Both the media-stream finally block and the /call-status webhook fire
# at call end; this guard ensures exactly ONE ResponseType="hangup" row per call.
_hangup_saved: set = set()


def _save_hangup_reason_once(call_sid: str, reason: str, phone_number: str = None):
    """Persist the hangup reason as a conversations row (ResponseType='hangup'),
    exactly once per call. Mirrors voice_agent.py's save_message(..., 'hangup')."""
    if not call_sid or call_sid in _hangup_saved:
        return
    _hangup_saved.add(call_sid)
    try:
        save_message(call_sid, "hangup", reason, phone_number)
        logger.info(f"[HANGUP] saved reason to DB for {call_sid}: {reason}")
    except Exception as e:
        logger.warning(f"[HANGUP] failed to save reason for {call_sid}: {e}")
    # Bound memory: keep the set from growing unbounded across many calls.
    if len(_hangup_saved) > 5000:
        _hangup_saved.clear()


async def _sync_leadai_transcript(call_sid: str):
    """Sync transcript from legacy ``conversations`` table into
    ``leadai_messages`` so the inbox / call transcript APIs return data
    immediately after the call ends."""
    try:
        from LeadAI.db import session
        from LeadAI.services import call_bridge
        from Domain.models import Client
        from LeadAI.models import LeadCall as LeadCallModel

        def _do_sync():
            db = session()
            try:
                call_row = (
                    db.query(LeadCallModel)
                    .filter(LeadCallModel.CallSid == call_sid, LeadCallModel.IsDeleted == False)
                    .first()
                )
                if call_row:
                    client = db.query(Client).filter(Client.Id == call_row.ClientId).first()
                    if client:
                        call_bridge.sync_call_transcript(db, call_row.ClientId, client.Name, call_row)
                        db.commit()
                        logger.info(f"[SYNC] transcript synced to leadai_messages for {call_sid}")
                    else:
                        logger.warning(f"[SYNC] client not found for call {call_sid}")
                else:
                    logger.warning(f"[SYNC] LeadCall row not found for {call_sid}")
            finally:
                db.close()

        await asyncio.to_thread(_do_sync)
    except Exception as exc:
        logger.warning(f"[SYNC] failed to sync transcript for {call_sid}: {exc}")

# Session storage for XML sections and language settings
session_xml_sections: Dict[str, list] = {}  # session_id -> sections
session_language: Dict[str, str] = {}  # session_id -> language code

# Import XML parser
from xml_parser import parse_xml_to_sections, sections_to_xml, sections_to_prompt

SCRIPTS_DIR = os.path.join(os.getcwd(), "scripts")
if not os.path.exists(SCRIPTS_DIR):
    os.makedirs(SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Batch-calling integration (see main.py / batching.py)
# ---------------------------------------------------------------------------
# Outbound calls placed by the batch engine (batching.py) flow through the SAME
# Sarvam webhooks the UI uses (/outbound-twiml, /call-status, /media-stream).
# These two helpers bridge the batch engine's per-call state into this app:
#   - _ensure_batch_call_context: hydrate active_calls from the batch's stashed
#     agent config (XML script + language/gender) so /media-stream builds the
#     right SimpleAgent for a batch call.
#   - _advance_batch_on_status: on a terminal Twilio status, drive the batch
#     state machine forward (counters + the per-call asyncio.Event the batch
#     runner waits on) — mirrors the old voice_agent.py /twilio/call-status glue.
def _ensure_batch_call_context(call_sid: str):
    """If `call_sid` is a batch call with no active_calls entry yet, build one
    from the config the batch engine stashed at dial time. No-op for UI calls."""
    if not call_sid or call_sid in active_calls:
        return
    try:
        from batching import service
        cfg = service.call_to_config.get(call_sid)
    except Exception:
        cfg = None
    if not cfg:
        return

    sections = []
    script_path = cfg.get("script_path")
    if script_path and os.path.isfile(script_path):
        try:
            with open(script_path, "r", encoding="utf-8") as f:
                sections = parse_xml_to_sections(f.read())
        except Exception as e:
            logger.warning(f"[batch] failed to parse script {script_path}: {e}")

    language = cfg.get("language", "multi")
    active_calls[call_sid] = {
        "phone_number": cfg.get("phone_number"),
        "status": "initiated",
        "language": language,
        "gender": cfg.get("gender", "female"),
        "speaker": None,
        "multi_stt": language == "multi",
        "xml_sections": sections,
    }
    logger.info(f"[batch] hydrated active_calls for batch call {call_sid} "
                f"(sections={len(sections)}, lang={language})")


async def _advance_batch_on_status(call_sid: str, status: str):
    """Advance the batch state machine when a BATCH call reaches a terminal
    Twilio status. Hard no-op for UI calls (which never touch the batch ORM
    tables — those may not even exist in this deployment)."""
    if not call_sid:
        return
    terminal = {"completed", "no-answer", "busy", "failed", "canceled"}
    if status not in terminal:
        return
    try:
        from batching import service
    except Exception:
        return

    # Only batch-dialed calls are tracked by the batch engine. A UI call
    # (placed via /api/make-call) appears in none of these maps, so we must
    # NOT run the batch counter update for it — that query hits tables like
    # `callnumberexecution` which only exist when batch calling is provisioned.
    is_batch_call = (
        call_sid in service.call_to_config
        or call_sid in service.call_to_batch
        or call_sid in service.active_calls
    )
    if not is_batch_call:
        return

    # database.py.get_dynamic_db ignores the email (single shared MySQL), so any
    # non-empty value works; kept configurable for parity with the old flow.
    email = os.getenv("DEFAULT_TENANT_EMAIL", "admin@gmail.com")
    try:
        await service.update_counters_for_call(call_sid, status, email)
    except Exception as e:
        logger.warning(f"[batch] update_counters failed for {call_sid}: {e}")

    ev = service.active_calls.get(call_sid)
    if ev is not None:
        logger.info(f"[batch] call {call_sid} terminal ({status}) -> signaling batch runner")
        ev.set()


# Using sections_to_prompt imported from xml_parser


# Strip the hyphen out of "N-digit", "N-letter", "N-character" style compounds
# before they reach TTS — the hyphen makes Sarvam Bulbul spell the second word
# letter-by-letter ("d-i-g-i-t"). "6-digit" → "6 digit", "10-character" → "10 character".
_TTS_HYPHEN_RE = re.compile(r'(\d+)-([A-Za-zऀ-ॿ]+)')


def _strip_digit_hyphen(text: str) -> str:
    if not text:
        return text
    return _TTS_HYPHEN_RE.sub(r'\1 \2', text)


_SILENT_TOKEN_RE = re.compile(r'\[SILENT\]', re.IGNORECASE)


def _strip_tts_control_tokens(text: str) -> str:
    """Remove non-spoken agent control tokens before TTS/UI persistence."""
    if not text:
        return text
    return _SILENT_TOKEN_RE.sub('', text).strip()


def save_message(call_sid: str, role: str, content: str, phone_number: str = None):
    """Save a chat message to MySQL"""
    try:
        save_transcript_message(call_sid, role, content, phone_number)
    except Exception as e:
        logger.error(f"Failed to save message: {e}")


class TTSManager:
    """
    Cartesia TTS Manager for Hindi and multilingual support.
    Uses Cartesia's sonic-multilingual model with WebSocket streaming.
    Converts PCM audio to mulaw for Twilio compatibility.
    """
    def __init__(self, callsid: str, voice_id: str = None, language: str = "hi"):
        self.callsid = callsid
        # Default to a Hindi voice - you can change this to any Cartesia Hindi voice ID
        # Some Hindi voice options: "aadhya", "amit", "parvati", etc.
        self.voice_id = voice_id or os.getenv("CARTESIA_VOICE_ID", "a0e99841-438c-4a64-b679-ae501e7d6091")
        self.language = language  # "hi" for Hindi, "en" for English, or auto-detect with None
        self.api_key = os.getenv("CARTESIA_API_KEY")
        self._ws = None
        self._main_loop = None
        self._current_twilio_ws = None
        self._current_stream_sid = None
        self._stop_event = None
        self._utterance_done = None
        self._last_audio_ts = None
        self._frame_bytes = 160  # 20ms at 8kHz mulaw (1 byte per sample)
        self._bytes_per_second = 8000
        self._silence = b'\xFF' * self._frame_bytes  # mulaw silence
        self._send_queue = None
        self._out_buffer = b""
        self._sender_task = None
        self._receiver_task = None
        self._context_counter = 0

    async def setup(self):
        import websockets
        self._main_loop = asyncio.get_running_loop()
        self._send_queue = asyncio.Queue(maxsize=400)
        
        # Connect to Cartesia WebSocket
        ws_url = f"wss://api.cartesia.ai/tts/websocket?api_key={self.api_key}&cartesia_version=2024-06-10"
        self._ws = await websockets.connect(ws_url)
        
        logger.info(f"Cartesia TTS connected for call {self.callsid}")
        
        # Start the paced sender task
        self._sender_task = asyncio.create_task(self._paced_sender())

    def _get_next_context_id(self) -> str:
        """Generate a unique context ID for each TTS request"""
        self._context_counter += 1
        return f"ctx-{self.callsid}-{self._context_counter}"

    async def _receive_audio(self, context_id: str):
        """Receive audio chunks from Cartesia and queue them for sending"""
        try:
            while True:
                if self._stop_event and self._stop_event.is_set():
                    # Send cancel request to Cartesia
                    try:
                        cancel_msg = json.dumps({"context_id": context_id, "cancel": True})
                        await self._ws.send(cancel_msg)
                    except:
                        pass
                    break
                
                try:
                    msg = await asyncio.wait_for(self._ws.recv(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                
                data = json.loads(msg)
                msg_type = data.get("type", "")
                msg_context = data.get("context_id", "")
                
                # Only process messages for our context
                if msg_context != context_id:
                    continue
                
                if msg_type == "chunk":
                    # Decode base64 audio data (PCM s16le at 8kHz)
                    audio_b64 = data.get("data", "")
                    if audio_b64:
                        pcm_data = base64.b64decode(audio_b64)
                        # Convert PCM s16le to mulaw
                        mulaw_data = audioop.lin2ulaw(pcm_data, 2)
                        self._last_audio_ts = time.monotonic()
                        await self._send_queue.put(mulaw_data)
                
                elif msg_type == "done":
                    # TTS generation complete
                    break
                
                elif msg_type == "error":
                    logger.error(f"Cartesia TTS error: {data.get('error', 'Unknown error')}")
                    break
                    
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Cartesia receive error: {e}")
        finally:
            if self._utterance_done and not self._utterance_done.is_set():
                self._utterance_done.set()

    async def _paced_sender(self):
        """Send audio frames at the correct rate for telephony (8kHz mulaw)"""
        frame_interval = self._frame_bytes / self._bytes_per_second  # 20ms per frame
        next_due = time.monotonic()
        try:
            while True:
                # Drain queue into buffer
                if self._send_queue:
                    try:
                        while True:
                            piece = self._send_queue.get_nowait()
                            self._out_buffer += piece
                    except asyncio.QueueEmpty:
                        pass
                
                now = time.monotonic()
                if next_due > now:
                    await asyncio.sleep(next_due - now)
                
                # If stopped, clear buffer
                if self._stop_event and self._stop_event.is_set():
                    self._out_buffer = b""
                    next_due = time.monotonic() + frame_interval
                    continue
                
                # Get next frame
                if len(self._out_buffer) >= self._frame_bytes:
                    chunk = self._out_buffer[:self._frame_bytes]
                    self._out_buffer = self._out_buffer[self._frame_bytes:]
                else:
                    chunk = self._silence
                
                # Send to Twilio
                try:
                    if self._current_twilio_ws and self._current_stream_sid:
                        payload = base64.b64encode(chunk).decode("utf-8")
                        await self._current_twilio_ws.send_json({
                            "event": "media",
                            "streamSid": self._current_stream_sid,
                            "media": {"payload": payload}
                        })
                except Exception as e:
                    logger.error(f"TTS send error: {e}")
                
                next_due += frame_interval
        except asyncio.CancelledError:
            return

    async def stream_tts_audio(self, ws, stream_sid: str, text: str, stop_event: asyncio.Event) -> bool:
        """Stream TTS audio for the given text"""
        if not text or stop_event.is_set():
            return True
        
        self._current_twilio_ws = ws
        self._current_stream_sid = stream_sid
        self._stop_event = stop_event
        self._utterance_done = asyncio.Event()
        self._last_audio_ts = None
        
        context_id = self._get_next_context_id()
        
        try:
            # Build Cartesia TTS request
            tts_request = {
                "model_id": "sonic-3-2026-01-12",  # Supports Hindi and auto-detects language
                "transcript": text,
                "voice": {
                    "mode": "id",
                    "id": self.voice_id
                },
                "context_id": context_id,
                "output_format": {
                    "container": "raw",
                    "encoding": "pcm_s16le",  # 16-bit signed PCM
                    "sample_rate": 8000  # 8kHz for telephony
                },
                "continue": False
            }
            
            # Add language hint if specified
            if self.language:
                tts_request["language"] = self.language
            
            # Send TTS request
            await self._ws.send(json.dumps(tts_request))
            
            # Start receiver task
            self._receiver_task = asyncio.create_task(self._receive_audio(context_id))
            
            # Wait for completion or interruption
            idle_seconds = 0.15
            while True:
                if self._stop_event and self._stop_event.is_set():
                    if self._receiver_task:
                        self._receiver_task.cancel()
                        try:
                            await self._receiver_task
                        except asyncio.CancelledError:
                            pass
                    break
                
                if self._utterance_done and self._utterance_done.is_set():
                    break
                
                now = time.monotonic()
                if self._last_audio_ts is not None and (now - self._last_audio_ts) >= idle_seconds:
                    await self._wait_drain(timeout=5.0)
                    break
                
                await asyncio.sleep(0.01)
                
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"TTS stream error: {e}")
        finally:
            if self._utterance_done and not self._utterance_done.is_set():
                self._utterance_done.set()
        
        return True

    async def _wait_drain(self, timeout: float = 2.0):
        """Wait for the audio buffer to drain"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            queue_empty = self._send_queue.empty() if self._send_queue else True
            if queue_empty and len(self._out_buffer) < self._frame_bytes:
                return
            await asyncio.sleep(0.01)

    async def clear_buffer(self):
        """Clear all pending audio"""
        self._out_buffer = b""
        if self._send_queue:
            try:
                while not self._send_queue.empty():
                    self._send_queue.get_nowait()
            except:
                pass
        
        # Cancel any pending receiver task
        if self._receiver_task and not self._receiver_task.done():
            self._receiver_task.cancel()
            try:
                await self._receiver_task
            except asyncio.CancelledError:
                pass

    async def cleanup(self):
        """Clean up all resources"""
        if self._sender_task:
            self._sender_task.cancel()
            try:
                await self._sender_task
            except asyncio.CancelledError:
                pass
        
        if self._receiver_task and not self._receiver_task.done():
            self._receiver_task.cancel()
            try:
                await self._receiver_task
            except asyncio.CancelledError:
                pass
        
        if self._ws:
            try:
                await self._ws.close()
            except:
                pass
        
        self._ws = None
        logger.info(f"Cartesia TTS cleaned up for call {self.callsid}")


class SimpleAgent:
    # In-call memory: keep the full history (so the agent never forgets the
    # start of the call), and only compress into a running prefix when the
    # raw transcript grows past MAX_RAW_TURNS. After compression we retain
    # the last KEEP_RAW turns verbatim plus the summary block.
    MAX_RAW_TURNS = 40
    KEEP_RAW = 20

    def __init__(self, xml_sections: list = None, gender: str = "neutral", language: str = "hi"):
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.sarvam.ai/v1"),
        )
        self._chat_model = os.getenv("OPENAI_CHAT_MODEL", "sarvam-105b")
        self.history = []
        self.summary_prefix = ""  # cumulative summary of compressed older turns
        self.gender = gender
        self.language = language
        self.system_prompt = self._build_system_prompt(xml_sections)

        script_name = "none"
        section_names = []
        if xml_sections:
            for sec in xml_sections:
                if isinstance(sec, dict):
                    section_names.append(sec.get("name", "unknown"))
            script_name = xml_sections[0].get("name", "custom") if xml_sections else "custom"
        logger.info(
            "[LeadAI voice] SimpleAgent init — gender=%s language=%s sections=%s",
            self.gender,
            self.language,
            section_names,
        )
        logger.debug(
            "[LeadAI voice] system_prompt (first 500 chars):\n%s",
            self.system_prompt[:500] if self.system_prompt else "(empty)",
        )

    def _load_base_prompt(self):
        """Load the base speaking style prompt"""
        try:
            with open("system_prompt.txt", "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return "You are a helpful voice assistant. Keep responses short and conversational."

    def _build_system_prompt(self, xml_sections: list = None):
        """Combine base prompt with XML sections"""
        base_prompt = self._load_base_prompt()
        
        if not xml_sections:
            return base_prompt
        
        # Convert XML sections to text
        xml_prompt = sections_to_prompt(xml_sections)
        
        # Gender instruction
        gender_instruction = ""
        if self.gender == "female":
            gender_instruction = "\n\n# Gender Instruction\nAct as a female AI agent. In Hindi, use feminine verb endings (e.g., 'रही हूँ' instead of 'रहा हूँ')."
        elif self.gender == "male":
            gender_instruction = "\n\n# Gender Instruction\nAct as a male AI agent. In Hindi, use masculine verb endings (e.g., 'रहा हूँ' instead of 'रही हूँ')."
        
        # Reply language is fixed by the UI selector (self.language). The "multi" mode
        # tells the bot to mirror whatever the caller speaks; every other code locks the
        # reply language so the model can't drift into English mid-call.
        lang_map = {
            "hi": "Hindi using Devanagari script",
            "en": "English",
            "ta": "Tamil using Tamil script",
            "te": "Telugu using Telugu script",
            "bn": "Bengali using Bengali script",
            "kn": "Kannada using Kannada script",
            "ml": "Malayalam using Malayalam script",
            "mr": "Marathi using Devanagari script",
            "pa": "Punjabi using Gurmukhi script",
            "gu": "Gujarati using Gujarati script",
        }
        if self.language == "multi":
            lang_instruction = (
                "\n\n# Multilingual Instruction\n"
                "Reply in the same language the user's LAST message was in. Look at the sentence, not the names inside it — "
                "an Indian name in an English sentence is still English. Switch language on the very next turn whenever the user switches. "
                "Banking terms (account, card, cashback, activate, lounge, OTP, debit, credit) may stay in English in any language. "
                "Dates are dd/mm/yyyy (Indian convention)."
            )
        else:
            forced = lang_map.get(self.language, "the user's language")
            # The script lock is intentionally aggressive — Sarvam keeps drifting from
            # Devanagari into romanized Hindi mid-call (e.g. "aapka account 8872 par
            # end hota hai" instead of "आपका account 8872 पर end होता है"), which
            # mangles the Bulbul TTS pronunciation.
            lang_instruction = (
                "\n\n# Reply Language (LOCKED)\n"
                f"CRITICAL: You MUST reply in {forced} for every single turn of this call. "
                "Do NOT switch to English under any circumstances, even if the script examples are in English — "
                "those examples are TEMPLATES; you must translate them on-the-fly into the locked reply language. "
                "Banking-specific English terms (account, card, cashback, activate, lounge, OTP, debit, credit) "
                "MAY appear inline in Latin/English spelling — but EVERY other Hindi word MUST be written in "
                "Devanagari script. NEVER write Hindi words in Latin/Roman letters (no 'aapka', 'main', 'hai', "
                "'kya', 'ji', 'mera', 'nahi' — write आपका, मैं, है, क्या, जी, मेरा, नहीं). "
                "Romanized Hindi is FORBIDDEN; it breaks the TTS pronunciation."
            )
        
        # Combine: Base speaking style + XML agent configuration + Gender + Language
        combined = f"{base_prompt}\n\n---\n\n# Agent Configuration\n\n{xml_prompt}{gender_instruction}{lang_instruction}"
        return combined

    async def get_response(self, user_text: str):
        self.history.append({"role": "user", "content": user_text})
        # Assemble: system prompt + (optional) running summary of older turns +
        # the FULL current history. We do not slice — turns are only removed
        # by _maybe_compress, which folds them into self.summary_prefix.
        messages = [{"role": "system", "content": self.system_prompt}]
        if self.summary_prefix:
            messages.append({
                "role": "system",
                "content": f"# Earlier in this call (summary)\n{self.summary_prefix}",
            })
        messages.extend(self.history)

        logger.info(
            "[LeadAI voice] get_response — history_turns=%d summary=%s",
            len(self.history),
            "yes" if self.summary_prefix else "no",
        )
        logger.debug(
            "[LeadAI voice] system_prompt (first 500 chars):\n%s",
            self.system_prompt[:500] if self.system_prompt else "(empty)",
        )

        # Per-turn language hint (multi mode only). The system-prompt rule alone
        # isn't enough — the model anchors to prior turns. We detect the dominant
        # script in the user's latest message and inject a one-line nudge so the
        # reply matches. Data-driven, not a longer prompt; supports any script.
        if self.language == "multi" and user_text:
            script_ranges = {
                "Devanagari script (Hindi/Marathi)": ('ऀ', 'ॿ'),
                "Bengali script": ('ঀ', '৿'),
                "Gurmukhi script (Punjabi)": ('਀', '੿'),
                "Gujarati script": ('઀', '૿'),
                "Odia script": ('଀', '୿'),
                "Tamil script": ('஀', '௿'),
                "Telugu script": ('ఀ', '౿'),
                "Kannada script": ('ಀ', '೿'),
                "Malayalam script": ('ഀ', 'ൿ'),
            }
            counts = {name: 0 for name in script_ranges}
            latin_alpha = 0
            for c in user_text:
                if c.isascii() and c.isalpha():
                    latin_alpha += 1
                    continue
                for name, (lo, hi) in script_ranges.items():
                    if lo <= c <= hi:
                        counts[name] += 1
                        break
            all_counts = [(n, v) for n, v in counts.items() if v > 0]
            if latin_alpha > 0:
                all_counts.append(("English (Latin script)", latin_alpha))
            if all_counts:
                dominant_name, dominant_count = max(all_counts, key=lambda x: x[1])
                if dominant_count >= 2:
                    messages.append({
                        "role": "system",
                        "content": (
                            f"User's last message is in {dominant_name}. "
                            f"Reply ONLY in {dominant_name} this turn."
                        ),
                    })
                    logger.info(f"[LANG HINT] dominant={dominant_name} (count={dominant_count})")

        try:
            full_response = ""
            # Sarvam silently reasons server-side and only streams content AFTER reasoning
            # completes. With too-tight max_tokens we get raw_len=0 + finish_reason=length
            # (model ran out of budget while still reasoning).
            #   attempt 0 → low reasoning, 2048 tokens — handles ~95% of turns
            #   attempt 1 → low reasoning, 4096 tokens — rescue for the rare deep-reason case
            attempt_configs = [
                {"reasoning_effort": "low", "max_tokens": 2048},
                {"reasoning_effort": "low", "max_tokens": 4096},
            ]
            llm_start = time.monotonic()
            first_token_logged = False
            for attempt, cfg in enumerate(attempt_configs):
                api_call_start = time.monotonic()
                logger.info(
                    f"[LLM] → request sent (model={self._chat_model}, attempt={attempt+1}, "
                    f"max_tokens={cfg['max_tokens']}, history_turns={len(self.history)})"
                )
                response = await self._client.chat.completions.create(
                    model=self._chat_model,
                    messages=messages,
                    temperature=0.2,
                    top_p=1,
                    max_tokens=cfg["max_tokens"],
                    stream=True,
                    timeout=20.0,
                )
                last_finish_reason = None
                buffer = ""
                raw_accum = ""
                in_think = False
                end_call_token = "[END_CALL]"
                async for chunk in response:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    if choice.finish_reason:
                        last_finish_reason = choice.finish_reason
                    delta = choice.delta.content or ""
                    if not delta:
                        continue
                    if not first_token_logged:
                        ttft_ms = (time.monotonic() - api_call_start) * 1000
                        logger.info(f"[LLM] ← first token in {ttft_ms:.0f}ms (attempt {attempt+1})")
                        first_token_logged = True
                    raw_accum += delta
                    buffer += delta
                    # Strip any <think>...</think> blocks before yielding to TTS.
                    out = ""
                    while buffer:
                        if in_think:
                            end = buffer.find("</think>")
                            if end == -1:
                                buffer = ""
                                break
                            buffer = buffer[end + len("</think>"):]
                            in_think = False
                            continue
                        start = buffer.find("<think>")
                        if start == -1:
                            # Hold back a tail that might be a partial "<think>" tag.
                            safe = len(buffer) - 7 if len(buffer) >= 7 else 0
                            if "<" in buffer[safe:]:
                                safe = buffer.rfind("<", 0, len(buffer))
                                if safe < 0:
                                    safe = len(buffer)
                            else:
                                safe = len(buffer)
                            out += buffer[:safe]
                            buffer = buffer[safe:]
                            break
                        out += buffer[:start]
                        buffer = buffer[start + len("<think>"):]
                        in_think = True
                    # Strip [END_CALL] tokens from speakable output (any partial split
                    # across chunks is held back until the full token can be matched).
                    if end_call_token in out:
                        out = out.replace(end_call_token, "")
                    # Hold back a trailing prefix of "[END_CALL]" so a partial split
                    # doesn't leak characters like "[E" to TTS.
                    for k in range(min(len(out), len(end_call_token) - 1), 0, -1):
                        if end_call_token.startswith(out[-k:]):
                            buffer = out[-k:] + buffer
                            out = out[:-k]
                            break
                    if out:
                        full_response += out
                        yield out
                if full_response:
                    total_ms = (time.monotonic() - llm_start) * 1000
                    logger.info(
                        f"[LLM] done in {total_ms:.0f}ms total | finish={last_finish_reason} "
                        f"| reply_len={len(full_response)} chars"
                    )
                    break
                logger.warning(
                    f"[SimpleAgent] empty response from Sarvam "
                    f"(finish_reason={last_finish_reason}, attempt={attempt+1}, "
                    f"reasoning={cfg['reasoning_effort']}, raw_len={len(raw_accum)}, "
                    f"raw_preview={raw_accum[:200]!r})"
                )
            self.history.append({"role": "assistant", "content": full_response})
        except Exception as e:
            logger.error(f"Agent error: {e}")
            yield "Sorry, I had trouble processing that."
            return

        # Inline (not background) so a fast user follow-up never races a
        # mid-edit history. Only runs when the raw transcript crosses the
        # threshold, which on most calls is never.
        await self._maybe_compress()

    async def _maybe_compress(self):
        """If the raw transcript has outgrown MAX_RAW_TURNS, fold the oldest
        portion into self.summary_prefix via a single Sarvam call. Failures
        are swallowed — next turn will simply retry. The user-facing assistant
        reply has already streamed back by the time this runs."""
        if len(self.history) <= self.MAX_RAW_TURNS:
            return

        older = self.history[:-self.KEEP_RAW]
        if not older:
            return

        # Render the older turns into a plain transcript for the summarizer
        transcript_lines = []
        for msg in older:
            role = msg.get("role", "?")
            text = (msg.get("content") or "").strip()
            if not text:
                continue
            speaker = "Caller" if role == "user" else "Agent"
            transcript_lines.append(f"{speaker}: {text}")
        transcript = "\n".join(transcript_lines)

        prior = self.summary_prefix.strip()
        prompt = (
            "You are summarising the EARLIER portion of an ongoing phone call "
            "so the agent can keep responding coherently without re-asking "
            "questions. Output ONLY the summary text — no preamble, no JSON, "
            "no markdown. Aim for 3 to 5 short sentences in English.\n\n"
            "MUST preserve, verbatim if known:\n"
            "- the caller's confirmed full name\n"
            "- their date of birth (dd/mm/yyyy if confirmed)\n"
            "- account number / last-4 digits if mentioned\n"
            "- the specific offer or topic discussed (e.g. Platinum Debit upgrade)\n"
            "- the caller's stated decisions (accepted / declined / asked to think about it)\n"
            "- any verification failures or retries\n\n"
        )
        if prior:
            prompt += f"Previous running summary (extend, do not discard):\n{prior}\n\n"
        prompt += f"Transcript of earlier turns:\n{transcript}\n"

        try:
            resp = await self._client.chat.completions.create(
                model=self._chat_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=512,
                timeout=15.0,
            )
            new_summary = (resp.choices[0].message.content or "").strip()
            if not new_summary:
                logger.warning("[SimpleAgent] compress returned empty; keeping history intact")
                return
            self.summary_prefix = new_summary
            self.history = self.history[-self.KEEP_RAW:]
            logger.info(
                f"[SimpleAgent] memory compressed: kept={len(self.history)} "
                f"summary_len={len(new_summary)}"
            )
        except Exception as e:
            logger.warning(f"[SimpleAgent] compress failed (will retry next turn): {e}")

    async def pre_warm(self, greeting: str):
        """Pre-warm the LLM cache with the system prompt and initial greeting."""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": greeting},
        ]
        try:
            logger.info(f"[SimpleAgent] Pre-warming LLM cache...")
            start_time = time.time()
            # Send a minimal request to trigger prompt caching and connection pooling
            await self._client.chat.completions.create(
                model=self._chat_model,
                messages=messages,
                max_tokens=1
            )
            duration = (time.time() - start_time) * 1000
            logger.info(f"[SimpleAgent] Pre-warm complete in {duration:.0f}ms")
        except Exception as e:
            logger.warning(f"[SimpleAgent] Pre-warm failed: {e}")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request):
    """Serve the main UI page"""
    return templates.TemplateResponse("multilingual_call.html", {"request": request})


# NOTE: local-JWT auth endpoints (/api/login, /api/check-session,
# /api/refresh-token, /api/logout) were removed — authentication is handled
# solely by the identity server via TokenValidationMiddleware. Clients obtain a
# Bearer token from the identity server and send it on every request.


@app.get("/api/scripts", tags=["Script Management"], summary="List available scripts")
async def get_scripts(request: Request, current_user: str = Depends(get_current_user)):
    """List all saved XML scripts in the server library"""
    try:
        files = [f for f in os.listdir(SCRIPTS_DIR) if f.endswith('.xml')]
        return JSONResponse({"status": "success", "scripts": files})
    except Exception as e:
        logger.error(f"Failed to list scripts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/scripts/{filename}", tags=["Script Management"], summary="Get script content")
async def get_script_content(request: Request, filename: str, current_user: str = Depends(get_current_user)):
    """Get sections and parsed content for a specific XML script"""
    try:
        if not filename.endswith('.xml'):
            filename += '.xml'
        
        file_path = os.path.join(SCRIPTS_DIR, filename)
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="Script not found")
            
        with open(file_path, "r", encoding="utf-8") as f:
            xml_content = f.read()
            
        sections = parse_xml_to_sections(xml_content)
        
        # Sync with session
        session_id = request.session.get("user", "default")
        session_xml_sections[session_id] = sections
        
        return JSONResponse({"status": "success", "sections": sections})
    except Exception as e:
        logger.error(f"Failed to load script {filename}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/download-script/{filename}", tags=["Script Management"], summary="Download script XML")
async def download_script_file(filename: str, current_user: str = Depends(get_current_user)):
    """Download local script file as raw XML for external editing"""
    try:
        if not filename.endswith('.xml'):
            filename += '.xml'
        file_path = os.path.join(SCRIPTS_DIR, filename)
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="File not found")
        
        from fastapi.responses import FileResponse
        return FileResponse(
            path=file_path, 
            filename=filename,
            media_type='application/xml'
        )
    except Exception as e:
        logger.error(f"Download failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload-xml", tags=["Script Management"], summary="Upload XML script")
async def upload_xml(request: Request, file: UploadFile = File(...), save_to_disk: bool = False, current_user: str = Depends(get_current_user)):
    """Upload and parse an XML template, optionally saving it permanently to the server library"""
    try:
        content = await file.read()
        xml_content = content.decode('utf-8')
        
        filename = file.filename
        if not filename.endswith('.xml'):
            filename += '.xml'
            
        if save_to_disk:
            file_path = os.path.join(SCRIPTS_DIR, filename)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(xml_content)
            message = f"Script '{filename}' saved to server library."
        else:
            message = f"Script '{filename}' loaded temporarily."
            
        # Parse XML into sections
        sections = parse_xml_to_sections(xml_content)
        
        # Sync with session
        session_id = request.session.get("user", "default")
        session_xml_sections[session_id] = sections
        
        return JSONResponse({
            "status": "success",
            "filename": filename,
            "sections": sections,
            "message": message
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"XML upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to parse XML: {str(e)}")


@app.get("/api/download-template", tags=["Script Management"], summary="Download default template")
async def download_template(current_user: str = Depends(get_current_user)):
    """Download the reference agent template XML file"""
    template_path = os.path.join(SCRIPTS_DIR, "default.xml")
    if os.path.exists(template_path):
        return FileResponse(template_path, filename="agent_template.xml", media_type='application/xml')
    
    # Fallback to creating a basic one if missing
    default_xml = """<agent-template version="2.0">
    <section title="Agent Identity" type="identity">
        <field name="name">Aura</field>
        <field name="role">Virtual Assistant</field>
        <field name="company">Prime Properties</field>
    </section>
    <section title="Core Objective" type="text">
        Help users find their dream home by gathering their requirements.
    </section>
</agent-template>"""
    temp_path = os.path.join(SCRIPTS_DIR, "template_tmp.xml")
    with open(temp_path, "w") as f:
        f.write(default_xml)
    return FileResponse(temp_path, filename="agent_template.xml", media_type='application/xml')


@app.post("/api/save-xml", tags=["Script Management"], summary="Save edited script")
async def save_xml(save_data: SaveXmlRequest, request: Request, current_user: str = Depends(get_current_user)):
    """Save edited sections and fields back to an XML file on the server disk"""
    try:
        sections = save_data.sections
        filename = save_data.filename
        
        if not filename.endswith('.xml'):
            filename += '.xml'
            
        # Reconstruct XML
        xml_content = sections_to_xml(sections)
        
        # Save to disk
        file_path = os.path.join(SCRIPTS_DIR, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(xml_content)
            
        session_id = request.session.get("user", "default")
        session_xml_sections[session_id] = sections
        
        return JSONResponse({"status": "success"})
    except Exception as e:
        logger.error(f"Save XML failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/scripts/{filename}", tags=["Script Management"], summary="Delete script")
async def delete_script(request: Request, filename: str, current_user: str = Depends(get_current_user)):
    """Permanently delete a script file from the server library"""
    try:
        if not filename.endswith('.xml'):
            filename += '.xml'
            
        file_path = os.path.join(SCRIPTS_DIR, filename)
        if os.path.exists(file_path):
            os.remove(file_path)
            return JSONResponse({"status": "success"})
        else:
            raise HTTPException(status_code=404, detail="Script not found")
    except Exception as e:
        logger.error(f"Delete script {filename} failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/set-language", tags=["Voice Calls"], summary="Set session language")
async def set_language(lang_data: SetLanguageRequest, request: Request, current_user: str = Depends(get_current_user)):
    """Set the STT language preference for the current user session"""
    try:
        language = lang_data.language
        
        session_id = request.session.get("user", "default")
        session_language[session_id] = language
        
        return JSONResponse({"status": "success", "language": language})
    except Exception as e:
        logger.error(f"Set language failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/call", response_class=HTMLResponse, include_in_schema=False)
async def call_page(request: Request):
    """Serve the active call management page"""
    return templates.TemplateResponse("multilingual_call.html", {"request": request})


def _normalize_sections(sections):
    """Coerce a client-supplied `sections` payload into the canonical shape the
    agent + sections_to_prompt expect: a list of dicts, where an 'identity'
    section's `content` is a list of {name, value}.

    The Angular client sometimes sends:
      - the whole payload as an XML/string blob   -> parse it
      - identity `content` as a "name: x\\ncompany: y" string -> split to fields
      - a JSON string instead of a list           -> json.loads
    Anything already in the right shape passes through untouched. This makes the
    call work regardless of how the client formats sections (no more JIN/Bank
    fallback, no string-indices crash)."""
    if not sections:
        return []

    # Whole payload is a string: try JSON first, then treat as raw XML template.
    if isinstance(sections, str):
        s = sections.strip()
        try:
            parsed = json.loads(s)
            return _normalize_sections(parsed)
        except Exception:
            pass
        if s.startswith("<"):
            try:
                return parse_xml_to_sections(s)
            except Exception as e:
                logger.warning(f"[make-call] could not parse XML sections: {e}")
                return []
        return []

    if not isinstance(sections, list):
        return []

    normalized = []
    for sec in sections:
        if not isinstance(sec, dict):
            # Stray string/other -> keep as a text section so prompt still uses it.
            if sec:
                normalized.append({"title": "", "type": "text", "content": str(sec)})
            continue

        sec = dict(sec)  # shallow copy; don't mutate caller's data
        stype = sec.get("type", "text")
        content = sec.get("content")

        # Identity content sent as a string -> parse "name: x" lines into fields.
        if stype == "identity" and isinstance(content, str):
            fields = []
            for line in content.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fields.append({"name": k.strip().lower(), "value": v.strip()})
                elif line.strip():
                    fields.append({"name": "description", "value": line.strip()})
            sec["content"] = fields

        normalized.append(sec)
    return normalized


@app.post("/api/make-call", tags=["Voice Calls"], summary="Initiate outbound call")
async def make_call(call_data: CallRequest, request: Request, current_user: str = Depends(get_current_user)):
    """Trigger Twilio to make an outbound call with the configured agent settings"""
    phone_number = call_data.phone_number
    language = call_data.language
    gender = call_data.gender
    speaker = call_data.speaker
    multi_stt = call_data.multi_stt

    # Diagnostic: log EXACTLY what the client sent so we can see why identity
    # (name/company) isn't being picked up. Logs the raw shape + the normalized
    # shape side by side.
    raw_sections = call_data.sections
    try:
        logger.info(f"[make-call] RAW sections from client: {json.dumps(raw_sections, ensure_ascii=False)[:1500]}")
    except Exception:
        logger.info(f"[make-call] RAW sections (non-JSON) type={type(raw_sections).__name__}: {str(raw_sections)[:1500]}")
    sections = _normalize_sections(raw_sections)
    try:
        logger.info(f"[make-call] NORMALIZED sections: {json.dumps(sections, ensure_ascii=False)[:1500]}")
    except Exception:
        logger.info(f"[make-call] NORMALIZED sections type={type(sections).__name__}: {str(sections)[:1500]}")

    # Reject malformed/impossible global numbers before any Twilio API call.
    # The validator also canonicalizes accepted input to E.164.
    phone_number = validate_phone_number(phone_number)

    session_id = request.session.get("user", "default")

    # If sections are provided from client, use them and update session
    if sections:
        session_xml_sections[session_id] = sections
    
    # Check if XML is available (either from client or session memory)
    if session_id not in session_xml_sections or not session_xml_sections[session_id]:
        raise HTTPException(status_code=400, detail="Please upload an XML template first")
    
    base_url = SERVER_URL.replace("https://", "").replace("http://", "")
    
    try:
        call = twilio_client.calls.create(
            to=phone_number,
            from_=TWILIO_PHONE_NUMBER,
            url=f"{SERVER_URL}/outbound-twiml",
            status_callback=f"{SERVER_URL}/call-status",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            record=True,
            recording_status_callback=f"{SERVER_URL}/twilio/recording-callback",
            recording_status_callback_event=["completed", "absent"],
        )
        
        # Store call settings including XML sections and language
        active_calls[call.sid] = {
            "phone_number": phone_number,
            "status": "initiated",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "language": language,
            "gender": gender,
            "speaker": speaker,
            "multi_stt": multi_stt,
            "xml_sections": session_xml_sections.get(session_id, [])
        }
        
        return JSONResponse({
            "status": "success",
            "call_sid": call.sid,
            "phone_number": phone_number
        })
    except Exception as e:
        logger.error(f"Call failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/hangup", tags=["Voice Calls"], summary="Terminate active call")
async def hangup(hangup_data: HangupRequest, current_user: str = Depends(get_current_user)):
    """End an active Twilio call session"""
    call_sid = hangup_data.call_sid
    
    if call_sid and call_sid in active_calls:
        try:
            # Record WHO ended the call (manual/agent-dashboard request).
            call_hangup_reasons[call_sid] = "Ended manually via /api/hangup"
            twilio_client.calls(call_sid).update(status="completed")
            return JSONResponse({"status": "success"})
        except Exception as e:
            logger.error(f"Hangup failed: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    return JSONResponse({"status": "not_found"})


@app.api_route("/outbound-twiml", methods=["GET", "POST"], response_class=Response, include_in_schema=False)
async def outbound_twiml(request: Request):
    """Twilio Webhook: Generate TwiML for media stream connection"""
    # Validate that the request came from Twilio
    await validate_twilio_request(request)
    
    form = await request.form()
    call_sid = form.get("CallSid", "")

    # Batch calls have no active_calls entry yet — hydrate it from the batch's
    # stashed agent config so /media-stream builds the right SimpleAgent.
    _ensure_batch_call_context(call_sid)

    # Generate a short-lived token for this call stream
    token = create_stream_token(call_sid)
    
    base_url = SERVER_URL.replace("https://", "").replace("http://", "")
    stream_url = f"wss://{base_url}/media-stream"
    # greeting_url = f"https://{base_url}/static/greeting.wav"
    
    response = VoiceResponse()
    # response.play(greeting_url)  # TwiML greeting - commented out, using Deepgram TTS instead
    response.pause(length=0.2)
    connect = Connect()
    stream = connect.stream(url=stream_url, name="voice_stream")
    stream.parameter(name='token', value=token)
    response.append(connect)
    
    return Response(content=str(response), media_type="application/xml")


@app.post("/call-status", include_in_schema=False)
async def call_status(request: Request):
    """Twilio Webhook: Update call status and pre-warm AI resources"""
    # Validate that the request came from Twilio
    await validate_twilio_request(request)
    form = await request.form()
    call_sid = form.get("CallSid", "")
    status = form.get("CallStatus", "")

    logger.info(f"Call status update: {call_sid} -> {status}")

    # Batch integration: hydrate config (so the ringing pre-warm below sees it)
    # and drive the batch state machine forward on terminal statuses.
    _ensure_batch_call_context(call_sid)
    await _advance_batch_on_status(call_sid, status)

    if call_sid in active_calls:
        active_calls[call_sid]["status"] = status
    
    # ── Pre-warm Sarvam TTS during ringing so WS is ready when user picks up ──
    if status == "ringing" and call_sid in active_calls:
        call_data = active_calls[call_sid]
        if not call_data.get("tts_manager"):  # don't double-create
            try:
                pre_tts = SarvamTTSManager(
                    call_sid,
                    gender=call_data.get("gender", "female"),
                    language=call_data.get("language", "hi"),
                    speaker=call_data.get("speaker"),
                )
                asyncio.create_task(pre_tts.setup())
                call_data["tts_manager"] = pre_tts
                logger.info(f"[TTS pre-warm] started for {call_sid}")
            except Exception as e:
                logger.error(f"[TTS pre-warm] failed: {e}")

        # ── Pre-warm Sarvam STT during ringing so WS is ready when user picks up ──
        if not call_data.get("stt_manager"):  # don't double-create
            try:
                stt_language = call_data.get("language", "hi")
                multi_stt = call_data.get("multi_stt", False)
                effective_stt_lang = "multi" if multi_stt else stt_language

                pre_stt = SarvamSTTManager(call_sid, language=effective_stt_lang)
                asyncio.create_task(pre_stt.setup())
                call_data["stt_manager"] = pre_stt
                logger.info(f"[STT pre-warm] started for {call_sid} (lang: {effective_stt_lang})")
            except Exception as e:
                logger.error(f"[STT pre-warm] failed: {e}")

    terminal_statuses = ("completed", "failed", "busy", "no-answer", "canceled")
    is_terminal = status in terminal_statuses

    # Get conversation_id for broadcasting to LeadAI inbox UI
    conversation_id = active_calls.get(call_sid, {}).get("conversation_id")

    # Broadcast exactly one UI event per provider status. A terminal status is
    # the canonical call-ended notification; detailed reasons remain in logs/DB.
    # For TERMINAL statuses we AWAIT the broadcast so the final state is
    # guaranteed delivered before the socket is torn down — a fire-and-forget
    # task here loses the race against cleanup and the client never sees the
    # 'no-answer'/'completed' update (the bug being fixed).
    try:
        if is_terminal:
            await manager.broadcast_to_call(call_sid, status)
            await manager.broadcast_transcript_event_to_call(call_sid, {
                "type": "status", "status": status
            })
            # Also broadcast to conversation WebSocket (inbox UI)
            if conversation_id:
                await manager.broadcast_to_call(conversation_id, status)
                await manager.broadcast_transcript_event_to_call(conversation_id, {
                    "type": "status", "status": status
                })
        else:
            asyncio.create_task(manager.broadcast_to_call(call_sid, status))
            asyncio.create_task(manager.broadcast_transcript_event_to_call(call_sid, {
                "type": "status", "status": status
            }))
            # Also broadcast to conversation WebSocket (inbox UI)
            if conversation_id:
                asyncio.create_task(manager.broadcast_to_call(conversation_id, status))
                asyncio.create_task(manager.broadcast_transcript_event_to_call(conversation_id, {
                    "type": "status", "status": status
                }))
    except Exception as e:
        logger.warning(f"[call-status] broadcast failed for {call_sid}: {e}")

    if is_terminal:
        # Resolve WHO/why the call ended for logging and persistence only. The
        # terminal status broadcast above is the UI's single end notification.
        reason = call_hangup_reasons.get(call_sid)
        if not reason:
            reason = {
                "no-answer": "No answer",
                "busy": "Line busy",
                "failed": "Call failed",
                "canceled": "Call canceled",
            }.get(status, "Caller hung up")
        logger.info(f"[HANGUP] call {call_sid} terminal status={status} — reason: {reason}")
        # Persist the hangup reason to the conversations table as a
        # ResponseType="hangup" row — same convention as voice_agent.py.
        # Deduped so the media-stream finally block + this webhook don't both
        # write a row for the same call.
        await asyncio.to_thread(
            _save_hangup_reason_once, call_sid, reason,
            active_calls.get(call_sid, {}).get("phone_number"),
        )

        # Cleanup pre-warmed TTS if call never answered
        call_data = active_calls.pop(call_sid, {})
        pre_tts = call_data.get("tts_manager")
        if pre_tts:
            asyncio.create_task(pre_tts.cleanup())
        try:
            update_call_status(call_sid, status, phone_number=call_data.get("phone_number"))
        except Exception as e:
            logger.error(f"Failed to update call status: {e}")

        # Cleanup pre-warmed STT if exists
        pre_stt = call_data.get("stt_manager")
        if pre_stt:
            asyncio.create_task(pre_stt.cleanup())
            logger.info(f"[STT pre-warm] cleaned up for {call_sid}")

        # Sync transcript from legacy conversations table to leadai_messages
        # so the inbox UI can display it immediately.
        if call_data.get("leadai") and call_data.get("conversation_id"):
            asyncio.create_task(_sync_leadai_transcript(call_sid))

        # Give the client a moment to receive the terminal + hangup messages,
        # then clean up its call-scoped WS connections (general + transcript).
        async def _cleanup_call_ws(sid: str):
            try:
                await asyncio.sleep(2.0)
                await manager.cleanup_call_only_connections(sid)
            except Exception as e:
                logger.warning(f"[call-status] WS cleanup failed for {sid}: {e}")
        asyncio.create_task(_cleanup_call_ws(call_sid))
        call_hangup_reasons.pop(call_sid, None)

    return Response(status_code=200)


@app.post("/twilio/recording-callback", include_in_schema=False)
async def recording_callback(request: Request):
    """Twilio Webhook: receive recording metadata when a call recording is ready.
    Stores the Twilio recording URL/SID on the existing phone_call_transcript
    document keyed by CallSid. Twilio hosts the audio (.mp3 / .wav) at that URL."""
    await validate_twilio_request(request)
    form = await request.form()

    call_sid       = form.get("CallSid", "")
    recording_sid  = form.get("RecordingSid", "")
    recording_url  = form.get("RecordingUrl", "")
    recording_dur  = form.get("RecordingDuration", "")
    recording_chan = form.get("RecordingChannels", "")
    recording_src  = form.get("RecordingSource", "")
    recording_stat = form.get("RecordingStatus", "")

    logger.info(
        f"[REC] call={call_sid} status={recording_stat} sid={recording_sid} "
        f"dur={recording_dur}s url={recording_url}"
    )

    # Archive the audio to MinIO, then delete it from Twilio. If MinIO isn't
    # configured (or archival fails) we still persist the Twilio URL so nothing
    # is lost — we just skip the offload+delete and keep Twilio as the source.
    minio_url = None
    if recording_url and minio_enabled():
        try:
            import httpx
            from storage import upload_bytes, build_key

            auth = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Ask Twilio for the mp3 rendering explicitly.
                resp = await client.get(f"{recording_url}.mp3", auth=auth)
                if resp.status_code == 200:
                    ext, content_type = "mp3", "audio/mpeg"
                else:
                    # Fall back to whatever Twilio serves at the base URL.
                    resp = await client.get(recording_url, auth=auth)
                    resp.raise_for_status()
                    content_type = resp.headers.get("Content-Type", "audio/wav")
                    ext = "mp3" if content_type == "audio/mpeg" else "wav"

                minio_url = await upload_bytes(resp.content, build_key(call_sid, ext), content_type)

            # Offloaded successfully — remove the recording from Twilio.
            if recording_sid:
                try:
                    twilio_client.recordings(recording_sid).delete()
                    logger.info(f"[REC] deleted Twilio recording {recording_sid} after MinIO archive")
                except Exception as del_err:
                    logger.error(f"[REC] MinIO archive OK but Twilio delete failed for {recording_sid}: {del_err}")
        except Exception as e:
            logger.error(f"[REC] MinIO archive failed for {call_sid} (keeping Twilio URL): {e}")
            minio_url = None

    try:
        save_recording(
            call_sid,
            recording_sid=recording_sid,
            recording_url=recording_url,
            recording_duration=int(recording_dur) if recording_dur.isdigit() else recording_dur,
            recording_channels=recording_chan,
            recording_source=recording_src,
            recording_status=recording_stat,
            recording_minio_url=minio_url,
        )
    except Exception as e:
        logger.error(f"[REC] failed to save metadata for {call_sid}: {e}")

    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Call data read APIs. Input data formats and response shapes are aligned to
# voice_agent.py (Domain.schema DTOs); backed by the same Domain ORM tables
# (conversations / calllogs / callstatus / recordings).
# ---------------------------------------------------------------------------
@app.get("/api/conversations", response_model=List[schema.ConversationResponse],
         tags=["Call Data"], summary="Get Call Transcript",
         description="**Input:** Query `call_sid`.<br>**Output:** List of message objects.<br><br>Retrieves the full conversation history (transcript) for a specific call.")
async def get_all_conversations(call_sid: Optional[str] = Query(None),
                                current_user: str = Depends(get_current_user)):
    """Transcript as a bare list of ConversationResponse, matching voice_agent."""
    try:
        rows = fetch_conversation(call_sid) if call_sid else []
        return [
            schema.ConversationResponse(
                id=r["id"],
                call_sid=r["call_sid"],
                response_type=r["response_type"],
                response_text=r["response_text"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
    except Exception as e:
        logger.error(f"Fetch conversation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching conversations: {str(e)}")


@app.post("/api/call-logs", response_model=schema.PaginatedCallLogResponse,
          tags=["Call Data"], summary="Get Call Logs",
          description="**Input:** JSON `CallLogsRequest` (page, page_size).<br>**Output:** Paginated list of call logs with statuses and recordings.")
async def get_all_call_logs(request: schema.CallLogsRequest,
                            current_user: str = Depends(get_current_user)):
    """Paginated CallLogResponse list, matching voice_agent's /call-logs."""
    try:
        data = list_call_logs(request.page, request.page_size)
        return schema.PaginatedCallLogResponse(
            items=[
                schema.CallLogResponse(
                    id=log["id"],
                    call_sid=log["call_sid"],
                    phone_number=log["phone_number"],
                    statuses=[
                        schema.CallStatusResponse(
                            id=s["id"], call_status=s["call_status"], date=s["date"]
                        ) for s in log["statuses"]
                    ],
                    recording_url=log["recording_url"],
                )
                for log in data["items"]
            ],
            total_items=data["total_items"],
        )
    except Exception as e:
        logger.error(f"Error fetching call logs: {e}")
        raise HTTPException(status_code=500, detail="Error fetching call logs")


@app.get("/api/recordings", tags=["Call Data"], summary="List Recordings",
         description="**Input:** Query `page`, `page_size`.<br>**Output:** Paginated recordings (id, call_sid, recording_url, created_at).")
async def get_recordings(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
                         current_user: str = Depends(get_current_user)):
    """Paginated recordings list (recording_url is the MinIO archive URL)."""
    try:
        return JSONResponse(jsonable_encoder(list_recordings(page, page_size)))
    except Exception as e:
        logger.error(f"List recordings failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/call-logs", response_model=schema.GenericDataResponse,
            tags=["Call Data"], summary="Delete All Logs",
            description="**Output:** Success message.<br><br>⚠️ **DESTRUCTIVE:** Deletes ALL call logs and statuses.")
async def delete_call_logs(current_user: str = Depends(get_current_user)):
    """Purge calllogs + callstatus. Returns voice_agent's {data: ...} shape."""
    try:
        purge_call_logs()
        return {"data": "Deleted Successfully!"}
    except Exception as e:
        logger.error(f"Delete call logs failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


def _valid_callsid(call_sid: str) -> bool:
    """Reject the literal 'undefined'/'null'/empty ids the client sometimes
    sends before it has the real CallSid."""
    return bool(call_sid) and call_sid.lower() not in ("undefined", "null")


@app.websocket("/ws/transcript/{call_sid}")
async def transcript_ws(ws: WebSocket, call_sid: str):
    """Live transcript stream (user + agent turns). Routed through the shared
    ConnectionManager so history is replayed on connect and messages are
    chunk-streamed — same as voice_agent.py."""
    if not _valid_callsid(call_sid):
        await ws.close(code=1008)
        logger.warning(f"[ws/transcript] rejected invalid call_sid={call_sid!r}")
        return
    await manager.connect(ws, call_sid, connection_type="transcript")
    logger.info(f"Transcript WS connected for call: {call_sid}")
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(ws, call_sid, connection_type="transcript")
        logger.info(f"Transcript WS disconnected for call: {call_sid}")


async def broadcast_transcript(call_sid: str, msg_type: str, text: str):
    """Broadcast via the shared ConnectionManager (same model as voice_agent.py):
      - "user"/"agent" -> transcript bucket (chunked JSON, replayable history)
      - "status"       -> general bucket (plain status line)
    Also push to the conversation WebSocket (inbox UI) using the conversation_id.
    Also kept on the local transcript_connections fallback for any direct client."""
    conversation_id = active_calls.get(call_sid, {}).get("conversation_id")
    try:
        if msg_type == "status":
            await manager.broadcast_to_call(call_sid, text)
        else:
            await manager.broadcast_transcript_to_call(
                call_sid, {"type": msg_type, "text": text})
        # Also broadcast to conversation WebSocket (inbox UI)
        if conversation_id:
            if msg_type == "status":
                await manager.broadcast_to_call(conversation_id, text)
            else:
                await manager.broadcast_transcript_to_call(
                    conversation_id, {"type": msg_type, "text": text})
    except Exception:
        pass

    # Back-compat: also push to any socket registered on the local dict.
    ws = transcript_connections.get(call_sid)
    if ws is not None:
        try:
            await ws.send_json({
                "type": msg_type,
                "text": text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass


@app.websocket("/media-stream")
async def media_stream(ws: WebSocket):
    # We accept first, then verify in the 'start' event because Twilio parameters
    # are sent after the connection is established.
    await ws.accept()
    
    authenticated = False
    
    stream_sid = None
    call_sid = None # Will be confirmed in 'start' event
    phone_number = None
    tts_manager = None
    stt_manager: SarvamSTTManager = None
    agent = None  # Will be created when call_sid is received with XML sections
    stt_language = "multi"  # Will be set from call settings
    stop_tts_event = asyncio.Event()
    silero_vad = SileroVoiceActivityDetector()

    transcript_queue = asyncio.Queue()
    buffer_sentence = []
    last_spoken_time = None
    should_terminate = False
    agent_initiated_hangup = False
    stt_started = False  # Track if Sarvam STT has been started
    
    vad_last_voice_ts = 0.0
    silero_buffer = bytearray()
    ratecv_state = None
    tts_already_cleared = {"value": False}

    # Barge-in tuning. 0.60 prob / 64 ms was tripping on coughs, breath,
    # and ambient room noise. Raised to 0.75 prob / 160 ms so the trigger
    # needs roughly a full syllable of confident speech, not a transient.
    # If real interruptions start feeling sluggish again, dial back to
    # 0.70 / 128 ms before going lower.
    BARGE_IN_PROB = 0.75         # Silero speech-probability threshold
    BARGE_IN_SUSTAIN_FRAMES = 5  # ~160ms of continuous speech (32ms per frame)
    vad_speech_streak = 0
    
    # Pause floors that protect against splitting mid-thought. Even with a
    # strong end-of-sentence punctuation the user often takes a 0.5-0.8s breath
    # before continuing (e.g. "That is my full name." [breath] "My full name is
    # Priya"), so finalising on a 0.3s pause causes two LLM calls for one turn.
    STRONG_PAUSE = 0.6
    WEAK_PAUSE = 0.9
    NO_PUNCT_PAUSE = 1.4

    STRONG_PUNCT_RE = re.compile(r'[.!?]+$')
    WEAK_PUNCT_RE = re.compile(r'[,;:]$')
    
    last_transcript_is_final = False

    async def on_transcript(snippet: str, detected_lang: str = None):
        nonlocal last_spoken_time, buffer_sentence, pending_merge_text, last_transcript_is_final
        snippet = (snippet or "").strip()
        if not snippet:
            return

        # Sarvam emits one transcript per VAD-segmented utterance; treat as final.
        is_final = True
        last_transcript_is_final = is_final
        flag = "FINAL" if is_final else "interim"

        # Unified Merging: Always append to buffer or resume from VAD hold
        if buffer_sentence:
            prev = " ".join(buffer_sentence).strip()
            merged = (prev + " " + snippet).strip()
            buffer_sentence = [merged]
            logger.info(f"[STT {flag}] MERGED snippet='{snippet}' → buffer='{merged[:120]}'")
        elif pending_merge_text:
            # If we had a VAD-Gate hold, resume it into the buffer
            merged = (pending_merge_text + " " + snippet).strip()
            buffer_sentence = [merged]
            pending_merge_text = ""
            logger.info(f"[STT {flag}] RESUMED from VAD hold snippet='{snippet}' → buffer='{merged[:120]}'")
        else:
            buffer_sentence = [snippet]
            logger.info(f"[STT {flag}] NEW snippet='{snippet}'")

        # Sentinel: tells monitor_silence we have buffered text to finalize.
        # Silence timing itself now comes from Silero VAD (vad_last_voice_ts),
        # not from Deepgram chunk arrival — Deepgram is noisy on digit-strings
        # and emits late chunks well after the user stops speaking.
        last_spoken_time = time.time()

    
    pending_merge_text = ""
    
    async def monitor_silence():
        nonlocal last_spoken_time, buffer_sentence, should_terminate, vad_last_voice_ts, pending_merge_text, STRONG_PAUSE, WEAK_PAUSE, NO_PUNCT_PAUSE, last_transcript_is_final

        while not should_terminate:
            if last_spoken_time is not None and buffer_sentence:
                # Silence is now measured from Silero VAD — the time since the
                # last frame Silero classified as speech. Deepgram chunk arrival
                # is no longer part of this calculation; it kept the silence
                # timer alive long after the user actually stopped speaking
                # (late-arriving final chunks on slow digit-strings).
                vad_silence = time.time() - vad_last_voice_ts

                text_preview = " ".join(buffer_sentence).strip()

                if STRONG_PUNCT_RE.search(text_preview):
                    required_pause = STRONG_PAUSE
                    punct_class = "strong"
                elif WEAK_PUNCT_RE.search(text_preview):
                    required_pause = WEAK_PAUSE
                    punct_class = "weak"
                else:
                    required_pause = NO_PUNCT_PAUSE
                    punct_class = "none"

                if vad_silence >= required_pause:
                    full_sentence = " ".join(buffer_sentence).strip()
                    full_sentence = re.sub(r"\s+", " ", full_sentence)

                    buffer_sentence.clear()
                    last_spoken_time = None
                    tts_already_cleared["value"] = False

                    if not full_sentence:
                        continue

                    if pending_merge_text:
                        combined = f"{pending_merge_text} {full_sentence}".strip()
                        combined = re.sub(r"\s+", " ", combined)
                        await transcript_queue.put(combined)
                        logger.info(
                            f"FINALIZED (combined) [punct={punct_class} pause={required_pause:.2f}s "
                            f"vad_silence={vad_silence:.2f}s]: '{combined}'"
                        )
                        pending_merge_text = ""
                    else:
                        await transcript_queue.put(full_sentence)
                        logger.info(
                            f"FINALIZED [punct={punct_class} pause={required_pause:.2f}s "
                            f"vad_silence={vad_silence:.2f}s]: '{full_sentence}'"
                        )

            await asyncio.sleep(0.05)
    
    async def process_transcripts():
        nonlocal should_terminate, agent_initiated_hangup

        # Grace window: after a transcript finalizes, wait briefly to see if the
        # user is just taking a breath and a short follow-up ("इसमें?", "क्या?")
        # is coming. If so, merge into one LLM call instead of two.
        # Tight pre-LLM merge window. The old 0.45s value was killing
        # 400+ ms of latency on every turn. Sarvam STT's natural multi-
        # segment emission for a single thought is usually <150 ms apart,
        # so 0.15 still catches the typical case. Anything longer is
        # handled by the preemption watcher (see below), which can abort
        # an in-flight LLM call when a fragment arrives mid-stream.
        FOLLOWUP_GRACE_S = 0.15

        while not should_terminate:
            try:
                user_text = await asyncio.wait_for(transcript_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

            try:
                # Drain any follow-up fragments that arrive within the grace window,
                # so a natural mid-thought pause doesn't trigger a wasted LLM call.
                grace_start = time.time()
                merge_count = 0
                try:
                    while True:
                        extra = await asyncio.wait_for(transcript_queue.get(), timeout=FOLLOWUP_GRACE_S)
                        user_text = f"{user_text} {extra}".strip()
                        merge_count += 1
                        logger.info(f"[MERGE #{merge_count}] absorbed follow-up → '{user_text}'")
                except asyncio.TimeoutError:
                    pass
                grace_waited = time.time() - grace_start
                logger.info(
                    f"[GRACE] window={FOLLOWUP_GRACE_S:.2f}s waited={grace_waited:.2f}s "
                    f"merges={merge_count}"
                )

                # Force every spoken date into Indian DD/MM/YYYY before the LLM
                # sees it. Deepgram's smart_format emits US MM/DD/YYYY for English
                # speech (e.g. "two November 1992" → "11/02/1992") while a Hindi
                # path may already be DD/MM. Decide per-match:
                #   - first  > 12 → already DD/MM (first is the day) → keep as is
                #   - second > 12 → MM/DD (second is the day)       → swap
                #   - both  <= 12 → ambiguous, assume MM/DD (Deepgram default) → swap
                def _to_dd_mm(m):
                    a, b, y = m.group(1), m.group(2), m.group(3)
                    ai, bi = int(a), int(b)

                    # If clearly DD/MM (day > 12)
                    if ai > 12:
                        logger.info(f"[DATE NORM] '{m.group(0)}' kept as DD/MM/YYYY")
                        return m.group(0)

                    # If clearly MM/DD (month > 12)
                    if bi > 12:
                        swapped = f"{b}/{a}/{y}"
                        logger.info(f"[DATE NORM] '{m.group(0)}' → '{swapped}' (MM/DD detected)")
                        return swapped

                    # BOTH <= 12 → default INDIA RULE (DD/MM)
                    # So DO NOT swap
                    logger.info(f"[DATE NORM] '{m.group(0)}' kept as DD/MM (India default)")
                    return m.group(0)

                date_normalized = re.sub(
                    r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b',
                    _to_dd_mm,
                    user_text,
                )

                # Deepgram time-formats spoken digit sequences with ":" / "." and
                # also splits digit runs across spaces when the speaker pauses
                # (e.g. spoken phone-last-4 "4506" arrives as "4 05:06" or "04:05 6").
                # Collapse digit-separating spaces, drop the colon/period punctuation,
                # then strip Deepgram's HH-padding leading zero, so verification
                # answers reach the LLM as a clean digit string.
                normalized = re.sub(r'(?<=\d)[\s:.]+(?=\d)', '', date_normalized)
                normalized = re.sub(r'\b0(\d{3,})\b', r'\1', normalized)
                if normalized != user_text:
                    logger.info(f"[DIGIT NORM] '{user_text}' → '{normalized}'")
                    user_text = normalized

                if call_sid:
                    asyncio.create_task(broadcast_transcript(call_sid, "user", user_text))
                    asyncio.create_task(asyncio.to_thread(save_message, call_sid, "user", user_text, phone_number))

                logger.info(f"USER: {user_text}")
                turn_start = time.monotonic()
            except Exception as _pt_pre_err:
                logger.exception(f"[PT] pre-LLM error on '{user_text}': {_pt_pre_err}")
                continue
            
            # Skip if agent not yet initialized
            if agent is None:
                continue

            # ─── Speculative dispatch with preemption ──────────────────────
            # We fire the LLM call IMMEDIATELY after STT finalize (post the
            # tight 150 ms merge window above), without waiting around for
            # a slower follow-up. While the LLM is streaming, a watcher
            # task listens on transcript_queue. If a follow-up arrives
            # before the assistant finishes:
            #   - watcher sets stop_tts_event (reusing the barge-in path)
            #   - the LLM loop breaks, any partial TTS is dropped
            #   - we pop the partial user turn from agent.history
            #   - the outer loop retries with user_text = original + extra
            # 95% of turns there's no follow-up and we save the full 450 ms
            # the old grace window cost. The 5% with a follow-up pay one
            # wasted LLM call but the user-perceived latency is still
            # lower than the old grace-then-dispatch path.
            preempt_retries = 0
            MAX_PREEMPT_RETRIES = 2
            full_response = ""

            while True:
                stop_tts_event.clear()
                tts_already_cleared["value"] = False

                sentence_queue = asyncio.Queue()
                preempt_info = {"triggered": False, "extra": ""}

                async def tts_worker():
                    """Consumes sentences from the queue and plays them with pipelining"""
                    try:
                        while not stop_tts_event.is_set():
                            sentence = await sentence_queue.get()
                            if sentence is None: # Sentinel for end of response
                                # For the very last sentence, we MUST wait for it to finish
                                # so the turn doesn't end abruptly while still speaking.
                                # We'll just wait a tiny bit to let any pending monitor finish
                                await asyncio.sleep(0.5)
                                break

                            # Pipeline: don't wait for Sentence 1 to finish before sending Sentence 2
                            await tts_manager.stream_tts_audio(
                                ws, stream_sid, sentence, stop_tts_event, wait_for_end=False
                            )
                    except Exception as e:
                        logger.error(f"TTS Worker Error: {e}")
                    finally:
                        # End of turn — tell Sarvam to flush whatever text it's still
                        # buffering internally. We stream convert() per sentence without
                        # per-sentence flush so Sarvam can batch; this single flush at
                        # turn-end renders the tail. Also runs on barge-in so the
                        # in-flight utterance is closed cleanly.
                        if tts_manager:
                            try:
                                await tts_manager.end_turn()
                            except Exception as e:
                                logger.error(f"end_turn error: {e}")
                        logger.debug("TTS Worker finished")

                async def followup_watcher():
                    """Watch transcript_queue for a follow-up fragment during
                    LLM streaming. If one arrives, abort the current turn
                    by setting stop_tts_event; the outer loop will merge
                    and restart."""
                    try:
                        while not stop_tts_event.is_set():
                            try:
                                extra = await asyncio.wait_for(transcript_queue.get(), timeout=0.05)
                            except asyncio.TimeoutError:
                                continue
                            preempt_info["triggered"] = True
                            preempt_info["extra"] = extra
                            logger.info(f"[PREEMPT] follow-up '{extra[:60]}' arrived mid-LLM — aborting")
                            stop_tts_event.set()
                            # Tell Twilio to drop any audio we already queued
                            try:
                                await ws.send_json({"event": "clear", "streamSid": stream_sid})
                            except Exception:
                                pass
                            if tts_manager and not tts_already_cleared["value"]:
                                await tts_manager.clear_buffer()
                                tts_already_cleared["value"] = True
                            return
                    except asyncio.CancelledError:
                        return

                # Start worker + watcher tasks
                worker_task = asyncio.create_task(tts_worker())
                watcher_task = asyncio.create_task(followup_watcher())

                is_first_chunk = True
                collected_text = ""
                full_response = ""
                first_audio_logged = False

                import unicodedata
                async for chunk in agent.get_response(user_text):
                    if stop_tts_event.is_set():
                        break

                    collected_text += chunk
                    full_response += chunk
                    if not first_audio_logged:
                        ttfb_ms = (time.monotonic() - turn_start) * 1000
                        logger.info(f"[TURN] user→first LLM chunk: {ttfb_ms:.0f}ms")
                        first_audio_logged = True

                    # Hyper-Responsive Logic:
                    # - First chunk: Fire after 18 chars (approx 3 words)
                    # - Steady state: Fire after 45 chars for stable naturalness
                    threshold = 18 if is_first_chunk else 45

                    has_terminator = any(p in chunk for p in ".!?।")
                    has_comma = any(p in chunk for p in ",;")

                    is_boundary = bool(re.search(r'[\s.!?,;।]$', collected_text))

                    # Combined Mark Protection (Matras, Accents, etc.)
                    is_combining = False
                    if collected_text:
                        cat = unicodedata.category(collected_text[-1])
                        if cat.startswith('M'):
                            is_combining = True

                    should_fire = has_terminator or has_comma or (
                        len(collected_text.strip()) >= threshold and
                        is_boundary and
                        not is_combining
                    )

                    if should_fire:
                        clean_sent = collected_text.strip()
                        if clean_sent and any(c.isalnum() for c in clean_sent):
                            clean_sent = _strip_tts_control_tokens(
                                _strip_digit_hyphen(clean_sent)
                            )
                            if clean_sent:
                                await sentence_queue.put(clean_sent)
                                is_first_chunk = False
                        collected_text = ""

                # Catch remaining text (only if we weren't preempted)
                if collected_text.strip() and not stop_tts_event.is_set():
                    clean_final = collected_text.strip()
                    if any(c.isalnum() for c in clean_final):
                        clean_final = _strip_tts_control_tokens(
                            _strip_digit_hyphen(clean_final)
                        )
                        if clean_final:
                            await sentence_queue.put(clean_final)

                await sentence_queue.put(None)  # Signal worker to stop
                await worker_task

                # Tear down the watcher in all cases
                if not watcher_task.done():
                    watcher_task.cancel()
                try:
                    await watcher_task
                except (asyncio.CancelledError, Exception):
                    pass

                # ─── Preemption handling ─────────────────────────────────
                if preempt_info["triggered"] and preempt_retries < MAX_PREEMPT_RETRIES:
                    # The watcher set stop_tts_event; the LLM loop bailed and
                    # any partial TTS was cleared. agent.get_response already
                    # appended the user turn but did NOT append the assistant
                    # turn (it bailed before the final history.append). Pop
                    # the orphaned user turn and merge the follow-up text.
                    if agent.history and agent.history[-1].get("role") == "user":
                        agent.history.pop()
                    extra = preempt_info["extra"]
                    user_text = f"{user_text} {extra}".strip()
                    preempt_retries += 1
                    logger.info(
                        f"[PREEMPT] retrying with merged text "
                        f"(attempt {preempt_retries}/{MAX_PREEMPT_RETRIES}): {user_text[:120]}"
                    )
                    continue

                # Normal completion path
                break

            raw_full_response = full_response
            full_response = _strip_tts_control_tokens(full_response)

            if call_sid and full_response:
                # Use a fire-and-forget task for broadcasting
                asyncio.create_task(broadcast_transcript(call_sid, "agent", full_response))
                asyncio.create_task(asyncio.to_thread(save_message, call_sid, "agent", full_response, phone_number))

            turn_total_ms = (time.monotonic() - turn_start) * 1000
            logger.info(f"[TURN] complete in {turn_total_ms:.0f}ms (user finalized → LLM done)")
            logger.info(f"AGENT: {full_response}")
            
            # Call ends if the agent emits [END_CALL] OR speaks a recognised closing phrase.
            lower = raw_full_response.lower()
            closing_phrases = [
                # English
                "goodbye", "good bye", "bye bye", "bye!", " bye.", " bye,", "bye.",
                # Do not use bare "take care": it also appears in non-closing
                # promises such as "I will take care of everything".
                "have a great day", "have a nice day", "have a good day",
                # Do not use "see you" here: it is context-dependent and can occur
                # in non-closing sentences. The agent must emit [END_CALL] when it
                # genuinely intends to finish the call.
                "talk soon",
                # Hinglish (Latin)
                "alvida", "dhyan rakhna", "chalta hoon", "chalti hoon",
                "phir milenge", "rakhta hoon", "rakhti hoon", "shubh din",
                # Hindi (Devanagari)
                "अलविदा", "ध्यान रखना", "ध्यान रखिए", "ध्यान रखिएगा",
                "फिर मिलेंगे", "चलता हूँ", "चलती हूँ", "रखता हूँ", "रखती हूँ",
                "आपका दिन शुभ हो", "शुभ दिन", "नमस्कार",
                # Punjabi (Gurmukhi). Avoid "ਸਤਿ ਸ੍ਰੀ ਅਕਾਲ" because it is
                # commonly an opening greeting as well as a farewell.
                "ਅਲਵਿਦਾ", "ਫਿਰ ਮਿਲਾਂਗੇ", "ਰੱਬ ਰਾਖਾ",
                "ਤੁਹਾਡਾ ਦਿਨ ਚੰਗਾ ਹੋਵੇ", "ਤੁਹਾਡਾ ਦਿਨ ਵਧੀਆ ਰਹੇ",
                "ਬਾਇ", "ਬਾਏ",
            ]
            triggered_by = None
            if "[END_CALL]" in raw_full_response or "[end_call]" in lower:
                triggered_by = "[END_CALL] token"
            else:
                for p in closing_phrases:
                    if p in lower or p in full_response:
                        triggered_by = f"closing phrase: '{p.strip()}'"
                        break
            if triggered_by:
                logger.info(f"Hangup trigger ({triggered_by}) — terminating call {call_sid}")
                if tts_manager:
                    await tts_manager.wait_until_done(timeout=10.0)
                await asyncio.sleep(1.0)
                should_terminate = True
                agent_initiated_hangup = True
                if call_sid:
                    # Record the detailed reason for server logs and persistence;
                    # the terminal callback sends the UI's one end notification.
                    call_hangup_reasons[call_sid] = f"AI ended the call ({triggered_by})"
    
    try:
        # Start background tasks (Sarvam STT is wired when we receive call_sid)
        silence_task = asyncio.create_task(monitor_silence())
        process_task = asyncio.create_task(process_transcripts())
        
        while not should_terminate:
            try:
                message = await asyncio.wait_for(ws.receive_text(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                break
            
            try:
                data = json.loads(message)
            except:
                continue
            
            event = data.get("event", "")
            
            if event == "start":
                stream_sid = data.get("start", {}).get("streamSid")
                call_sid = data.get("start", {}).get("callSid")
                
                # Get the token from customParameters
                custom_params = data.get("start", {}).get("customParameters", {})
                token = custom_params.get("token")
                
                # Verify the token
                try:
                    from auth import _decode_token
                    if not token:
                        raise Exception("Missing token in start event")
                    payload = _decode_token(token, "stream")
                    authenticated_call_sid = payload["sub"]
                    
                    if call_sid != authenticated_call_sid:
                        raise Exception(f"CallSid mismatch: {call_sid} vs {authenticated_call_sid}")
                    
                    authenticated = True
                    logger.info(f"WebSocket authenticated for call {call_sid}")
                except Exception as e:
                    logger.error(f"WebSocket authentication failed: {e}")
                    await ws.close(code=1008)
                    break
                
                if call_sid:
                    call_data = active_calls.get(call_sid, {})
                    phone_number = call_data.get("phone_number")
                    xml_sections = call_data.get("xml_sections", [])
                    stt_language = call_data.get("language", "hi")
                    gender = call_data.get("gender", "female")  # 'female' or 'male'
                    speaker = call_data.get("speaker")
                    multi_stt = call_data.get("multi_stt", False)
                    toggle_state = "ON (multi mirror mode)" if multi_stt else "OFF (locked to dropdown)"
                    logger.info(
                        f"[UI CONFIG] call={call_sid} | dropdown_language={stt_language} | "
                        f"smart_multilingual_toggle={toggle_state} | gender={gender} | speaker={speaker}"
                    )

                    # Sarvam Saaras:v3 streaming. The UI dropdown locks the STT
                    # language (so e.g. 'hi' → 'hi-IN'); only the Multi-lingual
                    # checkbox switches Sarvam into 'unknown' auto-detect.
                    effective_stt_lang = "multi" if multi_stt else stt_language

                    # Reuse pre-warmed TTS if available, else create fresh
                    pre_warmed = call_data.get("tts_manager")
                    if pre_warmed:
                        tts_manager = pre_warmed
                        logger.info(f"[TTS pre-warm] reusing pre-warmed WS for {call_sid}")
                        tts_setup_task = asyncio.create_task(asyncio.sleep(0))  # no-op
                    else:
                        tts_manager = SarvamTTSManager(call_sid, gender=gender, language=stt_language, speaker=speaker)
                        tts_setup_task = asyncio.create_task(tts_manager.setup())

                    # Reuse pre-warmed Sarvam STT connection if available
                    pre_warmed_stt = call_data.get("stt_manager")
                    if pre_warmed_stt:
                        stt_manager = pre_warmed_stt
                        logger.info(f"[STT pre-warm] reusing pre-warmed connection for {call_sid}")
                        stt_setup_task = asyncio.create_task(asyncio.sleep(0))  # already started
                    else:
                        stt_manager = SarvamSTTManager(call_sid, language=effective_stt_lang)
                        stt_setup_task = asyncio.create_task(stt_manager.setup())
                        logger.info(f"Sarvam STT started fresh for {call_sid}")

                    # Attach the snippet handler now that the per-call closure
                    # exists; this works whether the WS was pre-warmed or fresh.
                    stt_manager.on_transcript = on_transcript
                    
                    # Extract agent name and company from the sections, in WHATEVER
                    # shape the client sent them. The old loop only matched a rigid
                    # type=='identity' + content=[{name,value}] structure and fell
                    # back to JIN/Bank for anything else. This scans every section
                    # across all the shapes we actually receive:
                    #   - rawContent string  ("name: Riya\ncompany: Prime")
                    #   - content list of {name,value} dicts
                    #   - content string     ("name: Riya\ncompany: Prime")
                    #   - field key variants (name / agent_name / agent name)
                    # and finally falls back to scanning the rendered prompt text.
                    agent_name = None
                    company_name = None
                    call_topic = None

                    def _scan_text_for_identity(text: str):
                        nonlocal agent_name, company_name, call_topic
                        if not text:
                            return
                        if agent_name is None:
                            m = re.search(r'(?:agent\s*)?name\s*[:=]\s*([^\n,;]+)', text, re.IGNORECASE)
                            if m:
                                agent_name = m.group(1).strip()
                        if company_name is None:
                            m = re.search(r'company\s*[:=]\s*([^\n,;]+)', text, re.IGNORECASE)
                            if m:
                                company_name = m.group(1).strip()
                        if call_topic is None:
                            m = re.search(r'(?:topic|call[_\s]topic|call[_\s]reason|purpose)\s*[:=]\s*([^\n,;]+)', text, re.IGNORECASE)
                            if m:
                                call_topic = m.group(1).strip()

                    for section in (xml_sections or []):
                        if not isinstance(section, dict):
                            if isinstance(section, str):
                                _scan_text_for_identity(section)
                            continue

                        # rawContent (UI-edited) — scan as text.
                        _scan_text_for_identity(section.get('rawContent') or "")

                        content = section.get('content')
                        if isinstance(content, str):
                            _scan_text_for_identity(content)
                        elif isinstance(content, list):
                            for field in content:
                                if not isinstance(field, dict):
                                    continue
                                fname = (field.get('name') or field.get('label') or "").strip().lower()
                                fval = field.get('value')
                                if not fval:
                                    continue
                                if fname in ("name", "agent name", "agent_name", "agentname") and agent_name is None:
                                    agent_name = str(fval).strip()
                                elif fname in ("company", "company name", "company_name", "organisation", "organization") and company_name is None:
                                    company_name = str(fval).strip()
                                elif fname in ("topic", "call_topic", "call topic", "call_reason", "call reason", "purpose") and call_topic is None:
                                    call_topic = str(fval).strip()

                    # Last resort: scan the fully-rendered prompt text (this is what
                    # the LLM sees, so if the name is anywhere usable, it's here).
                    if agent_name is None or company_name is None or call_topic is None:
                        try:
                            _scan_text_for_identity(sections_to_prompt(xml_sections))
                        except Exception:
                            pass

                    # Final fallbacks — only agent_name gets a default; company and
                    # topic remain None so the greeting skips those parts entirely.
                    agent_name = agent_name or "Agent"

                    logger.info(f"Extracted metadata: Name='{agent_name}', Company='{company_name}', Topic='{call_topic}' for {call_sid}")
                    
                    # Detect gender from call data
                    logger.info(f"Agent gender: {gender}")
                    
                    # Agent creation
                    # When Smart Multi-lingual STT is on, the agent must MIRROR the
                    # caller's language instead of locking to the dropdown value
                    # (otherwise we transcribe English correctly but always reply
                    # in Hindi). Pass "multi" so the system prompt switches to its
                    # "mirror the user" branch.
                    agent_language = "multi" if multi_stt else stt_language
                    agent = SimpleAgent(xml_sections, gender=gender, language=agent_language)

                    # NOTE: we intentionally do NOT broadcast an app-level
                    # "connected" status. The client tracks call state purely
                    # from Twilio's real statuses (initiated/ringing/in-progress/
                    # completed) delivered via /call-status.

                    # ── Greeting + reason-for-call + permission-to-continue ──────
                    # Build greeting dynamically from extracted script metadata.
                    # company_name and call_topic may be None — omit those parts.
                    _co = f"{company_name} से " if company_name else ""
                    _co_en = f" from {company_name}" if company_name else ""
                    _topic_hi_m = f"मैं आपसे {call_topic} के बारे में बात करना चाहता था, " if call_topic else ""
                    _topic_hi_f = f"मैं आपसे {call_topic} के बारे में बात करना चाहती थी, " if call_topic else ""
                    _topic_ta   = f"உங்கள் {call_topic} பற்றி பேச விரும்புகிறேன், " if call_topic else ""
                    _topic_te   = f"మీతో {call_topic} గురించి మాట్లాడాలనుకుంటున్నాను, " if call_topic else ""
                    _topic_kn   = f"ನಿಮ್ಮ {call_topic} ಬಗ್ಗೆ ಮಾತನಾಡಬೇಕಿತ್ತು, " if call_topic else ""
                    _topic_ml   = f"നിങ്ങളുടെ {call_topic}-നെ കുറിച്ച് സംസാരിക്കാൻ ആഗ്രഹിക്കുന്നു, " if call_topic else ""
                    _topic_bn   = f"আপনার সাথে {call_topic} নিয়ে কথা বলতে চাই, " if call_topic else ""
                    _topic_gu   = f"તમારી સાથે {call_topic} વિશે વાત કરવી હતી, " if call_topic else ""
                    _topic_mr_m = f"तुमच्याशी {call_topic} विषयी बोलायचं होतं, " if call_topic else ""
                    _topic_mr_f = f"तुमच्याशी {call_topic} विषयी बोलायचं होतं, " if call_topic else ""
                    _topic_pa   = f"ਤੁਹਾਡੇ ਨਾਲ {call_topic} ਬਾਰੇ ਗੱਲ ਕਰਨੀ ਸੀ, " if call_topic else ""
                    _topic_od   = f"ଆପଣଙ୍କ ସହିତ {call_topic} ବିଷୟରେ କଥା କହିବାକୁ ଚାହୁଁଥିଲି, " if call_topic else ""
                    _topic_en   = f"I'd like to talk to you about {call_topic} — " if call_topic else ""

                    if stt_language in ("hi", "multi", "raj"):
                        if gender == "female":
                            greeting = f"नमस्ते! मैं {_co}{agent_name} बात कर रही हूँ। {_topic_hi_f}क्या हम कुछ मिनट बात कर सकते हैं?"
                        else:
                            greeting = f"नमस्ते! मैं {_co}{agent_name} बोल रहा हूँ। {_topic_hi_m}क्या हम कुछ मिनट बात कर सकते हैं?"
                    elif stt_language == "ta":
                        greeting = f"வணக்கம்! நான் {_co}{agent_name} பேசுகிறேன். {_topic_ta}சில நிமிடம் பேசலாமா?"
                    elif stt_language == "te":
                        greeting = f"నమస్కారం! నేను {_co}{agent_name} మాట్లాడుతున్నాను. {_topic_te}కొన్ని నిమిషాలు మాట్లాడవచ్చా?"
                    elif stt_language == "kn":
                        greeting = f"ನಮಸ್ಕಾರ! ನಾನು {_co}{agent_name} ಮಾತನಾಡುತ್ತಿದ್ದೇನೆ. {_topic_kn}ಕೆಲವು ನಿಮಿಷ ಮಾತನಾಡಬಹುದೇ?"
                    elif stt_language == "ml":
                        greeting = f"നമസ്കാരം! ഞാൻ {_co}{agent_name} ആണ് സംസാരിക്കുന്നത്. {_topic_ml}കുറച്ച് മിനിറ്റ് സംസാരിക്കാമോ?"
                    elif stt_language == "bn":
                        greeting = f"নমস্কার! আমি {_co}{agent_name} বলছি। {_topic_bn}কয়েক মিনিট কথা বলতে পারি?"
                    elif stt_language == "gu":
                        greeting = f"નમસ્તે! હું {_co}{agent_name} બોલું છું. {_topic_gu}શું આપણે થોડી મિનિટ વાત કરી શકીએ?"
                    elif stt_language == "mr":
                        if gender == "female":
                            greeting = f"नमस्कार! मी {_co}{agent_name} बोलतेय. {_topic_mr_f}आपण थोडा वेळ बोलू शकतो का?"
                        else:
                            greeting = f"नमस्कार! मी {_co}{agent_name} बोलतोय. {_topic_mr_m}आपण थोडा वेळ बोलू शकतो का?"
                    elif stt_language == "pa":
                        greeting = f"ਸਤ ਸ੍ਰੀ ਅਕਾਲ! ਮੈਂ {_co}{agent_name} ਗੱਲ ਕਰ ਰਹੀ ਹਾਂ। {_topic_pa}ਕੀ ਅਸੀਂ ਕੁਝ ਮਿੰਟ ਗੱਲ ਕਰ ਸਕਦੇ ਹਾਂ?"
                    elif stt_language == "od":
                        greeting = f"ନମସ୍କାର! ମୁଁ {_co}{agent_name} କହୁଛି। {_topic_od}ଆମେ କିଛି ମିନିଟ୍ କଥା କରିପାରିବା କି?"
                    else:  # English fallback
                        greeting = f"Hello! I am {agent_name}{_co_en}. {_topic_en}do you have a couple of minutes?"
                    
                    async def setup_stt():
                        nonlocal stt_started
                        await stt_setup_task
                        stt_started = True
                        logger.info(f"Sarvam STT ready for user audio")

                    asyncio.create_task(setup_stt())

                    # Wait ONLY for TTS setup before playing greeting
                    await tts_setup_task
                    logger.info(f"TTS ready, sending greeting for {call_sid}")

                    asyncio.create_task(broadcast_transcript(call_sid, "agent", greeting))
                    asyncio.create_task(asyncio.to_thread(save_message, call_sid, "agent", greeting, phone_number))
                    
                    # Add to agent's internal memory
                    if agent:
                        # Sarvam requires the first non-system message to be from the user,
                        # so seed a placeholder user turn before the greeting.
                        agent.history.append({"role": "user", "content": "(call started)"})
                        agent.history.append({"role": "assistant", "content": greeting})
                    
                    # Play greeting WITHOUT blocking the receive loop. If we awaited
                    # it here and Sarvam returned no audio, monitor_completion would
                    # spin forever and the loop would never read Twilio's inbound
                    # 'media' frames — the agent would be deaf (audio_sent_total=0).
                    # Firing it as a task keeps inbound audio flowing regardless.
                    async def play_greeting():
                        try:
                            # The greeting bypasses the regular response worker,
                            # so enqueue without waiting and explicitly flush the
                            # first turn to make Sarvam render short opening lines.
                            await tts_manager.stream_tts_audio(
                                ws, stream_sid, greeting, stop_tts_event,
                                wait_for_end=False,
                            )
                            await tts_manager.end_turn()
                        except Exception as e:
                            logger.error(f"Greeting TTS error for {call_sid}: {e}")

                    asyncio.create_task(play_greeting())

                    # Pre-warm the LLM for the first turn (background task)
                    if agent:
                        asyncio.create_task(agent.pre_warm(greeting))

                logger.info(f"Stream started: {call_sid}")
            
            elif event == "media":
                payload = data.get("media", {}).get("payload", "")
                if payload:
                    # We process audio immediately to avoid missing the first second
                    mulaw_data = base64.b64decode(payload)

                    pcm_8k = audioop.ulaw2lin(mulaw_data, 2)
                    pcm_16k, ratecv_state = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, ratecv_state)

                    if stt_manager is not None:
                        await stt_manager.send_pcm16k(pcm_16k)

                    silero_buffer.extend(pcm_16k)
                    
                    if len(silero_buffer) >= silero_vad.chunk_bytes():
                        chunk = bytes(silero_buffer[:silero_vad.chunk_bytes()])
                        del silero_buffer[:silero_vad.chunk_bytes()]
                        
                        try:
                            speech_prob = silero_vad(chunk)
                            
                            if speech_prob >= BARGE_IN_PROB:
                                vad_speech_streak += 1
                                vad_last_voice_ts = time.time()
                                last_spoken_time = time.time()

                                if (
                                    vad_speech_streak >= BARGE_IN_SUSTAIN_FRAMES
                                    and not stop_tts_event.is_set()
                                ):
                                    logger.info(
                                        f"[BARGE-IN] user interrupted TTS "
                                        f"(prob={speech_prob:.2f}, sustained={vad_speech_streak * 32}ms)"
                                    )
                                    stop_tts_event.set()
                                    try:
                                        await ws.send_json({"event": "clear", "streamSid": stream_sid})
                                    except:
                                        pass
                                    if not tts_already_cleared["value"]:
                                        if tts_manager:
                                            await tts_manager.clear_buffer()
                                        tts_already_cleared["value"] = True
                            else:
                                vad_speech_streak = 0
                        except:
                            pass
            
            elif event == "stop":
                should_terminate = True
                break
        
        silence_task.cancel()
        process_task.cancel()
        
    except Exception as e:
        logger.exception(f"Media stream error: {e}")
    finally:
        if stt_manager is not None:
            try:
                await stt_manager.cleanup()
            except Exception:
                pass
        if tts_manager:
            await tts_manager.cleanup()
        if call_sid:
            # Determine WHO ended the call:
            #  - AI / manual set call_hangup_reasons earlier
            #  - otherwise the caller (or carrier) dropped the media stream
            reason = call_hangup_reasons.get(call_sid)
            if not reason:
                reason = "Caller hung up" if not agent_initiated_hangup else "Agent ended the call"
                call_hangup_reasons[call_sid] = reason
            logger.info(f"[HANGUP] call {call_sid} ended — reason: {reason}")

            # Persist hangup reason to conversations table (ResponseType="hangup"),
            # deduped against the /call-status webhook.
            asyncio.create_task(asyncio.to_thread(
                _save_hangup_reason_once, call_sid, reason, phone_number))

            if agent_initiated_hangup:
                try:
                    twilio_client.calls(call_sid).update(status="completed")
                    logger.info(f"Agent initiated hangup for call {call_sid}")
                except Exception as e:
                    logger.error(f"Error terminating call {call_sid}: {e}")
            # Keep the reason until Twilio's terminal callback reads, logs,
            # persists, and removes it; that callback can race this cleanup.


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "5050")))
