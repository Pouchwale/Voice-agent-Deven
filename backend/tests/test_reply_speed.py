"""Reply speed (calls 74-76, 2026-11-24): the answer came 2.0-2.4 s after the customer stopped, 4.9-6.6 s when the AI
model stalled on its first word, and a late filler sometimes made a ready answer wait behind it.

- a request with no first word after HEDGE_SEC gets a second, identical request racing it; the loser is stopped
- a filler never plays once the answer's first words are with the voice engine
- every answered turn writes one timing line to the local log
"""
from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace

from app.services import llm as llm_mod
from app.services import session as session_mod
from app.services.session import CallSession

from .fakes import FakeSTT, FakeTTSStream, ScriptedLLM, tone
from .test_humanize_live import REAL_PREFETCH, _agent_lines, _session, _wait, fakes  # noqa: F401  (autouse fixture)


class SlowFirst(ScriptedLLM):
    """Request n waits stall[n] s before its first word (raises instead when the stall is an exception); it records
    the requests that were stopped before they finished."""

    def __init__(self, replies, stall, delay=0.005):
        super().__init__(replies, delay=delay)
        self.stall = list(stall)
        self.stopped: list[int] = []

    async def __call__(self, messages, cache_key=None):
        n = len(self.calls)
        self.calls.append(messages)
        reply = self.replies[n] if n < len(self.replies) else "जी।"
        stall = self.stall[n] if n < len(self.stall) else 0
        try:
            if isinstance(stall, BaseException):
                raise stall
            await asyncio.sleep(stall)
            for word in reply.split(" "):
                await asyncio.sleep(self.delay)
                yield word + " "
        except (asyncio.CancelledError, GeneratorExit):
            self.stopped.append(n)
            raise


async def _ask(text: str, llm, monkeypatch, phone: str, **cfg):
    """One customer line; returns (seconds until the agent's line was written, the agent's lines, the session, the
    requests stopped by then). Read before the call ends: ending it stops every task anyway."""
    s, t = await _session(phone, llm, monkeypatch, **cfg)
    run = asyncio.create_task(s.run())
    await _wait(lambda: s.greeted and s.state == "listening")
    said = time.monotonic()
    await FakeSTT.instances[-1].say(text)
    await _wait(lambda: len(_agent_lines(t)) >= 2, timeout=8)  # the greeting, then the answer
    took = time.monotonic() - said
    await asyncio.sleep(0.15)  # the loser's cancellation lands
    stopped = sorted(llm.stopped)
    await s.end("manual_hangup")
    await asyncio.wait_for(run, timeout=5)
    return took, _agent_lines(t), s, stopped


def test_the_race_starts_before_the_filler_moment():
    assert 0 < session_mod.HEDGE_SEC < session_mod.FIRST_TOKEN_SEC[0] <= 2.5


def test_a_stalled_first_word_gets_a_second_request_that_answers(monkeypatch):
    llm = SlowFirst(["पहला जवाब कभी नहीं आता।", "जी, MOQ एक हज़ार pieces से start होता है।"], stall=[30, 0])
    took, lines, _, stopped = asyncio.run(_ask("आपका MOQ कितना है?", llm, monkeypatch, "+919480003001"))
    assert "MOQ एक हज़ार pieces" in lines[-1]
    assert len(llm.calls) == 2
    assert took < session_mod.HEDGE_SEC + 0.9  # before: 2.5 s of waiting, then a fresh request
    assert stopped == [0]  # the stalled request was stopped as soon as the other one answered


def test_a_quick_first_word_sends_one_request_only(monkeypatch):
    llm = SlowFirst(["जी, बिल्कुल, हम design भी करते हैं।"], stall=[0.4])
    _, lines, _, stopped = asyncio.run(_ask("आप design भी करते हो?", llm, monkeypatch, "+919480003002"))
    assert "design भी करते हैं" in lines[-1]
    assert len(llm.calls) == 1 and stopped == []


def test_a_failed_request_is_asked_again_instead_of_apologising(monkeypatch):
    llm = SlowFirst(["(never said)", "जी, sample kit free है।"], stall=[RuntimeError("OpenAI 500"), 0])
    _, lines, _, _ = asyncio.run(_ask("sample मिलेगा?", llm, monkeypatch, "+919480003003"))
    assert "sample kit free" in lines[-1] and session_mod.CUT_LINE not in lines
    assert len(llm.calls) == 2


def test_two_failed_requests_end_in_the_apology(monkeypatch):
    llm = SlowFirst([], stall=[RuntimeError("OpenAI down"), RuntimeError("OpenAI down")])
    _, lines, _, _ = asyncio.run(_ask("sample मिलेगा?", llm, monkeypatch, "+919480003004"))
    assert lines[-1] == session_mod.CUT_LINE
    assert len(llm.calls) == 2


def test_at_most_two_fresh_requests_per_reply(monkeypatch):
    """One request stalls and the racing one fails: no third request (an outage must not multiply the load); the
    stalled one gets until the limit, then the apology."""
    monkeypatch.setattr(session_mod, "HEDGE_SEC", 0.1)
    monkeypatch.setattr(session_mod, "FIRST_TOKEN_SEC", (0.4, 0.6))
    llm = SlowFirst(["(stalls)", "(fails)", "जी, तीसरा जवाब।"], stall=[30, RuntimeError("OpenAI 500"), 0])
    _, lines, _, stopped = asyncio.run(_ask("sample मिलेगा?", llm, monkeypatch, "+919480003010"))
    assert len(llm.calls) == 2 and lines[-1] == session_mod.CUT_LINE
    assert stopped == [0]


def test_no_first_word_at_all_gives_up_after_the_limit_with_a_filler_on_the_way(monkeypatch):
    monkeypatch.setattr(session_mod, "HEDGE_SEC", 0.1)
    monkeypatch.setattr(session_mod, "FIRST_TOKEN_SEC", (0.4, 0.6))
    llm = SlowFirst([], stall=[30, 30])

    async def scenario():
        s, t = await _session("+919480003005", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening" and any(k.startswith("filler_") for k in s.clips))
        await FakeSTT.instances[-1].say("आपका MOQ कितना है?")
        await _wait(lambda: len(_agent_lines(t)) >= 2, timeout=6)
        filler = s._filler_played
        await asyncio.sleep(0.15)
        stopped = sorted(llm.stopped)  # before the call ends (that stops every task anyway)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return _agent_lines(t), filler, stopped

    lines, filler, stopped = asyncio.run(scenario())
    assert lines[-1] == session_mod.CUT_LINE and filler
    assert len(llm.calls) == 2 and stopped == [0, 1]


def test_a_stalled_early_reply_is_raced_from_when_it_started(monkeypatch):
    monkeypatch.setattr(CallSession, "_maybe_prefetch", REAL_PREFETCH)
    llm = SlowFirst(["(early, never said)", "जी, पाँच हज़ार pouches, बढ़िया। कब तक चाहिए?"], stall=[30, 0], delay=0.01)

    async def scenario():
        s, t = await _session("+919480003006", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.on_event("speech_start", {})
        await stt.on_event("partial", {"text": "मुझे पाँच हज़ार pouch चाहिए"})
        stopped_talking = time.monotonic()
        await stt.on_event("speech_end", {})
        await _wait(lambda: len(llm.calls) == 1)  # the early reply is being written
        await asyncio.sleep(0.8)  # a late final (on real calls 0.2-1.0 s after the end of speech)
        await stt.on_event("final", {"text": "मुझे पाँच हज़ार pouch चाहिए।"})
        await _wait(lambda: any("कब तक चाहिए" in x for x in _agent_lines(t)), timeout=6)
        took = time.monotonic() - stopped_talking
        used = s.prefetch_used
        await asyncio.sleep(0.15)
        stopped = sorted(llm.stopped)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return took, used, stopped

    took, used, stopped = asyncio.run(scenario())
    assert used == 1  # the early reply was the right one, it was only slow
    assert len(llm.calls) == 2 and stopped == [0]
    assert took < session_mod.HEDGE_SEC + 0.5  # counted from when the early reply started, not from the final (+0.8 s)


class SlowVoice(FakeTTSStream):
    """The first sound of a sentence comes `lag` s after its words were sent."""

    lag = 0.6

    async def send_sentence(self, text: str):
        FakeTTSStream.sentences.append(text)
        self.sent.append(text)
        index = len(self._pending)
        previous = self._pending[-1] if self._pending else None

        async def deliver():
            await asyncio.sleep(self.lag)
            if previous is not None:
                await asyncio.gather(previous, return_exceptions=True)
            pcm = tone(self.ms_per_sentence, self.rate)
            self.audio_bytes += len(pcm)
            await self.on_audio(pcm)
            self._finals = index + 1
            if self.on_sentence_end is not None:
                await self.on_sentence_end(self._finals)
        self._pending.append(asyncio.create_task(deliver()))


def test_no_filler_once_the_answer_is_with_the_voice_engine(monkeypatch):
    monkeypatch.setattr(session_mod, "TTSStream", SlowVoice)

    async def scenario():
        llm = ScriptedLLM(["जी, बिल्कुल, हम custom design भी करते हैं।"], delay=0.01)
        s, t = await _session("+919480003007", llm, monkeypatch, filler_delay_ms=300)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening" and any(k.startswith("filler_") for k in s.clips))
        said = time.monotonic()
        await FakeSTT.instances[-1].say("आप design भी करते हो?")
        # this answer's own first sound (~0.7 s: words at ~0.1 s, the voice 0.6 s later), well past the filler moment;
        # the greeting's sound must not count
        await _wait(lambda: s._turn_started_at > said and s._turn_first_audio > s._turn_started_at, timeout=5)
        assert s._turn_first_audio - said > 0.45
        await asyncio.sleep(0.1)
        result = (s._filler_played, s._turn_sent_at > 0)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return result

    filler, sent = asyncio.run(scenario())
    assert sent and not filler  # before: the filler played at 0.3 s and the answer waited behind it


def test_a_slow_answer_still_gets_its_filler(monkeypatch):
    monkeypatch.setattr(session_mod, "TTSStream", SlowVoice)

    async def scenario():
        llm = ScriptedLLM(["हम custom design भी करते हैं और printing भी करते हैं।"], delay=0.15)  # no words yet at 0.3 s
        s, t = await _session("+919480003008", llm, monkeypatch, filler_delay_ms=300)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening" and any(k.startswith("filler_") for k in s.clips))
        await FakeSTT.instances[-1].say("आप design भी करते हो?")
        await _wait(lambda: s._filler_played, timeout=4)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)

    asyncio.run(scenario())


def test_a_reply_cut_off_mid_way_stops_its_request(monkeypatch):
    llm = SlowFirst([" ".join(["जी, हम हर तरह के pouch बनाते हैं।"] + ["और भी बहुत कुछ बताना है"] * 12)], stall=[0],
                    delay=0.05)

    async def scenario():
        s, t = await _session("+919480003011", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        said = time.monotonic()
        await FakeSTT.instances[-1].say("आप क्या क्या बनाते हो?")
        await _wait(lambda: s._turn_started_at > said and s._turn_first_audio > s._turn_started_at, timeout=5)
        await s.interrupt("speech")  # the customer talks over her
        await asyncio.sleep(0.3)
        stopped = sorted(llm.stopped)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return stopped

    assert asyncio.run(scenario()) == [0]  # not left writing (and billing) the rest of a reply nobody hears


def test_each_answered_turn_logs_where_its_silence_went(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="session")
    llm = SlowFirst(["जी, बिल्कुल।"], stall=[0.05])
    _, _, s, _ = asyncio.run(_ask("आप design भी करते हो?", llm, monkeypatch, "+919480003009"))
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith(f"timing on {s.uuid}")]
    assert len(lines) == 1
    line = lines[0]
    for part in ("speech end", "final", "reply start", "first word", "(request 1)", "words to voice", "first audio",
                 "filler -"):
        assert part in line, part


def test_a_stream_stopped_early_is_closed(monkeypatch):
    closed: list[bool] = []

    class Stream:
        def __init__(self):
            self.n = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.n += 1
            if self.n > 20:
                raise StopAsyncIteration
            return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=f"w{self.n} "))])

        async def close(self):
            closed.append(True)

    class Completions:
        async def create(self, **kw):
            return Stream()

    monkeypatch.setattr(llm_mod, "client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=Completions())))

    async def stopped_early():
        gen = llm_mod.stream_reply([{"role": "user", "content": "x"}])
        first = await anext(gen)
        await gen.aclose()
        return first

    async def read_all():
        return [w async for w in llm_mod.stream_reply([{"role": "user", "content": "x"}])]

    assert asyncio.run(stopped_early()) == "w1 " and closed == [True]
    assert len(asyncio.run(read_all())) == 20 and closed == [True, True]
