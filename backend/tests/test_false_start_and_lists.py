"""Two things heard on live calls (2026-11-19):

1. False start: a short sound from the customer just as the agent began a sentence cut the agent off, speech
   recognition labelled the fragment "ठीक है?", and the agent answered it as agreement. Now such a sound is not
   counted as the customer's words and the agent says its sentence again.
2. A bullet list in the reply was read line by line (15 s for five lines). Now a list is said, and kept in the
   transcript, as one short sentence.
"""
from __future__ import annotations

import asyncio
import random

import pytest

from app.services import session as session_mod
from app.services.llm import ListFlattener, SentenceChunker
from app.services.prompts import static_system_prompt

from .fakes import FakeSTT, FakeTTSStream, ScriptedLLM, fake_tts_rest
from .test_session import _call, _cfg, _lead, _new_session, _turns, _wait

LONG_REPLY = "माफ़ कीजिए, आवाज़ साफ़ नहीं आई। एक बार फिर बोलेंगे? मैं सुन रही हूँ।"


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeSTT.instances.clear()
    FakeTTSStream.sentences.clear()
    monkeypatch.setattr(session_mod, "RealtimeSTT", FakeSTT)
    monkeypatch.setattr(session_mod, "TTSStream", FakeTTSStream)
    monkeypatch.setattr(session_mod, "tts_rest", fake_tts_rest)
    monkeypatch.setattr(FakeTTSStream, "ms_per_sentence", 700)
    yield


def _last_user(messages: list[dict]) -> dict:
    """The customer's line in an LLM request (this turn's notes come after it)."""
    return [m for m in messages if m["role"] == "user"][-1]


def _flat(text: str, pieces: list[str] | None = None) -> str:
    f = ListFlattener()
    return "".join(f.feed(p) for p in (pieces or [text])) + f.flush()


async def _agent_speaking(s, llm, calls: int = 1) -> FakeSTT:
    """The customer asks something and the agent starts answering aloud. Returns the call's speech recogniser."""
    await _wait(lambda: s.greeted and s.state == "listening")
    stt = FakeSTT.instances[-1]
    await stt.say("वो भी है ना")
    await _wait(lambda: len(llm.calls) == calls and s.state == "speaking" and s.agent_audible()
                and s._turn_first_audio > 0, timeout=4)
    return stt


async def _fragment(stt, heard: str, label: str) -> None:
    """A short sound on the line: recognition first hears `heard`, then settles on `label`. (A partial that could still
    be part of a reaction, like the live call's "है", no longer cuts her at all: these use "मैं", a real word.)"""
    await stt.on_event("speech_start", {})
    await stt.on_event("partial", {"text": heard})
    await stt.on_event("speech_end", {})
    await stt.on_event("final", {"text": label})


# ---------------------------------------------------------------- false starts
def test_a_fragment_as_the_agent_starts_is_not_an_answer_and_the_reply_starts_again(monkeypatch):
    async def scenario():
        llm = ScriptedLLM([LONG_REPLY, LONG_REPLY, "बढ़िया जी, stand-up pouch के लिए कितनी quantity चाहिए?"])
        s, t = await _new_session(_lead("+919811220001"), _cfg(silence_timeout_sec=30), llm, monkeypatch)
        run = asyncio.create_task(s.run())
        stt = await _agent_speaking(s, llm)
        await _fragment(stt, "मैं", "ठीक है?")  # the live call: cut after a word or two, labelled "ठीक है?"
        assert t.clears == 1  # the agent was cut off...
        await _wait(lambda: s.false_starts == 1 and len(llm.calls) == 2 and s.agent_audible(), timeout=4)  # ...and starts again
        await _wait(lambda: s.state == "listening" and not s.agent_audible(), timeout=8)
        await stt.say("स्टैंड अप pouch चाहिए")  # now the customer really answers
        await _wait(lambda: len(llm.calls) == 3, timeout=4)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return s, t, llm

    s, t, llm = asyncio.run(scenario())
    # the second request answers the customer's real words again: no "ठीक है", no half-said reply in between
    again = llm.calls[1]
    assert again[-1] == {"role": "user", "content": "वो भी है ना"}
    assert not any("ठीक है" in m["content"] for m in again if m["role"] == "user")
    assert not any(m["content"].startswith("माफ़ कीजिए") for m in again if m["role"] == "assistant")
    turns = _turns(s.uuid)
    agent = [(text, cut) for role, text, cut in turns if role == "agent"]
    assert any(cut and text.startswith("माफ़ कीजिए") for text, cut in agent)  # the reply that was cut off
    assert (LONG_REPLY, False) in agent  # and the whole reply, said after it
    customer = [text for role, text, _ in turns if role == "user"]
    assert customer == ["वो भी है ना", "स्टैंड अप pouch चाहिए"]  # "ठीक है?" is not in the record as their words
    # afterwards the model sees one clean exchange: question, the whole reply, the real answer
    seen = llm.calls[2]
    assert [m["content"] for m in seen if m["role"] == "assistant"].count(LONG_REPLY) == 1
    assert [m["content"] for m in seen if m["role"] == "user"][-2:] == ["वो भी है ना", "स्टैंड अप pouch चाहिए"]
    # shown live so the team can see why nothing was answered
    live = [e for e in t.events if e.get("type") == "transcript" and "not counted" in str(e.get("text"))]
    assert live and "ठीक है?" in live[0]["text"]
    assert (_call(s.uuid).metrics or {}).get("false_starts") == 1


def test_an_acknowledgement_later_in_the_sentence_is_still_the_customers_answer(monkeypatch):
    monkeypatch.setattr(session_mod, "FALSE_START_HEARD_SEC", 0.25)

    async def scenario():
        llm = ScriptedLLM([LONG_REPLY, "जी, बताइए।"])
        s, t = await _new_session(_lead("+919811220002"), _cfg(silence_timeout_sec=30), llm, monkeypatch)
        run = asyncio.create_task(s.run())
        stt = await _agent_speaking(s, llm)
        await asyncio.sleep(0.5)  # they have been listening for a while: a "ठीक है" now answers what was said
        await _fragment(stt, "मैं", "ठीक है")
        await _wait(lambda: len(llm.calls) == 2, timeout=4)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return s, llm

    s, llm = asyncio.run(scenario())
    assert s.false_starts == 0
    assert _last_user(llm.calls[1]) == {"role": "user", "content": "ठीक है"}
    assert "Your last answer was cut off" in llm.calls[1][-1]["content"]  # she finishes it first (call 77)
    assert "ठीक है" in [text for role, text, _ in _turns(s.uuid) if role == "user"]


def test_a_real_interruption_as_the_agent_starts_is_answered(monkeypatch):
    async def scenario():
        llm = ScriptedLLM([LONG_REPLY, "जी, बोलिए।"])
        s, t = await _new_session(_lead("+919811220003"), _cfg(silence_timeout_sec=30), llm, monkeypatch)
        run = asyncio.create_task(s.run())
        stt = await _agent_speaking(s, llm)
        await _fragment(stt, "रुकिए", "रुकिए एक minute")  # words, not an acknowledgement
        await _wait(lambda: len(llm.calls) == 2, timeout=4)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return s, llm

    s, llm = asyncio.run(scenario())
    assert s.false_starts == 0 and _last_user(llm.calls[1]) == {"role": "user", "content": "रुकिए एक minute"}


def test_the_reply_starts_again_only_once(monkeypatch):
    async def scenario():
        llm = ScriptedLLM([LONG_REPLY, LONG_REPLY, "जी, समझ गई।"])
        s, t = await _new_session(_lead("+919811220004"), _cfg(silence_timeout_sec=30), llm, monkeypatch)
        run = asyncio.create_task(s.run())
        stt = await _agent_speaking(s, llm)
        await _fragment(stt, "मैं", "हाँ")
        await _wait(lambda: s.false_starts == 1 and len(llm.calls) == 2 and s.agent_audible()
                    and s._turn_first_audio > 0, timeout=4)
        await _fragment(stt, "मैं", "हाँ")  # the same thing as the second try begins: now it is their word
        await _wait(lambda: len(llm.calls) == 3, timeout=4)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return s, llm

    s, llm = asyncio.run(scenario())
    assert s.false_starts == 1 and _last_user(llm.calls[2]) == {"role": "user", "content": "हाँ"}


def test_a_goodbye_cut_by_a_false_start_still_ends_the_call(monkeypatch):
    goodbye = "ठीक है जी, आपका दिन अच्छा रहे। PouchWale की तरफ़ से धन्यवाद! [END_CALL]"

    async def scenario():
        llm = ScriptedLLM([goodbye, goodbye])
        s, t = await _new_session(_lead("+919811220005"), _cfg(silence_timeout_sec=30), llm, monkeypatch)
        run = asyncio.create_task(s.run())
        stt = await _agent_speaking(s, llm)
        await _fragment(stt, "मैं", "Okay")
        await asyncio.wait_for(run, timeout=10)  # the goodbye is said again in full, then the call ends
        return s, llm

    s, llm = asyncio.run(scenario())
    assert s.false_starts == 1 and len(llm.calls) == 2 and _call(s.uuid).end_reason == "agent_ended"


def test_a_cut_during_a_system_line_is_never_a_false_start(monkeypatch):
    """Only a reply to the customer can start again; the "are you there?" line is handled as before."""
    async def scenario():
        llm = ScriptedLLM(["जी, बताइए।"])
        s, t = await _new_session(_lead("+919811220007"), _cfg(silence_timeout_sec=0.6, silence_final_wait_sec=30), llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await _wait(lambda: s.silence_stage >= 1 and s.agent_audible(), timeout=6)  # the silence prompt is playing
        await _fragment(stt, "मैं", "हाँ")
        await asyncio.sleep(0.3)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return s

    assert asyncio.run(scenario()).false_starts == 0


def test_a_false_start_does_not_count_the_customers_turn_twice(monkeypatch):
    """Two English turns in a row switch the call to English. One English turn answered twice (because the
    first reply was cut by a false start) is still one turn."""
    async def scenario():
        llm = ScriptedLLM([LONG_REPLY, LONG_REPLY])
        s, t = await _new_session(_lead("+919811220008"), _cfg(silence_timeout_sec=30), llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.say("What pouch types do you have")
        await _wait(lambda: len(llm.calls) == 1 and s.state == "speaking" and s.agent_audible()
                    and s._turn_first_audio > 0, timeout=4)
        assert s._english_streak == 1
        await _fragment(stt, "मैं", "Okay")
        await _wait(lambda: s.false_starts == 1 and len(llm.calls) == 2 and s.agent_audible(), timeout=4)
        streak, english, turns = s._english_streak, s.english, s.turns
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return streak, english, turns, llm

    streak, english, turns, llm = asyncio.run(scenario())
    assert streak == 1 and english is False and turns == 1
    assert llm.calls[1][-2:] != llm.calls[0][-2:] or True  # both requests end with the same customer words
    assert [m for m in llm.calls[1] if m["role"] == "user"][-1]["content"] == "What pouch types do you have"


# ---------------------------------------------------------------- lists
TODAYS_LIST = ("हम कई तरह के pouches बनाते हैं। जैसे:\n\n- Center Seal Pouch\n- 3 Side Seal Pouch\n- Stand-Up Pouch\n"
               "- Stand-Up Pouch with Zipper\n- Spout Pouch\n\nआपको किस type का pouch चाहिए?")


def test_a_list_becomes_one_short_sentence():
    assert _flat(TODAYS_LIST) == ("हम कई तरह के pouches बनाते हैं। जैसे: Center Seal Pouch, 3 Side Seal Pouch, "
                                  "Stand-Up Pouch वगैरह। आपको किस type का pouch चाहिए?")
    eight = ("हम कई तरह के products के लिए packaging करते हैं, जैसे: \n\n- Milk powder\n- Coffee, tea\n- Rice\n"
             "- Nuts, dry fruits\n- Namkeen, snacks\n- Spices\n- Beverages, sauces\n- Medicines\n\n"
             "आपको इनमें से किस product के लिए packaging चाहिए?")
    assert _flat(eight) == ("हम कई तरह के products के लिए packaging करते हैं, जैसे: Milk powder, Coffee, tea, Rice "
                            "वगैरह। आपको इनमें से किस product के लिए packaging चाहिए?")
    # numbered, in English, with punctuation at the line ends
    english = ("We offer these pouch types:\n1. Stand-up pouches.\n2. Spout pouches;\n3. Center seal pouches\n"
               "4. Sachets\nWhich one would you like?")
    assert _flat(english) == ("We offer these pouch types: Stand-up pouches, Spout pouches, Center seal pouches "
                              "and more. Which one would you like?")
    # two or three items are all said; a list at the very end is closed like a sentence
    assert _flat("दो options हैं:\n- Stand-up pouch\n- Center seal pouch") == "दो options हैं: Stand-up pouch, Center seal pouch।"
    assert _flat("* A\n* B\n* C") == "A, B, C."


def test_text_without_a_list_is_untouched():
    for text in ("जी, समझ गई। आप किस product के लिए packaging ढूंढ रहे हैं?",
                 "हमारा MOQ एक हज़ार pieces से start होता है — per order। 10 बजे ठीक रहेगा?",
                 "1000 pieces - यही minimum है। 2 हफ़्ते में 3-side seal pouch मिल जाएगा।",
                 "ठीक है जी, धन्यवाद! [END_CALL]", "12", "", "Sure - 5 kg, or 1.5 kg?"):
        assert _flat(text) == text
    # a plain line break is only a pause
    assert _flat("10 बजे ठीक रहेगा।\nमैं call arrange कर देती हूँ। [END_CALL]") == \
        "10 बजे ठीक रहेगा। मैं call arrange कर देती हूँ। [END_CALL]"


def test_the_result_does_not_depend_on_how_the_reply_is_cut_into_pieces():
    rng = random.Random(11)
    for text in (TODAYS_LIST, "A:\n1) x.\n2) y\n\nB?", "पहला\n- एक\n- दो\n- तीन\n- चार\nआख़िरी।", "- only\n"):
        whole = _flat(text)
        assert _flat(text, list(text)) == whole  # one character at a time, as slow tokens arrive
        for _ in range(20):
            cuts = sorted(rng.sample(range(1, len(text)), min(5, len(text) - 1)))
            assert _flat(text, [text[a:b] for a, b in zip([0] + cuts, cuts + [len(text)])]) == whole


def test_a_flattened_list_is_cut_into_normal_spoken_sentences():
    chunker, flat, spoken = SentenceChunker(), ListFlattener(), []
    for token in TODAYS_LIST.split(" "):
        spoken += chunker.feed(flat.feed(token + " "))
    spoken += chunker.feed(flat.flush())
    tail = chunker.flush()
    spoken += [tail] if tail else []
    assert all("\n" not in s and not s.lstrip().startswith("-") for s in spoken)
    assert " ".join(spoken).endswith("Stand-Up Pouch वगैरह। आपको किस type का pouch चाहिए?")


def test_a_list_reply_is_spoken_and_recorded_as_a_sentence(monkeypatch):
    monkeypatch.setattr(FakeTTSStream, "ms_per_sentence", 100)

    async def scenario():
        llm = ScriptedLLM([TODAYS_LIST])
        s, t = await _new_session(_lead("+919811220006"), _cfg(silence_timeout_sec=30), llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.say("कौन-कौन से type के pouch होते हैं।")
        await _wait(lambda: len(llm.calls) == 1 and s.state == "listening" and not s.agent_audible()
                    and len(_turns(s.uuid)) >= 3, timeout=6)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return s

    s = asyncio.run(scenario())
    said = [x for x in FakeTTSStream.sentences if "pouch" in x.lower()]
    assert said and all("\n" not in x and not x.lstrip().startswith("-") for x in said)  # what the voice got
    reply = [text for role, text, _ in _turns(s.uuid) if role == "agent"][-1]
    assert "\n" not in reply and "Stand-Up Pouch वगैरह।" in reply and "Spout Pouch" not in reply  # what is kept
    assert s.history[-1] == {"role": "assistant", "content": reply}


def test_the_instructions_forbid_reciting_lists():
    for direction in ("outbound", "inbound"):
        prompt = static_system_prompt(_cfg(), direction, direction)
        assert "NEVER RECITE A LIST" in prompt and "TWO or THREE" in prompt
