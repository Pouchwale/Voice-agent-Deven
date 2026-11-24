"""Call 74 (2026-11-24) did not sound human to the user:
- she changed tone on English words ("pieces", "start", "quality"): a Hindi voice reads English letters in another voice;
- the fillers sounded like someone else: "अच्छा..." came out at 320 Hz against her 200-220 Hz, drawn out, and every
  ready-made clip was about 3 dB quieter than her replies (levelled for the older v3 voices);
- "अच्छा जी" played right before her goodbye;
- "अरे वाह" sounded put on;
- after a filler, "अच्छा सवाल है!" lost its first word and became "सवाल है!".
"""
from __future__ import annotations

import asyncio
import base64

import pytest

from app.defaults import DEFAULT_AGENT_SETTINGS
from app.services import audio as audio_mod
from app.services import sarvam
from app.services import session as session_mod
from app.services.hindi_words import english_in_devanagari
from app.services.prompts import static_system_prompt
from app.services.session import ends_the_conversation, no_wow

from .fakes import FakeSTT, ScriptedLLM
from .test_humanize_live import _session, _voiced_median, _wait, fakes, tone  # noqa: F401  (autouse fixture)

SARIKA = {**DEFAULT_AGENT_SETTINGS, "voice": "sarika_hi_conversation", "language_code": "hi-IN"}


# ---------------------------------------------------------------- English words for a Hindi voice
@pytest.mark.parametrize("said, spoken", [
    ("MOQ एक हज़ार pieces से start होता है।", "एम ओ क्यू एक हज़ार पीसेस से स्टार्ट होता है।"),
    ("dry fruits के लिए stand-up pouch best रहेगा।", "ड्राई फ़्रूट्स के लिए स्टैंड-अप पाउच बेस्ट रहेगा।"),
    ("FSSAI का logo आपको देना होगा।", "एफ़ एस एस ए आई का लोगो आपको देना होगा।"),
    ("UPI से payment हो जाएगा।", "यू पी आई से पेमेंट हो जाएगा।"),  # a short code not in the list is spelled out
    ("Rahul जी, thank you!", "Rahul जी, थैंक यू!"),               # a name stays as it is
    ("Sure, our expert will call you.", "Sure, our expert will call you."),  # an English sentence is read as English
])
def test_english_words_in_a_hindi_sentence_are_written_in_hindi(said, spoken):
    assert english_in_devanagari(said) == spoken


def test_only_a_hindi_voice_gets_them_in_hindi_script():
    line = "हमारी team आपको quotation भेज देगी।"
    assert sarvam.speakable(line, SARIKA) == "हमारी टीम आपको कोटेशन भेज देगी।"
    assert "team" in sarvam.speakable(line, {**SARIKA, "voice": "ishita_enhi_customer"})  # Hinglish voices: as written
    assert "team" in sarvam.speakable(line, {**SARIKA, "language_code": "en-IN"})           # English calls: as written
    spoken = sarvam.speakable("PouchWale का MOQ एक हज़ार है।", SARIKA)
    assert spoken.startswith(sarvam.BRAND_SPOKEN_DEVANAGARI) and "MOQ" not in spoken


def test_the_list_covers_her_words_on_real_calls():
    """442 Hindi replies from real calls: 2,699 English-letter words before, 151 after (names and the brand, which
    has its own rule)."""
    from app.services.hindi_words import WORDS
    for w in ("pouch", "pouches", "packaging", "quantity", "pieces", "quality", "sample", "kit", "whatsapp", "team",
              "expert", "details", "order", "design", "custom", "printed", "seal", "standup", "zipper", "shipping"):
        assert w in WORDS, w


# ---------------------------------------------------------------- fillers and ready-made clips
def test_ready_made_clips_are_as_loud_as_a_v4_voice(monkeypatch):
    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"audios": [base64.b64encode(audio_mod.wav_bytes(tone(1000, 8000, amp=12000), 8000)).decode()]}

    class Client:
        async def post(self, *a, **k):
            return Resp()

    monkeypatch.setattr(sarvam, "http", lambda: Client())
    monkeypatch.setattr(sarvam.settings, "sarvam_api_key", "x")
    pcm = asyncio.run(sarvam.tts_rest("loudness test v4 line", SARIKA, 8000))
    assert abs(_voiced_median(pcm, 8000) - audio_mod.V4_VOICE_LEVEL) < 150


def test_fillers_are_said_plainly_not_drawn_out(monkeypatch):
    seen: list[str] = []

    async def fake_rest(text, cfg, rate):
        seen.append(text)
        return bytes([16, 39]) * (rate // 2)

    async def scenario():
        s, _ = await _session("+919480002001", ScriptedLLM([]), monkeypatch)
        monkeypatch.setattr(session_mod, "tts_rest", fake_rest)
        await s._prepare_clips()
        return s

    s = asyncio.run(scenario())
    fillers = [s._filler_texts[k] for k in s._filler_texts if k.startswith("filler_")]
    assert fillers and all(f + "।" in seen and f + "..." not in seen for f in fillers)
    assert "हम्म, एक second" not in DEFAULT_AGENT_SETTINGS["filler_words"]


@pytest.mark.parametrize("said, her_last_line, closing", [
    ("नहीं नहीं ठीक है मैम।", "हमारी team details भेज देगी। कोई और पूछना है?", True),
    ("ठीक है जी", "और कुछ जानना है आपको?", True),
    ("thank you madam", "", True),
    ("चलो phone रख दो", "", True),
    ("नहीं, rate क्या है?", "कोई और पूछना है?", False),
    ("नहीं", "आपका product क्या है?", False),
    ("हाँ, एक सवाल है", "कोई और पूछना है?", False),
    ("जी", "कोई और पूछना है?", False),  # could be "yes, I have": the filler may still play
])
def test_what_closes_the_conversation(said, her_last_line, closing):
    assert ends_the_conversation(said, her_last_line) is closing


def test_no_filler_right_before_her_goodbye(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["जी, MOQ एक हज़ार pieces से start होता है। कोई और पूछना है?", "आपका time देने के लिए thank you!"],
                          delay=0.15)
        s, _ = await _session("+919480002002", llm, monkeypatch, filler_delay_ms=200, silence_timeout_sec=30)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening" and any(k.startswith("filler_") for k in s.clips))
        stt = FakeSTT.instances[-1]
        await stt.say("आपका MOQ कितना है?")
        await _wait(lambda: len(llm.calls) == 1 and s.state == "listening", timeout=8)
        first = s._filler_played
        await stt.say("नहीं नहीं ठीक है मैम।")
        await _wait(lambda: len(llm.calls) == 2 and s.state == "listening", timeout=8)
        second = s._filler_played
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return first, second

    first, second = asyncio.run(scenario())
    assert first is True      # a slow answer still gets its filler
    assert second is False    # the goodbye does not


def test_a_filler_never_cuts_her_sentence(monkeypatch):
    async def scenario():
        s, _ = await _session("+919480002003", ScriptedLLM([]), monkeypatch)
        return s

    s = asyncio.run(scenario())
    s.cfg["filler_words"] = ["हम्म", "जी", "अच्छा", "हाँ जी", "अच्छा जी"]
    assert s._strip_leading_filler("अच्छा सवाल है! variants मतलब flavours।") == "अच्छा सवाल है! variants मतलब flavours।"
    assert s._strip_leading_filler("जी हाँ, बिल्कुल।") == "जी हाँ, बिल्कुल।"
    assert s._strip_leading_filler("अच्छा, MOQ एक हज़ार है।") == "MOQ एक हज़ार है।"
    assert s._strip_leading_filler("अच्छा जी, MOQ एक हज़ार है।") == "MOQ एक हज़ार है।"
    assert s._strip_leading_filler("जी।") == "जी।"


# ---------------------------------------------------------------- "अरे वाह"
@pytest.mark.parametrize("said, out", [
    ("अरे वाह, dry fruits के लिए!", "अच्छा, dry fruits के लिए!"),
    ("अरे, वाह! बढ़िया।", "अच्छा, बढ़िया।"),
    ("वाह जी! पाँच हज़ार pieces।", "अच्छा, पाँच हज़ार pieces।"),
    ("अरे वाह!", "अच्छा।"),
    ("अरे वाह,", "अच्छा,"),
    ("वाहन के लिए pouch चाहिए?", "वाहन के लिए pouch चाहिए?"),
    ("जी, standup pouch सही रहेगा।", "जी, standup pouch सही रहेगा।"),
])
def test_she_never_says_arre_waah(said, out):
    assert no_wow(said) == out


def test_a_spoken_reply_never_carries_it(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["अरे वाह, dry fruits के लिए! तो standup pouch बढ़िया रहेगा।", "अरे वाह बढ़िया"])
        s, _ = await _session("+919480002004", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        for i, said in enumerate(("हमारा dry fruits का काम है", "दस हज़ार pieces चाहिए"), 1):
            await stt.say(said)
            await _wait(lambda: len(llm.calls) == i and s.state == "listening")
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return [m["content"] for m in s.history if m["role"] == "assistant"][-2:]

    first, second = asyncio.run(scenario())
    assert first.startswith("अच्छा, dry fruits के लिए!") and "वाह" not in first
    assert second == "अच्छा, बढ़िया"  # the last words, sent without a full stop


def test_the_prompt_no_longer_offers_arre_waah():
    p = static_system_prompt(dict(DEFAULT_AGENT_SETTINGS), "outbound", None)
    assert 'or "अरे वाह" ONLY' not in p and 'Never say "अरे वाह" or "वाह"' in p


# ---------------------------------------------------------------- voices
def test_the_hinglish_voices_can_be_chosen():
    models = {v["id"]: v["model"] for v in sarvam.ALL_VOICES}
    for v in ("ishita_enhi_customer", "shalini_enhi_customer", "simran_enhi_customer"):
        assert models[v] == sarvam.V4_MODEL and sarvam.model_for(v) == sarvam.V4_MODEL
