"""A transcript with no human voice behind it is not answered (2026-11-25). On loud traffic with nobody speaking, both
Sarvam speech models wrote a word in 3 of 3 clips ("4", "वो"; "हाँ" with saaras:v4), loud enough to pass the quiet-voice
filter. The audio behind each transcript is checked: a voice repeats at a pitch, traffic rumble and line hiss do not.
Measured (stt-benchmark/2026-11-25): traffic and deep rumble 0.04-0.11 voice-like, speech in heavy traffic 0.43+, real
customer speech median 0.97 (of 52 judged utterances of two real calls, only a voiceless burst no recogniser heard words
in would have been blocked)."""
from __future__ import annotations

import asyncio
import math
import random
import struct
import time

from app.services import session as session_mod
from app.services.audio import voiced_share

from .fakes import FakeSTT, ScriptedLLM
from .test_humanize_live import PhoneTransport, _agent_lines, _session, _wait, fakes  # noqa: F401  (autouse fixture)

RATE = 8000


def voice(sec: float, f0: float = 140.0, level: float = 3000.0) -> bytes:
    """A vowel-like sound: a harmonic series at a slowly gliding pitch (what a voice looks like to the check)."""
    out, phase = [], 0.0
    for i in range(int(RATE * sec)):
        phase += 2 * math.pi * (f0 * (1 + 0.04 * math.sin(2 * math.pi * 3 * i / RATE))) / RATE
        s = sum(math.sin(k * phase) / k for k in range(1, 9))
        out.append(struct.pack("<h", int(max(-32767, min(32767, level * s)))))
    return b"".join(out)


def rumble(sec: float, level: float = 2500.0, seed: int = 1) -> bytes:
    """Road noise: low-passed random walk plus hiss, no pitch."""
    rng = random.Random(seed)
    n, x, lp, out = int(RATE * sec), 0.0, 0.0, []
    raw = []
    for _ in range(n):
        x = 0.995 * x + rng.gauss(0, 1)
        lp += 0.2 * (x - lp)
        raw.append(lp + 0.6 * rng.gauss(0, 1))
    rms = math.sqrt(sum(v * v for v in raw) / n)
    for v in raw:
        out.append(struct.pack("<h", int(max(-32767, min(32767, v / rms * level)))))
    return b"".join(out)


def test_a_voice_is_voice_like_and_traffic_is_not():
    share, active = voiced_share(voice(1.0), RATE)
    assert active > 20 and share >= 0.8
    share, active = voiced_share(rumble(2.0), RATE)
    assert active > 40 and share <= session_mod.NO_VOICE_SHARE
    assert voiced_share(b"\x00\x00" * RATE, RATE) == (0.0, 0)  # silence: nothing to judge


async def _final_after(audio: bytes, llm, monkeypatch, phone: str):
    monkeypatch.setattr(session_mod.settings, "background_voice_filter", True)
    monkeypatch.setattr(session_mod.settings, "min_voice_level", 0)  # the quiet-voice filter is not what is tested
    s, t = await _session(phone, llm, monkeypatch, transport=PhoneTransport())
    run = asyncio.create_task(s.run())
    await _wait(lambda: s.greeted and s.state == "listening")
    stt = FakeSTT.instances[-1]
    await stt.on_event("speech_start", {})
    for i in range(0, len(audio), 320):  # 20 ms frames, as the phone line delivers them
        s.on_user_audio(audio[i:i + 320])
        await asyncio.sleep(0.02)
    await stt.on_event("speech_end", {})
    await stt.on_event("final", {"text": "हाँ"})
    await asyncio.sleep(0.4)
    return s, t, run


def test_a_word_made_of_traffic_noise_is_not_answered(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["जी, बताइए।"])
        s, t, run = await _final_after(rumble(1.5), llm, monkeypatch, "+919480007001")
        result = (len(llm.calls), s.noise_ignored, [e["text"] for e in t.events if e.get("role") == "user"])
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return result

    calls, ignored, lines = asyncio.run(scenario())
    assert calls == 0 and ignored == 1
    assert any(x.startswith("(noise, no voice in it, ignored)") for x in lines)


def test_a_real_voice_is_answered_as_before(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["जी, बताइए।"])
        s, t, run = await _final_after(voice(1.0), llm, monkeypatch, "+919480007002")
        await _wait(lambda: len(llm.calls) == 1, timeout=4)
        result = (len(llm.calls), s.noise_ignored)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return result

    assert asyncio.run(scenario()) == (1, 0)


def test_a_voice_in_traffic_is_answered(monkeypatch):
    noisy = bytes()
    v, r = voice(1.2), rumble(1.2, level=1500.0, seed=2)
    noisy = b"".join(struct.pack("<h", max(-32767, min(32767, struct.unpack_from("<h", v, i)[0] + struct.unpack_from("<h", r, i)[0])))
                     for i in range(0, len(v), 2))

    async def scenario():
        llm = ScriptedLLM(["जी, बताइए।"])
        s, t, run = await _final_after(noisy, llm, monkeypatch, "+919480007003")
        await _wait(lambda: len(llm.calls) == 1, timeout=4)
        result = (len(llm.calls), s.noise_ignored)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return result

    assert asyncio.run(scenario()) == (1, 0)


def test_the_check_is_off_with_the_background_filter(monkeypatch):
    """BACKGROUND_VOICE_FILTER=false turns this check off too (one switch for both filters)."""
    monkeypatch.setattr(session_mod.settings, "background_voice_filter", False)

    async def run_off():
        llm = ScriptedLLM(["जी, बताइए।"])
        monkeypatch.setattr(session_mod.settings, "min_voice_level", 0)
        s, t = await _session("+919480007005", llm, monkeypatch, transport=PhoneTransport())
        task = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.on_event("speech_start", {})
        audio = rumble(1.5)
        for i in range(0, len(audio), 320):
            s.on_user_audio(audio[i:i + 320])
            await asyncio.sleep(0.02)
        await stt.on_event("speech_end", {})
        await stt.on_event("final", {"text": "हाँ"})
        await _wait(lambda: len(llm.calls) == 1, timeout=4)
        result = (len(llm.calls), s.noise_ignored)
        await s.end("manual_hangup")
        await asyncio.wait_for(task, timeout=5)
        return result

    assert asyncio.run(run_off()) == (1, 0)


def test_too_little_audio_is_never_judged(monkeypatch):
    """No audio before the transcript (or too little to tell): answered, as before the check existed."""
    async def scenario():
        monkeypatch.setattr(session_mod.settings, "background_voice_filter", True)
        monkeypatch.setattr(session_mod.settings, "min_voice_level", 0)
        llm = ScriptedLLM(["जी, बताइए।"])
        s, t = await _session("+919480007004", llm, monkeypatch, transport=PhoneTransport())
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        await FakeSTT.instances[-1].say("हाँ जी बोलिए")
        await _wait(lambda: len(llm.calls) == 1, timeout=4)
        result = (len(llm.calls), s.noise_ignored)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return result

    assert asyncio.run(scenario()) == (1, 0)


def test_the_check_is_quick():
    audio = rumble(5.0)
    t0 = time.perf_counter()
    voiced_share(audio, RATE)
    assert time.perf_counter() - t0 < 0.25  # 5 s of audio, judged on the call's event loop
