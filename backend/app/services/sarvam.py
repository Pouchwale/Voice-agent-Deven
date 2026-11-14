"""Sarvam AI speech services.

STT  : Saaras v3 realtime WebSocket  wss://api.sarvam.ai/speech-to-text-realtime/ws
       client -> {"event":"audio_input","audio":b64} | {"event":"ping"} | {"event":"end"}
       server -> session.begin, vad.speech_start, vad.speech_end, transcript.partial,
                 transcript.final {text}, pong, session.end, error {code,is_fatal,message}
TTS  : Bulbul v3 WebSocket  wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v3&send_completion_event=true
       client -> {"type":"config","data":{...}} then {"type":"text","data":{"text":...}}, {"type":"flush"}, {"type":"ping"}
       server -> {"type":"audio","data":{"audio":b64,"content_type":...}}, {"type":"event","data":{"event_type":"final"}}, {"type":"error",...}
       2,500 characters per WebSocket session -> one socket per agent reply.
TTS  : REST POST /text-to-speech -> {"audios":[b64 wav]} (cached clips: greeting, fillers, silence prompts)
Auth : header  api-subscription-key: <key>
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import functools
import json
import logging
import re
import time
import urllib.parse
from collections import OrderedDict
from typing import Awaitable, Callable

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from ..config import get_settings
from .audio import parse_wav_header_any, resample_once

log = logging.getLogger("sarvam")
settings = get_settings()

# Bulbul v3 speakers (lowercase ids, from Sarvam docs). Gender per Sarvam voice guide.
BULBUL_V3_VOICES: list[dict] = [
    *[{"id": v, "name": v.title(), "gender": "female"} for v in (
        "suhani", "priya", "ishita", "neha", "ritu", "simran", "kavya", "shreya",
        "roopa", "tanya", "shruti", "kavitha", "rupali")],
    *[{"id": v, "name": v.title(), "gender": "male"} for v in (
        "shubh", "ashutosh", "aditya", "rahul", "rohan", "amit", "dev", "ratan", "manan", "sumit",
        "kabir", "aayan", "advait", "anand", "tarun", "sunny", "mani", "gokul", "vijay", "mohit",
        "rehan", "soham", "varun")],
]
for _v in BULBUL_V3_VOICES:
    _v["model"] = "bulbul:v3"

# Bulbul v4-flash female personas (id = name_language_style; Sarvam docs, Nov 2026). Same streaming
# protocol and speed as v3 (measured 2026-11-13: first audio ~220 ms at 8 kHz linear16). The style is
# built into the persona, so `temperature` is not sent (v4 ignores it).
V4_MODEL = "bulbul:v4-flash"
BULBUL_V4_VOICES: list[dict] = [
    {"id": vid, "name": name, "gender": "female", "model": V4_MODEL} for vid, name in (
        ("aparna_hi_customer", "Aparna (v4) · customer care"),  # Sarvam's top pick for Hindi sales
        ("aarti_hi_customer", "Aarti (v4) · customer care"),
        ("ritu_hi_customer_warm", "Ritu (v4) · warm"),
        ("ritu_hi_sales", "Ritu (v4) · sales"),
        ("simran_hi_sales", "Simran (v4) · sales"),
        ("ishita_enhi_customer", "Ishita (v4) · Hinglish"),
        ("sarika_hi_conversation", "Sarika (v4) · conversational"),
        ("suhani_hi_social", "Suhani (v4) · lively"),
    )
]
ALL_VOICES = BULBUL_V3_VOICES + BULBUL_V4_VOICES
VALID_VOICES = {v["id"] for v in ALL_VOICES}
_V4_IDS = {v["id"] for v in BULBUL_V4_VOICES}


def voice_for(cfg: dict) -> str:
    v = (cfg.get("voice") or "").strip().lower()
    if v in VALID_VOICES:
        return v
    return "priya" if cfg.get("agent_gender", "female") == "female" else "shubh"


def model_for(voice: str) -> str:
    """Bulbul model that owns this speaker id (v4 persona ids look like name_hi_style)."""
    return V4_MODEL if voice in _V4_IDS else settings.sarvam_tts_model


def _headers() -> dict[str, str]:
    return {"api-subscription-key": settings.sarvam_api_key}


def _tts_lang(cfg: dict) -> str:
    code = cfg.get("language_code") or "hi-IN"
    return code if code != "auto" else "hi-IN"


# =====================================================================
# Text -> what the voice should actually say
# =====================================================================
_RULE_SPLIT = re.compile(r"\s*(?:=|->|→)\s*")
_MARKUP = re.compile(r"[*#`~|<>\[\]{}_]+")  # never meant to be read aloud


@functools.lru_cache(maxsize=32)
def _pronunciation_rules(lines: tuple[str, ...]) -> list[tuple[re.Pattern, str]]:
    rules: list[tuple[re.Pattern, str]] = []
    for line in lines:
        parts = _RULE_SPLIT.split(str(line).strip(), maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            src = re.escape(parts[0].strip())
            rules.append((re.compile(rf"(?<!\w){src}(?!\w)", re.IGNORECASE), parts[1].strip()))
    rules.sort(key=lambda r: len(r[0].pattern), reverse=True)  # "PouchWale India" before "PouchWale"
    return rules


def speakable(text: str, cfg: dict) -> str:
    """Text as it should be spoken: pronunciation fixes ("PouchWale = पाउचवाले") applied, stray
    markup removed. Only the audio uses this; transcripts keep the original spelling."""
    out = text
    for pattern, spoken in _pronunciation_rules(tuple(cfg.get("pronunciations") or ())):
        out = pattern.sub(spoken, out)
    out = _MARKUP.sub(" ", out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


# =====================================================================
# REST TTS (cached short clips)
# =====================================================================
_http: httpx.AsyncClient | None = None


def http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(base_url=settings.sarvam_base_url, timeout=20.0,
                                  limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))
    return _http


class _LRU(OrderedDict):
    def __init__(self, maxsize: int = 256):
        super().__init__()
        self.maxsize = maxsize

    def get_item(self, key):
        if key in self:
            self.move_to_end(key)
            return self[key]
        return None

    def put(self, key, value):
        self[key] = value
        self.move_to_end(key)
        while len(self) > self.maxsize:
            self.popitem(last=False)


_clip_cache = _LRU(400)


async def tts_rest(text: str, cfg: dict, sample_rate: int) -> bytes:
    """Synthesize `text` -> mono PCM16 at `sample_rate`. Cached by (text, voice, pace, temp, rate)."""
    text = speakable(text.strip(), cfg)
    if not text:
        return b""
    voice = voice_for(cfg)
    pace = float(cfg.get("tts_pace") or 1.0)
    temp = float(cfg.get("tts_temperature") or 0.6)
    key = (text, voice, round(pace, 2), round(temp, 2), sample_rate, _tts_lang(cfg))
    cached = _clip_cache.get_item(key)
    if cached is not None:
        return cached
    if not settings.sarvam_ready:
        raise RuntimeError("SARVAM_API_KEY is not set")
    model = model_for(voice)
    body = {
        "text": text[:2500],
        "target_language_code": _tts_lang(cfg),
        "speaker": voice,
        "model": model,
        "pace": max(0.5, min(2.0, pace)),
        "speech_sample_rate": sample_rate if sample_rate in (8000, 16000, 22050, 24000) else 24000,
    }
    if model != V4_MODEL:
        body["temperature"] = max(0.01, min(1.0, temp))
    resp = await http().post("/text-to-speech", json=body, headers=_headers())
    if resp.status_code >= 300:
        raise RuntimeError(f"Sarvam TTS {resp.status_code}: {resp.text[:200]}")
    audios = resp.json().get("audios") or []
    pcm = bytearray()
    rate = body["speech_sample_rate"]
    for b64 in audios:
        raw = base64.b64decode(b64)
        parsed = parse_wav_header_any(raw)
        if parsed:
            chunk, rate = parsed
        else:
            chunk = raw
        pcm.extend(chunk)
    out = resample_once(bytes(pcm), rate, sample_rate)
    _clip_cache.put(key, out)
    return out


# =====================================================================
# Streaming TTS (one socket per agent reply)
# =====================================================================
class TTSStream:
    """Bulbul v3 WebSocket session for one agent reply.

    Use: `await connect()` (can run while the LLM thinks), `await send_sentence()` per
    sentence, `await finish()` after the last one; audio arrives on `on_audio(pcm16)`.
    `done` is set once all requested audio was received (or on error/close).
    """

    def __init__(self, cfg: dict, sample_rate: int, on_audio: Callable[[bytes], Awaitable[None]]):
        self.cfg = cfg
        self.rate = sample_rate
        self.on_audio = on_audio
        self.ws: ClientConnection | None = None
        self.done = asyncio.Event()
        self.error: str | None = None
        self._reader: asyncio.Task | None = None
        self._pinger: asyncio.Task | None = None
        self._flushes = 0
        self._finals = 0
        self._finished = False
        self._last_audio = 0.0
        self._last_flush = 0.0
        self._chars = 0
        self._wav_header_pending = True
        self._carry = b""
        self.audio_bytes = 0
        self.sent: list[str] = []

    async def connect(self) -> None:
        voice = voice_for(self.cfg)
        model = model_for(voice)
        q = urllib.parse.urlencode({"model": model, "send_completion_event": "true"})
        url = f"{settings.sarvam_ws_url}/text-to-speech/ws?{q}"
        self.ws = await connect(url, additional_headers=_headers(), open_timeout=8, ping_interval=None,
                                max_size=8 * 1024 * 1024, compression=None)
        config = {
            "type": "config",
            "data": {
                "speaker": voice,
                "language_code": _tts_lang(self.cfg),
                "pace": max(0.5, min(2.0, float(self.cfg.get("tts_pace") or 1.0))),
                "speech_sample_rate": self.rate,
                "output_audio_codec": "linear16",
                "min_buffer_size": 30,
                "max_chunk_length": 200,
            },
        }
        if model != V4_MODEL:
            config["data"]["temperature"] = max(0.01, min(1.0, float(self.cfg.get("tts_temperature") or 0.6)))
        await self.ws.send(json.dumps(config))
        self._reader = asyncio.create_task(self._read_loop())
        self._pinger = asyncio.create_task(self._keepalive())

    async def _keepalive(self) -> None:
        """A pre-opened spare socket may sit idle while the customer talks: keep it alive."""
        try:
            while not self.done.is_set():
                await asyncio.sleep(8)
                if self.ws is not None and not self._chars:
                    await self.ws.send(json.dumps({"type": "ping"}))
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001  (socket gone: _read_loop marks done)
            pass

    async def send_sentence(self, text: str) -> None:
        """Queue one sentence. Raises if the socket is gone, so the caller can use the REST voice."""
        original = text.strip()
        text = speakable(original, self.cfg)
        if not text:
            return
        if self.ws is None or self.done.is_set():
            raise RuntimeError(self.error or "TTS socket is closed")
        if self._chars + len(text) > 2400:  # per-session character budget
            raise RuntimeError("TTS session character budget reached")
        self._chars += len(text)
        self.sent.append(original)  # REST fallback re-applies speakable() itself
        await self.ws.send(json.dumps({"type": "text", "data": {"text": text}}))
        await self.ws.send(json.dumps({"type": "flush"}))
        self._flushes += 1
        self._last_flush = time.monotonic()

    @property
    def unfinished_sentences(self) -> list[str]:
        """Sentences whose audio never fully arrived (Sarvam sends one 'final' event per flush)."""
        return self.sent[self._finals:]

    async def finish(self, timeout: float = 20.0) -> None:
        """Wait until every flushed sentence got its 'final' event (or the socket died)."""
        self._finished = True
        if self._finals >= self._flushes:
            self.done.set()
        deadline = time.monotonic() + timeout
        while not self.done.is_set() and time.monotonic() < deadline:
            # safety net only: Sarvam sends one 'final' per flush (measured), so this rarely triggers
            if time.monotonic() - max(self._last_audio, self._last_flush) > 5.0:
                self.error = self.error or "TTS stopped sending audio"
                break
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.done.wait(), 0.1)
        self.done.set()

    def _decode(self, raw: bytes, content_type: str) -> bytes:
        ct = (content_type or "").lower()
        if raw[:4] == b"RIFF":
            parsed = parse_wav_header_any(raw)
            if parsed:
                pcm, rate = parsed
                return resample_once(pcm, rate, self.rate)
        if "mp3" in ct or "mpeg" in ct or raw[:3] == b"ID3":
            self.error = "TTS returned MP3 audio; expected linear16"
            return b""
        data = self._carry + raw
        if len(data) % 2:
            self._carry, data = data[-1:], data[:-1]
        else:
            self._carry = b""
        return data

    async def _read_loop(self) -> None:
        assert self.ws is not None
        try:
            async for msg in self.ws:
                if isinstance(msg, bytes):
                    pcm = self._decode(msg, "")
                    if pcm:
                        self._last_audio = time.monotonic()
                        self.audio_bytes += len(pcm)
                        await self.on_audio(pcm)
                    continue
                data = json.loads(msg)
                mtype = data.get("type")
                if mtype == "audio":
                    d = data.get("data") or {}
                    pcm = self._decode(base64.b64decode(d.get("audio") or ""), d.get("content_type", ""))
                    if pcm:
                        self._last_audio = time.monotonic()
                        self.audio_bytes += len(pcm)
                        await self.on_audio(pcm)
                elif mtype == "event":
                    if (data.get("data") or {}).get("event_type") == "final":
                        self._finals += 1
                        if self._finished and self._finals >= self._flushes:
                            self.done.set()
                elif mtype == "error":
                    self.error = str((data.get("data") or {}).get("message") or data)[:300]
                    log.warning("TTS error: %s", self.error)
                    self.done.set()
        except ConnectionClosed as e:
            if self._finals < self._flushes or not self._finished:
                # closed by the server before all audio arrived (or while idle as a spare)
                self.error = self.error or f"TTS socket closed ({e.rcvd.code if e.rcvd else 'no code'})"
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            log.exception("TTS read loop failed")
        finally:
            self.done.set()

    async def close(self) -> None:
        self.done.set()
        if self._reader:
            self._reader.cancel()
        if self._pinger:
            self._pinger.cancel()
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.close()


# =====================================================================
# Realtime STT
# =====================================================================
class RealtimeSTT:
    """Saaras v3 realtime streaming STT with server-side VAD and partial transcripts."""

    def __init__(
        self,
        *,
        sample_rate: int,
        encoding: str,  # "mulaw" | "linear16"
        language_code: str,
        on_event: Callable[[str, dict], Awaitable[None]],
    ):
        self.rate = sample_rate
        self.encoding = encoding
        self.language_code = language_code or "hi-IN"
        self.on_event = on_event  # (kind, payload): speech_start, speech_end, partial, final, error
        self.ws: ClientConnection | None = None
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=500)
        self._tasks: list[asyncio.Task] = []
        self._closing = False
        self.connected = asyncio.Event()
        self.failed = False
        self._last_partial = ""

    def _url(self) -> str:
        q = {
            "model": settings.sarvam_stt_model,
            "language_code": self.language_code,
            "mode": settings.sarvam_stt_mode,
            "stream_type": settings.sarvam_stt_stream_type,
            "encoding": self.encoding,
            "sample_rate": str(self.rate),
            "endpointing": "vad",
            "silence_duration_ms": str(settings.sarvam_stt_silence_ms),
            "min_speech_duration_ms": "200",
        }
        return f"{settings.sarvam_ws_url}/speech-to-text-realtime/ws?{urllib.parse.urlencode(q)}"

    async def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._run()))

    def send_audio(self, chunk: bytes) -> None:
        if self._closing or not chunk:
            return
        try:
            self._queue.put_nowait(chunk)
        except asyncio.QueueFull:  # STT backlog: drop oldest, keep realtime
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(chunk)

    async def _run(self) -> None:
        attempts = 0
        while not self._closing:
            try:
                async with connect(self._url(), additional_headers=_headers(), open_timeout=8,
                                   ping_interval=None, compression=None, max_size=2 * 1024 * 1024) as ws:
                    self.ws = ws
                    self.connected.set()
                    writer = asyncio.create_task(self._writer(ws))
                    pinger = asyncio.create_task(self._pinger(ws))
                    try:
                        if await self._reader(ws):
                            attempts = 0  # healthy session: only real traffic resets the retry budget
                    finally:
                        writer.cancel()
                        pinger.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("STT connection error: %s", e)
            self.ws = None
            if self._closing:
                break
            attempts += 1
            if attempts > 4:
                self.failed = True
                await self._emit("error", {"message": "Speech recognition connection lost"})
                break
            await asyncio.sleep(min(0.3 * attempts, 1.5))

    async def _writer(self, ws: ClientConnection) -> None:
        # batch ~60 ms per message (Vobiz sends 20 ms frames)
        bytes_per_ms = self.rate / 1000 * (1 if self.encoding == "mulaw" else 2)
        target = int(bytes_per_ms * 60)
        buf = bytearray()
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                break
            buf.extend(chunk)
            while not self._queue.empty() and len(buf) < target:
                nxt = self._queue.get_nowait()
                if nxt is None:
                    break
                buf.extend(nxt)
            if len(buf) >= target or self._queue.empty():
                await ws.send(json.dumps({"event": "audio_input", "audio": base64.b64encode(bytes(buf)).decode()}))
                buf.clear()

    async def _pinger(self, ws: ClientConnection) -> None:
        while True:
            await asyncio.sleep(8)
            await ws.send(json.dumps({"event": "ping"}))

    async def _emit(self, kind: str, data: dict) -> None:
        """Deliver an event to the session; a bug or DB error there must never kill the STT socket."""
        try:
            await self.on_event(kind, data)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("STT event handler failed (%s)", kind)

    async def _reader(self, ws: ClientConnection) -> bool:
        """Read server events. Returns True if the session produced real traffic."""
        healthy = False
        async for msg in ws:
            if isinstance(msg, bytes):
                continue
            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                continue
            ev = data.get("event") or data.get("type")
            if ev != "error":
                healthy = True
            if ev == "vad.speech_start":
                await self._emit("speech_start", data)
            elif ev == "vad.speech_end":
                await self._emit("speech_end", data)
            elif ev == "transcript.partial":
                text = (data.get("text") or "").strip()
                if text and text != self._last_partial:  # Sarvam repeats identical partials often
                    self._last_partial = text
                    await self._emit("partial", {"text": text})
            elif ev == "transcript.final":
                self._last_partial = ""
                await self._emit("final", {"text": (data.get("text") or "").strip()})
            elif ev == "error":
                log.warning("STT error: %s", data)
                if data.get("is_fatal"):
                    await self._emit("error", {"message": data.get("message") or data.get("code")})
            elif ev == "session.end":
                break
        return healthy

    async def close(self) -> None:
        self._closing = True
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.send(json.dumps({"event": "end"}))
            with contextlib.suppress(Exception):
                await self.ws.close()
        for t in self._tasks:
            t.cancel()


async def check_tts_stream(cfg: dict | None = None) -> tuple[bool, str, int]:
    """Live check of the streaming path: first audio chunk latency + that it really is PCM (not MP3)."""
    if not settings.sarvam_ready:
        return False, "SARVAM_API_KEY not set", 0
    got: list[bytes] = []

    async def on_audio(pcm: bytes) -> None:
        got.append(pcm)

    t0 = time.monotonic()
    stream = TTSStream(cfg or {"voice": "priya", "language_code": "hi-IN"}, 8000, on_audio)
    try:
        await stream.connect()
        await stream.send_sentence("जी, बिल्कुल। मैं समझ गई।")
        first = 0
        while time.monotonic() - t0 < 8 and not got and not stream.done.is_set():
            await asyncio.sleep(0.02)
        first = int((time.monotonic() - t0) * 1000)
        await stream.finish(timeout=6)
        if stream.error:
            return False, stream.error, first
        if not got:
            return False, "no audio received from streaming TTS", first
        ms = sum(len(c) for c in got) // 16
        return True, f"streaming OK, first audio in {first} ms ({ms} ms of speech)", first
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200], int((time.monotonic() - t0) * 1000)
    finally:
        await stream.close()


async def check_stt_stream() -> tuple[bool, str, int]:
    """Handshake + short audio on the realtime STT socket with the exact production parameters."""
    if not settings.sarvam_ready:
        return False, "SARVAM_API_KEY not set", 0
    t0 = time.monotonic()
    stt = RealtimeSTT(sample_rate=8000, encoding="mulaw", language_code="hi-IN",
                      on_event=lambda kind, data: asyncio.sleep(0))
    url = stt._url()
    try:
        async with connect(url, additional_headers=_headers(), open_timeout=8, ping_interval=None) as ws:
            silence = base64.b64encode(b"\xff" * 4000).decode()  # 0.5 s of mu-law silence
            await ws.send(json.dumps({"event": "audio_input", "audio": silence}))
            await ws.send(json.dumps({"event": "ping"}))
            try:
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=4))
                    ev = msg.get("event") or msg.get("type")
                    if ev == "error":
                        return False, f"{msg.get('code')}: {msg.get('message')}", int((time.monotonic() - t0) * 1000)
                    if ev in ("pong", "session.begin"):
                        break
            except asyncio.TimeoutError:
                pass
            await ws.send(json.dumps({"event": "end"}))
        return True, f"{settings.sarvam_stt_model} accepted (mode={settings.sarvam_stt_mode}, mulaw 8 kHz)", \
            int((time.monotonic() - t0) * 1000)
    except ConnectionClosed as e:
        return False, f"closed {e.rcvd.code if e.rcvd else ''}: {e.rcvd.reason if e.rcvd else e}", 0
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200], int((time.monotonic() - t0) * 1000)


async def check_tts() -> tuple[bool, str, int]:
    if not settings.sarvam_ready:
        return False, "SARVAM_API_KEY not set", 0
    t0 = time.monotonic()
    try:
        pcm = await tts_rest("नमस्ते, test.", {"voice": "priya", "language_code": "hi-IN"}, 8000)
        ms = int((time.monotonic() - t0) * 1000)
        return bool(pcm), f"Bulbul OK ({len(pcm) // 16} ms audio)", ms
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200], int((time.monotonic() - t0) * 1000)
