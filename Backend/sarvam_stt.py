"""
sarvam_stt.py — Sarvam AI STT Manager (Saaras:v3 streaming WebSocket)
=====================================================================
Drop-in replacement for the Deepgram live connection used in
multiligual_call.py. Uses the sarvamai SDK's speech_to_text_streaming
endpoint.

Public interface mirrors what the call-stream code already expects:
    setup()                 — open the WS (idempotent; used for pre-warm too)
    send_pcm16k(pcm_bytes)  — push raw 16-bit PCM @ 16 kHz mono chunk
    cleanup()               — close the WS and tear down tasks

Caller attaches an `on_transcript(text, detected_language)` coroutine
on the manager; the receiver loop invokes it for every transcript
event Sarvam emits.

Audio expectation
-----------------
Twilio sends mulaw @ 8 kHz. The call handler already converts that to
PCM s16le @ 16 kHz (for Silero VAD) — we reuse those bytes verbatim.
"""

import os
import base64
import asyncio
import logging
from typing import Callable, Optional, Awaitable

from sarvamai import AsyncSarvamAI

logger = logging.getLogger(__name__)


class SarvamSTTManager:
    # Sarvam BCP-47 codes; "unknown" enables auto language detection.
    # Languages absent from Sarvam's streaming Literal (e.g. Rajasthani)
    # fall back to Hindi, matching the prior Deepgram mapping.
    _LANG_MAP = {
        "hi": "hi-IN", "hi-IN": "hi-IN",
        "en": "en-IN", "en-IN": "en-IN",
        "bn": "bn-IN", "gu": "gu-IN", "kn": "kn-IN",
        "ml": "ml-IN", "mr": "mr-IN", "od": "od-IN",
        "pa": "pa-IN", "ta": "ta-IN", "te": "te-IN",
        "raj": "hi-IN",
        "multi": "unknown",
    }

    def __init__(
        self,
        callsid: str,
        language: str = "hi",
        on_transcript: Optional[Callable[[str, Optional[str]], Awaitable[None]]] = None,
    ):
        self.callsid = callsid
        self.language = language
        self.on_transcript = on_transcript
        self.api_key = os.getenv("SARVAM_API_KEY")

        self._client = AsyncSarvamAI(api_subscription_key=self.api_key)
        self._send_queue: Optional[asyncio.Queue] = None
        self._ws_loop_task: Optional[asyncio.Task] = None
        self._open_event: Optional[asyncio.Event] = None
        self._closed = False
        # Diagnostics: how many audio packets sent and how many Sarvam
        # messages received. Surfaced via the periodic [STT HEARTBEAT] log
        # so we can tell when Sarvam silently stops returning transcripts.
        self._audio_packets_sent = 0
        self._messages_received = 0
        self._data_messages_received = 0

    def _lang_code(self) -> str:
        return self._LANG_MAP.get(self.language, "hi-IN")

    async def setup(self):
        """Open the streaming WS. Safe to await multiple times — second call
        is a no-op if the loop is already running."""
        if self._ws_loop_task is not None:
            return
        self._send_queue = asyncio.Queue(maxsize=200)
        self._open_event = asyncio.Event()
        self._ws_loop_task = asyncio.create_task(self._ws_loop())
        try:
            await asyncio.wait_for(self._open_event.wait(), timeout=6.0)
            logger.info(
                f"[Sarvam STT WS] ready call={self.callsid} lang={self._lang_code()}"
            )
        except asyncio.TimeoutError:
            logger.error(
                f"[Sarvam STT WS] open timeout call={self.callsid} lang={self._lang_code()}"
            )

    async def _ws_loop(self):
        try:
            async with self._client.speech_to_text_streaming.connect(
                language_code=self._lang_code(),
                model="saaras:v3",
                input_audio_codec="pcm_s16le",
                sample_rate="16000",
                vad_signals="false",
            ) as ws:
                self._open_event.set()
                sender = asyncio.create_task(self._sender_loop(ws))
                receiver = asyncio.create_task(self._receiver_loop(ws))
                heartbeat = asyncio.create_task(self._heartbeat_loop())
                done, pending = await asyncio.wait(
                    [sender, receiver],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # Identify which task ended (sender or receiver) so we can
                # see in logs whether the WS died on the send side, the
                # receive side, or was torn down externally.
                for t in done:
                    name = "sender" if t is sender else "receiver"
                    exc = t.exception() if not t.cancelled() else None
                    if exc is not None:
                        logger.error(
                            f"[Sarvam STT WS] {name} task exited with exception "
                            f"call={self.callsid}: {exc!r}"
                        )
                    else:
                        logger.warning(
                            f"[Sarvam STT WS] {name} task ended cleanly "
                            f"call={self.callsid} — WS will close"
                        )
                heartbeat.cancel()
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        pass
                try:
                    await heartbeat
                except (asyncio.CancelledError, Exception):
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[Sarvam STT WS] connect/loop error call={self.callsid}: {e}")
        finally:
            if self._open_event and not self._open_event.is_set():
                self._open_event.set()

    async def _heartbeat_loop(self):
        """Every 10s, log packets sent vs messages received. If we're
        sending audio but Sarvam has gone silent, that imbalance shows up
        here immediately instead of being invisible until the call ends."""
        try:
            prev_sent = 0
            prev_msgs = 0
            while True:
                await asyncio.sleep(10.0)
                sent_delta = self._audio_packets_sent - prev_sent
                msg_delta = self._messages_received - prev_msgs
                logger.info(
                    f"[STT HEARTBEAT] call={self.callsid} "
                    f"audio_sent_total={self._audio_packets_sent} (+{sent_delta} in 10s) | "
                    f"msgs_recv_total={self._messages_received} (+{msg_delta} in 10s) | "
                    f"transcripts_total={self._data_messages_received}"
                )
                if sent_delta > 50 and msg_delta == 0:
                    logger.warning(
                        f"[STT HEARTBEAT] call={self.callsid} — audio is flowing but "
                        f"Sarvam STT returned 0 messages in last 10s. WS may be dead."
                    )
                prev_sent = self._audio_packets_sent
                prev_msgs = self._messages_received
        except asyncio.CancelledError:
            return

    async def _sender_loop(self, ws):
        logger.info(f"[Sarvam STT] sender loop started call={self.callsid}")
        try:
            while True:
                pcm = await self._send_queue.get()
                if pcm is None:
                    logger.info(f"[Sarvam STT] sender loop got sentinel, exiting call={self.callsid}")
                    return
                try:
                    b64 = base64.b64encode(pcm).decode("ascii")
                    await ws.transcribe(audio=b64, encoding="audio/wav", sample_rate=16000)
                    self._audio_packets_sent += 1
                except Exception as e:
                    logger.error(f"[Sarvam STT] send error after {self._audio_packets_sent} packets call={self.callsid}: {e!r}")
                    return
        except asyncio.CancelledError:
            logger.info(f"[Sarvam STT] sender loop cancelled call={self.callsid}")
            raise

    async def _receiver_loop(self, ws):
        logger.info(f"[Sarvam STT] receiver loop started call={self.callsid}")
        try:
            async for resp in ws:
                self._messages_received += 1
                t = getattr(resp, "type", None)
                data = getattr(resp, "data", None)
                if t == "data" and data is not None:
                    self._data_messages_received += 1
                    transcript = getattr(data, "transcript", "") or ""
                    detected = getattr(data, "language_code", None)
                    logger.info(
                        f"[Sarvam STT] recv data call={self.callsid} "
                        f"transcript={transcript!r} detected_lang={detected}"
                    )
                    if transcript.strip() and self.on_transcript is not None:
                        try:
                            await self.on_transcript(transcript, detected)
                        except Exception as cb_err:
                            logger.error(f"[Sarvam STT] on_transcript error: {cb_err}")
                elif t == "error":
                    logger.error(f"[Sarvam STT] error event call={self.callsid}: {data}")
                else:
                    logger.info(f"[Sarvam STT] recv non-data call={self.callsid} type={t!r} data={data!r}")
            logger.warning(
                f"[Sarvam STT] receiver loop ended cleanly (WS iterator drained) "
                f"call={self.callsid} after {self._messages_received} messages — "
                f"Sarvam closed the connection"
            )
        except asyncio.CancelledError:
            logger.info(f"[Sarvam STT] receiver loop cancelled call={self.callsid}")
            raise
        except Exception as e:
            logger.error(f"[Sarvam STT] recv error call={self.callsid} after {self._messages_received} messages: {e!r}")

    async def send_pcm16k(self, pcm_bytes: bytes):
        """Push a chunk of PCM s16le @ 16 kHz. Drops silently if the WS
        isn't ready yet (pre-warm race) or the queue is saturated."""
        if self._closed or not pcm_bytes or self._send_queue is None:
            return
        try:
            self._send_queue.put_nowait(pcm_bytes)
        except asyncio.QueueFull:
            pass

    async def flush(self):
        """Force Sarvam to finalize whatever it's buffering. Best-effort."""
        # Sender owns the WS object; we don't have direct access here.
        # Flushing through the queue isn't supported by the SDK shape,
        # so this is a no-op placeholder kept for parity with Deepgram.
        return

    async def cleanup(self):
        self._closed = True
        if self._send_queue is not None:
            try:
                self._send_queue.put_nowait(None)
            except Exception:
                pass
        if self._ws_loop_task:
            try:
                await asyncio.wait_for(self._ws_loop_task, timeout=2.0)
            except asyncio.TimeoutError:
                self._ws_loop_task.cancel()
                try:
                    await self._ws_loop_task
                except (asyncio.CancelledError, Exception):
                    pass
            except Exception:
                pass
        logger.info(f"[Sarvam STT WS] cleaned up call={self.callsid}")
