"""Human touches: fast replies (prefetch), listening sounds, quick "Hello?" replies, mood matching,
natural voice variation, even loudness and the extended pronunciation list."""
from __future__ import annotations

import asyncio
import math
import struct
import time

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.defaults import DEFAULT_AGENT_SETTINGS
from app.models import Call, Lead
from app.services import audio as audio_mod
from app.services import session as session_mod
from app.services.agent_config import upgrade_saved
from app.services.call_manager import manager
from app.services.prompts import (ENGLISH_LINES, HEAR_YOU_REPLY, HELLO_REPLY, MOOD_NOTES, detect_mood,
                                  hello_check)
from app.services.sarvam import speakable
from app.services.session import CallSession, english_voice_cfg

from .fakes import FakeSTT, FakeTransport, FakeTTSStream, ScriptedLLM, fake_tts_rest, tone

settings = get_settings()
REAL_PREFETCH = CallSession.__dict__["_maybe_prefetch"]  # conftest switches it off for scripted tests
NEAR = 9000


class PhoneTransport(FakeTransport):
    channel = "phone"
    in_rate = 8000
    out_rate = 8000
    stt_encoding = "mulaw"


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeSTT.instances.clear()
    FakeTTSStream.sentences.clear()
    FakeTTSStream.configs.clear()
    monkeypatch.setattr(session_mod, "RealtimeSTT", FakeSTT)
    monkeypatch.setattr(session_mod, "TTSStream", FakeTTSStream)
    monkeypatch.setattr(session_mod, "tts_rest", fake_tts_rest)
    yield


def _cfg(**over) -> dict:
    cfg = dict(DEFAULT_AGENT_SETTINGS)
    cfg.update({"silence_timeout_sec": 30, "silence_final_wait_sec": 5, "filler_delay_ms": 5000})
    cfg.update(over)
    return cfg


async def _session(phone: str, llm, monkeypatch, *, transport=None, **cfg_over):
    monkeypatch.setattr(session_mod, "stream_reply", llm)
    with SessionLocal() as db:
        lead = Lead(name="Kavya Rao", phone=phone, company="Rao Masala", city="Pune")
        db.add(lead)
        db.commit()
        lead_id = lead.id
    call, lead_d = await manager.create_browser_call(lead_id, None, None)
    with SessionLocal() as db:
        call_id = db.scalar(select(Call.id).where(Call.uuid == call["uuid"]))
    t = transport or FakeTransport()
    s = CallSession(call_id=call_id, call_uuid=call["uuid"], lead=lead_d, cfg=_cfg(**cfg_over), transport=t,
                    greeting_pcm=b"\x00\x00" * (t.out_rate // 5), on_finished=manager.session_finished)
    manager.register(s)
    return s, t


async def _wait(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


def _agent_lines(t) -> list[str]:
    return [e["text"] for e in t.events if e.get("type") == "transcript" and e.get("role") == "agent" and e.get("final")]


# ---------------------------------------------------------------- fast replies
def test_reply_starts_when_the_customer_stops_and_is_used_if_the_words_match(monkeypatch):
    monkeypatch.setattr(CallSession, "_maybe_prefetch", REAL_PREFETCH)

    async def scenario():
        llm = ScriptedLLM(["जी, पाँच हज़ार pouches, बढ़िया। कब तक चाहिए?"], delay=0.02)
        s, t = await _session("+919480000001", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.on_event("speech_start", {})
        await stt.on_event("partial", {"text": "मुझे पाँच हज़ार pouch चाहिए"})
        await stt.on_event("speech_end", {})
        assert s._prefetch is not None
        await _wait(lambda: len(llm.calls) == 1)  # writing before the final transcript
        await asyncio.sleep(0.3)  # the final arrives ~0.4 s later on a real call
        await stt.on_event("final", {"text": "मुझे पाँच हज़ार pouch चाहिए।"})
        await _wait(lambda: any("कब तक चाहिए" in x for x in _agent_lines(t)))
        run.cancel()
        return s, llm

    s, llm = asyncio.run(scenario())
    assert len(llm.calls) == 1 and s.prefetch_used == 1  # the early reply was used, no second request
    assert s.history[-2] == {"role": "user", "content": "मुझे पाँच हज़ार pouch चाहिए।"}  # the final words are kept


def test_early_reply_is_dropped_when_the_final_words_differ(monkeypatch):
    monkeypatch.setattr(CallSession, "_maybe_prefetch", REAL_PREFETCH)

    async def scenario():
        llm = ScriptedLLM(["(written for the partial)", "जी, rice के लिए stand-up pouch अच्छा रहेगा।"])
        s, t = await _session("+919480000002", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.on_event("speech_start", {})
        await stt.on_event("partial", {"text": "मुझे rice"})
        await stt.on_event("speech_end", {})
        await stt.on_event("final", {"text": "मुझे rice के लिए pouch चाहिए"})
        await _wait(lambda: any("stand-up" in x for x in _agent_lines(t)))
        run.cancel()
        return s, t, llm

    s, t, llm = asyncio.run(scenario())
    assert s.prefetch_used == 0 and len(llm.calls) == 2
    assert llm.calls[1][-1] == {"role": "user", "content": "मुझे rice के लिए pouch चाहिए"}
    assert not any("written for the partial" in x for x in _agent_lines(t))


def test_early_reply_is_dropped_when_the_customer_goes_on(monkeypatch):
    monkeypatch.setattr(CallSession, "_maybe_prefetch", REAL_PREFETCH)

    async def scenario():
        llm = ScriptedLLM(["(too early)", "जी, बताइए।"])
        s, t = await _session("+919480000003", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.on_event("speech_start", {})
        await stt.on_event("partial", {"text": "मुझे pouch चाहिए"})
        await stt.on_event("speech_end", {})
        pf = s._prefetch
        await stt.on_event("speech_start", {})  # they keep talking
        assert s._prefetch is None
        await asyncio.sleep(0.05)
        assert pf.task.cancelled() or pf.task.done()
        run.cancel()

    asyncio.run(scenario())


# ---------------------------------------------------------------- quick "Hello?" replies
def test_hello_mid_call_gets_an_instant_answer_in_the_call_language(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["जी, हम custom printed pouches बनाते हैं। आपका product क्या है?", "जी, बताइए।"])
        s, t = await _session("+919480000004", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.say("हाँ जी बताइए")
        await _wait(lambda: len(llm.calls) == 1 and s.state == "listening")
        await stt.say("हेलो हेलो")
        await _wait(lambda: HELLO_REPLY["hindi"] in _agent_lines(t))
        assert len(llm.calls) == 1  # no LLM round trip
        await _wait(lambda: s.state == "listening")
        await stt.say("मेरी आवाज़ आ रही है?")
        await _wait(lambda: HEAR_YOU_REPLY["hindi"] in _agent_lines(t))
        await _wait(lambda: s.state == "listening")
        await stt.say("हाँ आ रही है")  # an answer, not another check: the LLM picks the conversation up
        await _wait(lambda: len(llm.calls) == 2)
        run.cancel()
        return s, llm

    s, llm = asyncio.run(scenario())
    contents = [m["content"] for m in llm.calls[1] if m["role"] in ("user", "assistant")]
    assert "हेलो हेलो" in contents and HELLO_REPLY["hindi"] in contents  # the LLM knows what happened


def test_quick_replies_are_limited(monkeypatch):
    monkeypatch.setattr(session_mod, "QUICK_REPLY_ECHO_SEC", 0.0)  # each "Hello?" counts as a new check here

    async def scenario():
        llm = ScriptedLLM(["जी।"] * 3)
        s, t = await _session("+919480000005", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        for _ in range(3):
            await stt.say("Hello?")
            await _wait(lambda: s.state == "listening" and not s.responding())
            await asyncio.sleep(0.05)
        run.cancel()
        return t, llm

    t, llm = asyncio.run(scenario())
    assert _agent_lines(t).count(HELLO_REPLY["hindi"]) == 2 and len(llm.calls) == 1  # the 3rd goes to the LLM


def test_english_call_quick_reply_is_english(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["Sure, what do you make?"])
        s, t = await _session("+919480000006", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.say("Please talk in English")
        await _wait(lambda: len(llm.calls) == 1 and s.state == "listening")
        await stt.say("Hello? Are you there?")
        await _wait(lambda: ENGLISH_LINES["hello_reply"] in _agent_lines(t))
        run.cancel()

    asyncio.run(scenario())


def test_hello_check_and_mood_words():
    assert hello_check("Hello? Hello?") == "hello" and hello_check("कोई है?") == "hello"
    assert hello_check("Can you hear me?") == "hear_me"
    for normal in ("Hello, मुझे pouch चाहिए", "हाँ आवाज़ आ रही है", "Hi, I need pouches for spices", "हाँ"):
        assert hello_check(normal) is None, normal
    assert detect_mood("अभी meeting में हूँ, जल्दी बताइए") == "busy"
    assert detect_mood("बार बार call मत करो") == "irritated"
    assert detect_mood("हाहा, बहुत अच्छा") == "friendly"
    assert detect_mood("पाँच हज़ार चाहिए") is None


# ---------------------------------------------------------------- mood matching
def test_busy_customer_gets_short_calm_replies(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["जी, बस एक बात।", "जी।", "जी।", "जी।"])
        s, t = await _session("+919480000007", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.say("अभी meeting में हूँ, जल्दी बताइए")
        await _wait(lambda: len(llm.calls) == 1 and s.state == "listening")
        await asyncio.sleep(0.05)
        run.cancel()
        return s, llm

    s, llm = asyncio.run(scenario())
    assert s.mood == "busy" and {"role": "system", "content": MOOD_NOTES["busy"]} in llm.calls[0]
    # the next reply's voice is a touch quicker (busy), within the natural variation
    pace = FakeTTSStream.configs[-1]["tts_pace"]
    assert 1.05 * 0.97 - 0.001 <= pace <= 1.05 * 1.03 + 0.001


def test_brief_answers_get_brief_replies(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["जी।"] * 4)
        s, t = await _session("+919480000008", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        for i, text in enumerate(("चावल", "पाँच सौ", "नहीं पता")):
            await stt.say(text)
            await _wait(lambda: len(llm.calls) == i + 1 and s.state == "listening")
        run.cancel()
        return s, llm

    s, llm = asyncio.run(scenario())
    assert s.mood == "brief" and {"role": "system", "content": MOOD_NOTES["brief"]} in llm.calls[2]
    assert {"role": "system", "content": MOOD_NOTES["brief"]} not in llm.calls[1]


def test_mood_matching_can_be_switched_off(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["जी।"])
        s, t = await _session("+919480000009", llm, monkeypatch, mood_matching=False)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        await FakeSTT.instances[-1].say("बार बार call मत करो")
        await _wait(lambda: len(llm.calls) == 1)
        run.cancel()
        return s, llm

    s, llm = asyncio.run(scenario())
    assert s.mood is None and not any(m["content"] in MOOD_NOTES.values() for m in llm.calls[0])


# ---------------------------------------------------------------- natural variation
def test_voice_varies_a_little_per_reply_and_sentences_get_a_breath(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["जी, हम pouches बनाते हैं। आपका product क्या है?"])
        s, t = await _session("+919480000010", llm, monkeypatch)
        paces = {s._voice_cfg()["tts_pace"] for _ in range(20)}
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        start = len(t.audio)
        await FakeSTT.instances[-1].say("हाँ जी बताइए")
        await _wait(lambda: len(FakeTTSStream.sentences) == 2 and s.state == "listening")
        run.cancel()
        return paces, bytes(t.audio[start:]), t.out_rate

    paces, audio, rate = asyncio.run(scenario())
    assert len(paces) > 1 and all(0.97 - 0.001 <= p <= 1.03 + 0.001 for p in paces)
    # between the two sentences: a run of silence of 60-180 ms
    samples = struct.unpack(f"<{len(audio) // 2}h", audio)
    longest, run_len = 0, 0
    for x in samples:
        run_len = run_len + 1 if x == 0 else 0
        longest = max(longest, run_len)
    assert 0.05 * rate <= longest <= 0.2 * rate


# ---------------------------------------------------------------- listening sounds
def test_listening_sound_in_a_pause_of_a_long_sentence(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["जी, समझ गई।"])
        s, t = await _session("+919480000011", llm, monkeypatch, transport=PhoneTransport())
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening" and any(k.startswith("listen_") for k in s.clips))
        stt = FakeSTT.instances[-1]
        await stt.on_event("speech_start", {})
        await stt.on_event("partial", {"text": "हम लोग मसाले बनाते हैं और अभी हमें नए pouches चाहिए"})
        loud, quiet = tone(20, 8000, amp=NEAR), tone(20, 8000, amp=30)
        end = time.monotonic() + 3.2
        while time.monotonic() < end:  # 3.2 s of talking, in real time
            s.on_user_audio(loud)
            await asyncio.sleep(0.02)
        before = len(t.audio)
        for _ in range(12):  # a short breath (~0.24 s)
            s.on_user_audio(quiet)
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.05)
        played = len(t.audio) - before
        state, audible = s.state, s.agent_audible()
        run.cancel()
        return played, state, audible, s

    played, state, audible, s = asyncio.run(scenario())
    assert played > 0 and s._listening_in_segment == 1
    assert state == "listening" and not audible  # the customer keeps the floor


def test_no_listening_sound_for_short_answers(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["जी।"])
        s, t = await _session("+919480000012", llm, monkeypatch, transport=PhoneTransport())
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening" and any(k.startswith("listen_") for k in s.clips))
        stt = FakeSTT.instances[-1]
        await stt.on_event("speech_start", {})
        await stt.on_event("partial", {"text": "हाँ जी"})
        before = len(t.audio)
        for _ in range(30):
            s.on_user_audio(tone(20, 8000, amp=NEAR))
            await asyncio.sleep(0.02)
        for _ in range(12):
            s.on_user_audio(tone(20, 8000, amp=30))
            await asyncio.sleep(0.02)
        played = len(t.audio) - before
        run.cancel()
        return played

    assert asyncio.run(scenario()) == 0


# ---------------------------------------------------------------- even loudness
def _voiced_median(pcm: bytes, rate: int) -> int:
    import audioop
    frame = rate // 50 * 2
    levels = sorted(r for r in (audioop.rms(pcm[i:i + frame], 2) for i in range(0, len(pcm) - frame + 1, frame))
                    if r > 300)
    return levels[len(levels) // 2]


def test_prerecorded_lines_match_the_live_voice_loudness():
    loud = tone(1500, 8000, amp=12000)  # like Sarvam's REST voice: ~1.8x the stream
    out = audio_mod.match_loudness(loud, 8000)
    assert abs(_voiced_median(out, 8000) - audio_mod.STREAM_VOICE_LEVEL) < 150
    clipped = tone(500, 8000, amp=32000)
    assert max(abs(x) for x in struct.unpack(f"<{len(audio_mod.match_loudness(clipped, 8000)) // 2}h",
                                              audio_mod.match_loudness(clipped, 8000))) <= 29001
    quiet = tone(1500, 8000, amp=1500)
    assert _voiced_median(audio_mod.match_loudness(quiet, 8000), 8000) <= 1500 * 1.5 / math.sqrt(2) + 10


def test_sarvam_rest_voice_is_levelled(monkeypatch):
    from app.services import sarvam

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            import base64
            return {"audios": [base64.b64encode(audio_mod.wav_bytes(tone(1000, 8000, amp=12000), 8000)).decode()]}

    class Client:
        async def post(self, *a, **k):
            return Resp()

    monkeypatch.setattr(sarvam, "http", lambda: Client())
    monkeypatch.setattr(sarvam.settings, "sarvam_api_key", "x")
    pcm = asyncio.run(sarvam.tts_rest("loudness test line", dict(DEFAULT_AGENT_SETTINGS), 8000))
    assert abs(_voiced_median(pcm, 8000) - audio_mod.STREAM_VOICE_LEVEL) < 150


# ---------------------------------------------------------------- calm lines, pronunciations
def test_silence_lines_are_calm_and_pronunciations_cover_both_languages():
    assert "..." not in DEFAULT_AGENT_SETTINGS["silence_prompt_text"]
    assert ENGLISH_LINES["silence_prompt"] == "Hello, are you still on the line?"
    cfg = dict(DEFAULT_AGENT_SETTINGS)
    assert speakable("PouchWale का MOQ, GST और 5 kg", cfg) == "पाउचवाले का एम ओ क्यू, जी एस टी और पाँच किलो"
    assert speakable("PouchWale MOQ, GST and 5 kg", english_voice_cfg(cfg)) == "Pouch Waale M O Q, G S T and 5 kilo"


def test_settings_upgrade_adds_new_pronunciations_and_keeps_the_teams():
    saved = {"_version": 3, "pronunciations": ["PouchWale = पाउच वाले", "Surat = सूरत"],
             "silence_prompt_text": "हेलो...? मेरी आवाज़ आ रही है आपको?"}
    assert upgrade_saved(saved)
    rules = saved["pronunciations"]
    assert rules[:2] == ["PouchWale = पाउच वाले", "Surat = सूरत"]  # theirs first, unchanged
    assert "PouchWale = पाउचवाले" not in rules  # their own spelling of the word wins
    assert "GST = जी एस टी" in rules and "PouchWale = Pouch Waale" in rules
    assert saved["silence_prompt_text"] == DEFAULT_AGENT_SETTINGS["silence_prompt_text"]


# ---------------------------------------------------------------- live-call regressions (end-to-end run)
def test_reply_is_taken_back_when_the_customer_goes_on_after_a_pause(monkeypatch):
    """A comma pause closed the customer's sentence, the agent started a reply, and the customer went on: the
    agent must not start talking over them. It answers everything they said together."""
    async def scenario():
        llm = ScriptedLLM(["(about to talk over them)", "जी, हल्दी के लिए center seal pouch अच्छा रहता है।"],
                          delay=0.15)
        s, t = await _session("+919480000013", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.say("हम मसाले बनाते हैं हल्दी और मिर्ची")
        await _wait(lambda: s.responding() and len(llm.calls) == 1)
        assert not s._turn_first_audio
        s.on_user_audio(tone(20, 16000, amp=NEAR))  # they are talking again...
        await stt.on_event("speech_start", {})       # ...STT notices
        await _wait(lambda: not s.responding())
        await stt.say("तो आप कौन से pouch बनाते हो?", partial_first=False)
        await _wait(lambda: any("center seal" in x for x in _agent_lines(t)))
        run.cancel()
        return s, t, llm

    s, t, llm = asyncio.run(scenario())
    assert not any("talk over" in x for x in _agent_lines(t))
    assert llm.calls[-1][-1] == {"role": "user",
                                 "content": "हम मसाले बनाते हैं हल्दी और मिर्ची तो आप कौन से pouch बनाते हो?"}
    users = [m["content"] for m in s.history if m["role"] == "user"]
    assert users.count("हम मसाले बनाते हैं हल्दी और मिर्ची") == 0  # not left behind as a separate question


def test_split_hello_hello_is_answered_once(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["जी।"])
        s, t = await _session("+919480000014", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.say("Hello")
        await _wait(lambda: HELLO_REPLY["hindi"] in _agent_lines(t))
        await stt.say("Hello")  # the second half of "Hello? Hello?" (STT made two sentences of it)
        await asyncio.sleep(0.6)
        run.cancel()
        return t, llm

    t, llm = asyncio.run(scenario())
    assert _agent_lines(t).count(HELLO_REPLY["hindi"]) == 1 and len(llm.calls) == 0


def test_reply_starts_on_our_own_silence_detection_before_stt(monkeypatch):
    """Sarvam reports the end of speech ~0.5 s and the final ~0.7 s after the voice stops (measured); our own
    audio sees the pause after 0.3 s, so the reply starts writing then."""
    monkeypatch.setattr(CallSession, "_maybe_prefetch", REAL_PREFETCH)

    async def scenario():
        llm = ScriptedLLM(["जी, पाँच हज़ार, बढ़िया।"])
        s, t = await _session("+919480000015", llm, monkeypatch, transport=PhoneTransport())
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.on_event("speech_start", {})
        await stt.on_event("partial", {"text": "पाँच हज़ार pouch हर महीने"})
        for _ in range(20):
            s.on_user_audio(tone(20, 8000, amp=NEAR))
            await asyncio.sleep(0.02)
        for _ in range(20):  # 0.4 s of quiet; STT hasn't said anything yet
            s.on_user_audio(tone(20, 8000, amp=30))
            await asyncio.sleep(0.02)
        started = s._prefetch is not None
        await stt.on_event("speech_end", {})
        await stt.on_event("final", {"text": "पाँच हज़ार pouch हर महीने।"})
        await _wait(lambda: any("बढ़िया" in x for x in _agent_lines(t)))
        run.cancel()
        return started, s, llm

    started, s, llm = asyncio.run(scenario())
    assert started and s.prefetch_used == 1 and len(llm.calls) == 1


def test_no_early_reply_in_english_calls(monkeypatch):
    monkeypatch.setattr(CallSession, "_maybe_prefetch", REAL_PREFETCH)

    async def scenario():
        llm = ScriptedLLM(["Sure.", "Right, five thousand."])
        s, t = await _session("+919480000016", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.say("Please talk in English", partial_first=False)
        await _wait(lambda: len(llm.calls) == 1 and s.state == "listening")
        await stt.on_event("speech_start", {})
        await stt.on_event("partial", {"text": "Around फाइव थाउजेंड"})  # Sarvam's partials in an English call
        await stt.on_event("speech_end", {})
        assert s._prefetch is None
        run.cancel()

    asyncio.run(scenario())


def test_voice_out_of_credit_ends_the_call_politely(monkeypatch):
    """Sarvam ran out of credit mid-call on a live test: replies went half-silent. Now the ready-made "line
    problem, we'll call back" line (made at the start of the call) plays and the call ends."""
    from app.services.session import ERROR_LINE
    broken = {"on": False}

    class BrokeTTS(FakeTTSStream):
        async def send_sentence(self, text):
            if broken["on"]:
                raise RuntimeError("Credits exhausted. Add credits in the API Dashboard.")
            await super().send_sentence(text)

    async def broke_rest(text, cfg, rate):
        if broken["on"]:
            raise RuntimeError('Sarvam TTS 402: {"error":{"message":"No credits available."}}')
        return await fake_tts_rest(text, cfg, rate)

    monkeypatch.setattr(session_mod, "TTSStream", BrokeTTS)
    monkeypatch.setattr(session_mod, "tts_rest", broke_rest)

    async def scenario():
        llm = ScriptedLLM(["जी, बताइए आपका product क्या है?"])
        s, t = await _session("+919480000017", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening" and "error" in s.clips)
        broken["on"] = True
        await FakeSTT.instances[-1].say("हाँ जी बताइए")
        await _wait(lambda: s.ending, timeout=10)
        await _wait(lambda: run.done(), timeout=10)
        return s, t

    s, t = asyncio.run(scenario())
    assert s.end_reason == "error" and ERROR_LINE in _agent_lines(t)
    assert any("out of credit" in str(e.get("detail", "")) for e in t.events if e.get("type") == "error")
