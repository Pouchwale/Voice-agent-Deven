"""Human-sounding agent: fillers, turn-taking, pronunciation, prompt rules, settings upgrade."""
from __future__ import annotations

import asyncio
import copy

from fastapi.testclient import TestClient

from app.defaults import DEFAULT_AGENT_SETTINGS, DROPPED_FILLERS, LEGACY_DEFAULTS, SETTINGS_VERSION
from app.main import app
from app.services import session as session_mod
from app.services.agent_config import upgrade_saved
from app.services.prompts import build_system_prompt
from app.services.sarvam import speakable
from app.services.session import is_question, sounds_unfinished

from .fakes import FakeSTT, FakeTTSStream, ScriptedLLM, fake_tts_rest
from .test_session import _cfg, _lead, _new_session, _wait

LOCAL = ("127.0.0.1", 50000)


def test_default_fillers_are_hindi_and_neutral():
    words = DEFAULT_AGENT_SETTINGS["filler_words"]
    assert words and not any(w.strip().lower() in DROPPED_FILLERS for w in words)
    joined = " ".join(words).lower()
    for banned in ("ओके", "ओह", "okay", "got it", "yeah", "बिल्कुल"):  # बिल्कुल would agree before the reply exists
        assert banned not in joined, banned
    for line in ("opening_line", "closing_line", "silence_prompt_text", "silence_goodbye_text"):
        assert DEFAULT_AGENT_SETTINGS[line] not in LEGACY_DEFAULTS[line]


def test_prompt_is_warm_female_and_bans_foreign_fillers():
    sp = build_system_prompt(dict(DEFAULT_AGENT_SETTINGS), {"name": "Rahul"})
    assert "feminine" in sp and "never masculine" in sp
    assert '"ओह, got it"' not in sp            # no longer offered as an example
    assert 'NEVER say "ओह"' in sp and '"okay"' in sp
    assert "repeat the key detail back" in sp
    male = build_system_prompt({**DEFAULT_AGENT_SETTINGS, "agent_gender": "male"}, None)
    assert "masculine" in male and "young Indian man" in male


def test_english_turns_get_an_english_reply_note():
    from app.services.prompts import spoke_english

    assert spoke_english("Yes, tell me. What do you do?")
    assert spoke_english("We make spices for export")
    for hinglish in ("price kitna hai aapka?", "हाँ बोलिए", "हमें pouch चाहिए", "ok sure", "price कितना है"):
        assert not spoke_english(hinglish), hinglish


def test_turn_taking_helpers():
    for unfinished in ("मुझे pouch चाहिए और", "हमें चाहिए, लेकिन", "मतलब rice के", "we need pouches and",
                       "हाँ तो...", "देखिए,"):
        assert sounds_unfinished(unfinished), unfinished
    for done in ("मुझे पाँच हज़ार pouch चाहिए", "price कितना है?", "नहीं चाहिए", "", "हाँ जी"):
        assert not sounds_unfinished(done), done
    assert is_question("price कितना है") and is_question("What is the MOQ") and is_question("आप design करते हो?")
    assert not is_question("हमें rice के लिए चाहिए")


def test_speakable_applies_pronunciations_only_to_audio():
    cfg = dict(DEFAULT_AGENT_SETTINGS)
    assert speakable("मैं PouchWale से बोल रही हूँ।", cfg) == "मैं पाउचवाले से बोल रही हूँ।"
    assert speakable("pouchwale का MOQ", cfg) == "पाउचवाले का एम ओ क्यू"
    assert speakable("PouchWales", cfg) == "PouchWales"            # whole words only
    assert "*" not in speakable("**जी**, बिल्कुल", cfg)
    assert speakable("PouchWale", {**cfg, "pronunciations": ["PouchWale -> पाउच वाले", "junk line"]}) == "पाउच वाले"


def test_settings_upgrade_replaces_only_untouched_defaults():
    legacy = {**copy.deepcopy(DEFAULT_AGENT_SETTINGS), **{k: copy.deepcopy(v[0]) for k, v in LEGACY_DEFAULTS.items()}}
    legacy.pop("pronunciations")                      # rows saved before the field existed
    legacy["closing_line"] = "हमारी team का custom closing।"  # customised: must survive
    assert upgrade_saved(legacy) is True
    assert legacy["filler_words"] == DEFAULT_AGENT_SETTINGS["filler_words"]
    assert legacy["opening_line"] == DEFAULT_AGENT_SETTINGS["opening_line"]
    assert legacy["filler_delay_ms"] == DEFAULT_AGENT_SETTINGS["filler_delay_ms"]
    assert legacy["closing_line"] == "हमारी team का custom closing।"
    assert legacy["_version"] == SETTINGS_VERSION
    assert upgrade_saved(legacy) is False             # one-time only

    custom = {"filler_words": ["हम्म", "ओके", "Okay", "ओह, got it", "जी"]}
    upgrade_saved(custom)
    assert custom["filler_words"] == ["हम्म", "जी"]   # the team's own fillers stay, foreign ones go
    only_bad = {"filler_words": ["ओके"]}
    upgrade_saved(only_bad)
    assert only_bad["filler_words"] == DEFAULT_AGENT_SETTINGS["filler_words"]


def test_v4_voice_uses_its_own_model_and_no_temperature(monkeypatch):
    from app.services import sarvam

    sent: list[dict] = []

    class FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return {"audios": []}

    class FakeHTTP:
        async def post(self, path, json, headers):
            sent.append(json)
            return FakeResp()

    monkeypatch.setattr(sarvam, "http", lambda: FakeHTTP())
    monkeypatch.setattr(sarvam.settings, "sarvam_api_key", "x")
    cfg = {**DEFAULT_AGENT_SETTINGS}
    asyncio.run(sarvam.tts_rest("जी, नमस्ते v4", {**cfg, "voice": "aparna_hi_customer"}, 8000))
    asyncio.run(sarvam.tts_rest("जी, नमस्ते v3", {**cfg, "voice": "suhani"}, 8000))
    v4, v3 = sent
    assert v4["model"] == "bulbul:v4-flash" and v4["speaker"] == "aparna_hi_customer" and "temperature" not in v4
    assert v3["model"] == "bulbul:v3" and v3["temperature"] == 0.7
    assert sarvam.model_for("suhani") == "bulbul:v3" and sarvam.model_for("ritu_hi_sales") == "bulbul:v4-flash"
    with TestClient(app, client=LOCAL) as c:
        assert c.put("/api/settings", json={"voice": "aparna_hi_customer"}).status_code == 200
        assert c.get("/api/settings/system").json()["tts_model"] == "bulbul:v4-flash"
        assert c.put("/api/settings", json={"voice": "suhani"}).status_code == 200


def test_pronunciation_setting_is_validated():
    with TestClient(app, client=LOCAL) as c:
        r = c.put("/api/settings", json={"pronunciations": ["PouchWale"]})
        assert r.status_code == 422 and "word = how to say it" in r.json()["detail"]
        r = c.put("/api/settings", json={"pronunciations": ["PouchWale = पाउचवाले", "  ", "GP3 = जी पी थ्री"]})
        assert r.status_code == 200 and r.json()["pronunciations"] == ["PouchWale = पाउचवाले", "GP3 = जी पी थ्री"]


# ---------------------------------------------------------------- live session behaviour
def _patch(monkeypatch):
    FakeSTT.instances.clear()
    FakeTTSStream.sentences.clear()
    monkeypatch.setattr(session_mod, "RealtimeSTT", FakeSTT)
    monkeypatch.setattr(session_mod, "TTSStream", FakeTTSStream)
    monkeypatch.setattr(session_mod, "tts_rest", fake_tts_rest)


def test_filler_fits_the_moment_and_never_repeats(monkeypatch):
    _patch(monkeypatch)

    async def scenario():
        s, _ = await _new_session(_lead("+919800000301"), _cfg(), ScriptedLLM([]), monkeypatch)
        await s._prepare_clips()
        thinking = {k for k, w in s._filler_texts.items() if "हम्म" in w or "second" in w}
        assert thinking and len(thinking) < len(s._filler_texts)
        picks = []
        for _ in range(12):
            s._pick_filler("price कितना है?")
            picks.append(s._recent_fillers[-1])
        assert set(picks) <= thinking                       # questions get a thinking sound
        assert all(a != b for a, b in zip(picks, picks[1:]))  # never twice in a row
        s._pick_filler("हमें rice के लिए pouch चाहिए")
        assert s._recent_fillers[-1] not in thinking        # statements get an acknowledgement
        await s.end("manual_hangup")

    asyncio.run(scenario())


def test_trailing_sentence_gets_a_moment_to_finish(monkeypatch):
    _patch(monkeypatch)

    async def scenario():
        llm = ScriptedLLM(["अच्छा जी, rice के लिए। कितनी quantity चाहिए होगी?"])
        s, _ = await _new_session(_lead("+919800000302"), _cfg(silence_timeout_sec=30), llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.say("हमें pouch चाहिए, और")
        await asyncio.sleep(0.35)
        assert not llm.calls                                # did not jump in mid-thought
        await stt.say("rice के लिए")                         # ...the customer goes on
        await _wait(lambda: len(llm.calls) == 1, timeout=4)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return llm

    llm = asyncio.run(scenario())
    assert llm.calls[0][-1] == {"role": "user", "content": "हमें pouch चाहिए, और rice के लिए"}


def test_language_requests():
    from app.services.prompts import language_request

    for ask in ("Can you speak in English?", "English please", "please talk in english", "इंग्लिश में बोलिए",
                "अंग्रेज़ी में बात कीजिए", "english me bolo"):
        assert language_request(ask) == "english", ask
    for ask in ("हिंदी में बोलिए", "Hindi please", "can you speak hindi"):
        assert language_request(ask) == "hindi", ask
    for not_ask in ("हमें English labels चाहिए", "No No", "हाँ ठीक है", "Hindi English दोनों", ""):
        assert language_request(not_ask) is None, not_ask
