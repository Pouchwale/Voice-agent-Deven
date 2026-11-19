""""Don't call me" is respected for the rest of the call, and the agent never promises to send things itself.

Real calls behind these: call 45 (the customer said "अरे मत call करना आप।" and the agent asked "आपको कब call
करूँ?" again, then they hung up) and calls 38 / 43 / 44 (the agent said it would send the details itself,
but nothing sends WhatsApp messages: the team does that after the call).
"""
from __future__ import annotations

import asyncio
import copy

from app.defaults import DEFAULT_AGENT_SETTINGS, LEGACY_DEFAULTS
from app.defaults_legacy import V6_TEXTS
from app.services.agent_config import upgrade_saved
from app.services.prompts import NO_CALLS_NOTE, asked_no_calls, static_system_prompt

from .fakes import FakeSTT, ScriptedLLM
from .test_humanize_live import _session, _wait, fakes  # noqa: F401  (fakes: autouse fixture)


def test_dont_call_me_is_recognised():
    for said in ("अरे मत call करना आप।", "नहीं Madam अब आप call मत करो", "मुझे call मत कीजिए", "मत कॉल करो",
                 "फोन मत करना", "call नहीं करना मुझे", "call ना करो", "call back मत करना", "Please don't call me again",
                 "Do not call this number", "stop calling me", "no more calls please", "dont call"):
        assert asked_no_calls(said), said
    for said in ("मैंने कोई call नहीं किया, wrong number है।", "हाँ call कर लेना कल", "call back कर दीजिए",
                 "मत भेजो WhatsApp पर", "I will call you later", "call करने के लिए thank you", "कल call करो"):
        assert not asked_no_calls(said), said


def test_the_no_calls_note_stays_for_the_rest_of_the_call(monkeypatch):
    async def scenario():
        llm = ScriptedLLM(["जी, हम custom printed pouches बनाते हैं। आपका product क्या है?",
                           "माफ़ कीजिए जी।", "जी, standup pouch में zipper भी होता है।"])
        s, _ = await _session("+919480000071", llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        for i, said in enumerate(("हाँ जी बताइए", "अरे मत call करना आप।", "standup में क्या options हैं?"), 1):
            await stt.say(said)
            await _wait(lambda: len(llm.calls) == i and s.state == "listening")
        run.cancel()
        return s, llm

    s, llm = asyncio.run(scenario())
    notes = [[m["content"] for m in call if m["role"] == "system"][2:] for call in llm.calls]
    assert NO_CALLS_NOTE not in notes[0]                       # before they said it
    assert NO_CALLS_NOTE in notes[1] and NO_CALLS_NOTE in notes[2]  # when they said it, and every reply after
    assert s.no_calls is True


def test_prompt_says_the_team_sends_and_no_more_call_backs():
    for direction, purpose in (("outbound", None), ("inbound", None), ("outbound", "callback")):
        sp = static_system_prompt(dict(DEFAULT_AGENT_SETTINGS), direction, purpose)
        assert "WHO DOES IT" in sp and "हमारी team आपको WhatsApp पर details भेज देगी" in sp
        assert "NO MORE CALLS" in sp
        # the prompt no longer teaches "I'll send" as an example of good Hindi
        assert "मैं भेज दूँगी" not in sp and "भेज देती हूँ" not in sp and "इसी number पर भेज देंगे" not in sp
    kb = DEFAULT_AGENT_SETTINGS["knowledge_base"]
    assert "pays only ₹199 shipping" in kb and 'always say it as "one ninety nine rupees"' in kb
    assert "our team will send them on WhatsApp" in DEFAULT_AGENT_SETTINGS["objection_handling"]


def test_untouched_knowledge_base_upgrades_and_a_customised_one_stays():
    row = {**copy.deepcopy(DEFAULT_AGENT_SETTINGS), "_version": 7, **copy.deepcopy(V6_TEXTS)}
    assert upgrade_saved(row) is True
    assert row["knowledge_base"] == DEFAULT_AGENT_SETTINGS["knowledge_base"]
    assert row["objection_handling"] == DEFAULT_AGENT_SETTINGS["objection_handling"]
    custom = {**copy.deepcopy(DEFAULT_AGENT_SETTINGS), "_version": 7, "knowledge_base": "हमारी अपनी KB।"}
    upgrade_saved(custom)
    assert custom["knowledge_base"] == "हमारी अपनी KB।"
    assert all(DEFAULT_AGENT_SETTINGS[k] not in LEGACY_DEFAULTS[k] for k in V6_TEXTS)
