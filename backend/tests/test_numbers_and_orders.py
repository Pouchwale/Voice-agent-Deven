"""Numbers spoken like a person, phone numbers never read out, pouch names said right, no order taking."""
from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.defaults import DEFAULT_AGENT_SETTINGS
from app.models import Call, Lead
from app.services import session as session_mod
from app.services.agent_config import upgrade_saved
from app.services.call_manager import manager
from app.services.numbers import hindi_number, speak_numbers_hindi
from app.services.prompts import lead_context, static_system_prompt
from app.services.sarvam import speakable
from app.services.session import CallSession, english_voice_cfg

from .fakes import FakeSTT, FakeTransport, FakeTTSStream, ScriptedLLM, fake_tts_rest

CFG = dict(DEFAULT_AGENT_SETTINGS)


@pytest.mark.parametrize("n,words", [(5, "पाँच"), (15, "पंद्रह"), (99, "निन्यानवे"), (199, "एक सौ निन्यानवे"),
                                     (1000, "एक हज़ार"), (10000, "दस हज़ार"), (150000, "एक लाख पचास हज़ार"),
                                     (20000000, "दो करोड़")])
def test_hindi_number_words(n, words):
    assert hindi_number(n) == words


@pytest.mark.parametrize("text,spoken", [
    ("क्या आप अभी भी 10,000 pouches की बात कर रहे हैं?", "क्या आप अभी भी दस हज़ार pouches की बात कर रहे हैं?"),
    ("क्या मैं आपसे 2 मिनट बात कर सकती हूँ?", "क्या मैं आपसे दो मिनट बात कर सकती हूँ?"),
    ("कल 3:30 बजे या 11:00 बजे?", "कल साढ़े तीन बजे या ग्यारह बजे?"),
    ("2:30 बजे, 1:30 बजे, 4:15 बजे, 4:45 बजे", "ढाई बजे, डेढ़ बजे, सवा चार बजे, पौने पाँच बजे"),
    ("size 10x12 inch", "size दस बाय बारह inch"),
    ("2.5 लाख, 20% कम", "ढाई लाख, बीस percent कम"),
])
def test_numbers_are_spoken_like_a_person(text, spoken):
    assert speak_numbers_hindi(text) == spoken


def test_phone_numbers_are_never_read_out():
    assert "9875" not in speakable("आपका WhatsApp नंबर +919875003040 है।", CFG)
    assert "9875" not in speakable("Your number is +91 98750 03040.", english_voice_cfg(CFG))
    assert speakable("call 98750-03040 today", english_voice_cfg(CFG)) == "call today"


def test_pouch_names_are_said_as_words_not_letters():
    assert speakable("Milk powder के लिए 3-side seal pouch या center seal pouch", CFG) == \
        "Milk powder के लिए थ्री साइड सील pouch या सेंटर सील pouch"
    assert speakable("quad seal bag, standup pouch, spout pouch", CFG) == "क्वाड सील bag, स्टैंड अप pouch, स्पाउट pouch"
    assert speakable("A 3-side seal pouch", english_voice_cfg(CFG)) == "A three side seal pouch"


def test_saved_pronunciations_get_the_new_rules():
    saved = {"_version": 5, "pronunciations": ["PouchWale = पाउचवाले", "₹199 = one ninety nine रुपये"]}
    assert upgrade_saved(saved)
    assert "3-side seal = थ्री साइड सील" in saved["pronunciations"]
    assert "3-side seal = three side seal" in saved["pronunciations"]


def test_agent_never_gets_the_digits_and_is_told_not_to_take_orders():
    ctx = lead_context({"name": "Deven", "phone": "+919875003040"}, "", direction="outbound")
    assert "9875003040" not in ctx and "never say any number aloud" in ctx
    p = static_system_prompt(CFG, "outbound", "outbound")
    assert "ORDERS:" in p and 'NEVER say "order confirm कर लिया"' in p
    assert "NEVER say a phone number" in p and "इसी number पर" in p


def test_a_phone_number_in_a_reply_is_replaced_before_it_is_spoken(monkeypatch):
    FakeSTT.instances.clear()
    FakeTTSStream.sentences.clear()
    monkeypatch.setattr(session_mod, "RealtimeSTT", FakeSTT)
    monkeypatch.setattr(session_mod, "TTSStream", FakeTTSStream)
    monkeypatch.setattr(session_mod, "tts_rest", fake_tts_rest)
    llm = ScriptedLLM(["बढ़िया! आपका WhatsApp नंबर +919875003040 है। हमारी team details भेज देगी।"])
    monkeypatch.setattr(session_mod, "stream_reply", llm)

    async def scenario():
        with SessionLocal() as db:
            lead = Lead(name="Deven", phone="+919875003099", company="D Foods")
            db.add(lead)
            db.commit()
            lead_id = lead.id
        call, lead_d = await manager.create_browser_call(lead_id, None, None)
        with SessionLocal() as db:
            call_id = db.scalar(select(Call.id).where(Call.uuid == call["uuid"]))
        t = FakeTransport()
        s = CallSession(call_id=call_id, call_uuid=call["uuid"], lead=lead_d, cfg=dict(CFG), transport=t,
                        greeting_pcm=b"\x00\x00" * 4800, on_finished=manager.session_finished)
        manager.register(s)
        run = asyncio.create_task(s.run())
        end = time.monotonic() + 8
        while not (s.greeted and s.state == "listening") and time.monotonic() < end:
            await asyncio.sleep(0.02)
        await FakeSTT.instances[-1].say("हाँ यही number WhatsApp है")
        end = time.monotonic() + 8
        while len(FakeTTSStream.sentences) < 2 and time.monotonic() < end:
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.2)
        run.cancel()
        return s

    s = asyncio.run(scenario())
    said = " ".join(FakeTTSStream.sentences)
    assert "9875" not in said and "आपका WhatsApp नंबर यही है।" in said
    assert "9875" not in s.history[-1]["content"]
