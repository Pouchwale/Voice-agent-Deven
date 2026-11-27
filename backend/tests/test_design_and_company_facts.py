"""The team's answers of 2026-11-25: printing technology (Hybrid LED, no development cost), the company intro (near
Ahmedabad), the name "Pouch Wale", and design: "do you make the design?" -> no, but after order confirmation they get
access to the Pouch Wale design library. Measured on the real model before saving: 52 of 52 replies right (13 cases x 4),
after "do you also make designs?" in mid-call had been answered with the what-we-make line 0 of 4 times."""
from __future__ import annotations

import pytest

from app.defaults import DEFAULT_AGENT_SETTINGS
from app.services.prompts import COMPANY_INTRO_NOTE, DESIGN_NOTE, WHAT_WE_MAKE_NOTE
from app.services import analysis as analysis_mod
from app.services.call_manager import caller_id_name
from app.services.session import CallSession, asks_about_our_company, asks_if_we_design


@pytest.mark.parametrize("text", [
    "आप लोग design भी बनाते हो क्या?", "क्या आप design बना देंगे?", "क्या आप हमारे लिए design बना सकते हो?",
    "design भी आप ही बनाओगे?", "design कौन बनाता है?", "डिज़ाइन आप करते हो?", "Do you make the design too?",
])
def test_asks_if_we_design(text):
    assert asks_if_we_design(text)


@pytest.mark.parametrize("text", ["design ready है", "design कब तक ready होगा?", "मेरे पास design है",
                                  "आप क्या क्या बनाते हो?", "आप कौन से pouch बनाते हो?"])
def test_not_a_design_question(text):
    assert not asks_if_we_design(text)


class _Said:
    _last_agent_text = CallSession._last_agent_text

    def __init__(self):
        self.history = [{"role": "assistant", "content": "जी, ठीक है। लगभग कितनी quantity चाहिए?"}]
        self._cut_answer = None


def test_a_design_question_gets_the_design_note_not_the_what_we_make_one():
    notes = CallSession._turn_notes(_Said(), "आप लोग design भी बनाते हो क्या?", None, None, None)
    assert notes[-1] == DESIGN_NOTE and WHAT_WE_MAKE_NOTE not in notes
    notes = CallSession._turn_notes(_Said(), "आप क्या क्या बनाते हो?", None, None, None)
    assert notes[-1] == WHAT_WE_MAKE_NOTE


@pytest.mark.parametrize("text, ours", [
    ("अपनी company के बारे में थोड़ा बताइए।", True), ("आपकी company के बारे में बताइए", True),
    ("company के बारे में बताइए", True), ("Tell me about your company", True), ("आपकी कंपनी की details दीजिए", True),
    ("मेरी company के बारे में आपको पता है?", False), ("आपकी company का नाम क्या है?", False),
    ("मेरी company का नाम Crunchy Bites है", False),
])
def test_asks_about_our_company(text, ours):
    assert asks_about_our_company(text) is ours


def test_a_question_about_our_company_gets_the_intro_note():
    notes = CallSession._turn_notes(_Said(), "अपनी company के बारे में थोड़ा बताइए।", None, None, None)
    assert notes[-1] == COMPANY_INTRO_NOTE


def test_the_shipped_facts():
    kb = DEFAULT_AGENT_SETTINGS["knowledge_base"]
    assert DEFAULT_AGENT_SETTINGS["company_name"] == "Pouch Wale"
    assert "near Ahmedabad, Gujarat" in kb and "गुजरात में अहमदाबाद के पास" in kb
    assert "Hybrid LED technology" in kb and "don't name any other printing method" in kb
    assert "no development cost" in kb
    assert "design library" in kb and "we don't make designs" in kb
    assert "PouchWale (" not in kb
    rules = DEFAULT_AGENT_SETTINGS["pronunciations"]
    assert "PouchWale = पाउच वाले" in rules and "PouchWale = पाउचवाले" not in rules


@pytest.mark.parametrize("written", ["PouchWale", "Pouch Wale", "pouchwale packaging", "POUCH-WALE"])
def test_our_own_name_is_never_saved_as_the_customers_company(written):
    """Found by the full suite after the rename: the own-name filter compared exact strings, so "PouchWale" written by
    the AI no longer matched the setting "Pouch Wale" and our brand would have been saved as the customer's company."""
    cfg = {**DEFAULT_AGENT_SETTINGS, "company_name": "Pouch Wale"}
    assert analysis_mod.customer_field(written, "company", cfg) is None
    if " packaging" not in written:  # caller ID is compared with our name as a whole (it always was)
        assert caller_id_name(written, cfg) is None
    assert analysis_mod.customer_field("Crunchy Bites", "company", cfg) == "Crunchy Bites"
