"""End-to-end conversation engine tests with scripted STT / LLM / TTS."""
from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.defaults import DEFAULT_AGENT_SETTINGS
from app.models import Call, Lead, TranscriptTurn
from app.services import analysis as analysis_mod
from app.services import session as session_mod
from app.services.call_manager import manager
from app.services.session import CallSession, is_backchannel

from .fakes import FakeSTT, FakeTransport, FakeTTSStream, ScriptedLLM, fake_tts_rest


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeSTT.instances.clear()
    FakeTTSStream.sentences.clear()
    monkeypatch.setattr(session_mod, "RealtimeSTT", FakeSTT)
    monkeypatch.setattr(session_mod, "TTSStream", FakeTTSStream)
    monkeypatch.setattr(session_mod, "tts_rest", fake_tts_rest)
    yield


def _cfg(**over) -> dict:
    cfg = dict(DEFAULT_AGENT_SETTINGS)
    cfg.update({"silence_timeout_sec": 1.0, "silence_final_wait_sec": 0.5, "filler_delay_ms": 5000})
    cfg.update(over)
    return cfg


def _lead(phone: str) -> int:
    with SessionLocal() as db:
        lead = Lead(name="Rahul Sharma", phone=phone, company="Sharma Snacks", city="Ahmedabad")
        db.add(lead)
        db.commit()
        return lead.id


async def _new_session(lead_id: int, cfg: dict, llm: ScriptedLLM, monkeypatch) -> tuple[CallSession, FakeTransport]:
    monkeypatch.setattr(session_mod, "stream_reply", llm)
    call, lead = await manager.create_browser_call(lead_id, None, None)
    with SessionLocal() as db:
        call_id = db.scalar(select(Call.id).where(Call.uuid == call["uuid"]))
    t = FakeTransport()
    s = CallSession(call_id=call_id, call_uuid=call["uuid"], lead=lead, cfg=cfg, transport=t,
                    on_finished=manager.session_finished)
    manager.register(s)
    return s, t


async def _wait(cond, timeout=8.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(step)
    raise AssertionError("condition not met in time")


def _turns(call_uuid: str) -> list[tuple[str, str, bool]]:
    with SessionLocal() as db:
        call = db.scalar(select(Call).where(Call.uuid == call_uuid))
        return [(t.role, t.text, t.interrupted) for t in db.scalars(
            select(TranscriptTurn).where(TranscriptTurn.call_id == call.id).order_by(TranscriptTurn.id))]


def _call(call_uuid: str) -> Call:
    with SessionLocal() as db:
        return db.scalar(select(Call).where(Call.uuid == call_uuid))


# ---------------------------------------------------------------- tests
def test_full_conversation_end_call_recording_and_scoring(monkeypatch):
    analysis_result = {
        "summary": "Rice brand needs 5000 stand-up pouches within 2 weeks; wants a quote.",
        "rating": 8, "interest": "hot", "qualified": True, "sentiment": "positive",
        "next_action": "BDE to share quote for 5000 zipper pouches today",
        "callback_time": None,
        "qualification": [{"question": "Product", "answer": "Rice", "status": "answered"}],
        "key_points": ["Rice", "5000 pcs", "2 weeks"], "objections": [],
    }

    async def fake_json(messages, max_tokens=None):
        assert "CUSTOMER:" in messages[1]["content"]
        return analysis_result

    monkeypatch.setattr(analysis_mod, "complete_json", fake_json)

    async def scenario():
        lead_id = _lead("+919800000001")
        llm = ScriptedLLM([
            "अच्छा जी, आप किस product के लिए pouch चाहते हैं?",
            "बहुत बढ़िया! हमारे packaging expert आज ही आपको quote भेजेंगे। धन्यवाद! [END_CALL]",
        ])
        s, t = await _new_session(lead_id, _cfg(), llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening" and len(t.audio) > 0)
        greeting_audio = len(t.audio)
        stt = FakeSTT.instances[-1]
        # customer audio flows into STT + recorder
        s.on_user_audio(b"\x00\x00" * 1600)
        assert stt.audio_bytes == 3200

        await stt.say("हाँ जी बताइए, हमें pouch चाहिए")
        await _wait(lambda: len(t.audio) > greeting_audio and s.state == "listening", timeout=6)
        await stt.say("rice के लिए, पाँच हज़ार pieces, दो हफ़्ते में")
        await asyncio.wait_for(run, timeout=8)
        # post-call analysis runs as a background task in this loop
        await _wait(lambda: _call(s.uuid).analysis_status == "done", timeout=5)
        return s, t, llm

    s, t, llm = asyncio.run(scenario())
    assert s.end_reason == "agent_ended"
    assert t.closed_reason == "agent_ended"
    # the LLM saw the greeting + the customer's words in order
    roles = [m["role"] for m in llm.calls[1]]
    assert roles[0] == "system" and roles[-1] == "user"
    assert "[END_CALL]" not in " ".join(FakeTTSStream.sentences)  # marker never spoken

    turns = _turns(s.uuid)
    assert [r for r, _, _ in turns] == ["agent", "user", "agent", "user", "agent"]
    assert turns[0][1].startswith("नमस्ते Rahul जी")
    assert "[END_CALL]" not in turns[-1][1]

    call = _call(s.uuid)
    assert (call.rating, call.interest, call.qualified) == (8, "hot", True)
    assert call.qualification[0]["answer"] == "Rice"
    assert call.status == "completed" and call.end_reason == "agent_ended"
    assert call.duration_sec and call.duration_sec > 0
    assert call.recording_path and session_mod.recording_abs_path(call.recording_path) is not None
    assert call.metrics["turns"] == 2 and call.metrics["user_words"] > 0
    assert "avg_latency_ms" in call.metrics
    with SessionLocal() as db:
        lead = db.get(Lead, call.lead_id)
        assert (lead.interest, lead.rating, lead.status) == ("hot", 8, "called")


def test_silence_prompt_then_goodbye_then_hangup(monkeypatch):
    async def scenario():
        lead_id = _lead("+919800000002")
        s, t = await _new_session(lead_id, _cfg(), ScriptedLLM([]), monkeypatch)
        t0 = time.monotonic()
        await asyncio.wait_for(s.run(), timeout=10)
        return s, t, time.monotonic() - t0

    s, t, elapsed = asyncio.run(scenario())
    assert s.end_reason == "silence_timeout"
    cfg = _cfg()
    texts = [txt for role, txt, _ in _turns(s.uuid) if role == "agent"]
    assert texts[1] == cfg["silence_prompt_text"]
    assert texts[2] == cfg["silence_goodbye_text"]
    # greeting(~0.9s wait + 0.2s audio) + 1s silence + prompt 0.2s + 0.5s + goodbye 0.2s
    assert 2.5 < elapsed < 6, elapsed


def test_customer_reply_resets_silence_timer(monkeypatch):
    async def scenario():
        lead_id = _lead("+919800000003")
        s, t = await _new_session(lead_id, _cfg(silence_timeout_sec=1.5), ScriptedLLM(["ठीक है जी।"]), monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        await asyncio.sleep(1.0)
        await FakeSTT.instances[-1].say("हाँ बोलिए")
        await asyncio.sleep(0.8)  # would have passed the original 1.5 s deadline
        assert not s.ending
        await asyncio.wait_for(run, timeout=10)
        return s

    s = asyncio.run(scenario())
    assert s.end_reason == "silence_timeout"
    agent = [txt for role, txt, _ in _turns(s.uuid) if role == "agent"]
    assert agent[1] == "ठीक है जी।" and agent[2] == _cfg()["silence_prompt_text"]


def test_barge_in_stops_agent_but_backchannel_does_not(monkeypatch):
    FakeTTSStream.ms_per_sentence = 1500

    async def scenario():
        lead_id = _lead("+919800000004")
        llm = ScriptedLLM([
            "हमारे पास stand-up pouch है। Zipper pouch भी है। Spout pouch भी है।",
            "जी बिल्कुल, बताइए।",
        ])
        s, t = await _new_session(lead_id, _cfg(silence_timeout_sec=30), llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.say("कौन से pouch हैं?")
        await _wait(lambda: s.state == "speaking" and s.agent_audible(), timeout=4)
        # short backchannel: keep talking
        await stt.on_event("partial", {"text": "हाँ"})
        assert t.clears == 0 and s.agent_audible()
        # real interruption
        await stt.on_event("speech_start", {})
        await stt.on_event("partial", {"text": "रुकिए रुकिए एक minute"})
        assert t.clears == 1
        assert not s.agent_audible()
        await stt.on_event("final", {"text": "रुकिए रुकिए एक minute"})
        await _wait(lambda: len(llm.calls) == 2, timeout=4)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return s, llm

    try:
        s, llm = asyncio.run(scenario())
    finally:
        FakeTTSStream.ms_per_sentence = 300
    turns = _turns(s.uuid)
    interrupted = [txt for role, txt, intr in turns if role == "agent" and intr]
    assert interrupted and "stand-up pouch" in interrupted[0]
    assert llm.calls[1][-1] == {"role": "user", "content": "रुकिए रुकिए एक minute"}
    assert _call(s.uuid).end_reason == "manual_hangup"


def test_filler_plays_when_reply_is_slow(monkeypatch):
    async def scenario():
        lead_id = _lead("+919800000005")
        llm = ScriptedLLM(["हम्म, अच्छा सवाल है। हम custom design भी करते हैं।"], delay=0.12)
        s, t = await _new_session(lead_id, _cfg(filler_delay_ms=300, silence_timeout_sec=30), llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening" and any(k.startswith("filler_") for k in s.clips))
        await FakeSTT.instances[-1].say("आप design भी करते हो?")
        await _wait(lambda: len(FakeTTSStream.sentences) >= 1, timeout=5)
        filler = s._filler_played
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return filler

    assert asyncio.run(scenario()) is True
    # the reply's own leading "हम्म" is dropped after a filler already played
    assert not FakeTTSStream.sentences[0].startswith("हम्म")


def test_backchannel_rules():
    assert is_backchannel("हाँ")
    assert is_backchannel("हाँ जी")
    assert is_backchannel("ok")
    assert is_backchannel("Hello?", greeting=True)
    assert not is_backchannel("Hello?")
    assert not is_backchannel("नहीं चाहिए")
    assert not is_backchannel("price kitna hai")
