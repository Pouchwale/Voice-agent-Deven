"""Lifecycle of calls: start (Vobiz / browser), live session registry, cleanup, analysis hand-off."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid as uuidlib
from datetime import timedelta
from typing import Awaitable, Callable

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..config import get_settings
from ..db import SessionLocal, utcnow
from ..models import ACTIVE_CALL_STATUSES, Call, CampaignLead, Lead
from ..schemas import call_list_out, lead_out
from . import vobiz
from .agent_config import load_agent_settings
from .analysis import analyze_call
from .events import bus, spawn
from .prompts import fill_template
from .repo import run_db
from .sarvam import tts_rest
from .session import CallSession

log = logging.getLogger("calls")
settings = get_settings()

DoneHook = Callable[[str, str], Awaitable[None]]  # (call_uuid, outcome: connected|no_answer|busy|failed)


class CallError(Exception):
    def __init__(self, message: str, status_code: int = 400, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable  # provider-side problem: retry later, don't count the attempt


class CallManager:
    def __init__(self) -> None:
        self.sessions: dict[str, CallSession] = {}
        self.prepared: dict[str, dict] = {}  # phone calls waiting for the media stream
        self.done_hooks: list[DoneHook] = []
        self._finished: set[str] = set()
        self._analyzed: set[str] = set()
        self.hangup_received: set[str] = set()
        self._watchdog_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ helpers
    def provider_problems(self, phone: bool = True) -> list[str]:
        problems = []
        if not settings.openai_ready:
            problems.append("OPENAI_API_KEY is not set")
        if not settings.sarvam_ready:
            problems.append("SARVAM_API_KEY is not set")
        if phone and not settings.vobiz_ready:
            problems.append("Vobiz is not configured (VOBIZ_AUTH_ID / VOBIZ_AUTH_TOKEN / VOBIZ_FROM_NUMBER)")
        if phone and settings.public_base_url.startswith(("http://localhost", "http://127.")):
            problems.append("PUBLIC_BASE_URL is localhost — Vobiz cannot reach it (use an https tunnel / domain)")
        return problems

    def active_count(self, campaign_id: int | None = None) -> int:
        with SessionLocal() as db:
            q = select(Call.id).where(Call.status.in_(ACTIVE_CALL_STATUSES))
            if campaign_id is not None:
                q = q.where(Call.campaign_id == campaign_id)
            return len(db.scalars(q).all())

    # ------------------------------------------------------------------ phone
    async def start_phone_call(self, lead_id: int, campaign_id: int | None = None,
                               call_uuid: str | None = None) -> dict:
        problems = self.provider_problems(phone=True)
        if problems:
            raise CallError("Cannot place call: " + "; ".join(problems))
        cfg = load_agent_settings()
        call_uuid = call_uuid or uuidlib.uuid4().hex

        def create() -> tuple[dict, dict]:
            with SessionLocal() as db:
                lead = db.get(Lead, lead_id)
                if not lead:
                    raise CallError("Lead not found", 404)
                if lead.status == "dnc":
                    raise CallError("Lead is marked Do-Not-Call", 409)
                busy = db.scalar(select(Call.id).where(Call.lead_id == lead_id,
                                                       Call.status.in_(ACTIVE_CALL_STATUSES)))
                if busy:
                    raise CallError("This lead already has a call in progress", 409)
                call = Call(uuid=call_uuid, lead_id=lead.id, campaign_id=campaign_id, channel="phone",
                            status="queued", from_number=settings.vobiz_from_number, to_number=lead.phone,
                            started_at=utcnow())
                lead.status = "calling"
                lead.attempts = (lead.attempts or 0) + 1
                lead.last_called_at = utcnow()
                db.add(call)
                try:
                    db.commit()
                except IntegrityError:
                    # uq_calls_active_lead: a concurrent request created the live call first
                    db.rollback()
                    raise CallError("This lead already has a call in progress", 409) from None
                db.refresh(call)
                return call_list_out(call), lead_out(lead)

        out, lead = await run_db(create)
        bus.publish({"type": "call.update", "call": out})
        greeting_text = fill_template(cfg.get("opening_line", ""), cfg, lead).strip()
        greeting_task = asyncio.create_task(tts_rest(greeting_text, cfg, 8000)) if greeting_text else None
        self.prepared[call_uuid] = {"cfg": cfg, "lead": lead, "greeting": greeting_task}
        try:
            provider_id = await vobiz.make_call(call_uuid, lead["phone"], int(cfg.get("max_call_sec") or 300),
                                                caller_name=cfg.get("company_name", ""))
        except Exception as e:  # noqa: BLE001
            self.prepared.pop(call_uuid, None)
            if greeting_task:
                greeting_task.cancel()
            retryable = isinstance(e, httpx.HTTPError) or (isinstance(e, vobiz.VobizError) and e.retryable)
            await self.finish_unanswered(call_uuid, "failed", "error", error=str(e)[:500],
                                         outcome="provider_error" if retryable else "failed")
            raise CallError(str(e), 502, retryable=retryable) from e
        return await run_db(lambda: self._update(call_uuid, status="initiated", provider_call_id=provider_id))

    def _update(self, call_uuid: str, **fields) -> dict:
        from .repo import update_call
        return update_call(call_uuid, **fields) or {}

    async def take_prepared(self, call_uuid: str) -> tuple[dict, dict | None, bytes | None]:
        """Config, lead and pre-synthesised greeting for a phone call whose stream just connected."""
        prep = self.prepared.pop(call_uuid, None)
        if prep is None:  # e.g. server restarted between dial and answer
            cfg = load_agent_settings()

            def lead_for() -> dict | None:
                with SessionLocal() as db:
                    call = db.scalar(select(Call).options(selectinload(Call.lead)).where(Call.uuid == call_uuid))
                    return lead_out(call.lead) if call and call.lead else None

            return cfg, await run_db(lead_for), None
        greeting = None
        task = prep.get("greeting")
        if task is not None:
            with contextlib.suppress(Exception):
                greeting = await asyncio.wait_for(task, timeout=4)
        return prep["cfg"], prep["lead"], greeting

    def call_id_for(self, call_uuid: str) -> int | None:
        with SessionLocal() as db:
            return db.scalar(select(Call.id).where(Call.uuid == call_uuid))

    # ------------------------------------------------------------------ browser
    async def create_browser_call(self, lead_id: int | None, name: str | None, company: str | None) -> tuple[dict, dict]:
        problems = self.provider_problems(phone=False)
        if problems:
            raise CallError("Cannot start test call: " + "; ".join(problems))
        call_uuid = uuidlib.uuid4().hex

        def create() -> tuple[dict, dict]:
            with SessionLocal() as db:
                lead = db.get(Lead, lead_id) if lead_id else None
                lead_d = lead_out(lead) if lead else {"name": name or "", "company": company or "", "city": ""}
                call = Call(uuid=call_uuid, lead_id=lead.id if lead else None, channel="browser",
                            status="in_progress", started_at=utcnow(), answered_at=utcnow(),
                            to_number=lead.phone if lead else None)
                db.add(call)
                db.commit()
                db.refresh(call)
                return call_list_out(call), lead_d

        out, lead_d = await run_db(create)
        bus.publish({"type": "call.update", "call": out})
        return out, lead_d

    # ------------------------------------------------------------------ registry
    def register(self, session: CallSession) -> bool:
        """Returns False if this call already has a live session (duplicate stream connection)."""
        if session.uuid in self.sessions:
            return False
        self.sessions[session.uuid] = session
        return True

    async def session_finished(self, session: CallSession) -> None:
        if self.sessions.get(session.uuid) is session:
            del self.sessions[session.uuid]
        if session.user_words > 0:
            outcome = "connected"
        elif session.end_reason == "error" and session.agent_audio_ms == 0:
            outcome = "provider_error"  # our failure, the customer heard nothing: retry, don't count
        else:
            outcome = "no_answer"

        def lead_update() -> None:
            with SessionLocal() as db:
                call = db.scalar(select(Call).where(Call.uuid == session.uuid))
                if call and call.lead_id:
                    lead = db.get(Lead, call.lead_id)
                    if lead and lead.status != "dnc":
                        lead.status = "called"
                        db.commit()

        await run_db(lead_update)
        spawn(self._analyze_then_hooks(session.uuid, outcome))

    async def _analyze_then_hooks(self, call_uuid: str, outcome: str) -> None:
        await self._run_hooks(call_uuid, outcome)
        if call_uuid in self._analyzed:  # a duplicate end path must not pay for a second analysis
            return
        self._analyzed.add(call_uuid)
        if len(self._analyzed) > 5000:
            self._analyzed.clear()
        try:
            await analyze_call(call_uuid)
        except Exception:  # noqa: BLE001
            log.exception("analysis crashed")

    async def _run_hooks(self, call_uuid: str, outcome: str) -> None:
        if call_uuid in self._finished:
            return
        self._finished.add(call_uuid)
        if len(self._finished) > 5000:
            self._finished = set(list(self._finished)[-1000:])
        for hook in self.done_hooks:
            try:
                await hook(call_uuid, outcome)
            except Exception:  # noqa: BLE001
                log.exception("done hook failed")

    async def finish_unanswered(self, call_uuid: str, status: str, end_reason: str, error: str | None = None,
                                duration: float | None = None, outcome: str | None = None) -> None:
        """Call ended before a conversation (no answer / busy / failed / canceled). Idempotent."""
        self.prepared.pop(call_uuid, None)

        def upd() -> dict | None:
            with SessionLocal() as db:
                call = db.scalar(select(Call).options(selectinload(Call.lead)).where(Call.uuid == call_uuid))
                if not call or call.status not in ACTIVE_CALL_STATUSES:
                    return None
                call.status = status
                call.end_reason = end_reason
                call.ended_at = utcnow()
                call.analysis_status = "skipped"
                if duration is not None:
                    call.duration_sec = duration
                if error:
                    call.error = error
                if call.lead and call.lead.status != "dnc":
                    call.lead.status = "failed" if status == "failed" else "called"
                db.commit()
                db.refresh(call)
                return call_list_out(call)

        out = await run_db(upd)
        if out:
            bus.publish({"type": "call.update", "call": out})
            outcome = outcome or {"no_answer": "no_answer", "busy": "busy", "canceled": "no_answer"}.get(status, "failed")
            await self._run_hooks(call_uuid, outcome)

    async def hangup(self, call_uuid: str) -> bool:
        session = self.sessions.get(call_uuid)
        if session:
            await session.end("manual_hangup")
            return True

        def provider() -> str | None:
            with SessionLocal() as db:
                return db.scalar(select(Call.provider_call_id).where(Call.uuid == call_uuid))

        pid = await run_db(provider)
        if pid:
            await vobiz.hangup_call(pid)
        await self.finish_unanswered(call_uuid, "canceled", "manual_hangup")
        return True

    # ------------------------------------------------------------------ watchdog
    def recover_on_startup(self) -> tuple[int, list[str]]:
        """State left behind by a previous process: close calls, free leads and campaign items.

        Returns (calls closed, uuids of answered calls that still need their analysis).
        """
        with SessionLocal() as db:
            stale = db.scalars(select(Call).where(Call.status.in_(ACTIVE_CALL_STATUSES))).all()
            to_analyze: list[str] = []
            for c in stale:
                c.status = "failed" if c.answered_at is None else "completed"
                c.end_reason = c.end_reason or "error"
                c.ended_at = c.ended_at or utcnow()
                c.error = " ".join(filter(None, [c.error, "Server restarted during the call."]))
                if c.answered_at is None:
                    c.analysis_status = "skipped"
            for c in db.scalars(select(Call).where(Call.status == "completed",
                                                   Call.analysis_status == "pending")).all():
                to_analyze.append(c.uuid)
            db.execute(update(Lead).where(Lead.status == "calling").values(status="called"))
            db.execute(update(CampaignLead).where(CampaignLead.status == "dialing")
                       .values(status="pending", next_attempt_at=None))
            db.commit()
            return len(stale), to_analyze

    def mark_hangup_received(self, call_uuid: str) -> None:
        """The provider told us the line is down (no REST hang-up needed afterwards)."""
        self.hangup_received.add(call_uuid)
        if len(self.hangup_received) > 5000:
            self.hangup_received.clear()

    async def watchdog(self) -> None:
        while True:
            await asyncio.sleep(15)
            try:
                await self._sweep()
            except Exception:  # noqa: BLE001
                log.exception("watchdog sweep failed")

    async def _sweep(self) -> None:
        now = utcnow()

        def stale() -> list[tuple[str, str]]:
            out = []
            with SessionLocal() as db:
                for c in db.scalars(select(Call).where(Call.status.in_(ACTIVE_CALL_STATUSES),
                                                       Call.channel == "phone")).all():
                    if c.uuid in self.sessions:
                        continue
                    started = c.started_at or c.created_at
                    if c.status in ("queued", "initiated", "ringing") and now - started > timedelta(seconds=150):
                        out.append((c.uuid, "no_answer"))
                    elif c.status == "in_progress" and now - (c.answered_at or started) > timedelta(seconds=45):
                        out.append((c.uuid, "stream_failed"))
            return out

        for call_uuid, kind in await run_db(stale):
            if kind == "no_answer":
                await self.finish_unanswered(call_uuid, "no_answer", "no_answer")
            else:
                await self.finish_unanswered(call_uuid, "failed", "error",
                                             error="Call answered but the audio stream never connected "
                                                   "(check PUBLIC_BASE_URL / websocket reachability).")

    def start_watchdog(self) -> None:
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self.watchdog())

    async def shutdown(self) -> None:
        if self._watchdog_task:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        for s in list(self.sessions.values()):
            with contextlib.suppress(Exception):
                await s.end("error")


manager = CallManager()
