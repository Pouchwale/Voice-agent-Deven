"""Load / save the editable agent settings (single row, merged over defaults)."""
from __future__ import annotations

import copy
import re
import threading
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select

from ..db import SessionLocal, utcnow
from ..defaults import DEFAULT_AGENT_SETTINGS, DROPPED_FILLERS, LEGACY_DEFAULTS, SETTING_LABELS, SETTINGS_VERSION
from ..models import AgentSettings, SettingsChange
from ..schemas import iso

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
    """Save a (validated) patch from the Settings page. The change is recorded in the settings history."""
    return apply_settings_change(patch)[0]


def apply_settings_change(patch: dict[str, Any], *, source: str = "settings", label: str = "",
                          ref_id: int | None = None) -> tuple[dict[str, Any], int | None]:
    """Save a patch and record what changed (old -> new per setting) in the settings history, in one
    transaction. Returns (the effective settings, the history entry id or None when nothing changed)."""
    global _cache
    with _lock:
        with SessionLocal() as db:
            row = db.get(AgentSettings, 1)
            current = copy.deepcopy(DEFAULT_AGENT_SETTINGS)
            if row and isinstance(row.data, dict):
                saved = copy.deepcopy(row.data)
                upgrade_saved(saved)
                current.update(saved)
            before = {k: copy.deepcopy(current.get(k)) for k in DEFAULT_AGENT_SETTINGS}
            current[VERSION_KEY] = SETTINGS_VERSION
            for k, v in patch.items():
                if k in DEFAULT_AGENT_SETTINGS and v is not None:
                    current[k] = v
            changes = _diff(before, current)
            change_id = _record(db, changes, source, label, ref_id, current) if changes else None
            if row is None:
                row = AgentSettings(id=1, data=current)
                db.add(row)
            else:
                row.data = current
            db.commit()
        _cache = {k: v for k, v in current.items() if k in DEFAULT_AGENT_SETTINGS}
        if change_id is not None:
            _set_revision(change_id)
        return copy.deepcopy(_cache), change_id


def reset_agent_settings() -> dict[str, Any]:
    global _cache
    with _lock:
        with SessionLocal() as db:
            row = db.get(AgentSettings, 1)
            if row:
                before = copy.deepcopy(DEFAULT_AGENT_SETTINGS)
                saved = copy.deepcopy(row.data or {})
                upgrade_saved(saved)
                before.update({k: v for k, v in saved.items() if k in DEFAULT_AGENT_SETTINGS})
                # internal facts (the inbound connection, the review agent's start date) are not settings: a
                # reset must keep them, or Disconnect could no longer restore the number's previous routing
                kept = {k: v for k, v in (row.data or {}).items() if str(k).startswith("_") and k != VERSION_KEY}
                after = {**copy.deepcopy(DEFAULT_AGENT_SETTINGS), VERSION_KEY: SETTINGS_VERSION, **kept}
                changes = _diff(before, after)
                change_id = (_record(db, changes, "reset", "Reset all settings to defaults", None, after)
                             if changes else None)
                row.data = after
                db.commit()
                if change_id is not None:
                    _set_revision(change_id)
        _cache = None
    return load_agent_settings()


# ---------------------------------------------------------------------- settings history
_revision: int | None = None  # id of the newest history entry (calls record which settings they used)


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {k: {"old": copy.deepcopy(before.get(k)), "new": copy.deepcopy(after.get(k))}
            for k in DEFAULT_AGENT_SETTINGS if before.get(k) != after.get(k)}


def _default_label(source: str, changes: dict[str, Any]) -> str:
    names = [SETTING_LABELS.get(k, k) for k in changes]
    shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    if source == "reset":
        return "Reset all settings to defaults"
    return f"Changed {shown}"


def _record(db, changes: dict[str, Any], source: str, label: str, ref_id: int | None,
            effective: dict[str, Any]) -> int:
    """Insert a history entry (same transaction as the save) and drop entries older than history_days."""
    entry = SettingsChange(source=source, label=(label or _default_label(source, changes))[:300],
                           changes=changes, ref_id=ref_id)
    db.add(entry)
    db.flush()
    days = int(effective.get("history_days") or DEFAULT_AGENT_SETTINGS["history_days"])
    db.execute(delete(SettingsChange).where(SettingsChange.created_at < utcnow() - timedelta(days=days),
                                            SettingsChange.id != entry.id))
    return entry.id


def _set_revision(change_id: int) -> None:
    global _revision
    _revision = max(_revision or 0, change_id)


def current_revision() -> int:
    """The newest settings-history id (0 before any change). Stored on every call, so a review can tell which
    calls ran on which version of the settings (for example before / after an approved suggestion)."""
    global _revision
    if _revision is None:
        with SessionLocal() as db:
            _revision = int(db.scalar(select(func.max(SettingsChange.id))) or 0)
    return _revision


def prune_history() -> int:
    """Remove entries older than history_days. Returns how many were removed."""
    days = int(load_agent_settings().get("history_days") or DEFAULT_AGENT_SETTINGS["history_days"])
    with SessionLocal() as db:
        n = db.execute(delete(SettingsChange).where(SettingsChange.created_at < utcnow() - timedelta(days=days)))
        db.commit()
        return int(n.rowcount or 0)


def list_history(limit: int = 300) -> list[dict[str, Any]]:
    """Newest first, within history_days, each change as {key, label, old, new}."""
    days = int(load_agent_settings().get("history_days") or DEFAULT_AGENT_SETTINGS["history_days"])
    with SessionLocal() as db:
        rows = db.scalars(select(SettingsChange)
                          .where(SettingsChange.created_at >= utcnow() - timedelta(days=days))
                          .order_by(SettingsChange.id.desc()).limit(limit)).all()
        return [_entry_out(r) for r in rows]


def _entry_out(r: SettingsChange) -> dict[str, Any]:
    return {
        "id": r.id, "created_at": iso(r.created_at), "source": r.source, "label": r.label, "ref_id": r.ref_id,
        "undone_by": r.undone_by,
        "changes": [{"key": k, "label": SETTING_LABELS.get(k, k), "old": v.get("old"), "new": v.get("new")}
                    for k, v in (r.changes or {}).items()],
    }


class UndoError(Exception):
    def __init__(self, message: str, status: int = 409, conflicts: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.status = status
        self.conflicts = conflicts or []


def undo_change(change_id: int, *, force: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    """Put back the values a history entry replaced (only the settings that entry changed). If one of them
    was changed again later, nothing happens unless force=True (UndoError lists the conflicts).
    Returns (the effective settings, the new history entry)."""
    with SessionLocal() as db:
        entry = db.get(SettingsChange, change_id)
        if entry is None:
            raise UndoError("This change is no longer in the history.", 404)
        if entry.undone_by:
            raise UndoError("This change was already undone.", 409)
        changes = {k: v for k, v in (entry.changes or {}).items() if k in DEFAULT_AGENT_SETTINGS}
        label = entry.label
    current = load_agent_settings()
    conflicts = [{"key": k, "label": SETTING_LABELS.get(k, k), "now": current.get(k),
                  "after_change": v.get("new"), "restore": v.get("old")}
                 for k, v in changes.items() if current.get(k) != v.get("new")]
    if conflicts and not force:
        names = ", ".join(c["label"] for c in conflicts)
        raise UndoError(f"{names} changed again after this. Undo anyway?", 409, conflicts)
    patch = {k: copy.deepcopy(v.get("old") if v.get("old") is not None else DEFAULT_AGENT_SETTINGS[k])
             for k, v in changes.items()}
    saved, new_id = apply_settings_change(patch, source="undo", label=f"Undo: {label}"[:300], ref_id=change_id)
    with SessionLocal() as db:
        entry = db.get(SettingsChange, change_id)
        if entry is not None:
            entry.undone_by = new_id or -1  # -1: nothing to put back (the old values were already in place)
            db.commit()
        new_entry = db.get(SettingsChange, new_id) if new_id else None
        return saved, (_entry_out(new_entry) if new_entry is not None else {})
