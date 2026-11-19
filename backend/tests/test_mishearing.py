"""A misheard word is asked about, never built on (call 49: "3-5 silk out" became "3 से 5 thousand pouches",
"1 माई" became "एक मई तक", "Masjid" was saved as the customer's city)."""
from __future__ import annotations

import asyncio

from app.defaults import DEFAULT_AGENT_SETTINGS
from app.services.prompts import build_analysis_prompt, misheard_note, static_system_prompt

from .fakes import FakeSTT, ScriptedLLM
from .test_humanize_live import _session, _wait, fakes  # noqa: F401  (fakes: autouse fixture)


def test_typical_mishearings_are_flagged_with_their_likely_meaning():
    cases = {
        "हम्म Yes 3-5 silk out चाहिए मेरे को।": '"seal"',
        "हाँ 1 माई।": '"minute"',
        "एक माई में चाहिए": '"minute"',
        "Masjid": "मतलब",
        "Stand up field. Stand up field.": '"pouch"',
        "Madam power pouch की बात कर रहा हूँ मैं": '"spout pouch"',
        "Okay, but हमें 3 side wheel नहीं चाहिए।": '"seal"',
        "5,000 rescue": '"per month"',
        "बट मैं 3 side सिल सोच रहा हूं।": '"seal"',
    }
    for said, meant in cases.items():
        note = misheard_note(said)
        assert note and meant in note, said


def test_normal_words_are_not_flagged():
    for said in ("मुझे पाँच हज़ार pouches चाहिए", "three side seal pouch चाहिए", "माई नेम इज़ राहुल",
                 "मेरी सिलाई की दुकान है", "एक मई तक चाहिए", "standup pouch with zipper", "1000 pieces per month",
                 "मेरे 5 variant हैं।", "हमारी company का नाम Shree Foods है।"):
        assert misheard_note(said) is None, said


def test_a_misheard_turn_gets_the_note_for_that_reply_only(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["जी, आपको packaging कब तक चाहिए?", "माफ़ कीजिए, एक minute या एक month?", "जी, बढ़िया।"])
        s, _ = await _session("+919480000081", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        for i, said in enumerate(("हाँ जी बताइए", "हाँ 1 माई।", "एक month में चाहिए।"), 1):
            await stt.say(said)
            await _wait(lambda: len(llm.calls) == i and s.state == "listening")
        run.cancel()
        return llm

    llm = asyncio.run(scenario())
    notes = [" ".join([m["content"] for m in call if m["role"] == "system"][2:]) for call in llm.calls]
    assert "misheard" not in notes[0] and "misheard" in notes[1] and "misheard" not in notes[2]
    assert '"minute"' in notes[1]


def test_stt_vocabulary_hints_are_off_by_default_and_safe():
    import json
    import urllib.parse

    from app.services.sarvam import RealtimeSTT, stt_hints

    assert stt_hints("saaras:v3-realtime", "", "") == {}
    # keyterms only go to saaras:v4: v3-realtime rejects them and the call would get no transcripts
    assert "keyterms" not in stt_hints("saaras:v3-realtime", "three side seal, makhana", "")
    hints = stt_hints("saaras:v4", " three side seal, makhana ,, PouchWale, makhana", "packaging terms")
    assert json.loads(hints["keyterms"]) == ["three side seal", "makhana", "PouchWale"]
    assert hints["prompt"] == "packaging terms"
    assert len(json.loads(stt_hints("saaras:v4", ",".join(f"w{i}" for i in range(80)), "")["keyterms"])) == 50
    url = RealtimeSTT(sample_rate=8000, encoding="mulaw", language_code="hi-IN", on_event=None)._url()
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert "keyterms" not in q and "prompt" not in q and q["model"] == ["saaras:v3-realtime"]  # unchanged


def test_prompts_say_confirm_dont_assume():
    sp = static_system_prompt(dict(DEFAULT_AGENT_SETTINGS), "outbound", None)
    assert "KEY DETAILS, CONFIRM DON'T ASSUME" in sp and "आपकी company का नाम स्वप्न है?" in sp
    analysis = build_analysis_prompt(dict(DEFAULT_AGENT_SETTINGS), None, "CUSTOMER: Masjid", "outbound")[0]["content"]
    assert "never report a garbled word as a fact" in analysis and "'Masjid'" in analysis
