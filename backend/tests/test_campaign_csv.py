"""Campaign from a CSV upload (preview, de-duplication, reuse of existing leads) and per-number results."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal, utcnow
from app.main import app
from app.models import Call, Campaign, CampaignLead, Lead
from app.services.campaign_stats import FILTERS, outcome_counts

LOCAL = ("127.0.0.1", 50000)

CSV = (
    "Customer Name,Mobile Number,Brand,City,Remarks\n"
    "Asha,6300000001,Asha Foods,Surat,first\n"
    "Asha again,+91 63000 00001,Asha Foods,Surat,same number other format\n"   # duplicate of row 2
    "Bad,12345,X,Y,\n"                                                       # invalid
    "Lossy,6.3E+09,X,Y,\n"                                                   # Excel-shortened
    ",,,,\n"                                                                 # blank: ignored
    "Known,6300000003,Known Co,Pune,\n"                                      # existing lead
    "Blocked,6300000004,,,\n"                                                # existing DNC lead
    "Bina,06300000002,Bina Pouches,Rajkot,\n"                                # new (leading 0)
)


def _upload(c, path, **form):
    return c.post(path, files={"file": ("list.csv", CSV.encode("utf-8-sig"), "text/csv")}, data=form)


def test_csv_preview_and_create():
    with TestClient(app, client=LOCAL) as c:
        with SessionLocal() as db:
            db.add_all([Lead(name="", phone="+916300000003", company=None, city="Pune Old"),
                        Lead(name="Blocked", phone="+916300000004", status="dnc")])
            db.commit()
        before = c.get("/api/leads").json()["total"]

        r = _upload(c, "/api/campaigns/csv/preview")
        assert r.status_code == 200, r.text
        p = r.json()
        assert p["summary"] == {"rows": 7, "unique": 4, "new": 2, "existing": 1, "duplicate": 1, "invalid": 2,
                                "dnc": 1, "callable": 3}, p["summary"]
        by_line = {row["line"]: row for row in p["rows"]}
        assert by_line[3]["status"] == "duplicate" and by_line[3]["duplicate_of"] == 2
        assert by_line[4]["status"] == "invalid" and "valid" in by_line[4]["problem"]
        assert "Excel" in by_line[5]["problem"]
        assert 6 not in by_line                                    # blank row ignored
        assert by_line[7]["status"] == "existing" and by_line[7]["lead_id"]
        assert by_line[8]["status"] == "dnc"
        assert by_line[9]["phone"] == "+916300000002" and by_line[9]["status"] == "new"
        assert c.get("/api/leads").json()["total"] == before       # preview saves nothing

        r = _upload(c, "/api/campaigns/csv", name="CSV push", concurrency="3", max_attempts="2",
                    retry_delay_min="15", respect_call_window="false")
        assert r.status_code == 201, r.text
        camp = r.json()
        assert camp["upload"]["callable"] == 3 and camp["counts"]["total"] == 3
        assert camp["concurrency"] == 3 and camp["respect_call_window"] is False and camp["status"] == "draft"
        assert camp["outcomes"]["waiting"] == 3 and camp["calls_placed"] == 0
        assert c.get("/api/leads").json()["total"] == before + 2    # only the 2 new numbers became leads

        detail = c.get(f"/api/campaigns/{camp['id']}").json()
        assert [i["lead"]["phone"] for i in detail["items"]] == ["+916300000001", "+916300000003", "+916300000002"]
        assert all(i["result"] == "waiting" and i["last_call"] is None for i in detail["items"])
        known = detail["items"][1]["lead"]
        assert known["name"] == "Known" and known["company"] == "Known Co" and known["city"] == "Pune Old"
        assert detail["items"][0]["lead"]["source"] == "Campaign: CSV push"
        c.delete(f"/api/campaigns/{camp['id']}")


def test_csv_without_callable_numbers_creates_nothing():
    with TestClient(app, client=LOCAL) as c:
        n_campaigns = len(c.get("/api/campaigns").json())
        bad = "name,phone\nA,123\nB,\n"
        r = c.post("/api/campaigns/csv", files={"file": ("bad.csv", bad.encode(), "text/csv")}, data={"name": "x"})
        assert r.status_code == 400 and "No callable numbers" in r.json()["detail"]
        assert len(c.get("/api/campaigns").json()) == n_campaigns
        r = c.post("/api/campaigns/csv/preview", files={"file": ("x.csv", b"name,city\nA,B\n", "text/csv")})
        assert r.status_code == 400 and "No phone column" in r.json()["detail"]
        r = c.post("/api/campaigns/csv", files={"file": ("ok.csv", b"name,phone\nA,6300000099\n", "text/csv")},
                   data={"name": "x", "concurrency": "50"})
        assert r.status_code == 422  # out-of-range settings are refused


def test_outcomes_follow_each_numbers_latest_call():
    """Each number counts once, by its latest call; buckets add up to the total; filters agree."""
    now = utcnow()
    calls = {  # phone suffix -> (call status, answered, campaign item status)
        "11": None, "12": ("ringing", False, "dialing"), "13": ("in_progress", True, "dialing"),
        "14": ("completed", True, "done"), "15": ("no_answer", False, "pending"), "16": ("busy", False, "pending"),
        "17": ("failed", False, "failed"), "18": ("canceled", False, "failed"),
    }
    # App first: its startup recovery closes calls left "active" by an earlier run.
    with TestClient(app, client=LOCAL) as c:
        with SessionLocal() as db:
            camp = Campaign(name="outcomes", status="paused")  # paused: the dialer leaves it alone
            db.add(camp)
            db.flush()
            for suffix, call in calls.items():
                lead = Lead(name=f"O{suffix}", phone=f"+9163000001{suffix}")
                db.add(lead)
                db.flush()
                uuid = f"outcome-{suffix}" if call else None
                db.add(CampaignLead(campaign_id=camp.id, lead_id=lead.id, status=call[2] if call else "pending",
                                    attempts=1 if call else 0, last_call_uuid=uuid))
                if call:
                    db.add(Call(uuid=uuid, lead_id=lead.id, campaign_id=camp.id, status=call[0],
                                started_at=now, answered_at=now if call[1] else None))
            skipped = Lead(name="Skip", phone="+916300000119", status="dnc")
            db.add(skipped)
            db.flush()
            db.add(CampaignLead(campaign_id=camp.id, lead_id=skipped.id, status="skipped"))
            # an older, answered attempt must not count once a newer attempt exists
            db.add(Call(uuid="outcome-15-old", lead_id=None, campaign_id=camp.id, status="completed",
                        started_at=now, answered_at=now))
            db.commit()
            cid = camp.id
            counts = outcome_counts(db, cid)
        assert counts == {"waiting": 1, "ringing": 1, "talking": 1, "picked_up": 1, "no_answer": 2, "busy": 1,
                          "failed": 1, "skipped": 1}
        d = c.get(f"/api/campaigns/{cid}").json()
        assert d["outcomes"] == counts and d["calls_placed"] == 8 and d["active_calls"] == 2
        for name in FILTERS:
            got = c.get(f"/api/campaigns/{cid}", params={"result": name}).json()
            expected = counts["ringing"] + counts["talking"] if name == "ongoing" else counts[name]
            assert got["items_total"] == expected == len(got["items"]), name
            if name != "ongoing":
                assert {i["result"] for i in got["items"]} == {name}, name
        assert c.get(f"/api/campaigns/{cid}", params={"result": "nope"}).status_code == 422
        page = c.get(f"/api/campaigns/{cid}", params={"page": 2, "page_size": 4}).json()
        assert page["items_total"] == 9 and len(page["items"]) == 4
        with SessionLocal() as db:
            db.execute(CampaignLead.__table__.delete().where(CampaignLead.campaign_id == cid))
            db.execute(Call.__table__.delete().where(Call.campaign_id == cid))
            db.execute(Campaign.__table__.delete().where(Campaign.id == cid))
            db.commit()
