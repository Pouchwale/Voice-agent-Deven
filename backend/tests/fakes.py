"""Scripted stand-ins for Sarvam STT/TTS, the OpenAI stream and the Vobiz account API, used to drive a
real CallSession and the inbound setup / call-record sync."""
from __future__ import annotations

import asyncio
import json
import math
import struct
import urllib.parse
from typing import AsyncIterator

import httpx


def tone(ms: int, rate: int, amp: int = 6000, freq: int = 300) -> bytes:
    n = int(rate * ms / 1000)
    return b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / rate))) for i in range(n))


class FakeSTT:
    instances: list["FakeSTT"] = []

    def __init__(self, *, sample_rate, encoding, language_code, on_event):
        self.rate = sample_rate
        self.encoding = encoding
        self.on_event = on_event
        self.audio_bytes = 0
        self.failed = False
        self.closed = False
        FakeSTT.instances.append(self)

    async def start(self):
        return None

    def send_audio(self, chunk: bytes):
        self.audio_bytes += len(chunk)

    async def close(self):
        self.closed = True

    async def say(self, text: str, *, partial_first: bool = True):
        """Simulate the customer saying `text`."""
        await self.on_event("speech_start", {})
        if partial_first:
            await self.on_event("partial", {"text": text})
        await self.on_event("speech_end", {})
        await self.on_event("final", {"text": text})


class FakeTTSStream:
    """Each sentence -> `ms_per_sentence` of tone, delivered asynchronously like the real socket."""

    ms_per_sentence = 300
    sentences: list[str] = []

    def __init__(self, cfg, sample_rate, on_audio):
        self.rate = sample_rate
        self.on_audio = on_audio
        self.error = None
        self.audio_bytes = 0
        self.done = asyncio.Event()
        self.sent: list[str] = []
        self._pending: list[asyncio.Task] = []

    @property
    def unfinished_sentences(self) -> list[str]:
        return [] if self.audio_bytes else list(self.sent)

    async def connect(self):
        await asyncio.sleep(0.01)

    async def send_sentence(self, text: str):
        FakeTTSStream.sentences.append(text)
        self.sent.append(text)

        async def deliver():
            await asyncio.sleep(0.02)
            pcm = tone(self.ms_per_sentence, self.rate)
            self.audio_bytes += len(pcm)
            await self.on_audio(pcm)

        self._pending.append(asyncio.create_task(deliver()))

    async def finish(self, timeout: float = 20.0):
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

    async def close(self):
        for t in self._pending:
            t.cancel()


async def fake_tts_rest(text: str, cfg: dict, sample_rate: int) -> bytes:
    return tone(200, sample_rate)


class ScriptedLLM:
    """Replaces services.session.stream_reply; yields queued replies token by token."""

    def __init__(self, replies: list[str], delay: float = 0.005):
        self.replies = list(replies)
        self.delay = delay
        self.calls: list[list[dict]] = []

    async def __call__(self, messages: list[dict], cache_key: str | None = None) -> AsyncIterator[str]:
        self.calls.append(messages)
        self.cache_keys = getattr(self, "cache_keys", []) + [cache_key]
        reply = self.replies.pop(0) if self.replies else "जी।"
        for word in reply.split(" "):
            await asyncio.sleep(self.delay)
            yield word + " "


class FakeTransport:
    channel = "browser"
    in_rate = 16000
    out_rate = 24000
    stt_encoding = "linear16"

    def __init__(self):
        self.audio = bytearray()
        self.clears = 0
        self.events: list[dict] = []
        self.closed_reason: str | None = None

    async def send_audio(self, pcm16: bytes):
        self.audio.extend(pcm16)

    async def clear(self):
        self.clears += 1

    async def send_event(self, data: dict):
        self.events.append(data)

    async def close(self, reason: str):
        self.closed_reason = reason

    def user_pcm16(self, raw: bytes) -> bytes:
        return raw


class FakeVobizAPI:
    """In-memory Vobiz account REST API: applications, numbers (attach / detach), concurrency and call
    records. install() routes every services.vobiz.api() request here (nothing reaches Vobiz)."""

    PREFIX = "/api/v1/Account/MA_TEST"

    def __init__(self, number: str = "+918000000000"):
        self.apps: dict[str, dict] = {}
        self.numbers: dict[str, dict] = {number: {
            "e164": number, "application_id": None, "trunk_group_id": None, "voice_enabled": True,
            "status": "active", "is_blocked": False, "is_trial_number": False}}
        self.max_concurrent = 20
        self.cdrs: list[dict] = []
        self.requests: list[tuple[str, str]] = []  # (method, raw path + query) of every request
        self.down = False
        self._ids = 7000

    def add_app(self, name: str, answer_url: str = "https://ivr.example.com/answer", hangup_url: str | None = None) -> str:
        self._ids += 1
        app_id = str(self._ids)
        self.apps[app_id] = {"app_id": app_id, "app_name": name, "answer_url": answer_url, "answer_method": "POST",
                             "hangup_url": hangup_url or answer_url, "hangup_method": "POST", "application_type": "XML"}
        return app_id

    def install(self, monkeypatch, vobiz_module) -> None:
        transport = httpx.MockTransport(self.handle)
        monkeypatch.setattr(vobiz_module, "_client", lambda timeout: httpx.AsyncClient(transport=transport, timeout=timeout))

    @staticmethod
    def _json(status: int, data: dict) -> httpx.Response:
        return httpx.Response(status, json=data)

    def handle(self, request: httpx.Request) -> httpx.Response:
        raw = request.url.raw_path.decode()
        self.requests.append((request.method, raw))
        if self.down:
            return httpx.Response(503, text="service unavailable")
        path = raw.split("?", 1)[0]
        assert path.startswith(self.PREFIX), path
        path, method = path[len(self.PREFIX):], request.method
        body = json.loads(request.content) if request.content else {}
        q = dict(request.url.params)
        if path == "/Application/" and method == "GET":
            objs, off, lim = list(self.apps.values()), int(q.get("offset", 0)), int(q.get("limit", 20))
            return self._json(200, {"meta": {"limit": lim, "offset": off, "total_count": len(objs)},
                                    "objects": objs[off:off + lim]})
        if path == "/Application/" and method == "POST":
            assert {"app_name", "answer_url", "answer_method"} <= set(body), body
            app_id = self.add_app(body["app_name"], body["answer_url"], body.get("hangup_url"))
            self.apps[app_id].update(body)
            return self._json(201, {"app_id": app_id, "message": "created"})
        if path.startswith("/Application/") and method == "POST":
            app_id = path.strip("/").split("/")[1]
            if app_id not in self.apps:
                return self._json(404, {"error": "Application not found"})
            self.apps[app_id].update(body)
            return self._json(200, {"message": "changed"})
        if path == "/numbers" and method == "GET":
            items = [n for e, n in self.numbers.items() if e == q.get("search")]
            return self._json(200, {"items": items, "page": 1, "per_page": 25, "total": len(items)})
        if path.startswith("/numbers/") and path.endswith("/application"):
            encoded = path.split("/")[2]
            assert encoded.startswith("%2B"), "the + of an E.164 number must travel URL-encoded"
            num = self.numbers.get(urllib.parse.unquote(encoded))
            if num is None:
                return self._json(404, {"error": "number not found"})
            if method == "POST":
                if body.get("application_id") not in self.apps:
                    return self._json(404, {"error": "application not found"})
                num["application_id"] = body["application_id"]
            elif method == "DELETE":
                num["application_id"] = None
            return self._json(200, {"message": "ok", "number": num["e164"]})
        if path == "/concurrency":
            return self._json(200, {"concurrent_calls": 0, "max_concurrent": self.max_concurrent})
        if path == "/cdr":
            return self._json(200, {"data": list(self.cdrs), "count": len(self.cdrs),
                                    "pagination": {"page": 1, "has_next": False}})
        return self._json(404, {"error": f"no fake for {method} {path}"})


def _keep_background_providers_offline() -> None:
    """Safety net for every test that imports these fakes, whichever test starts the app:
    * the inbound clip cache pre-warms on every app startup and settings save: never through Sarvam;
    * Vobiz account API calls (inbound setup check, call-record sync) fail locally unless a test installs
      FakeVobizAPI (monkeypatch restores this guard afterwards)."""
    from app.services import inbound_audio, vobiz

    inbound_audio.tts_rest = fake_tts_rest
    offline = httpx.MockTransport(lambda request: httpx.Response(599, text="no real Vobiz from tests"))
    vobiz._client = lambda timeout: httpx.AsyncClient(transport=offline, timeout=timeout)


_keep_background_providers_offline()
