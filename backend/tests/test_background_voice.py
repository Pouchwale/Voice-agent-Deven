"""Background voices, unclear speech and the English call (phone sessions with real audio levels).

Levels are measured like a real call: a customer talking into the phone peaked at ~5000-8000 RMS, people
talking near them at ~1600-3300. The tests feed 20 ms frames of that loudness next to the STT events.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.defaults import DEFAULT_AGENT_SETTINGS, DROPPED_FILLERS
from app.models import Call, Lead, TranscriptTurn
from app.services import session as session_mod
from app.services.call_manager import manager
from app.services.prompts import (ENGLISH_AUTO_NOTE, ENGLISH_LINES, ENGLISH_PREFERRED_NOTE, language_request,
                                  spoke_hindi, static_system_prompt)
from app.services.session import CallSession, english_voice_cfg

from .fakes import FakeSTT, FakeTransport, FakeTTSStream, ScriptedLLM, fake_tts_rest, tone

settings = get_settings()
NEAR = 9000   # sine amplitude -> RMS ~6400: the customer talking into the phone
FAR = 3000    # RMS ~2100: someone talking a few metres away


class PhoneTransport(FakeTransport):
    channel = "phone"
    in_rate = 8000
    out_rate = 8000
    stt_encoding = "mulaw"


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeSTT.instances.clear()
    FakeTTSStream.sentences.clear()
    monkeypatch.setattr(session_mod, "RealtimeSTT", FakeSTT)
    monkeypatch.setattr(session_mod, "TTSStream", FakeTTSStream)
    monkeypatch.setattr(session_mod, "tts_rest", fake_tts_rest)
    monkeypatch.setattr(settings, "background_voice_filter", True)
    yield


def _cfg(**over) -> dict:
    cfg = dict(DEFAULT_AGENT_SETTINGS)
    cfg.update({"silence_timeout_sec": 30, "silence_final_wait_sec": 5, "filler_delay_ms": 5000})
    cfg.update(over)
    return cfg


async def _session(phone: str, llm: ScriptedLLM, monkeypatch, *, transport=None, greeting_ms: int = 300,
                   **cfg_over) -> tuple[CallSession, FakeTransport]:
    monkeypatch.setattr(session_mod, "stream_reply", llm)
    with SessionLocal() as db:
        lead = Lead(name="Prit Shah", phone=phone, company="Shah Foods", city="Surat")
        db.add(lead)
        db.commit()
        lead_id = lead.id
    call, lead_d = await manager.create_browser_call(lead_id, None, None)
    with SessionLocal() as db:
        call_id = db.scalar(select(Call.id).where(Call.uuid == call["uuid"]))
    t = transport or PhoneTransport()
    s = CallSession(call_id=call_id, call_uuid=call["uuid"], lead=lead_d, cfg=_cfg(**cfg_over), transport=t,
                    greeting_pcm=tone(greeting_ms, t.out_rate, amp=0) or None,
                    on_finished=manager.session_finished)
    manager.register(s)
    return s, t


async def _wait(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


async def _say(s: CallSession, text: str, amp: int, ms: int = 600, *, partial: bool = True) -> None:
    """The customer (or someone near them) says `text` at loudness `amp`: STT events around real audio."""
    stt = FakeSTT.instances[-1]
    await stt.on_event("speech_start", {})
    frame = tone(20, 8000, amp=amp)
    for i in range(ms // 20):
        s.on_user_audio(frame)
        if partial and i == (ms // 20) // 2:
            await stt.on_event("partial", {"text": text})
        await asyncio.sleep(0)
    quiet = tone(20, 8000, amp=40)
    for _ in range(10):  # the VAD's end-of-speech pause
        s.on_user_audio(quiet)
    await stt.on_event("speech_end", {})
    await stt.on_event("final", {"text": text})


def _saved_user_turns(call_uuid: str) -> list[str]:
    with SessionLocal() as db:
        call_id = db.scalar(select(Call.id).where(Call.uuid == call_uuid))
        return [t.text for t in db.scalars(select(TranscriptTurn).where(TranscriptTurn.call_id == call_id,
                                                                        TranscriptTurn.role == "user")).all()]


# ---------------------------------------------------------------- background voices
QUIET = 1900  # RMS ~1340: a customer speaking softly / holding the phone away (the dropped "9 to 5" answer)
WHISPER = 700  # RMS ~500: near-silent, a TV across the room


def test_quiet_customer_is_always_answered(monkeypatch):
    """Live call: a real answer ("Anytime between 9 to 5") was as quiet as background talk, was dropped, and
    the agent said "are you there?". A quiet answer must be answered like any other."""
    async def scenario():
        llm = ScriptedLLM(["When would suit you?", "Perfect, tomorrow between nine and five then."])
        s, t = await _session("+919470000001", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        await _say(s, "Yes sure why not", NEAR, ms=2000)
        await _wait(lambda: len(llm.calls) == 1 and s.state == "listening")
        await _say(s, "Anytime between 9 to 5 tomorrow", QUIET, ms=1500)
        await _wait(lambda: len(llm.calls) == 2)
        run.cancel()
        return s, llm

    s, llm = asyncio.run(scenario())
    assert s.background_ignored == 0 and s.silence_stage == 0
    assert llm.calls[1][-1]["content"] == "Anytime between 9 to 5 tomorrow" or any(
        m.get("content") == "Anytime between 9 to 5 tomorrow" for m in llm.calls[1])
    assert "Anytime between 9 to 5 tomorrow" in _saved_user_turns(s.uuid)


def test_near_silent_speech_is_not_answered(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["जी, thank you! आपका product क्या है?", "पाँच हज़ार, बढ़िया। कब तक चाहिए?"])
        s, t = await _session("+919470000008", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        await _say(s, "हाँ जी बताइए", NEAR)
        await _wait(lambda: len(llm.calls) == 1 and s.state == "listening")
        words_before = s.user_words
        await _say(s, "वो टीवी पर क्या आ रहा है", WHISPER, ms=1200)
        await asyncio.sleep(0.4)
        assert len(llm.calls) == 1 and s.background_ignored == 1 and s.user_words == words_before
        await _say(s, "पाँच हज़ार pouch चाहिए", NEAR)
        await _wait(lambda: len(llm.calls) == 2)
        run.cancel()
        return s, t, llm

    s, t, llm = asyncio.run(scenario())
    shown = [e["text"] for e in t.events if e.get("type") == "transcript" and e.get("role") == "user" and e.get("final")]
    assert any(x.startswith("(background voice, ignored)") for x in shown)
    assert all("टीवी" not in m["content"] for m in llm.calls[1] if m["role"] == "user")
    assert not any("टीवी" in x for x in _saved_user_turns(s.uuid))


def test_background_voice_cannot_cut_off_the_greeting(monkeypatch):
    async def scenario():
        s, t = await _session("+919470000002", ScriptedLLM(["जी।"]), monkeypatch, greeting_ms=6000)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeting_playing)
        # someone nearby talks over the greeting: several words, but far quieter than a customer
        await _say(s, "Hello पड़ी थोड़ा है ना अरे", FAR, ms=1400)
        await asyncio.sleep(0.2)
        assert s.greeting_playing and t.clears == 0
        # the customer themselves talks over it: that does interrupt
        await _say(s, "हाँ बोलिए कौन बोल रहा है", NEAR, ms=1400)
        await _wait(lambda: t.clears >= 1)
        run.cancel()

    asyncio.run(scenario())


def test_quiet_voice_over_the_agent_waits_its_turn(monkeypatch):
    """Too quiet to cut the agent off, but not lost: answered as soon as the agent's sentence ends."""
    async def scenario():
        FakeTTSStream.ms_per_sentence = 2500  # a long agent sentence to talk over
        try:
            llm = ScriptedLLM(["जी, हम brands के लिए custom printed pouches बनाते हैं।", "जी, बताइए।"])
            s, t = await _session("+919470000003", llm, monkeypatch)
            run = asyncio.create_task(s.run())
            await _wait(lambda: s.greeted and s.state == "listening")
            await _say(s, "हाँ जी बोलिए क्या काम है", NEAR)
            await _wait(lambda: s.agent_audible())
            await _say(s, "मुझे पाँच हज़ार pouch चाहिए", QUIET, ms=900)
            assert t.clears == 0  # didn't cut the agent off...
            await _wait(lambda: len(llm.calls) == 2, timeout=10)  # ...but was answered right after
            run.cancel()
            return llm
        finally:
            FakeTTSStream.ms_per_sentence = 300

    llm = asyncio.run(scenario())
    assert any(m.get("content") == "मुझे पाँच हज़ार pouch चाहिए" for m in llm.calls[1])


def test_filter_never_applies_to_browser_tests(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["जी।"])
        s, t = await _session("+919470000004", llm, monkeypatch, transport=FakeTransport())
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        await FakeSTT.instances[-1].say("हाँ जी बताइए")  # no audio at all
        await _wait(lambda: len(llm.calls) == 1)
        run.cancel()

    asyncio.run(scenario())


# ---------------------------------------------------------------- unclear speech
def test_no_filler_or_prompt_claims_to_understand():
    words = DEFAULT_AGENT_SETTINGS["filler_words"]
    assert not any("समझ" in w for w in words)
    assert "जी, समझ गई" in DROPPED_FILLERS
    prompt = static_system_prompt(dict(DEFAULT_AGENT_SETTINGS), "outbound", "outbound")
    assert "UNCLEAR SPEECH" in prompt and "do NOT act as if you understood" in prompt
    assert "DEFLECTION" in prompt and "never per kg" in prompt  # anything not in the knowledge: the team covers it


def test_saved_understanding_filler_is_never_played(monkeypatch):
    async def scenario():
        s, t = await _session("+919470000005", ScriptedLLM([]), monkeypatch,
                              filler_words=["जी, समझ गई", "हम्म"])
        await s._prepare_clips()
        return s

    s = asyncio.run(scenario())
    played = [s._filler_texts[k] for k in s.clips if k.startswith("filler_")]
    assert played == ["हम्म"]


# ---------------------------------------------------------------- English calls
def test_short_english_requests_are_understood():
    for ask in ("In English", "English", "English please", "English only sir", "स्पीक इंग्लिश", "इन इंग्लिश",
                "Can you speak in English?"):
        assert language_request(ask) == "english", ask
    for not_ask in ("हमें English labels चाहिए", "English labels", "No No", "Hindi English दोनों"):
        assert language_request(not_ask) is None, not_ask
    assert spoke_hindi("मुझे पाँच हज़ार pouch चाहिए") and not spoke_hindi("हाँ जी")


def test_english_call_stays_english_to_the_end(monkeypatch):
    """Asked once: every reply, the filler set, the voice language and the silence lines are English."""
    async def scenario():
        llm = ScriptedLLM(["Sure! What product do you make?", "Great. How many pouches a month?", "Okay."])
        s, t = await _session("+919470000006", llm, monkeypatch, silence_timeout_sec=0.8,
                              silence_final_wait_sec=5)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        await _say(s, "In English", NEAR)
        await _wait(lambda: len(llm.calls) == 1 and s.state == "listening")
        await _say(s, "हाँ", NEAR)  # a one-word Hindi answer doesn't switch back
        await _wait(lambda: len(llm.calls) == 2 and s.state == "listening")
        await _wait(lambda: "en_silence_prompt" in s.clips)
        # silence: the "are you there?" line is the English one
        await _wait(lambda: any(e.get("text") == ENGLISH_LINES["silence_prompt"] for e in t.events), timeout=6)
        run.cancel()
        return s, t, llm

    s, t, llm = asyncio.run(scenario())
    assert [m[-1]["content"] for m in llm.calls] == [ENGLISH_PREFERRED_NOTE, ENGLISH_PREFERRED_NOTE]
    assert s.english and s.tts_cfg["language_code"] == "en-IN"
    assert all("पाउच" not in str(r) for r in s.tts_cfg.get("pronunciations") or [])  # Hindi-spelled fixes dropped
    agent_lines = [e["text"] for e in t.events if e.get("type") == "transcript" and e.get("role") == "agent"]
    assert ENGLISH_LINES["silence_prompt"] in agent_lines
    assert any(k.startswith("en_filler_") for k in s.clips)


def test_customer_speaking_english_switches_the_call(monkeypatch):
    """Two English answers in a row: the call goes English (also for short answers), back on real Hindi."""
    async def scenario():
        llm = ScriptedLLM(["Sure.", "Great.", "Right.", "जी, बताइए।"])
        s, t = await _session("+919470000007", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        for i, text in enumerate(("Yes tell me what you do", "We make spices for export", "Yes",
                                  "मुझे पाँच हज़ार pouch चाहिए")):
            await _say(s, text, NEAR)
            await _wait(lambda: len(llm.calls) == i + 1 and s.state == "listening")
        run.cancel()
        return s, llm

    s, llm = asyncio.run(scenario())
    notes = [m[-1]["content"] if m[-1]["role"] == "system" else None for m in llm.calls]
    assert notes[1:3] == [ENGLISH_AUTO_NOTE, ENGLISH_AUTO_NOTE]  # "Yes" stays English
    assert notes[3] is None and not s.english  # a real Hindi sentence: back to the default


def test_english_voice_cfg_keeps_the_voice():
    cfg = dict(DEFAULT_AGENT_SETTINGS)
    en = english_voice_cfg(cfg)
    assert en["language_code"] == "en-IN" and en["voice"] == cfg["voice"] and cfg["language_code"] == "hi-IN"
