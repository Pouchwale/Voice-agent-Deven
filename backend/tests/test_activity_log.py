"""Activity log: every change made through the dashboard is recorded (who, when, what, old -> new) and is
never deleted. Requests that fail or change nothing leave no entry."""
from __future__ import annotations

import asyncio
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.main import app
from app.models import ActivityLog, ReviewRun, ReviewSuggestion, SettingsChange
from app.services import agent_config, audit, vobiz

LOCAL = ("127.0.0.1", 50000)
RIYA = {"X-Actor": quote("Riya Shah")}


@pytest.fixture()
def client(monkeypatch):
    agent_config.reset_agent_settings()

    async def fake_make_call(call_uuid, to, max_sec, caller_name=""):
        return f"vobiz-{call_uuid[:8]}"

    async def fake_hangup(pid):
        return True

    monkeypatch.setattr(vobiz, "make_call", fake_make_call)
    monkeypatch.setattr(vobiz, "hangup_call", fake_hangup)
    with TestClient(app, client=LOCAL) as c:
        yield c
    agent_config.reset_agent_settings()


def _mark() -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.max(ActivityLog.id))) or 0)


def _new(c, mark: int, **params) -> list[dict]:
    """Entries added after `mark`, oldest first."""
    r = c.get("/api/activity", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return sorted((e for e in r.json()["items"] if e["id"] > mark), key=lambda e: e["id"])


def _lead(c, name: str, phone: str, **more) -> dict:
    r = c.post("/api/leads", json={"name": name, "phone": phone, **more}, headers=RIYA)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- settings
def test_a_settings_save_is_logged_with_the_persons_name(client):
    mark = _mark()
    assert client.put("/api/settings", json={"voice": "ishita", "tts_pace": 1.1}, headers=RIYA).status_code == 200
    history = client.get("/api/settings/history").json()["items"][0]
    assert history["actor"] == "Riya Shah" and history["source"] == "settings"
    (entry,) = _new(client, mark)
    assert entry["actor"] == "Riya Shah" and entry["area"] == "settings" and entry["action"] == "settings.save"
    assert entry["summary"] == history["label"] and entry["target_id"] == str(history["id"])
    by_key = {c["key"]: c for c in entry["details"]["changes"]}
    assert by_key["voice"]["new"] == "ishita" and by_key["voice"]["label"] == "Voice"
    assert by_key["tts_pace"]["new"] == 1.1 and entry["created_at"]
    # saving the same values again changes nothing: no entry
    client.put("/api/settings", json={"voice": "ishita"}, headers=RIYA)
    assert len(_new(client, mark)) == 1


def test_the_name_is_decoded_and_cleaned_and_may_be_missing(client):
    mark = _mark()
    client.put("/api/settings", json={"voice": "ishita"}, headers={"X-Actor": quote("  रिया   शाह\n")})
    client.put("/api/settings", json={"voice": "shreya"})  # an API client that sends no name
    client.put("/api/settings", json={"voice": "kavya"}, headers={"X-Actor": quote("N" * 200)})
    named, unnamed, long = _new(client, mark)
    assert named["actor"] == "रिया शाह" and unnamed["actor"] == "" and long["actor"] == "N" * 60
    items = client.get("/api/settings/history").json()["items"]
    assert [i["actor"] for i in items[:3]] == ["N" * 60, "", "रिया शाह"]


def test_reset_and_undo_are_logged(client):
    client.put("/api/settings", json={"voice": "ishita", "silence_timeout_sec": 15}, headers=RIYA)
    change = client.get("/api/settings/history").json()["items"][0]
    mark = _mark()
    assert client.post(f"/api/settings/history/{change['id']}/undo", headers=RIYA).status_code == 200
    client.put("/api/settings", json={"voice": "shreya"}, headers=RIYA)
    assert client.post("/api/settings/reset", headers={"X-Actor": "Deven"}).status_code == 200
    undo, save, reset = _new(client, mark)
    assert undo["action"] == "settings.undo" and undo["summary"].startswith("Undo: ") and undo["actor"] == "Riya Shah"
    assert {c["key"] for c in undo["details"]["changes"]} == {"voice", "silence_timeout_sec"}
    assert save["action"] == "settings.save"
    assert reset["action"] == "settings.reset" and reset["actor"] == "Deven"
    assert reset["summary"] == "Reset all settings to defaults"
    # an undo that is refused (409: already undone) leaves no entry
    assert client.post(f"/api/settings/history/{change['id']}/undo", headers=RIYA).status_code == 409
    assert len(_new(client, mark)) == 3


def test_an_approved_review_suggestion_is_one_entry_and_a_rejected_one_too(client):
    current = client.get("/api/settings").json()["closing_line"]
    with SessionLocal() as db:
        run = ReviewRun(trigger="manual", status="done", model="gpt-4o-mini", call_count=1)
        db.add(run)
        db.flush()
        yes = ReviewSuggestion(run_id=run.id, field="closing_line", title="Shorter goodbye", base_value=current,
                               proposed_value="धन्यवाद, फिर बात करते हैं।", status="pending")
        no = ReviewSuggestion(run_id=run.id, field="closing_line", title="Longer goodbye", base_value=current,
                              proposed_value="बहुत बहुत धन्यवाद आपका।", status="pending")
        db.add_all([yes, no])
        db.commit()
        yes_id, no_id = yes.id, no.id
    mark = _mark()
    assert client.post(f"/api/review/suggestions/{yes_id}/approve", json={}, headers=RIYA).status_code == 200
    r = client.post(f"/api/review/suggestions/{no_id}/reject", json={"note": "too long"}, headers=RIYA)
    assert r.status_code == 200, r.text
    approved, rejected = _new(client, mark)
    assert approved["area"] == "settings" and approved["action"] == "settings.review"
    assert approved["summary"] == f"Review suggestion #{yes_id}: Shorter goodbye" and approved["actor"] == "Riya Shah"
    assert approved["details"]["changes"][0]["new"] == "धन्यवाद, फिर बात करते हैं।"
    assert rejected["area"] == "review" and rejected["action"] == "suggestion.reject"
    assert rejected["summary"] == f"Rejected review suggestion #{no_id}: Longer goodbye"
    assert rejected["details"]["data"] == {"Note": "too long"}


# ---------------------------------------------------------------- leads
def test_lead_changes_are_logged_with_old_and_new_values(client):
    mark = _mark()
    lead = _lead(client, "Asha Patel", "9600000101", company="Asha Foods")
    lid = lead["id"]
    r = client.patch(f"/api/leads/{lid}", json={"company": "Asha Organics", "notes": "wants 500 pouches"}, headers=RIYA)
    assert r.status_code == 200, r.text
    client.patch(f"/api/leads/{lid}", json={"company": "Asha Organics"}, headers=RIYA)  # nothing changes: no entry
    client.patch(f"/api/leads/{lid}", json={"stage": "hot"}, headers=RIYA)
    assert client.delete(f"/api/leads/{lid}", headers=RIYA).json() == {"ok": True}
    added, edited, moved, deleted = _new(client, mark)

    assert added["action"] == "lead.create" and added["area"] == "leads" and added["actor"] == "Riya Shah"
    assert added["summary"] == "Added lead Asha Patel (+919600000101)" and added["target_id"] == str(lid)
    assert added["details"]["data"] == {"Name": "Asha Patel", "Phone": "+919600000101", "Company": "Asha Foods"}

    assert edited["action"] == "lead.update"
    assert edited["summary"] == "Edited lead Asha Patel (+919600000101): Company, Notes"
    assert edited["details"]["changes"] == [
        {"key": "company", "label": "Company", "old": "Asha Foods", "new": "Asha Organics"},
        {"key": "notes", "label": "Notes", "old": None, "new": "wants 500 pouches"}]

    assert moved["summary"] == "Moved lead Asha Patel (+919600000101) to Hot"
    assert {"key": "interest", "label": "Interest", "old": "unknown", "new": "hot"} in moved["details"]["changes"]

    assert deleted["action"] == "lead.delete" and deleted["summary"] == "Deleted lead Asha Patel (+919600000101)"
    # the deleted record's content survives in the log
    assert deleted["details"]["data"]["Company"] == "Asha Organics"
    assert deleted["details"]["data"]["Notes"] == "wants 500 pouches"


def test_failed_requests_reads_and_previews_are_not_logged(client):
    lead = _lead(client, "Dup Check", "9600000102")
    mark = _mark()
    assert client.post("/api/leads", json={"name": "Dup", "phone": "9600000102"}, headers=RIYA).status_code == 409
    assert client.post("/api/leads", json={"name": "Bad", "phone": "12"}, headers=RIYA).status_code == 400
    assert client.patch(f"/api/leads/{lead['id']}", json={"interest": "boiling"}, headers=RIYA).status_code == 422
    assert client.delete("/api/leads/99999999", headers=RIYA).status_code == 404
    assert client.put("/api/settings", json={"tts_pace": "fast"}, headers=RIYA).status_code == 422
    assert client.get("/api/leads", headers=RIYA).status_code == 200
    assert client.get(f"/api/leads/{lead['id']}", headers=RIYA).status_code == 200
    r = client.post("/api/campaigns/csv/preview", headers=RIYA,
                    files={"file": ("numbers.csv", "name,phone\nA,9600000199\n", "text/csv")})
    assert r.status_code == 200, r.text
    assert client.post("/api/auth/logout", headers=RIYA).status_code == 200
    assert _new(client, mark) == []
    client.delete(f"/api/leads/{lead['id']}", headers=RIYA)


def test_bulk_delete_and_csv_import(client):
    a = _lead(client, "Bulk One", "9600000103")
    b = _lead(client, "", "9600000104")
    mark = _mark()
    r = client.post("/api/leads/bulk-delete", json={"ids": [a["id"], b["id"], 99999998]}, headers=RIYA)
    assert r.json() == {"deleted": 2}
    client.post("/api/leads/bulk-delete", json={"ids": [99999998]}, headers=RIYA)  # nothing deleted: no entry
    csv_text = "name,phone\nImport One,9600000105\nImport Two,9600000106\nBad,12\n"
    r = client.post("/api/leads/import", files={"file": ("leads.csv", csv_text, "text/csv")}, headers=RIYA)
    assert r.status_code == 200 and r.json()["imported"] == 2, r.text
    client.post("/api/leads/import", files={"file": ("leads.csv", csv_text, "text/csv")}, headers=RIYA)  # all known now
    deleted, imported = _new(client, mark)
    assert deleted["action"] == "lead.bulk_delete" and deleted["summary"] == "Deleted 2 leads"
    assert deleted["details"]["items"] == ["Bulk One (+919600000103)", "+919600000104"]
    assert imported["action"] == "lead.import" and imported["summary"] == "Imported 2 leads from a CSV file"
    assert imported["details"]["data"] == {"Added": 2, "Already in the list": 0, "Invalid numbers": 1}
    assert imported["actor"] == "Riya Shah"


def test_a_change_too_large_to_read_is_still_saved_and_logged(client):
    lead = _lead(client, "Big Notes", "9600000107")
    mark = _mark()
    notes = "x" * (audit.BODY_LIMIT + 5000)
    r = client.patch(f"/api/leads/{lead['id']}", json={"notes": notes}, headers=RIYA)
    assert r.status_code == 200 and len(r.json()["notes"]) == len(notes)
    (entry,) = _new(client, mark)
    assert entry["summary"] == "Edited lead Big Notes (+919600000107): Notes"
    client.delete(f"/api/leads/{lead['id']}", headers=RIYA)


# ---------------------------------------------------------------- campaigns and calls
def test_campaign_actions_are_logged(client):
    ids = [_lead(client, f"Camp {i}", f"96000002{i:02d}")["id"] for i in range(2)]
    mark = _mark()
    r = client.post("/api/campaigns", headers=RIYA, json={"name": "Navratri offer", "lead_ids": ids, "concurrency": 1})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert client.post(f"/api/campaigns/{cid}/start", headers=RIYA).json()["status"] == "running"
    assert client.post(f"/api/campaigns/{cid}/pause", headers=RIYA).json()["status"] == "paused"
    client.post(f"/api/campaigns/{cid}/pause", headers=RIYA)  # already paused: no entry
    assert client.delete(f"/api/campaigns/{cid}", headers=RIYA).json() == {"ok": True}
    created, started, paused, deleted = _new(client, mark, area="campaigns")
    assert created["action"] == "campaign.create" and created["summary"] == "Created campaign Navratri offer (2 leads)"
    assert created["details"]["data"]["Leads"] == 2 and created["details"]["data"]["Calls at the same time"] == 1
    assert started["summary"] == "Started campaign Navratri offer"
    assert started["details"]["changes"] == [{"key": "status", "label": "Status", "old": "draft", "new": "running"}]
    assert paused["summary"] == "Paused campaign Navratri offer" and paused["action"] == "campaign.pause"
    assert deleted["summary"] == "Deleted campaign Navratri offer" and deleted["target_id"] == str(cid)
    assert {e["actor"] for e in (created, started, paused, deleted)} == {"Riya Shah"}


def test_call_actions_are_logged(client):
    lead = _lead(client, "Neha Rao", "9600000301")
    mark = _mark()
    r = client.post("/api/calls", json={"lead_id": lead["id"]}, headers=RIYA)
    assert r.status_code == 201, r.text
    uuid = r.json()["uuid"]
    assert client.post(f"/api/calls/{uuid}/hangup", headers=RIYA).status_code == 200
    client.post(f"/api/calls/{uuid}/hangup", headers=RIYA)  # already over: no entry
    assert client.delete(f"/api/calls/{uuid}", headers=RIYA).json() == {"ok": True}
    started, hung_up, deleted = _new(client, mark, area="calls")
    assert started["action"] == "call.start" and started["summary"] == "Started a call to Neha Rao (+919600000301)"
    assert started["target_id"] == uuid and started["actor"] == "Riya Shah"
    assert hung_up["summary"] == "Hung up the call with Neha Rao (+919600000301)"
    assert deleted["summary"] == "Deleted the call with Neha Rao (+919600000301)"
    assert deleted["details"]["data"]["Direction"] == "outbound"


# ---------------------------------------------------------------- reading the log
def test_filter_search_and_paging(client):
    mark = _mark()
    leads = [_lead(client, f"Page {i}", f"96000004{i:02d}") for i in range(5)]
    client.put("/api/settings", json={"voice": "ishita"}, headers={"X-Actor": "Zubin"})
    only_leads = _new(client, mark, area="leads")
    assert len(only_leads) == 5 and {e["area"] for e in only_leads} == {"leads"}
    assert [e["actor"] for e in _new(client, mark, q="zubin")] == ["Zubin"]  # the name, any case
    assert len(_new(client, mark, q="Page 3")) == 1  # the description
    assert _new(client, mark, q="100%_nothing") == []  # % and _ are plain characters
    assert client.get("/api/activity", params={"area": "nonsense"}).status_code == 422

    first = client.get("/api/activity", params={"limit": 2, "area": "leads"}).json()
    assert len(first["items"]) == 2 and first["has_more"] is True and first["total"] >= 5
    second = client.get("/api/activity", params={"limit": 2, "area": "leads",
                                                 "before_id": first["items"][-1]["id"]}).json()
    assert [e["id"] for e in first["items"] + second["items"]] == sorted(
        (e["id"] for e in first["items"] + second["items"]), reverse=True)
    assert first["items"][-1]["id"] > second["items"][0]["id"]
    for lead in leads:
        client.delete(f"/api/leads/{lead['id']}", headers=RIYA)


def test_the_log_downloads_as_a_spreadsheet_file(client):
    lead = _lead(client, "रीना Exports", "9600000501")
    r = client.get("/api/activity/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and "activity_log_" in r.headers["content-disposition"]
    text = r.content.decode("utf-8")
    assert text.startswith(chr(0xFEFF) + "id,time_ist,person,area,action,what,details")
    assert "Riya Shah,leads,lead.create,Added lead रीना Exports (+919600000501)" in text
    client.delete(f"/api/leads/{lead['id']}", headers=RIYA)


def test_entries_are_never_deleted_or_changed(client):
    lead = _lead(client, "Keep Me", "9600000601")
    (entry,) = [e for e in client.get("/api/activity", params={"q": "Keep Me"}).json()["items"]]
    for method in ("delete", "put", "patch", "post"):
        assert getattr(client, method)(f"/api/activity/{entry['id']}").status_code in (404, 405)
    assert client.delete("/api/activity").status_code == 405
    with SessionLocal() as db:
        row = db.get(ActivityLog, entry["id"])
        assert row is not None and row.summary == "Added lead Keep Me (+919600000601)"
    client.delete(f"/api/leads/{lead['id']}", headers=RIYA)


# ---------------------------------------------------------------- the middleware itself
def test_a_route_without_a_description_is_still_logged():
    mark = _mark()
    ctx = {"method": "POST", "path": "/api/something-new", "params": {}, "query": {}, "body": None,
           "response": None, "before": None}
    audit._finish(None, ctx, {"actor": "Riya Shah", "recorded": 0})
    audit._finish(None, ctx, {"actor": "Riya Shah", "recorded": 1})  # an endpoint that recorded it itself
    with SessionLocal() as db:
        rows = db.scalars(select(ActivityLog).where(ActivityLog.id > mark)).all()
        assert [(r.area, r.summary, r.actor) for r in rows] == [("other", "POST /api/something-new", "Riya Shah")]


def test_a_broken_description_or_log_never_fails_the_request(client, monkeypatch):
    spec, _ = audit.match("POST", "/api/leads")

    def boom(ctx):
        raise RuntimeError("description bug")

    monkeypatch.setattr(spec, "describe", boom)
    mark = _mark()
    first = _lead(client, "Still Saved", "9600000701")
    (entry,) = _new(client, mark)
    assert entry["summary"] == "Added a lead" and entry["action"] == "lead.create"  # the plain wording instead

    def no_log(**fields):
        raise RuntimeError("log is unavailable")

    monkeypatch.setattr(audit, "write", no_log)
    second = _lead(client, "Saved Too", "9600000702")  # the change itself still goes through
    monkeypatch.undo()
    for lead in (first, second):
        client.delete(f"/api/leads/{lead['id']}")


def test_the_request_body_is_passed_on_exactly_as_it_arrived():
    async def run(chunks: list[bytes], limit: int) -> tuple[bytes | None, bytes]:
        queue = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1} for i, c in enumerate(chunks)]
        queue.append({"type": "http.disconnect"})

        async def receive():
            return queue.pop(0)

        body, replay = await audit._buffer_body(receive, limit)
        seen = []
        while True:
            message = await replay()
            if message["type"] != "http.request":
                break
            seen.append(message["body"])
            if not message.get("more_body"):
                break
        return body, b"".join(seen)

    assert asyncio.run(run([b'{"a":', b" 1}"], 100)) == (b'{"a": 1}', b'{"a": 1}')
    assert asyncio.run(run([b""], 100)) == (b"", b"")
    big = [b"x" * 60, b"y" * 60, b"z" * 60]
    assert asyncio.run(run(big, 100)) == (None, b"".join(big))  # too large to read: handed on untouched


def test_settings_entries_point_at_the_settings_history(client):
    client.put("/api/settings", json={"voice": "ishita"}, headers=RIYA)
    with SessionLocal() as db:
        entry = db.scalars(select(ActivityLog).order_by(ActivityLog.id.desc())).first()
        change = db.scalars(select(SettingsChange).order_by(SettingsChange.id.desc())).first()
        assert entry.details == {"change_id": change.id} and change.actor == "Riya Shah"


def test_a_body_nested_too_deeply_to_read_is_refused_normally(client):
    """Such a body makes the JSON parser give up with a RecursionError: the log must hand it on untouched, so the
    endpoint answers with its usual 'cannot read this' instead of the request crashing."""
    lead = _lead(client, "Nested", "9600000801")
    mark = _mark()
    deep = b"[" * 100_000
    for method, path in (("patch", f"/api/leads/{lead['id']}"), ("post", "/api/leads/bulk-delete")):
        r = getattr(client, method)(path, content=deep, headers={**RIYA, "Content-Type": "application/json"})
        assert 400 <= r.status_code < 500, (path, r.status_code)
    assert _new(client, mark) == []
    assert audit._parse(deep) is None and audit._parse(b"{broken") is None and audit._parse(b'{"a": 1}') == {"a": 1}
    client.delete(f"/api/leads/{lead['id']}", headers=RIYA)


def test_a_review_entry_names_the_calls_really_reviewed():
    ctx = {"body": {"limit": 10}, "response": {"id": 7, "call_count": 4}}
    entry = audit._review_started(ctx)
    assert entry["summary"] == "Started a call review of 4 calls" and entry["target_id"] == 7
    ctx = {"body": {"limit": 1}, "response": {"id": 8, "call_count": 1}}
    assert audit._review_started(ctx)["summary"] == "Started a call review of 1 call"
    ctx = {"body": {"limit": 5}, "response": None}  # the answer could not be read: say what was asked for
    assert audit._review_started(ctx)["summary"] == "Started a call review of the last 5 calls"


def test_only_requests_that_send_a_name_are_recorded_with_one(client):
    """The dashboard sends the name only with Settings changes (their confirm popup asks for it); every other
    change arrives without one and is logged with an empty name."""
    mark = _mark()
    lead = client.post("/api/leads", json={"name": "No Name", "phone": "9600000802"}).json()
    client.patch(f"/api/leads/{lead['id']}", json={"city": "Surat"})
    client.put("/api/settings", json={"voice": "ishita"}, headers=RIYA)
    client.delete(f"/api/leads/{lead['id']}")
    added, edited, saved, deleted = _new(client, mark)
    assert [e["actor"] for e in (added, edited, saved, deleted)] == ["", "", "Riya Shah", ""]
    assert saved["area"] == "settings" and added["summary"] == "Added lead No Name (+919600000802)"
