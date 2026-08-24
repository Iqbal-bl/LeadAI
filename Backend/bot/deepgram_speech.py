
import asyncio
import logging
import base64
import os
import inspect
import time
from deepgram import (
    DeepgramClient,
    SpeakWebSocketEvents,
    SpeakWSOptions,
    DeepgramClientOptions,
)
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Global counter for active TTS instances to track leaks
_active_tts_instances = 0


class TTSManager:
    """
    Deepgram TTS manager for streaming μ-law 8kHz audio to Twilio Media Streams.

    - 20 ms frames (160 bytes μ-law @ 8kHz).
    - Dedicated paced sender (exactly 1 frame / 20 ms) with drift correction.
    - Pre-roll padding to stabilize short utterances and connection jitter.
    - End-of-utterance via ~250 ms idle gap.
    """

    def __init__(self, callsid: str, voice_id: str = "aura-2-andromeda-en"):
        global _active_tts_instances
        self.callsid = callsid
        self.voice_id = voice_id
        self.api_key = os.getenv("DEEPGRAM_API_KEY")

        # Increment global counter
        _active_tts_instances += 1
        logger.info(f"[TTS-INIT] Active TTS instances: {_active_tts_instances}, new for callsid: {callsid}")

        # Deepgram SDK
        self._client = None
        self._connection = None
        self._main_loop: asyncio.AbstractEventLoop | None = None

        # Streaming state
        self._current_ws = None
        self._current_stream_sid = None
        self._stop_event: asyncio.Event | None = None

        # Utterance lifecycle
        self._utterance_done: asyncio.Event | None = None
        self._last_audio_ts: float | None = None       # monotonic timestamp
        self._current_text: str | None = None

        # Audio pacing / buffering
        self._frame_bytes = 160          # 20 ms @ 8kHz μ-law
        self._bytes_per_second = 8000
        self._silence = b'\xFF' * self._frame_bytes

        # Producer/consumer buffers
        self._send_queue: asyncio.Queue | None = None  # DG bytes -> sender
        self._out_buffer = b""                         # raw bytes for framing
        self._sender_task: asyncio.Task | None = None  # paced sender

        logger.info(f"TTSManager (SDK) initialized for call: {callsid}")

    async def setup(self):
        """Initialize Deepgram Speak WebSocket connection + paced sender."""
        start_time = time.time()
        logger.info(f"[TTS-SETUP-START] For callsid: {self.callsid} at {start_time}")
        try:
            config = DeepgramClientOptions(options={"keepalive": "true"})
            self._client = DeepgramClient(self.api_key, config=config)
            self._connection = self._client.speak.websocket.v("1")
            self._main_loop = asyncio.get_running_loop()
            self._send_queue = asyncio.Queue(maxsize=500)

            self._setup_event_listeners()

            options = SpeakWSOptions(
                model=self.voice_id,
                encoding="mulaw",
                sample_rate=8000,
            )
            started = self._connection.start(options)
            if not started:
                raise RuntimeError("Failed to start Deepgram TTS connection")

            # Start the paced sender that ticks every 20 ms
            self._sender_task = asyncio.create_task(self._paced_sender())

            setup_time = time.time() - start_time
            logger.info(f"[TTS-SETUP-SUCCESS] For callsid: {self.callsid}, took {setup_time:.2f}s, active instances: {_active_tts_instances}")
        except Exception as e:
            setup_time = time.time() - start_time
            import traceback
            logger.error(f"[TTS-SETUP-ERROR] For callsid: {self.callsid}, took {setup_time:.2f}s, error: {e}, stack: {traceback.format_exc()}")
            raise

            logger.info(f"TTSManager (SDK) setup complete for call: {self.callsid}")
        except Exception as e:
            logger.error(f"Failed to setup TTSManager for {self.callsid}: {e}")
            raise

    def _setup_event_listeners(self):
        """Register Deepgram WebSocket event handlers."""

        def on_binary_data(dg_self, data: bytes, *args, **kwargs):
            try:
                if self._stop_event and self._stop_event.is_set():
                    if self._utterance_done and not self._utterance_done.is_set() and self._main_loop and not self._main_loop.is_closed():
                        self._main_loop.call_soon_threadsafe(self._utterance_done.set)
                    return

                if not self._main_loop or self._main_loop.is_closed() or not self._send_queue:
                    logger.warning("Main loop or send_queue missing/closed; dropping audio frame.")
                    return

                self._last_audio_ts = time.monotonic()
                asyncio.run_coroutine_threadsafe(self._send_queue.put(data), self._main_loop)

            except Exception as ex:
                logger.error(f"[TTS-BINARY-ERROR] For callsid: {self.callsid}, error: {ex}")

        def on_close(dg_self, *args, **kwargs):
            logger.info(f"[TTS-CLOSE] For callsid: {self.callsid}")
            if self._utterance_done and not self._utterance_done.is_set() and self._main_loop and not self._main_loop.is_closed():
                self._main_loop.call_soon_threadsafe(self._utterance_done.set)

        def on_error(dg_self, error, *args, **kwargs):
            logger.error(f"[TTS-ERROR] For callsid: {self.callsid}, error: {error}")
            if self._utterance_done and not self._utterance_done.is_set() and self._main_loop and not self._main_loop.is_closed():
                self._main_loop.call_soon_threadsafe(self._utterance_done.set)

        self._connection.on(SpeakWebSocketEvents.AudioData, on_binary_data)
        self._connection.on(SpeakWebSocketEvents.Close, on_close)
        self._connection.on(SpeakWebSocketEvents.Error, on_error)

    async def stream_tts_audio(
        self,
        ws,
        stream_sid: str,
        text: str,
        stop_event: asyncio.Event,
        check_only: bool = False
    ) -> bool:
        """Synthesize `text` and stream to Twilio in real time."""
        if check_only:
            return False
        if not text:
            return True
        if stop_event.is_set():
            return True

        self._current_ws = ws
        self._current_stream_sid = stream_sid
        self._stop_event = stop_event

        await self._synthesize_and_stream(text)
        return True

    async def _synthesize_and_stream(self, text: str):
        """Send text to Deepgram and wait until audio fully drains."""
        self._utterance_done = asyncio.Event()
        self._last_audio_ts = None
        self._current_text = text
        logger.info(f"[TTS-SYNTH-START] For callsid: {self.callsid}, text: '{text[:50]}...'")

        # For very short text (single words like "okay", "yes", "1"),
        # Deepgram may clip the final syllable because it doesn't know the
        # utterance has ended. Adding a trailing period signals end-of-utterance.
        tts_text = text.strip()
        if len(tts_text) <= 15 and tts_text and tts_text[-1] not in ".?!,;:":
            tts_text = tts_text + "."
        is_short = len(tts_text) <= 16  # still short after period

        try:
            # Increase pre-roll to stabilize the audio path for Twilio/VoIP
            preroll_frames = 8 if is_short else 12
            for _ in range(preroll_frames):
                await self._enqueue_bytes(self._silence)

            self._connection.send_text(tts_text)
            self._connection.flush()

            # For short text, wait at least 200ms before checking for idle
            # so Deepgram has time to actually deliver audio.
            minimum_wait = 0.20 if is_short else 0.0
            synth_start = time.monotonic()

            # Wait loop: detect idle gap or external stop
            # Increase idle gap to avoid cutting off fast synthesis
            idle_seconds = 0.3

            while True:
                if self._stop_event and self._stop_event.is_set():
                    break
                if self._utterance_done and self._utterance_done.is_set():
                    break

                now = time.monotonic()
                elapsed = now - synth_start
                if self._last_audio_ts is not None and (now - self._last_audio_ts) >= idle_seconds:
                    # For short text, enforce minimum wait before treating as done
                    if elapsed >= minimum_wait:
                        await self._pad_tail_for_sender()
                        await self.wait_until_done(timeout=4.0)
                        logger.info(f"[TTS-SYNTH-IDLE] For callsid: {self.callsid}, idle after {now - self._last_audio_ts:.2f}s")
                        break

                await asyncio.sleep(0.01)

        except asyncio.CancelledError:
            logger.info(f"[TTS-SYNTH-CANCEL] For callsid: {self.callsid}")
            raise
        except Exception as e:
            logger.error(f"[TTS-SYNTH-ERROR] For callsid: {self.callsid}, error: {e}")
        finally:
            if self._utterance_done and not self._utterance_done.is_set():
                self._utterance_done.set()
                logger.info(f"[TTS-SYNTH-DONE] For callsid: {self.callsid}")

    async def _enqueue_bytes(self, data: bytes):
        """Put bytes into the sender queue."""
        if not self._send_queue:
            return
        try:
            await self._send_queue.put(data)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"_enqueue_bytes failed: {e}")

    async def _paced_sender(self):
        """
        Single precise sender:
          - Pulls DG bytes from _send_queue
          - Assembles into _out_buffer
          - Every 20 ms sends exactly one frame (or comfort silence)
          - Corrects for timer drift (catch-up mode for Windows resolution)
        """
        frame_interval = self._frame_bytes / self._bytes_per_second  # 0.02s
        next_due = time.monotonic()
        sent_frames = 0
        from starlette.websockets import WebSocketState

        try:
            while True:
                # 1. Drain queue into buffer
                if self._send_queue:
                    try:
                        while True:
                            piece = self._send_queue.get_nowait()
                            self._out_buffer += piece
                    except asyncio.QueueEmpty:
                        pass

                # 2. Barge-in check: drop buffered bytes
                if self._stop_event and self._stop_event.is_set():
                    self._out_buffer = b""
                    next_due = time.monotonic() + frame_interval
                    await asyncio.sleep(frame_interval)
                    continue

                # 3. Pacing: catch up if behind, else wait
                now = time.monotonic()
                if now < next_due:
                    await asyncio.sleep(next_due - now)
                    now = time.monotonic()
                
                # 4. Transmission (catch-up loop)
                while now >= next_due:
                    if self._stop_event and self._stop_event.is_set():
                        break

                    if len(self._out_buffer) >= self._frame_bytes:
                        chunk = self._out_buffer[:self._frame_bytes]
                        self._out_buffer = self._out_buffer[self._frame_bytes:]
                    else:
                        chunk = self._silence

                    try:
                        if self._current_ws and self._current_stream_sid:
                            if self._current_ws.client_state == WebSocketState.CONNECTED:
                                payload = base64.b64encode(chunk).decode("utf-8")
                                await self._current_ws.send_json({
                                    "event": "media",
                                    "streamSid": self._current_stream_sid,
                                    "media": {"payload": payload}
                                })
                                sent_frames += 1
                                if sent_frames % 200 == 0:
                                    logger.debug(f"[TTS-SENDER-SEND] For callsid: {self.callsid}, sent {sent_frames} frames")
                    except RuntimeError as re:
                        if "discon" in str(re).lower() or "close" in str(re).lower():
                            return
                        logger.error(f"[TTS-SENDER-WS-ERROR] {re}")
                        return
                    except Exception as e:
                        logger.error(f"[TTS-SENDER-SEND-ERROR] {e}")
                        return

                    next_due += frame_interval
                    # Limit catch-up to avoid overwhelming target in one burst
                    if (time.monotonic() - now) > 0.1:
                        break

        except asyncio.CancelledError:
            logger.info(f"[TTS-SENDER-CANCEL] For callsid: {self.callsid}, sent {sent_frames} frames total")
            return

    async def _pad_tail_for_sender(self):
        """Pad tail ONCE with μ-law silence so sender finishes cleanly."""
        if not self._out_buffer:
            return
        rem = len(self._out_buffer) % self._frame_bytes
        if rem:
            self._out_buffer += b'\xFF' * (self._frame_bytes - rem)

    async def wait_until_done(self, timeout: float = 10.0):
        """
        Wait until the audio queue and sender buffer are fully drained.
        Returns True if drained, False if timed out or interrupted.
        """
        deadline = time.monotonic() + timeout
        logger.debug(f"[TTS-DRAIN-WAIT] Starting drain check for {self.callsid} (timeout={timeout}s)")
        
        try:
            while time.monotonic() < deadline:
                # 1. Stop early if interrupted
                if self._stop_event and self._stop_event.is_set():
                    logger.info(f"[TTS-DRAIN-STOP] Drain check aborted due to stop_event for {self.callsid}")
                    return False

                # 2. Check if anything is pending in DG -> Sender queue
                queue_empty = (self._send_queue.empty() if self._send_queue else True)
                
                # 3. Check if anything is pending in Sender -> Twilio buffer
                # Buffer must be smaller than one 20ms frame (160 bytes)
                buffer_empty = len(self._out_buffer) < self._frame_bytes

                if queue_empty and buffer_empty:
                    logger.info(f"[TTS-DRAIN-COMPLETE] Audio fully delivered for {self.callsid}")
                    return True

                await asyncio.sleep(0.05)
            
            logger.warning(f"[TTS-DRAIN-TIMEOUT] Failed to drain audio in {timeout}s for {self.callsid}")
            return False

        except Exception as e:
            logger.error(f"[TTS-DRAIN-ERROR] {e}")
            return False

    async def clear_buffer(self):
        """Drop any pending audio and clear DG’s side (for barge-in)."""
        self._out_buffer = b""
        queue_cleared = 0
        if self._send_queue:
            try:
                while not self._send_queue.empty():
                    _ = self._send_queue.get_nowait()
                    queue_cleared += 1
            except Exception:
                pass
        if self._connection:
            try:
                self._connection.clear()
            except Exception as e:
                logger.error(f"[TTS-CLEAR-ERROR] {e}")

    async def cleanup(self):
        """Stop paced sender, close DG connection, reset state."""
        global _active_tts_instances
        if self._sender_task:
            self._sender_task.cancel()
            try:
                await self._sender_task
            except asyncio.CancelledError:
                pass
            self._sender_task = None

        if self._connection:
            try:
                result = self._connection.finish()
                if inspect.isawaitable(result):
                    await result
                logger.info(f"[TTS-FINISH] For callsid: {self.callsid}")
            except Exception as e:
                logger.error(f"[TTS-CLEANUP-ERROR] {e}")

        self._connection = None
        self._client = None
        self._out_buffer = b""
        self._send_queue = None
        self._current_ws = None
        self._current_stream_sid = None
        self._stop_event = None
        self._utterance_done = None
        self._last_audio_ts = None
        self._current_text = None

        _active_tts_instances -= 1
        logger.info(f"[TTS-CLEANUP-DONE] For callsid: {self.callsid}, active instances now: {_active_tts_instances}")