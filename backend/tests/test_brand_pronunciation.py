"""The company name is always spoken "पाउच वाले", however it is written (services/sarvam.py: _say_brand).

On 2026-11-19 a line written "Pouch Wale" (with a space) was heard as "पूच वाले": the pronunciation fix in
Settings only knew the one-word spelling. Every spelling that sounds wrong now reaches the voice as a tested one:
"पाउचवाले" for the Hindi voice, "Pouch Waale" for English calls (both heard as "पाउच वाले"). A right Devanagari
spelling ("पाउच वाले" / "पाउचवाले") is left as written in Hindi calls.
"""
from __future__ import annotations

import asyncio
import copy
import json

import httpx
import pytest

from app.defaults import DEFAULT_AGENT_SETTINGS
from app.services import sarvam
from app.services.prompts import ENGLISH_LINES, fill_template
from app.services.sarvam import BRAND_SPOKEN_DEVANAGARI as HI
from app.services.sarvam import BRAND_SPOKEN_LATIN as EN
from app.services.sarvam import speakable
from app.services.session import english_voice_cfg

HINDI = copy.deepcopy(DEFAULT_AGENT_SETTINGS)  # a Hindi call with the shipped pronunciation fixes
ENGLISH = english_voice_cfg(HINDI)             # the same call after it switched to English
BARE_HINDI = {"language_code": "hi-IN"}        # no pronunciation fixes at all: the rule is built in
BARE_ENGLISH = {"language_code": "en-IN"}

WRITTEN = ["PouchWale", "Pouch Wale", "pouchwale", "POUCHWALE", "Pouch wale", "Pouch-Wale", "Pouch_Wale",
           "Pouch  Wale", "PouchWaale", "Pouch Waale", "pouchwaley", "पाउचवाले", "पौचवाले", "पौच वाले", "पोच वाले",
           "पूच वाले", "पूचवाले"]


@pytest.mark.parametrize("cfg", [HINDI, BARE_HINDI], ids=["hindi", "hindi-no-fixes"])
def test_a_right_devanagari_spelling_is_kept_in_hindi_calls(cfg):
    assert speakable("पाउच वाले को call करें", cfg) == "पाउच वाले को call करें"
    assert speakable("पाउचवाले को call करें", cfg) == "पाउचवाले को call करें"
    assert speakable("PouchWale", {**cfg, "pronunciations": ["PouchWale -> पाउच वाले"]}) == "पाउच वाले"  # team's rule


@pytest.mark.parametrize("cfg", [ENGLISH, BARE_ENGLISH], ids=["english", "english-no-fixes"])
def test_english_calls_get_the_latin_spelling_for_devanagari_too(cfg):
    assert speakable("Call पाउच वाले today", cfg) == f"Call {EN} today"


@pytest.mark.parametrize("written", WRITTEN)
@pytest.mark.parametrize("cfg, spoken", [(HINDI, HI), (BARE_HINDI, HI), (ENGLISH, EN), (BARE_ENGLISH, EN)],
                         ids=["hindi", "hindi-no-fixes", "english", "english-no-fixes"])
def test_every_spelling_is_spoken_pouch_waale(written, cfg, spoken):
    assert speakable(f"{written} को call करने के लिए thank you", cfg) == f"{spoken} को call करने के लिए thank you"
    assert speakable(f"Thank you for calling {written}.", cfg) == f"Thank you for calling {spoken}."


def test_marathi_voice_gets_the_devanagari_spelling_and_other_languages_the_latin_one():
    assert speakable("Pouch Wale", {"language_code": "mr-IN"}) == HI
    assert speakable("Pouch Wale", {"language_code": "gu-IN"}) == EN


def test_the_lines_the_team_wrote():
    # Settings -> Inbound closing line as typed on 2026-11-19: heard as "पूच वाले" before this fix
    line = ("अच्छा जी, मैं आपकी details हमारे expert को दे देती हूँ, वो आपको जल्दी call करेंगे। Pouch Wale को call "
            "करने के लिए thank youuu!")
    assert f"। {HI} को call करने के लिए thank youuu!" in speakable(line, HINDI)
    # the saved "unavailable" message spelled it "Pouch wale"
    assert speakable("नमस्ते! Pouch wale में call करने के लिए धन्यवाद।", HINDI) == f"नमस्ते! {HI} में call करने के लिए धन्यवाद।"


def test_the_email_address_rule_still_wins():
    assert speakable("mail करें info@pouchwale.com पर", HINDI) == f"mail करें इन्फो at {HI} dot com पर"
    assert speakable("Mail info@pouchwale.com today", ENGLISH) == f"Mail info at {EN} dot com today"


@pytest.mark.parametrize("text", [
    "हम custom printed pouch बनाते हैं।", "pouch with zipper", "stand-up pouches वाले customers", "zipper pouch वाले bags",
    "spout pouch walls", "Pouchwala", "pouchwalein", "मैं पाउच ले लूँगा", "touch wale",
])
def test_ordinary_words_are_left_alone(text):
    assert speakable(text, BARE_HINDI) == text
    assert speakable(text, BARE_ENGLISH) == text


@pytest.mark.parametrize("cfg", [HINDI, ENGLISH], ids=["hindi", "english"])
def test_saying_it_twice_changes_nothing(cfg):
    once = speakable("Thank you for calling Pouch Wale. PouchWale से बोल रही हूँ, पाउच वाले team.", cfg)
    assert speakable(once, cfg) == once


def test_every_fixed_line_says_it_right():
    """The greeting, closing and silence lines (Hindi, with {company_name}) and the English fixed lines."""
    for key in ("opening_line", "closing_line", "silence_goodbye_text", "inbound_greeting", "inbound_closing_line",
                "callback_greeting", "inbound_unavailable_message"):
        spoken = speakable(fill_template(HINDI[key], HINDI, {"name": "Rahul"}), HINDI)
        assert "pouch" not in spoken.lower(), (key, spoken)
    for key, line in ENGLISH_LINES.items():
        spoken = speakable(fill_template(line, HINDI, {"name": "Rahul"}), ENGLISH)
        assert "pouchwale" not in spoken.lower().replace(" ", ""), (key, spoken)
        assert "पाउच" not in spoken, (key, spoken)


def test_live_call_voice_receives_the_spoken_form():
    """The streaming voice used for every reply on a call."""
    sent: list[dict] = []

    class FakeSocket:
        async def send(self, message: str) -> None:
            sent.append(json.loads(message))

    async def on_audio(pcm: bytes) -> None:
        return None

    stream = sarvam.TTSStream(dict(HINDI), 8000, on_audio)
    stream.ws = FakeSocket()
    asyncio.run(stream.send_sentence("Pouch Wale से बोल रही हूँ।"))
    assert [m["data"]["text"] for m in sent if m.get("type") == "text"] == [f"{HI} से बोल रही हूँ।"]
    assert stream.sent == ["Pouch Wale से बोल रही हूँ।"]  # the transcript keeps what was written


def test_play_button_and_ready_made_lines_receive_the_spoken_form(monkeypatch):
    """The REST voice: Settings -> Play, the greeting and the other ready-made lines."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(500, text="stop here")

    monkeypatch.setattr(sarvam, "_http", httpx.AsyncClient(base_url="https://voice.test",
                                                           transport=httpx.MockTransport(handler)))
    for text, cfg in (("Thank you for calling Pouch Wale.", dict(ENGLISH)), ("Pouch Wale से बोल रही हूँ।", dict(HINDI))):
        with pytest.raises(RuntimeError):
            asyncio.run(sarvam.tts_rest(text, cfg, 24000))
    assert [b["text"] for b in bodies] == [f"Thank you for calling {EN}.", f"{HI} से बोल रही हूँ।"]
