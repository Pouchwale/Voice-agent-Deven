"""Kanban board, analytics and report exports."""
from __future__ import annotations

import csv
import io
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, utcnow
from app.main import app
from app.models import Call, Lead, TranscriptTurn


@pytest.fixture()
def client():
    with TestClient(app, client=("127.0.0.1", 50000)) as c:  # the dashboard on this machine
        yield c


def _lead(db, phone: str, name: str, interest: str = "unknown", attempts: int = 0, rating=None) -> Lead:
    lead = Lead(name=name, phone=phone, company=f"{name} Co", interest=interest, attempts=attempts, rating=rating,
                status="called" if attempts else "new", last_called_at=utcnow() if attempts else None)
    db.add(lead)
    db.flush()
    return lead


def _call(db, lead: Lead, *, answered: bool, interest: str = "unknown", rating=None, words: int = 0,
          tags=(), end_reason="agent_ended", minutes_ago: int = 5) -> Call:
    created = utcnow() - timedelta(minutes=minutes_ago)
    c = Call(uuid=f"rep-{lead.phone[-6:]}-{minutes_ago}", lead_id=lead.id, channel="phone", status="completed",
             created_at=created, started_at=created, answered_at=created if answered else None,
             ended_at=created + timedelta(seconds=70), duration_sec=70 if answered else None,
             interest=interest, rating=rating, end_reason=end_reason if answered else "no_answer",
             analysis_status="done", qualified=interest == "hot", sentiment="positive" if interest == "hot" else "neutral",
             summary=f"{lead.name} summary", next_action=f"Call {lead.name} back",
             metrics={"user_words": words, "objection_tags": list(tags)},
             qualification=[{"question": "Quantity", "answer": "5000" if interest == "hot" else None,
                             "status": "answered" if interest == "hot" else "not_asked"}])
    db.add(c)
    db.flush()
    if answered and words:
        db.add_all([TranscriptTurn(call_id=c.id, role="agent", text="नमस्ते जी! क्या अभी दो minute बात कर सकते हैं?",
                                   offset_ms=0),
                    TranscriptTurn(call_id=c.id, role="user", text="हाँ जी, हमें stand-up pouch चाहिए", offset_ms=3000),
                    TranscriptTurn(call_id=c.id, role="agent", text="अच्छा जी, कितनी quantity चाहिए?", offset_ms=5000,
                                   latency_ms=1100)])
    return c


@pytest.fixture()
def data():
    with SessionLocal() as db:
        db.query(TranscriptTurn).delete()
        db.query(Call).delete()
        db.query(Lead).delete()
        new = _lead(db, "+919100000001", "New Lead")
        na = _lead(db, "+919100000002", "No Answer Lead", attempts=1)
        cold = _lead(db, "+919100000003", "Cold Lead", "cold", 1, 1)
        warm = _lead(db, "+919100000004", "Warm Lead", "warm", 1, 5)
        hot = _lead(db, "+919100000005", "राहुल शर्मा", "hot", 1, 9)
        _call(db, na, answered=False, minutes_ago=50)
        _call(db, cold, answered=True, interest="cold", rating=1, end_reason="user_hangup", minutes_ago=40)
        _call(db, warm, answered=True, interest="warm", rating=5, words=20, tags=("call_later",), minutes_ago=30)
        _call(db, hot, answered=True, interest="hot", rating=9, words=45, tags=("price",), minutes_ago=20)
        db.commit()
        ids = {"new": new.id, "no_answer": na.id, "cold": cold.id, "warm": warm.id, "hot": hot.id}
    yield ids


def test_board_columns_and_drag_move(client, data):
    b = client.get("/api/leads/board").json()
    assert [c["key"] for c in b["columns"]] == ["new", "no_answer", "cold", "warm", "hot"]
    counts = {c["key"]: c["count"] for c in b["columns"]}
    assert counts == {"new": 1, "no_answer": 1, "cold": 1, "warm": 1, "hot": 1}
    hot_col = next(c for c in b["columns"] if c["key"] == "hot")
    assert hot_col["leads"][0]["name"] == "राहुल शर्मा" and hot_col["leads"][0]["stage"] == "hot"

    # sales team drags Warm -> Hot, and No answer -> New
    r = client.patch(f"/api/leads/{data['warm']}", json={"stage": "hot"})
    assert r.status_code == 200 and r.json()["stage"] == "hot" and r.json()["interest"] == "hot"
    r = client.patch(f"/api/leads/{data['no_answer']}", json={"stage": "new"})
    assert r.json()["stage"] == "new" and r.json()["attempts"] == 0
    r = client.patch(f"/api/leads/{data['new']}", json={"stage": "no_answer"})
    assert r.json()["stage"] == "no_answer"
    assert client.patch(f"/api/leads/{data['new']}", json={"stage": "closed"}).status_code == 422

    page = client.get("/api/leads", params={"stage": "hot"}).json()
    assert page["total"] == 2 and page["items"][0]["rating"] == 9  # sorted by rating
    assert client.get("/api/leads/board", params={"search": "राहुल"}).json()["columns"][4]["count"] == 1


def test_analytics_numbers(client, data):
    a = client.get("/api/analytics").json()
    t = a["totals"]
    assert (t["dialed"], t["connected"], t["conversations"]) == (4, 3, 2)
    assert (t["hot"], t["warm"], t["cold"]) == (1, 1, 1)
    assert t["avg_latency_ms"] == 1100 and t["p90_latency_ms"] == 1100
    assert [f["count"] for f in a["funnel"]] == [4, 3, 2, 2, 1]
    assert len(a["by_hour"]) == 24 and len(a["rating_histogram"]) == 10 and len(a["by_day"]) == 30
    assert {o["tag"] for o in a["objections"]} == {"price", "call_later"}
    assert a["interest"] == {"unknown": 1, "cold": 1, "warm": 1, "hot": 1}
    assert sum(b["count"] for b in a["duration_buckets"]) == 3
    q = a["qualification"][0]
    assert q["question"] == "Quantity" and q["answered"] == 1
    empty = client.get("/api/analytics", params={"from": "2020-01-01", "to": "2020-01-31"}).json()
    assert empty["totals"]["dialed"] == 0 and len(empty["by_day"]) == 31
    assert client.get("/api/analytics", params={"from": "bad"}).status_code == 422


def test_reports_csv_and_pdf(client, data):
    r = client.get("/api/reports/calls.csv")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert len(rows) == 4
    hot = next(x for x in rows if x["interest"] == "hot")
    assert hot["lead_name"] == "राहुल शर्मा" and "हमें stand-up pouch" in hot["transcript"]
    assert "Quantity: 5000 (answered)" in hot["qualification"] and hot["objection_tags"] == "price"

    leads = list(csv.DictReader(io.StringIO(client.get("/api/reports/leads.csv").content.decode("utf-8-sig"))))
    assert {l["stage"] for l in leads} == {"new", "no_answer", "cold", "warm", "hot"}

    for transcripts in ("false", "true"):
        p = client.get("/api/reports/summary.pdf", params={"include_transcripts": transcripts})
        assert p.status_code == 200 and p.headers["content-type"] == "application/pdf"
        assert p.content[:5] == b"%PDF-" and len(p.content) > 20_000
    empty = client.get("/api/reports/summary.pdf", params={"from": "2020-01-01", "to": "2020-01-02"})
    assert empty.status_code == 200 and empty.content[:5] == b"%PDF-"
