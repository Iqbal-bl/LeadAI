"""
sarvam_tts.py — Sarvam AI TTS Manager (Bulbul:v3, WebSocket streaming)
=====================================================================
Drop-in replacement for the Cartesia TTSManager in multiligual_call.py.
Uses the sarvamai SDK for low-latency WebSocket streaming.

Identical public interface:
    setup()  ·  stream_tts_audio()  ·  clear_buffer()  ·  cleanup()

Audio pipeline
--------------
Sarvam WebSocket (mulaw @ 8 kHz) → Twilio Media Stream
    160 bytes / 20 ms frame, no conversion needed
"""

import os
import base64
import asyncio
import time
import logging
from sarvamai import AsyncSarvamAI, AudioOutput, EventResponse

logger = logging.getLogger(__name__)

class SarvamTTSManager:
    """
    Sarvam AI TTS Manager using the Bulbul:v3 model with WebSocket streaming.
    """

    # Sentinel placed on _text_queue to tell text_sender to flush() the WS.
    # We stream convert() per sentence WITHOUT flushing — Sarvam buffers text
    # internally and starts rendering on its own threshold, giving cross-sentence
    # batching that drastically lowers per-sentence first-byte latency. flush()
    # is called once per turn (via end_turn()) when the LLM stream completes.
    _FLUSH_MARKER = object()

    _DEFAULT_SPEAKER = {
        "female": "priya",
        "male":   "shubh",
    }

    # Valid bulbul:v3 speakers (per sarvamai SDK configure_connection_data docs).
    # An invalid speaker makes Sarvam accept convert() but return NO audio — a
    # silent failure. We validate against this set and fall back to the gender
    # default so a bad client-supplied speaker can never mute the call.
    _VALID_SPEAKERS = {
        "aditya", "ritu", "priya", "neha", "rahul", "pooja", "rohan", "simran",
        "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun", "manan",
        "sumit", "roopa", "kabir", "aayan", "shubh", "ashutosh", "advait",
        "amelia", "sophia",
    }

    _LANG_MAP = {
        "hi": "hi-IN", "hi-IN": "hi-IN",
        "en": "en-IN", "en-IN": "en-IN",
        # Multi-lingual mode uses the en-IN voice. Bulbul:v3 with
        # enable_preprocessing=True handles Hindi/Hinglish reasonably under
        # en-IN, while keeping clean English pronunciation for digits and
        # English words — fixes the "400076 read as Hindi number" and
        # "d-i-g-i-t spelled letter-by-letter" issues with the hi-IN voice.
        "multi": "en-IN",
        "bn": "bn-IN", "gu": "gu-IN", "kn": "kn-IN",
        "ml": "ml-IN", "mr": "mr-IN", "od": "od-IN",
        "pa": "pa-IN", "raj": "raj-IN", "ta": "ta-IN", "te": "te-IN",
    }

    _FRAME_BYTES      = 160
    _BYTES_PER_SECOND = 8000
    _SILENCE_FRAME    = b'\xFF' * 160

    def __init__(
        self,
        callsid: str,
        gender: str = "female",
        language: str = "hi",
        speaker: str = None,
    ):
        self.callsid  = callsid
        self.language = language
        self.gender   = gender if gender in ("female", "male") else "female"
        # Validate the requested speaker against bulbul:v3's set. An unknown
        # value (e.g. client sent "ana") would make Sarvam return zero audio,
        # so fall back to the gender default and log the substitution.
        default_speaker = self._DEFAULT_SPEAKER.get(self.gender, "priya")
        requested = (speaker or "").strip().lower()
        if requested and requested in self._VALID_SPEAKERS:
            self.speaker = requested
        else:
            if requested:
                logger.warning(
                    f"[Sarvam TTS] invalid speaker {speaker!r} (not a bulbul:v3 voice); "
                    f"falling back to {default_speaker!r} call={callsid}"
                )
            self.speaker = default_speaker
        self.api_key  = os.getenv("SARVAM_API_KEY")

        self._current_twilio_ws   = None
        self._current_stream_sid  = None
        self._stop_event          = None
        self._utterance_done      = asyncio.Event() # Create once
        self._last_audio_ts       = None

        self._send_queue:   asyncio.Queue = None
        self._out_buffer:   bytes         = b""
        self._sender_task:  asyncio.Task  = None

        # When True, the receive loop discards any audio Sarvam delivers.
        # Set by clear_buffer() during barge-in to suppress in-flight server-
        # side renderings whose text was already sent before the interrupt.
        # Cleared at the start of each new stream_tts_audio call so the next
        # turn's audio flows normally.
        self._drop_incoming = False
        # Cleared on barge-in and set when Sarvam confirms the interrupted
        # utterance is final. A new turn must not accept audio before this,
        # otherwise late audio from the old turn leaks into the new one.
        self._barge_in_drain_done = asyncio.Event()
        self._barge_in_drain_done.set()

        self._client = AsyncSarvamAI(api_subscription_key=self.api_key)
        self._ws_loop_task: asyncio.Task = None
        self._text_queue: asyncio.Queue = None
        self._ws = None

    def _lang_code(self) -> str:
        return self._LANG_MAP.get(self.language, "hi-IN")

    async def setup(self):
        """Initialise the queues and start the WebSocket and paced sender tasks."""
        self._send_queue  = asyncio.Queue(maxsize=400)
        self._text_queue  = asyncio.Queue()
        self._sender_task = asyncio.create_task(self._paced_sender())
        self._ws_loop_task = asyncio.create_task(self._ws_connection_loop())
        
        # Wait for WS to be ready
        start_time = time.time()
        while self._ws is None and (time.time() - start_time) < 5.0:
            await asyncio.sleep(0.1)
            
        logger.info(
            f"[Sarvam TTS WS] ready  call={self.callsid} "
            f"speaker={self.speaker}  lang={self._lang_code()}"
        )

    async def cleanup(self):
        """Cancel tasks and release resources."""
        if self._sender_task:
            self._sender_task.cancel()
        
        if self._ws_loop_task:
            self._ws_loop_task.cancel()
            
        logger.info(f"[Sarvam TTS WS] cleaned up  call={self.callsid}")

    async def _ws_connection_loop(self):
        """Persistent WebSocket loop with auto-reconnect capability"""
        retry_delay = 1.0
        
        while True:
            try:
                # Always ensure client is ready
                if not self._client:
                    self._client = AsyncSarvamAI(api_subscription_key=self.api_key)
                
                async with self._client.text_to_speech_streaming.connect(
                    model="bulbul:v3", 
                    send_completion_event=True
                ) as ws:
                    self._ws = ws
                    # Reset retry delay on successful connection
                    retry_delay = 1.0
                    
                    # IMPORTANT: Must configure the session for mulaw 8kHz
                    # pace=1.05 keeps speech natural without dragging;
                    # loudness=1.4 compensates for phone-line attenuation;
                    # enable_preprocessing handles Hindi-English code-mixed text correctly.
                    await ws.configure(
                        target_language_code=self._lang_code(),
                        speaker=self.speaker,
                        speech_sample_rate=8000,
                        output_audio_codec="mulaw",
                        pace=1.05,
                        loudness=1.4,
                        enable_preprocessing=True,
                    )
                    
                    async def text_sender():
                        item = None
                        try:
                            while True:
                                item = await self._text_queue.get()
                                if item is None:
                                    break
                                if item is SarvamTTSManager._FLUSH_MARKER:
                                    # End of turn — force Sarvam to render whatever
                                    # text is still in its internal buffer.
                                    logger.info(f"[Sarvam TTS] flush() call={self.callsid}")
                                    await ws.flush()
                                    item = None
                                    continue
                                logger.info(f"[Sarvam TTS] convert() call={self.callsid} drop_incoming={self._drop_incoming} text={item[:60]!r}")
                                await ws.convert(item)
                                item = None
                        except Exception as e:
                            # The WS likely closed under us (Sarvam sends 1000 after
                            # idle/end-of-session). Restore the failed text at the FRONT
                            # of the logical queue. Appending it after an already queued
                            # FLUSH marker produces `flush -> convert` on reconnect, so
                            # short text remains buffered forever and the call is silent.
                            logger.warning(f"[Sarvam TTS WS] sender error ({e}); re-queuing text for reconnect")
                            if item is not None and item is not SarvamTTSManager._FLUSH_MARKER:
                                try:
                                    pending = []
                                    while not self._text_queue.empty():
                                        pending.append(self._text_queue.get_nowait())
                                    self._text_queue.put_nowait(item)
                                    for pending_item in pending:
                                        self._text_queue.put_nowait(pending_item)
                                except Exception:
                                    pass

                    # Sarvam closes the WS after a period of inactivity. In
                    # practice the socket has been reaped after only ~20s of
                    # silence (e.g. while waiting for the caller to start
                    # speaking after the greeting), so a 25s ping was too slow —
                    # the idle close landed BEFORE the next ping, and the first
                    # convert() afterwards hit a dead socket ("received 1000").
                    # Ping every 10s so the server side never goes idle long
                    # enough to close. Overridable via SARVAM_TTS_PING_INTERVAL.
                    PING_INTERVAL = float(os.getenv("SARVAM_TTS_PING_INTERVAL", "10.0"))

                    async def keepalive_pinger():
                        try:
                            while True:
                                await asyncio.sleep(PING_INTERVAL)
                                try:
                                    await ws.ping()
                                except Exception as e:
                                    logger.warning(f"[Sarvam TTS WS] ping failed: {e}")
                                    return
                        except asyncio.CancelledError:
                            raise

                    sender_task = asyncio.create_task(text_sender())
                    pinger_task = asyncio.create_task(keepalive_pinger())

                    try:
                        async for message in ws:
                            # Catch-all: log EVERY message type Sarvam returns so we
                            # can see when it accepts text but returns no audio (e.g.
                            # bad speaker/lang → silent rejection or error frame).
                            # logger.info(f"[Sarvam TTS] recv msg type={type(message).__name__} call={self.callsid}")
                            if isinstance(message, AudioOutput):
                                audio_b64 = message.data.audio
                                if audio_b64:
                                    # Add padding if necessary to avoid base64 decode errors
                                    missing_padding = len(audio_b64) % 4
                                    if missing_padding:
                                        audio_b64 += "=" * (4 - missing_padding)
                                    
                                    if self._drop_incoming:
                                        # Barge-in killed this turn; Sarvam is
                                        # still flushing audio for text we sent
                                        # before the interrupt. Discard so the
                                        # stale audio can't leak into the next
                                        # turn's playback.
                                        logger.warning(f"[Sarvam TTS] DROPPING audio (drop_incoming=True) call={self.callsid}")
                                        continue
                                    mulaw_chunk = base64.b64decode(audio_b64)
                                    self._last_audio_ts = time.monotonic()
                                    await self._send_queue.put(mulaw_chunk)
                            
                            elif isinstance(message, EventResponse):
                                event_type = getattr(message.data, "event_type", "unknown")
                                if event_type == "final":
                                    self._utterance_done.set()
                                    if self._drop_incoming:
                                        self._barge_in_drain_done.set()
                                elif event_type == "error":
                                    error_msg = getattr(message.data, "error", "Unknown Sarvam Error")
                                    logger.error(f"[Sarvam TTS WS] Error Event: {error_msg}")
                    except Exception as e:
                        logger.error(f"[Sarvam TTS WS] Stream Error: {e}")
                    finally:
                        sender_task.cancel()
                        pinger_task.cancel()
                        self._ws = None
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Sarvam TTS WS] connection lost: {e}. Retrying in {retry_delay}s...")
                self._ws = None  # Force fresh iterator on next loop
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 10.0) # Exponential backoff
        
        self._ws = None

    async def stream_tts_audio(
        self,
        ws,
        stream_sid: str,
        text: str,
        stop_event: asyncio.Event,
        wait_for_end: bool = True,
    ) -> bool:
        if not text or stop_event.is_set():
            return True

        if not self._barge_in_drain_done.is_set():
            try:
                await asyncio.wait_for(self._barge_in_drain_done.wait(), timeout=2.0)
                logger.info(
                    f"[Sarvam TTS] stale interrupted audio drained call={self.callsid}"
                )
            except asyncio.TimeoutError:
                # Do not deadlock the call if Sarvam omits its final event. The
                # local/Twilio queues were already hard-cleared during barge-in.
                logger.warning(
                    f"[Sarvam TTS] timed out draining interrupted audio call={self.callsid}"
                )
                self._barge_in_drain_done.set()

        self._current_twilio_ws  = ws
        self._current_stream_sid = stream_sid
        self._stop_event         = stop_event
        self._utterance_done.clear() # Reset existing event
        self._last_audio_ts      = None
        # New turn — accept Sarvam audio again. (clear_buffer raises this
        # flag during barge-in to suppress stale in-flight audio.)
        self._drop_incoming      = False

        tts_start = time.monotonic()

        async def monitor_completion():
            nonlocal tts_start
            first_byte_logged = False
            idle_seconds = 0.15
            
            try:
                while True:
                    if self._stop_event and self._stop_event.is_set():
                        break

                    if self._utterance_done.is_set():
                        break

                    # Log first-byte latency once
                    if not first_byte_logged and self._last_audio_ts is not None:
                        fb_ms = (self._last_audio_ts - tts_start) * 1000
                        logger.info(f"[Sarvam TTS] first-byte: {fb_ms:.0f}ms | text: {text[:50]}")
                        first_byte_logged = True

                    now = time.monotonic()
                    if (
                        self._last_audio_ts is not None
                        and (now - self._last_audio_ts) >= idle_seconds
                    ):
                        break
                    
                    await asyncio.sleep(0.05)
                
                if first_byte_logged:
                    total_ms = (time.monotonic() - tts_start) * 1000
                    logger.info(f"[Sarvam TTS] done: {total_ms:.0f}ms | text: {text[:50]}")
            except Exception as e:
                logger.error(f"[Sarvam TTS] monitor error: {e}")
            finally:
                if not self._utterance_done.is_set():
                    self._utterance_done.set()

        try:
            # Enqueue text for the WS loop
            await self._text_queue.put(text)

            if wait_for_end:
                await monitor_completion()
            else:
                # Fire and forget monitoring for pipelining, but return immediately
                asyncio.create_task(monitor_completion())
                
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[Sarvam TTS WS] stream_tts_audio error: {e}")
            return False

    async def end_turn(self):
        """Signal end of LLM turn — flushes any text Sarvam is still buffering
        so it renders the final tail of the utterance. Safe to call multiple
        times; extra flushes are no-ops on the server side."""
        if self._text_queue is not None:
            await self._text_queue.put(self._FLUSH_MARKER)

    async def clear_buffer(self):
        """Hard-flush all TTS state on barge-in.

        Three queues / buffers are in play; clearing only the outbound one
        (as the prior version did) lets stale audio leak into the next
        turn — Sarvam's server-side queue is still rendering text we sent
        before the interrupt, and that audio arrives a beat later.
        """
        server_already_final = self._utterance_done.is_set()

        # 1. Local outbound (audio already framed-and-paced for Twilio).
        self._out_buffer = b""
        if self._send_queue:
            try:
                while not self._send_queue.empty():
                    self._send_queue.get_nowait()
            except Exception:
                pass

        # 2. Pending text not yet sent to Sarvam. Drain so no further
        #    convert() calls are made for the interrupted turn.
        if self._text_queue:
            try:
                while not self._text_queue.empty():
                    self._text_queue.get_nowait()
            except Exception:
                pass

        # 3. Suppress audio Sarvam is still in the middle of rendering
        #    for text we sent BEFORE the interrupt. The receive loop checks
        #    this flag and silently drops audio while it's True. It's reset
        #    by stream_tts_audio at the start of the next turn.
        self._drop_incoming = True
        if server_already_final:
            self._barge_in_drain_done.set()
        else:
            self._barge_in_drain_done.clear()

    async def _paced_sender(self):
        frame_interval = self._FRAME_BYTES / self._BYTES_PER_SECOND
        next_due = time.monotonic()

        try:
            while True:
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

                if self._stop_event and self._stop_event.is_set():
                    self._out_buffer = b""
                    next_due = time.monotonic() + frame_interval
                    continue

                if len(self._out_buffer) >= self._FRAME_BYTES:
                    frame = self._out_buffer[: self._FRAME_BYTES]
                    self._out_buffer = self._out_buffer[self._FRAME_BYTES:]
                else:
                    frame = self._SILENCE_FRAME

                try:
                    if self._current_twilio_ws and self._current_stream_sid:
                        payload_b64 = base64.b64encode(frame).decode("utf-8")
                        await self._current_twilio_ws.send_json({
                            "event": "media",
                            "streamSid": self._current_stream_sid,
                            "media": {"payload": payload_b64},
                        })
                except Exception as e:
                    logger.error(f"[Sarvam TTS WS] send error: {e}")

                next_due += frame_interval

        except asyncio.CancelledError:
            return

    async def wait_until_done(self, timeout: float = 10.0):
        """Wait until text queue is empty and audio buffer is drained."""
        deadline = time.monotonic() + timeout
        
        # 1. Wait for text queue to be processed by WS
        while self._text_queue and not self._text_queue.empty() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            
        # 2. Wait for audio bits to be sent to Twilio
        while time.monotonic() < deadline:
            queue_empty = self._send_queue.empty() if self._send_queue else True
            if queue_empty and len(self._out_buffer) < self._FRAME_BYTES:
                return
            await asyncio.sleep(0.05)
