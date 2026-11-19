"""The checks made before a phone call is dialed (services/preflight.py).

What they must do: refuse a call, with a reason the dashboard can show, when it is PROVEN that the customer
would pick up a silent line (the voice provider has no credit; the tunnel is not running). What they must
never do: cost a healthy call anything, or hold a call back on an answer that is not clear.
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import or_, select, update

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.main import app
from app.models import ACTIVE_CALL_STATUSES, Call, Campaign, CampaignLead, Lead
from app.services import call_manager as cm_mod
from app.services import dialer as dialer_mod
from app.services import inbound_audio, preflight, sarvam, vobiz
from app.services import session as session_mod
from app.services.call_manager import manager
from app.services.dialer import Dialer, dialer

from .fakes import FakeSTT, FakeTTSStream, ScriptedLLM, fake_tts_rest

settings = get_settings()
LOCAL = ("127.0.0.1", 50000)
NGROK = "https://demo-agent.ngrok-free.dev"

# the real functions (conftest replaces them with "everything is fine" before every test)
REAL_VOICE_STATUS = preflight._voice_status
REAL_LINK_ANSWER = preflight._link_answer

# what the voice provider really answered on 2026-11-19 (backend log), and an unrelated error whose request
# id happens to contain "402"
NO_CREDIT_TEXT = ('Sarvam TTS 402: {"error":{"message":"No credits available.","code":"insufficient_quota_error",'
                  '"request_id":"20261119_30a01d20-b449-4b13-a7b0-16c88abddc15"}}')
OTHER_ERROR_TEXT = ('Sarvam TTS 500: {"error":{"message":"Internal error.","code":"internal_server_error",'
                    '"request_id":"20261119_a402b1c2-4020-4402-9402-402402402402"}}')
REFUSED_NO_CREDIT = f"{preflight.REFUSED} {preflight.NO_CREDIT}"
REFUSED_NO_TUNNEL = f"{preflight.REFUSED} {preflight.NO_TUNNEL}"


# ---------------------------------------------------------------- fixtures + helpers
def _end_my_calls() -> None:
    with SessionLocal() as db:
        uuids = list(db.scalars(select(Call.uuid).where(
            Call.status.in_(ACTIVE_CALL_STATUSES),
            or_(Call.to_number.like("+91977%"), Call.from_number.like("+91977%")))).all())
        if uuids:
            db.execute(update(Call).where(Call.uuid.in_(uuids)).values(status="failed", end_reason="error",
                                                                       ended_at=utcnow()))
            db.commit()
    for u in uuids:
        manager.prepared.pop(u, None)


@pytest.fixture()
def client(monkeypatch):
    FakeSTT.instances.clear()
    monkeypatch.setattr(session_mod, "RealtimeSTT", FakeSTT)
    monkeypatch.setattr(session_mod, "TTSStream", FakeTTSStream)
    monkeypatch.setattr(session_mod, "tts_rest", fake_tts_rest)
    monkeypatch.setattr(session_mod, "stream_reply", ScriptedLLM(["जी बिल्कुल।"]))
    monkeypatch.setattr(cm_mod, "tts_rest", fake_tts_rest)
    monkeypatch.setattr(inbound_audio, "schedule_prewarm", lambda cfg: None)
    dialed: list[str] = []

    async def fake_make_call(call_uuid, to, max_sec, caller_name=""):
        dialed.append(to)
        return f"vobiz-{call_uuid[:8]}"

    async def fake_hangup(pid):
        return True

    monkeypatch.setattr(vobiz, "make_call", fake_make_call)
    monkeypatch.setattr(vobiz, "hangup_call", fake_hangup)
    with SessionLocal() as db:  # running campaigns left by other tests would be dialed by this test's app
        parked = list(db.scalars(select(Campaign.id).where(Campaign.status == "running")).all())
        if parked:
            db.execute(update(Campaign).where(Campaign.id.in_(parked)).values(status="paused"))
            db.commit()
    try:
        with TestClient(app, client=LOCAL) as c:
            c.portal.call(dialer.stop)  # campaigns in these tests are driven by hand
            c.dialed = dialed
            yield c
    finally:
        _end_my_calls()
        if parked:
            with SessionLocal() as db:
                db.execute(update(Campaign).where(Campaign.id.in_(parked), Campaign.status == "paused")
                           .values(status="running"))
                db.commit()


class Answers:
    """A scripted check: every call takes the next answer (the last one repeats). An exception is raised."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list = []

    async def __call__(self, *args):
        self.calls.append(args)
        answer = self.answers[0] if len(self.answers) == 1 else self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _voice(monkeypatch, *answers) -> Answers:
    fake = Answers(*answers)
    monkeypatch.setattr(preflight, "_voice_status", fake)
    return fake


def _link(monkeypatch, *answers) -> Answers:
    """The public link is an ngrok address and answers as scripted."""
    fake = Answers(*answers)
    monkeypatch.setattr(settings, "public_base_url", NGROK)
    monkeypatch.setattr(preflight, "_link_answer", fake)
    return fake


def _lead(n: int, name: str = "Preflight Lead") -> dict:
    phone = f"+9197700{n:05d}"
    with SessionLocal() as db:
        lead = Lead(name=name, phone=phone)
        db.add(lead)
        db.commit()
        return {"id": lead.id, "phone": phone}


def _lead_row(lead_id: int) -> Lead:
    with SessionLocal() as db:
        return db.get(Lead, lead_id)


def _calls_of(lead_id: int) -> list[Call]:
    with SessionLocal() as db:
        return list(db.scalars(select(Call).where(Call.lead_id == lead_id)).all())


def _call(client, lead: dict):
    return client.post("/api/calls", json={"lead_id": lead["id"]})


def _wait(cond, timeout: float = 6.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- pure rules
def test_only_a_credit_refusal_counts_as_one():
    assert preflight.is_credit_refusal(NO_CREDIT_TEXT)
    assert preflight.is_credit_refusal(RuntimeError(NO_CREDIT_TEXT))
    assert preflight.is_credit_refusal("quota_exceeded: Credits exhausted. Visit the API Dashboard to review "
                                       "and manage your subscription.")
    assert preflight.is_credit_refusal("Credits exhausted. Add credits in the API Dashboard.")
    # "402" inside a request id of another error is not a credit refusal
    assert not preflight.is_credit_refusal(OTHER_ERROR_TEXT)
    assert not preflight.is_credit_refusal("TTS socket closed (1006)")
    assert not preflight.is_credit_refusal("SARVAM_API_KEY is not set")
    assert not preflight.is_credit_refusal("")


def test_only_an_ngrok_link_is_ever_asked(monkeypatch):
    for url, expected in [
        ("https://enjoyable-demo.ngrok-free.dev", "https://enjoyable-demo.ngrok-free.dev"),
        ("https://enjoyable-demo.ngrok-free.dev/", "https://enjoyable-demo.ngrok-free.dev"),
        ("https://demo.ngrok-free.app", "https://demo.ngrok-free.app"),
        ("https://DEMO.NGROK.IO", "https://DEMO.NGROK.IO"),
        ("https://agent.example.com", None),       # a server's own domain: never asked, never refused
        ("https://calls.pouchwale.com", None),
        ("http://demo.ngrok-free.dev", None),      # not https: the older checks already report it
        ("http://localhost:8100", None),
        ("", None),
        ("https://[bad", None),
    ]:
        monkeypatch.setattr(settings, "public_base_url", url)
        assert preflight.checkable_link() == expected, url


def test_voice_check_asks_the_provider_itself(monkeypatch):
    """The real request: a two-character synthesis sent straight to the provider, judged by its HTTP status
    (never by a cached clip)."""
    seen: list[httpx.Request] = []
    status = {"code": 402}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status["code"], json={"error": {"message": "No credits available."}})

    monkeypatch.setattr(preflight, "_voice_status", REAL_VOICE_STATUS)
    monkeypatch.setattr(sarvam, "_http", httpx.AsyncClient(base_url="https://voice.test",
                                                           transport=httpx.MockTransport(handler)))
    assert preflight.voice_suspect() is None
    assert _run(preflight.voice_has_no_credit()) is True
    assert preflight.voice_suspect() is not None  # proven empty: from now on calls ask before they dial
    preflight.suspect_voice("test")
    req = seen[-1]
    body = json.loads(req.content)
    assert req.method == "POST" and req.url.path == "/text-to-speech"
    assert req.headers["api-subscription-key"] == "test-sarvam"
    assert body["speaker"] == "priya" and body["model"] == settings.sarvam_tts_model
    assert len(body["text"]) == 2 and body["speech_sample_rate"] == 8000

    for code in (500, 429, 401):  # not a clear answer: no verdict, and the suspicion stays
        status["code"] = code
        assert _run(preflight.voice_has_no_credit()) is None
        assert preflight.voice_suspect() == "test"

    status["code"] = 200  # it speaks again: no more extra checks
    assert _run(preflight.voice_has_no_credit()) is False
    assert preflight.voice_suspect() is None
    assert len(seen) == 5


def test_link_check_reads_the_tunnel_services_own_answer(monkeypatch):
    seen: list[httpx.Request] = []
    reply = {"status": 404, "headers": {"Ngrok-Error-Code": "ERR_NGROK_3200"}}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(reply["status"], headers=reply["headers"], text="x")

    monkeypatch.setattr(preflight, "_link_answer", REAL_LINK_ANSWER)
    monkeypatch.setattr(preflight, "_link_client",
                        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(settings, "public_base_url", NGROK + "/")
    assert _run(preflight.call_blockers()) == [preflight.NO_TUNNEL]
    assert str(seen[-1].url) == f"{NGROK}/vobiz/ping" and seen[-1].method == "GET"
    assert seen[-1].headers["ngrok-skip-browser-warning"] == "1"

    reply.update(status=200, headers={})  # the tunnel is running again
    assert _run(preflight.call_blockers()) == []
    assert len(seen) == 2
    assert _run(preflight.call_blockers()) == [] and len(seen) == 2  # a link that just answered is not asked again


def test_a_check_that_breaks_never_holds_a_call(monkeypatch):
    async def broken():
        raise RuntimeError("the check itself is broken")

    monkeypatch.setattr(preflight, "_voice_blocker", broken)
    link = _link(monkeypatch, (404, "ERR_NGROK_3200"))
    assert _run(preflight.call_blockers()) == [preflight.NO_TUNNEL]  # the other check still counts
    monkeypatch.setattr(preflight, "_link_blocker", broken)
    assert _run(preflight.call_blockers()) == []
    assert len(link.calls) == 1


def test_refused_inbound_clip_makes_calls_ask_first(monkeypatch):
    async def refused(text, cfg, sample_rate):
        raise RuntimeError(NO_CREDIT_TEXT)

    async def broken(text, cfg, sample_rate):
        raise RuntimeError(OTHER_ERROR_TEXT)

    cfg = {"voice": "priya", "language_code": "hi-IN"}
    monkeypatch.setattr(inbound_audio, "tts_rest", broken)
    assert _run(inbound_audio.build("preflight clip one", cfg)) is None
    assert preflight.voice_suspect() is None  # an unrelated failure is not a reason to suspect the credit
    monkeypatch.setattr(inbound_audio, "tts_rest", refused)
    assert _run(inbound_audio.build("preflight clip two", cfg)) is None
    assert preflight.voice_suspect() is not None


# ---------------------------------------------------------------- the Call button
def test_healthy_call_asks_nobody(client, monkeypatch):
    voice = _voice(monkeypatch, AssertionError("a healthy call must not ask the voice provider"))
    link = Answers(AssertionError("a link that is not an ngrok address must never be asked"))
    monkeypatch.setattr(preflight, "_link_answer", link)
    lead = _lead(1)
    r = _call(client, lead)
    assert r.status_code == 201, r.text
    assert client.dialed == [lead["phone"]] and voice.calls == [] and link.calls == []


def test_no_credit_is_refused_before_the_phone_rings(client, monkeypatch):
    voice = _voice(monkeypatch, 402, 402, 200)
    preflight.suspect_voice("an earlier call ended with an error")
    lead = _lead(2)
    for _ in range(2):  # a refusal is never remembered: every attempt asks again
        r = _call(client, lead)
        assert r.status_code == 400 and r.json()["detail"] == REFUSED_NO_CREDIT, r.text
    assert len(voice.calls) == 2
    # nothing happened to the customer or to the records
    assert client.dialed == [] and _calls_of(lead["id"]) == []
    row = _lead_row(lead["id"])
    assert row.status == "new" and (row.attempts or 0) == 0 and row.last_called_at is None

    r = _call(client, lead)  # credits were added: the very next attempt goes through
    assert r.status_code == 201, r.text
    assert client.dialed == [lead["phone"]] and preflight.voice_suspect() is None
    other = _lead(3)
    assert _call(client, other).status_code == 201
    assert len(voice.calls) == 3  # and from then on nobody is asked again


def test_an_unclear_voice_answer_never_holds_a_call(client, monkeypatch):
    voice = _voice(monkeypatch, httpx.ConnectTimeout("no answer"), 500, 429, asyncio.TimeoutError())
    preflight.suspect_voice("an earlier call ended with an error")
    leads = [_lead(10 + i) for i in range(4)]
    for lead in leads:
        r = _call(client, lead)
        assert r.status_code == 201, r.text
        assert preflight.voice_suspect() is not None  # still not known: the next call asks again
    assert client.dialed == [lead["phone"] for lead in leads] and len(voice.calls) == 4


def test_a_greeting_refused_for_credit_makes_the_next_call_ask(client, monkeypatch):
    async def other_failure(text, cfg, sample_rate):
        raise RuntimeError(OTHER_ERROR_TEXT)

    async def refused(text, cfg, sample_rate):
        raise RuntimeError(NO_CREDIT_TEXT)

    voice = _voice(monkeypatch, 402)
    monkeypatch.setattr(cm_mod, "tts_rest", other_failure)
    first = _lead(20)
    assert _call(client, first).status_code == 201
    time.sleep(0.2)
    assert preflight.voice_suspect() is None and voice.calls == []  # some other failure: nothing to check

    monkeypatch.setattr(cm_mod, "tts_rest", refused)
    second = _lead(21)
    assert _call(client, second).status_code == 201  # this one was already being dialed, as before
    _wait(lambda: preflight.voice_suspect() is not None)
    third = _lead(22)
    r = _call(client, third)
    assert r.status_code == 400 and r.json()["detail"] == REFUSED_NO_CREDIT
    assert client.dialed == [first["phone"], second["phone"]] and len(voice.calls) == 1


def _answer_and_fail(client, lead: dict) -> str:
    """Place a call whose session cannot speak (the voice provider refuses every request) and let it end."""
    r = _call(client, lead)
    assert r.status_code == 201, r.text
    uuid = r.json()["uuid"]
    tok = vobiz.url_token(uuid)
    assert client.post(f"/vobiz/answer/{uuid}/{tok}", data={"CallUUID": f"prov-{uuid[:6]}"}).status_code == 200
    with client.websocket_connect(f"/vobiz/stream/{uuid}/{tok}") as ws:
        ws.send_text(json.dumps({"event": "start", "sequenceNumber": 0,
                                 "start": {"callId": f"prov-{uuid[:6]}", "streamId": "s-1", "tracks": ["inbound"],
                                           "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000}}}))
        _wait(lambda: client.get(f"/api/calls/{uuid}").json()["status"] not in ACTIVE_CALL_STATUSES)
    return uuid


def test_a_call_that_died_for_credit_says_so_and_the_next_is_refused(client, monkeypatch):
    async def refused(text, cfg, sample_rate):
        raise RuntimeError(NO_CREDIT_TEXT)

    monkeypatch.setattr(cm_mod, "tts_rest", refused)
    monkeypatch.setattr(session_mod, "tts_rest", refused)
    _voice(monkeypatch, 402)
    lead = _lead(30)
    uuid = _answer_and_fail(client, lead)  # placed while nothing was suspected yet: it rings, as before
    _wait(lambda: client.get(f"/api/calls/{uuid}").json().get("error") == preflight.NO_CREDIT_ON_CALL)
    detail = client.get(f"/api/calls/{uuid}").json()
    assert detail["end_reason"] == "error" and detail["error"] == preflight.NO_CREDIT_ON_CALL
    assert preflight.voice_suspect() is not None

    nxt = _lead(31)
    r = _call(client, nxt)
    assert r.status_code == 400 and r.json()["detail"] == REFUSED_NO_CREDIT
    assert client.dialed == [lead["phone"]]


def test_an_error_end_keeps_the_next_call_asking_until_the_answer_is_clear(client, monkeypatch):
    """The call died of something the voice provider's text does not explain, and the check right after it
    got no answer either: nothing is written on the call, but the next call still asks before it dials."""
    async def broken(text, cfg, sample_rate):
        raise RuntimeError(OTHER_ERROR_TEXT)

    monkeypatch.setattr(cm_mod, "tts_rest", broken)
    monkeypatch.setattr(session_mod, "tts_rest", broken)
    voice = _voice(monkeypatch, httpx.ConnectTimeout("no answer"), 402)
    lead = _lead(34)
    uuid = _answer_and_fail(client, lead)
    _wait(lambda: len(voice.calls) >= 1)
    time.sleep(0.2)
    assert client.get(f"/api/calls/{uuid}").json()["error"] is None
    assert preflight.voice_suspect() is not None
    nxt = _lead(35)
    r = _call(client, nxt)
    assert r.status_code == 400 and r.json()["detail"] == REFUSED_NO_CREDIT
    assert client.dialed == [lead["phone"]] and len(voice.calls) == 2


def test_an_error_end_with_credit_left_is_not_blamed_on_credit(client, monkeypatch):
    async def broken(text, cfg, sample_rate):
        raise RuntimeError(OTHER_ERROR_TEXT)

    monkeypatch.setattr(cm_mod, "tts_rest", broken)
    monkeypatch.setattr(session_mod, "tts_rest", broken)
    voice = _voice(monkeypatch, 200)
    lead = _lead(32)
    uuid = _answer_and_fail(client, lead)
    _wait(lambda: len(voice.calls) >= 1)        # the provider was asked once the call had ended with an error
    _wait(lambda: preflight.voice_suspect() is None)  # ... and it speaks: nothing more to check
    assert client.get(f"/api/calls/{uuid}").json()["error"] is None
    nxt = _lead(33)
    assert _call(client, nxt).status_code == 201
    assert len(voice.calls) == 1


# ---------------------------------------------------------------- the tunnel
def test_tunnel_not_running_is_refused_before_the_phone_rings(client, monkeypatch):
    link = _link(monkeypatch, (404, "ERR_NGROK_3200"), (404, "err_ngrok_3200"), (200, ""))
    lead = _lead(40)
    for _ in range(2):  # never remembered: asked again on every attempt
        r = _call(client, lead)
        assert r.status_code == 400 and r.json()["detail"] == REFUSED_NO_TUNNEL, r.text
    assert client.dialed == [] and _calls_of(lead["id"]) == []
    assert [args[0] for args in link.calls] == [f"{NGROK}/vobiz/ping"] * 2

    r = _call(client, lead)  # the tunnel was started: the very next attempt goes through
    assert r.status_code == 201, r.text
    other = _lead(41)
    assert _call(client, other).status_code == 201
    assert client.dialed == [lead["phone"], other["phone"]]
    assert len(link.calls) == 3  # a link that just answered is not asked again for a while


@pytest.mark.parametrize("n, answer", [
    (50, httpx.ConnectError("no route")),  # cannot tell from here: the phone provider may still get through
    (52, asyncio.TimeoutError()),
    (54, (404, "")),                       # an older backend without the ping route, or a proxy in front
    (56, (502, "ERR_NGROK_8012")),         # another tunnel error: not proof that calls cannot connect
    (58, (200, "")),
])
def test_an_unclear_link_answer_never_holds_a_call(client, monkeypatch, n, answer):
    link = _link(monkeypatch, answer)
    lead, other = _lead(n), _lead(n + 1)
    for one in (lead, other):
        r = _call(client, one)
        assert r.status_code == 201, r.text
    assert client.dialed == [lead["phone"], other["phone"]]
    assert len(link.calls) == 1  # asked once, then left alone for a while


def test_both_reasons_are_reported_together(client, monkeypatch):
    _voice(monkeypatch, 402)
    _link(monkeypatch, (404, "ERR_NGROK_3200"))
    preflight.suspect_voice("an earlier call ended with an error")
    lead = _lead(60)
    r = _call(client, lead)
    assert r.status_code == 400
    assert r.json()["detail"] == f"{preflight.REFUSED} {preflight.NO_CREDIT} {preflight.NO_TUNNEL}"
    assert client.dialed == []


def test_ping_answers_through_both_prefixes(client):
    for prefix in ("/plivo", "/vobiz"):
        r = client.get(f"{prefix}/ping")
        assert r.status_code == 200 and r.text == "ok"
    tunnel = {"X-Forwarded-For": "203.0.113.7"}  # how a request through the tunnel arrives (no login)
    assert client.get("/plivo/ping", headers=tunnel).text == "ok"
    assert client.get("/api/leads", headers=tunnel).status_code == 401  # ... and nothing else opens up


# ---------------------------------------------------------------- campaigns
def test_a_campaign_pauses_instead_of_ringing_its_list(client, monkeypatch):
    voice = _voice(monkeypatch, 402)
    preflight.suspect_voice("an earlier call ended with an error")
    leads = [_lead(70), _lead(71)]
    with SessionLocal() as db:
        camp = Campaign(name="preflight-no-credit", status="running", concurrency=5, max_attempts=2,
                        retry_delay_min=15, respect_call_window=False)
        db.add(camp)
        db.flush()
        for lead in leads:
            db.add(CampaignLead(campaign_id=camp.id, lead_id=lead["id"], status="pending", attempts=0))
        db.commit()
        cid = camp.id

    def items() -> list[CampaignLead]:
        with SessionLocal() as db:
            return list(db.scalars(select(CampaignLead).where(CampaignLead.campaign_id == cid)
                                   .order_by(CampaignLead.id)).all())

    def status() -> str:
        with SessionLocal() as db:
            return db.get(Campaign, cid).status

    with SessionLocal() as db:  # calls other tests left open must not use up the lines this test needs
        open_calls = len(db.scalars(select(Call.id).where(Call.channel == "phone",
                                                          Call.status.in_(ACTIVE_CALL_STATUSES))).all())
    monkeypatch.setattr(dialer_mod, "GLOBAL_MAX_CONCURRENT", open_calls + 20)
    try:
        d = Dialer()
        client.portal.call(d._tick)
        assert client.dialed == [] and status() == "paused"  # nobody was rung, and the list is not used up
        assert len(voice.calls) == 2  # each planned call asked, and was refused
        assert [(it.status, it.attempts, it.last_call_uuid) for it in items()] == [("pending", 0, None)] * 2
        assert all(_calls_of(lead["id"]) == [] for lead in leads)
        assert all(_lead_row(lead["id"]).status == "new" for lead in leads)

        voice.answers = [200]  # credits were added and the campaign is resumed by hand
        with SessionLocal() as db:
            db.execute(update(Campaign).where(Campaign.id == cid).values(status="running"))
            db.commit()
        client.portal.call(d._tick)
        assert sorted(client.dialed) == sorted(lead["phone"] for lead in leads)
        assert [(it.status, it.attempts) for it in items()] == [("dialing", 1)] * 2
    finally:
        with SessionLocal() as db:
            db.execute(update(Campaign).where(Campaign.id == cid).values(status="paused"))
            db.commit()
