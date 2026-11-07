"""Vobiz webhooks + bidirectional media stream (mu-law 8 kHz)."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select

from ..db import SessionLocal, utcnow
from ..models import ACTIVE_CALL_STATUSES, Call
from ..services import vobiz
from ..services.audio import pcm16_to_ulaw, ulaw_to_pcm16
from ..services.call_manager import manager
from ..services.events import spawn
from ..services.repo import run_db, update_call
from ..services.session import CallSession, Transport

log = logging.getLogger("vobiz.router")
router = APIRouter(prefix="/vobiz", tags=["vobiz"])


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
@router.post("/answer/{call_uuid}/{token}")
async def answer(call_uuid: str, token: str, request: Request):
    if not vobiz.verify_token(call_uuid, token):
        return PlainTextResponse("forbidden", status_code=403)
    data = await _payload(request)
    row = await run_db(_call_row, call_uuid)
    if not row or row["status"] not in ("queued", "initiated", "ringing", "in_progress"):
        # cancelled/finished before the customer picked up (or a late retry): hang up, no AI
        log.info("answer for inactive call %s (%s): hanging up", call_uuid, row and row["status"])
        return Response(content='<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
                        media_type="application/xml")
    fields = {"status": "in_progress"}
    if not row["answered"]:
        fields["answered_at"] = utcnow()
    if data.get("CallUUID"):
        fields["provider_call_id"] = data["CallUUID"]
    await run_db(lambda: update_call(call_uuid, **fields))
    from ..services.agent_config import load_agent_settings
    xml = vobiz.answer_xml(call_uuid, int(load_agent_settings().get("max_call_sec") or 300))
    return Response(content=xml, media_type="application/xml")


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


# ------------------------------------------------------------------ media stream
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
        await ws.close(code=1008)
        return
    cfg, lead, greeting = await manager.take_prepared(call_uuid)
    call_id = await run_db(manager.call_id_for, call_uuid)
    transport = VobizTransport(ws, stream_id, row["provider"] or start.get("callId"), call_uuid)
    session = CallSession(call_id=call_id, call_uuid=call_uuid, lead=lead, cfg=cfg, transport=transport,
                          greeting_pcm=greeting, on_finished=manager.session_finished)
    if not manager.register(session):
        await ws.close(code=1008)
        return
    fields = {"status": "in_progress"}
    if not row["answered"]:
        fields["answered_at"] = utcnow()
    await run_db(lambda: update_call(call_uuid, **fields))
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
