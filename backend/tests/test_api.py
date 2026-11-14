"""REST + WebSocket API tests (providers stubbed)."""
from __future__ import annotations

import base64
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Call, CampaignLead, Lead
from app.services import call_manager as cm_mod
from app.services import session as session_mod
from app.services import vobiz
from app.services.audio import pcm16_to_ulaw

from .fakes import FakeSTT, FakeTTSStream, ScriptedLLM, fake_tts_rest, tone


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(session_mod, "RealtimeSTT", FakeSTT)
    monkeypatch.setattr(session_mod, "TTSStream", FakeTTSStream)
    monkeypatch.setattr(session_mod, "tts_rest", fake_tts_rest)
    monkeypatch.setattr(session_mod, "stream_reply", ScriptedLLM(["जी बिल्कुल।"]))
    monkeypatch.setattr(cm_mod, "tts_rest", fake_tts_rest)
    dialed: list[str] = []

    async def fake_make_call(call_uuid, to, max_sec, caller_name=""):
        dialed.append(to)
        return f"vobiz-{call_uuid[:8]}"

    async def fake_hangup(pid):
        return True

    monkeypatch.setattr(vobiz, "make_call", fake_make_call)
    monkeypatch.setattr(vobiz, "hangup_call", fake_hangup)
    with TestClient(app, client=("127.0.0.1", 50000)) as c:  # the dashboard on this machine
        c.dialed = dialed
        yield c


# ---------------------------------------------------------------- leads
def test_lead_crud_and_validation(client):
    r = client.post("/api/leads", json={"name": "Amit", "phone": "098250 11111", "company": "Amit Foods"})
    assert r.status_code == 201, r.text
    lead = r.json()
    assert lead["phone"] == "+919825011111" and lead["status"] == "new" and lead["interest"] == "unknown"
    assert client.post("/api/leads", json={"name": "Dup", "phone": "+91 98250-11111"}).status_code == 409
    assert client.post("/api/leads", json={"name": "Bad", "phone": "12345"}).status_code == 400

    r = client.patch(f"/api/leads/{lead['id']}", json={"interest": "warm", "notes": "call after Diwali"})
    assert r.json()["interest"] == "warm"
    assert client.patch(f"/api/leads/{lead['id']}", json={"interest": "boiling"}).status_code == 422
    page = client.get("/api/leads", params={"search": "Amit Foods"}).json()
    assert page["total"] == 1 and page["items"][0]["id"] == lead["id"]
    detail = client.get(f"/api/leads/{lead['id']}").json()
    assert detail["calls"] == []
    assert client.delete(f"/api/leads/{lead['id']}").json() == {"ok": True}
    assert client.get(f"/api/leads/{lead['id']}").status_code == 404


def test_csv_import_export_template(client):
    csv_text = (
        "Customer Name,Mobile Number,Brand,Location,Remarks,GST\n"
        "राहुल शर्मा,9876500001,Sharma Snacks,अहमदाबाद,needs zipper pouch,24ABC\n"
        "Priya,+91 98765 00002,Organic Roots,Pune,,\n"
        "Dup Priya,9876500002,X,Y,,\n"
        "Bad Row,12,X,Y,,\n"
        "Excel,9.876500003e9,X,Y,,\n"
        "Excel lossy,9.88E+09,X,Y,,\n"
        "Garbage,98765000039,X,Y,,\n"
    )
    r = client.post("/api/leads/import", files={"file": ("leads.csv", csv_text.encode("utf-8-sig"), "text/csv")})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["imported"] == 3 and res["duplicates"] == 1 and res["invalid"] == 3, res
    assert any("Row 5" in e for e in res["errors"])
    with SessionLocal() as db:
        l = db.scalar(select(Lead).where(Lead.phone == "+919876500001"))
        assert l.name == "राहुल शर्मा" and l.city == "अहमदाबाद" and l.extra == {"GST": "24ABC"}

    bad = client.post("/api/leads/import", files={"file": ("x.csv", b"foo,bar\n1,2\n", "text/csv")})
    assert bad.status_code == 400 and "No phone column" in bad.json()["detail"]

    exp = client.get("/api/leads/export")
    assert exp.status_code == 200 and exp.text.startswith("﻿name,phone")
    assert "राहुल शर्मा" in exp.text
    tpl = client.get("/api/leads/template")
    assert tpl.status_code == 200 and "phone" in tpl.text.splitlines()[0]


# ---------------------------------------------------------------- settings
def test_settings_validation(client):
    s = client.get("/api/settings").json()
    assert s["silence_timeout_sec"] == 10 and s["silence_final_wait_sec"] == 3 and s["voice"] == "suhani"
    assert s["opening_line"].endswith("अभी बात कर सकते हैं?")  # permission-first greeting
    assert "दो minute" in s["opening_line"]
    r = client.put("/api/settings", json={"silence_timeout_sec": 12, "voice": "suhani", "call_window_start": "9:05"})
    assert r.status_code == 200 and r.json()["voice"] == "suhani" and r.json()["call_window_start"] == "09:05"
    assert client.put("/api/settings", json={"silence_timeout_sec": 999}).status_code == 422
    assert client.put("/api/settings", json={"voice": "nobody"}).status_code == 422
    assert client.put("/api/settings", json={"call_window_end": "25:00"}).status_code == 422
    assert client.post("/api/settings/reset").json()["voice"] == "suhani"
    voices = client.get("/api/settings/voices").json()
    assert {"id": "suhani", "name": "Suhani", "gender": "female", "model": "bulbul:v3"} in voices
    assert any(v["id"] == "aparna_hi_customer" and v["model"] == "bulbul:v4-flash" for v in voices)
    assert not any(v["id"] == "pooja" for v in voices)
    sysinfo = client.get("/api/settings/system").json()
    assert sysinfo["llm_model"] == "gpt-4o-mini" and sysinfo["llm_max_tokens"] == 120
    assert sysinfo["llm_temperature"] == 0.6 and sysinfo["public_url_ok"] is True


# ---------------------------------------------------------------- vobiz
def _phone_call(client, phone: str) -> dict:
    lead = client.post("/api/leads", json={"name": "Neha Gupta", "phone": phone, "company": "Neha Spices"}).json()
    r = client.post("/api/calls", json={"lead_id": lead["id"]})
    assert r.status_code == 201, r.text
    call = r.json()
    assert call["status"] == "initiated" and call["lead_name"] == "Neha Gupta"
    return call


def test_vobiz_answer_xml_and_no_answer_hangup(client):
    call = _phone_call(client, "9811100001")
    tok = vobiz.url_token(call["uuid"])
    assert client.post(f"/vobiz/answer/{call['uuid']}/wrong").status_code == 403
    assert client.post(f"/vobiz/ring/{call['uuid']}/{tok}").status_code == 200
    assert client.get(f"/api/calls/{call['uuid']}").json()["status"] == "ringing"
    # second call for same lead is refused while the first is live
    lead_id = client.get(f"/api/calls/{call['uuid']}").json()["lead_id"]
    assert client.post("/api/calls", json={"lead_id": lead_id}).status_code == 409

    r = client.post(f"/vobiz/hangup/{call['uuid']}/{tok}",
                    data={"CallUUID": "x", "CallStatus": "no-answer", "HangupCauseCode": "3000"})
    assert r.status_code == 200
    d = client.get(f"/api/calls/{call['uuid']}").json()
    assert d["status"] == "no_answer" and d["end_reason"] == "no_answer" and d["analysis_status"] == "skipped"


def test_answer_xml_shape(client):
    call = _phone_call(client, "9811100002")
    tok = vobiz.url_token(call["uuid"])
    r = client.post(f"/vobiz/answer/{call['uuid']}/{tok}", data={"CallUUID": "prov-123"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    xml = r.text
    assert xml.index("<Record") < xml.index("<Stream")
    assert 'bidirectional="true"' in xml and 'keepCallAlive="true"' in xml
    assert 'contentType="audio/x-mulaw;rate=8000"' in xml and 'audioTrack="inbound"' in xml
    assert f"wss://agent.example.com/vobiz/stream/{call['uuid']}/{tok}</Stream>" in xml
    d = client.get(f"/api/calls/{call['uuid']}").json()
    assert d["status"] == "in_progress"
    client.post(f"/api/calls/{call['uuid']}/hangup")


def test_vobiz_media_stream_full_call(client):
    call = _phone_call(client, "9811100003")
    tok = vobiz.url_token(call["uuid"])
    client.post(f"/vobiz/answer/{call['uuid']}/{tok}", data={"CallUUID": "prov-456"})
    with client.websocket_connect(f"/vobiz/stream/{call['uuid']}/{tok}") as ws:
        ws.send_text(json.dumps({"event": "start", "sequenceNumber": 0,
                                 "start": {"callId": "prov-456", "streamId": "stream-1", "tracks": ["inbound"],
                                           "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000}}}))
        mu = pcm16_to_ulaw(tone(20, 8000))
        for i in range(60):  # 1.2 s of customer audio
            ws.send_text(json.dumps({"event": "media", "streamId": "stream-1",
                                     "media": {"track": "inbound", "chunk": i, "payload": base64.b64encode(mu).decode()}}))
        msg = json.loads(ws.receive_text())  # greeting audio comes back as playAudio
        assert msg["event"] == "playAudio" and msg["streamId"] == "stream-1"
        assert msg["media"]["contentType"] == "audio/x-mulaw" and msg["media"]["sampleRate"] == 8000
        assert 0 < len(base64.b64decode(msg["media"]["payload"])) <= 480
        ws.send_text(json.dumps({"event": "stop"}))
    deadline = time.time() + 5
    while time.time() < deadline:
        d = client.get(f"/api/calls/{call['uuid']}").json()
        if d["status"] == "completed":
            break
        time.sleep(0.1)
    assert d["status"] == "completed" and d["end_reason"] == "user_hangup"
    assert d["transcript"][0]["role"] == "agent" and d["has_recording"]
    # picked up, never spoke, then cut the call -> cold (client rule), lead moves to the Cold column
    deadline = time.time() + 5
    while time.time() < deadline and d["analysis_status"] != "done":
        time.sleep(0.1)
        d = client.get(f"/api/calls/{call['uuid']}").json()
    assert (d["interest"], d["rating"]) == ("cold", 1)
    assert client.get(f"/api/leads/{d['lead_id']}").json()["stage"] == "cold"
    rec = client.get(f"/api/calls/{call['uuid']}/recording", headers={"Range": "bytes=0-99"})
    assert rec.status_code == 206 and len(rec.content) == 100 and rec.content[:4] == b"RIFF"
    assert FakeSTT.instances[-1].encoding == "mulaw" and FakeSTT.instances[-1].audio_bytes >= 60 * 160


def test_browser_call_websocket(client):
    with client.websocket_connect("/ws/browser-call") as ws:
        ws.send_text(json.dumps({"type": "start", "name": "Test User", "company": "Demo Co"}))
        ready = json.loads(ws.receive_text())
        assert ready["type"] == "ready" and ready["input_sample_rate"] == 16000 and ready["output_sample_rate"] == 24000
        got_audio = False
        for _ in range(200):
            m = ws.receive()
            if m.get("bytes"):
                got_audio = True
                break
        assert got_audio
        ws.send_text(json.dumps({"type": "hangup"}))
        ended = None
        for _ in range(500):
            m = ws.receive()
            if m.get("type") == "websocket.close":
                break
            if m.get("text") and json.loads(m["text"]).get("type") == "ended":
                ended = json.loads(m["text"])
                break
        assert ended and ended["reason"] == "manual_hangup"
    d = client.get(f"/api/calls/{ready['uuid']}").json()
    assert d["channel"] == "browser" and d["end_reason"] == "manual_hangup"


# ---------------------------------------------------------------- campaigns + dialer
def test_campaign_dials_and_retries(client):
    ids = [client.post("/api/leads", json={"name": f"C{i}", "phone": f"97000000{i:02d}"}).json()["id"]
           for i in range(3)]
    r = client.post("/api/campaigns", json={"name": "Diwali push", "lead_ids": ids, "concurrency": 2,
                                            "max_attempts": 2, "retry_delay_min": 1, "respect_call_window": False})
    assert r.status_code == 201, r.text
    camp = r.json()
    assert camp["counts"]["total"] == 3 and camp["status"] == "draft"
    assert client.post(f"/api/campaigns/{camp['id']}/start").json()["status"] == "running"

    deadline = time.time() + 8
    while time.time() < deadline and len(client.dialed) < 2:
        time.sleep(0.1)
    assert len(client.dialed) == 2  # concurrency respected
    time.sleep(0.5)
    with SessionLocal() as db:
        live = db.scalars(select(Call).where(Call.campaign_id == camp["id"])).all()
        assert len(live) == 2
        first = live[0]
    # first call: no answer -> retried later (attempt 1 of 2)
    tok = vobiz.url_token(first.uuid)
    client.post(f"/vobiz/hangup/{first.uuid}/{tok}", data={"HangupCauseCode": "3000"})
    time.sleep(0.3)
    with SessionLocal() as db:
        item = db.scalar(select(CampaignLead).where(CampaignLead.last_call_uuid == first.uuid))
        assert item.status == "pending" and item.attempts == 1 and item.next_attempt_at is not None
    deadline = time.time() + 8
    while time.time() < deadline and len(client.dialed) < 3:
        time.sleep(0.1)
    assert len(client.dialed) == 3  # freed slot used for the third lead
    assert client.post(f"/api/campaigns/{camp['id']}/pause").json()["status"] == "paused"
    assert client.delete(f"/api/campaigns/{camp['id']}").json() == {"ok": True}


def test_dashboard_stats_shape(client):
    s = client.get("/api/dashboard/stats").json()
    assert set(s) == {"totals", "interest", "rating_histogram", "calls_by_day", "end_reasons", "recent_calls",
                      "top_leads"}
    assert len(s["rating_histogram"]) == 10 and len(s["calls_by_day"]) == 14
    assert set(s["interest"]) == {"unknown", "cold", "warm", "hot"}
    assert {"inbound_today", "inbound_missed_today"} <= set(s["totals"])
    assert all(set(d) == {"date", "total", "connected", "hot", "inbound"} for d in s["calls_by_day"])
    # the per-day series and the tiles agree (today = the last day)
    assert s["calls_by_day"][-1]["total"] == s["totals"]["calls_today"]
    assert s["calls_by_day"][-1]["inbound"] == s["totals"]["inbound_today"]


def test_dashboard_excludes_browser_test_calls(client):
    from app.db import utcnow
    with SessionLocal() as db:
        db.add(Call(uuid="browser-metric-check", channel="browser", status="completed", rating=10, interest="hot",
                    qualified=True, duration_sec=99, answered_at=utcnow(), end_reason="agent_ended"))
        db.commit()
    s = client.get("/api/dashboard/stats").json()
    assert s["rating_histogram"][9]["count"] == 0  # rating 10 came only from the browser call
    assert all(d["total"] == 0 or d["date"] for d in s["calls_by_day"])
    assert sum(d["total"] for d in s["calls_by_day"]) == s["totals"]["calls_today"] + sum(
        d["total"] for d in s["calls_by_day"][:-1])
    recent = [c["uuid"] for c in s["recent_calls"]]
    assert "browser-metric-check" in recent  # still visible to testers
