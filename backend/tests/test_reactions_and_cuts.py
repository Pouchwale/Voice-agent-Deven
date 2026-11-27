"""Call 77 (2026-11-24): while she answered "your MOQ?", the customer reacted ("अच्छा, ओह", heard as "Hello"). She
stopped, and then went on to her next question as if the MOQ answer had been given; the customer had to ask again.

- a reaction ("अच्छा ओह", "हाँ हाँ हाँ", "ओके जी, समझ गया") and, while she is audibly talking, "Hello?" never stop her
- a reply that is cut off is remembered as cut: the conversation keeps only what the customer heard, and the next reply
  is told to finish it (unless they asked something new); once that reply is heard, the note goes
- a reply started early is used when the final transcript only spells the same words differently
- the HTTPS connection to OpenAI is kept between a call's replies
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.services import llm as llm_mod
from app.services import session as session_mod
from app.services.session import CallSession, content_words, could_be_reaction, heard_part, is_backchannel

from .fakes import FakeSTT, FakeTTSStream, ScriptedLLM, tone
from .test_humanize_live import REAL_PREFETCH, _agent_lines, _session, _wait, fakes  # noqa: F401  (autouse fixture)

MOQ_ANSWER = "MOQ एक हज़ार pieces से start होता है। इससे आप छोटी quantity में भी quality देख सकते हैं।"
CUT_NOTE = "Your last answer was cut off"


# ---------------------------------------------------------------- what counts as a reaction
@pytest.mark.parametrize("text", ["अच्छा ओह", "ओह अच्छा अच्छा", "हाँ हाँ हाँ", "हाँ जी बिल्कुल", "ओके जी, समझ गया",
                                  "ठीक है जी", "अरे वाह", "सही है", "oh okay", "हम्म"])
def test_reactions(text):
    assert is_backchannel(text) and could_be_reaction(text)


@pytest.mark.parametrize("text", ["नहीं नहीं", "रुकिए", "अरे सुनिए", "अच्छा तो MOQ कितना है", "एक second",
                                  "हाँ हाँ हाँ हाँ हाँ हाँ"])  # more than five in a row is talking, not reacting
def test_not_reactions(text):
    assert not is_backchannel(text) and not could_be_reaction(text)


def test_a_partial_growing_into_a_reaction_is_not_yet_an_interruption():
    assert could_be_reaction("ओके जी समझ") and not is_backchannel("ओके जी समझ")  # "...गया" still to come
    assert could_be_reaction("है") and not is_backchannel("है")                # a lone "है" is not a reaction


def test_hello_is_a_reaction_only_while_she_is_talking():
    assert is_backchannel("Hello?", greeting=True) and not is_backchannel("Hello?")
    assert could_be_reaction("Hello", greeting=True) and not could_be_reaction("Hello")


# ---------------------------------------------------------------- what was heard of a cut reply
S1, S2 = "MOQ एक हज़ार pieces से start होता है।", "इससे आप छोटी quantity में भी quality देख सकते हैं।"


def test_heard_part_counts_whole_sentences_and_the_played_share_of_the_cut_one():
    # 1 s into the second sentence's 3 s: a third of its 10 words
    assert heard_part([S1, S2], [12.0, 15.0], 10.0, 13.0) == (S1 + " इससे आप छोटी", "quantity में भी quality देख सकते हैं।")
    assert heard_part([S1, S2], [12.0, 15.0], 10.0, 11.0) == ("MOQ एक हज़ार pieces", "से start होता है। " + S2)
    assert heard_part([S1, S2], [12.0, 15.0], 10.0, 16.0) == (S1 + " " + S2, "")  # heard to the end
    assert heard_part([S1, S2], [12.0, 15.0], 10.0, 10.0) == ("", S1 + " " + S2)  # cut as it began


def test_heard_part_uses_her_speaking_rate_while_the_sentence_is_still_arriving():
    heard, rest = heard_part([S1], [], 10.0, 11.0)  # no end yet: ~2.5 words a second
    assert heard == "MOQ एक" and rest == "हज़ार pieces से start होता है।"


@pytest.mark.parametrize("said, finish", [
    ("अच्छा", True), ("ओह अच्छा", True), ("हाँ हाँ", True), ("Hello?", True), ("हेलो हेलो", True),
    ("पाँच हज़ार", False), ("sample मिलेगा क्या?", False), ("हाँ हाँ, पता है।", False), ("एक second रुकिए", False),
])
def test_a_cut_answer_is_finished_only_after_a_reaction(said, finish):
    """Measured on the real model: asked to decide by itself, it finished the cut answer even when they had answered
    or said "पता है" (0 of 4 right); decided here, 28 of 28 replies were right."""
    assert session_mod.finish_cut_answer(said, S1 + " " + S2) is finish


def test_missing_only_the_end_of_her_question_is_not_finished():
    """Their "हाँ" answers a question whose gist they heard ("क्या मैं आपसे 2 मिनट बात ..." -> "हाँ")."""
    assert not session_mod.finish_cut_answer("हाँ", "क्या मैं आपसे 2 मिनट बात कर सकती हूँ?")
    assert session_mod.finish_cut_answer("हाँ", "इससे आप quality देख सकते हैं। आपको कितने चाहिए?")


class _Cut:
    uuid = "test"
    _cut_answer = None


def test_a_cut_on_her_last_word_or_two_counts_as_heard():
    s = _Cut()
    said = CallSession._note_cut(s, [S1, S2], [12.0, 15.0], 10.0, 14.6, "MOQ?")  # ~1 word of S2 left
    assert said == S1 + " " + S2 and s._cut_answer is None
    said = CallSession._note_cut(s, [S1, S2], [12.0, 15.0], 10.0, 13.0, "MOQ?")
    assert said == S1 + " इससे आप छोटी …" and s._cut_answer["rest"] == S2  # missed: S2 from its start


# ---------------------------------------------------------------- a reaction never stops her
@pytest.mark.parametrize("partials, final", [
    (["अच्छा", "अच्छा ओह"], "अच्छा, ओह।"),
    (["हाँ", "हाँ हाँ", "हाँ हाँ हाँ"], "हाँ हाँ हाँ"),
    (["ओके", "ओके जी", "ओके जी समझ", "ओके जी समझ गया"], "ओके जी, समझ गया।"),
    (["Hello"], "Hello?"),  # call 77: the reaction was heard as "Hello"
])
def test_a_reaction_while_she_answers_never_stops_her(monkeypatch, partials, final):
    monkeypatch.setattr(FakeTTSStream, "ms_per_sentence", 1500)

    async def scenario():
        llm = ScriptedLLM([MOQ_ANSWER, "जी।"])
        s, t = await _session(f"+91948000410{len(partials)}{len(final)}", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        said = time.monotonic()
        await stt.say("आपका MOQ कितना है?")
        await _wait(lambda: s._turn_started_at > said and s._turn_first_audio > s._turn_started_at, timeout=4)
        await stt.on_event("speech_start", {})
        for p in partials:
            await stt.on_event("partial", {"text": p})
        await stt.on_event("speech_end", {})
        await stt.on_event("final", {"text": final})
        await asyncio.sleep(0.3)
        result = (t.clears, s.agent_audible(), len(llm.calls), s.barge_ins)
        await _wait(lambda: s.state == "listening", timeout=6)  # she finishes the answer
        whole = [m["content"] for m in s.history if m["role"] == "assistant"][-1]
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return result, whole

    (clears, audible, calls, barge_ins), whole = asyncio.run(scenario())
    assert clears == 0 and audible and barge_ins == 0  # not stopped
    assert calls == 1  # and the reaction is not a turn of its own
    assert whole == MOQ_ANSWER


def test_a_reaction_in_her_last_second_waits_for_her_to_finish(monkeypatch):
    """"हाँ" as she finishes a question is their answer: it waits for her last words instead of cutting them."""
    monkeypatch.setattr(FakeTTSStream, "ms_per_sentence", 1200)

    async def scenario():
        llm = ScriptedLLM(["आप किस product के लिए pouch ढूंढ रहे हैं?", "जी, बताइए।"])
        s, t = await _session("+919480004007", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        said = time.monotonic()
        await stt.say("मुझे pouch चाहिए")
        await _wait(lambda: s._turn_started_at > said and s._turn_first_audio > s._turn_started_at, timeout=4)
        await _wait(lambda: 0 < s.play_until - time.monotonic() < 0.8, timeout=4)  # her last second
        await stt.on_event("speech_start", {})
        await stt.on_event("partial", {"text": "हाँ"})
        await stt.on_event("speech_end", {})
        await stt.on_event("final", {"text": "हाँ"})
        await asyncio.sleep(0.1)
        clears = t.clears
        await _wait(lambda: len(llm.calls) == 2, timeout=4)  # answered once she has finished
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return clears, llm

    clears, llm = asyncio.run(scenario())
    assert clears == 0
    assert [m for m in llm.calls[1] if m["role"] == "user"][-1]["content"] == "हाँ"


@pytest.mark.parametrize("partial, stops", [("अच्छा अच्छा", False), ("", True)])
def test_a_long_reaction_does_not_trip_the_talking_over_her_backstop(monkeypatch, partial, stops):
    """The backstop stops her when the customer has talked over her for 1 s with no transcript yet; a transcript that
    is a reaction keeps her talking."""
    monkeypatch.setattr(FakeTTSStream, "ms_per_sentence", 2500)

    async def scenario():
        llm = ScriptedLLM([MOQ_ANSWER])
        s, t = await _session("+91948000420" + ("1" if partial else "2"), llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        said = time.monotonic()
        await stt.say("आपका MOQ कितना है?")
        await _wait(lambda: s._turn_started_at > said and s._turn_first_audio > s._turn_started_at, timeout=4)
        await stt.on_event("speech_start", {})
        if partial:
            await stt.on_event("partial", {"text": partial})
        frame = tone(20, t.in_rate, amp=9000)
        end = time.monotonic() + 1.4
        while time.monotonic() < end:  # 1.4 s of their voice, no final yet
            s.on_user_audio(frame)
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.1)
        clears = t.clears
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return clears

    assert (asyncio.run(scenario()) > 0) is stops


# ---------------------------------------------------------------- a cut reply is remembered as cut
def _assistant(messages: list[dict]) -> str:
    return [m["content"] for m in messages if m["role"] == "assistant"][-1]


def test_a_reply_cut_while_playing_is_kept_as_heard_and_finished_next(monkeypatch):
    monkeypatch.setattr(FakeTTSStream, "ms_per_sentence", 1500)

    async def scenario():
        llm = ScriptedLLM([MOQ_ANSWER, "जी, तो इससे आप छोटी quantity में भी quality देख सकते हैं।", "जी, बताइए।"])
        s, t = await _session("+919480004003", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        said = time.monotonic()
        await stt.say("आपका MOQ कितना है?")
        await _wait(lambda: s._turn_started_at > said and s._turn_first_audio > s._turn_started_at, timeout=4)
        await asyncio.sleep(1.8)  # the first sentence (1.5 s) is heard, the second has begun
        assert s._playing is not None  # written in full, now playing: the cut is noted by interrupt()
        await stt.on_event("speech_start", {})
        await stt.on_event("partial", {"text": "मैं"})  # a sound that cuts her...
        assert t.clears == 1
        await stt.on_event("final", {"text": "अच्छा"})  # ...and was only a reaction
        await _wait(lambda: len(llm.calls) == 2, timeout=4)
        await _wait(lambda: s.state == "listening", timeout=6)  # the reply that finishes it is heard
        await stt.say("ठीक है")
        await _wait(lambda: len(llm.calls) == 3, timeout=4)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return llm

    llm = asyncio.run(scenario())
    kept = _assistant(llm.calls[1])
    assert kept.startswith(S1) and kept.endswith("…") and kept != MOQ_ANSWER  # only what they heard, marked as cut
    note = llm.calls[1][-1]["content"]
    assert note.startswith(CUT_NOTE) and f'missed "{S2}"' in note  # the sentence they were cut off in, from its start
    assert not any(CUT_NOTE in m["content"] for m in llm.calls[2])  # finished: the note is gone


def test_a_reply_cut_while_still_being_written_is_remembered_too(monkeypatch):
    monkeypatch.setattr(FakeTTSStream, "ms_per_sentence", 3000)
    first = "MOQ एक हज़ार pieces से start होता है और इससे आप छोटी quantity में भी quality देख सकते हैं।"

    async def scenario():
        llm = ScriptedLLM([first + " " + " ".join(["और"] * 40), "जी।"], delay=0.08)  # still being written when cut
        s, t = await _session("+919480004004", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        said = time.monotonic()
        await stt.say("आपका MOQ कितना है?")
        await _wait(lambda: s._turn_started_at > said and s._turn_first_audio > s._turn_started_at, timeout=4)
        await asyncio.sleep(1.7)  # past the false-start window (1.5 s), in the middle of the first sentence
        writing = s.responding() and s._playing is None  # the reply is still being written
        await stt.on_event("speech_start", {})
        await stt.on_event("partial", {"text": "मैं"})
        await stt.on_event("final", {"text": "ओह अच्छा"})
        await _wait(lambda: len(llm.calls) == 2, timeout=4)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return llm, writing, s.false_starts

    llm, writing, false_starts = asyncio.run(scenario())
    assert writing and false_starts == 0
    note = llm.calls[1][-1]["content"]
    assert note.startswith(CUT_NOTE) and f'missed "{first}"' in note


def test_a_reply_heard_to_the_end_leaves_no_note(monkeypatch):
    async def scenario():
        llm = ScriptedLLM([MOQ_ANSWER, "जी।"])
        s, t = await _session("+919480004005", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.say("आपका MOQ कितना है?")
        await _wait(lambda: len(llm.calls) == 1 and s.state == "listening" and len(_agent_lines(t)) >= 2, timeout=6)
        await stt.say("अच्छा")
        await _wait(lambda: len(llm.calls) == 2, timeout=4)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return llm, s

    llm, s = asyncio.run(scenario())
    assert _assistant(llm.calls[1]) == MOQ_ANSWER and not any(CUT_NOTE in m["content"] for m in llm.calls[1])
    assert s._cut_answer is None


# ---------------------------------------------------------------- the reply started early, same words spelled differently
@pytest.mark.parametrize("partial, final", [("ओके", "okay"), ("आ शाम को पांच बजे", "आ शाम को 5 बजे"),
                                            ("जी बताइए", "हां जी बताइए"), ("अच्छा", "अच्छा अच्छा")])
def test_same_content_words(partial, final):
    assert content_words(partial) == content_words(final)


@pytest.mark.parametrize("partial, final", [("मैं", "मैं milk powder के लिए"), ("टेंडर द पाउच", "stand up pouch"),
                                            ("pouch है", "pouch नहीं है")])
def test_different_content_words(partial, final):
    assert content_words(partial) != content_words(final)


def test_an_early_reply_is_used_when_the_final_only_spells_it_differently(monkeypatch):
    monkeypatch.setattr(CallSession, "_maybe_prefetch", REAL_PREFETCH)

    async def scenario():
        llm = ScriptedLLM(["जी, पाँच हज़ार pouches, बढ़िया। कब तक चाहिए?"], delay=0.02)
        s, t = await _session("+919480004006", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.on_event("speech_start", {})
        await stt.on_event("partial", {"text": "मुझे पांच हज़ार pouch चाहिए"})
        await stt.on_event("speech_end", {})
        await _wait(lambda: len(llm.calls) == 1)
        await asyncio.sleep(0.3)
        await stt.on_event("final", {"text": "मुझे 5 हज़ार pouch चाहिए।"})
        await _wait(lambda: any("कब तक चाहिए" in x for x in _agent_lines(t)))
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return s, llm

    s, llm = asyncio.run(scenario())
    assert s.prefetch_used == 1 and len(llm.calls) == 1


# ---------------------------------------------------------------- the connection to OpenAI
def test_the_openai_connection_is_kept_between_replies():
    pool = llm_mod.client()._client._transport._pool
    assert pool._keepalive_expiry == llm_mod.OPENAI_KEEPALIVE_SEC >= 60  # replies come 5-30 s apart
