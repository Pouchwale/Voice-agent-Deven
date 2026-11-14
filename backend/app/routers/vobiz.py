"""Vobiz webhooks + bidirectional media stream (mu-law 8 kHz).

Outbound calls carry a per-call token in every URL. Inbound calls arrive on the static application URLs
/vobiz/inbound/{token}/answer|hangup (token = vobiz.inbound_token()), set up by /api/inbound/setup.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select

from ..db import SessionLocal
from ..models import ACTIVE_CALL_STATUSES, Call
from ..services import inbound_audio, vobiz
from ..services.audio import pcm16_to_ulaw, ulaw_to_pcm16
from ..services.call_manager import manager
from ..services.events import spawn
from ..services.repo import run_db, update_call
from ..services.session import CallSession, Transport

log = logging.getLogger("vobiz.router")
router = APIRouter(prefix="/vobiz", tags=["vobiz"])

# CallStatus values that only ever arrive once a call is over (a hangup posted to the answer URL)
_ENDED_STATUSES = {"completed", "cancel", "canceled", "cancelled", "busy", "no-answer", "no_answer", "failed",
                   "timeout"}


async def _payload(request: Request) -> dict:
    """Vobiz posts form-encoded bodies (sometimes JSON) — accept both."""
    ctype = request.headers.get("content-type", "")
    try:
        if "json" in ctype:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        form = await request.form()
        return {k: str(v) for k, v in form.items()}
    except Exception:  # noqa: BLE001
        with contextlib.suppress(Exception):
            return json.loads((await request.body()) or b"{}")
    return {}


def _call_row(call_uuid: str) -> dict | None:
    with SessionLocal() as db:
        c = db.scalar(select(Call).where(Call.uuid == call_uuid))
        if not c:
            return None
        return {"status": c.status, "answered": c.answered_at is not None, "provider": c.provider_call_id}


# ------------------------------------------------------------------ webhooks
def _xml(content: str) -> Response:
    return Response(content=content, media_type="application/xml")


@router.post("/answer/{call_uuid}/{token}")
async def answer(call_uuid: str, token: str, request: Request):
    if not vobiz.verify_token(call_uuid, token):
        return PlainTextResponse("forbidden", status_code=403)
    data = await _payload(request)
    # Guarded: only a still-active call is answered. Cancelled / superseded / finished before the customer
    # picked up (or a late retry): hang up, no AI.
    out = await run_db(manager.mark_in_progress, call_uuid, str(data.get("CallUUID") or "") or None)
    if out is None:
        log.info("answer for inactive call %s: hanging up", call_uuid)
        return _xml(vobiz.hangup_xml())
    from ..services.agent_config import load_agent_settings
    return _xml(vobiz.answer_xml(call_uuid, int(load_agent_settings().get("max_call_sec") or 300)))


@router.post("/ring/{call_uuid}/{token}")
async def ring(call_uuid: str, token: str, request: Request):
    if vobiz.verify_token(call_uuid, token):
        row = await run_db(_call_row, call_uuid)
        if row and row["status"] in ("queued", "initiated"):
            await run_db(lambda: update_call(call_uuid, status="ringing"))
    return PlainTextResponse("ok")


async def _handle_hangup(call_uuid: str, data: dict) -> None:
    manager.mark_hangup_received(call_uuid)  # the line is down: no REST hang-up needed later
    session = manager.sessions.get(call_uuid)
    if session is not None:
        if not session.ending:
            await session.end("user_hangup")
        return
    row = await run_db(_call_row, call_uuid)
    if not row or row["status"] not in ACTIVE_CALL_STATUSES:
        return
    code = str(data.get("HangupCauseCode") or "")
    cause = str(data.get("HangupCause") or data.get("HangupCauseName") or "").upper()
    call_status = str(data.get("CallStatus") or "").lower()
    if row["answered"]:
        await manager.finish_unanswered(call_uuid, "failed", "error",
                                        error=f"Answered but no media stream ({cause or code}).")
        return
    status, reason = vobiz.HANGUP_CODE_MAP.get(code, (None, None))
    if status is None:
        if "BUSY" in cause or call_status == "busy" or "REJECT" in cause:
            status, reason = "busy", "busy"
        elif call_status in ("failed",) or "INVALID" in cause:
            status, reason = "failed", "error"
        else:
            status, reason = "no_answer", "no_answer"
    await manager.finish_unanswered(call_uuid, status, reason, error=(cause or None) if status == "failed" else None)


@router.post("/hangup/{call_uuid}/{token}")
async def hangup(call_uuid: str, token: str, request: Request, background: BackgroundTasks):
    if not vobiz.verify_token(call_uuid, token):
        return PlainTextResponse("forbidden", status_code=403)
    data = await _payload(request)
    log.info("hangup webhook %s: %s", call_uuid, {k: data.get(k) for k in ("CallStatus", "HangupCause", "HangupCauseCode", "Duration")})
    background.add_task(_handle_hangup, call_uuid, data)  # respond < 3 s, Vobiz retries otherwise
    return PlainTextResponse("ok")


@router.post("/record-start/{call_uuid}/{token}")
async def record_start(call_uuid: str, token: str):
    return PlainTextResponse("ok")


@router.post("/recording/{call_uuid}/{token}")
async def recording_ready(call_uuid: str, token: str, request: Request):
    if not vobiz.verify_token(call_uuid, token):
        return PlainTextResponse("forbidden", status_code=403)
    data = await _payload(request)
    url = data.get("RecordUrl") or data.get("RecordFile") or data.get("record_url") or data.get("recording_url")
    if url:
        await run_db(lambda: update_call(call_uuid, recording_url=str(url)))
    return PlainTextResponse("ok")


@router.post("/stream-status/{call_uuid}/{token}")
async def stream_status(call_uuid: str, token: str, request: Request):
    data = await _payload(request)
    log.info("stream status %s: %s %s", call_uuid, data.get("Event"), data.get("Error") or "")
    return PlainTextResponse("ok")


# ------------------------------------------------------------------ inbound (static application URLs)
def _inbound_forbidden(what: str, request: Request) -> PlainTextResponse:
    """Counted and reported by the inbound setup check: usually Vobiz still calling an old tunnel / salt URL."""
    vobiz.note_inbound_rejection()
    log.warning("inbound %s with a wrong secret from %s", what, request.client.host if request.client else "?")
    return PlainTextResponse("forbidden", status_code=403)


async def _inbound_payload(request: Request) -> dict:
    """Form / JSON body and query string together (the application may be set to GET or POST)."""
    data = {k: v for k, v in request.query_params.items()}
    if request.method != "GET":
        data.update(await _payload(request))
    return {str(k): ("" if v is None else str(v)) for k, v in data.items()}


def _is_hangup_callback(data: dict) -> bool:
    """Without a hangup_url on the application, Vobiz posts hangups to the answer URL too."""
    if str(data.get("Event") or "").strip().lower() == "hangup":
        return True
    if str(data.get("HangupCause") or "").strip() or str(data.get("HangupCauseCode") or "").strip():
        return True
    return str(data.get("CallStatus") or "").strip().lower() in _ENDED_STATUSES


@router.api_route("/inbound/{token}/answer", methods=["GET", "POST"])
async def inbound_answer(token: str, request: Request, background: BackgroundTasks):
    if not vobiz.verify_inbound_token(token):
        return _inbound_forbidden("answer", request)
    try:
        data = await _inbound_payload(request)
        if _is_hangup_callback(data):
            background.add_task(manager.inbound_hangup, data)  # reply at once; nothing is created
            return PlainTextResponse("ok")
        direction = str(data.get("Direction") or "").strip().lower()
        if direction and direction != "inbound":
            log.warning("inbound answer URL got a %s call (%s): refused", direction, data.get("CallUUID"))
            return PlainTextResponse("not an inbound call", status_code=400)
        if not str(data.get("CallUUID") or "").strip():
            log.warning("inbound answer without CallUUID: %s", {k: data.get(k) for k in ("From", "To", "CallStatus")})
            return PlainTextResponse("CallUUID missing", status_code=400)
        xml = await manager.answer_inbound(data)  # never raises
    except Exception:  # noqa: BLE001  (belt and braces: never a 500, never invalid XML)
        log.exception("inbound answer crashed")
        xml = manager.inbound_failure_xml()
    return _xml(xml)


@router.api_route("/inbound/{token}/hangup", methods=["GET", "POST"])
async def inbound_hangup(token: str, request: Request, background: BackgroundTasks):
    if not vobiz.verify_inbound_token(token):
        return _inbound_forbidden("hangup", request)
    data = await _inbound_payload(request)
    log.info("inbound hangup %s: %s", data.get("CallUUID"),
             {k: data.get(k) for k in ("CallStatus", "HangupCause", "HangupCauseCode", "Duration")})
    background.add_task(manager.inbound_hangup, data)  # respond < 3 s, Vobiz retries otherwise
    return PlainTextResponse("ok")


@router.get("/inbound/{token}/audio/{key}.wav")
async def inbound_audio_clip(token: str, key: str, request: Request):
    """Our own voice for the unavailable message (<Play> is only issued for a clip that is cached)."""
    if not vobiz.verify_inbound_token(token):
        return _inbound_forbidden("audio", request)
    data = inbound_audio.wav(key)
    if data is None:
        return PlainTextResponse("not found", status_code=404)
    return Response(content=data, media_type="audio/wav")


# ------------------------------------------------------------------ media stream
def _drop_line(call_uuid: str, provider_call_id: str | None) -> None:
    """A stream for a call we already closed: unless Vobiz told us the line is down, hang it up, or with
    keepCallAlive the caller could sit on a silent (billed) line."""
    if provider_call_id and call_uuid not in manager.hangup_received:
        spawn(vobiz.hangup_call(provider_call_id))


class VobizTransport(Transport):
    channel = "phone"
    in_rate = 8000
    out_rate = 8000
    stt_encoding = "mulaw"
    CHUNK = 480  # 60 ms of mu-law @ 8 kHz

    def __init__(self, ws: WebSocket, stream_id: str, provider_call_id: str | None, call_uuid: str = ""):
        self.ws = ws
        self.stream_id = stream_id
        self.provider_call_id = provider_call_id
        self.call_uuid = call_uuid
        self._lock = asyncio.Lock()          # one websocket message at a time
        self._audio_lock = asyncio.Lock()    # one audio clip at a time (keeps clip chunks contiguous)
        self.cleared = asyncio.Event()
        self.cleared.set()
        self.closed = False

    async def _send(self, msg: dict) -> None:
        if self.closed:
            return
        async with self._lock:
            try:
                await self.ws.send_text(json.dumps(msg))
            except Exception:  # noqa: BLE001
                self.closed = True

    async def send_audio(self, pcm16: bytes) -> None:
        if self.closed or not pcm16:
            return
        if not self.cleared.is_set():  # Vobiz: wait for clearedAudio before new audio
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.cleared.wait(), 0.5)
            self.cleared.set()
        mu = pcm16_to_ulaw(pcm16)
        async with self._audio_lock:
            for i in range(0, len(mu), self.CHUNK):
                if not self.cleared.is_set():
                    return  # barge-in happened mid-clip: drop the rest
                await self._send({
                    "event": "playAudio",
                    "streamId": self.stream_id,
                    "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000,
                              "payload": base64.b64encode(mu[i:i + self.CHUNK]).decode()},
                })

    async def clear(self) -> None:
        self.cleared.clear()
        await self._send({"event": "clearAudio", "streamId": self.stream_id})

    def user_pcm16(self, raw: bytes) -> bytes:
        return ulaw_to_pcm16(raw)

    async def close(self, reason: str) -> None:
        """Make sure the phone line really drops, unless Vobiz already told us it did.

        A closed media socket is NOT proof the customer hung up (tunnel blip, network drop):
        with keepCallAlive the PSTN leg could otherwise stay open, silent and billed.
        """
        if self.call_uuid not in manager.hangup_received:
            await self._send({"event": "stop", "streamId": self.stream_id})
            pid = self.provider_call_id
            call_uuid = self.call_uuid

            async def backup_hangup() -> None:
                await asyncio.sleep(1.5)
                if pid and call_uuid not in manager.hangup_received:
                    await vobiz.hangup_call(pid)  # 404 if already gone: fine

            spawn(backup_hangup())
        self.closed = True


@router.websocket("/stream/{call_uuid}/{token}")
async def media_stream(ws: WebSocket, call_uuid: str, token: str):
    await ws.accept()
    if not vobiz.verify_token(call_uuid, token):
        await ws.close(code=1008)
        return
    # wait for the 'start' event (carries streamId)
    start = None
    try:
        while start is None:
            msg = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=15))
            if msg.get("event") == "start":
                start = msg.get("start") or {}
    except Exception:  # noqa: BLE001
        log.warning("stream %s closed before start", call_uuid)
        return

    stream_id = start.get("streamId") or ""
    row = await run_db(_call_row, call_uuid)
    if row is None or row["status"] not in ("initiated", "ringing", "in_progress") or call_uuid in manager.sessions:
        # unknown / already finished call, or a duplicate stream connection: never start a 2nd AI
        log.warning("rejecting stream for %s (status=%s, live=%s)", call_uuid, row and row["status"],
                    call_uuid in manager.sessions)
        if row is not None and call_uuid not in manager.sessions:
            _drop_line(call_uuid, row["provider"] or start.get("callId"))
        await ws.close(code=1008)
        return
    prep = await manager.take_prepared(call_uuid)
    # The caller may have hung up while the greeting was being prepared: never start an AI on a dead line.
    # Guarded claim: in_progress (+ answered_at on inbound calls, set here: the caller hears us from now on)
    # only while the call is still active.
    claimed = None
    if call_uuid not in manager.hangup_received:
        claimed = await run_db(manager.mark_in_progress, call_uuid)
    if claimed is None or call_uuid in manager.hangup_received:
        log.info("stream for %s arrived after the call ended: closing", call_uuid)
        _drop_line(call_uuid, row["provider"] or start.get("callId"))
        await ws.close(code=1008)
        return
    call_id = await run_db(manager.call_id_for, call_uuid)
    transport = VobizTransport(ws, stream_id, row["provider"] or start.get("callId"), call_uuid)
    session = CallSession(call_id=call_id, call_uuid=call_uuid, lead=prep["lead"], cfg=prep["cfg"],
                          transport=transport, greeting_pcm=prep["greeting_pcm"], on_finished=manager.session_finished,
                          direction=prep["direction"], memory=prep["memory"], greeting_text=prep["greeting_text"],
                          purpose=prep["purpose"])
    if not manager.register(session):
        await ws.close(code=1008)
        return
    if call_uuid in manager.hangup_received:  # the hangup slipped in between the claim and the registration
        manager.sessions.pop(call_uuid, None)
        await ws.close(code=1008)
        return
    run_task = asyncio.create_task(session.run())
    try:
        while not session.ending:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                ev = msg.get("event")
                payload = (msg.get("media") or {}).get("payload") if ev == "media" else None
                audio = base64.b64decode(payload) if payload else b""
            except (ValueError, TypeError, AttributeError):
                continue  # one malformed frame must not end the call
            if ev == "media":
                if audio:
                    session.on_user_audio(audio)
            elif ev == "clearedAudio":
                transport.cleared.set()
            elif ev == "dtmf":
                log.info("dtmf on %s: %s", call_uuid, (msg.get("dtmf") or {}).get("digit"))
            elif ev == "stop":
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:  # noqa: BLE001
        log.exception("media stream error %s", call_uuid)
    finally:
        transport.closed = True
        if not session.ending:
            await session.end("user_hangup")
        with contextlib.suppress(Exception):
            await run_task
