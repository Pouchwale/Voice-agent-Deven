"""Audio helpers: mu-law <-> PCM16, resampling, simple VAD, WAV, call recorder."""
from __future__ import annotations

import io
import math
import wave
from pathlib import Path

import audioop  # provided by `audioop-lts` on Python 3.13+

SAMPLE_WIDTH = 2  # PCM16


def ulaw_to_pcm16(data: bytes) -> bytes:
    return audioop.ulaw2lin(data, SAMPLE_WIDTH)


def pcm16_to_ulaw(data: bytes) -> bytes:
    return audioop.lin2ulaw(data, SAMPLE_WIDTH)


class Resampler:
    """Stateful mono PCM16 resampler (keeps filter state between chunks)."""

    def __init__(self, src_rate: int, dst_rate: int):
        self.src = src_rate
        self.dst = dst_rate
        self._state = None

    def __call__(self, pcm: bytes) -> bytes:
        if self.src == self.dst or not pcm:
            return pcm
        out, self._state = audioop.ratecv(pcm, SAMPLE_WIDTH, 1, self.src, self.dst, self._state)
        return out


def resample_once(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    return Resampler(src_rate, dst_rate)(pcm)


def rms_level(pcm: bytes) -> float:
    """0..1 loudness of a PCM16 chunk (log-scaled so speech sits around 0.4-0.9)."""
    if not pcm:
        return 0.0
    rms = audioop.rms(pcm, SAMPLE_WIDTH)
    if rms <= 0:
        return 0.0
    db = 20 * math.log10(rms / 32768.0)  # -inf..0
    return max(0.0, min(1.0, (db + 60) / 60))


def wav_bytes(pcm: bytes, rate: int, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm16(data: bytes) -> tuple[bytes, int]:
    """Decode a WAV (PCM16 / mu-law / A-law) into mono PCM16 + sample rate."""
    with wave.open(io.BytesIO(data), "rb") as w:
        rate = w.getframerate()
        width = w.getsampwidth()
        channels = w.getnchannels()
        frames = w.readframes(w.getnframes())
    if width != SAMPLE_WIDTH:
        frames = audioop.lin2lin(frames, width, SAMPLE_WIDTH)
    if channels == 2:
        frames = audioop.tomono(frames, SAMPLE_WIDTH, 0.5, 0.5)
    return frames, rate


def parse_wav_header_any(data: bytes) -> tuple[bytes, int] | None:
    """Best-effort WAV parse that also handles mu-law (format 7) payloads, which `wave` rejects."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    pos = 12
    fmt_tag = channels = rate = bits = None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt_tag = int.from_bytes(body[0:2], "little")
            channels = int.from_bytes(body[2:4], "little")
            rate = int.from_bytes(body[4:8], "little")
            bits = int.from_bytes(body[14:16], "little")
        elif cid == b"data":
            if size == 0 or size > len(data) - pos - 8:  # streaming WAVs may leave size unset
                body = data[pos + 8:]
            if fmt_tag == 7:  # mu-law
                pcm = ulaw_to_pcm16(body)
            elif fmt_tag == 6:  # A-law
                pcm = audioop.alaw2lin(body, SAMPLE_WIDTH)
            elif bits and bits != 16:
                pcm = audioop.lin2lin(body, bits // 8, SAMPLE_WIDTH)
            else:
                pcm = body
            if channels == 2:
                pcm = audioop.tomono(pcm, SAMPLE_WIDTH, 0.5, 0.5)
            return pcm, int(rate or 8000)
        pos += 8 + size + (size & 1)
    return None


class EnergyVAD:
    """Lightweight speech detector used for barge-in and the silence timer.

    Adapts to the line's noise floor so it works on both quiet browser mics
    and noisy phone lines.
    """

    def __init__(self, rate: int, start_ms: int = 220, end_ms: int = 700):
        self.rate = rate
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.noise_floor = 300.0
        self.speaking = False
        self._voiced_ms = 0.0
        self._silent_ms = 0.0

    def recalibrate(self, pcm: bytes) -> None:
        """Treat the current level as background noise (called when STT hears no speech in it)."""
        rms = audioop.rms(pcm, SAMPLE_WIDTH) if pcm else 0
        self.noise_floor = max(self.noise_floor, rms * 1.1)
        self.speaking = False
        self._voiced_ms = 0.0
        self._silent_ms = 0.0

    def update(self, pcm: bytes) -> str | None:
        """Feed a chunk; returns 'start', 'end' or None."""
        if not pcm:
            return None
        ms = len(pcm) / SAMPLE_WIDTH / self.rate * 1000.0
        rms = audioop.rms(pcm, SAMPLE_WIDTH)
        threshold = max(600.0, self.noise_floor * 3.0)
        voiced = rms > threshold
        if not voiced:
            # slow adaptation of the noise floor on non-speech
            self.noise_floor = 0.95 * self.noise_floor + 0.05 * max(rms, 50)
        if voiced:
            self._voiced_ms += ms
            self._silent_ms = 0.0
            if not self.speaking and self._voiced_ms >= self.start_ms:
                self.speaking = True
                return "start"
        else:
            self._silent_ms += ms
            if self._silent_ms > 120:
                self._voiced_ms = 0.0
            if self.speaking and self._silent_ms >= self.end_ms:
                self.speaking = False
                return "end"
        return None


class CallRecorder:
    """Two-track recorder (L = customer, R = agent), time-aligned on the inbound clock.

    The customer track is continuous (telephony streams silence too), so its
    length is the call timeline. Agent audio is placed where it actually plays.
    """

    def __init__(self, rate: int):
        import time

        self._clock = time.monotonic
        self.t0 = self._clock()
        self.rate = rate
        self.user = bytearray()
        self.agent = bytearray()
        self._agent_cursor = 0  # bytes

    @property
    def now_bytes(self) -> int:
        return len(self.user)

    def _sync_clock(self) -> None:
        """Pad the customer track with silence if input audio stalled (e.g. browser muted)."""
        expected = int((self._clock() - self.t0) * self.rate) * SAMPLE_WIDTH
        gap = expected - len(self.user)
        if gap > int(self.rate * SAMPLE_WIDTH * 0.6):  # > 600 ms behind (beyond normal network jitter)
            gap -= gap % SAMPLE_WIDTH
            self.user.extend(b"\x00" * gap)

    def add_user(self, pcm: bytes) -> None:
        self._sync_clock()
        # A client cannot send audio faster than real time: drop anything beyond wall clock + 1 s
        # (protects memory against a misbehaving or malicious sender).
        limit = (int((self._clock() - self.t0) * self.rate) + self.rate) * SAMPLE_WIDTH
        room = limit - len(self.user)
        if room <= 0:
            return
        self.user.extend(pcm[: room - room % SAMPLE_WIDTH] if len(pcm) > room else pcm)

    def add_agent(self, pcm: bytes) -> None:
        self._sync_clock()
        start = max(self._agent_cursor, self.now_bytes)
        start -= start % SAMPLE_WIDTH
        end = start + len(pcm)
        if len(self.agent) < end:
            self.agent.extend(b"\x00" * (end - len(self.agent)))
        self.agent[start:end] = pcm
        self._agent_cursor = end

    def interrupt_agent(self) -> None:
        """Barge-in: drop agent audio that was queued but never played."""
        cut = self.now_bytes - self.now_bytes % SAMPLE_WIDTH
        if len(self.agent) > cut:
            del self.agent[cut:]
        self._agent_cursor = min(self._agent_cursor, cut)

    def save(self, path: Path) -> float:
        """Write a stereo WAV in 10 s chunks (constant memory, even for long calls)."""
        n = max(len(self.user), len(self.agent))
        n -= n % SAMPLE_WIDTH
        step = self.rate * SAMPLE_WIDTH * 10
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(SAMPLE_WIDTH)
            w.setframerate(self.rate)
            for i in range(0, n, step):
                j = min(n, i + step)
                user = bytes(self.user[i:j]).ljust(j - i, b"\x00")
                agent = bytes(self.agent[i:j]).ljust(j - i, b"\x00")
                w.writeframes(audioop.add(audioop.tostereo(user, SAMPLE_WIDTH, 1, 0),
                                          audioop.tostereo(agent, SAMPLE_WIDTH, 0, 1), SAMPLE_WIDTH))
        return n / SAMPLE_WIDTH / self.rate
