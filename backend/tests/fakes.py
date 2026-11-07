"""Scripted stand-ins for Sarvam STT/TTS and the OpenAI stream, used to drive a real CallSession."""
from __future__ import annotations

import asyncio
import math
import struct
from typing import AsyncIterator


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
