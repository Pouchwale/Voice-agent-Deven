"""The PouchWale team's facts reach the agent, and saved settings that were never edited pick them up."""
from __future__ import annotations

from app.defaults import DEFAULT_AGENT_SETTINGS
from app.defaults_legacy import V4_TEXTS
from app.services.agent_config import upgrade_saved
from app.services.prompts import static_system_prompt


def test_knowledge_has_the_teams_facts():
    kb = DEFAULT_AGENT_SETTINGS["knowledge_base"]
    assert "1,000 pieces" in kb and "PER PIECE" in kb and "NEVER per kg" in kb
    assert "₹199" in kb and "pouchwale.com" in kb and "[FILL IN]" not in kb
    assert "- Coffee, tea: quad seal bag" in kb and "- Beverages, juices: spout pouch" in kb
    assert "- Oats, granola, breakfast cereals: standup pouch" in kb


def test_prompt_has_facts_and_deflection_in_both_directions():
    for direction in ("outbound", "inbound"):
        p = static_system_prompt(dict(DEFAULT_AGENT_SETTINGS), direction, direction)
        assert "DEFLECTION" in p and "never per kg" in p and "Vary the wording" in p
        assert "1,000 pieces" in p and "one ninety nine" in p


def test_untouched_saved_texts_get_the_new_facts_custom_ones_stay():
    saved = {"_version": 4, **V4_TEXTS}
    assert upgrade_saved(saved)
    assert all(saved[k] == DEFAULT_AGENT_SETTINGS[k] for k in V4_TEXTS)
    custom = {"_version": 4, "knowledge_base": "Our own knowledge base text"}
    upgrade_saved(custom)
    assert custom["knowledge_base"] == "Our own knowledge base text"
