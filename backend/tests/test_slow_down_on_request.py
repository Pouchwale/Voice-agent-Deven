"""Call 82 (2026-11-24): "क्या आप थोड़ा धीरे बात कर सकते हैं आपकी speed बहुत fast है" - she said "मैं थोड़ी धीरे बोलती हूँ"
and her voice stayed as fast. Her normal pace stays quick, like a person on the phone (the team's wish); after a request
to slow down or to repeat, the rest of the call is a little slower (SLOW_DOWN_PACE)."""
from __future__ import annotations

import asyncio

import pytest

from app.services import session as session_mod
from app.services.session import SLOW_DOWN_PACE, asks_to_slow_down

from .fakes import FakeSTT, FakeTTSStream, ScriptedLLM
from .test_humanize_live import _session, _wait, fakes  # noqa: F401  (autouse fixture)


@pytest.mark.parametrize("text", [
    "क्या आप थोड़ा धीरे बात कर सकते हैं आपकी speed बहुत fast है।", "थोड़ा धीरे बोलिए", "धीरे से बताइए", "आराम से बोलिए",
    "आप बहुत fast बोल रही हो", "जल्दी जल्दी मत बोलिए", "can you speak slowly", "slow down please", "you talk too fast",
    "फिर से बोलिए", "दोबारा बोलिए", "क्या बोला आपने?", "please repeat", "say that again",
])
def test_asks_to_slow_down(text):
    assert asks_to_slow_down(text)


@pytest.mark.parametrize("text", [
    "धीरे धीरे business बढ़ रहा है", "delivery speed कितनी है?", "हमें जल्दी चाहिए", "fast delivery चाहिए",
    "मुझे standup pouch चाहिए", "हाँ जी बोलिए",
])
def test_not_a_request_to_slow_down(text):
    assert not asks_to_slow_down(text)


def test_slower_after_the_request_and_for_the_rest_of_the_call(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["जी, हम custom pouch बनाते हैं।", "जी, मैं थोड़ा धीरे बोलती हूँ।", "जी, MOQ एक हज़ार pieces है।"])
        s, t = await _session("+919480006001", llm, monkeypatch, natural_variation=False, tts_pace=1.0)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.say("आप क्या बनाते हो?")
        await _wait(lambda: len(llm.calls) == 1 and s.state == "listening", timeout=6)
        before = FakeTTSStream.configs[-1]["tts_pace"]  # the socket ready for the next reply, at her normal pace
        seen = len(FakeTTSStream.configs)
        await stt.say("क्या आप थोड़ा धीरे बात कर सकते हैं आपकी speed बहुत fast है")
        await _wait(lambda: len(llm.calls) == 2 and s.state == "listening", timeout=6)
        await stt.say("MOQ कितना है?")
        await _wait(lambda: len(llm.calls) == 3 and s.state == "listening", timeout=6)
        after = [c["tts_pace"] for c in FakeTTSStream.configs[seen:]]
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return before, after

    before, after = asyncio.run(scenario())
    assert before == 1.0
    assert after and all(p == pytest.approx(SLOW_DOWN_PACE) for p in after)  # that reply and every later one


class Recording(FakeTTSStream):
    used: list["Recording"] = []

    def __init__(self, cfg, sample_rate, on_audio):
        super().__init__(cfg, sample_rate, on_audio)
        Recording.used.append(self)


def test_the_reply_to_the_request_itself_is_slower(monkeypatch):
    """Not only later replies: the voice socket opened before the request (at the old pace) is not used for it."""
    monkeypatch.setattr(session_mod, "TTSStream", Recording)
    Recording.used.clear()

    async def scenario():
        llm = ScriptedLLM(["जी, हम custom pouch बनाते हैं।", "जी, मैं थोड़ा धीरे बोलती हूँ।"])
        s, t = await _session("+919480006002", llm, monkeypatch, natural_variation=False, tts_pace=1.0)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.say("आप क्या बनाते हो?")
        await _wait(lambda: len(llm.calls) == 1 and s.state == "listening", timeout=6)
        await stt.say("थोड़ा धीरे बोलिए")
        await _wait(lambda: len(llm.calls) == 2 and s.state == "listening", timeout=6)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)

    asyncio.run(scenario())
    first = next(r for r in Recording.used if any("custom pouch" in x for x in r.sent))
    reply = next(r for r in Recording.used if any("धीरे बोलती" in x for x in r.sent))
    assert first.cfg["tts_pace"] == 1.0 and reply.cfg["tts_pace"] == pytest.approx(SLOW_DOWN_PACE)


def test_her_normal_pace_is_untouched():
    assert 0.8 <= session_mod.SLOW_DOWN_PACE < 1.0
