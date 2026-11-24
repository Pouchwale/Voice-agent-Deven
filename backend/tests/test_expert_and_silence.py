"""Call 64 (2026-11-21): the agent went silent, and added "our team will confirm" to almost every line.

Silence: the voice service sometimes returns a short line with many seconds of pure silence ("हम्म, एक second..." came back
as 30.2 s with 0.7 s of voice). She "spoke" that silence while her real answer waited behind it.
Expert: she answers packaging questions herself; only a price, an exact size or spec, a date, payment, complex custom work or
a company fact she wasn't given goes to our expert, with no number or promise from her.
"""
from __future__ import annotations

import asyncio
import base64
import json
import math
import struct

import httpx
import pytest

from app.defaults import DEFAULT_AGENT_SETTINGS
from app.services import sarvam
from app.services import session as session_mod
from app.services.audio import MAX_PAUSE_MS, SilenceLimiter, trim_clip, wav_bytes
from app.services.prompts import (ANSWER_THEN_WAIT_NOTE, CUT_OFF_NOTE, END_CALL_TOKEN, EXPERT_CALL_NOTE, HAND_OVER_NOTE,
                                  HANG_UP_NOTE, MOOD_NOTES, OUR_NAME_NOTE, OWN_ANSWER_NOTE, TIME_AGREED_NOTE, WHAT_WE_MAKE_NOTE,
                                  detect_mood,
                                  static_system_prompt)
from app.services.session import (asks_for_a_call, asks_for_info, asks_our_name, asks_what_we_make, is_cut_off, is_our_pitch,
                                  needs_expert,
                                  pushes_back_on_team, says_a_time, wants_our_expert, wants_to_end_call)

from .fakes import FakeSTT, ScriptedLLM
from .test_humanize_live import _agent_lines, _session, _wait, fakes  # noqa: F401  (autouse fixture)

RATE = 8000


def voice(sec: float) -> bytes:
    return b"".join(struct.pack("<h", int(6000 * math.sin(i * .3))) for i in range(int(RATE * sec)))


def quiet(sec: float, level: int = 0) -> bytes:
    return struct.pack("<h", level) * int(RATE * sec)


def seconds(pcm: bytes) -> float:
    return len(pcm) / 2 / RATE


# ---------------------------------------------------------------- silence from the voice service
@pytest.mark.parametrize("clip, longest", [
    (voice(.7) + quiet(29.5), 1.0),               # the real fault: 30.2 s with 0.7 s of voice
    (voice(.5) + quiet(13) + voice(.8), 2.2),     # the same in the middle of a line
    (quiet(9) + voice(.6), 0.9),                  # or before it
    (voice(.7) + quiet(20, level=20), 1.0),       # not perfectly zero, still silence on a phone line
])
def test_a_ready_made_clip_never_carries_a_long_silence(clip, longest):
    assert seconds(trim_clip(clip, RATE)) <= longest


def test_natural_pauses_and_all_the_voice_are_kept():
    line = voice(1) + quiet(.3) + voice(1)
    assert trim_clip(line, RATE) == line
    assert seconds(trim_clip(voice(.7) + quiet(3), RATE)) >= .7  # the voice itself is never cut


def test_every_natural_pause_is_kept_not_just_the_first():
    line = voice(.4) + quiet(.5) + voice(.4) + quiet(.5) + voice(.4)
    assert trim_clip(line, RATE) == line


def test_nothing_but_silence_is_no_clip_at_all():
    assert trim_clip(quiet(5), RATE) == b"" and trim_clip(b"", RATE) == b""


def test_the_limiter_works_on_a_stream_of_small_pieces():
    audio = voice(.5) + quiet(10) + voice(.5)
    limiter = SilenceLimiter(RATE)
    out = b"".join(limiter.feed(audio[i:i + 333]) for i in range(0, len(audio), 333)) + limiter.flush()
    assert abs(seconds(out) - (1.0 + MAX_PAUSE_MS / 1000)) < .05


def _rest_answer(pcm: bytes):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"audios": [base64.b64encode(wav_bytes(pcm, RATE)).decode()]})
    return handler


def test_tts_rest_cuts_the_silence_and_never_keeps_a_silent_take(monkeypatch):
    calls: list[int] = []
    takes = [quiet(5), voice(.7) + quiet(25)]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _rest_answer(takes[len(calls) - 1])(request)

    async def scenario():
        monkeypatch.setattr(sarvam, "_http", httpx.AsyncClient(base_url="https://voice.test",
                                                               transport=httpx.MockTransport(handler)))
        cfg = {"language_code": "hi-IN", "voice": "sarika_hi_conversation", "tts_pace": 0.97}
        text = "हम्म, एक second... (silence test)"
        first = await sarvam.tts_rest(text, cfg, RATE)    # nothing but silence: not kept
        second = await sarvam.tts_rest(text, cfg, RATE)   # so the next request gets a fresh take
        third = await sarvam.tts_rest(text, cfg, RATE)    # and a good one is kept
        return first, second, third

    first, second, third = asyncio.run(scenario())
    assert first == b"" and len(calls) == 2
    assert 0.6 < seconds(second) <= 1.0 and third == second


def test_the_live_voice_stream_drops_a_long_silence():
    got = bytearray()

    async def on_audio(pcm: bytes) -> None:
        got.extend(pcm)

    class Socket:
        def __init__(self, messages):
            self.messages = messages

        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            for m in self.messages:
                yield m

    audio = voice(.7) + quiet(15)
    pieces = [audio[i:i + 1600] for i in range(0, len(audio), 1600)]
    frames = [json.dumps({"type": "audio", "data": {"audio": base64.b64encode(p).decode(), "content_type": "audio/l16"}})
              for p in pieces]
    frames.append(json.dumps({"type": "event", "data": {"event_type": "final"}}))

    async def scenario():
        s = sarvam.TTSStream({"language_code": "hi-IN"}, RATE, on_audio)
        s.ws = Socket(frames)
        await s._read_loop()
        return s

    s = asyncio.run(scenario())
    assert seconds(bytes(got)) <= 0.7 + MAX_PAUSE_MS / 1000 + .05
    assert s.audio_bytes == len(audio) and s._finals == 1  # the stream itself is still counted as complete


def test_a_clip_that_came_back_silent_is_not_used(monkeypatch):
    async def silent(text, cfg, rate):
        return b""

    async def scenario():
        s, _ = await _session("+919480001001", ScriptedLLM([]), monkeypatch)
        monkeypatch.setattr(session_mod, "tts_rest", silent)
        await s._build_clips({"filler_0": "जी..."}, s.cfg)
        return "filler_0" in s.clips

    assert asyncio.run(scenario()) is False


def test_a_filler_that_came_back_too_long_is_not_used(monkeypatch):
    """Its real answer would wait behind it. Other ready-made lines may be long."""
    forgotten: list[str] = []

    async def long_take(text, cfg, rate):
        return bytes([16, 39]) * (rate * 4)

    async def scenario():
        s, _ = await _session("+919480001003", ScriptedLLM([]), monkeypatch)
        monkeypatch.setattr(session_mod, "tts_rest", long_take)
        monkeypatch.setattr(session_mod, "forget_clip", lambda text, cfg, rate: forgotten.append(text))
        await s._build_clips({"filler_0": "हम्म, एक second...", "en_filler_0": "Hmm...", "closing": "thank you"}, s.cfg)
        return set(s.clips)

    clips = asyncio.run(scenario())
    assert "closing" in clips and "filler_0" not in clips and "en_filler_0" not in clips
    assert forgotten.count("Hmm...") == 2 and "thank you" not in forgotten  # a fresh take, then not kept for the next call


def test_a_long_filler_gets_one_fresh_take(monkeypatch):
    calls: list[str] = []

    async def take(text, cfg, rate):
        calls.append(text)
        return bytes([16, 39]) * (rate * 4 if len(calls) == 1 else rate // 2)

    async def scenario():
        s, _ = await _session("+919480001005", ScriptedLLM([]), monkeypatch)
        monkeypatch.setattr(session_mod, "tts_rest", take)
        await s._build_clips({"filler_0": "हम्म, एक second..."}, s.cfg)
        return set(s.clips)

    assert asyncio.run(scenario()) == {"filler_0"} and len(calls) == 2


def test_a_forgotten_clip_is_fetched_fresh(monkeypatch):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _rest_answer(voice(.7))(request)

    async def scenario():
        monkeypatch.setattr(sarvam, "_http", httpx.AsyncClient(base_url="https://voice.test",
                                                               transport=httpx.MockTransport(handler)))
        cfg = {"language_code": "hi-IN", "voice": "sarika_hi_conversation", "tts_pace": 0.97}
        text = "हम्म, एक second... (fresh take test)"
        await sarvam.tts_rest(text, cfg, RATE)
        await sarvam.tts_rest(text, cfg, RATE)   # kept
        sarvam.forget_clip(text, cfg, RATE)
        await sarvam.tts_rest(text, cfg, RATE)   # fresh

    asyncio.run(scenario())
    assert len(calls) == 2


def test_a_stalled_ai_start_is_retried_quickly(monkeypatch):
    """OpenAI sometimes takes 5-18 s for the first word: a fresh request answers in under a second."""
    monkeypatch.setattr(session_mod, "FIRST_TOKEN_SEC", (0.2, 2.0))

    class StallFirst(ScriptedLLM):
        async def __call__(self, messages, cache_key=None):
            self.calls.append(messages)
            if len(self.calls) == 1:
                await asyncio.sleep(30)
            yield "जी, MOQ एक हज़ार pieces से start होता है।"

    async def scenario():
        llm = StallFirst([])
        s, t = await _session("+919480001002", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        await FakeSTT.instances[-1].say("आपका MOQ कितना है?")
        await _wait(lambda: any("MOQ" in x for x in _agent_lines(t)), timeout=4)
        calls = len(llm.calls)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return calls

    assert asyncio.run(scenario()) == 2


def test_the_normal_wait_for_the_first_word_is_short():
    assert session_mod.FIRST_TOKEN_SEC[0] <= 2.5


# ---------------------------------------------------------------- what she answers, what goes to our expert
@pytest.mark.parametrize("said", [
    "एक pouch का rate कितना पड़ेगा?", "पाँच हज़ार का total कितना होगा?", "कोई discount मिलेगा क्या?", "इसका भाव क्या चल रहा है?",
    "How much would 5000 pouches cost?", "मुझे rate चाहिए",                                             # price
    "पाँच सौ gram के लिए pouch का size क्या होगा?", "कितने micron का material होगा?", "pouch की लंबाई चौड़ाई कितनी होगी?",
    "एक किलो के लिए कितना बड़ा pouch चाहिए?", "printing कितने colour तक होती है?",                       # exact specs
    "आप कब तक दे सकती है", "माल कितने दिनों में आ जाएगा?", "advance कितना देना पड़ेगा?", "क्या आप COD पर देते हो?",  # promises
    "आपके पास कितनी machines हैं?", "आपके बड़े clients कौन कौन हैं?", "आपकी company कितने साल पुरानी है?",
    "क्या आप export भी करते हो?", "आप dubai भेज सकते हो?", "GST bill मिल जाएगा?", "आपके यहाँ कितने लोग काम करते हैं?",
    "madam मेरे को आपके plant की visit करनी है तो अपना location बता दो",                                   # company facts
    "मुझे compostable material में child-resistant zipper वाला pouch चाहिए, बन जाएगा?",                   # complex work
])
def test_what_only_a_person_can_commit_to_goes_to_our_expert(said):
    assert asks_for_info(said) and needs_expert(said)


@pytest.mark.parametrize("said", [
    "चिप्स के लिए कौन सा pouch best रहेगा?", "एक premium brand बनानी है, उसके लिए कौन सा pouch suitable रहेगा?",
    "standup pouch और center seal में क्या फर्क है?", "ये three side seal pouch क्या होता है?", "matte finish अच्छा रहता है या gloss?",
    "zipper लगवाना चाहिए क्या?", "आप design में कुछ help कर पाएंगी क्या", "pouch के front पर क्या क्या होना चाहिए?",
    "मसालों के लिए कौन सा material अच्छा रहता है?", "custom printing का process क्या होता है?",
    "ये pouch में chips कब तक fresh रहेंगे?", "Why do coffee bags have a valve?",
    "आपका MOQ कितना है?", "sample kit का charge कितना लगता है?", "sample का rate क्या है?", "आपकी company कहाँ पर है?",   # in the knowledge
])
def test_packaging_questions_she_answers_herself(said):
    assert asks_for_info(said) and not needs_expert(said)


@pytest.mark.parametrize("said, ends", [
    ("ठीक है तो फिर रखो रखो phone रखो", True), ("चलो phone रख दो", True), ("call काट दो", True), ("फोन रखता हूँ", True),
    ("please hang up", True), ("phone मत रखना", False), ("फोन बंद मत करना", False), ("phone रखना मत", False), ("मत call करना", False), ("एक हज़ार रख लो", False), ("call back करना", False),
])
def test_being_told_to_end_the_call(said, ends):
    assert wants_to_end_call(said) is ends


@pytest.mark.parametrize("said, cut", [
    ("तो कौन", True), ("आप क्या अपना", True), ("और वो अपना", True),
    ("आप कौन हैं", False), ("आप कौन?", False), ("क्या?", False), ("हाँ", False), ("मेरा product chips है", False),
])
def test_cut_off_fragments(said, cut):
    assert is_cut_off(said) is cut


@pytest.mark.parametrize("said", [
    "अच्छा तो आप क्या बता सकती हो, सब आपकी team करेगी तो आप क्या करेंगे", "हर बार team team बोल रही हो, आप खुद कुछ बता दो",
    "बार बार आपकी team की बात क्यों कर रही हो", "सब कुछ expert ही बताएंगे तो फिर आप क्या बताओगी",
    "team को छोड़ो, आप ही बता दो कौन सा pouch लूँ", "अरे madam आप कुछ बताओगी भी या सब team ही बताएगी",
    "Why do you keep saying your team will tell me?", "हर बार team team बोल रही हो", "सब आपकी team ही करेगी",
])
def test_a_customer_tired_of_hearing_about_the_team(said):
    assert pushes_back_on_team(said) and asks_for_info(said)


@pytest.mark.parametrize("said", [
    "आपकी team कब call करेगी?", "तो आप क्या team से बात करवाओगे?", "बार बार team से बात करनी पड़ेगी क्या?",
    "आप क्या बता सकती हो chips के बारे में?", "मेरी team से बात कर लो", "Can your team tell me the rate?",
    "तो आप क्या करते हो?", "आप ही बता दो कौन सा pouch लूँ",  # sound the same, but nobody mentioned the team
])
def test_an_ordinary_question_about_the_team_is_no_complaint(said):
    assert not pushes_back_on_team(said)


def test_being_tired_of_the_team_still_gets_no_price_from_her():
    said = "हर बार team बोलती हो, rate आप ही बता दो"
    assert pushes_back_on_team(said) and needs_expert(said)
    assert "Do NOT mention our team" in OWN_ANSWER_NOTE and "No price, no number" in " ".join(OWN_ANSWER_NOTE.split())


def _last_note(llm) -> str:
    return [m["content"] for m in llm.calls[-1] if m["role"] == "system"][-1]


@pytest.mark.parametrize("phone, said, note", [
    ("+919480001011", "matte finish अच्छा रहता है या gloss?", ANSWER_THEN_WAIT_NOTE),
    ("+919480001012", "एक pouch का rate कितना पड़ेगा?", HAND_OVER_NOTE),
    ("+919480001013", "तो कौन", CUT_OFF_NOTE),
    ("+919480001014", "ठीक है तो फिर रखो रखो phone रखो", HANG_UP_NOTE),
    ("+919480001015", "सब आपकी team करेगी तो आप क्या करेंगे", OWN_ANSWER_NOTE),
    ("+919480001016", "हर बार team बोलती हो, rate आप ही बता दो", HAND_OVER_NOTE),
    ("+919480001017", "तो मेरे को कल दोपहर 1 बजे आपकी expert के साथ meeting schedule कर सकते हो", TIME_AGREED_NOTE),
    ("+919480001018", "क्या आप मेरा call आपके expert के साथ connect कर सकते हो अभी?", EXPERT_CALL_NOTE),
    ("+919480001024", "आपकी company क्या करती है?", WHAT_WE_MAKE_NOTE),
    ("+919480001025", "और आपके brand का नाम क्या है", OUR_NAME_NOTE),
])
def test_each_kind_of_turn_gets_its_own_note_last(monkeypatch, phone, said, note):
    async def scenario():
        llm = ScriptedLLM(["जी।"])
        s, _ = await _session(phone, llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        await FakeSTT.instances[-1].say(said)
        await _wait(lambda: len(llm.calls) == 1 and s.state == "listening")
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return llm

    assert _last_note(asyncio.run(scenario())) == note


def test_the_notes_say_the_right_thing():
    assert "Do NOT state any number" in HAND_OVER_NOTE and "must NOT contain a question mark" in HAND_OVER_NOTE
    assert "packaging expert" in ANSWER_THEN_WAIT_NOTE and "must NOT contain a question mark" in ANSWER_THEN_WAIT_NOTE
    assert "add nothing about our team" in " ".join(ANSWER_THEN_WAIT_NOTE.split())
    assert "do NOT add what we make" in " ".join(ANSWER_THEN_WAIT_NOTE.split()) and "हम namkeen" not in ANSWER_THEN_WAIT_NOTE
    assert "no line about what we make" in " ".join(HAND_OVER_NOTE.split())
    assert END_CALL_TOKEN in HANG_UP_NOTE and "nothing about an order" in HANG_UP_NOTE


def test_the_prompt_makes_her_the_expert():
    for direction, purpose in (("outbound", None), ("inbound", None), ("outbound", "callback")):
        p = static_system_prompt(dict(DEFAULT_AGENT_SETTINGS), direction, purpose)
        assert "- YOU ARE THE PACKAGING EXPERT (critical):" in p and "DEFLECTION, ONLY for these" in p
        assert "USE WHAT YOU ALREADY HEARD" in p
        assert "ये exact detail मैं अभी confirm नहीं कर पाऊँगी" not in p  # the stock line she repeated all through call 64
        assert '"phone रखो"' in p
        assert "Tell them what we make only ONCE" in p and "A time they named themselves is already agreed" in p


# ---------------------------------------------------------------- call 68: what we make, "busy", and a time they give
@pytest.mark.parametrize("said", [
    "तो मेरे को कल दोपहर 1 बजे आपकी expert के साथ meeting schedule कर सकते हो",
    "मैंने आपको बोला कल दोपहर 1 बजे मेरी meeting schedule कीजिए",
])
def test_asking_for_a_meeting_is_not_being_busy(said):
    assert detect_mood(said) is None


@pytest.mark.parametrize("said", ["अभी meeting में हूँ", "I am in a meeting", "मीटिंग चल रही है", "अभी busy हूँ"])
def test_really_being_busy_still_counts(said):
    assert detect_mood(said) == "busy"


def test_she_never_tells_them_they_seem_busy():
    assert "Never say out loud that they seem busy" in MOOD_NOTES["busy"]
    assert "Do NOT say they seem busy" in " ".join(TIME_AGREED_NOTE.split())


def test_an_agreed_time_is_said_back_and_the_line_stays_open():
    note = " ".join(TIME_AGREED_NOTE.split())
    assert "name none" in note and "must NOT contain a question mark" in note and "no goodbye and no end-call marker" in note


@pytest.mark.parametrize("said", [
    "तो क्या आप मेरा call आपके expert team के साथ connect कर सकते हो अभी?",
    "मैंने बोला कि आप मेरे को आपके expert के साथ connect कर सकते हो अभी", "आपके expert से बात करवा दो",
    "Can I talk to your expert?", "कल एक बजे meeting रख दो", "क्या मुझे आप अपने किसी sales person का number दे सकते हैं?",
    "call transfer कर दो", "When can it be scheduled?",
])
def test_asking_for_a_person_or_a_booked_time(said):
    assert wants_our_expert(said)


@pytest.mark.parametrize("said", [
    "मेरी team से बात कर लो", "अभी meeting में हूँ", "आपकी team कब call करेगी?", "standup pouch कैसा रहेगा?", "आपका MOQ कितना है?",
])
def test_not_asking_for_a_person(said):
    assert not wants_our_expert(said)


@pytest.mark.parametrize("said, is_time", [
    ("कल दोपहर 1 बजे", True), ("3:00 बजे।", True), ("शाम को चार बजे", True), ("साढ़े तीन बजे", True), ("tomorrow at 11", True),
    ("at 3 pm", True), ("कल", False), ("5000 pieces", False), ("दो हज़ार चाहिए", False), ("500 gram", False),
    ("at 5000 pieces", False), ("5 amount", False),
])
def test_a_clock_time(said, is_time):
    assert says_a_time(said) is is_time


@pytest.mark.parametrize("said, asks", [
    ("कल 3 बजे call कर लेना", True), ("शाम को फोन करना", True), ("Call me at 4 pm", True), ("call back मत", False),
    ("पाँच हज़ार चाहिए", False), ("the call message", False),
])
def test_asking_for_a_call(said, asks):
    assert asks_for_a_call(said) is asks


@pytest.mark.parametrize("said, asks", [
    ("Ma'am आप कौन से कौन से type की packaging बनाते हो", True), ("और कौन से कौन से type के like बनाती है pouches?", True),
    ("क्या करती है आपकी company", True), ("आपकी कंपनी किस-किस product की packaging बनाती है?", True),
    ("what type of pouches are you selling?", True), ("And what kind of pouches do you make?", True),
    ("आपके कंपनी में special pouches आते हैं वो बनाते हो आप?", True), ("मुझे कौन-कौन सी service मिलेगी", True),
    ("आपकी company कहाँ पर है?", False), ("आपके brand का नाम क्या है", False), ("आपका MOQ कितना है?", False),
    ("क्या आप export भी करते हो?", False),
])
def test_asking_what_we_make(said, asks):
    assert asks_what_we_make(said) is asks


@pytest.mark.parametrize("said, asks", [
    ("और आपके brand का नाम क्या है", True), ("आपकी company का नाम बताइए", True), ("आपकी कंपनी का नाम क्या है?", True),
    ("What is your company's name?", True), ("your brand name please", True),
    ("मेरी company का नाम Omix है", False), ("आपका नाम क्या है", False), ("आपकी company कहाँ पर है?", False),
])
def test_asking_our_name(said, asks):
    assert asks_our_name(said) is asks


@pytest.mark.parametrize("line, pitch", [
    ("हम namkeen, spices और dry fruits जैसे products के pouches बनाते हैं।", True),
    ("हम custom pouch बनाते हैं, जैसे namkeen, spices और dry fruits के packets।", True),
    ("हमारी company custom printed pouches बनाती है।", True), ("We make custom printed pouches.", True),
    ("PouchWale custom printed pouches बनाती है, low MOQ के साथ।", True),
    ("MOQ एक हज़ार pieces से start होता है।", False), ("standup pouch shelf पर अच्छा दिखता है।", False),
    ("हम गुजरात में हैं।", False), ("custom pouches बनाते हैं", False),
])
def test_what_counts_as_saying_what_we_make(line, pitch):
    assert is_our_pitch(line) is pitch


@pytest.mark.parametrize("said", [
    "क्या आप मेरे को बता सकते हो कि आप कौन सी machinery का use करते हो", "और आप raw material कौन सा use करते हो",
    "आपकी company की policy क्या है?", "पर SKU मुझे thousand order करना पड़ेगा या दोनों के combine हो के thousand",
])
def test_company_facts_she_invented_on_real_calls_go_to_our_expert(said):
    assert needs_expert(said)


class _Said:
    _last_agent_text = session_mod.CallSession._last_agent_text

    def __init__(self, *agent):
        self.history = [{"role": "assistant", "content": a} for a in agent]


def test_a_supplier_they_name_is_not_said_back():
    from app.services.prompts import ASK_WHICH_COMPANY_NOTE, NO_PACKAGING_NOTE, SUPPLIER_NAME_NOTE
    asked = "जी, क्या आप अभी अपने product के लिए किसी तरह की packaging use कर रहे हैं?"
    asked_en = "Are you currently using any packaging for your product?"
    whose = "अच्छा जी, आप अभी किस company की packaging use कर रहे हैं?"
    for said in ("हाँ, अभी XYZ Packaging से लेते हैं।", "हाँ, XYZ से लेते हैं अभी।", "हाँ, XYZ की packaging है।"):
        assert _note(said, asked) == SUPPLIER_NAME_NOTE
    assert _note("Yes, we buy from XYZ.", asked_en) == SUPPLIER_NAME_NOTE
    for said in ("हाँ, कर रहे हैं।", "जी हाँ, अभी printed pouches लेते हैं।", "हाँ हाँ, बिल्कुल use करते हैं।",
                 "अभी printed pouches लेते हैं।", "हाँ जी, पर printed नहीं, plain pouch है।"):
        assert _note(said, asked) == ASK_WHICH_COMPANY_NOTE
    assert _note("Yes, we are.", asked_en) == ASK_WHICH_COMPANY_NOTE
    for said in ("नहीं, अभी कोई packaging use नहीं कर रहे।", "नहीं जी, अभी नया start कर रहे हैं।", "मुझे पक्का नहीं पता।",
                 "पता नहीं, ये सब मेरे partner देखते हैं।"):
        assert _note(said, asked) == NO_PACKAGING_NOTE
    for said in ("No, not yet.", "No, we sell it loose right now.", "Not sure."):
        assert _note(said, asked_en) == NO_PACKAGING_NOTE
    assert _note("XYZ की", whose) == SUPPLIER_NAME_NOTE
    assert _note("Yes, from XYZ.", "Which company's packaging are you currently using?") == SUPPLIER_NAME_NOTE
    assert _note("हाँ", "आपको किस तरह का pouch चाहिए?") not in (SUPPLIER_NAME_NOTE, ASK_WHICH_COMPANY_NOTE, NO_PACKAGING_NOTE)
    assert _note("हाँ, आपका rate क्या है?", asked) != ASK_WHICH_COMPANY_NOTE  # a question gets its own answer note
    assert "Do NOT say that company name back" in SUPPLIER_NAME_NOTE and "do NOT ask which company again" in SUPPLIER_NAME_NOTE


def _note(text, *agent, no_calls=None):
    notes = session_mod.CallSession._turn_notes(_Said(*agent), text, None, no_calls, None)
    return notes[-1] if notes else None


def test_a_time_stands_when_it_answers_when_or_asks_for_a_call():
    asked = "जी, आपको कब call करना ठीक रहेगा?"
    assert _note("दोपहर 5 बजे", asked) == TIME_AGREED_NOTE
    assert _note("कल 3 बजे call कर लेना", "ठीक है जी।") == TIME_AGREED_NOTE
    assert _note("कल दोपहर 1 बजे आपके expert से बात करवा दो", "ठीक है जी।") == TIME_AGREED_NOTE
    assert _note("दोपहर 5 बजे", "आपका product क्या है?") != TIME_AGREED_NOTE   # she asked something else
    assert _note("दोपहर 5 बजे", "आपका time अच्छा रहा।") != TIME_AGREED_NOTE     # she asked nothing
    assert _note("कल 3 बजे मत call करना", asked) != TIME_AGREED_NOTE           # the opposite
    assert _note("3 बजे silk out call कर लेना", "ठीक है जी।") != TIME_AGREED_NOTE  # probably misheard: asked about first


def test_no_calls_still_wins_over_a_call_back():
    assert _note("कल 3 बजे call कर लेना", "ठीक है जी।", no_calls="no") != TIME_AGREED_NOTE
    assert _note("expert से बात करवा दो", "ठीक है जी।", no_calls="no") != EXPERT_CALL_NOTE
    assert _note("expert से बात करवा दो", "ठीक है जी।") == EXPERT_CALL_NOTE


async def _say_all(phone, llm, lines, monkeypatch):
    s, _ = await _session(phone, llm, monkeypatch)
    run = asyncio.create_task(s.run())
    await _wait(lambda: s.greeted and s.state == "listening")
    stt = FakeSTT.instances[-1]
    for i, line in enumerate(lines, 1):
        await stt.say(line)
        await _wait(lambda: len(llm.calls) == i and s.state == "listening")
    await s.end("manual_hangup")
    await asyncio.wait_for(run, timeout=5)
    return [m["content"] for m in s.history if m["role"] == "assistant"]


def test_what_we_make_is_not_added_to_a_later_answer(monkeypatch):
    llm = ScriptedLLM(["जी, हम custom pouch बनाते हैं। आपका product क्या है?",
                       "जी, MOQ एक हज़ार pieces से start होता है। हम namkeen, spices और dry fruits जैसे products के pouches बनाते हैं।",
                       "जी, standup pouch भी। हम namkeen के pouches भी बनाते हैं।"])
    replies = asyncio.run(_say_all("+919480001021", llm, ["हाँ बोलिए", "आपका MOQ कितना है?", "आप क्या क्या बनाते हो?"], monkeypatch))
    assert replies[-2] == "जी, MOQ एक हज़ार pieces से start होता है।"
    assert "namkeen" in replies[-1]  # they asked: she tells them


def test_what_we_make_is_not_added_even_without_a_full_stop(monkeypatch):
    llm = ScriptedLLM(["जी, हम custom pouch बनाते हैं। आपका product क्या है?",
                       "जी, MOQ एक हज़ार pieces से start होता है। हम namkeen के pouches बनाते हैं"])
    replies = asyncio.run(_say_all("+919480001026", llm, ["हाँ बोलिए", "आपका MOQ कितना है?"], monkeypatch))
    assert replies[-1] == "जी, MOQ एक हज़ार pieces से start होता है।"


def test_the_first_time_she_says_what_we_make_is_kept(monkeypatch):
    llm = ScriptedLLM(["जी, thank you जी। हम custom pouch बनाते हैं।"])
    replies = asyncio.run(_say_all("+919480001023", llm, ["हाँ बोलिए"], monkeypatch))
    assert "बनाते" in replies[-1]


def test_a_reply_that_is_only_that_line_is_not_emptied(monkeypatch):
    llm = ScriptedLLM(["जी, हम custom pouch बनाते हैं। आपका product क्या है?", "हम namkeen के pouches बनाते हैं।"])
    replies = asyncio.run(_say_all("+919480001022", llm, ["हाँ बोलिए", "मेरा product chips है"], monkeypatch))
    assert replies[-1] == "हम namkeen के pouches बनाते हैं।"
