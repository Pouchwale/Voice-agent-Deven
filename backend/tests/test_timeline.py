"""Customer timeline, calls-list filters, inbound KPIs and inbound-vs-outbound metric scopes.

Rows go straight into the database with fixed PAST timestamps (2021), so every date-range assertion sees
only this file's rows (one database is shared by all test files); dashboard checks use before/after deltas.
Phone numbers are in this file's own range (+9195...). The app runs WITHOUT its lifespan here: no dialer,
watchdog or startup recovery touches these rows (a "live" row would otherwise be swept or recovered).
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from app.db import SessionLocal, utcnow
from app.main import app
from app.models import ACTIVE_CALL_STATUSES, Call, Campaign, CampaignLead, Lead, TranscriptTurn
from app.services import pdf_report, vobiz

LOCAL = ("127.0.0.1", 50000)
OUR_DID = "+918000000000"  # VOBIZ_FROM_NUMBER (tests/conftest.py)
LIST_KEYS = {"uuid", "lead_id", "lead_name", "lead_phone", "lead_company", "campaign_id", "direction", "caller_type",
             "from_number", "to_number", "channel", "status", "end_reason", "started_at", "answered_at", "ended_at",
             "duration_sec", "rating", "interest", "qualified", "summary", "next_action", "callback_time",
             "call_reason", "follow_up", "analysis_status", "has_recording", "created_at"}


@pytest.fixture()
def client(monkeypatch):
    dialed: list = []

    async def fake_make_call(*args, **kwargs):
        dialed.append(args)
        return "vobiz-test"

    async def fake_hangup(*args, **kwargs):
        return True

    monkeypatch.setattr(vobiz, "make_call", fake_make_call)
    monkeypatch.setattr(vobiz, "hangup_call", fake_hangup)
    yield TestClient(app, client=LOCAL)  # no `with`: no lifespan (see the module docstring)
    assert not dialed  # reading data must never place a call


def at(text: str) -> datetime:
    """'2021-02-01 18:29:59.999' as a UTC instant."""
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def iso(text: str) -> str:
    return at(text).strftime("%Y-%m-%dT%H:%M:%S.") + f"{at(text).microsecond // 1000:03d}Z"


def add_lead(db, phone: str, name: str = "", *, created: datetime | None = None, source: str | None = None) -> Lead:
    when = created or utcnow()
    lead = Lead(name=name, phone=phone, source=source, created_at=when, updated_at=when)
    db.add(lead)
    db.flush()
    return lead


def add_call(db, uuid: str, when: datetime, *, lead: Lead | None = None, direction: str = "outbound",
             status: str = "completed", channel: str = "phone", caller: str | None = None,
             caller_type: str | None = None, words: int = 0, duration: float | None = None,
             end_reason: str | None = None, analysis: str = "skipped", interest: str = "unknown",
             rating: int | None = None, **extra) -> Call:
    """A call row as the engine leaves it. `caller` = the customer's number when it differs from the lead's."""
    number = caller if caller is not None else (lead.phone if lead else "")
    inbound = direction == "inbound"
    live = status in ACTIVE_CALL_STATUSES
    answered = status == "completed" or status == "in_progress"
    c = Call(uuid=uuid, lead_id=lead.id if lead else None, channel=channel, direction=direction,
             caller_type=caller_type, status=status,
             end_reason=end_reason if end_reason or live else ("agent_ended" if status == "completed" else "no_answer"),
             from_number=number if inbound else OUR_DID, to_number=OUR_DID if inbound else number,
             started_at=when, answered_at=when if answered else None,
             ended_at=None if live else when + timedelta(seconds=duration or 20), duration_sec=duration,
             created_at=when, analysis_status=analysis, interest=interest, rating=rating,
             metrics={"user_words": words, "turns": 2 if words else 0}, **extra)
    db.add(c)
    db.flush()
    return c


def uuids(page: dict) -> set[str]:
    return {c["uuid"] for c in page["items"]}


# ---------------------------------------------------------------- timeline
def test_timeline_orders_calls_campaigns_and_creation(client):
    with SessionLocal() as db:
        lead = add_lead(db, "+919500000001", "Sunita Foods", created=at("2021-03-01 09:00:00"), source="IndiaMART")
        camp = Campaign(name="Holi push", status="completed", created_at=at("2021-03-02 09:00:00"))
        db.add(camp)
        db.flush()
        db.add(CampaignLead(campaign_id=camp.id, lead_id=lead.id, status="done", attempts=1))
        add_call(db, "tl1-out", at("2021-03-03 10:00:00"), lead=lead, status="no_answer")
        add_call(db, "tl1-browser", at("2021-03-04 12:00:00"), lead=lead, channel="browser", words=5)
        add_call(db, "tl1-in", at("2021-03-05 11:00:00"), lead=lead, direction="inbound", caller_type="known",
                 words=40, duration=151, analysis="done", interest="hot", rating=8, next_action="Send the quote",
                 callback_time="Monday 11 am", key_points=[f"point {i}" for i in range(7)],
                 summary="Wants 5000 zipper pouches")
        db.commit()
        lead_id, camp_id = lead.id, camp.id

    r = client.get(f"/api/leads/{lead_id}/timeline")
    assert r.status_code == 200, r.text
    t = r.json()
    assert set(t) == {"lead", "events", "stats", "next_step"} and t["lead"]["id"] == lead_id
    assert [e["id"] for e in t["events"]] == ["call-tl1-in", "call-tl1-browser", "call-tl1-out",
                                             f"campaign-{camp_id}", f"lead-{lead_id}"]
    first = t["events"][0]
    assert first["kind"] == "call" and first["at"] == iso("2021-03-05 11:00:00")
    assert set(first["call"]) == LIST_KEYS
    assert first["call"]["direction"] == "inbound" and first["call"]["caller_type"] == "known"
    assert first["call"]["from_number"] == "+919500000001" and first["call"]["follow_up"] is None
    assert first["next_action"] == "Send the quote" and first["callback_time"] == "Monday 11 am"
    assert first["key_points"] == [f"point {i}" for i in range(5)]  # at most 5
    assert t["events"][1]["call"]["channel"] == "browser"  # browser tests are listed (but never counted)
    assert t["events"][3] == {"id": f"campaign-{camp_id}", "kind": "campaign_added", "at": iso("2021-03-02 09:00:00"),
                              "campaign_id": camp_id, "campaign_name": "Holi push", "campaign_status": "completed"}
    assert t["events"][4] == {"id": f"lead-{lead_id}", "kind": "lead_created", "at": iso("2021-03-01 09:00:00"),
                              "source": "IndiaMART"}
    assert t["stats"] == {"calls": 2, "inbound": 1, "outbound": 1, "answered": 1, "talk_sec": 151,
                          "first_contact_at": iso("2021-03-03 10:00:00"), "last_contact_at": iso("2021-03-05 11:00:00")}
    assert t["next_step"] == {"next_action": "Send the quote", "callback_time": "Monday 11 am", "call_uuid": "tl1-in",
                              "at": iso("2021-03-05 11:00:00")}


def test_timeline_ties_and_csv_campaign_order(client):
    # A CSV campaign is saved a moment BEFORE the leads it creates: "Added to campaign" must still read
    # as happening after "Lead created" (newest first: added, then created).
    r = client.post("/api/campaigns/csv", files={"file": ("list.csv", b"name,phone,company\nRavi,9500000011,Ravi Snacks\n",
                                                          "text/csv")}, data={"name": "Tie check"})
    assert r.status_code == 201, r.text
    camp_id = r.json()["id"]
    with SessionLocal() as db:
        lead = db.scalar(select(Lead).where(Lead.phone == "+919500000011"))
        assert db.get(Campaign, camp_id).created_at <= lead.created_at
        lead_id = lead.id
    t = client.get(f"/api/leads/{lead_id}/timeline").json()
    assert [e["kind"] for e in t["events"]] == ["campaign_added", "lead_created"]
    assert t["events"][0]["at"] == t["events"][1]["at"] == t["lead"]["created_at"]  # clamped to the lead's creation
    assert t["events"][0]["campaign_name"] == "Tie check" and t["events"][0]["campaign_status"] == "draft"
    assert client.delete(f"/api/campaigns/{camp_id}").status_code == 200

    # New caller: the lead and its first call share one instant -> the call is listed above "Lead created";
    # two calls at the same instant -> the later row first.
    same = at("2021-03-10 10:00:00")
    with SessionLocal() as db:
        lead2 = add_lead(db, "+919500000012", created=same, source="Inbound call")
        add_call(db, "tl2-a", same, lead=lead2, direction="inbound", caller_type="new", status="no_answer",
                 end_reason="user_hangup")
        add_call(db, "tl2-b", same, lead=lead2, direction="inbound", caller_type="known", status="no_answer",
                 end_reason="lines_busy")
        db.commit()
        lead2_id = lead2.id
    t2 = client.get(f"/api/leads/{lead2_id}/timeline").json()
    assert [e["id"] for e in t2["events"]] == ["call-tl2-b", "call-tl2-a", f"lead-{lead2_id}"]
    assert {e["at"] for e in t2["events"]} == {iso("2021-03-10 10:00:00")}


def test_timeline_links_inbound_orphans_only(client):
    with SessionLocal() as db:
        lead = add_lead(db, "+919500000021", "Meena", created=at("2021-03-11 08:00:00"))
        other = add_lead(db, "+919500000022", "Other Lead", created=at("2021-03-11 08:00:00"))
        # inbound call from this number saved without a lead (it rang before the lead existed): it is theirs
        add_call(db, "tl3-orphan-in", at("2021-03-12 10:00:00"), direction="inbound", caller="+919500000021",
                 caller_type="known", status="no_answer", end_reason="lines_busy")
        # an OUTBOUND row that lost its lead (deleted lead / recycled number): never linked by number
        add_call(db, "tl3-orphan-out", at("2021-03-12 11:00:00"), caller="+919500000021", words=3, duration=15)
        add_call(db, "tl3-stranger", at("2021-03-12 12:00:00"), direction="inbound", caller="+919500000029",
                 caller_type="new", status="no_answer", end_reason="user_hangup")
        # another lead's inbound call from the same number belongs to that lead
        add_call(db, "tl3-other", at("2021-03-12 13:00:00"), lead=other, direction="inbound", caller="+919500000021",
                 caller_type="known", status="no_answer", end_reason="user_hangup")
        # our call back, no conversation yet: the missed call is "tried"
        add_call(db, "tl3-callback", at("2021-03-12 14:00:00"), lead=lead, status="no_answer")
        db.commit()
        lead_id, other_id = lead.id, other.id

    t = client.get(f"/api/leads/{lead_id}/timeline").json()
    assert [e["id"] for e in t["events"]] == ["call-tl3-callback", "call-tl3-orphan-in", f"lead-{lead_id}"]
    orphan = t["events"][1]["call"]
    assert orphan["lead_id"] is None and orphan["lead_phone"] == "+919500000021" and orphan["follow_up"] == "tried"
    assert t["stats"]["calls"] == 2 and t["stats"]["inbound"] == 1 and t["stats"]["answered"] == 0
    detail = client.get(f"/api/leads/{lead_id}").json()  # the lead page's call table uses the same rule
    assert {c["uuid"] for c in detail["calls"]} == {"tl3-callback", "tl3-orphan-in"}
    assert next(c for c in detail["calls"] if c["uuid"] == "tl3-orphan-in")["follow_up"] == "tried"
    assert [e["id"] for e in client.get(f"/api/leads/{other_id}/timeline").json()["events"]] == \
        ["call-tl3-other", f"lead-{other_id}"]


def test_timeline_stats_cover_all_calls(client):
    base = at("2021-03-20 00:00:00")
    with SessionLocal() as db:
        lead = add_lead(db, "+919500000031", "Bulk Caller", created=base)
        db.execute(insert(Call), [
            {"uuid": f"tl4-{i:03d}", "lead_id": lead.id, "channel": "phone", "direction": "outbound",
             "status": "no_answer", "end_reason": "no_answer", "from_number": OUR_DID, "to_number": lead.phone,
             "started_at": base + timedelta(minutes=i + 1), "created_at": base + timedelta(minutes=i + 1),
             "analysis_status": "skipped", "interest": "unknown"} for i in range(203)])
        add_call(db, "tl4-in-talk", base + timedelta(days=1), lead=lead, direction="inbound", caller_type="known",
                 words=20, duration=90.4)
        add_call(db, "tl4-in-missed", base + timedelta(days=1, minutes=5), lead=lead, direction="inbound",
                 caller_type="known", status="no_answer", end_reason="inbound_off")
        add_call(db, "tl4-browser", base + timedelta(days=2), lead=lead, channel="browser", words=3, duration=30)
        db.commit()
        lead_id = lead.id

    t = client.get(f"/api/leads/{lead_id}/timeline").json()
    events = t["events"]
    assert len(events) == 200  # 207 events: capped at 200 ...
    assert events[-1] == {"id": f"lead-{lead_id}", "kind": "lead_created", "at": iso("2021-03-20 00:00:00"),
                          "source": None}  # ... but "Lead created" is always kept
    assert [e["id"] for e in events[:3]] == ["call-tl4-browser", "call-tl4-in-missed", "call-tl4-in-talk"]
    assert events[1]["call"]["follow_up"] == "pending"  # nobody spoke with them after the missed call
    ats = [e["at"] for e in events]
    assert ats == sorted(ats, reverse=True)
    # stats cover every linked PHONE call, not just the 200 listed (the browser test is left out)
    assert t["stats"] == {"calls": 205, "inbound": 2, "outbound": 203, "answered": 1, "talk_sec": 90,
                          "first_contact_at": iso("2021-03-20 00:01:00"),
                          "last_contact_at": iso("2021-03-21 00:05:00")}


def test_timeline_next_step_from_conversation(client):
    with SessionLocal() as db:
        lead = add_lead(db, "+919500000041", "Next Step", created=at("2021-03-25 08:00:00"))
        empty = add_lead(db, "+919500000042", "No Calls Yet", created=at("2021-03-25 08:00:00"), source="CSV import")
        add_call(db, "tl5-old", at("2021-03-26 10:00:00"), lead=lead, words=25, analysis="done", next_action="Old plan")
        add_call(db, "tl5-agreed", at("2021-03-27 10:00:00"), lead=lead, direction="inbound", caller_type="known",
                 words=30, analysis="done", next_action="  ", callback_time="Monday 11 am")
        # newer, but not usable: analysis not done / picked up and cut (no conversation) / a browser role-play
        add_call(db, "tl5-pending", at("2021-03-28 10:00:00"), lead=lead, words=12, analysis="pending",
                 next_action="Not analysed yet")
        add_call(db, "tl5-cut", at("2021-03-29 10:00:00"), lead=lead, words=0, analysis="done", interest="cold",
                 rating=1, end_reason="user_hangup", next_action="Try again at a different time of day, or move on.")
        add_call(db, "tl5-test", at("2021-03-30 10:00:00"), lead=lead, channel="browser", words=9, analysis="done",
                 next_action="Browser role-play")
        db.commit()
        lead_id, empty_id = lead.id, empty.id

    t = client.get(f"/api/leads/{lead_id}/timeline").json()
    assert t["next_step"] == {"next_action": None, "callback_time": "Monday 11 am", "call_uuid": "tl5-agreed",
                              "at": iso("2021-03-27 10:00:00")}
    e = client.get(f"/api/leads/{empty_id}/timeline").json()
    assert e["next_step"] is None
    assert e["events"] == [{"id": f"lead-{empty_id}", "kind": "lead_created", "at": iso("2021-03-25 08:00:00"),
                            "source": "CSV import"}]
    assert e["stats"] == {"calls": 0, "inbound": 0, "outbound": 0, "answered": 0, "talk_sec": 0,
                          "first_contact_at": None, "last_contact_at": None}


def test_timeline_404(client):
    r = client.get("/api/leads/987654321/timeline")
    assert r.status_code == 404 and r.json()["detail"] == "Lead not found"


# ---------------------------------------------------------------- calls list
def test_calls_direction_outcome_and_followup_filters(client):
    with SessionLocal() as db:
        day0 = at("2021-03-31 08:00:00")
        a = add_lead(db, "+919500000201", "A", created=day0)
        b = add_lead(db, "+919500000202", "B", created=day0)
        c = add_lead(db, "+919500000203", "C", created=day0)
        m = add_lead(db, "+919500000204", "M", created=day0)
        on_line = add_lead(db, "+919500000205", "On The Line", created=day0)
        g = add_lead(db, "+919500000206", "G", created=day0)
        # A: missed, then we called back (no answer yet) -> tried
        add_call(db, "f-a1", at("2021-04-01 04:30:00"), lead=a, direction="inbound", caller_type="known",
                 status="no_answer", end_reason="inbound_off")
        add_call(db, "f-a2", at("2021-04-01 05:30:00"), lead=a, status="no_answer")
        # B: missed, then they called again and talked -> done
        add_call(db, "f-b1", at("2021-04-01 06:30:00"), lead=b, direction="inbound", caller_type="known",
                 status="no_answer", end_reason="lines_busy")
        add_call(db, "f-b2", at("2021-04-02 03:30:00"), lead=b, direction="inbound", caller_type="known", words=15,
                 duration=80)
        # C: missed twice; our dial was cancelled because they were calling in (superseded): no attempt -> pending
        add_call(db, "f-c1", at("2021-04-02 04:30:00"), lead=c, direction="inbound", caller_type="known",
                 status="no_answer", end_reason="user_hangup")
        add_call(db, "f-c2", at("2021-04-02 05:00:00"), lead=c, status="canceled", end_reason="superseded")
        add_call(db, "f-c3", at("2021-04-02 05:00:01"), lead=c, direction="inbound", caller_type="known",
                 status="no_answer", end_reason="lines_busy")
        # international callers (no lead while ALLOW_INTERNATIONAL is off)
        add_call(db, "f-d1", at("2021-04-02 06:00:00"), direction="inbound", caller="+12025550101",
                 caller_type="international", status="no_answer", end_reason="user_hangup")
        add_call(db, "f-d2", at("2021-04-02 06:30:00"), direction="inbound", caller="+12025550102",
                 caller_type="international", status="no_answer", end_reason="lines_busy")
        k = add_lead(db, "+12025550102", "Kim", created=at("2021-04-02 07:00:00"))  # ...who later became a lead
        add_call(db, "f-k1", at("2021-04-02 07:30:00"), lead=k, words=22, duration=60)  # and talked with us: done
        # M: missed, then called again while the lead was busy (saved without a lead) and talked -> done
        add_call(db, "f-m1", at("2021-04-02 08:00:00"), lead=m, direction="inbound", caller_type="known",
                 status="no_answer", end_reason="user_hangup")
        add_call(db, "f-m2", at("2021-04-02 08:30:00"), direction="inbound", caller="+919500000204",
                 caller_type="known", words=10, duration=40)
        # hidden number: nobody to call back -> no follow-up at all
        add_call(db, "f-e1", at("2021-04-03 03:00:00"), direction="inbound", caller="", caller_type="anonymous",
                 status="no_answer", end_reason="user_hangup")
        add_call(db, "f-f1", at("2021-04-03 04:00:00"), lead=on_line, direction="inbound", caller_type="new",
                 status="in_progress")
        add_call(db, "f-g1", at("2021-04-03 05:00:00"), lead=g, words=18, duration=70)
        # a browser rehearsal of an inbound call (with lead A): never clears A's missed call
        add_call(db, "f-h1", at("2021-04-03 06:00:00"), lead=a, channel="browser", direction="inbound", words=5)
        db.commit()

    rng = {"from": "2021-04-01", "to": "2021-04-03", "page_size": 200}

    def get(**params) -> dict:
        r = client.get("/api/calls", params={**rng, **params})
        assert r.status_code == 200, r.text
        return r.json()

    inbound = get(direction="inbound")
    assert uuids(inbound) == {"f-a1", "f-b1", "f-b2", "f-c1", "f-c3", "f-d1", "f-d2", "f-m1", "f-m2", "f-e1",
                              "f-f1", "f-h1"}
    assert inbound["total"] == 12 and all(set(i) == LIST_KEYS for i in inbound["items"])
    assert uuids(get(direction="outbound")) == {"f-a2", "f-c2", "f-k1", "f-g1"}
    missed, answered, live = get(direction="inbound", outcome="missed"), get(direction="inbound", outcome="answered"), \
        get(direction="inbound", outcome="live")
    assert uuids(missed) == {"f-a1", "f-b1", "f-c1", "f-c3", "f-d1", "f-d2", "f-m1", "f-e1"}
    assert uuids(answered) == {"f-b2", "f-m2", "f-h1"} and uuids(live) == {"f-f1"}
    assert inbound["total"] == missed["total"] + answered["total"] + live["total"]  # calls = answered + missed + live
    assert uuids(get(direction="inbound", outcome="answered,live")) == {"f-b2", "f-m2", "f-h1", "f-f1"}

    follow = {i["uuid"]: i["follow_up"] for i in inbound["items"]}
    assert follow == {"f-a1": "tried", "f-b1": "done", "f-c1": "pending", "f-c3": "pending", "f-d1": "pending",
                      "f-d2": "done", "f-m1": "done", "f-e1": None, "f-b2": None, "f-m2": None, "f-f1": None,
                      "f-h1": None}
    pending = get(follow_up="pending")
    assert uuids(pending) == {"f-c1", "f-c3", "f-d1"} and {i["follow_up"] for i in pending["items"]} == {"pending"}
    assert uuids(get(follow_up="pending", caller_type="international")) == {"f-d1"}
    assert uuids(get(caller_type="international")) == {"f-d1", "f-d2"}
    assert uuids(get(caller_type="new,anonymous")) == {"f-f1", "f-e1"}
    assert uuids(get(direction="outbound", outcome="missed")) == {"f-a2", "f-c2"}

    # IST day boundaries on created_at: 2 Apr IST = 1 Apr 18:30 UTC .. 2 Apr 18:30 UTC
    day2 = client.get("/api/calls", params={"from": "2021-04-02", "to": "2021-04-02", "direction": "inbound"}).json()
    assert uuids(day2) == {"f-b2", "f-c1", "f-c3", "f-d1", "f-d2", "f-m1", "f-m2"}
    # the KPI tile counts the same "to call back" calls as the tab
    s = client.get("/api/inbound/summary", params={"from": "2021-04-01", "to": "2021-04-03"}).json()
    assert s["totals"]["to_call_back"] == 3
    assert s["totals"]["calls"] == 11  # phone only: the browser rehearsal never counts in KPIs
    assert s["totals"]["calls"] == s["totals"]["answered"] + s["totals"]["missed"] + 1  # + the live call

    for bad in ({"direction": "sideways"}, {"outcome": "maybe"}, {"follow_up": "done"}, {"caller_type": "vip"},
                {"from": "2021-13-01"}, {"to": "yesterday"}, {"from": "2021-04-03", "to": "2021-04-01"}):
        assert client.get("/api/calls", params=bad).status_code == 422, bad
    with SessionLocal() as db:  # leave nothing "live" behind for other tests
        db.execute(update(Call).where(Call.uuid == "f-f1").values(status="completed"))
        db.commit()


def test_calls_search_by_number(client):
    with SessionLocal() as db:
        day0 = at("2021-04-30 08:00:00")
        ram = add_lead(db, "+919500000301", "Ramesh Traders", created=day0)
        out = add_lead(db, "+919500000303", "Outbound Person", created=day0)
        add_call(db, "s-ram-in", at("2021-05-01 05:00:00"), lead=ram, direction="inbound", caller_type="known",
                 words=10, duration=30)
        add_call(db, "s-short", at("2021-05-01 06:00:00"), direction="inbound", caller="12345",
                 caller_type="anonymous", status="no_answer", end_reason="user_hangup")
        add_call(db, "s-orphan", at("2021-05-01 07:00:00"), direction="inbound", caller="+919500000302",
                 caller_type="known", status="no_answer", end_reason="lines_busy")
        add_call(db, "s-out", at("2021-05-01 08:00:00"), lead=out, status="no_answer")
        db.commit()

    def found(term: str) -> set[str]:
        r = client.get("/api/calls", params={"search": term, "from": "2021-05-01", "to": "2021-05-02"})
        assert r.status_code == 200, r.text
        return uuids(r.json())

    assert found("95000 00302") == {"s-orphan"}           # a caller with no lead, by number
    assert found("0950000 0302") == {"s-orphan"}          # typed with the trunk 0
    assert found("+91 95000-00303") == {"s-out"}          # the dialed number on an outbound call
    assert found("9500000301") == {"s-ram-in"}
    assert found("8000000000") == set()                   # our own DID never matches
    assert found("+91 80000 00000") == set()
    assert found("Ramesh") == {"s-ram-in"}                # text search unchanged
    assert found("2345") == {"s-short"}                   # 4+ digits: number match
    assert found("234") == set()                          # fewer than 4 digits: no number match


# ---------------------------------------------------------------- inbound summary
def test_inbound_summary_counts_and_ist_boundary(client):
    rng = {"from": "2021-02-01", "to": "2021-02-02"}
    live_before = client.get("/api/inbound/summary", params=rng).json()["live"]
    with SessionLocal() as db:
        n = add_lead(db, "+919500000401", "New Caller", created=at("2021-02-01 18:29:59.999"), source="Inbound call")
        o = add_lead(db, "+919500000402", "Second New", created=at("2021-02-02 07:00:00"), source="Inbound call")
        p = add_lead(db, "+919500000403", "Known Lead", created=at("2021-01-15 10:00:00"))
        q = add_lead(db, "+919500000404", "On The Line", created=at("2021-01-15 10:00:00"))
        # 31 Jan 23:59:59.999 IST and 3 Feb 00:00 IST: just outside the range
        add_call(db, "sum-before", at("2021-01-31 18:29:59.999"), lead=p, direction="inbound", caller_type="known",
                 status="no_answer", end_reason="user_hangup")
        add_call(db, "sum-after", at("2021-02-02 18:30:00.000"), lead=o, direction="inbound", caller_type="known",
                 status="no_answer", end_reason="user_hangup")
        # 1 Feb 23:59:59.999 IST (last instant of day 1) and 2 Feb 00:00:00.000 IST (first instant of day 2)
        add_call(db, "sum-r1", at("2021-02-01 18:29:59.999"), lead=n, direction="inbound", caller_type="new",
                 words=12, duration=100, interest="hot", rating=8, analysis="done")
        add_call(db, "sum-r2", at("2021-02-01 18:30:00.000"), lead=p, direction="inbound", caller_type="known",
                 status="no_answer", end_reason="inbound_off")
        add_call(db, "sum-r3", at("2021-02-02 05:00:00"), direction="inbound", caller="", caller_type="anonymous",
                 status="no_answer", end_reason="lines_busy")
        add_call(db, "sum-r4", at("2021-02-02 06:00:00"), lead=n, direction="inbound", caller_type="returning",
                 words=30, duration=50, interest="warm", rating=6, analysis="done")
        add_call(db, "sum-r5", at("2021-02-02 07:00:00"), lead=o, direction="inbound", caller_type="new",
                 status="failed", end_reason="error")
        add_call(db, "sum-live", at("2021-02-02 08:00:00"), lead=q, direction="inbound", caller_type="known",
                 status="in_progress")
        # never counted: an outbound call and a browser rehearsal in the range
        add_call(db, "sum-out", at("2021-02-01 10:00:00"), lead=p, status="no_answer")
        add_call(db, "sum-browser", at("2021-02-01 11:00:00"), lead=n, channel="browser", direction="inbound", words=8)
        # the day after the range we talked with P: their missed call is no longer "to call back"
        add_call(db, "sum-p-later", at("2021-02-03 09:00:00"), lead=p, words=20, duration=60)
        db.commit()

    try:
        r = client.get("/api/inbound/summary", params=rng)
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["from"] == "2021-02-01" and s["to"] == "2021-02-02" and s["live"] == live_before + 1
        assert s["totals"] == {"calls": 6, "answered": 2, "missed": 3, "to_call_back": 1, "new_callers": 2,
                               "returning_callers": 1, "anonymous": 1, "avg_duration_sec": 75.0,
                               "hot": 1, "warm": 1, "cold": 0}
        assert s["by_day"] == [{"date": "2021-02-01", "calls": 1, "answered": 1, "missed": 0},
                               {"date": "2021-02-02", "calls": 5, "answered": 1, "missed": 3}]
        day1 = client.get("/api/inbound/summary", params={"from": "2021-02-01", "to": "2021-02-01"}).json()
        assert day1["totals"]["calls"] == 1 and day1["totals"]["new_callers"] == 1
        assert day1["totals"]["to_call_back"] == 0 and len(day1["by_day"]) == 1
        today = client.get("/api/inbound/summary").json()  # default: today (IST) only
        assert today["from"] == today["to"] and len(today["by_day"]) == 1
    finally:
        with SessionLocal() as db:  # leave nothing "live" behind for other tests
            db.execute(update(Call).where(Call.uuid == "sum-live").values(status="completed"))
            db.commit()


def test_inbound_summary_validation(client):
    def code(**params) -> int:
        return client.get("/api/inbound/summary", params=params).status_code

    assert code(**{"from": "2021-02-03", "to": "2021-02-01"}) == 422      # from after to
    assert code(**{"from": "2020-01-01", "to": "2021-01-01"}) == 422      # 367 days
    assert code(**{"from": "2020-01-02", "to": "2021-01-01"}) == 200      # 366 days
    assert code(**{"from": "bad"}) == 422
    assert code(**{"to": "2021-02-30"}) == 422
    s = client.get("/api/inbound/summary", params={"from": "2021-02-05", "to": "2021-02-07"}).json()
    assert set(s) == {"from", "to", "totals", "by_day", "live"}
    assert set(s["totals"]) == {"calls", "answered", "missed", "to_call_back", "new_callers", "returning_callers",
                                "anonymous", "avg_duration_sec", "hot", "warm", "cold"}
    assert [d["date"] for d in s["by_day"]] == ["2021-02-05", "2021-02-06", "2021-02-07"]
    assert s["totals"]["avg_duration_sec"] is None and s["totals"]["calls"] == 0


# ---------------------------------------------------------------- metric scopes
def test_metrics_do_not_count_inbound_as_dialed(client):
    with SessionLocal() as db:
        day0 = at("2021-05-31 08:00:00")
        x = add_lead(db, "+919500000501", "X", created=day0)
        y = add_lead(db, "+919500000502", "Y", created=day0)
        z = add_lead(db, "+919500000503", "Z", created=day0)
        add_call(db, "m-out-hot", at("2021-06-01 05:00:00"), lead=x, words=30, duration=120, interest="hot", rating=8,
                 analysis="done")
        add_call(db, "m-out-na", at("2021-06-01 06:00:00"), lead=y, status="no_answer")
        add_call(db, "m-in-warm", at("2021-06-02 05:00:00"), lead=z, direction="inbound", caller_type="known",
                 words=20, duration=60, interest="warm", rating=6, analysis="done")
        add_call(db, "m-in-busy", at("2021-06-02 06:00:00"), lead=y, direction="inbound", caller_type="known",
                 status="no_answer", end_reason="lines_busy")
        add_call(db, "m-in-off", at("2021-06-02 07:00:00"), direction="inbound", caller="+919500000509",
                 caller_type="known", status="no_answer", end_reason="inbound_off")
        db.commit()

    rng = {"from": "2021-06-01", "to": "2021-06-02"}
    a = client.get("/api/analytics", params=rng).json()
    t = a["totals"]
    assert (t["dialed"], t["connected"], t["connect_rate"]) == (2, 1, 0.5)          # outbound only
    assert (t["inbound"], t["inbound_answered"], t["inbound_missed"]) == (3, 1, 2)
    assert t["conversations"] == 2 and (t["hot"], t["warm"], t["cold"]) == (1, 1, 0)  # all phone calls
    assert t["avg_rating"] == 7.0 and t["talk_minutes"] == 3.0 and t["avg_duration_sec"] == 90.0
    funnel = [f["count"] for f in a["funnel"]]
    assert funnel == [2, 1, 1, 1, 1] and funnel == sorted(funnel, reverse=True)
    assert sum(d["dialed"] for d in a["by_day"]) == 2 and sum(d["inbound"] for d in a["by_day"]) == 3
    assert sum(d["connected"] for d in a["by_day"]) == 1 and sum(h["dialed"] for h in a["by_hour"]) == 2
    assert sum(a["interest"].values()) == 5
    reasons = {r["reason"]: r["count"] for r in a["end_reasons"]}
    assert reasons["Missed (lines busy)"] == 1 and reasons["Missed (inbound off)"] == 1

    # more inbound calls (even a hot one) never change what was dialed or the funnel
    with SessionLocal() as db:
        w = add_lead(db, "+919500000504", "W", created=at("2021-05-31 08:00:00"))
        add_call(db, "m-in-hot", at("2021-06-02 08:00:00"), lead=w, direction="inbound", caller_type="new", words=50,
                 duration=200, interest="hot", rating=9, analysis="done")
        add_call(db, "m-in-cut", at("2021-06-02 09:00:00"), lead=w, direction="inbound", caller_type="returning",
                 status="no_answer", end_reason="user_hangup")
        db.commit()
    a2 = client.get("/api/analytics", params=rng).json()
    assert [f["count"] for f in a2["funnel"]] == funnel
    assert (a2["totals"]["dialed"], a2["totals"]["connected"]) == (2, 1)
    assert sum(h["dialed"] for h in a2["by_hour"]) == 2
    assert (a2["totals"]["inbound"], a2["totals"]["inbound_answered"], a2["totals"]["hot"]) == (5, 2, 2)

    # dashboard: the per-day series and the tiles agree, and inbound calls never count as dialed
    before = client.get("/api/dashboard/stats").json()
    for s in (before,):
        assert s["calls_by_day"][-1]["total"] == s["totals"]["calls_today"]
        assert s["calls_by_day"][-1]["inbound"] == s["totals"]["inbound_today"]
    with SessionLocal() as db:
        v = add_lead(db, "+919500000510", "Today Caller")
        add_call(db, "m-today-missed", utcnow(), lead=v, direction="inbound", caller_type="known",
                 status="no_answer", end_reason="lines_busy")
        add_call(db, "m-today-talk", utcnow(), lead=v, direction="inbound", caller_type="known", words=10, duration=30)
        db.commit()
    after = client.get("/api/dashboard/stats").json()
    bt, at_ = before["totals"], after["totals"]
    assert at_["inbound_today"] == bt["inbound_today"] + 2 and at_["inbound_missed_today"] == bt["inbound_missed_today"] + 1
    assert (at_["calls"], at_["calls_today"], at_["connected"]) == (bt["calls"], bt["calls_today"], bt["connected"])
    assert after["calls_by_day"][-1]["total"] == at_["calls_today"]
    assert after["calls_by_day"][-1]["inbound"] == at_["inbound_today"]
    assert all(set(d) == {"date", "total", "connected", "hot", "inbound"} for d in after["calls_by_day"])


def test_pdf_and_csv_with_inbound(client, monkeypatch):
    with SessionLocal() as db:
        day0 = at("2021-06-30 08:00:00")
        lead = add_lead(db, "+919500000601", "Pdf Caller", created=day0)
        out_lead = add_lead(db, "+919500000602", "Pdf Outbound", created=day0)
        call = add_call(db, "p-in", at("2021-07-01 05:00:00"), lead=lead, direction="inbound", caller_type="known",
                        words=14, duration=75, interest="hot", rating=9, analysis="done",
                        summary="Wants 5000 stand-up pouches", next_action="Send the quote")
        db.add_all([TranscriptTurn(call_id=call.id, role="agent", text="नमस्ते जी! PouchWale से बोल रही हूँ, बताइए?",
                                   offset_ms=0),
                    TranscriptTurn(call_id=call.id, role="user", text="हमें 5000 stand-up pouches चाहिए", offset_ms=2500)])
        add_call(db, "p-anon", at("2021-07-01 06:00:00"), direction="inbound", caller="", caller_type="anonymous",
                 status="no_answer", end_reason="lines_busy")
        add_call(db, "p-intl", at("2021-07-01 07:00:00"), direction="inbound", caller="+12025550199",
                 caller_type="international", status="no_answer", end_reason="inbound_off")
        add_call(db, "p-out", at("2021-07-02 05:00:00"), lead=out_lead, words=5, duration=40, interest="warm",
                 rating=5, analysis="done")
        db.commit()

    bars: list[tuple[list, int | None]] = []
    tables: list[tuple[list, list]] = []
    real_hbars, real_table = pdf_report.ReportPDF.hbars, pdf_report.ReportPDF.table

    def spy_hbars(self, rows, total=None, label_w=52):
        bars.append((rows, total))
        return real_hbars(self, rows, total=total, label_w=label_w)

    def spy_table(self, headings, rows, widths, size=8):
        tables.append((headings, rows))
        return real_table(self, headings, rows, widths, size=size)

    monkeypatch.setattr(pdf_report.ReportPDF, "hbars", spy_hbars)
    monkeypatch.setattr(pdf_report.ReportPDF, "table", spy_table)

    inbound_only = {"from": "2021-07-01", "to": "2021-07-01"}
    assert client.get("/api/analytics", params=inbound_only).json()["totals"]["dialed"] == 0
    for params in ({**inbound_only, "include_transcripts": "true"}, {"from": "2021-07-01", "to": "2021-07-02"}):
        bars.clear()
        tables.clear()
        p = client.get("/api/reports/summary.pdf", params=params)
        assert p.status_code == 200 and p.content[:5] == b"%PDF-", params
        for rows, total in bars:  # no bar may claim more than 100%
            assert total is None or all(value <= total for _, value, _ in rows), (rows, total)
        all_calls = next(rows for headings, rows in tables if "In/Out" in headings)
        by_phone = {row[3]: row for row in all_calls}
        assert by_phone["+919500000601"][1] == "In" and by_phone["Hidden number"][1] == "In"
        assert by_phone["+12025550199"][1] == "In"
        assert OUR_DID not in {row[3] for row in all_calls}  # our own number is never printed as the customer's
        hot = next(rows for headings, rows in tables if headings[-1] == "Last call")
        assert hot[0][:2] == ["Pdf Caller", "+919500000601"]
    assert by_phone["+919500000602"][1] == "Out"  # the mixed range has the outbound call too

    r = client.get("/api/reports/calls.csv", params={"from": "2021-07-01", "to": "2021-07-02"})
    assert r.status_code == 200

    def plain(value: str) -> str:  # cells starting with "+" carry Excel's text marker (csvsafe.cell)
        return value[1:] if value.startswith("'") else value

    rows = {row["call_id"]: {k: plain(v) for k, v in row.items()}
            for row in csv.DictReader(io.StringIO(r.content.decode("utf-8-sig")))}
    assert set(rows) == {"p-in", "p-anon", "p-intl", "p-out"}
    assert {"direction", "customer_number", "caller_type", "call_reason"} <= set(rows["p-in"])
    assert (rows["p-in"]["direction"], rows["p-in"]["phone"], rows["p-in"]["customer_number"]) == \
        ("inbound", "+919500000601", "+919500000601")
    assert (rows["p-intl"]["phone"], rows["p-intl"]["customer_number"], rows["p-intl"]["caller_type"]) == \
        ("+12025550199", "+12025550199", "international")
    assert (rows["p-out"]["direction"], rows["p-out"]["customer_number"]) == ("outbound", "+919500000602")
    assert OUR_DID not in {row["phone"] for row in rows.values()} | {row["customer_number"] for row in rows.values()}
    assert "हमें 5000 stand-up pouches" in rows["p-in"]["transcript"]
