from builtins import Exception, ValueError, bytearray, bytes, dict, int, len, locals, open, str
from datetime import datetime, timezone
import inspect
import os
import json
import base64
import asyncio
import logging
from fastapi import Depends, FastAPI, Form, HTTPException, Query, WebSocket, WebSocketDisconnect, Request, File, UploadFile,Header
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import func, null
from Domain import models
from Domain.models import Base
from Repositories.BaseRepository import DocumentBaseRepository
from database import get_dynamic_db, engine_admin,get_db_from_headers, get_db_from_query
# from database import Base, get_db, engine
# from seeding import Seeding
from twilio.twiml.voice_response import VoiceResponse, Connect, Pause
from twilio.rest import Client as TwilioClient
from twilio.http.http_client import TwilioHttpClient
import uvicorn
from sqlalchemy.orm import Session
import Domain.schema as schema
from Websockets.connection import manager
from fastapi.middleware.cors import CORSMiddleware
from twilio.base.exceptions import TwilioRestException
from dotenv import load_dotenv
from botocore.exceptions import ClientError
from twilio.request_validator import RequestValidator
import aioboto3
import time
import shutil
import tempfile
from typing import List, Optional
from conversation_entity import ConversationEntity
import globals
import subprocess
import asyncio
import base64
import audioop
import uuid
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse
from twilio.rest import Client
import numpy as np
from bot.agent import InsuranceClaimAgent
from bot.knowledge_base import KnowledgeBase
from bot.data_manager import ClaimDataManager
from bot.batch_data_fetcher import BatchDataFetcher
from bot.batch_info_ids_fetch import BatchIdsFetcher
from bot.output_field_manager import OutputFieldManager
from bot.extracted_csv_created import CSVExtractionService

import httpx
import aioboto3
import boto3
import io
from fastapi import Request
from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions, DeepgramClientOptions
from pysilero_vad import SileroVoiceActivityDetector
from collections import deque
import re
import asyncio
import os
from bot.deepgram_speech import TTSManager
from batching import router as batch_router
from batching import service
# from batching_service_redis import router as batch_router_redis
# from batching_service_redis import service
# from batching import active_calls, callstatus_map, update_counters_for_call
from bot.app_env_based import router

from token_validation import validate_token_async
from fastapi.security import HTTPBearer
from swagger_schema import configure_swagger
load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

from globals import call_hangup_reasons


# class TokenValidationMiddleware(BaseHTTPMiddleware):
#     async def dispatch(self, request: Request, call_next):
#         path = request.url.path
#         method = request.method
#         origin = request.headers.get("origin")

#         print(f"🔍 Token validation middleware triggered {method} {path} from origin: {origin}")

#         # ✅ Handle CORS preflight
#         if method.upper() == "OPTIONS":
#             response = Response(status_code=200)
#             if origin:
#                 response.headers["Access-Control-Allow-Origin"] = origin
#                 response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS,PUT,DELETE"
#                 response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Email, X-Requested-With, ngrok-skip-browser-warning"
#                 response.headers["Access-Control-Allow-Credentials"] = "true"
#                 response.headers["X-Middleware-Status"] = "OPTIONS_OK"
#             return response

#         # ✅ Allow public routes
#         public_paths = [
#             "/docs", "/openapi.json", "/redoc",
#             "/twilio/call-status",
#             "/twilio/recording-callback",
#             "/outbound"
#         ]
#         if any(path.startswith(p) for p in public_paths):
#             response = await call_next(request)
#             if origin:
#                 response.headers["Access-Control-Allow-Origin"] = origin
#                 response.headers["X-Middleware-Status"] = "PUBLIC_PATH"
#             return response

#         # ✅ Validate JWT token
#         auth_header = request.headers.get("Authorization")
#         if not auth_header or not auth_header.startswith("Bearer "):
#             response = JSONResponse(
#                 status_code=401,
#                 content={"error": ["Missing or invalid Authorization header"]}
#             )
#             if origin:
#                 response.headers["Access-Control-Allow-Origin"] = origin
#                 response.headers["Access-Control-Allow-Credentials"] = "true"
#                 response.headers["X-Middleware-Status"] = "MISSING_TOKEN"
#             return response

#         token = auth_header.replace("Bearer ", "").strip()
#         validation_result = await validate_token_async(token)

#         if not validation_result:
#             response = JSONResponse(
#                 status_code=401,
#                 content={"error": ["Invalid or expired token"]}
#             )
#             if origin:
#                 response.headers["Access-Control-Allow-Origin"] = origin
#                 response.headers["Access-Control-Allow-Credentials"] = "true"
#                 response.headers["X-Middleware-Status"] = "INVALID_TOKEN"
#             return response

#         # ✅ Proceed to next request
#         response = await call_next(request)
#         if origin:
#             response.headers["Access-Control-Allow-Origin"] = origin
#             response.headers["Access-Control-Allow-Credentials"] = "true"
#             response.headers["X-Middleware-Status"] = "AUTHORIZED"
#         return response

class TokenValidationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method
        origin = request.headers.get("origin")

        print(f"Token validation middleware triggered {method} {path} from origin: {origin}")

        # ✅ Handle CORS preflight (OPTIONS)
        if method.upper() == "OPTIONS":
            response = Response(status_code=200)
            if origin:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS,PUT,DELETE"
                response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Email, X-Requested-With, ngrok-skip-browser-warning"
                response.headers["Access-Control-Allow-Credentials"] = "true"
                response.headers["X-Middleware-Status"] = "OPTIONS_OK"
            return response

        # ✅ Allow docs and openapi
        public_paths = [
            "/docs", "/openapi.json", "/redoc",
            "/twilio/call-status",
            "/twilio/recording-callback",
            "/outbound"
        ]
        if any(path.startswith(p) for p in public_paths):
            response = await call_next(request)
            if origin:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["X-Middleware-Status"] = "PUBLIC_PATH"
            return response

        # ✅ Validate token
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            response = JSONResponse(
                status_code=401,
                content={"error": ["Missing or invalid Authorization header"]}
            )
            if origin:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Credentials"] = "true"
                response.headers["X-Middleware-Status"] = "MISSING_TOKEN"
            return response

        token = auth_header.replace("Bearer ", "").strip()
        validation_result = await validate_token_async(token)

        if not validation_result:
            response = JSONResponse(
                status_code=401,
                content={"error": ["Invalid or expired token"]}
            )
            if origin:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Credentials"] = "true"
                response.headers["X-Middleware-Status"] = "INVALID_TOKEN"
            return response

        response = await call_next(request)
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["X-Middleware-Status"] = "AUTHORIZED"
        return response

 # We will resample to 16 kHz before sending to Deepgram
os.environ["AWS_ACCESS_KEY_ID"] = os.getenv("AWS_ACCESS_KEY_ID")
os.environ["AWS_SECRET_ACCESS_KEY"] = os.getenv("AWS_SECRET_ACCESS_KEY")
os.environ["AWS_SESSION_TOKEN"] = os.getenv("AWS_SESSION_TOKEN", "")
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
RATE = os.getenv("AUDIO_RATE", "8000")

async def save_message_async(email: str, conversations_repo: DocumentBaseRepository, callsid: str, message: str, type: str):
    """
    Background task to save messages. Creates its own short-lived DB session
    to avoid race conditions with the main WebSocket loop or closed sessions.
    """
    db = None
    try:
        db = get_dynamic_db(email)
        conversation_entry = {
            "CallSid": callsid,
            "ResponseType": type,
            "ResponseText": message,
            "CreatedAt": datetime.now(timezone.utc)
        }
        await conversations_repo.add(db, document_data=conversation_entry)
    except Exception as e:
        logger.error(f"Error saving message to DB (Background): {e}")
    finally:
        if db:
            db.close()

# Keep original for synchronous contexts if needed, but prefer async version for loops
async def save_message(db: Session, conversations_repo: DocumentBaseRepository, callsid: str, message: str, type: str):
    # Backward compatibility or if you explicitly want to use the shared session
    try:
        conversation_entry = {
            "CallSid": callsid,
            "ResponseType": type,
            "ResponseText": message,
            "CreatedAt": datetime.now(timezone.utc)
        }
        await conversations_repo.add(db, document_data=conversation_entry)
    except Exception as e:
        logger.error(f"Error saving message to DB: {e}")



app = FastAPI()
bearer_scheme = HTTPBearer()
configure_swagger(
    app,
    title="VoiceBot API",
    version="2.0.0",
    description="Twilio Voicebot APIs with Bearer Token Authentication"
)

# raw_origins = os.getenv("origin")
# origins = json.loads(raw_origins) if raw_origins else []
origin=[
    "http://localhost:4300",
    "http://127.0.0.1:4200",
    "https://ec2-34-234-56-33.compute-1.amazonaws.com:4300",
    "https://ec2-34-234-56-33.compute-1.amazonaws.com:4200",
    "https://voicedev.sghdev.cloud"
    ]

# app.add_middleware(TokenValidationMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origin,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Email",
        "X-Requested-With",
        "ngrok-skip-browser-warning"   
    ],
)
# app.add_middleware(TokenValidationMiddleware)

app.include_router(batch_router, tags=["Batches"])
# app.include_router(batch_router_redis, tags=["Batches"])
app.include_router(router)


# Create a custom HTTP client with a 30s timeout
custom_http_client = TwilioHttpClient()
custom_http_client.session.timeout = 30.0

twilio_client = TwilioClient(
    os.getenv("TWILIO_ACCOUNT_SID"), 
    os.getenv("TWILIO_AUTH_TOKEN"),
    http_client=custom_http_client
)
claim_data_manager = ClaimDataManager(os.getenv("INPUT_FILE"))
knowledge_base = KnowledgeBase(os.getenv("KNOWLEDGE_FILE"))
output_manager = OutputFieldManager(os.getenv("OUTPUT_FILE"))
dg_client = DeepgramClient(DEEPGRAM_API_KEY,config=DeepgramClientOptions(options={"keepalive": "true"}))
# Base.metadata.create_all(bind=engine)
# Base.metadata.create_all(bind=engine_admin, checkfirst=True)
# Base.metadata.create_all(bind=engine_support)
# Seeding.create_hardcoded_users()

# Create temp directory
TEMP_DIR = os.path.join(os.getcwd(), "temp")
os.makedirs(TEMP_DIR, exist_ok=True)

def validate_phonenumber(number: str):
    
    try:
        logger.info(f"Validating phone number: {number}")
        # parsed = phonenumbers.parse(number, None)
        # if not phonenumbers.is_possible_number(parsed) or not phonenumbers.is_valid_number(parsed):
        #     raise ValueError("Invalid phone number format")
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid phone number format. Use international format, e.g., +14155552671")

@app.get("/", response_class=HTMLResponse, summary="Root Index", description="**Output:** Returns a simple HTML welcome message.<br>**Input:** None")
async def index():
    return "<h1>Twilio Voicebot with Amazon Polly and Transcribe</h1>"

@app.post("/login", response_model=schema.LoginSuccessResponse, summary="User Login", description="**Input:** JSON with `email` and `password`.<br>**Output:** JSON with success message and email.<br><br>Authenticates the user against the database.")
async def login(request: schema.LoginRequest):
    if request.email is None:
        raise HTTPException(status_code=400, detail="Email is required") 
    elif request.password is None:
        raise HTTPException(status_code=400, detail="Password is required")
    users_repo = DocumentBaseRepository(models.User)
    db = None
    db: Session = get_dynamic_db(request.email)
    try:
        user = await users_repo.get(db, filter_predicate=models.User.email == request.email)
        if not user or len(user) == 0:
            raise HTTPException(status_code=400, detail="User not found.")
        if user[0].password != request.password:
            raise HTTPException(status_code=400, detail="Email or password is incorrect.")

        return {
            "msg": "Login Success!",
            "email": request.email  
        }
    finally:
       if db:
            db.close()

@app.api_route("/outbound", methods=["GET", "POST"], response_class=Response, summary="Twilio TwiML Stream", description="**Input:** Twilio webhook payload + `user-email` query/header.<br>**Output:** TwiML XML to connect the call to the Media Stream.<br><br>Used internally by Twilio Voice.")
async def outbound_twiml(request: Request):
    email = request.query_params.get("user-email") or request.headers.get("user-email")

    if not email:
        raise HTTPException(status_code=400, detail="Missing 'user-email' in headers")
    
    # Dynamic stream URL based on email
    
    ngrok_url = os.getenv("NGURL")
    if not ngrok_url:
         raise HTTPException(status_code=500, detail="NGURL environment variable not set")
    stream_url = f"wss://{ngrok_url}/media-stream"
    
    print(f"Stream URL for {email}: {stream_url}")
    response = VoiceResponse()
    response.pause(length=0.2)  # Prevent silence timeout
    connect = Connect()
    
    # Include email directly in the WebSocket URL path
    stream_url_with_email = f"{stream_url}/{email}"
    connect.stream(url=stream_url_with_email, name="voice_stream")
    
    response.append(connect)
    logger.info("TwiML generated for %s: %s", email, str(response))
    return Response(content=str(response), media_type="application/xml")


@app.post("/make-outbound-call", response_model=schema.OutboundCallInitiatedResponse, summary="Initiate Outbound Call", description="**Input:**<br>- Query: `to_number` (Target Phone)<br>- File: `file` (.txt script/questions)<br>- Header: `user-email`<br><br>**Output:** JSON with `status`, `callsid`, `phonenumber`.<br><br>Triggers an outbound call via Twilio.")
async def make_outbound_call(request: Request, to_number: str = Query(...), file: UploadFile = File(...)):
    try:
        email = request.headers.get("user-email") or "admin@gmail.com"
        if not email:
            raise HTTPException(status_code=400, detail="Missing 'user-email' in headers")
        
        validate_phonenumber(to_number)

        base_url = os.getenv("NGROK_URL")
        print(f"NGROK_URL: {base_url}")
        if not base_url:
            raise HTTPException(status_code=500, detail="NGROK_URL not set in environment")

        logger.info("------------------------Base URL for Twilio: %s", base_url)
        if not file.filename.endswith('.txt'):
            raise HTTPException(status_code=400, detail="Only .txt files are allowed")

        # callsid_temp = f"temp_{int(time.time())}"
        # temp_file_path = os.path.join(TEMP_DIR, f"{callsid_temp}.txt")
        # with open(temp_file_path, "wb") as temp_file:
        #     shutil.copyfileobj(file.file, temp_file)

        # QUESTIONS = []
        # try:
        #     with open(temp_file_path, "r", encoding="utf-8") as f:
        #         QUESTIONS = [line.strip() for line in f if line.strip()]
        #     if not QUESTIONS:
        #         raise ValueError("Uploaded file is empty")
        #     logger.info(f"Loaded {len(QUESTIONS)} questions from uploaded file")
        # except Exception as e:
        #     logger.error(f"Failed to read uploaded file: {e}")
        #     os.remove(temp_file_path)
        #     raise HTTPException(status_code=400, detail="Failed to read uploaded file")

        call = twilio_client.calls.create(
            to=to_number,
            record=True,
            from_=os.getenv("TWILIO_CALLER_ID"),
            url=f"{base_url}/outbound?user-email={email}",
            status_callback=f"{base_url}/twilio/call-status?user-email={email}",
            recording_status_callback=f"{base_url}/twilio/recording-callback?user-email={email}",
            status_callback_event=[
                "initiated", "ringing", "answered", "completed",
                "busy", "canceled", "failed", "no-answer"
            ]
        )
        
        # Rename temp file with actual call SID
        # final_file_path = os.path.join(TEMP_DIR, f"{call.sid}.txt")
        # os.rename(temp_file_path, final_file_path)
        
        logger.info("Call initiated. SID: %s, To: %s", call.sid, to_number)
        return {
            "status": "success",
            "callsid": call.sid,
            "phonenumber": to_number
        }
    except TwilioRestException as tre:
        logger.error("Twilio Error: %s", tre)
        # if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
        #     os.remove(temp_file_path)
        raise HTTPException(
            status_code=tre.status or 400,
            detail={
                "error": "Twilio API Error",
                "status_code": tre.status,
                "error_code": tre.code,
                "message": tre.msg,
                "to_number": to_number
            }
        )
    except Exception as e:
        logger.error("General Call error: %s", str(e))
        # if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
        #     os.remove(temp_file_path)
        raise HTTPException(
            status_code=400,
            detail=f"Call failed: {e}"
        )


@app.delete("/delete-call-logs", response_model=schema.GenericDataResponse, summary="Delete All Logs", description="**Input:** `user-email` header (for DB selection).<br>**Output:** Success message.<br><br>⚠️ **DESTRUCTIVE:** Deletes ALL call logs and statuses for the user.")
async def delete_call_logs(db: Session = Depends(get_db_from_headers)):
    try:
        # Create repositories locally
        callstatus_repo_local = DocumentBaseRepository(models.CallStatus)
        call_logs_repo_local = DocumentBaseRepository(models.CallLogs)
        
        await callstatus_repo_local.delete_all(db)
        await call_logs_repo_local.delete_all(db)
        return {"data": "Deleted Successfully!"}
    except Exception as e:
        logger.error("Deletion error: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e))
    # finally:
    #     db.close()


def decode_twilio_audio(payload_b64: str) -> bytes:
    mulaw_data = base64.b64decode(payload_b64)
    return audioop.ulaw2lin(mulaw_data, 2)


class TranscriptCollector:
    """
    Helper that collects partial snippets until we manually finalize
    after 2 seconds of no new speech. Then we join all parts into one sentence.
    """
    def __init__(self):
        self.reset()

    def reset(self):
        self.transcript_parts = []

    def add_part(self, part: str):
        self.transcript_parts.append(part)

    def get_full_transcript(self) -> str:
        return " ".join(self.transcript_parts).strip()

# 1) DTMF frequency map


class DtmfToneGenerator:
    def __init__(self):
        self.DTMF_FREQS = {
            "1": (697, 1209), "2": (697, 1336), "3": (697, 1477),
            "4": (770, 1209), "5": (770, 1336), "6": (770, 1477),
            "7": (852, 1209), "8": (852, 1336), "9": (852, 1477),
            "0": (941, 1336), "*": (941, 1209), "#": (941, 1477),
        }
    # 2) Helper to synthesize a DTMF tone 
    def generate_dtmf_tone(self, digit: str, duration: float = 0.3, fs: int = 8000) -> bytes:
        """Generate a DTMF tone (300 ms @ 8000 Hz, 16-bit PCM) with envelope"""
        if digit not in self.DTMF_FREQS:
            raise ValueError(f"Unsupported DTMF digit: {digit}")
        f1, f2 = self.DTMF_FREQS[digit]
        t = np.linspace(0, duration, int(fs * duration), endpoint=False)
        # sum two sine waves
        waveform = np.sin(2 * np.pi * f1 * t) + np.sin(2 * np.pi * f2 * t)
       
        # Add a 5ms fade-in and fade-out envelope to prevent clicks
        fade_duration = 0.005
        fade_samples = int(fs * fade_duration)
        if fade_samples > 0 and len(waveform) > 2 * fade_samples:
            env = np.ones(len(waveform))
            env[:fade_samples] = np.linspace(0, 1, fade_samples)
            env[-fade_samples:] = np.linspace(1, 0, fade_samples)
            waveform *= env
 
        # Normalize to 50% volume to prevent clipping/distortion in mu-law conversion
        pcm16 = (waveform / np.max(np.abs(waveform)) * 32767 * 0.5).astype(np.int16).tobytes()
        return pcm16


def split_into_sentences(text: str) -> list[str]:
    """
    Split `text` on sentence-ending punctuation (.?!), keeping the punctuation,
    and trimming any extra whitespace.
    """
    # This regex looks for a sentence boundary: one of . ? !, followed by whitespace
    sentences = re.split(r'(?<=[\.?!])\s+', text.strip())
    # Filter out any empty strings
    return [s for s in sentences if s]

def chunk_by_sentence(text):
    # Split text at sentence boundaries (periods, question marks, exclamation points)
    # while preserving the punctuation
    sentences = re.split(r'(?<=[.!?])\s+', text)
    
    # Remove any empty chunks
    return [sentence for sentence in sentences if sentence]


# Add this BEFORE @app.websocket("/media-stream/{email}")

class SpeakerContextDetector:
    """Detects whether the speaker is likely an IVR system or human"""

    def __init__(self):
        self.current_state = "ivr"  # Default assumption at start of call
        self.confidence_history = []
        self.max_history = 5


        # IVR indicators
        self.ivr_keywords = [
            "press", "option", "menu", "dial", "enter", "say",
            "representative", "department", "please hold",
            "for english", "para español", "language",
            "thank you for calling", "thanks for calling", "your call is important",
            "pound", "star", "hash", "operator", "leave a message", "voicemail",
            "our system", "automated system",
            "press star", "press pound", "press hash",
            "to continue",
            "for privacy",
            "say or enter",
            "to verify your identity",
            "please say or enter"
        ]

    def analyze_speech(self, transcript: str, speech_duration: float = None) -> dict:
        """Analyze transcript to determine if speaker is IVR or human"""
        transcript_lower = (transcript or "").lower().strip()
        words = transcript_lower.split()
        words_len = len(words)
        speech_duration = float(speech_duration or 0.0)

        # count IVR keywords
        ivr_keyword_count = sum(1 for keyword in self.ivr_keywords if keyword in transcript_lower)

        # human signals (no fillers needed)
        has_pronoun = any(w in {"i", "me", "my", "we", "our", "you", "your", "so", "tell me","can you"} for w in words)
        has_casual = any(p in transcript_lower for p in ["okay", "ok", "alright", "sure", "got it", "yeah", "right", "hello", "hi", "speaking", "help you", "may i"])
        has_question = "?" in transcript_lower or any(
            transcript_lower.startswith(q)
            for q in ["what", "where", "when", "why", "how", "can", "could", "would"]
        )

        # scoring
        ivr_score = 0
        human_score = 0

        if ivr_keyword_count >= 2:
            ivr_score += 6
        elif ivr_keyword_count == 1:
            ivr_score += 3

        # if longest_digits >= 6:
        #     ivr_score += 2

        # long continuous structured speech leans IVR
        if words_len > 30 and "." in transcript_lower:
            ivr_score += 1

        # human signs
        if has_pronoun:
            human_score += 2
        if has_casual:
            human_score += 2
        if has_question and ivr_keyword_count == 0 and words_len < 15:
            human_score += 1
        if words_len <= 3 and ivr_keyword_count == 0:
            human_score += 2

        # duration bias
        if speech_duration >= 3.0:
            if ivr_keyword_count > 0 and not has_pronoun:
                ivr_score += 2
            elif has_pronoun or has_casual:
                human_score += 1

        # State machine with hysteresis (momentum)
        # If we are already in 'human' mode, we need strong IVR signals to switch back
        if self.current_state == "human":
            human_score += 2

        if self.current_state == "ivr":
            ivr_score += 1

        # Raw evaluation
        if ivr_score - human_score >= 2:
            raw_eval = "ivr"
        elif human_score - ivr_score >= 1:
            raw_eval = "human"
        else:
            raw_eval = "unknown"

        # Update historical momentum
        self.confidence_history.append(raw_eval)
        if len(self.confidence_history) > self.max_history:
            self.confidence_history.pop(0)

        # Transition Logic
        if self.current_state == "ivr":
            # Need a definitive human response, OR multiple recent human-leaning responses
            if raw_eval == "human" or self.confidence_history.count("human") >= 3:
                self.current_state = "human"
                
        elif self.current_state == "human":
            # It's rare to go back to IVR unless transfer happens.
            # Require strong/consistent IVR signals to switch back.
            if raw_eval == "ivr" and ivr_keyword_count >= 1 and self.confidence_history.count("ivr") >= 2:
                self.current_state = "ivr"

        return {
            "speaker_type": self.current_state,
            "confidence": abs(ivr_score - human_score),
            "ivr_score": ivr_score,
            "human_score": human_score,
        }

    def get_pause_thresholds(self, speaker_type: str) -> dict:
        """Return pause thresholds based on speaker type"""
        if speaker_type == "ivr":
            return {
                "STRONG_PAUSE": 1.0,      # ← Increased from 0.8
                "WEAK_PAUSE": 1.2,        # ← Increased from 1.0
                "NO_PUNCT_PAUSE": 1.5,    # ← Increased from 1.2
                "COMMIT_GRACE": 0.1,
            }


        else:
            return {
                "STRONG_PAUSE": 0.6,      # ← Increased from 0.8
                "WEAK_PAUSE": 0.8,        # ← Increased from 1.0
                "NO_PUNCT_PAUSE": 1.0,    # ← Increased from 1.2
                "COMMIT_GRACE": 0.1,
            }

    def reset(self):
        """Reset detection state"""
        self.current_state = "ivr"
        self.confidence_history.clear()


@app.websocket("/media-stream/{email}")
async def media_stream(ws: WebSocket, email: str):
    import re, inspect, time  # ← added time

    await ws.accept()
    if not email:
        logger.error("Missing email in path parameter")
        await ws.close(code=1008)
        return

    db = None
    try:
        db = get_dynamic_db(email)
        conversations_repo = DocumentBaseRepository(models.Conversation)
    except Exception as e:
        logger.error(f"Repository initialization error: {e}")
        await ws.close(code=1011)
        return

    # conversation_array = []
    transcript_queue = asyncio.Queue()
    stop_tts_event = asyncio.Event()
    silero_vad = SileroVoiceActivityDetector()
    silero_buffer = bytearray()
    ratecv_state = None
    tts_already_cleared = {"value": False}
    # vad_prebuffer = deque(maxlen=2)
    stream_sid = None
    callsid = None
    waiting_for_response = False
    deepgram_ready = False
    temp_file_path = None
    should_terminate = False
    tts_manager = None
    # ==== NEW: VAD-gated merging state ====
    vad_last_voice_ts = time.time()    # when VAD last detected voice
    pending_merge_text = ""            # held fragment while speech continues
    current_speaker_type = {"value": "ivr"}  # updated by pause target
    nudge_count = 0                    # Track sequential silence nudges

    # ==== NEW: Task-Based Turn Control ====
    active_turn_task: asyncio.Task | None = None
    last_processed_ts: float = 0
    last_processed_text: str = ""
    interrupted_transcript_buffer: str = ""  # sticky buffer for cancelled turns
    last_interruption_ts: float = 0


    # ==== punctuation-aware pause thresholds ====
    STRONG_PUNCT_RE = re.compile(r'[.!?]+$')
    WEAK_PUNCT_RE = re.compile(r'[,;:]$')

    # Initialize speaker detector
    speaker_detector = SpeakerContextDetector()
    dtmf_tone_generator= DtmfToneGenerator()
    csv_service = CSVExtractionService()
    # Start with human defaults (more responsive)
    current_thresholds = speaker_detector.get_pause_thresholds("human")
    STRONG_PAUSE = current_thresholds['STRONG_PAUSE']
    WEAK_PAUSE = current_thresholds['WEAK_PAUSE']
    NO_PUNCT_PAUSE = current_thresholds['NO_PUNCT_PAUSE']
    COMMIT_GRACE = current_thresholds['COMMIT_GRACE']
    # --- Always-insert helper (no duplicate precheck, no update) ---
    from sqlalchemy.exc import IntegrityError
    _DB_MANAGED = {"Id", "CreatedBy", "CreatedAt", "UpdatedBy", "UpdatedAt", "IsDeleted"}

    def _strip_db_managed(d: dict) -> dict:
        return {k: v for k, v in (d or {}).items() if k not in _DB_MANAGED}

    async def insert_batchinfo_output_force(db, payload: dict, created_by: str = "system") -> str:
        """
        Always INSERT a new BatchInfoOutput row.
        Optimized to rely on DB constraints rather than pre-fetching.
        """
        if not payload:
            return "skipped-empty"

        # Extract the three critical foreign keys
        batch_info_id = payload.get("BatchInfoId")
        batch_execution_id = payload.get("BatchExecutionId")
        call_number_execution_id = payload.get("CallNumberExecutionId")

        # Basic ID presence check (no DB hit)
        if not batch_info_id:
            logger.error("BatchInfoId is missing or None")
            return "failed-missing-batch-info-id"
        if not batch_execution_id:
            logger.error("BatchExecutionId is missing or None")
            return "failed-missing-batch-execution-id"
        if not call_number_execution_id:
            logger.error("CallNumberExecutionId is missing or None")
            return "failed-missing-call-number-execution-id"

        # Proceed directly with insert
        data = _strip_db_managed(dict(payload))
        data.setdefault("CreatedBy", created_by)
        data.setdefault("IsDeleted", 0)

        row = models.BatchInfoOutput(**data)
        db.add(row)
        try:
            db.commit()
            logger.info(f"Successfully inserted BatchInfoOutput")
            return "inserted"
        except IntegrityError as ie:
            db.rollback()
            logger.error(f"BatchInfoOutput insert failed (FK violation or other integrity error): {ie}")
            return "failed-integrity"
        except Exception as e:
            db.rollback()
            logger.error(f"BatchInfoOutput insert failed (unexpected): {e}")
            return "failed-unexpected"

    def _analyze_punctuation(text: str) -> tuple[bool, bool, float]:
        text = text.strip()
        if not text:
            return False, False, NO_PUNCT_PAUSE
        has_strong = bool(STRONG_PUNCT_RE.search(text))
        has_weak = bool(WEAK_PUNCT_RE.search(text))
        if has_strong:
            return True, False, STRONG_PAUSE
        elif has_weak:
            return False, True, WEAK_PAUSE
        else:
            return False, False, NO_PUNCT_PAUSE

    def _speaking_recently(window: float = 0.25) -> bool:
        """True if VAD saw speech within the last `window` seconds."""
        return (time.time() - vad_last_voice_ts) <= window

    def _current_pause_target(buffer_sentence: list[str]) -> float:
        """
        Dynamic IVR/human thresholds with punctuation:
        - Enforce minimums: human >= 0.7s, IVR >= 2.0s
        """
        nonlocal STRONG_PAUSE, WEAK_PAUSE, NO_PUNCT_PAUSE, COMMIT_GRACE

        if not buffer_sentence:
            return NO_PUNCT_PAUSE

        joined = " ".join(buffer_sentence).strip()

        # detect speaker type
        context = speaker_detector.analyze_speech(joined)
        current_speaker_type["value"] = context["speaker_type"]

        thresholds = speaker_detector.get_pause_thresholds(context['speaker_type'])
        STRONG_PAUSE = thresholds['STRONG_PAUSE']
        WEAK_PAUSE = thresholds['WEAK_PAUSE']
        NO_PUNCT_PAUSE = thresholds['NO_PUNCT_PAUSE']
        COMMIT_GRACE = thresholds['COMMIT_GRACE']

        # logger.info(f"🔍 Speaker: {context['speaker_type']} | IVR:{context['ivr_score']} Human:{context['human_score']}")
        # logger.info(f"📊 Thresholds: STRONG={STRONG_PAUSE}s WEAK={WEAK_PAUSE}s NONE={NO_PUNCT_PAUSE}s | Text: '{joined[:80]}'")

        # punctuation-driven pause
        has_strong, has_weak, pause_time = _analyze_punctuation(joined)

        # 👉 Enforce absolute minimums (human 1.0s, IVR 1.5s)
        if context['speaker_type'] == 'ivr':
            pause_time = max(pause_time, 0.8)
        else:
            pause_time = max(pause_time, 0.6)

        return pause_time

    # Deepgram-driven timing + buffer
    last_spoken_time = None
    buffer_sentence = []

    # Pending commit task for grace finalize
    pending_commit_task = None

    # Per-call agent instance
    agent = InsuranceClaimAgent(
        knowledge_base=knowledge_base,
        output_manager=OutputFieldManager(os.getenv("OUTPUT_FILE"))
    )

    # ==== Deepgram Async Live Transcription Setup ====
    dg_connection = dg_client.listen.asynclive.v("1")

    async def _cancel_pending_commit():
        nonlocal pending_commit_task
        if pending_commit_task and not pending_commit_task.done():
            try:
                pending_commit_task.cancel()
                await asyncio.sleep(0)
            except Exception:
                pass
        pending_commit_task = None

    async def on_transcript(_, result, **kwargs):
        nonlocal last_spoken_time, buffer_sentence, pending_commit_task

        # Extract text safely
        snippet = getattr(result.channel.alternatives[0], "transcript", "") or ""
        if not snippet:
            return

        # NEW speech → cancel any scheduled finalize (grace commit)
        if pending_commit_task and not pending_commit_task.done():
            try:
                pending_commit_task.cancel()
            except Exception:
                pass
            pending_commit_task = None

        # Deepgram "final" marker (optional nudge)
        is_final = bool(getattr(result, "is_final", False))

        # Decide whether to MERGE into current utterance or start NEW one
        if buffer_sentence and last_spoken_time:
            try:
                elapsed = time.time() - last_spoken_time
                required_pause = _current_pause_target(buffer_sentence) if buffer_sentence else 1.3
            except Exception:
                elapsed, required_pause = 0.0, 1.3

            if elapsed < required_pause:
                prev = " ".join(buffer_sentence).strip()
                merged = (prev + " " + snippet).strip()
                buffer_sentence = [merged]
                logger.info(f"🔄 MERGED: '{merged[:80]}...' | Timer RESET")
            else:
                buffer_sentence = [snippet]
                logger.info(f"🎤 NEW SPEECH: '{snippet[:60]}...' | Timer RESET")
        else:
            buffer_sentence = [snippet]
            logger.info(f"🎤 SPEAKING: '{snippet[:60]}...' | Timer RESET")

        # Reset pause timer (we just got speech)
        last_spoken_time = time.time()

        # --- Nudges removed to prevent split-turns. We now use 'Honest Timing' ---
        pass

    async def on_error(_, error, **kwargs):
        logger.error(f"Deepgram error: {error}")

    dg_connection.on(LiveTranscriptionEvents.Transcript, on_transcript)
    dg_connection.on(LiveTranscriptionEvents.Error, on_error)

    options = LiveOptions(
        model="nova-3",
        punctuate=True,
        language="multi",
        encoding="mulaw",
        channels=1,
        smart_format=True,
        numerals=True,
        sample_rate=RATE,   # 8000
        vad_events=True,
    )

    # ==== Silence Monitor Task (finalize with dynamic target + grace + VAD gate) ====
    async def monitor_silence():
        nonlocal last_spoken_time, buffer_sentence, should_terminate, pending_commit_task, pending_merge_text, last_interruption_ts


        async def _commit_now(force: bool = False):
            """Finalize if safe; otherwise hold partial and combine with next finalize."""
            nonlocal last_spoken_time, buffer_sentence, pending_merge_text

            full_sentence = " ".join(buffer_sentence).strip()
            full_sentence = re.sub(r"\s+", " ", full_sentence)

            # reset local timers/buffers for next utterance window
            buffer_sentence.clear()
            last_spoken_time = None
            tts_already_cleared["value"] = False

            if not full_sentence:
                return

            # If VAD still sees speech, hold the fragment (do not emit yet) unless forced
            st = current_speaker_type.get("value", "human")
            vad_gate = 0.40 if st == "ivr" else 0.35
            if not force and _speaking_recently(vad_gate):
                pending_merge_text = (f"{pending_merge_text} {full_sentence}".strip()
                                      if pending_merge_text else full_sentence)
                logger.info(f"⏸️ Held finalize (VAD active). Accumulated: '{pending_merge_text[:80]}...'")
                return

            # speech quiet → safe to emit; combine if we were holding a fragment
            if pending_merge_text:
                combined = f"{pending_merge_text} {full_sentence}".strip()
                combined = re.sub(r"\s+", " ", combined)
                await transcript_queue.put(combined)
                logger.info(f"✅ FINALIZED (combined): '{combined}'")
                pending_merge_text = ""
            else:
                await transcript_queue.put(full_sentence)
                logger.info(f"✅ FINALIZED: '{full_sentence}'")


        # async def _schedule_commit_after_grace(grace: float):
        #     try:
        #         await asyncio.sleep(grace)
        #         await _commit_now()
        #     except asyncio.CancelledError:
        #         return

        try:
            while True:
                if should_terminate:
                    return

                if last_spoken_time is not None and buffer_sentence:
                    now = time.time()
                    silence_duration = now - last_spoken_time
                    required_pause = _current_pause_target(buffer_sentence)

                    # only schedule commit if pause reached AND VAD has been quiet
                    st = current_speaker_type.get("value", "human")
                    vad_quiet_needed = 0.30 if st == "ivr" else 0.20
                    vad_quiet = not _speaking_recently(vad_quiet_needed)

                    # DEADLOCK BYPASS: If silence > 5s, force commit anyway (noise gate bypass)
                    force_limit = 5.0 if st == "ivr" else 3.5
                    force_needed = silence_duration >= force_limit

                    if silence_duration >= required_pause and (vad_quiet or force_needed):
                        # Direct commit
                        await _commit_now(force=force_needed)
                        # Reset pending task tracker
                        pending_commit_task = None
                
                # --- 💓 INTERRUPTION RECOVERY HEARTBEAT ---
                elif interrupted_transcript_buffer and not (active_turn_task and not active_turn_task.done()):
                    now = time.time()
                    # If we were interrupted by noise, and it's been quiet for 2.0s
                    # and no new speech has started for 2.5s since the interruption
                    if (now - vad_last_voice_ts > 2.0) and (now - last_interruption_ts > 2.5):
                        logger.info(f"[{callsid}] 💓 Interruption recovery triggered. Resuming stale context.")
                        await transcript_queue.put("[RESUME_INTERRUPTED]")
                        # last_interruption_ts reset to prevent double-trigger
                        last_interruption_ts = now + 1000 # Wait a long time for next one

                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            return

    async def _handle_turn_execution(transcript: str, merged_transcript: str):
        """
        Single turn of AI processing.
        'transcript' is the raw new fragment (for UI/DB).
        'merged_transcript' is the full context (for AI reasoning).
        """
        nonlocal tts_manager, should_terminate, waiting_for_response, last_processed_text, last_processed_ts, interrupted_transcript_buffer

        try:
            is_system_event = transcript.startswith("[SYSTEM EVENT:")
            logger.info(f"[{callsid}] 🤖 Starting turn: raw='{transcript[:50]}...', merged='{merged_transcript[:50]}...'")
            
            if not is_system_event:
                # 📡 BROADCAST & SAVE raw fragment immediately (what the user actually said right now)
                asyncio.create_task(save_message_async(email, conversations_repo, callsid, transcript, "answer"))
                asyncio.create_task(manager.broadcast_transcript_to_call(callsid, {"type": "user", "text": transcript}))

            # --- Hold/Pause Logic ---
            hold_phrases = ["please hold on call", "hold on please", "hang on", "give me a second", "one second", "hold for a minute", "just a moment"]
            resume_phrases = ["thanks for holding", "thank you for holding", "thank you for waiting", "thanks for waiting", "appreciate your patience", "thank you for your patience"]
            t_lower = merged_transcript.lower() # Use merged for logic

            if any(phrase in t_lower for phrase in hold_phrases) and not any(r in t_lower for r in resume_phrases):
                logger.info("User requested hold/pause, skipping agent response.")
                chunked_wait = "okey sure."
                if not stop_tts_event.is_set(): stop_tts_event.set()
                stop_tts_event.clear()
                tts_already_cleared["value"] = False
                await tts_manager.stream_tts_audio(ws, stream_sid, chunked_wait, stop_tts_event)

                asyncio.create_task(save_message_async(email, conversations_repo, callsid, chunked_wait, "question"))
                asyncio.create_task(manager.broadcast_transcript_to_call(callsid, {"type": "agent", "text": chunked_wait}))
                agent.conversation_history.append({"role": "assistant", "content": chunked_wait})
                waiting_for_response = True
                return

            # --- Query the AI Agent ---
            end_call_triggered = False 

            # 🧠 USE MERGED TRANSCRIPT for the AI reasoning
            async for chunk in agent.query_stream(merged_transcript, agent.claim_data):
                if chunk["type"] == "tts":
                    if not stop_tts_event.is_set(): stop_tts_event.set()
                    stop_tts_event.clear()
                    tts_already_cleared["value"] = False
                    await tts_manager.stream_tts_audio(ws, stream_sid, chunk["text"], stop_tts_event)
                    
                    asyncio.create_task(manager.broadcast_transcript_to_call(callsid, {"type": "agent", "text": chunk["text"]}))
                    asyncio.create_task(save_message_async(email, conversations_repo, callsid, chunk["text"], "question"))
                    waiting_for_response = True

                elif chunk["type"] == "dtmf":
                    logger.info(f"[{callsid}] AI selected DTMF: {chunk['text']}")
                    digits_to_send = list(chunk["text"])
                    if digits_to_send: await asyncio.sleep(0.4)
                    for i, digit in enumerate(digits_to_send):
                        pcm16 = dtmf_tone_generator.generate_dtmf_tone(digit)
                        mulaw_bytes = audioop.lin2ulaw(pcm16, 2)
                        payload = base64.b64encode(mulaw_bytes).decode("ascii")
                        if i >= 1: await asyncio.sleep(0.8)
                        await ws.send_json({"event": "media", "streamSid": stream_sid, "media": {"payload": payload}})
                    
                    asyncio.create_task(manager.broadcast_transcript_to_call(callsid, {"type": "agent", "text": f"Sent DTMF {chunk['text']}"}))
                    asyncio.create_task(save_message_async(email, conversations_repo, callsid, f"Sent DTMF {chunk['text']}", "question"))
                    waiting_for_response = True

                elif chunk["type"] == "end_call":
                    call_hangup_reasons[callsid] = "AI cut the call"
                    end_call_triggered = True

            if end_call_triggered:
                logger.info(f"AI said goodbye → ending call gracefully for {callsid}")
                await tts_manager.wait_until_done(timeout=5.0)
                if dg_connection:
                    try: await dg_connection.finish()
                    except Exception: pass
                await end_call(callsid, ws, stream_sid, agent, db)
                should_terminate = True
        
        except asyncio.CancelledError:
            logger.warning(f"[{callsid}] ⏪ Turn cancelled. Sticky-buffering context for AI.")
            # Use the MERGED context for the next turn buffer
            interrupted_transcript_buffer = merged_transcript 
            agent.rollback_last_turn()
            raise

        except Exception as e:
            logger.error(f"Error in turn execution: {e}")

    # ==== Transcript Processing Task ====
    async def process_transcripts():
        nonlocal tts_manager, should_terminate, waiting_for_response, active_turn_task, last_processed_text, last_processed_ts, interrupted_transcript_buffer

        try:
            while not should_terminate:
                transcript = await transcript_queue.get()
                if should_terminate: break

                # --- 💓 HEARTBEAT RESUMPTION ---
                if transcript == "[RESUME_INTERRUPTED]":
                    if not interrupted_transcript_buffer:
                        transcript_queue.task_done()
                        continue
                    logger.info(f"[{callsid}] 💓 HEARTBEAT: Resuming turn for '{interrupted_transcript_buffer[:50]}...'")
                    # Move buffer to transcript and clear it
                    transcript = interrupted_transcript_buffer
                    interrupted_transcript_buffer = ""

                # --- 🔀 MERGE LOGIC: Internal context for AI vs Raw for Display ---
                merged_transcript = transcript
                if interrupted_transcript_buffer:
                    logger.info(f"[{callsid}] 🩹 Prepending previous context for AI: '{interrupted_transcript_buffer}'")
                    merged_transcript = f"{interrupted_transcript_buffer} {transcript}".strip()
                    interrupted_transcript_buffer = ""


                # --- 🔀 SPLICING LOGIC: Merge Fragmented Utterances (IVR or Human) ---
                now = time.time()
                # If new text arrives within 1.5s of previous processing (handles split sentences/menus)
                if (now - last_processed_ts < 1.5) and last_processed_text:
                    logger.info(f"[{callsid}] 🔀 Continuation detected ({now-last_processed_ts:.2f}s). Merging fragments.")
                    # Splicing is both UI and AI level (it's a single thought)
                    transcript = f"{last_processed_text} {transcript}".strip()
                    merged_transcript = transcript # Splicing reset merged context too

                    # Cancel the "half-turn" that was already being processed
                    if active_turn_task and not active_turn_task.done():
                        active_turn_task.cancel()
                        # Await with timeout to ensure cleanup finishes but we don't block forever
                        try: await asyncio.wait_for(active_turn_task, timeout=0.5)
                        except (asyncio.CancelledError, asyncio.TimeoutError): pass
                # Update trackers for next potential splice
                last_processed_text = transcript
                last_processed_ts = now

                # Execute Turn as a cancellable task
                active_turn_task = asyncio.create_task(_handle_turn_execution(transcript, merged_transcript))
                
                transcript_queue.task_done()

        except asyncio.CancelledError:
            return

    # ==== Nudge Monitor Task ====
    async def nudge_monitor():
        nonlocal should_terminate, waiting_for_response, current_speaker_type, vad_last_voice_ts, nudge_count
        try:
            last_nudge_time = time.time()
            while not should_terminate:
                await asyncio.sleep(5)
                now = time.time()
                st = current_speaker_type.get("value", "human")
                if waiting_for_response and st == "human":
                    # Check for 40 seconds of silence
                    if (now - vad_last_voice_ts > 40) and (now - last_nudge_time > 40):
                        nudge_count += 1
                        
                        if nudge_count >= 3:
                            logger.warning(f"Maximum nudges (3) reached for {callsid}. Terminating call.")
                            call_hangup_reasons[callsid] = "no response coming"
                            should_terminate = True
                            break

                        nudge_msg = f"[SYSTEM EVENT: Human has been silent for {40 * nudge_count} seconds total. Politely ask 'Are you still there?']"
                        logger.info(f"Triggering NUDGE #{nudge_count}: {nudge_msg}")
                        await transcript_queue.put(nudge_msg)
                        last_nudge_time = now
        except asyncio.CancelledError:
            pass

    # ==== Max AHT Task ====
    async def max_aht_monitor():
        nonlocal should_terminate
        try:
            # 40 minutes = 2400 seconds
            await asyncio.sleep(2400)
            if not should_terminate:
                logger.warning(f"Max AHT exceeded for {callsid}. Forcing termination.")
                call_hangup_reasons[callsid] = "Max Call Time reached (40 mins)"
                should_terminate = True
                try:
                    if ws:
                        await ws.close()
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    # ==== Start Deepgram & Tasks (once) ====
    await dg_connection.start(options)
    silence_task = asyncio.create_task(monitor_silence())
    transcript_task = asyncio.create_task(process_transcripts())
    nudge_task = asyncio.create_task(nudge_monitor())
    aht_task = asyncio.create_task(max_aht_monitor())

    # ==== Main WebSocket Loop ====
    try:
        while True:
            message = await ws.receive_text()
            try:
                data = json.loads(message)
                event_type = data.get("event")
                logger.debug(f"WebSocket event: {event_type}, payload: {data}")
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON message: {e}")
                continue

            if event_type == "start":
                stream_sid = data.get("streamSid")
                callsid = data.get("start", {}).get("callSid")

                if callsid:
                    call_hangup_reasons[callsid] = None
                    agent.call_id = callsid
                    tts_manager = TTSManager(callsid=callsid)
                    await tts_manager.setup()
                
                # Pre-warmed manager should already be set up in the main task

                try:
                    fetcher = BatchDataFetcher(db, call_sid=callsid)
                    claim_data = await fetcher.get_claim_data()
                    # print(" Fetched claim data:", claim_data)
                    agent.claim_data = claim_data 
                except Exception as e:
                    logger.error(f"Error fetching claim data: {e}")
                    agent.claim_data = None
                try:
                    # Get BatchExecutionId, CallNumberExecutionId, BatchInfoId, etc.
                    ids_fetcher = BatchIdsFetcher(db, call_sid=callsid)
                    ids = await ids_fetcher.get_ids()
                    # Let agent remember these IDs for final write
                    agent.set_call_context_ids(
                        batch_info_id=ids.get("BatchInfoId"),
                        batch_execution_id=ids.get("BatchExecutionId"),
                        call_number_execution_id=ids.get("CallNumberExecutionId"),
                        csv_name=ids.get("CsvName"),
                    )
                except Exception as e:
                    logger.error(f"Error fetching context IDs: {e}")
                    # still continue; agent will write with None if needed


                buffer_sentence.clear()
                last_spoken_time = None
                vad_last_voice_ts = time.time()  # Initialize to current time to prevent immediate nudge
                nudge_count = 0                  # Reset nudge count on start
                pending_merge_text = ""          # reset held fragment
                waiting_for_response = True
                deepgram_ready = True

            elif event_type == "media" and waiting_for_response:
                if not deepgram_ready:
                    continue
                media = data.get("media", {})
                payload = media.get("payload", "")
                if not payload:
                    logger.debug("Empty media payload received")
                    continue

                try:
                    # 1) Send to Deepgram
                    mulaw_bytes = base64.b64decode(payload)
                    try:
                        await dg_connection.send(mulaw_bytes)
                    except Exception as dc_err:
                        logger.error(f"Deepgram send() failed: {dc_err}")
                        should_terminate = True
                        break

                    # 2) Silero VAD for barge-in / speech detection
                    pcm_8k = audioop.ulaw2lin(mulaw_bytes, 2)
                    pcm_16k, ratecv_state = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, ratecv_state)
                    silero_buffer += pcm_16k

                    if len(silero_buffer) >= silero_vad.chunk_bytes():
                        chunk = silero_buffer[:silero_vad.chunk_bytes()]
                        silero_buffer = silero_buffer[silero_vad.chunk_bytes():]
                        score = silero_vad(chunk)
                        logger.debug(f"Silero VAD score: {score:.2f}")

                        if score >= 0.7:
                            # 🛑 STOP/CANCEL Active Turns on Barge-in
                            if active_turn_task and not active_turn_task.done():
                                # Throttle log: don't spam 'Interruption' every 32ms
                                if time.time() - last_interruption_ts > 0.5:
                                    logger.info(f"[{callsid}] 🛑 Interruption! Cancelling active AI reasoning task.")
                                
                                active_turn_task.cancel()
                                last_interruption_ts = time.time() # Track the interruption time


                                # Note: rollback happens in the task's cleanup block or turn runner


                            # User speaking → cancel any pending commit & reset timer
                            await _cancel_pending_commit()
                            # NEW: record last VAD-positive time
                            vad_last_voice_ts = time.time()
                            nudge_count = 0  # Reset nudge count on speech detection

                            if not stop_tts_event.is_set():
                                stop_tts_event.set()
                                try:
                                    await ws.send_json({"event": "clear", "streamSid": stream_sid})
                                except Exception:
                                    pass
                                if not tts_already_cleared["value"]:
                                    try:
                                        await tts_manager.clear_buffer()
                                    except Exception:
                                        pass
                                    tts_already_cleared["value"] = True
                            last_spoken_time = time.time()  # prevents finalize while speaking


                except Exception as e:
                    logger.error(f"Error processing audio: {e}")

            elif event_type == "stop":
                logger.info("Stream stopped. Breaking out of event loop.")
                break

    except WebSocketDisconnect as e:
        logger.info(f"WebSocket disconnected: {e}")
        await end_call(callsid, ws, stream_sid, agent, db)
    except Exception as e:
        logger.error(f"Unhandled WebSocket error: {e}")
        await end_call(callsid, ws, stream_sid, agent, db)
    finally:
        logger.info(f"Starting final cleanup for call {callsid}")
        should_terminate = True

        try:
            await asyncio.sleep(0.5)  # Wait for pending transcripts
            await asyncio.wait_for(transcript_queue.join(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
        except Exception:
            pass
        
        # SAVE REMAINING BUFFER TEXT (NEW)
        if buffer_sentence:
            incomplete_text = " ".join(buffer_sentence).strip()
            incomplete_text = re.sub(r"\s+", " ", incomplete_text)
            
            if incomplete_text:
                logger.info(f"Saving incomplete buffer: '{incomplete_text}'")
                await save_message(db, conversations_repo, callsid, incomplete_text, "answer")
                await manager.broadcast_transcript_to_call(callsid, {"type": "user", "text": incomplete_text})
                agent.conversation_history.append({"role": "user", "content": incomplete_text})

        # 0) Ask Twilio to clear audio buffer (best effort)
        try:
            if stream_sid:
                await ws.send_json({"event": "clear", "streamSid": stream_sid})
        except Exception:
            pass

        # 1) Stop TTS FIRST (so we don't talk over the user)
        try:
            stop_tts_event.set()
        except Exception:
            pass
        try:
            if tts_manager:
                await tts_manager.clear_buffer()
        except Exception:
            pass
        try:
            if tts_manager:
                await tts_manager.cleanup()
        except Exception as e:
            logger.error(f"Error during TTSManager cleanup: {e}")

        # 2) Stop STT (Deepgram) SECOND — await its finish
        try:
            if pending_commit_task and not pending_commit_task.done():
                pending_commit_task.cancel()
        except Exception:
            pass

        if dg_connection:
            try:
                res = dg_connection.finish()
                if inspect.isawaitable(res):
                    await res
            except Exception:
                pass

        # 2.5) Give the silence monitor a tick to finalize any held buffer
        try:
            await asyncio.sleep(0.1)
            # Wait for queued transcripts to be consumed; don't hang forever
            try:
                await asyncio.wait_for(transcript_queue.join(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("Timed out waiting for transcript_queue to drain before shutdown.")
        except Exception:
            pass

        # 3) Now cancel background tasks (after draining)
        for task in [silence_task, transcript_task, nudge_task, aht_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Save hangup reason AFTER all background tasks are cancelled
        # This ensures it's the LAST message in the transcript
        if db and callsid:
            try:
                reason = call_hangup_reasons.get(callsid)
                if not reason:
                    reason = "Agent cut the call"
                logger.info(f"Saving hangup reason: {reason}")
                
                # Broadcast hangup reason to transcript WebSocket BEFORE closing connection
                try:
                    await manager.broadcast_transcript_to_call(callsid, {"type": "hangup", "text": reason})
                    logger.info(f"Broadcast hangup reason to transcript: {reason}")
                except Exception as broadcast_err:
                    logger.error(f"Error broadcasting hangup reason: {broadcast_err}")
                
                await save_message(db, conversations_repo, callsid, reason, "hangup")
                call_hangup_reasons.pop(callsid, None)
            except Exception as e:
                logger.error(f"Error saving hangup reason: {e}")

        # Run extraction and CSV creation in background - don't block WebSocket closure
        async def process_extraction_and_csv_async(email_for_db: str):
            # Create a FRESH session for this background task to avoid race conditions
            try:
                db = get_dynamic_db(email_for_db)
            except Exception as e:
                logger.error(f"Failed to create DB session for background task: {e}")
                return
            try:
                batch_execution_id = getattr(agent, "_batch_execution_id", None)
                call_number_execution_id = getattr(agent, "_call_number_execution_id", None)
                batch_info_id = getattr(agent, "_batch_info_id", None)
                
                # Skip extraction if critical IDs are missing
                if not batch_execution_id or not call_number_execution_id:
                    logger.warning(
                        f"Skipping extraction - missing IDs: "
                        f"BatchExecutionId={batch_execution_id}, "
                        f"CallNumberExecutionId={call_number_execution_id}"
                    )
                elif agent and getattr(agent, "ai_extract_and_build_output_record", None):
                    # Run extraction
                    output_record = await agent.ai_extract_and_build_output_record() or {}
                    
                    output_record["BatchInfoId"] = batch_info_id
                    output_record["BatchExecutionId"] = batch_execution_id
                    output_record["CallNumberExecutionId"] = call_number_execution_id
                    output_record["CsvName"] = getattr(agent, "_csv_name", None)
                    
                    logger.info(
                        f"BatchInfoOutput IDs for call {callsid}: "
                        f"BatchInfoId={batch_info_id}, "
                        f"BatchExecutionId={batch_execution_id}, "
                        f"CallNumberExecutionId={call_number_execution_id}"
                    )

                    if output_record.get("BatchExecutionId") and output_record.get("CallNumberExecutionId"):
                        # USE CSV SERVICE TO CREATE AND UPLOAD CSV
                        call_number_execution_id = output_record.get("CallNumberExecutionId")

                        logger.info(f"DEBUG - call_number_execution_id type: {type(call_number_execution_id)}")
                        logger.info(f"DEBUG - call_number_execution_id value: {call_number_execution_id}")

                        csv_url = await csv_service.create_and_upload_csv(
                            db,
                            output_record,
                            batch_info_id,
                            call_number_execution_id,
                            batch_info_data=getattr(agent, "claim_data", None)
                        )
                        # Add CSV URL to output record
                    if csv_url:
                        output_record["CsvLink"] = csv_url
                        logger.info(f"CSV URL added to output: {csv_url}")
                        
                        # Store CSV link and broadcast to batch (in background to avoid blocking)
                        async def broadcast_csv_link():
                            try:
                                # Store CSV link in service mapping
                                service.call_to_csv[callsid] = csv_url
                                
                                # Optimization: Get BatchId directly from memory (agent.claim_data)
                                # This avoids an extra DB query to BatchExecution
                                batch_id = None
                                if agent and getattr(agent, "claim_data", None):
                                    batch_id = agent.claim_data.get("BatchId")
                                
                                # Fallback (should rarely happen if fetcher works)
                                if not batch_id and batch_execution_id:
                                    try:
                                        batch_exec = db.query(models.BatchExecution).filter(
                                            models.BatchExecution.Id == batch_execution_id,
                                            models.BatchExecution.IsDeleted == False
                                        ).first()
                                        if batch_exec:
                                            batch_id = batch_exec.BatchId
                                    except Exception as e:
                                        logger.warning(f"Could not fetch batch_id from database (fallback): {e}")

                                # Get CallNumberId
                                call_number_id = None
                                if call_number_execution_id:
                                    try:
                                        call_num_exec = db.query(models.CallNumberExecution).filter(
                                            models.CallNumberExecution.Id == call_number_execution_id,
                                            models.CallNumberExecution.IsDeleted == False
                                        ).first()
                                        if call_num_exec:
                                            call_number_id = call_num_exec.CallNumberId
                                    except Exception as e:
                                        logger.warning(f"Could not fetch CallNumberId from database: {e}")
                                
                                if batch_id and batch_execution_id:
                                    # Store batch mapping for batch status broadcasts
                                    service.call_to_batch[callsid] = batch_id
                                    if call_number_id:
                                        service.call_to_callnumber[callsid] = call_number_id
                                    
                                    # Trigger immediate batch status broadcast with CSV link
                                    state = service.active_batches.get(batch_id)
                                    if state:
                                        await manager.broadcast_to_batch(batch_id, service._status_payload(state, state.stop_flag and "stopped" or "running"))
                                        logger.info(f"CSV link broadcast sent for batch {batch_id}, call {callsid}: {csv_url}")
                                    else:
                                        logger.info(f"CSV link stored for batch call {callsid}: {csv_url}")
                                else:
                                    # This is a standalone call (not part of a batch)
                                    logger.info(f"CSV created for standalone call {callsid}: {csv_url} (no batch broadcast)")
                            except Exception as broadcast_err:
                                logger.error(f"Error broadcasting CSV creation: {broadcast_err}")
                        
                        # Run broadcast in background - don't block
                        asyncio.create_task(broadcast_csv_link())
                    else:
                        logger.warning(f"CSV upload failed for call {callsid}")
                    
                    # Save to database with CSV URL + all extracted data
                    # NOTE: This function is now async!
                    result = await insert_batchinfo_output_force(db, output_record, created_by="system")
                    logger.info(f"BatchInfoOutput insert result: {result}")
                else:
                    logger.warning(
                        f"Skipping BatchInfoOutput insert — missing IDs: "
                        f"BatchExecutionId={output_record.get('BatchExecutionId')}, "
                        f"CallNumberExecutionId={output_record.get('CallNumberExecutionId')}, "
                        f"CallSid={callsid}"
                    )
                if agent:
                    try:
                        agent.conversation_history = []
                        logger.info(f"Cleared agent conversation history for call {callsid}")
                    except Exception:
                            pass
            except Exception as e:
                logger.error(f"Extraction/CSV error for call {callsid}: {e}")
            finally:
                if 'db' in locals() and db:
                    db.close()
        
        # Start extraction/CSV work in background - don't block call completion
        # Pass the email so it can create its own DB session
        asyncio.create_task(process_extraction_and_csv_async(email))
        
        # 6) Close the WebSocket LAST
        try:
            await ws.close()
        except Exception:
            pass


        # 7) Temp files
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception:
                pass

        # 8) Close DB
        if db:
            try:
                db.close()
            except Exception as e:
                logger.error(f"Error closing DB session: {e}")

        logger.info(f"Final cleanup complete for call {callsid}")



async def end_call(callsid, ws, stream_sid, agent, db):
    """End the call gracefully and clear agent conversation history"""
    if callsid:
        try:
            await send_clear(ws, stream_sid)
            twilio_client.calls(callsid).update(status="completed")

            temp_file_path = os.path.join(TEMP_DIR, f"{callsid}.txt")
            if os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                    logger.info(f"Deleted temp question file: {temp_file_path}")
                except Exception as e:
                    logger.error(f"Failed to delete temp question file {temp_file_path}: {e}")

        except Exception as e:
            logger.error(f"Failed to end call {callsid}: {e}")

async def send_clear(ws: WebSocket, stream_sid: str):
    try:
        await ws.send_json({
            "event": "clear",
            "streamSid": stream_sid
        })
        logger.info("Sent clear buffer command")
    except Exception as e:
        logger.error(f"Failed to send clear command: {e}")

required_env_vars = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "AWS_DEFAULT_REGION", "S3_BUCKET"]
missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars:
    raise RuntimeError(f"Missing environment variables: {missing_vars}")

@app.post("/twilio/recording-callback", response_model=schema.RecordingUploadResponse, summary="Twilio Recording Webhook", description="**Input:** Twilio form data (RecordingUrl, CallSid).<br>**Output:** JSON confirmation.<br><br>Receives recording from Twilio, uploads to S3, and saves metadata to DB.")
async def save_twilio_recording(request: Request, db: Session = Depends(get_db_from_query)):
    # Validate Twilio request
    recordings_repo = DocumentBaseRepository(models.Recordings)
    validator = RequestValidator(os.getenv("TWILIO_AUTH_TOKEN"))
    form_data = await request.form()
    signature = request.headers.get("X-Twilio-Signature")
    url = str(request.url).replace("http://", "https://")
    logger.info(f"Recording callback URL: {url}")
    if not validator.validate(url, form_data, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    # Extract form data
    recordingurl = form_data.get("RecordingUrl")
    recording_sid = form_data.get("RecordingSid")
    callsid = form_data.get("CallSid")

    if not all([recordingurl, callsid, recording_sid]):
        raise HTTPException(status_code=400, detail="Missing required Twilio data")

    # Download from Twilio
    async with httpx.AsyncClient() as client:
        response = await client.get(
            recordingurl,
            auth=(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
        )
        if response.status_code != 200:
            logger.error(f"Failed to download recording for CallSid {callsid}: {response.status_code}")
            raise HTTPException(status_code=502, detail="Twilio download failed")

        content_type = response.headers.get("Content-Type")
        # Accept audio/mpeg, audio/wav, and audio/x-wav
        if content_type not in ["audio/mpeg", "audio/wav", "audio/x-wav"]:
            logger.error(f"Unexpected content type: {content_type} for CallSid {callsid}")
            raise HTTPException(status_code=502, detail="Invalid audio format")

        file_extension = "mp3" if content_type == "audio/mpeg" else "wav"
        audio_url = f"{recordingurl}.{file_extension}"
        # s3_key = f"recordings/{callsid}.{file_extension}"
        now = datetime.now(timezone.utc)
        year = now.strftime("%Y")
        month = now.strftime("%m")
        day = now.strftime("%d")
        s3_key = f"recordings/{year}/{month}/{day}/{callsid}.{file_extension}"
        # Re-download with correct extension
        if audio_url != recordingurl:
            response = await client.get(
                audio_url,
                auth=(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
            )
            if response.status_code != 200:
                logger.error(f"Failed to download recording with extension {file_extension}: {response.status_code}")
                raise HTTPException(status_code=502, detail="Twilio download failed")

    # Upload to S3 (no ACL since Bucket owner enforced)
    try:
        audio_stream = io.BytesIO(response.content)
        session = aioboto3.Session()
        s3_bucket = os.getenv("MINIO_BUCKET_RECORDINGS", os.getenv("MINIO_BUCKET", "twiliorecordingsdata"))
        async with session.client("s3", region_name=os.getenv("AWS_DEFAULT_REGION")) as s3:
            await s3.upload_fileobj(
                Fileobj=audio_stream,
                Bucket=s3_bucket,
                Key=s3_key,
                ExtraArgs={"ContentType": content_type}
            )
        logger.info(f"Uploaded to s3://{s3_bucket}/{s3_key}")
        
        s3_key = f"recordings/{year}/{month}/{day}/{callsid}.{file_extension}"
        minio_public_endpoint = os.getenv("MINIO_PUBLIC_ENDPOINT", "").rstrip("/")
        public_url = f"{minio_public_endpoint}/{s3_bucket}/{s3_key}" if minio_public_endpoint else f"https://{s3_bucket}.s3.amazonaws.com/{s3_key}"
        recording_data = {
            "CallSid": callsid,
            "RecordingUrl": public_url,
            "CreatedAt": datetime.now(timezone.utc)
        }
        await recordings_repo.add(db, document_data=recording_data)
        logger.info(f"Recording stored in DB for CallSid {callsid}: {public_url}")
        try:
            twilio_client.recordings(recording_sid).delete()
            logger.info(f"Deleted Twilio recording {recording_sid} for CallSid {callsid}")
        except TwilioRestException as e:
            logger.error(f"Failed to delete Twilio recording {recording_sid}: {e}")
            pass
        return {"message": "Recording uploaded", "callsid": callsid}

    except ClientError as e:
        logger.error(f"S3 upload failed for CallSid {callsid}: {e}")
        raise HTTPException(status_code=500, detail=f"S3 upload failed: {e}")
    except Exception as e:
        logger.error(f"Unexpected error for CallSid {callsid}: {e}")
        raise HTTPException(status_code=500, detail="Internal upload error")
    
@app.get("/twilio/call-status/", summary="Twilio Status Webhook", description="**Input:** Twilio query params (CallSid, CallStatus).<br>**Output:** TwiML (empty/pause).<br><br>Tracks call lifecycle (ringing, in-progress, completed) and broadcasts updates via WebSocket.")
async def callstatus_webhook(
    request: Request,
    email: str = Query(..., alias="user-email")
):
    db = None
    try:
        db = get_dynamic_db(email)
        call_logs_repo = DocumentBaseRepository(models.CallLogs)
        callstatus_repo = DocumentBaseRepository(models.CallStatus)
        active_calls = service.active_calls
        callstatus_map = service.callstatus_map
        update_counters_for_call = service.update_counters_for_call
        params = dict(request.query_params)
        callsid = params.get("CallSid")
        callstatus = params.get("CallStatus")

        if not callsid:
            # Nothing to do without a CallSid; ack to Twilio to avoid retries
            return PlainTextResponse("OK", status_code=200)

        # ---------------------------------------------------------
        # 1) SAVE LOGS FIRST (Moved to top as requested)
        # ---------------------------------------------------------
        try:
            call_log = await call_logs_repo.get(db, filter_predicate=models.CallLogs.CallSid == callsid)
            if not call_log:
                call_log_data = {
                    "CallSid": callsid,
                    "PhoneNumber": params.get("Called"),
                    "CreatedAt": datetime.now(timezone.utc),
                }
                new_data = await call_logs_repo.add(db, document_data=call_log_data)
                logger.info(f"Call log added for CallSid: {callsid}")
                call_log = [new_data]
            
            callstatus_entry_data = {
                "CallLogsId": call_log[0].Id,
                "Status": callstatus,  # Use param directly (OPTIMIZATION)
            }
            await callstatus_repo.add(db, document_data=callstatus_entry_data)
            logger.info(f"Call status added for CallSid: {callsid}")
        except Exception as e:
            logger.error(f"Error saving call logs/status: {e}")
            # Continue execution even if logging fails? 
            # Usually better to log error and proceed so broadcast still happens.
        
        # ---------------------------------------------------------
        # 2) Logic & Broadcasts
        # ---------------------------------------------------------

        # record last status (shared map from batching.py)
        callstatus_map[callsid] = callstatus

        # signal batch runner when a call reaches a terminal state
        terminal_statuses = {"completed", "no-answer", "busy", "failed", "canceled"}
        if callstatus in terminal_statuses:
            ev = active_calls.get(callsid)
    
            # NEW: persist DB status + counters for the batch
            try:
                await update_counters_for_call(callsid, callstatus, email)
            except Exception as e:
                logger.warning(f"Failed to update batch counters for {callsid}: {e}")

            if ev is not None:
                logger.info(f"[CALL ENDED] {callsid} - Status: {callstatus} -> signaling batch runner")
                ev.set()
                
        # Broadcast "in-progress" initialization + wait
        if callstatus == "in-progress":
            await manager.broadcast_to_call(callsid, "agent initialization. waiting...")
            await asyncio.sleep(3)

        # Broadcast the status (Use callstatus param instead of slow fetch)
        # new_status = twilio_client.calls(callsid).fetch() <--- REMOVED (Slow)
        logger.info(f"Call {callsid} status: {callstatus}")
        await manager.broadcast_to_call(callsid, callstatus)

        # NEW: Universal cleanup for all terminal call statuses - wait 2 seconds after broadcast, then cleanup
        if callstatus in terminal_statuses:
            try:
                # Wait 2 seconds after terminal status broadcast
                logger.info(f"UNIVERSAL CLEANUP: Waiting 2 seconds before cleanup for call {callsid} with status {callstatus}")
                await asyncio.sleep(2.0)
                # Clean up ONLY call-specific WebSocket connections for this terminal call (not batch)
                logger.info(f"UNIVERSAL CLEANUP: Starting WebSocket cleanup for call {callsid} with status {callstatus}")
                cleanup_result = await manager.cleanup_call_only_connections(callsid)
                if cleanup_result:
                    logger.info(f"UNIVERSAL CLEANUP: Call-specific WebSocket connections cleaned up for TERMINAL call {callsid} with status: {callstatus}")
                else:
                    logger.warning(f"UNIVERSAL CLEANUP: Cleanup completed with warnings for call {callsid} with status {callstatus}")
            except Exception as e:
                logger.error(f"UNIVERSAL CLEANUP: Error during cleanup for terminal call {callsid} with status {callstatus}: {e}")
                logger.error(f"Exception details: {type(e).__name__}: {str(e)}")

        # Periodic broadcast for ringing status in callstatus_webhook
        if callstatus == "ringing":  # Check local param, equivalent to intended logic
            async def periodic_ringing():
                count = 0
                max_attempts = 5  # 10 minutes max (4s * 150 = 600s)
                
                while count < max_attempts:
                    await asyncio.sleep(4)
                    current_status = callstatus_map.get(callsid)
                    
                    # Stop if status changed to anything other than ringing
                    if current_status != "ringing":
                        logger.info(f"[RINGING-PERIODIC-STOP] Status changed for {callsid}: {current_status}")
                        break
                    
                    # Only broadcast if still ringing
                    try:
                        await manager.broadcast_to_call(callsid, "ringing")
                        count += 1
                        logger.info(f"[RINGING-PERIODIC] Sent for {callsid}, count: {count}")
                    except Exception as e:
                        logger.warning(f"[RINGING-PERIODIC] Failed to broadcast for {callsid}: {e}")
                        break
                
                # Cleanup if loop exits (either status changed or max attempts reached)
                if count >= max_attempts:
                    logger.warning(f"[RINGING-PERIODIC-TIMEOUT] Max attempts reached for {callsid} after {count * 4}s")
            
            asyncio.create_task(periodic_ringing())

        return Response(content=str(VoiceResponse()), media_type="application/xml")

    except Exception as e:
        logger.error(f"Error handling call status: {e}")
        return PlainTextResponse("OK", status_code=200)
    finally:
        if db:
            try:
                db.close()
                logger.info("DB session closed after call status webhook")
            except Exception as e:
                logger.error(f"Error closing DB session: {e}")

@app.get("/conversations", response_model=List[schema.ConversationResponse], summary="Get Call Transcript", description="**Input:** Query `call_sid`.<br>**Output:** List of message objects.<br><br>Retrieves the full conversation history (transcript) for a specific call.")
async def get_all_conversations(call_sid: Optional[str] = Query(None)):
    db = get_dynamic_db("admin@gmail.com")
    try:
        q = db.query(models.Conversation)
        if call_sid:
            q = q.filter(models.Conversation.CallSid == call_sid)

        rows = q.order_by(models.Conversation.CreatedAt.asc()).all()

        # Manual mapping from DB fields -> old API fields
        payload = [
            schema.ConversationResponse(
                id=r.Id,
                call_sid=r.CallSid,
                response_type=r.ResponseType,
                response_text=r.ResponseText,
                created_at=r.CreatedAt,
            )
            for r in rows
        ]
        return payload
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching conversations: {str(e)}")
    finally:
        db.close()


@app.websocket("/ws/transcript/{callsid}")
async def tts_websocket_endpoint(websocket: WebSocket, callsid: str):
    """WebSocket endpoint specifically for receiving TTS messages"""
    await manager.connect(websocket, callsid, connection_type="transcript")
    logger.info(f"TTS WebSocket connected for call: {callsid}")

    try:
        while True:
            # Just keep connection alive, server will push messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket, callsid, connection_type="transcript")
        logger.info(f"TTS client disconnected from call {callsid}")

# @app.websocket("/ws/message/{callsid}")
# async def sst_websocket_endpoint(websocket: WebSocket, callsid: str):
#     """WebSocket endpoint specifically for receiving SST transcriptions"""
#     await manager.connect(websocket, callsid, connection_type="message")
#     logger.info(f"SST WebSocket connected for call: {callsid}")

#     try:
#         while True:
#             # Just keep connection alive, server will push messages
#             await websocket.receive_text()
#     except WebSocketDisconnect:
#         await manager.disconnect(websocket, callsid, connection_type="message")
#         logger.info(f"SST client disconnected from call {callsid}")

@app.websocket("/ws/batch/{batch_id}")
async def batch_websocket_endpoint(websocket: WebSocket, batch_id: str):
    """WebSocket endpoint specifically for receiving batch status updates"""
    print(f"Batch WebSocket connection attempt for batch_id: {batch_id}")
    await manager.connect(websocket, batch_id, connection_type="batch")
    logger.info(f"Batch WebSocket connected for batch: {batch_id}")
    print(f"Batch WebSocket connected successfully for batch_id: {batch_id}")

    try:
        while True:
            # Just keep connection alive, server will push batch status messages
            data = await websocket.receive_text()
            print(f"Received message from batch WebSocket: {data}")
    except WebSocketDisconnect:
        await manager.disconnect(websocket, batch_id, connection_type="batch")
        logger.info(f"Batch client disconnected from batch {batch_id}")
        print(f"Batch WebSocket disconnected for batch_id: {batch_id}")

@app.websocket("/ws/activecalls")
async def activecalls_websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for receiving active call count"""
    print(f"Active calls WebSocket connection attempt")
    await manager.connect(websocket, key="ignored", connection_type="activecalls")
    logger.info(f"Active calls WebSocket connected")
    print(f"Active calls WebSocket connected successfully")

    try:
        while True:
            data = await websocket.receive_text()
            print(f"Received message from active calls WebSocket: {data}")
    except WebSocketDisconnect:
        await manager.disconnect(websocket, "global_active_calls", connection_type="activecalls")
        logger.info(f"Active calls WebSocket client disconnected")
        print(f"Active calls WebSocket disconnected")

@app.websocket("/ws/runningbatch")
async def runningbatch_websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for receiving running batch IDs"""
    print(f"Running batch WebSocket connection attempt")
    await manager.connect(websocket, "global_running_batches", connection_type="runningbatch")
    logger.info(f"Running batch WebSocket connected")
    print(f"Running batch WebSocket connected successfully")

    try:
        while True:
            # Just keep connection alive, server will push running batch ID updates
            data = await websocket.receive_text()
            print(f"Received message from running batch WebSocket: {data}")
    except WebSocketDisconnect:
        await manager.disconnect(websocket, "global_running_batches", connection_type="runningbatch")
        logger.info(f"Running batch WebSocket client disconnected")
        print(f"Running batch WebSocket disconnected")

@app.websocket("/ws/{callsid}")
async def websocket_endpoint(websocket: WebSocket, callsid: str):
    """General WebSocket endpoint for all messages (backward compatibility)"""
    await manager.connect(websocket, callsid, connection_type="general")
    logger.info(f"General WebSocket connected for call: {callsid}")

    try:
        while True:
            data = await websocket.receive_text()
            logger.info(f"Received data from WebSocket: {data}")
            await manager.send_personal_message(f"Received: {data}", websocket)
    except WebSocketDisconnect:
        await manager.disconnect(websocket, callsid, connection_type="general")
        logger.info(f"Client disconnected from call {callsid}")



@app.post("/hangup", response_model=schema.GenericMessageResponse, summary="Force Hangup", description="**Input:** Query `call_sid`.<br>**Output:** JSON message and `callsid`.<br><br>Forcefully terminates a live call.")
async def hangup_call(call_sid: str = Query(...)):
    try:
        call_hangup_reasons[call_sid] = "Forcefully cut the call through system"
        call = twilio_client.calls(call_sid).update(status="completed")
        logger.info(f"Call {call.sid} has been hung up.")
        # Delete temp question file
        temp_file_path = os.path.join(TEMP_DIR, f"{call.sid}.txt")
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.info(f"Deleted temp question file: {temp_file_path}")
            except Exception as e:
                logger.error(f"Failed to delete temp question file {temp_file_path}: {e}")
        return {"message": "Call hung up successfully", "callsid": call.sid}
    except Exception as e:
        logger.error(f"Error hanging up call {call_sid}: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to hang up call: {str(e)}")


@app.post("/call-logs", response_model=schema.PaginatedCallLogResponse, summary="Get Call Logs", description="**Input:** JSON `CallLogsRequest` (page, page_size) + `user-email` header.<br>**Output:** Paginated list of call logs with statuses and recordings.")
async def get_all_call_logs(
    request: schema.CallLogsRequest,
    db: Session = Depends(get_db_from_headers)
):  
    try:
        call_logs_repo = DocumentBaseRepository(models.CallLogs)
        paginated_data = await call_logs_repo.get_call_logs_with_statuses(
            db, request.page, request.page_size
        )
        return schema.PaginatedCallLogResponse(
            items=[
                schema.CallLogResponse(
                    id=log["log"].Id,
                    call_sid=log["log"].CallSid,
                    phone_number=log["log"].PhoneNumber,
                    statuses=[
                        schema.CallStatusResponse(
                            id=status.Id,
                            call_status =status.Status,
                            date=status.CreatedAt
                        ) for status in log["statuses"]
                    ],
                    recording_url=log["recordingurl"]
                )
                for log in paginated_data["items"]
            ],
            total_items=paginated_data["total_items"]
        )
    except Exception as e:
        logger.error(f"Error fetching call logs: {e}")
        raise HTTPException(status_code=500, detail="Error fetching call logs")
   

if __name__ == "__main__":

    try:
        port = int(os.getenv("PORT", "5050"))
        os.makedirs(TEMP_DIR, exist_ok=True)
        logger.info(f"Created directories: {TEMP_DIR}")
    except Exception as e:
        logger.error(f"Failed to create directories: {e}")
    uvicorn.run(app, host="0.0.0.0", port=port, timeout_keep_alive=75)