"""Live monitor feed, listen-in audio and browser test calls (WebSockets)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..db import SessionLocal
from ..models import ACTIVE_CALL_STATUSES, Call
from ..schemas import call_list_out
from ..services.agent_config import load_agent_settings
from ..services.call_manager import CallError, manager
from ..services.events import bus
from ..services.repo import run_db
from ..services.session import CallSession, Transport

log = logging.getLogger("live")
router = APIRouter(tags=["live"])


def active_calls() -> list[dict]:
    with SessionLocal() as db:
        rows = db.scalars(select(Call).options(selectinload(Call.lead))
                          .where(Call.status.in_(ACTIVE_CALL_STATUSES)).order_by(Call.id.desc())).all()
        return [call_list_out(c) for c in rows]


@router.websocket("/ws/live")
async def live_feed(ws: WebSocket):
    await ws.accept()
    q = bus.subscribe()
    try:
        await ws.send_text(json.dumps({"type": "snapshot", "calls": await run_db(active_calls)}))

        async def reader() -> None:
            while True:
                msg = await ws.receive_text()
                with contextlib.suppress(Exception):
                    if json.loads(msg).get("type") == "ping":
                        await ws.send_text('{"type":"pong"}')

        read_task = asyncio.create_task(reader())
        try:
            while not read_task.done():
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20)
                except asyncio.TimeoutError:
                    msg = '{"type":"pong"}'  # keep proxies from closing an idle socket
                await ws.send_text(msg)
        finally:
            read_task.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(q)


@router.websocket("/ws/listen/{call_uuid}")
async def listen_in(ws: WebSocket, call_uuid: str):
    await ws.accept()
    session = manager.sessions.get(call_uuid)
    if session is None:
        await ws.send_text(json.dumps({"type": "error", "detail": "Call is not live"}))
        await ws.close()
        return
    q = bus.subscribe_audio(call_uuid)
    try:
        await ws.send_text(json.dumps({"type": "format", "sample_rate": session.rec_rate}))
        while not session.ended.is_set():
            try:
                frame = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if isinstance(frame, bytes):
                await ws.send_bytes(frame)
            else:
                await ws.send_text(frame)
        await ws.send_text(json.dumps({"type": "ended"}))
        await ws.close()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe_audio(call_uuid, q)


class BrowserTransport(Transport):
    channel = "browser"
    in_rate = 16000
    out_rate = 24000
    stt_encoding = "linear16"

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self._lock = asyncio.Lock()
        self.closed = False

    async def _send(self, *, text: str | None = None, data: bytes | None = None) -> None:
        if self.closed:
            return
        async with self._lock:
            try:
                if data is not None:
                    await self.ws.send_bytes(data)
                else:
                    await self.ws.send_text(text or "")
            except Exception:  # noqa: BLE001
                self.closed = True

    async def send_audio(self, pcm16: bytes) -> None:
        await self._send(data=pcm16)

    async def clear(self) -> None:
        await self._send(text='{"type":"clear"}')

    async def send_event(self, data: dict) -> None:
        await self._send(text=json.dumps(data, ensure_ascii=False))

    async def close(self, reason: str) -> None:
        await self.send_event({"type": "ended", "reason": reason})
        self.closed = True
        with contextlib.suppress(Exception):
            await self.ws.close()


@router.websocket("/ws/browser-call")
async def browser_call(ws: WebSocket):
    await ws.accept()
    try:
        start = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=20))
    except Exception:  # noqa: BLE001
        await ws.close()
        return
    if start.get("type") != "start":
        await ws.send_text(json.dumps({"type": "error", "detail": "First message must be {type:'start'}"}))
        await ws.close()
        return
    # direction "inbound" = "Customer calls us" rehearsal: inbound greeting, purpose and memory
    direction = "inbound" if str(start.get("direction") or "").strip().lower() == "inbound" else "outbound"
    try:
        lead_id = int(start["lead_id"]) if start.get("lead_id") else None
        call, lead = await manager.create_browser_call(lead_id, start.get("name"), start.get("company"),
                                                       direction=direction)
    except (CallError, ValueError, TypeError) as e:
        await ws.send_text(json.dumps({"type": "error", "detail": str(e) if isinstance(e, CallError) else "Bad lead_id"}))
        await ws.close()
        return

    transport = BrowserTransport(ws)
    cfg = load_agent_settings()
    call_id = await run_db(manager.call_id_for, call["uuid"])
    extras = await manager.browser_session_extras(call, lead, cfg)
    session = CallSession(call_id=call_id, call_uuid=call["uuid"], lead=lead, cfg=cfg, transport=transport,
                          on_finished=manager.session_finished, direction=extras["direction"],
                          memory=extras["memory"], greeting_text=extras["greeting_text"], purpose=extras["purpose"])
    manager.register(session)
    await transport.send_event({"type": "ready", "uuid": call["uuid"], "input_sample_rate": transport.in_rate,
                                "output_sample_rate": transport.out_rate})
    run_task = asyncio.create_task(session.run())
    try:
        while not session.ending:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes"):
                if len(msg["bytes"]) <= 64_000:  # ~2 s of 16 kHz PCM; bigger frames are not a mic
                    session.on_user_audio(msg["bytes"])
            elif msg.get("text"):
                with contextlib.suppress(json.JSONDecodeError):
                    if json.loads(msg["text"]).get("type") == "hangup":
                        await session.end("manual_hangup")
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:  # noqa: BLE001
        log.exception("browser call error")
    finally:
        if not session.ending:
            transport.closed = True  # browser is gone: nothing more to send
            await session.end("user_hangup")
        with contextlib.suppress(Exception):
            await run_task
