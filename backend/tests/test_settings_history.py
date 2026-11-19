"""Settings history: every saved change is listed (old -> new), kept forever, and can be undone."""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.db import SessionLocal, utcnow
from app.defaults import DEFAULT_AGENT_SETTINGS
from app.main import app
from app.models import AgentSettings, SettingsChange
from app.services import agent_config

LOCAL = ("127.0.0.1", 50000)


@pytest.fixture()
def client():
    agent_config.reset_agent_settings()
    with TestClient(app, client=LOCAL) as c:
        yield c
    agent_config.reset_agent_settings()


def _history(c) -> list[dict]:
    r = c.get("/api/settings/history")
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_a_save_is_listed_with_old_and_new_values(client):
    before = len(_history(client))
    assert client.put("/api/settings", json={"voice": "ishita", "tts_pace": 1.1}).status_code == 200
    items = _history(client)
    assert len(items) == before + 1
    entry = items[0]
    assert entry["source"] == "settings" and entry["label"].startswith("Changed ")
    by_key = {c["key"]: c for c in entry["changes"]}
    assert by_key["voice"]["old"] == DEFAULT_AGENT_SETTINGS["voice"] and by_key["voice"]["new"] == "ishita"
    assert by_key["voice"]["label"] == "Voice" and by_key["tts_pace"]["new"] == 1.1
    # saving the same values again is not a change
    client.put("/api/settings", json={"voice": "ishita"})
    assert len(_history(client)) == before + 1


def test_undo_puts_the_old_value_back(client):
    """The user's example: voice changed from kavya to ishita, Undo -> kavya again."""
    client.put("/api/settings", json={"voice": "kavya"})
    client.put("/api/settings", json={"voice": "ishita"})
    entry = _history(client)[0]
    r = client.post(f"/api/settings/history/{entry['id']}/undo")
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["voice"] == "kavya" and client.get("/api/settings").json()["voice"] == "kavya"
    items = _history(client)
    assert items[0]["source"] == "undo" and items[0]["label"].startswith("Undo: ") and items[0]["ref_id"] == entry["id"]
    assert next(i for i in items if i["id"] == entry["id"])["undone_by"] == items[0]["id"]
    # undoing the same change twice is refused
    assert client.post(f"/api/settings/history/{entry['id']}/undo").status_code == 409
    # the undo itself can be undone (redo)
    assert client.post(f"/api/settings/history/{items[0]['id']}/undo").status_code == 200
    assert client.get("/api/settings").json()["voice"] == "ishita"


def test_undo_warns_when_the_setting_changed_again(client):
    client.put("/api/settings", json={"voice": "ishita"})
    first = _history(client)[0]
    client.put("/api/settings", json={"voice": "shreya"})
    r = client.post(f"/api/settings/history/{first['id']}/undo")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "changed again" in detail["message"]
    assert detail["conflicts"][0]["key"] == "voice" and detail["conflicts"][0]["now"] == "shreya"
    assert client.get("/api/settings").json()["voice"] == "shreya"  # nothing changed
    assert client.post(f"/api/settings/history/{first['id']}/undo?force=true").status_code == 200
    assert client.get("/api/settings").json()["voice"] == DEFAULT_AGENT_SETTINGS["voice"]


def test_undo_only_touches_the_settings_of_that_change(client):
    client.put("/api/settings", json={"voice": "ishita"})
    voice_change = _history(client)[0]
    client.put("/api/settings", json={"opening_line": "नमस्ते {lead_name_ji}! दो minute बात हो सकती है?"})
    client.post(f"/api/settings/history/{voice_change['id']}/undo")
    s = client.get("/api/settings").json()
    assert s["voice"] == DEFAULT_AGENT_SETTINGS["voice"]
    assert s["opening_line"] == "नमस्ते {lead_name_ji}! दो minute बात हो सकती है?"


def test_reset_is_recorded_and_can_be_undone(client):
    client.put("/api/settings", json={"voice": "ishita", "silence_timeout_sec": 15})
    assert client.post("/api/settings/reset").status_code == 200
    entry = _history(client)[0]
    assert entry["source"] == "reset" and {c["key"] for c in entry["changes"]} >= {"voice", "silence_timeout_sec"}
    client.post(f"/api/settings/history/{entry['id']}/undo")
    s = client.get("/api/settings").json()
    assert s["voice"] == "ishita" and s["silence_timeout_sec"] == 15


def test_internal_facts_are_not_history_and_survive_a_reset(client):
    with SessionLocal() as db:
        row = db.get(AgentSettings, 1)
        row.data = {**(row.data or {}), "_inbound_app_id": "app-1", "_review_since": "2026-11-01T00:00:00+00:00"}
        db.commit()
    agent_config._cache = None
    client.post("/api/settings/reset")
    with SessionLocal() as db:
        data = db.get(AgentSettings, 1).data
    assert data["_inbound_app_id"] == "app-1" and data["_review_since"].startswith("2026-11-01")
    assert all(not c["key"].startswith("_") for e in _history(client) for c in e["changes"])


def test_history_is_kept_forever(client):
    client.put("/api/settings", json={"voice": "ishita"})
    old_id = _history(client)[0]["id"]
    with SessionLocal() as db:
        db.execute(update(SettingsChange).where(SettingsChange.id == old_id)
                   .values(created_at=utcnow() - timedelta(days=400)))
        db.commit()
    client.put("/api/settings", json={"tts_pace": 1.1})  # a later save must not remove anything
    assert any(e["id"] == old_id for e in _history(client))
    with SessionLocal() as db:
        assert db.get(SettingsChange, old_id) is not None
    # an entry that old can still be undone
    assert client.post(f"/api/settings/history/{old_id}/undo").status_code == 200
    # the "keep history for N days" setting is gone: a browser tab that still sends it is ignored
    r = client.put("/api/settings", json={"history_days": 1})
    assert r.status_code == 200 and "history_days" not in r.json()
    assert not hasattr(agent_config, "prune_history")


def test_history_loads_older_entries_page_by_page(client):
    for voice in ("ishita", "shreya", "kavya", "ishita", "shreya"):
        client.put("/api/settings", json={"voice": voice})
    first = client.get("/api/settings/history", params={"limit": 2}).json()
    assert len(first["items"]) == 2 and first["has_more"] is True and first["total"] >= 5
    older = client.get("/api/settings/history", params={"limit": 2, "before_id": first["items"][-1]["id"]}).json()
    assert len(older["items"]) == 2 and older["items"][0]["id"] < first["items"][-1]["id"]
    assert {"actor", "changes", "undone_by"} <= set(first["items"][0])


def test_setting_names_for_the_confirm_popup(client):
    labels = client.get("/api/settings/labels").json()
    assert labels["voice"] == "Voice" and labels["opening_line"] == "Opening line"
    assert set(DEFAULT_AGENT_SETTINGS) <= set(labels)  # every setting has a name


def test_every_change_bumps_the_revision_calls_record(client):
    rev = agent_config.current_revision()
    client.put("/api/settings", json={"voice": "ishita"})
    assert agent_config.current_revision() > rev
    with SessionLocal() as db:
        assert agent_config.current_revision() == db.scalar(select(SettingsChange.id).order_by(SettingsChange.id.desc()))
