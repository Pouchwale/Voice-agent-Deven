"""A customer who asks for something gets the answer and time to react, not the next question in the same breath.
If they react she carries on; if they stay quiet she gently checks in once (2026-11-21: 99 of 109 replies on real calls
carried a question, right after the answer: "बताइए, आपकी quantity लगभग कितनी चाहिए?", "क्या ये ठीक रहेगा?")."""
from __future__ import annotations

import asyncio

import pytest

from app.defaults import DEFAULT_AGENT_SETTINGS
from app.services import session as session_mod
from app.services.prompts import (ANSWER_THEN_WAIT_NOTE, CHECK_IN_LINES, COMPANY_NAME_NOTE, ENGLISH_TURN_NOTE, OUR_NAME_NOTE,
                                  static_system_prompt)
from app.services.session import CallSession, asks_for_info

from .fakes import FakeSTT, ScriptedLLM
from .test_humanize_live import REAL_PREFETCH, _agent_lines, _session, _wait, fakes  # noqa: F401  (autouse fixture)

ANSWER = "जी, MOQ एक हज़ार pieces से start होता है।"


def _notes(call: list[dict]) -> list[str]:
    return [m["content"] for m in call if m["role"] == "system"][2:]


async def _started(phone: str, llm, monkeypatch, **cfg):
    s, t = await _session(phone, llm, monkeypatch, **cfg)
    run = asyncio.create_task(s.run())
    await _wait(lambda: s.greeted and s.state == "listening")
    return s, t, run, FakeSTT.instances[-1]


async def _finish(s, run) -> None:
    await s.end("manual_hangup")
    await asyncio.wait_for(run, timeout=5)


def _check_ins(t, lang: str = "hindi") -> list[str]:
    return [line for line in _agent_lines(t) if line in CHECK_IN_LINES[lang]]


# ---------------------------------------------------------------- what counts as asking
@pytest.mark.parametrize("said", [
    "आपका MOQ कितना है?",
    "अह madam मेरे को कुछ पता नहीं है pouch के बारे में अह आप मेरे को suggest करो आपके according कौन सा अच्छा है",
    "madam मेरे को आपके plant की visit करनी है तो आप मेरे को अपना location तो बताओ",
    "आपका plant कहाँ पर है like location तो कुछ होगा city",
    "और आपके brand का नाम क्या है?",
    "हाँ ठीक है पर आपकी brand कितनी पुरानी है like मेरे को जानना है कि आप suitable हो या नहीं",
    "zipper वाला pouch मिलेगा?",
    "Rate?",
    "आप design में help कर सकते हैं",
    "अभी मुझे पता नहीं है पर आपका MOQ कितना है?",
    "मैडम call back नहीं आप इसी call पे बता दीजिए ना मुझे।",
    "What is your minimum order quantity?",
    "Do you make spout pouches",
    "That would be great madam but first I would like to understand a little bit about pricing.",
])
def test_the_ways_customers_ask_are_recognised(said):
    assert asks_for_info(said)


@pytest.mark.parametrize("said", [
    "हाँ", "हम्म", "ठीक है", "ठीक है?", "अच्छा ठीक है।", "हाँ जी बताइए", "हाँ हाँ ज़रूर ज़रूर बताइए", "जी बोलिए",  # go ahead
    "क्या?", "क्या बोला आपने?", "क्या आप फिर से बोल सकती हो", "Sorry Ma'am repeat करो तो",                      # say it again
    "Can you speak in English please?", "अह sorry हिंदी में बात कर सकते हैं आप",                                 # the language
    "मुझे spices के लिए pouch चाहिए", "महीने के पाँच हज़ार चाहिए", "मेरी company का नाम है Omix", "Omix Foods?",    # answers
    "पता नहीं कितना होगा", "हम्म ठीक है बढ़िया आपकी team मेरे से contact कर सकती है", "",
])
def test_reactions_answers_and_repeats_are_not_asking(said):
    assert not asks_for_info(said)


def test_the_prompt_says_answer_then_wait():
    for direction, purpose in (("outbound", None), ("inbound", None), ("outbound", "callback")):
        sp = static_system_prompt(dict(DEFAULT_AGENT_SETTINGS), direction, purpose)
        assert "ANSWER, THEN WAIT" in sp
        assert "Usually end with one easy question" not in sp and "then ask which one they want" not in sp
        assert "cover it with them, then move on" not in sp
    assert "at most one question" in ENGLISH_TURN_NOTE


# ---------------------------------------------------------------- the note
def test_a_question_gets_the_note_last_and_only_on_that_reply(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["जी, हम custom pouches बनाते हैं। आपका product क्या है?", ANSWER, "जी, आपका product क्या है?"])
        s, _, run, stt = await _started("+919480000901", llm, monkeypatch)
        for i, said in enumerate(("हाँ जी बताइए", "आपका MOQ कितना है?", "अच्छा ठीक है"), 1):
            await stt.say(said)
            await _wait(lambda: len(llm.calls) == i and s.state == "listening")
        await _finish(s, run)
        return llm

    notes = [_notes(c) for c in asyncio.run(scenario()).calls]
    assert ANSWER_THEN_WAIT_NOTE not in notes[0]             # "go ahead": she says why she called and asks
    assert notes[1][-1] == ANSWER_THEN_WAIT_NOTE             # asked: answer, then wait
    assert ANSWER_THEN_WAIT_NOTE not in notes[2]             # reacted: she carries on with her next question


def test_in_english_the_note_comes_after_the_language_note(monkeypatch):
    """Before the language note, English replies still added a question (2 of 4 on the real model)."""
    async def scenario():
        llm = ScriptedLLM(["Our MOQ starts from one thousand pieces."])
        s, _, run, stt = await _started("+919480000902", llm, monkeypatch)
        await stt.say("What is your minimum order quantity?")
        await _wait(lambda: len(llm.calls) == 1 and s.state == "listening")
        await _finish(s, run)
        return llm

    notes = _notes(asyncio.run(scenario()).calls[0])
    assert notes[-2:] == [ENGLISH_TURN_NOTE, ANSWER_THEN_WAIT_NOTE]


def test_asking_our_name_is_not_taken_for_their_company_name(monkeypatch):
    """Call 63: "आपके brand का नाम क्या है?" set off the don't-say-their-company-name note: "ठीक है। ..." """
    async def scenario():
        llm = ScriptedLLM(["जी, आपकी company का नाम क्या है?", "जी, हम PouchWale हैं। आपकी company का नाम क्या है?", "जी, ठीक है।"])
        s, _, run, stt = await _started("+919480000903", llm, monkeypatch)
        for i, said in enumerate(("हाँ जी बताइए", "आपके brand का नाम क्या है?", "Omix Foods"), 1):
            await stt.say(said)
            await _wait(lambda: len(llm.calls) == i and s.state == "listening")
        await _finish(s, run)
        return llm

    calls = asyncio.run(scenario()).calls
    assert COMPANY_NAME_NOTE not in _notes(calls[1]) and _notes(calls[1])[-1] == OUR_NAME_NOTE
    assert COMPANY_NAME_NOTE in _notes(calls[2]) and OUR_NAME_NOTE not in _notes(calls[2])


def test_misheard_words_are_asked_about_first(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["आपने three side seal pouch कहा?"])
        s, _, run, stt = await _session_said(llm, monkeypatch, "+919480000904", "3-5 silk out क्या होता है?")
        await _finish(s, run)
        return llm

    notes = " ".join(_notes(asyncio.run(scenario()).calls[0]))
    assert "misheard" in notes and ANSWER_THEN_WAIT_NOTE not in notes


async def _session_said(llm, monkeypatch, phone: str, said: str, **cfg):
    s, t, run, stt = await _started(phone, llm, monkeypatch, **cfg)
    await stt.say(said)
    await _wait(lambda: len(llm.calls) >= 1 and s.state == "listening")
    return s, t, run, stt


def test_a_reply_started_early_carries_the_same_note(monkeypatch):
    """The early reply (written while the final transcript is on its way) must still be used for a question."""
    monkeypatch.setattr(CallSession, "_maybe_prefetch", REAL_PREFETCH)

    async def scenario():
        llm = ScriptedLLM([ANSWER], delay=0.02)
        s, t, run, stt = await _started("+919480000905", llm, monkeypatch)
        await stt.on_event("speech_start", {})
        await stt.on_event("partial", {"text": "आपका MOQ कितना है?"})
        await stt.on_event("speech_end", {})
        await _wait(lambda: len(llm.calls) == 1)
        await stt.on_event("final", {"text": "आपका MOQ कितना है?"})
        await _wait(lambda: ANSWER in _agent_lines(t) and s.state == "listening")
        await _finish(s, run)
        return llm

    llm = asyncio.run(scenario())
    assert len(llm.calls) == 1 and _notes(llm.calls[0])[-1] == ANSWER_THEN_WAIT_NOTE


# ---------------------------------------------------------------- if they stay quiet
def test_quiet_after_an_answer_gets_one_gentle_check_in(monkeypatch):
    monkeypatch.setattr(session_mod, "ANSWER_WAIT_SEC", 0.4)

    async def scenario():
        llm = ScriptedLLM([ANSWER])
        s, t, run, stt = await _started("+919480000906", llm, monkeypatch)
        await stt.say("आपका MOQ कितना है?")
        await _wait(lambda: bool(_check_ins(t)))
        await asyncio.sleep(1.0)  # no second one
        lines, history = _check_ins(t), list(s.history)
        await _finish(s, run)
        return lines, history

    lines, history = asyncio.run(scenario())
    assert lines == [CHECK_IN_LINES["hindi"][0]]
    assert history[-1] == {"role": "assistant", "content": lines[0]}  # the model knows she said it


def test_the_check_in_is_ready_before_it_is_needed(monkeypatch):
    """Made on the spot it could start just as the customer begins to speak."""
    async def scenario():
        s, _, run, _stt = await _started("+919480000914", ScriptedLLM([ANSWER]), monkeypatch)
        await _wait(lambda: all(f"check_in_hindi_{i}" in s.clips for i in range(len(CHECK_IN_LINES["hindi"]))))
        key = s._check_in_line()[0]
        ready = key in s.clips
        await _finish(s, run)
        return ready

    assert asyncio.run(scenario())


def test_each_check_in_is_a_different_line(monkeypatch):
    monkeypatch.setattr(session_mod, "ANSWER_WAIT_SEC", 0.3)

    async def scenario():
        llm = ScriptedLLM([ANSWER, "जी, sample kit free है।"])
        s, t, run, stt = await _started("+919480000907", llm, monkeypatch)
        await stt.say("आपका MOQ कितना है?")
        await _wait(lambda: len(_check_ins(t)) == 1)
        await _wait(lambda: s.state == "listening")
        await stt.say("sample मिल सकता है क्या?")
        await _wait(lambda: len(_check_ins(t)) == 2)
        lines = _check_ins(t)
        await _finish(s, run)
        return lines

    assert asyncio.run(scenario()) == CHECK_IN_LINES["hindi"][:2]


def test_no_check_in_after_a_reply_that_asked_something(monkeypatch):
    monkeypatch.setattr(session_mod, "ANSWER_WAIT_SEC", 0.3)

    async def scenario():
        llm = ScriptedLLM(["जी, आपका product क्या है?"])
        s, t, run, stt = await _started("+919480000908", llm, monkeypatch)
        await stt.say("हाँ जी बताइए")
        await _wait(lambda: len(llm.calls) == 1 and s.state == "listening")
        await asyncio.sleep(0.9)
        lines = _check_ins(t)
        await _finish(s, run)
        return lines

    assert asyncio.run(scenario()) == []


def test_a_reaction_in_time_moves_the_call_on_without_a_check_in(monkeypatch):
    monkeypatch.setattr(session_mod, "ANSWER_WAIT_SEC", 0.8)

    async def scenario():
        llm = ScriptedLLM([ANSWER, "जी, आपका product क्या है?"])
        s, t, run, stt = await _started("+919480000909", llm, monkeypatch)
        await stt.say("आपका MOQ कितना है?")
        await _wait(lambda: ANSWER in _agent_lines(t) and s.state == "listening")
        await stt.say("अच्छा ठीक है")
        await _wait(lambda: len(llm.calls) == 2 and s.state == "listening")
        await asyncio.sleep(1.1)  # past the wait: her last reply asked something
        lines = _check_ins(t)
        await _finish(s, run)
        return lines

    assert asyncio.run(scenario()) == []


def test_no_check_in_on_a_reply_that_ends_the_call(monkeypatch):
    monkeypatch.setattr(session_mod, "ANSWER_WAIT_SEC", 0.2)

    async def scenario():
        llm = ScriptedLLM(["जी, आपका time देने के लिए thank you! [END_CALL]"])
        s, t, run, stt = await _started("+919480000910", llm, monkeypatch)
        await stt.say("ठीक है thank you, bye")
        await asyncio.wait_for(run, timeout=5)
        return _check_ins(t), s.end_reason

    lines, reason = asyncio.run(scenario())
    assert lines == [] and reason == "agent_ended"


def test_the_silence_hang_up_still_works_after_the_check_in(monkeypatch):
    monkeypatch.setattr(session_mod, "ANSWER_WAIT_SEC", 0.3)

    async def scenario():
        llm = ScriptedLLM([ANSWER])
        s, t, run, stt = await _started("+919480000911", llm, monkeypatch, silence_timeout_sec=0.7,
                                        silence_final_wait_sec=0.3)
        await stt.say("आपका MOQ कितना है?")
        await asyncio.wait_for(run, timeout=8)
        return _agent_lines(t), s.end_reason

    lines, reason = asyncio.run(scenario())
    at = lines.index(ANSWER)
    assert lines[at + 1] == CHECK_IN_LINES["hindi"][0]                       # first the gentle one
    assert lines[at + 2] == DEFAULT_AGENT_SETTINGS["silence_prompt_text"]     # then the usual line check
    assert reason == "silence_timeout"


def test_a_short_silence_timeout_keeps_the_normal_line_check(monkeypatch):
    """A team that set the silence timeout below the wait gets exactly what it set."""
    monkeypatch.setattr(session_mod, "ANSWER_WAIT_SEC", 0.6)

    async def scenario():
        llm = ScriptedLLM([ANSWER])
        s, t, run, stt = await _started("+919480000912", llm, monkeypatch, silence_timeout_sec=0.3,
                                        silence_final_wait_sec=0.3)
        await stt.say("आपका MOQ कितना है?")
        await asyncio.wait_for(run, timeout=8)
        return _check_ins(t), s.end_reason

    lines, reason = asyncio.run(scenario())
    assert lines == [] and reason == "silence_timeout"


def test_an_english_call_gets_an_english_check_in(monkeypatch):
    monkeypatch.setattr(session_mod, "ANSWER_WAIT_SEC", 0.3)

    async def scenario():
        llm = ScriptedLLM(["Sure, what is your product?", "Our MOQ starts from one thousand pieces."])
        s, t, run, stt = await _started("+919480000913", llm, monkeypatch)
        await stt.say("Can you speak in English please?")
        await _wait(lambda: len(llm.calls) == 1 and s.state == "listening")
        await stt.say("What is your minimum order quantity?")
        await _wait(lambda: bool(_check_ins(t, "english")))
        lines = _check_ins(t, "english"), _check_ins(t)
        await _finish(s, run)
        return lines

    english, hindi = asyncio.run(scenario())
    assert english == [CHECK_IN_LINES["english"][0]] and hindi == []
