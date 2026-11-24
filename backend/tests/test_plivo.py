"""Plivo as the phone provider (TELEPHONY_PROVIDER=plivo): REST requests, webhook / stream URLs under /plivo,
a full outbound call over the shared media stream, hangup codes, inbound answer, the account API used by the
inbound setup and the call-record back-fill. Numbers used here: +91946xxxxxxx; Plivo CallUUIDs "pl-".
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import xml.etree.ElementTree as ET

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import or_, select, update

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.main import app
from app.models import ACTIVE_CALL_STATUSES, Call
from app.routers import inbound as inbound_router
from app.services import analysis as analysis_mod
from app.services import call_manager as cm_mod
from app.services import inbound_audio, plivo, telephony, vobiz
from app.services import session as session_mod
from app.services.audio import pcm16_to_ulaw
from app.services.call_manager import manager

from .fakes import FakeSTT, FakeTTSStream, ScriptedLLM, fake_tts_rest, tone

settings = get_settings()
LOCAL = ("127.0.0.1", 50000)
PLIVO_PREFIX = "https://api.plivo.com/v1/Account/MAPLIVOTEST"


def _end_my_calls() -> None:
    mine = or_(Call.provider_call_id.like("pl-%"), Call.to_number.like("+91946%"), Call.from_number.like("+91946%"))
    with SessionLocal() as db:
        uuids = list(db.scalars(select(Call.uuid).where(Call.status.in_(ACTIVE_CALL_STATUSES), mine)).all())
        if uuids:
            db.execute(update(Call).where(Call.uuid.in_(uuids)).values(status="failed", end_reason="error",
                                                                       ended_at=utcnow()))
            db.commit()
    for u in uuids:
        manager.prepared.pop(u, None)


@pytest.fixture()
def plivo_mode(monkeypatch):
    monkeypatch.setattr(settings, "telephony_provider", "plivo")
    yield


@pytest.fixture()
def client(plivo_mode, monkeypatch):
    FakeSTT.instances.clear()
    monkeypatch.setattr(session_mod, "RealtimeSTT", FakeSTT)
    monkeypatch.setattr(session_mod, "TTSStream", FakeTTSStream)
    monkeypatch.setattr(session_mod, "tts_rest", fake_tts_rest)
    monkeypatch.setattr(session_mod, "stream_reply", ScriptedLLM(["जी बिल्कुल।"]))
    monkeypatch.setattr(cm_mod, "tts_rest", fake_tts_rest)
    monkeypatch.setattr(inbound_audio, "tts_rest", fake_tts_rest)
    monkeypatch.setattr(inbound_audio, "schedule_prewarm", lambda cfg: None)
    inbound_router._clear_check()

    async def fake_analysis(messages, max_tokens=None):
        return {"summary": "Short call.", "rating": 3, "interest": "cold", "qualified": False,
                "sentiment": "neutral", "next_action": "None", "callback_time": None, "qualification": [],
                "key_points": [], "objections": [], "objection_tags": []}

    monkeypatch.setattr(analysis_mod, "complete_json", fake_analysis)
    dialed: list[str] = []
    hung_up: list[str] = []

    async def fake_make_call(call_uuid, to, max_sec, caller_name=""):
        dialed.append(to)
        return f"pl-req-{call_uuid[:8]}"

    async def fake_hangup(pid):
        hung_up.append(pid)
        return True

    async def must_not_use_vobiz(*a, **k):
        raise AssertionError("Vobiz was used while TELEPHONY_PROVIDER=plivo")

    monkeypatch.setattr(plivo, "make_call", fake_make_call)
    monkeypatch.setattr(plivo, "hangup_call", fake_hangup)
    monkeypatch.setattr(vobiz, "make_call", must_not_use_vobiz)
    monkeypatch.setattr(vobiz, "hangup_call", must_not_use_vobiz)
    try:
        with TestClient(app, client=LOCAL) as c:
            c.dialed, c.hung_up = dialed, hung_up
            yield c
    finally:
        _end_my_calls()
        inbound_router._clear_check()


class FakePlivo:
    """Records every Plivo REST request and answers from a small route table."""

    def __init__(self, routes: dict[tuple[str, str], object]):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.replace("/v1/Account/MAPLIVOTEST", "")
        answer = self.routes.get((request.method, path))
        if callable(answer):
            answer = answer(request)
        if answer is None:
            return httpx.Response(404, json={"error": "not found"})
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)

    def install(self, monkeypatch) -> "FakePlivo":
        transport = httpx.MockTransport(self)
        monkeypatch.setattr(plivo, "_client", lambda timeout: httpx.AsyncClient(transport=transport, timeout=timeout))
        return self


def _body(req: httpx.Request) -> dict:
    return json.loads(req.content or b"{}")


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- switch + REST
def test_switch_resolves_to_plivo(plivo_mode):
    assert settings.provider == "plivo" and settings.provider_name == "Plivo"
    assert settings.caller_number == "+918000000001" and settings.telephony_ready
    assert telephony.active() is plivo and telephony.url_token is plivo.url_token
    assert telephony.speak_xml is plivo.speak_xml


def test_make_call_request(plivo_mode, monkeypatch):
    fake = FakePlivo({("POST", "/Call/"): httpx.Response(201, json={"message": "call fired",
                                                                    "request_uuid": "pl-req-1", "api_id": "x"})})
    fake.install(monkeypatch)
    assert _run(plivo.make_call("u" * 32, "+919460000001", 300, caller_name="PouchWale")) == "pl-req-1"
    req = fake.requests[-1]
    assert str(req.url) == f"{PLIVO_PREFIX}/Call/"
    assert req.headers["authorization"].startswith("Basic ")  # HTTP Basic, not X-Auth headers
    assert "x-auth-id" not in req.headers
    b = _body(req)
    assert (b["from"], b["to"], b["caller_name"]) == ("918000000001", "919460000001", "PouchWale")
    tok = plivo.url_token("u" * 32)
    assert b["answer_url"] == f"https://agent.example.com/plivo/answer/{'u' * 32}/{tok}"
    assert b["hangup_url"].startswith("https://agent.example.com/plivo/hangup/")
    assert b["ring_url"].startswith("https://agent.example.com/plivo/ring/")
    assert b["time_limit"] == 360 and b["ring_timeout"] == 35


def test_make_call_errors(plivo_mode, monkeypatch):
    FakePlivo({("POST", "/Call/"): httpx.Response(401, text="unauthorized")}).install(monkeypatch)
    with pytest.raises(plivo.PlivoError) as e:
        _run(plivo.make_call("u" * 32, "+919460000001", 300))
    assert "PLIVO_AUTH_ID" in str(e.value) and not e.value.retryable and telephony.is_retryable(e.value) is False
    FakePlivo({("POST", "/Call/"): httpx.Response(503, text="busy")}).install(monkeypatch)
    with pytest.raises(plivo.PlivoError) as e:
        _run(plivo.make_call("u" * 32, "+919460000001", 300))
    assert e.value.retryable and telephony.is_retryable(e.value)


def test_hangup_call_falls_back_to_request_cancel(plivo_mode, monkeypatch):
    fake = FakePlivo({("DELETE", "/Request/pl-req-9/"): httpx.Response(204)}).install(monkeypatch)
    assert _run(plivo.hangup_call("pl-req-9")) is True
    assert [r.url.path.split("MAPLIVOTEST")[1] for r in fake.requests] == ["/Call/pl-req-9/", "/Request/pl-req-9/"]
    fake = FakePlivo({("DELETE", "/Call/pl-call-9/"): httpx.Response(204)}).install(monkeypatch)
    assert _run(plivo.hangup_call("pl-call-9")) is True and len(fake.requests) == 1
    # an empty id must never reach Plivo (DELETE /Call/ without an id hangs up EVERY call on the account)
    fake = FakePlivo({}).install(monkeypatch)
    assert _run(plivo.hangup_call("")) is False and fake.requests == []


def test_check_auth(plivo_mode, monkeypatch):
    FakePlivo({("GET", "/"): {"auth_id": "MAPLIVOTEST"},
               ("GET", "/Number/918000000001/"): {"number": "918000000001", "voice_enabled": True}}).install(monkeypatch)
    ok, detail = _run(plivo.check_auth())
    assert ok and "found" in detail
    FakePlivo({("GET", "/"): {"auth_id": "MAPLIVOTEST"}}).install(monkeypatch)
    ok, detail = _run(plivo.check_auth())
    assert not ok and "not a number on this Plivo account" in detail


def test_recording_hosts():
    assert plivo.is_recording_url("https://media.plivo.com/v1/Account/MA1/Recording/r1.mp3")
    assert plivo.is_recording_url("https://s3.amazonaws.com/recordings_2026/r1.mp3")
    assert not plivo.is_recording_url("http://media.plivo.com/r1.mp3")
    assert not plivo.is_recording_url("https://evil.example.com/plivo.com/r1.mp3")
    assert not plivo.is_plivo_host("https://s3.amazonaws.com/r1.mp3")  # no credentials to S3


# ---------------------------------------------------------------- account API used by inbound setup / back-fill
def test_account_api_normalised(plivo_mode, monkeypatch):
    apps_p1 = {"meta": {"limit": 20, "next": "/v1/...offset=20", "offset": 0, "total_count": 21},
               "objects": [{"app_id": str(i), "app_name": f"app{i}", "answer_url": "x"} for i in range(20)]}
    apps_p2 = {"meta": {"limit": 20, "next": None, "offset": 20, "total_count": 21},
               "objects": [{"app_id": "77", "app_name": "GP3-Voice-Agent-Inbound", "answer_url": "a",
                            "hangup_url": "h"}]}
    calls = {"meta": {"next": None, "total_count": 1},
             "objects": [{"call_uuid": "pl-cdr-1", "call_direction": "inbound", "from_number": "919460000050",
                          "to_number": "918000000001", "initiation_time": "2026-11-14 10:00:00+05:30",
                          "end_time": "2026-11-14 10:00:30+05:30", "caller_name": "Ravi"}]}
    fake = FakePlivo({
        ("GET", "/Application/"): lambda r: apps_p2 if r.url.params.get("offset") == "20" else apps_p1,
        ("POST", "/Application/"): {"message": "created", "app_id": "88"},
        ("POST", "/Application/88/"): {"message": "changed"},
        ("GET", "/Number/918000000001/"): {"number": "918000000001", "voice_enabled": True,
                                           "application": "/v1/Account/MAPLIVOTEST/Application/77/"},
        ("POST", "/Number/918000000001/"): {"message": "changed"},
        ("GET", "/Call/"): calls,
    }).install(monkeypatch)

    apps = _run(plivo.list_applications())
    assert len(apps) == 21 and apps[-1]["app_id"] == "77"
    assert all(r.url.params["limit"] == "20" for r in fake.requests)

    num = _run(plivo.find_number("+918000000001"))
    assert (num["application_id"], num["voice_enabled"], num["status"], num["trunk_group_id"]) == ("77", True,
                                                                                                    "active", None)
    assert _run(plivo.find_number("+919999999999")) is None  # 404 = not on this account

    assert _run(plivo.create_application({"app_name": "GP3-Voice-Agent-Inbound", "answer_url": "a",
                                          "answer_method": "POST", "application_type": "XML"})) == "88"
    assert "application_type" not in _body(fake.requests[-1])  # Vobiz-only field dropped
    _run(plivo.update_application("88", {"answer_url": "b", "hangup_url": "c"}))
    assert _body(fake.requests[-1]) == {"answer_url": "b", "hangup_url": "c"}
    _run(plivo.attach_number("+918000000001", "88"))
    assert _body(fake.requests[-1]) == {"app_id": "88"}
    assert _run(plivo.account_concurrency()) == {}

    recs = _run(plivo.inbound_call_records("+918000000001", "2026-11-13", "2026-11-15"))
    p = fake.requests[-1].url.params
    assert (p["call_direction"], p["to_number"], p["end_time__gte"], p["end_time__lte"]) == (
        "inbound", "918000000001", "2026-11-13 00:00", "2026-11-15 23:59")
    r = recs[0]
    assert (r["uuid"], r["destination_number"], r["caller_id_number"], r["caller_id_name"]) == (
        "pl-cdr-1", "918000000001", "919460000050", "Ravi")
    assert r["start_time"].startswith("2026-11-14 10:00:00")


# ---------------------------------------------------------------- outbound call end to end
def _phone_call(c, phone: str) -> dict:
    lead = c.post("/api/leads", json={"name": "Kiran Rao", "phone": phone, "company": "Rao Foods"}).json()
    r = c.post("/api/calls", json={"lead_id": lead["id"]})
    assert r.status_code == 201, r.text
    return r.json()


def _wait(c, uuid: str, cond, secs: float = 6) -> dict:
    deadline = time.time() + secs
    d = c.get(f"/api/calls/{uuid}").json()
    while time.time() < deadline and not cond(d):
        time.sleep(0.1)
        d = c.get(f"/api/calls/{uuid}").json()
    return d


def test_outbound_call_over_plivo(client):
    call = _phone_call(client, "9460000001")
    assert client.dialed[-1] == "+919460000001"
    uuid, tok = call["uuid"], plivo.url_token(call["uuid"])
    assert client.post(f"/plivo/answer/{uuid}/{vobiz.url_token(uuid)}").status_code == 403  # Vobiz's secret
    r = client.post(f"/plivo/ring/{uuid}/{tok}", data={"CallUUID": "pl-call-1", "CallStatus": "ringing"})
    assert r.status_code == 200
    r = client.post(f"/plivo/answer/{uuid}/{tok}", data={"CallUUID": "pl-call-1", "RequestUUID": "pl-req-x",
                                                          "Direction": "outbound", "CallStatus": "in-progress"})
    root = ET.fromstring(r.text)
    assert [el.tag for el in root] == ["Record", "Stream"]
    assert root[0].get("recordSession") == "true"
    assert root[1].text == f"wss://agent.example.com/plivo/stream/{uuid}/{tok}"
    assert root[1].get("bidirectional") == "true" and root[1].get("contentType") == "audio/x-mulaw;rate=8000"
    assert root[0].get("callbackUrl") == f"https://agent.example.com/plivo/recording/{uuid}/{tok}"

    with client.websocket_connect(f"/plivo/stream/{uuid}/{tok}") as ws:
        ws.send_text(json.dumps({"event": "start", "sequenceNumber": 1,
                                 "start": {"callId": "pl-call-1", "streamId": "st-1", "accountId": "MAPLIVOTEST",
                                           "tracks": ["inbound"],
                                           "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000}}}))
        mu = pcm16_to_ulaw(tone(20, 8000))
        for i in range(60):
            ws.send_text(json.dumps({"event": "media", "sequenceNumber": i + 2, "streamId": "st-1",
                                     "media": {"track": "inbound", "timestamp": str(i * 20), "chunk": i,
                                               "payload": base64.b64encode(mu).decode()}}))
        msg = json.loads(ws.receive_text())
        assert msg["event"] == "playAudio" and msg["streamId"] == "st-1"
        assert msg["media"]["contentType"] == "audio/x-mulaw" and msg["media"]["sampleRate"] == 8000
        # recording callback (Plivo: RecordUrl + RecordingID)
        assert client.post(f"/plivo/recording/{uuid}/{tok}",
                           data={"RecordUrl": "https://media.plivo.com/v1/Account/MAPLIVOTEST/Recording/r1.mp3",
                                 "RecordingID": "r1"}).status_code == 200
    # Plivo documents no 'stop' event: the socket closing ends the session
    d = _wait(client, uuid, lambda d: d["status"] == "completed")
    assert d["status"] == "completed" and d["end_reason"] == "user_hangup"
    assert d["transcript"][0]["role"] == "agent" and d["has_recording"]
    with SessionLocal() as db:
        row = db.scalar(select(Call).where(Call.uuid == uuid))
        assert row.provider_call_id == "pl-call-1" and row.from_number == "+918000000001"
        assert row.recording_url.startswith("https://media.plivo.com/")


@pytest.mark.parametrize("phone,data,expected", [
    ("9460000101", {"CallStatus": "busy", "HangupCause": "USER_BUSY"}, ("busy", "busy")),
    ("9460000102", {"CallStatus": "no-answer"}, ("no_answer", "no_answer")),
    ("9460000103", {"CallStatus": "timeout"}, ("no_answer", "no_answer")),
    ("9460000104", {"HangupCauseCode": "6010"}, ("no_answer", "no_answer")),
    ("9460000105", {"HangupCauseCode": "3020", "CallStatus": "completed"}, ("busy", "busy")),
    ("9460000106", {"HangupCauseCode": "2000", "CallStatus": "failed"}, ("failed", "error")),
    ("9460000107", {"CallStatus": "failed", "HangupCause": "UNALLOCATED_NUMBER"}, ("failed", "error")),
])
def test_unanswered_hangup_outcomes(client, phone, data, expected):
    call = _phone_call(client, phone)
    uuid, tok = call["uuid"], plivo.url_token(call["uuid"])
    assert client.post(f"/plivo/hangup/{uuid}/{tok}", data={"CallUUID": "pl-x", **data}).status_code == 200
    d = _wait(client, uuid, lambda d: d["status"] not in ACTIVE_CALL_STATUSES)
    assert (d["status"], d["end_reason"]) == expected


def test_system_info_and_provider_test(client, monkeypatch):
    info = client.get("/api/settings/system").json()
    assert info["telephony_provider"] == "plivo" and info["telephony_name"] == "Plivo"
    assert info["providers"]["plivo"] is True and info["providers"]["telephony"] is True

    async def fake_check():
        return True, "credentials OK"

    monkeypatch.setattr(plivo, "check_auth", fake_check)
    from app.routers import settings as settings_router

    async def ok3(*a, **k):
        return True, "ok", 1

    for name in ("check_tts", "check_tts_stream", "check_stt_stream"):
        monkeypatch.setattr(settings_router, name, ok3)
    out = client.post("/api/settings/test-providers").json()
    assert out["plivo"] == {"ok": True, "detail": "credentials OK"} and "vobiz" not in out


# ---------------------------------------------------------------- inbound over Plivo
def _stream_uuid(xml: str) -> str:
    m = re.search(r"/plivo/stream/([0-9a-f]{32})/", xml)
    assert m, xml
    return m.group(1)


def test_inbound_answer_and_off_message(client):
    tok = plivo.inbound_token()
    assert tok != vobiz.inbound_token()
    data = {"CallUUID": "pl-inb-1", "From": "919460000071", "To": "918000000001", "Direction": "inbound",
            "CallStatus": "ringing", "CallerName": "919460000071"}
    assert client.post(f"/plivo/inbound/{vobiz.inbound_token()}/answer", data=data).status_code == 403
    r = client.post(f"/plivo/inbound/{tok}/answer", data=data)
    assert r.status_code == 200 and _stream_uuid(r.text)
    with SessionLocal() as db:
        row = db.scalar(select(Call).where(Call.provider_call_id == "pl-inb-1"))
        assert (row.direction, row.from_number, row.to_number) == ("inbound", "+919460000071", "+918000000001")

    assert client.put("/api/settings", json={"inbound_enabled": False}).status_code == 200
    try:
        r = client.post(f"/plivo/inbound/{tok}/answer", data={**data, "CallUUID": "pl-inb-2",
                                                               "From": "919460000072"})
        root = ET.fromstring(r.text)
        assert [el.tag for el in root] == ["Speak", "Hangup"]
        assert (root[0].get("voice"), root[0].get("language")) == ("Polly.Aditi", "hi-IN")
    finally:
        client.put("/api/settings", json={"inbound_enabled": True})
    urls = telephony.inbound_urls()
    assert urls["answer"] == f"https://agent.example.com/plivo/inbound/{tok}/answer"


def test_inbound_setup_status_names_plivo(client):
    s = client.get("/api/inbound/setup", params={"check": "false"}).json()
    assert (s["provider"], s["provider_name"], s["provider_ready"], s["vobiz_ready"]) == ("plivo", "Plivo",
                                                                                         True, True)
    assert s["answer_url"].startswith("https://agent.example.com/plivo/inbound/")
    assert not any("Vobiz" in p for p in s["problems"])


def test_plivo_number_accepts_any_typed_format():
    for typed in ("80 3177 6200", "080-3177-6200", "+91 80 3177 6200", "918031776200"):
        assert plivo.plivo_number(typed) == "918031776200"
    assert plivo.plivo_number("+919876543210") == plivo.plivo_number("98765 43210") == "919876543210"


def test_plivo_stream_has_noise_cancellation(monkeypatch):
    xml = plivo.answer_xml("u" * 32, 300)
    assert 'noiseCancellation="true"' in xml and 'noiseCancellationLevel="85"' in xml
    monkeypatch.setattr(settings, "plivo_noise_cancellation_level", 150)
    assert 'noiseCancellationLevel="100"' in plivo.answer_xml("u" * 32, 300)  # clamped to Plivo's 60-100
    monkeypatch.setattr(settings, "plivo_noise_cancellation", False)
    assert "noiseCancellation" not in plivo.answer_xml("u" * 32, 300)
    assert "noiseCancellation" not in vobiz.answer_xml("u" * 32, 300)  # Vobiz: not a documented option


# ---------------------------------------------------------------- lost hangup webhooks
def test_ended_call_reads_plivo_call_record(plivo_mode, monkeypatch):
    """GET /Call/{id}/: a record exists only once the call is over (404 while it is ringing or live)."""
    FakePlivo({
        ("GET", "/Call/pl-ring/"): {"call_uuid": "pl-ring", "end_time": "2026-11-20 03:32:54+00:00", "answer_time": None,
                                     "hangup_cause_code": 6010, "hangup_cause_name": "Ring Timeout Reached",
                                     "hangup_source": "Plivo"},
        ("GET", "/Call/pl-err/"): {"call_uuid": "pl-err", "end_time": "2026-11-20 03:32:43+00:00",
                                    "answer_time": "2026-11-20 03:32:42+00:00", "hangup_cause_code": 7011,
                                    "hangup_cause_name": "Error Reaching Answer URL", "hangup_source": "Error"},
        ("GET", "/Call/pl-open/"): {"call_uuid": "pl-open", "end_time": None},
    }).install(monkeypatch)
    assert _run(plivo.ended_call("pl-ring")) == {"HangupCauseCode": "6010", "HangupCause": "Ring Timeout Reached",
                                                  "CallStatus": "completed", "Answered": False}
    err = _run(plivo.ended_call("pl-err"))
    assert err["CallStatus"] == "failed" and err["Answered"] and err["HangupCauseCode"] == "7011"
    assert _run(plivo.ended_call("pl-live")) is None    # 404: still ringing / live
    assert _run(plivo.ended_call("pl-open")) is None    # no end time yet
    assert _run(plivo.ended_call("")) is None


def test_watchdog_settles_calls_whose_hangup_webhook_never_came(client, monkeypatch):
    """2026-11-20: the tunnel passed none of Plivo's callbacks for ~90 s. Three campaign calls Plivo had already
    ended (ring timeout, an answer it could not connect) stayed 'ringing' and held the campaign's slots until
    someone hung them up by hand. After 20 s without a session or webhook the watchdog asks Plivo."""
    calls = {name: _phone_call(client, phone) for name, phone in
             (("timeout", "9460000201"), ("broken", "9460000202"), ("ringing", "9460000203"), ("fresh", "9460000204"))}
    records = {
        "timeout": {"HangupCauseCode": "6010", "HangupCause": "Ring Timeout Reached", "CallStatus": "completed",
                    "Answered": False},
        "broken": {"HangupCauseCode": "7011", "HangupCause": "Error Reaching Answer URL", "CallStatus": "failed",
                   "Answered": True},
        "fresh": {"HangupCauseCode": "6010", "HangupCause": "Ring Timeout Reached", "CallStatus": "completed",
                  "Answered": False},
    }
    with SessionLocal() as db:
        ids = {name: db.scalar(select(Call.provider_call_id).where(Call.uuid == c["uuid"])) for name, c in calls.items()}
        old = [calls[n]["uuid"] for n in ("timeout", "broken", "ringing")]  # "fresh" was dialled just now
        db.execute(update(Call).where(Call.uuid.in_(old)).values(started_at=utcnow() - cm_mod.timedelta(seconds=30)))
        db.commit()
    asked: list[str] = []

    async def fake_ended(pid):
        asked.append(pid)
        name = next(n for n, i in ids.items() if i == pid)
        return records.get(name)  # "ringing": Plivo has no record yet

    monkeypatch.setattr(plivo, "ended_call", fake_ended)
    client.portal.call(manager._sweep)
    state = {n: client.get(f"/api/calls/{c['uuid']}").json() for n, c in calls.items()}
    assert (state["timeout"]["status"], state["timeout"]["end_reason"]) == ("no_answer", "no_answer")
    assert state["broken"]["status"] == "failed" and "Error Reaching Answer URL".upper() in state["broken"]["error"]
    assert state["ringing"]["status"] in ACTIVE_CALL_STATUSES      # still ringing at Plivo: left alone
    assert state["fresh"]["status"] in ACTIVE_CALL_STATUSES        # under 20 s: not asked yet
    assert ids["fresh"] not in asked and ids["ringing"] in asked
