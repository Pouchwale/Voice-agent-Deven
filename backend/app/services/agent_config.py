"""Load / save the editable agent settings (single row, merged over defaults)."""
from __future__ import annotations

import copy
import re
import threading
from typing import Any

from ..db import SessionLocal
from ..defaults import DEFAULT_AGENT_SETTINGS, DROPPED_FILLERS, LEGACY_DEFAULTS, SETTINGS_VERSION
from ..models import AgentSettings

_lock = threading.Lock()
_cache: dict[str, Any] | None = None
VERSION_KEY = "_version"


def upgrade_saved(data: dict[str, Any]) -> bool:
    """One-time upgrade of a saved settings row to the current defaults. Returns True if it changed.

    Only values still equal to an earlier shipped default are replaced (the team never customised
    them); anything the team typed is kept. Fillers the team asked to drop are removed from any list.
    """
    if int(data.get(VERSION_KEY) or 1) >= SETTINGS_VERSION:
        return False
    for key, old_values in LEGACY_DEFAULTS.items():
        if key in data and data[key] in old_values:
            data[key] = copy.deepcopy(DEFAULT_AGENT_SETTINGS[key])
    words = data.get("filler_words")
    if isinstance(words, list):
        kept = [w for w in words if str(w).strip().lower() not in DROPPED_FILLERS]
        data["filler_words"] = kept if kept else copy.deepcopy(DEFAULT_AGENT_SETTINGS["filler_words"])
    rules = data.get("pronunciations")
    if isinstance(rules, list):
        # new shipped pronunciations are added; the team's own (and their spellings of a word) are kept
        have = {_rule_key(r) for r in rules}
        data["pronunciations"] = rules + [r for r in DEFAULT_AGENT_SETTINGS["pronunciations"]
                                          if _rule_key(r) not in have]
    data[VERSION_KEY] = SETTINGS_VERSION
    return True


def _rule_key(rule: Any) -> tuple[str, bool]:
    """A pronunciation rule's word, and whether it is spelled in Devanagari (Hindi calls) or Latin (English)."""
    parts = re.split(r"\s*(?:=|->|→)\s*", str(rule).strip(), maxsplit=1)  # same separators as sarvam.speakable
    left, right = (parts + [""])[:2]
    return left.strip().lower(), bool(re.search(r"[ऀ-ॿ]", right))


def load_agent_settings() -> dict[str, Any]:
    global _cache
    with _lock:
        if _cache is not None:
            return copy.deepcopy(_cache)
        with SessionLocal() as db:
            row = db.get(AgentSettings, 1)
            data = copy.deepcopy(DEFAULT_AGENT_SETTINGS)
            if row and isinstance(row.data, dict):
                saved = copy.deepcopy(row.data)
                if upgrade_saved(saved):
                    row.data = saved
                    db.commit()
                data.update({k: v for k, v in saved.items() if k in DEFAULT_AGENT_SETTINGS})
        _cache = data
        return copy.deepcopy(data)


def save_agent_settings(patch: dict[str, Any]) -> dict[str, Any]:
    global _cache
    with _lock:
        with SessionLocal() as db:
            row = db.get(AgentSettings, 1)
            current = copy.deepcopy(DEFAULT_AGENT_SETTINGS)
            if row and isinstance(row.data, dict):
                saved = copy.deepcopy(row.data)
                upgrade_saved(saved)
                current.update(saved)
            current[VERSION_KEY] = SETTINGS_VERSION
            for k, v in patch.items():
                if k in DEFAULT_AGENT_SETTINGS and v is not None:
                    current[k] = v
            if row is None:
                row = AgentSettings(id=1, data=current)
                db.add(row)
            else:
                row.data = current
            db.commit()
        _cache = {k: v for k, v in current.items() if k in DEFAULT_AGENT_SETTINGS}
        return copy.deepcopy(_cache)


def reset_agent_settings() -> dict[str, Any]:
    global _cache
    with _lock:
        with SessionLocal() as db:
            row = db.get(AgentSettings, 1)
            if row:
                # facts about the provider's inbound connection are not settings: a reset must keep them, or
                # Disconnect could no longer restore the number's previous routing
                kept = {k: v for k, v in (row.data or {}).items() if str(k).startswith("_inbound_")}
                row.data = {**copy.deepcopy(DEFAULT_AGENT_SETTINGS), VERSION_KEY: SETTINGS_VERSION, **kept}
                db.commit()
        _cache = None
    return load_agent_settings()
