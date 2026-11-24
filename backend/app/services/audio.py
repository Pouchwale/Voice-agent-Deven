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


def pcm_rms(pcm: bytes) -> int:
    """Raw RMS of a PCM16 chunk (0..32767): the loudness scale the background-voice filter uses."""
    return audioop.rms(pcm, SAMPLE_WIDTH) if pcm else 0


# Loudness of Sarvam's streaming voice (median RMS of voiced 20 ms frames, measured on Hindi and English
# sentences: 2970-3780). Its REST endpoint returns the same voice 1.5-2x louder, sometimes clipped, so a
# pre-made clip (greeting, filler, "are you there?") jumped out next to streamed replies.
STREAM_VOICE_LEVEL = 3300
# The same for Sarvam's v4 voices, measured on 2026-11-24 with this statistic: Sarika 4740, Shalini 4714, Simran 4465,
# Aparna 4196, Ishita 5458. With the v3 figure the ready-made clips (fillers, greeting, "are you there?") were about 3 dB
# quieter than her replies on calls with a v4 voice.
V4_VOICE_LEVEL = 4700


def match_loudness(pcm: bytes, rate: int, target: int = STREAM_VOICE_LEVEL) -> bytes:
    """Scale speech so its voiced frames sit at `target` (never above a safe peak)."""
    if not pcm:
        return pcm
    frame = max(1, rate // 50) * SAMPLE_WIDTH
    levels = sorted(r for r in (audioop.rms(pcm[i:i + frame], SAMPLE_WIDTH)
                                for i in range(0, len(pcm) - frame + 1, frame)) if r > 300)
    if len(levels) < 3:
        return pcm
    gain = min(1.5, target / levels[len(levels) // 2])
    peak = audioop.max(pcm, SAMPLE_WIDTH)
    if peak and peak * gain > 29000:
        gain = 29000 / peak
    if abs(gain - 1.0) < 0.05:
        return pcm
    return audioop.mul(pcm, SAMPLE_WIDTH, gain)


# Bulbul v4 sometimes returns a short line with many seconds of pure silence inside or after it: the filler
# "हम्म, एक second..." came back as 30.2 s with 0.7 s of voice (2026-11-21). The agent then "speaks" silence, her real
# answer queues behind it, and the customer says "Hello? आवाज़ नहीं आ रही" (call 64: 77 s of it in a 7 minute call).
SILENCE_PEAK = 64    # a 10 ms frame whose loudest sample is below this is silence on a phone line
MAX_PAUSE_MS = 700   # longest pause kept (the natural ones in her speech: 99% under 0.75 s)
CLIP_EDGE_MS = 120   # silence kept before and after a ready-made clip


class SilenceLimiter:
    """Passes PCM16 through chunk by chunk, dropping whatever is beyond MAX_PAUSE_MS of continuous silence."""

    def __init__(self, rate: int, max_pause_ms: int = MAX_PAUSE_MS):
        self.frame = max(1, rate // 100) * SAMPLE_WIDTH  # 10 ms
        self.limit = max_pause_ms // 10
        self._quiet = 0
        self._rest = b""

    def feed(self, pcm: bytes) -> bytes:
        data = self._rest + pcm
        whole = len(data) - len(data) % self.frame
        self._rest = data[whole:]
        out = bytearray()
        for i in range(0, whole, self.frame):
            frame = data[i:i + self.frame]
            if audioop.max(frame, SAMPLE_WIDTH) < SILENCE_PEAK:
                self._quiet += 1
                if self._quiet > self.limit:
                    continue
            else:
                self._quiet = 0
            out += frame
        return bytes(out)

    def flush(self) -> bytes:
        out, self._rest = self._rest, b""
        return out


def trim_clip(pcm: bytes, rate: int) -> bytes:
    """A ready-made line: no long pause inside it and almost none before or after it. Nothing but silence: b""."""
    limiter = SilenceLimiter(rate)
    out = limiter.feed(pcm) + limiter.flush()
    frame = limiter.frame
    voiced = [i for i in range(0, len(out) - frame + 1, frame)
              if audioop.max(out[i:i + frame], SAMPLE_WIDTH) >= SILENCE_PEAK]
    if not voiced:
        return b""
    edge = frame * (CLIP_EDGE_MS // 10)
    return out[max(0, voiced[0] - edge):min(len(out), voiced[-1] + frame + edge)]


def scale_volume(pcm: bytes, factor: float) -> bytes:
    return audioop.mul(pcm, SAMPLE_WIDTH, factor) if pcm and factor != 1.0 else pcm


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
