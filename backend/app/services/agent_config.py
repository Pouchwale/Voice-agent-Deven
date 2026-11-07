"""Load / save the editable agent settings (single row, merged over defaults)."""
from __future__ import annotations

import copy
import threading
from typing import Any

from ..db import SessionLocal
from ..defaults import DEFAULT_AGENT_SETTINGS
from ..models import AgentSettings

_lock = threading.Lock()
_cache: dict[str, Any] | None = None


def load_agent_settings() -> dict[str, Any]:
    global _cache
    with _lock:
        if _cache is not None:
            return copy.deepcopy(_cache)
        with SessionLocal() as db:
            row = db.get(AgentSettings, 1)
            data = copy.deepcopy(DEFAULT_AGENT_SETTINGS)
            if row and isinstance(row.data, dict):
                data.update({k: v for k, v in row.data.items() if k in DEFAULT_AGENT_SETTINGS})
        _cache = data
        return copy.deepcopy(data)


def save_agent_settings(patch: dict[str, Any]) -> dict[str, Any]:
    global _cache
    with _lock:
        with SessionLocal() as db:
            row = db.get(AgentSettings, 1)
            current = copy.deepcopy(DEFAULT_AGENT_SETTINGS)
            if row and isinstance(row.data, dict):
                current.update(row.data)
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
                row.data = copy.deepcopy(DEFAULT_AGENT_SETTINGS)
                db.commit()
        _cache = None
    return load_agent_settings()
