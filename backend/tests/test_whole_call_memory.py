"""Call 82 (2026-11-24, 8 minutes): after 5 minutes she asked again for the product, the pouch type and the variants
the customer had given in the first minute ("मैंने आपके पिछले 5 minute से मखाना के लिए बात की है"). The AI model saw only
the last 24 messages of the call; it now sees the whole call."""
from __future__ import annotations

import asyncio

from app.services import session as session_mod

from .fakes import FakeSTT, ScriptedLLM
from .test_humanize_live import _session, _wait, fakes  # noqa: F401  (autouse fixture)


def test_the_ai_sees_the_whole_call_not_only_the_last_messages(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["जी, ठीक है।"] * 20)
        s, t = await _session("+919480005001", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.say("मखाना के लिए standup pouch चाहिए")  # the product and pouch type, in the first minute
        await _wait(lambda: len(llm.calls) == 1 and s.state == "listening", timeout=6)
        for i in range(15):  # a long call: 15 more exchanges, 32 messages in all
            await stt.say(f"अगली बात नंबर {i} के बारे में बताइए")
            await _wait(lambda: len(llm.calls) == i + 2 and s.state == "listening", timeout=6)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return llm

    llm = asyncio.run(scenario())
    last = llm.calls[-1]
    said = [m["content"] for m in last if m["role"] == "user"]
    assert len(said) == 16
    assert said[0] == "मखाना के लिए standup pouch चाहिए"  # still in front of the model at the end of the call


def test_the_bound_is_far_beyond_a_real_call():
    assert session_mod.MAX_HISTORY_MESSAGES >= 150  # a 10-minute call is ~100 messages
