"""Inbound calling: answer webhook, caller resolution, availability gate, retries, hangups, supersede,
media stream, Vobiz setup, offline back-fill, outbound switch, dialer courtesy and callback mode.

Numbers used here (one database is shared by all tests): mobiles +9193000xxxxx, landlines +91261xxxxxxx,
foreign +1415555xxxx / +1930...; Vobiz CallUUIDs start with "inb-" or "cdr-".
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import time
import uuid as uuidlib
import wave
import xml.etree.ElementTree as ET
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, or_, select, update

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.main import app
from app.models import ACTIVE_CALL_STATUSES, Call, Campaign, CampaignLead, Lead, TranscriptTurn
from app.routers import inbound as inbound_router
from app.schemas import iso, lead_out
from app.services import agent_config, inbound_audio, inbound_sync, vobiz
from app.services import analysis as analysis_mod
from app.services import call_manager as cm_mod
from app.services import dialer as dialer_mod
from app.services import session as session_mod
from app.services.audio import pcm16_to_ulaw
from app.services.call_manager import CallError, manager
from app.services.dialer import Dialer, dialer
from app.services.phone import caller_e164, normalize_caller, normalize_phone
from app.services.prompts import fill_template

from .fakes import FakeSTT, FakeTTSStream, FakeVobizAPI, ScriptedLLM, fake_tts_rest, tone

settings = get_settings()
LOCAL = ("127.0.0.1", 50000)
OUR_DID = "+918000000000"


# ---------------------------------------------------------------- fixtures + helpers
def _pause_running_campaigns() -> list[int]:
    """Leftover running campaigns of other tests would be dialed by this test's app: park them."""
    with SessionLocal() as db:
        ids = list(db.scalars(select(Campaign.id).where(Campaign.status == "running")).all())
        if ids:
            db.execute(update(Campaign).where(Campaign.id.in_(ids)).values(status="paused"))
            db.commit()
    return ids


def _resume(ids: list[int]) -> None:
    if ids:
        with SessionLocal() as db:
            db.execute(update(Campaign).where(Campaign.id.in_(ids), Campaign.status == "paused")
                       .values(status="running"))
            db.commit()


def _end_my_calls() -> None:
    """Close every call of this file still marked live, so later tests' live counts are untouched."""
    mine = or_(Call.provider_call_id.like("inb-%"), Call.provider_call_id.like("cdr-%"),
               Call.from_number.like("+9193%"), Call.to_number.like("+9193%"), Call.from_number.like("+91261%"),
               Call.from_number.like("+1%"), Call.from_number == "", Call.from_number == OUR_DID)
    with SessionLocal() as db:
        uuids = list(db.scalars(select(Call.uuid).where(Call.status.in_(ACTIVE_CALL_STATUSES), mine)).all())
        if uuids:
            db.execute(update(Call).where(Call.uuid.in_(uuids)).values(status="failed", end_reason="error",
                                                                       ended_at=utcnow()))
            db.commit()
    for u in uuids:
        manager.prepared.pop(u, None)
    for key in [k for k in manager._early_hangups if k.startswith(("inb-", "cdr-"))]:
        manager._early_hangups.pop(key, None)


@pytest.fixture()
def client(monkeypatch):
    FakeSTT.instances.clear()
    FakeTTSStream.sentences.clear()
    llm = ScriptedLLM(["जी, बताइए किस product के लिए pouch चाहिए?", "जी ज़रूर, हमारे expert आपको call करेंगे।"])
    monkeypatch.setattr(session_mod, "RealtimeSTT", FakeSTT)
    monkeypatch.setattr(session_mod, "TTSStream", FakeTTSStream)
    monkeypatch.setattr(session_mod, "tts_rest", fake_tts_rest)
    monkeypatch.setattr(session_mod, "stream_reply", llm)
    monkeypatch.setattr(cm_mod, "tts_rest", fake_tts_rest)
    monkeypatch.setattr(inbound_audio, "tts_rest", fake_tts_rest)
    monkeypatch.setattr(inbound_audio, "schedule_prewarm", lambda cfg: None)  # each test decides what is cached
    inbound_audio._clips.clear()
    inbound_audio._building.clear()
    inbound_router._clear_check()

    async def fake_analysis(messages, max_tokens=None):
        return {"summary": "Caller asked about pouches.", "rating": 6, "interest": "warm", "qualified": False,
                "sentiment": "positive", "next_action": "Share a quote", "callback_time": None,
                "qualification": [], "key_points": [], "objections": [], "objection_tags": []}

    monkeypatch.setattr(analysis_mod, "complete_json", fake_analysis)
    dialed: list[str] = []
    hung_up: list[str] = []

    async def fake_make_call(call_uuid, to, max_sec, caller_name=""):
        dialed.append(to)
        return f"vobiz-{call_uuid[:8]}"

    async def fake_hangup(pid):
        hung_up.append(pid)
        return True

    monkeypatch.setattr(vobiz, "make_call", fake_make_call)
    monkeypatch.setattr(vobiz, "hangup_call", fake_hangup)
    parked = _pause_running_campaigns()
    try:
        with TestClient(app, client=LOCAL) as c:
            c.llm, c.dialed, c.hung_up = llm, dialed, hung_up
            yield c
    finally:
        _end_my_calls()
        _resume(parked)
        inbound_router._clear_check()


@pytest.fixture()
def quiet_dialer(client, monkeypatch):
    """Stop the app's own dialer loop (these tests drive a Dialer by hand) and the watchdog sweep (it could
    close an old leftover call in the middle of a capacity count)."""
    client.portal.call(dialer.stop)

    async def no_sweep() -> None:
        return None

    monkeypatch.setattr(manager, "_sweep", no_sweep)
    yield


def _answer(c, pid: str, frm: str, **extra):
    data = {"CallUUID": pid, "From": frm, "To": "918000000000", "Direction": "inbound", "CallStatus": "ringing",
            **extra}
    return c.post(f"/vobiz/inbound/{vobiz.inbound_token()}/answer", data=data)


def _hangup(c, pid: str, **extra):
    data = {"CallUUID": pid, "Event": "Hangup", "CallStatus": "completed", "HangupCause": "NORMAL_CLEARING",
            "HangupCauseCode": "4000", **extra}
    return c.post(f"/vobiz/inbound/{vobiz.inbound_token()}/hangup", data=data)


def _rows(pid: str) -> list[Call]:
    with SessionLocal() as db:
        return list(db.scalars(select(Call).where(Call.provider_call_id == pid, Call.direction == "inbound")).all())


def _row(pid: str) -> Call | None:
    rows = _rows(pid)
    return rows[0] if rows else None


def _call(uuid: str) -> Call:
    with SessionLocal() as db:
        return db.scalar(select(Call).where(Call.uuid == uuid))


def _lead(phone: str, name: str = "", **kw) -> int:
    with SessionLocal() as db:
        lead = Lead(name=name, phone=phone, **kw)
        db.add(lead)
        db.commit()
        return lead.id


def _get_lead(lead_id: int | None = None, phone: str | None = None) -> Lead | None:
    with SessionLocal() as db:
        if lead_id is not None:
            return db.get(Lead, lead_id)
        return db.scalar(select(Lead).where(Lead.phone == phone))


def _add_call(**kw) -> str:
    now = utcnow()
    data = {"uuid": uuidlib.uuid4().hex, "channel": "phone", "direction": "outbound", "status": "completed",
            "analysis_status": "done", "started_at": now, "created_at": now}
    data.update(kw)
    with SessionLocal() as db:
        db.add(Call(**data))
        db.commit()
    return data["uuid"]


def _xml(r) -> ET.Element:
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/xml")
    return ET.fromstring(r.content)  # valid XML or this raises


def _stream_uuid(text: str) -> str:
    m = re.search(r"/vobiz/stream/([0-9a-f]+)/([0-9a-f]+)</Stream>", text)
    assert m, text
    return m.group(1)


def _wait(cond, timeout: float = 6.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def _start_stream(ws, pid: str) -> None:
    ws.send_text(json.dumps({"event": "start", "sequenceNumber": 0,
                             "start": {"callId": pid, "streamId": f"s-{pid}", "tracks": ["inbound"],
                                       "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000}}}))
    mu = pcm16_to_ulaw(tone(20, 8000, amp=50))
    for i in range(5):
        ws.send_text(json.dumps({"event": "media", "streamId": f"s-{pid}",
                                 "media": {"track": "inbound", "chunk": i, "payload": base64.b64encode(mu).decode()}}))


def _active_phone_calls() -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(Call).where(
            Call.channel == "phone", Call.status.in_(ACTIVE_CALL_STATUSES))) or 0


def _cfg() -> dict:
    return agent_config.load_agent_settings()


def _campaign(name: str, lead_ids: list[int], status: str = "running", **kw) -> int:
    with SessionLocal() as db:
        camp = Campaign(name=name, status=status, concurrency=kw.pop("concurrency", 5), max_attempts=2,
                        retry_delay_min=kw.pop("retry_delay_min", 15), respect_call_window=False, **kw)
        db.add(camp)
        db.flush()
        for lid in lead_ids:
            db.add(CampaignLead(campaign_id=camp.id, lead_id=lid, status="pending", attempts=0))
        db.commit()
        return camp.id


def _items(campaign_id: int) -> dict[int, CampaignLead]:
    with SessionLocal() as db:
        return {it.lead_id: it for it in db.scalars(select(CampaignLead).where(CampaignLead.campaign_id == campaign_id))}


def _set_campaign(campaign_id: int, status: str) -> None:
    with SessionLocal() as db:
        db.execute(update(Campaign).where(Campaign.id == campaign_id).values(status=status))
        db.commit()


# ---------------------------------------------------------------- caller resolution
def test_inbound_known_caller_gets_memory_and_named_greeting(client, monkeypatch):
    seen = []

    def fake_memory(lead_id, exclude_call_uuid=None, *, from_number=None, direction="outbound"):
        seen.append((lead_id, exclude_call_uuid, from_number, direction))
        return "MEMORY-MARKER: last time they wanted 5000 zipper pouches"

    monkeypatch.setattr(cm_mod, "caller_memory", fake_memory)
    lead_id = _lead("+919300000001", "Rahul Sharma", company="Sharma Snacks")
    _add_call(lead_id=lead_id, to_number="+919300000001", metrics={"user_words": 18, "agent_audio_ms": 6000},
              created_at=utcnow() - timedelta(days=2))
    r = _answer(client, "inb-known-1", "+919300000001")
    assert _xml(r).find("Stream") is not None
    uuid = _stream_uuid(r.text)
    row = _row("inb-known-1")
    assert (row.caller_type, row.lead_id, row.from_number, row.to_number) == ("returning", lead_id, "+919300000001",
                                                                               OUR_DID)
    assert row.status == "in_progress" and row.answered_at is None  # nothing heard yet: set at stream start
    expected = fill_template(_cfg()["inbound_greeting_known"], _cfg(), {"name": "Rahul Sharma"}).strip()
    with client.websocket_connect(f"/vobiz/stream/{uuid}/{vobiz.url_token(uuid)}") as ws:
        _start_stream(ws, "inb-known-1")
        assert json.loads(ws.receive_text())["event"] == "playAudio"
        session = manager.sessions[uuid]
        assert session.greeting_text == expected and "Rahul जी" in expected
        assert session.direction == "inbound" and session.purpose == "inbound"
        assert session.memory.startswith("MEMORY-MARKER")
        _wait(lambda: session.greeted and session.state == "listening")
        client.portal.call(FakeSTT.instances[-1].say, "हाँ, फिर से pouches का order देना है")
        _wait(lambda: len(client.llm.calls) >= 1)
        ws.send_text(json.dumps({"event": "stop"}))
    assert "MEMORY-MARKER" in client.llm.calls[0][1]["content"]  # the brain saw the memory
    assert seen == [(lead_id, uuid, "+919300000001", "inbound")]
    assert _row("inb-known-1").answered_at is not None

    # A lead we never actually spoke with: "known", first-caller greeting with their name
    lead2 = _lead("+919300000002", "Amit Jain")
    _add_call(lead_id=lead2, to_number="+919300000002", status="no_answer", analysis_status="skipped")
    r = _answer(client, "inb-known-2", "919300000002")
    uuid2 = _stream_uuid(r.text)
    assert _row("inb-known-2").caller_type == "known"
    assert manager.prepared[uuid2]["greeting_text"] == fill_template(_cfg()["inbound_greeting"], _cfg(),
                                                                     {"name": "Amit Jain"}).strip()
    assert manager.prepared[uuid2]["greeting_text"].startswith("नमस्ते Amit जी")


def test_inbound_new_caller_creates_lead(client):
    # '+' in a form body arrives as a space; CallerName "Ramesh+Patel" decodes to "Ramesh Patel"
    body = b"CallUUID=inb-new-1&From=+919300000003&To=918000000000&Direction=inbound&CallStatus=ringing" \
           b"&CallerName=Ramesh+Patel"
    r = client.post(f"/vobiz/inbound/{vobiz.inbound_token()}/answer", content=body,
                    headers={"content-type": "application/x-www-form-urlencoded"})
    uuid = _stream_uuid(r.text)
    lead = _get_lead(phone="+919300000003")
    assert lead is not None
    assert (lead.name, lead.source, lead.status, lead.interest, lead.attempts) == ("", "Inbound call", "new",
                                                                                   "unknown", 0)
    assert lead.last_called_at is not None and lead.extra == {"caller_id_name": "Ramesh Patel"}
    row = _row("inb-new-1")
    assert (row.caller_type, row.lead_id, row.from_number) == ("new", lead.id, "+919300000003")
    prep = manager.prepared[uuid]
    assert prep["greeting_text"] == inbound_audio.greeting_text(_cfg())  # no name yet: "नमस्ते जी! ..."
    assert prep["greeting"] is not None and prep["greeting_pcm"] is None  # clip not cached: synthesised now

    # With the first-caller clip cached, a new caller gets it with zero synthesis
    cfg = _cfg()
    client.portal.call(inbound_audio.build, inbound_audio.greeting_text(cfg), cfg)
    r = _answer(client, "inb-new-2", "919300000004", CallerName="919300000004")  # a number is not a name
    prep = manager.prepared[_stream_uuid(r.text)]
    assert prep["greeting_pcm"] and prep["greeting"] is None
    assert not (_get_lead(phone="+919300000004").extra or {}).get("caller_id_name")

    # Calls again after a call without a conversation: still "known", not "returning"
    _hangup(client, "inb-new-1")
    r = _answer(client, "inb-new-3", "919300000003")
    assert _row("inb-new-3").caller_type == "known" and _row("inb-new-3").lead_id == lead.id


def test_inbound_landline_and_number_formats(client):
    same = {"+919300000005": "+919300000005", "919300000005": "+919300000005", " 919300000005": "+919300000005",
            "09300000005": "+919300000005", "9300000005": "+919300000005", "00919300000005": "+919300000005",
            "+91 93000-00005": "+919300000005",
            # landlines (any first digit): Surat, Mumbai, Delhi
            "912612345678": "+912612345678", "+91 261 234 5678": "+912612345678", "02612345678": "+912612345678",
            "2612345678": "+912612345678", "+912240001234": "+912240001234", "911123456789": "+911123456789"}
    for raw, want in same.items():
        assert normalize_caller(raw) == want, raw
    for raw in ("", None, "anonymous", "Private", "UNKNOWN", "12345", "0", "+1", "0123456789", "910123456789"):
        assert normalize_caller(raw) is None, raw
    assert normalize_phone("912612345678") is None  # imports / dialing keep their mobile-only rule
    assert normalize_caller("+14155550101") is None and normalize_caller("+14155550101", True) == "+14155550101"
    assert caller_e164("14155550101") == "+14155550101"

    r = _answer(client, "inb-land-1", "912612345601")  # Surat landline, no '+'
    assert _stream_uuid(r.text)
    lead = _get_lead(phone="+912612345601")
    assert lead is not None and _row("inb-land-1").from_number == "+912612345601"
    assert _row("inb-land-1").caller_type == "new"
    _hangup(client, "inb-land-1")
    r = _answer(client, "inb-land-2", "02612345601")  # the same office line, written with the trunk 0
    assert _row("inb-land-2").lead_id == lead.id and _row("inb-land-2").caller_type == "known"


def test_inbound_anonymous_and_international(client, monkeypatch):
    victim = _lead("+919300000010", "Must Not Match")
    leads_before = _count_leads()
    for pid, frm in (("inb-anon-1", "anonymous"), ("inb-anon-2", ""), ("inb-anon-3", "12345")):
        r = _answer(client, pid, frm)
        uuid = _stream_uuid(r.text)
        row = _row(pid)
        assert (row.caller_type, row.lead_id) == ("anonymous", None), pid
        assert row.from_number == ("12345" if frm == "12345" else "")
        assert manager.prepared[uuid]["greeting_text"] == inbound_audio.greeting_text(_cfg())  # "नमस्ते जी!"
        assert manager.prepared[uuid]["memory"] is None
        _hangup(client, pid)
    # a foreign number ending in an Indian lead's 10 digits never matches that lead
    r = _answer(client, "inb-intl-1", "+1 9300000010")
    row = _row("inb-intl-1")
    assert (row.caller_type, row.lead_id, row.from_number) == ("international", None, "+19300000010")
    assert _stream_uuid(r.text)
    _answer(client, "inb-intl-2", "+14155550101")
    assert _row("inb-intl-2").caller_type == "international"
    assert _count_leads() == leads_before and _get_lead(victim).name == "Must Not Match"
    # with ALLOW_INTERNATIONAL on, a foreign caller becomes a lead like anyone else
    monkeypatch.setattr(settings, "allow_international", True)
    _answer(client, "inb-intl-3", "+14155550102")
    assert _row("inb-intl-3").caller_type == "new" and _get_lead(phone="+14155550102") is not None


def _count_leads() -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(Lead)) or 0


def test_inbound_own_number_is_anonymous(client):
    _answer(client, "inb-own-1", "918000000000")  # a softphone test from our own DID
    row = _row("inb-own-1")
    assert (row.caller_type, row.lead_id, row.from_number) == ("anonymous", None, OUR_DID)
    assert _get_lead(phone=OUR_DID) is None  # a campaign could otherwise make the AI call itself
    _answer(client, "inb-own-2", "919300000099", To="919300000099")  # From == the number that was called
    assert _row("inb-own-2").caller_type == "anonymous" and _get_lead(phone="+919300000099") is None


# ---------------------------------------------------------------- availability gate
def test_inbound_off_plays_message_and_logs_missed(client):
    assert client.put("/api/settings", json={"inbound_enabled": False}).status_code == 200
    r = _answer(client, "inb-off-1", "919300000011")
    root = _xml(r)
    assert [el.tag for el in root] == ["Speak", "Hangup"]
    speak = root[0]
    assert speak.get("voice") == "WOMAN" and speak.get("language") == "hi-IN"
    assert "पाउचवाले" in speak.text and "PouchWale" not in speak.text  # pronunciation fixes apply here too
    row = _row("inb-off-1")
    assert (row.status, row.end_reason, row.analysis_status, row.answered_at) == ("no_answer", "inbound_off",
                                                                                  "skipped", None)
    assert "switched off" in row.summary and row.caller_type == "new"
    lead = _get_lead(phone="+919300000011")
    assert row.lead_id == lead.id and lead.last_called_at is not None  # the team can call back
    # a Vobiz retry replays the same decision (by now maybe in our own, freshly cached voice), no second row
    tags = [el.tag for el in _xml(_answer(client, "inb-off-1", "919300000011"))]
    assert tags in (["Speak", "Hangup"], ["Play", "Hangup"])
    assert len(_rows("inb-off-1")) == 1

    # once our own voice is cached, callers hear it through <Play>
    cfg = _cfg()
    client.portal.call(inbound_audio.build, inbound_audio.message_text(cfg), cfg)
    root = _xml(_answer(client, "inb-off-2", "919300000012"))
    assert [el.tag for el in root] == ["Play", "Hangup"]
    url = root[0].text
    assert url.startswith(f"https://agent.example.com/vobiz/inbound/{vobiz.inbound_token()}/audio/")
    path = url.replace("https://agent.example.com", "")
    a = client.get(path)
    assert a.status_code == 200 and a.headers["content-type"] == "audio/wav"
    assert int(a.headers["content-length"]) == len(a.content)
    with wave.open(io.BytesIO(a.content)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (8000, 1, 2)
    key = path.rsplit("/", 1)[1][:-4]
    assert client.get(path.replace(key, "0" * 40)).status_code == 404
    assert client.get(path.replace(vobiz.inbound_token(), "wrong")).status_code == 403

    # switching inbound back on doesn't change the decision already given for that call
    client.put("/api/settings", json={"inbound_enabled": True})
    root = _xml(_answer(client, "inb-off-1", "919300000011"))
    assert [el.tag for el in root] == ["Play", "Hangup"]
    assert _stream_uuid(_answer(client, "inb-off-3", "919300000013").text)  # new calls are answered again


def test_inbound_off_reject_busy(client, monkeypatch):
    client.put("/api/settings", json={"inbound_enabled": False, "inbound_off_action": "reject"})
    r = _answer(client, "inb-rej-1", "919300000014")
    assert '<Response><Hangup reason="busy"/></Response>' in r.text  # first element: rejected, never billed
    row = _row("inb-rej-1")
    assert row.end_reason == "inbound_off" and row.metrics == {"inbound_reply": "reject"}
    # inbound on, but the AI can't work (key missing): turned away the same way, the summary says why
    client.put("/api/settings", json={"inbound_enabled": True})
    monkeypatch.setattr(settings, "openai_api_key", "")
    r = _answer(client, "inb-rej-2", "919300000015")
    assert '<Hangup reason="busy"/>' in r.text
    row = _row("inb-rej-2")
    assert row.end_reason == "inbound_off" and "OPENAI_API_KEY" in row.summary
    monkeypatch.setattr(settings, "openai_api_key", "test-openai")
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8100")  # stream would point at localhost
    _answer(client, "inb-rej-3", "919300000016")
    assert _row("inb-rej-3").end_reason == "inbound_off" and "PUBLIC_BASE_URL" in _row("inb-rej-3").summary


def test_inbound_lines_busy_and_inbound_reserve(client, quiet_dialer, monkeypatch):
    monkeypatch.setattr(dialer_mod, "GLOBAL_MAX_CONCURRENT", _active_phone_calls() + 2)
    caller_lead = _lead("+919300000017", "Busy Caller")
    other = _lead("+919300000018", "Someone Else")
    _add_call(lead_id=caller_lead, to_number="+919300000017", status="ringing", analysis_status="pending",
              provider_call_id="vobiz-ring-17")
    _add_call(lead_id=other, direction="inbound", from_number="+919300000018", status="in_progress",
              analysis_status="pending", provider_call_id="inb-busy-live")
    r = _answer(client, "inb-busy-1", "919300000019")
    assert [el.tag for el in _xml(r)] == ["Speak", "Hangup"]
    row = _row("inb-busy-1")
    assert (row.status, row.end_reason) == ("no_answer", "lines_busy") and "lines were busy" in row.summary
    # the caller's own ringing outbound call doesn't count against them: it is superseded instead
    r = _answer(client, "inb-busy-2", "919300000017")
    assert _stream_uuid(r.text) and _row("inb-busy-2").lead_id == caller_lead

    # campaigns leave INBOUND_RESERVE lines free for callers while inbound is on
    monkeypatch.setattr(dialer_mod, "GLOBAL_MAX_CONCURRENT", _active_phone_calls() + dialer_mod.INBOUND_RESERVE + 1)
    ids = [_lead(f"+9193000002{i:02d}", f"Reserve {i}") for i in range(3)]
    cid = _campaign("inb-reserve", ids)
    try:
        d = Dialer()
        planned = d._plan(True, dialer_mod.INBOUND_RESERVE)
        assert len(planned) == 1
        d._requeue(planned)
        assert all(it.status == "pending" and it.attempts == 0 for it in _items(cid).values())
        planned = d._plan(True, 0)  # inbound off: every line may dial
        assert len(planned) == 3
        d._requeue(planned)
    finally:
        _set_campaign(cid, "paused")


# ---------------------------------------------------------------- retries / races
def test_inbound_answer_retry_is_idempotent(client):
    first = _answer(client, "inb-retry-1", "919300000020")
    again = _answer(client, "inb-retry-1", "919300000020")
    assert first.text == again.text and "<Stream" in first.text
    assert len(_rows("inb-retry-1")) == 1
    _hangup(client, "inb-retry-1")
    late = _answer(client, "inb-retry-1", "919300000020")  # a very late retry of an ended call
    assert "<Hangup/>" in late.text and "<Stream" not in late.text
    assert len(_rows("inb-retry-1")) == 1


def test_inbound_concurrent_answers_one_row(client):
    data = {"CallUUID": "inb-conc-1", "From": "919300000021", "To": "918000000000", "Direction": "inbound"}

    async def both():
        return await asyncio.gather(manager.answer_inbound(dict(data)), manager.answer_inbound(dict(data)))

    x1, x2 = client.portal.call(both)
    assert x1 == x2 and "<Stream" in x1
    assert len(_rows("inb-conc-1")) == 1

    # two different first calls from one new number at the same moment: one lead, both calls stored
    async def two_calls():
        return await asyncio.gather(
            manager.answer_inbound({"CallUUID": "inb-conc-2", "From": "919300000022", "To": "918000000000"}),
            manager.answer_inbound({"CallUUID": "inb-conc-3", "From": "919300000022", "To": "918000000000"}))

    client.portal.call(two_calls)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Lead).where(Lead.phone == "+919300000022")) == 1
    a, b = _row("inb-conc-2"), _row("inb-conc-3")
    assert a and b and sorted([a.caller_type, b.caller_type]) == ["known", "new"]
    assert sorted([a.lead_id is None, b.lead_id is None]) == [False, True]  # one live call per lead


# ---------------------------------------------------------------- hangups
def test_inbound_hangup_webhook_ends_call(client):
    r = _answer(client, "inb-hang-1", "919300000023")
    uuid = _stream_uuid(r.text)
    with client.websocket_connect(f"/vobiz/stream/{uuid}/{vobiz.url_token(uuid)}") as ws:
        _start_stream(ws, "inb-hang-1")
        assert json.loads(ws.receive_text())["event"] == "playAudio"
        assert uuid in manager.sessions
        assert _hangup(client, "inb-hang-1").text == "ok"
        _wait(lambda: uuid not in manager.sessions)
    row = _call(uuid)
    assert row.status == "completed" and row.end_reason == "user_hangup" and row.answered_at is not None
    assert uuid in manager.hangup_received  # no REST hang-up needed for a line Vobiz says is down


def test_inbound_hangup_before_stream_is_missed(client):
    lead_id = _lead("+919300000024", "Kavita Nair")
    uuid = _stream_uuid(_answer(client, "inb-early-1", "919300000024").text)
    assert _hangup(client, "inb-early-1").status_code == 200
    row = _row("inb-early-1")
    assert (row.status, row.end_reason, row.analysis_status) == ("no_answer", "user_hangup", "skipped")
    assert row.summary == "Caller hung up before the agent answered" and row.answered_at is None
    assert _get_lead(lead_id).status == "new"  # a missed inbound call never marks the lead failed
    assert uuid not in manager.prepared
    with client.websocket_connect(f"/vobiz/stream/{uuid}/{vobiz.url_token(uuid)}") as ws:  # too late: refused
        _start_stream(ws, "inb-early-1")
        with pytest.raises(Exception):
            ws.receive_text()
    assert uuid not in manager.sessions

    # our side failed before the stream (end of XML / answer URL error): failed, call back
    _answer(client, "inb-early-2", "919300000025")
    _hangup(client, "inb-early-2", HangupCauseCode="4010", HangupCause="END_OF_XML_INSTRUCTIONS")
    row = _row("inb-early-2")
    assert (row.status, row.end_reason) == ("failed", "error")
    assert row.summary == "Call failed on our side — call back"
    assert _get_lead(phone="+919300000025").status != "failed"


def test_inbound_early_hangup_before_answer(client):
    # the caller hung up so fast that Vobiz's hangup beat its (retried) answer request
    assert _hangup(client, "inb-pre-1", CallStatus="cancel", HangupCause="ORIGINATOR_CANCEL",
                   HangupCauseCode="").status_code == 200
    assert _row("inb-pre-1") is None
    lead_id = _lead("+919300000026", "Neeraj Gupta")
    r = _answer(client, "inb-pre-1", "919300000026")
    assert "<Hangup/>" in r.text and "<Stream" not in r.text
    row = _row("inb-pre-1")
    assert (row.status, row.end_reason, row.lead_id) == ("no_answer", "user_hangup", lead_id)
    r = client.post("/api/calls", json={"lead_id": lead_id})  # the lead's slot was never held
    assert r.status_code == 201, r.text
    client.post(f"/api/calls/{r.json()['uuid']}/hangup")


def test_inbound_hangup_sent_to_answer_url(client):
    _answer(client, "inb-ansh-1", "919300000027")
    # an application without hangup_url posts the hangup to the answer URL
    r = client.post(f"/vobiz/inbound/{vobiz.inbound_token()}/answer",
                    data={"CallUUID": "inb-ansh-1", "Event": "Hangup", "CallStatus": "completed",
                          "HangupCause": "NORMAL_CLEARING"})
    assert r.status_code == 200 and r.text == "ok"
    assert (_row("inb-ansh-1").status, _row("inb-ansh-1").end_reason) == ("no_answer", "user_hangup")
    r = client.post(f"/vobiz/inbound/{vobiz.inbound_token()}/answer",
                    data={"CallUUID": "inb-ansh-2", "From": "919300000028", "CallStatus": "no-answer"})
    assert r.text == "ok" and _row("inb-ansh-2") is None  # a hangup never creates a call
    r = _answer(client, "inb-ansh-3", "919300000028", Direction="outbound")
    assert r.status_code == 400 and _row("inb-ansh-3") is None
    r = client.post(f"/vobiz/inbound/{vobiz.inbound_token()}/answer", data={"From": "919300000028"})
    assert r.status_code == 400


# ---------------------------------------------------------------- security
def test_inbound_bad_token_forbidden(client, monkeypatch):
    before = vobiz.inbound_rejections["count"]
    assert client.post("/vobiz/inbound/wrong/answer", data={"CallUUID": "inb-tok-1", "From": "919300000029"}
                       ).status_code == 403
    assert client.post("/vobiz/inbound/wrong/hangup", data={"CallUUID": "inb-tok-1"}).status_code == 403
    assert client.get("/vobiz/inbound/wrong/audio/" + "0" * 40 + ".wav").status_code == 403
    assert vobiz.inbound_rejections["count"] == before + 3 and vobiz.inbound_rejections["last_at"] is not None
    assert _row("inb-tok-1") is None
    s = client.get("/api/inbound/setup", params={"check": "false"}).json()
    assert s["rejected_requests"] >= 3 and s["last_rejected_at"]
    assert any("old inbound URL" in p for p in s["problems"])
    # the application may use GET (query string) as well
    r = client.get(f"/vobiz/inbound/{vobiz.inbound_token()}/answer",
                   params={"CallUUID": "inb-tok-2", "From": "919300000029", "To": "918000000000"})
    assert _stream_uuid(r.text)
    # rotating INBOUND_TOKEN_SALT revokes a leaked URL
    old = vobiz.inbound_token()
    monkeypatch.setattr(settings, "inbound_token_salt", "inbound-v2")
    assert vobiz.inbound_token() != old
    assert client.post(f"/vobiz/inbound/{old}/answer", data={"CallUUID": "inb-tok-3"}).status_code == 403
    # without VOBIZ_AUTH_TOKEN the secret would be guessable: everything is refused
    monkeypatch.setattr(settings, "vobiz_auth_token", "")
    assert _answer(client, "inb-tok-4", "919300000029").status_code == 403


def test_inbound_rate_limit(client):
    now = utcnow()
    for i in range(5):
        _add_call(direction="inbound", from_number="+919300000030", status="no_answer", end_reason="user_hangup",
                  analysis_status="skipped", provider_call_id=f"inb-rl-old-{i}", created_at=now - timedelta(minutes=i + 1))
    r = _answer(client, "inb-rl-6", "919300000030")
    assert '<Response><Hangup reason="busy"/></Response>' in r.text
    row = _row("inb-rl-6")
    assert row.end_reason == "lines_busy" and "more than 5 calls" in row.summary
    assert row.metrics == {"inbound_reply": "busy"}
    assert '<Hangup reason="busy"/>' in _answer(client, "inb-rl-6", "919300000030").text  # retry: same
    assert _stream_uuid(_answer(client, "inb-rl-other", "919300000031").text)  # other callers unaffected
    for i in range(5):  # calls older than 10 minutes don't count
        _add_call(direction="inbound", from_number="+919300000032", status="no_answer", end_reason="user_hangup",
                  analysis_status="skipped", provider_call_id=f"inb-rl-older-{i}",
                  created_at=now - timedelta(minutes=11 + i))
    assert _stream_uuid(_answer(client, "inb-rl-ok", "919300000032").text)


# ---------------------------------------------------------------- supersede our own dial
def test_inbound_supersedes_ringing_outbound(client):
    hooks: list[tuple[str, str]] = []

    async def spy(call_uuid, outcome):
        hooks.append((call_uuid, outcome))

    manager.done_hooks.append(spy)
    try:
        lead_id = _lead("+919300000033", "Sneha Iyer")
        out = client.post("/api/calls", json={"lead_id": lead_id}).json()
        tok = vobiz.url_token(out["uuid"])
        client.post(f"/vobiz/ring/{out['uuid']}/{tok}")
        assert _call(out["uuid"]).status == "ringing" and _get_lead(lead_id).attempts == 1
        assert out["uuid"] in manager.prepared
        r = _answer(client, "inb-sup-1", "919300000033")
        assert _stream_uuid(r.text)
        old = _call(out["uuid"])
        assert (old.status, old.end_reason, old.analysis_status) == ("canceled", "superseded", "skipped")
        assert old.ended_at is not None and out["uuid"] not in manager.prepared
        assert _get_lead(lead_id).attempts == 0  # refunded: we never reached them
        assert _row("inb-sup-1").lead_id == lead_id
        _wait(lambda: (out["uuid"], "superseded") in hooks and old.provider_call_id in client.hung_up)
        # the customer picking up our (cancelled) call a moment later gets no second AI
        late = client.post(f"/vobiz/answer/{out['uuid']}/{tok}", data={"CallUUID": old.provider_call_id})
        assert "<Hangup/>" in late.text and _call(out["uuid"]).status == "canceled"
    finally:
        manager.done_hooks.remove(spy)


def test_inbound_supersedes_queued_outbound_mid_dial(client, monkeypatch):
    release = asyncio.Event()

    async def slow_make_call(call_uuid, to, max_sec, caller_name=""):
        await release.wait()  # Vobiz is still placing our call...
        return "vobiz-slow-1"

    monkeypatch.setattr(vobiz, "make_call", slow_make_call)
    lead_id = _lead("+919300000034", "Mid Dial")
    fut = client.portal.start_task_soon(manager.start_phone_call, lead_id)
    _wait(lambda: _calls_for(lead_id, "queued"))
    out_uuid = _calls_for(lead_id, "queued")[0]
    r = _answer(client, "inb-mid-1", "919300000034")  # ...while the customer calls us
    assert _stream_uuid(r.text) and _row("inb-mid-1").lead_id == lead_id
    client.portal.call(release.set)
    with pytest.raises(CallError) as err:
        fut.result(timeout=5)
    assert err.value.status_code == 409 and err.value.reason == "superseded"
    row = _call(out_uuid)
    assert (row.status, row.end_reason, row.provider_call_id) == ("canceled", "superseded", "vobiz-slow-1")
    _wait(lambda: "vobiz-slow-1" in client.hung_up)  # the real call that was already ringing is dropped
    assert _row("inb-mid-1").status == "in_progress"  # no IntegrityError, the inbound call is untouched


def _calls_for(lead_id: int, status: str) -> list[str]:
    with SessionLocal() as db:
        return list(db.scalars(select(Call.uuid).where(Call.lead_id == lead_id, Call.status == status)).all())


def test_inbound_redial_while_active_relinks(client):
    lead_id = _lead("+919300000035", "Priya Menon")
    first = _stream_uuid(_answer(client, "inb-redial-1", "919300000035").text)
    second = _stream_uuid(_answer(client, "inb-redial-2", "919300000035").text)  # redial while still "live"
    row = _row("inb-redial-2")
    assert row.lead_id is None and row.caller_type == "known" and row.from_number == "+919300000035"
    prep = manager.prepared[second]
    assert prep["lead"]["id"] == lead_id and prep["greeting_text"].startswith("नमस्ते Priya जी")
    _hangup(client, "inb-redial-2")
    assert _row("inb-redial-2").lead_id == lead_id  # re-linked once it ended
    _hangup(client, "inb-redial-1")
    assert _row("inb-redial-1").lead_id == lead_id and first != second


# ---------------------------------------------------------------- full call over the media stream
def test_inbound_media_stream_full_call(client):
    lead_id = _lead("+919300000036", "Sunita Verma", company="Verma Foods")
    cid = _campaign("inb-courtesy-live", [lead_id], status="paused")  # paused: settled, never dialed here
    try:
        r = _answer(client, "inb-full-1", "919300000036")
        uuid = _stream_uuid(r.text)
        with client.websocket_connect(f"/vobiz/stream/{uuid}/{vobiz.url_token(uuid)}") as ws:
            _start_stream(ws, "inb-full-1")
            msg = json.loads(ws.receive_text())
            assert msg["event"] == "playAudio" and msg["streamId"] == "s-inb-full-1"
            assert _call(uuid).answered_at is not None and _call(uuid).status == "in_progress"
            session = manager.sessions[uuid]
            assert session.greeting_text.startswith("नमस्ते Sunita जी")
            _wait(lambda: session.greeted and session.state == "listening")
            client.portal.call(FakeSTT.instances[-1].say, "मुझे पाँच हज़ार pouches चाहिए")
            _wait(lambda: len(client.llm.calls) >= 1)
            _wait(lambda: session.state == "listening" and session.turns >= 1)
            ws.send_text(json.dumps({"event": "stop"}))
        _wait(lambda: _call(uuid).status == "completed")
        row = _call(uuid)
        assert row.end_reason == "user_hangup" and row.metrics["user_words"] > 0
        assert row.direction == "inbound" and row.lead_id == lead_id
        _wait(lambda: _get_lead(lead_id).status == "called")  # a real conversation
        _wait(lambda: _items(cid)[lead_id].status == "done")  # courtesy: no cold call right after
        assert _items(cid)[lead_id].last_call_uuid == uuid
        with SessionLocal() as db:
            roles = [t.role for t in db.scalars(select(TranscriptTurn).where(TranscriptTurn.call_id == row.id)
                                                .order_by(TranscriptTurn.id))]
        assert roles[:2] == ["agent", "user"]
    finally:
        _set_campaign(cid, "paused")


def test_inbound_handler_failure_still_xml(client, monkeypatch):
    original_gate = cm_mod.CallManager._inbound_gate

    def boom(*a, **kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(cm_mod.CallManager, "_inbound_gate", boom)
    root = _xml(_answer(client, "inb-fail-1", "919300000037"))
    assert [el.tag for el in root] == ["Speak", "Hangup"]
    row = _row("inb-fail-1")
    assert (row.status, row.end_reason, row.analysis_status) == ("failed", "error", "skipped")
    assert row.summary == "Call failed on our side — call back"
    assert row.lead_id == _get_lead(phone="+919300000037").id  # the team can call back
    monkeypatch.setattr(cm_mod.CallManager, "_inbound_gate", original_gate)

    def broken_memory(*a, **kw):
        raise ValueError("memory bug")

    monkeypatch.setattr(cm_mod, "caller_memory", broken_memory)
    r = _answer(client, "inb-fail-2", "919300000038")
    uuid = _stream_uuid(r.text)  # memory runs in the background: its failure never blocks the answer
    client.portal.call(manager._drop_prepared, uuid)  # defensive path: the hand-off went missing
    with client.websocket_connect(f"/vobiz/stream/{uuid}/{vobiz.url_token(uuid)}") as ws:
        _start_stream(ws, "inb-fail-2")
        assert json.loads(ws.receive_text())["event"] == "playAudio"
        session = manager.sessions[uuid]
        assert session.direction == "inbound" and session.purpose == "inbound" and session.memory == ""
        assert session.greeting_text == inbound_audio.greeting_text(_cfg())
        ws.send_text(json.dumps({"event": "stop"}))


# ---------------------------------------------------------------- offline back-fill
def test_inbound_offline_backfill(client, monkeypatch):
    fake = FakeVobizAPI(OUR_DID)
    fake.install(monkeypatch, vobiz)
    urls = vobiz.inbound_urls()
    ours = fake.add_app(vobiz.INBOUND_APP_NAME, urls["answer"], urls["hangup"])
    fake.numbers[OUR_DID]["application_id"] = ours
    inbound_sync.save_meta(_inbound_app_id=ours)
    now = utcnow()
    real_anchor = inbound_sync._anchor
    monkeypatch.setattr(inbound_sync, "_anchor", lambda meta: now - timedelta(hours=1))
    _add_call(direction="inbound", from_number="+919300000302", status="completed", provider_call_id="inb-bf-live",
              created_at=now - timedelta(minutes=40))

    def rec(uid, caller, start_min, end_min=None, dest=OUR_DID):
        end = start_min - 0.5 if end_min is None else end_min
        return {"uuid": uid, "call_direction": "inbound", "caller_id_number": caller, "caller_id_name": "",
                "destination_number": dest, "start_time": iso(now - timedelta(minutes=start_min)),
                "end_time": iso(now - timedelta(minutes=end))}

    fake.cdrs = [rec("cdr-new-1", "+919300000301", 30),
                 rec("inb-bf-live", "+919300000302", 40),        # the call we already have
                 rec("cdr-twin-1", "+919300000302", 39.5),       # the same call under another id
                 rec("cdr-recent", "+919300000303", 1.5, 0.5),   # just ended: left to the live webhooks
                 rec("cdr-other-did", "+919300000304", 20, dest="+918000000099"),
                 rec("cdr-too-old", "+919300000305", 90),        # before we were connected
                 rec("cdr-hidden", "anonymous", 25)]
    assert client.portal.call(inbound_sync.sync_once) == 2
    row = _row("cdr-new-1")
    assert (row.status, row.end_reason, row.analysis_status, row.answered_at) == ("no_answer", "offline", "skipped",
                                                                                  None)
    assert row.summary == "Missed while the agent was offline — call back"
    assert row.caller_type == "new" and row.from_number == "+919300000301" and row.to_number == OUR_DID
    assert abs((row.created_at - (now - timedelta(minutes=30))).total_seconds()) < 1  # counted on its own day
    lead = _get_lead(phone="+919300000301")
    assert row.lead_id == lead.id and lead.source == "Inbound call"
    hidden = _row("cdr-hidden")
    assert hidden.caller_type == "anonymous" and hidden.lead_id is None and hidden.from_number == ""
    for uid in ("cdr-twin-1", "cdr-recent", "cdr-other-did", "cdr-too-old"):
        assert _row(uid) is None, uid
    assert len(_rows("inb-bf-live")) == 1
    assert client.portal.call(inbound_sync.sync_once) == 0  # idempotent
    query = next(p for m, p in fake.requests if p.startswith(f"{FakeVobizAPI.PREFIX}/cdr?"))
    assert "call_direction=inbound" in query and "to_number=%2B918000000000" in query

    fake.cdrs.append(rec("cdr-new-2", "+919300000306", 10))
    fake.numbers[OUR_DID]["application_id"] = None  # routed elsewhere: those calls aren't ours
    assert client.portal.call(inbound_sync.sync_once) == 0 and _row("cdr-new-2") is None
    fake.numbers[OUR_DID]["application_id"] = ours
    fake.down = True  # Vobiz unreachable: logged, never raised
    assert client.portal.call(inbound_sync.sync_once) == 0
    fake.down = False
    assert client.portal.call(inbound_sync.sync_once) == 1 and _row("cdr-new-2") is not None

    # the anchor: the moment we connected wins over older calls (earlier calls went to another app)
    later = utcnow() + timedelta(minutes=5)
    assert real_anchor({"_inbound_connected_at": iso(later)}) == later.replace(microsecond=later.microsecond // 1000 * 1000)
    assert real_anchor({}) is not None


# ---------------------------------------------------------------- one-click setup
def test_inbound_setup_connect_force_disconnect(client, monkeypatch):
    fake = FakeVobizAPI(OUR_DID)
    fake.install(monkeypatch, vobiz)
    ivr = fake.add_app("IVR-Main")
    fake.numbers[OUR_DID]["application_id"] = ivr
    s = client.get("/api/inbound/setup").json()
    assert s["checked"] and s["number"] == OUR_DID and s["app_id"] is None and s["vobiz_ready"] and s["ai_ready"]
    assert (s["number_app_id"], s["number_app_name"], s["number_linked"]) == (ivr, "IVR-Main", False)
    assert s["concurrency_max"] == 20 and s["ready"] is False and s["number_voice_enabled"] is True
    assert s["answer_url"] == f"https://agent.example.com/vobiz/inbound/{vobiz.inbound_token()}/answer"
    assert any("IVR-Main" in p for p in s["problems"])
    assert s["problems"][-1].startswith("The agent answers only while this computer")

    r = client.post("/api/inbound/setup")
    assert r.status_code == 409
    assert "IVR-Main" in r.json()["detail"] and OUR_DID in r.json()["detail"]
    assert fake.numbers[OUR_DID]["application_id"] == ivr and len(fake.apps) == 1  # nothing changed

    r = client.post("/api/inbound/setup", params={"force": "true"})
    assert r.status_code == 200, r.text
    s = r.json()
    app_ = fake.apps[s["app_id"]]
    assert app_["app_name"] == "GP3-Voice-Agent-Inbound" and app_["application_type"] == "XML"
    assert (app_["answer_url"], app_["hangup_url"]) == (s["answer_url"], s["hangup_url"])
    assert app_["answer_method"] == app_["hangup_method"] == "POST"
    assert fake.numbers[OUR_DID]["application_id"] == s["app_id"]
    assert ("POST", f"{FakeVobizAPI.PREFIX}/numbers/%2B918000000000/application") in fake.requests
    assert s["number_linked"] and s["app_urls_match"] and s["previous_app_id"] == ivr and s["ready"]
    assert not any("IVR-Main" in p for p in s["problems"])
    assert inbound_sync.load_meta()["_inbound_app_id"] == s["app_id"]

    n = len(fake.requests)  # cached for a minute; ?check=false never queries Vobiz
    assert client.get("/api/inbound/setup").json()["ready"] and len(fake.requests) == n
    assert client.get("/api/inbound/setup", params={"check": "false"}).json()["number_linked"]
    assert len(fake.requests) == n

    app_["answer_url"] = "https://old-tunnel.example.com/vobiz/inbound/x/answer"  # the tunnel URL changed
    inbound_router._clear_check()
    s = client.get("/api/inbound/setup").json()
    assert s["app_urls_match"] is False and not s["ready"] and any("old address" in p for p in s["problems"])
    s = client.post("/api/inbound/setup").json()  # ours already: no 409, URLs updated in place
    assert s["app_urls_match"] and s["ready"] and len(fake.apps) == 2

    s = client.delete("/api/inbound/setup").json()  # Disconnect restores the previous routing
    assert fake.numbers[OUR_DID]["application_id"] == ivr
    assert s["previous_app_id"] is None and s["number_linked"] is False
    client.post("/api/inbound/setup", params={"force": "true"})
    del fake.apps[ivr]  # the previous application is gone: Disconnect detaches
    client.delete("/api/inbound/setup")
    assert fake.numbers[OUR_DID]["application_id"] is None

    fake.numbers[OUR_DID]["trunk_group_id"] = "trunk-1"  # a SIP trunk binding asks first too
    r = client.post("/api/inbound/setup")
    assert r.status_code == 409 and "SIP trunk" in r.json()["detail"]
    fake.numbers[OUR_DID]["trunk_group_id"] = None

    fake.down = True  # Vobiz errors are problems, never a 500
    inbound_router._clear_check()
    r = client.get("/api/inbound/setup")
    assert r.status_code == 200 and r.json()["checked"] is False
    assert any("Couldn't check Vobiz" in p for p in r.json()["problems"])
    assert client.post("/api/inbound/setup").status_code == 502
    fake.down = False
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8100")
    assert client.post("/api/inbound/setup").status_code == 400


# ---------------------------------------------------------------- outbound switch + dialer
def test_outbound_switch_blocks_calls_and_dialer(client, quiet_dialer):
    lead_id = _lead("+919300000040", "Switch Off")
    assert client.put("/api/settings", json={"outbound_enabled": False}).status_code == 200
    r = client.post("/api/calls", json={"lead_id": lead_id})
    assert r.status_code == 400 and r.json()["detail"] == "Outbound calling is turned off in Settings."
    cid = _campaign("inb-switch", [lead_id], status="draft")
    try:
        r = client.post(f"/api/campaigns/{cid}/start")
        assert r.status_code == 400 and r.json()["detail"] == "Outbound calling is turned off in Settings."
        _set_campaign(cid, "running")
        d = Dialer()
        client.portal.call(d._tick)
        assert client.dialed == [] and _items(cid)[lead_id].status == "pending"
        assert _items(cid)[lead_id].attempts == 0
        with client.websocket_connect("/ws/browser-call") as ws:  # browser tests are not outbound calls
            ws.send_text(json.dumps({"type": "start", "name": "Tester"}))
            assert json.loads(ws.receive_text())["type"] == "ready"
            ws.send_text(json.dumps({"type": "hangup"}))
        client.put("/api/settings", json={"outbound_enabled": True})  # resumes by itself
        client.portal.call(d._tick)
        assert client.dialed == ["+919300000040"] and _items(cid)[lead_id].attempts == 1
    finally:
        _set_campaign(cid, "paused")


def test_outbound_switched_off_mid_tick_keeps_campaign_running(client, quiet_dialer, monkeypatch):
    a, b = _lead("+919300000041", "Mid A"), _lead("+919300000042", "Mid B")
    cid = _campaign("inb-midtick", [a, b], concurrency=2)
    try:
        dialed = []

        async def flipping_make_call(call_uuid, to, max_sec, caller_name=""):
            dialed.append(to)
            agent_config.save_agent_settings({"outbound_enabled": False})  # the owner switches it off now
            return f"vobiz-{call_uuid[:8]}"

        monkeypatch.setattr(vobiz, "make_call", flipping_make_call)
        d = Dialer()
        client.portal.call(d._tick)
        items = _items(cid)
        assert len(dialed) == 1
        rest = items[b] if dialed[0] == "+919300000041" else items[a]
        assert (rest.status, rest.attempts, rest.last_call_uuid) == ("pending", 0, None)
        with SessionLocal() as db:
            assert db.get(Campaign, cid).status == "running"  # not paused: resumes when switched back on

        # a planned dial that hits the switch inside start_phone_call: put back, campaign not paused
        for u in _calls_for(a, "initiated") + _calls_for(b, "initiated"):
            client.post(f"/api/calls/{u}/hangup")
        with SessionLocal() as db:
            db.execute(update(CampaignLead).where(CampaignLead.campaign_id == cid)
                       .values(status="pending", attempts=0, next_attempt_at=None, last_call_uuid=None))
            db.commit()
        stale = {**_cfg(), "outbound_enabled": True}
        monkeypatch.setattr(dialer_mod, "load_agent_settings", lambda: dict(stale))  # the tick's view is stale
        client.portal.call(d._tick)
        assert all((it.status, it.attempts) == ("pending", 0) for it in _items(cid).values())
        with SessionLocal() as db:
            assert db.get(Campaign, cid).status == "running"
    finally:
        _set_campaign(cid, "paused")


def test_dialer_skips_lead_on_inbound_call(client, quiet_dialer, monkeypatch):
    monkeypatch.setattr(dialer_mod, "GLOBAL_MAX_CONCURRENT", _active_phone_calls() + 20)
    now = utcnow()
    on_call = _lead("+919300000043", "On Inbound Call")
    just_called = _lead("+919300000044", "Called 10 Min Ago")
    long_ago = _lead("+919300000045", "Called 40 Min Ago")
    unlinked = _lead("+919300000046", "Unlinked Redial")
    free = _lead("+919300000047", "Free Lead")
    _add_call(lead_id=on_call, direction="inbound", from_number="+919300000043", status="in_progress",
              analysis_status="pending", provider_call_id="inb-plan-1")
    _add_call(lead_id=just_called, direction="inbound", from_number="+919300000044", status="no_answer",
              provider_call_id="inb-plan-2", created_at=now - timedelta(minutes=10))
    _add_call(lead_id=long_ago, direction="inbound", from_number="+919300000045", status="completed",
              provider_call_id="inb-plan-3", created_at=now - timedelta(minutes=40))
    _add_call(lead_id=None, direction="inbound", from_number="+919300000046", status="in_progress",
              caller_type="known", analysis_status="pending", provider_call_id="inb-plan-4",
              created_at=now - timedelta(minutes=45))
    cid = _campaign("inb-plan", [on_call, just_called, long_ago, unlinked, free], retry_delay_min=20)
    try:
        d = Dialer()
        planned = d._plan(True, 0)
        assert {p[2] for p in planned} == {long_ago, free}
        items = _items(cid)
        for lid in (on_call, just_called, unlinked):
            assert (items[lid].status, items[lid].attempts) == ("pending", 0)
        d._requeue(planned)

        async def busy(*a, **kw):  # the lead got on a call between planning and dialing
            raise CallError("This lead already has a call in progress", 409, reason="lead_busy")

        monkeypatch.setattr(manager, "start_phone_call", busy)
        client.portal.call(d._tick)
        for lid in (long_ago, free):
            it = _items(cid)[lid]
            assert (it.status, it.attempts) == ("pending", 0)  # retried later, never skipped
            assert it.next_attempt_at is not None and it.next_attempt_at > utcnow() + timedelta(minutes=19)

        async def dnc(*a, **kw):
            raise CallError("Lead is marked Do-Not-Call", 409, reason="dnc")

        with SessionLocal() as db:
            db.execute(update(CampaignLead).where(CampaignLead.campaign_id == cid).values(next_attempt_at=None))
            db.commit()
        monkeypatch.setattr(manager, "start_phone_call", dnc)
        client.portal.call(d._tick)
        assert _items(cid)[free].status == "skipped"
    finally:
        _set_campaign(cid, "paused")


def test_superseded_never_pauses_campaign(client, quiet_dialer):
    ids = [_lead(f"+9193000005{i}0", f"Supersede {i}") for i in range(3)]
    cid = _campaign("inb-supersede", ids, retry_delay_min=5)
    try:
        uuids = []
        with SessionLocal() as db:
            for lid in ids:
                u = uuidlib.uuid4().hex
                db.add(Call(uuid=u, lead_id=lid, campaign_id=cid, channel="phone", status="canceled",
                            end_reason="superseded", started_at=utcnow()))
                it = db.scalar(select(CampaignLead).where(CampaignLead.campaign_id == cid, CampaignLead.lead_id == lid))
                it.status, it.attempts, it.last_call_uuid = "dialing", 1, u
                uuids.append(u)
            db.commit()
        d = Dialer()
        for u in uuids:
            client.portal.call(d.on_call_done, u, "superseded")
        with SessionLocal() as db:
            assert db.get(Campaign, cid).status == "running"
        assert d._provider_errors[cid] == 0
        for it in _items(cid).values():
            assert (it.status, it.attempts) == ("pending", 0)
            assert it.next_attempt_at > utcnow() + timedelta(minutes=4)
    finally:
        _set_campaign(cid, "paused")


def test_inbound_connected_settles_campaign_items(client, quiet_dialer):
    lead_id = _lead("+919300000060", "Courtesy Lead")
    camps = {s: _campaign(f"inb-courtesy-{s}", [lead_id], status=s) for s in ("running", "paused", "draft", "completed")}
    busy_camp = _campaign("inb-courtesy-dialing", [lead_id])
    try:
        with SessionLocal() as db:
            it = db.scalar(select(CampaignLead).where(CampaignLead.campaign_id == busy_camp))
            it.status, it.last_call_uuid = "dialing", "some-outbound-call"
            db.commit()
        silent = _add_call(lead_id=lead_id, direction="inbound", from_number="+919300000060", status="completed",
                           metrics={"user_words": 0, "agent_audio_ms": 3000}, provider_call_id="inb-court-0")
        d = Dialer()
        client.portal.call(d.on_call_done, silent, "connected")
        assert _items(camps["running"])[lead_id].status == "pending"  # no conversation: nothing settled
        talked = _add_call(lead_id=lead_id, direction="inbound", from_number="+919300000060", status="completed",
                           metrics={"user_words": 25, "agent_audio_ms": 9000}, provider_call_id="inb-court-1")
        client.portal.call(d.on_call_done, talked, "connected")
        for s in ("running", "paused"):
            it = _items(camps[s])[lead_id]
            assert (it.status, it.last_call_uuid) == ("done", talked), s
        assert _items(camps["draft"])[lead_id].status == "pending"
        assert _items(camps["completed"])[lead_id].status == "pending"
        assert _items(busy_camp)[lead_id].status == "dialing"  # a live dial settles through its own call
    finally:
        for c in (*camps.values(), busy_camp):
            _set_campaign(c, "paused")


def test_callback_mode_greeting(client):
    lead_id = _lead("+919300000061", "Pooja Shah")
    _add_call(lead_id=lead_id, direction="inbound", from_number="+919300000061", status="no_answer",
              end_reason="inbound_off", analysis_status="skipped", provider_call_id="inb-cb-old",
              created_at=utcnow() - timedelta(days=1))
    out = client.post("/api/calls", json={"lead_id": lead_id}).json()
    prep = manager.prepared[out["uuid"]]
    lead_d = lead_out(_get_lead(lead_id))
    assert (prep["purpose"], prep["direction"], out["direction"]) == ("callback", "outbound", "outbound")
    assert prep["greeting_text"] == fill_template(_cfg()["callback_greeting"], _cfg(), lead_d).strip()
    assert prep["memory"] is not None  # outbound calls get the caller memory too
    client.post(f"/api/calls/{out['uuid']}/hangup")
    # once we have spoken with them since, the next call is a normal one
    _add_call(lead_id=lead_id, to_number="+919300000061", metrics={"user_words": 9})
    out = client.post("/api/calls", json={"lead_id": lead_id}).json()
    prep = manager.prepared[out["uuid"]]
    assert prep["purpose"] == "outbound"
    assert prep["greeting_text"] == fill_template(_cfg()["opening_line"], _cfg(), lead_d).strip()
    client.post(f"/api/calls/{out['uuid']}/hangup")
    # a missed call more than 7 days old doesn't make it a call back
    old = _lead("+919300000062", "Old Miss")
    _add_call(lead_id=old, direction="inbound", from_number="+919300000062", status="no_answer",
              provider_call_id="inb-cb-older", created_at=utcnow() - timedelta(days=8))
    out = client.post("/api/calls", json={"lead_id": old}).json()
    assert manager.prepared[out["uuid"]]["purpose"] == "outbound"
    client.post(f"/api/calls/{out['uuid']}/hangup")


def test_browser_inbound_rehearsal(client):
    lead_id = _lead("+919300000063", "Deepak Rao", status="queued")
    with client.websocket_connect("/ws/browser-call") as ws:
        ws.send_text(json.dumps({"type": "start", "lead_id": lead_id, "direction": "inbound"}))
        ready = json.loads(ws.receive_text())
        assert ready["type"] == "ready"
        session = manager.sessions[ready["uuid"]]
        assert (session.direction, session.purpose) == ("inbound", "inbound")
        assert session.greeting_text == fill_template(_cfg()["inbound_greeting"], _cfg(),
                                                      {"name": "Deepak Rao"}).strip()
        ws.send_text(json.dumps({"type": "hangup"}))
        for _ in range(500):
            m = ws.receive()
            if m.get("type") == "websocket.close" or (m.get("text") and json.loads(m["text"]).get("type") == "ended"):
                break
    row = _call(ready["uuid"])
    assert (row.channel, row.direction, row.caller_type, row.lead_id) == ("browser", "inbound", "known", lead_id)
    assert row.from_number == "+919300000063"
    _wait(lambda: _call(ready["uuid"]).status == "completed")
    assert _get_lead(lead_id).status == "queued"  # browser tests never touch the lead
    with client.websocket_connect("/ws/browser-call") as ws:  # a caller with no lead
        ws.send_text(json.dumps({"type": "start", "direction": "inbound"}))
        ready = json.loads(ws.receive_text())
        assert manager.sessions[ready["uuid"]].greeting_text == inbound_audio.greeting_text(_cfg())
        ws.send_text(json.dumps({"type": "hangup"}))


# ---------------------------------------------------------------- restart + clip cache
def test_recover_on_startup_inbound_rules():
    lead_id = _lead("+919300000064", "Restart Lead")
    never = _add_call(lead_id=lead_id, direction="inbound", from_number="+919300000064", status="in_progress",
                      analysis_status="pending", provider_call_id="inb-rst-1", caller_type="known")
    silent = _add_call(direction="inbound", from_number="+919300000064", status="in_progress", lead_id=None,
                       caller_type="known", analysis_status="pending", provider_call_id="inb-rst-2",
                       answered_at=utcnow())
    spoke = _add_call(direction="inbound", from_number="+919300000065", status="in_progress", caller_type="anonymous",
                      analysis_status="pending", provider_call_id="inb-rst-3", answered_at=utcnow())
    with SessionLocal() as db:
        db.add(TranscriptTurn(call_id=_call(spoke).id, role="user", text="हाँ जी, pouches चाहिए"))
        db.commit()
    _, to_analyze = manager.recover_on_startup()
    a, b, c = _call(never), _call(silent), _call(spoke)
    assert (a.status, a.end_reason, a.analysis_status) == ("failed", "error", "skipped")
    assert a.summary == "Call dropped (server restarted) — call back"
    assert (b.status, b.analysis_status, b.summary) == ("completed", "skipped", a.summary)
    assert b.lead_id == lead_id  # the unlinked redial gets its lead back
    assert c.status == "completed" and c.analysis_status == "pending" and spoke in to_analyze
    assert _get_lead(lead_id).status != "failed"


def test_inbound_audio_prewarm(monkeypatch):
    calls: list[tuple[str, int]] = []

    async def tts(text, cfg, rate):
        calls.append((text, rate))
        return tone(100, rate)

    monkeypatch.setattr(inbound_audio, "tts_rest", tts)
    inbound_audio._clips.clear()
    inbound_audio._building.clear()
    cfg = _cfg()
    cfg2 = {**cfg, "inbound_greeting": "नमस्ते जी! {company_name} में आपका स्वागत है, बताइए?"}

    async def scenario():
        inbound_audio.schedule_prewarm(cfg)  # at startup (on the loop)
        await asyncio.gather(*list(inbound_audio._building.values()))
        await asyncio.to_thread(inbound_audio.schedule_prewarm, cfg2)  # a settings save (worker thread)
        await asyncio.sleep(0.05)
        await asyncio.gather(*list(inbound_audio._building.values()))
        inbound_audio.schedule_prewarm(cfg2)  # nothing changed: nothing to synthesise
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    texts = [t for t, _ in calls]
    assert texts.count(inbound_audio.greeting_text(cfg)) == 1 and texts.count(inbound_audio.message_text(cfg)) == 1
    assert texts.count(inbound_audio.greeting_text(cfg2)) == 1 and len(calls) == 3
    assert all(rate == 8000 for _, rate in calls)
    assert inbound_audio.cached_greeting(cfg2)[1] is not None
    monkeypatch.setattr(settings, "sarvam_api_key", "")  # no key: a quiet no-op
    calls.clear()

    async def no_key():
        inbound_audio.schedule_prewarm({**cfg, "inbound_greeting": "नमस्ते! कुछ नया?"})
        await asyncio.sleep(0.05)

    asyncio.run(no_key())
    assert calls == []


# ---------------------------------------------------------------- review regressions (one per finding)
def test_backfill_not_hidden_by_a_later_live_call(client, monkeypatch):
    """A live call stored before the next pass must not hide earlier missed calls (high-water mark)."""
    fake = FakeVobizAPI(OUR_DID)
    fake.install(monkeypatch, vobiz)
    urls = vobiz.inbound_urls()
    ours = fake.add_app(vobiz.INBOUND_APP_NAME, urls["answer"], urls["hangup"])
    fake.numbers[OUR_DID]["application_id"] = ours
    now = utcnow()
    inbound_sync.save_meta(_inbound_app_id=ours, _inbound_connected_at=iso(now - timedelta(hours=1)),
                           _inbound_synced_until=None)
    _add_call(direction="inbound", from_number="+919300000701", status="completed", provider_call_id="rv-live",
              created_at=now - timedelta(minutes=1))  # a live call AFTER the missed one
    fake.cdrs = [{"uuid": "rv-missed", "call_direction": "inbound", "caller_id_number": "+919300000702",
                  "caller_id_name": "", "destination_number": OUR_DID,
                  "start_time": iso(now - timedelta(minutes=20)), "end_time": iso(now - timedelta(minutes=19))}]
    assert client.portal.call(inbound_sync.sync_once) == 1 and _row("rv-missed") is not None
    assert inbound_sync.load_meta()["_inbound_synced_until"]  # next pass starts where this one ended
    assert client.portal.call(inbound_sync.sync_once) == 0


def test_call_now_refuses_lead_on_an_unlinked_inbound_call(client):
    lead_id = _lead("+919300000711", "Redialer")
    _add_call(direction="inbound", lead_id=None, from_number="+919300000711", status="in_progress",
              provider_call_id="rv-unlinked", analysis_status="pending")
    try:
        r = client.post("/api/calls", json={"lead_id": lead_id})
        assert r.status_code == 409 and "call in progress" in r.json()["detail"]
    finally:
        with SessionLocal() as db:
            c = db.scalar(select(Call).where(Call.provider_call_id == "rv-unlinked"))
            c.status = "completed"
            db.commit()


def test_heal_stuck_refunds_a_superseded_dial(client):
    """The dialer's heal pass may reach a superseded item before the refund hook: never count it."""
    lead_id = _lead("+919300000721", "Heal race")
    cid = _campaign("rv-heal", [lead_id], status="paused", retry_delay_min=5)
    u = uuidlib.uuid4().hex
    with SessionLocal() as db:
        db.add(Call(uuid=u, lead_id=lead_id, campaign_id=cid, channel="phone", status="canceled",
                    end_reason="superseded", started_at=utcnow()))
        it = db.scalar(select(CampaignLead).where(CampaignLead.campaign_id == cid))
        it.status, it.attempts, it.last_call_uuid = "dialing", 1, u
        db.commit()
        Dialer._heal_stuck(db, utcnow())
        db.commit()
    it = _items(cid)[lead_id]
    assert (it.status, it.attempts) == ("pending", 0)


def test_rate_limit_ignores_calls_missed_while_offline(client):
    now = utcnow()
    for i in range(6):  # a customer who kept trying during an outage (back-filled rows)
        _add_call(direction="inbound", from_number="+919300000731", status="no_answer", end_reason="offline",
                  analysis_status="skipped", provider_call_id=f"rv-off-{i}", created_at=now - timedelta(minutes=i + 1))
    assert _stream_uuid(_answer(client, "rv-off-real", "919300000731").text)  # answered, not a busy tone
    _end_my_calls()
