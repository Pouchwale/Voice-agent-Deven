"""Campaign auto-dialer: keeps N concurrent calls per running campaign, retries, calling window.

Robustness rules:
  * every campaign item gets its call uuid when it is marked 'dialing', so it can always be
    matched to its call (or to the absence of one) later;
  * each item is dialed in isolation: an unexpected error re-queues that item instead of
    aborting the tick and leaving others stuck in 'dialing';
  * provider-side failures (Vobiz 429/5xx, network, our own TTS/STT outage) do not use up a
    lead's attempts; three in a row pause the campaign so a list isn't burned during an outage;
  * customers come first: a lead on a live call (either direction) or who called us in the last
    30 minutes is left for later, a few lines stay free for callers while inbound is on, and an
    inbound conversation settles that lead's pending campaign items;
  * "Outbound calling" off in Settings: nothing is dialed, running campaigns stay running and
    resume by themselves when it is switched back on.
"""
from __future__ import annotations

import asyncio
import logging
import uuid as uuidlib
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select

from ..config import get_settings
from ..db import SessionLocal, utcnow
from ..models import ACTIVE_CALL_STATUSES, Call, Campaign, CampaignLead, Lead
from ..schemas import campaign_out
from .agent_config import load_agent_settings
from .call_manager import CallError, manager
from .campaign_stats import calls_placed, outcome_counts
from .events import bus
from .repo import run_db

log = logging.getLogger("dialer")
settings = get_settings()
GLOBAL_MAX_CONCURRENT = 10        # phone calls at once, both directions (inbound may use all of them)
INBOUND_RESERVE = 2               # lines campaigns leave free for customers calling in (when inbound is on)
INBOUND_COOLDOWN = timedelta(minutes=30)  # never cold-call someone who has just called us
PROVIDER_ERROR_PAUSE = 3          # consecutive provider errors before a campaign pauses
PROVIDER_RETRY_DELAY = timedelta(minutes=2)

# (campaign_id, item_id, lead_id, planned call uuid, the item's previous last_call_uuid)
Planned = tuple[int, int, int, str, str | None]


def campaign_dict(db, c: Campaign) -> dict:
    rows = db.execute(
        select(CampaignLead.status, func.count()).where(CampaignLead.campaign_id == c.id).group_by(CampaignLead.status)
    ).all()
    counts = {"total": 0, "pending": 0, "dialing": 0, "done": 0, "failed": 0, "skipped": 0}
    for st, n in rows:
        counts[st] = counts.get(st, 0) + n
        counts["total"] += n
    irows = db.execute(
        select(Lead.interest, func.count()).join(CampaignLead, CampaignLead.lead_id == Lead.id)
        .where(CampaignLead.campaign_id == c.id).group_by(Lead.interest)
    ).all()
    interest = {"unknown": 0, "cold": 0, "warm": 0, "hot": 0}
    for k, n in irows:
        interest[k] = interest.get(k, 0) + n
    active = db.scalar(select(func.count()).select_from(Call).where(
        Call.campaign_id == c.id, Call.status.in_(ACTIVE_CALL_STATUSES))) or 0
    return campaign_out(c, counts, interest, active, outcome_counts(db, c.id), calls_placed(db, c.id))


def publish_campaign(campaign_id: int) -> dict | None:
    with SessionLocal() as db:
        c = db.get(Campaign, campaign_id)
        if not c:
            return None
        out = campaign_dict(db, c)
    bus.publish({"type": "campaign.update", "campaign": out})
    return out


def in_call_window(cfg: dict) -> bool:
    try:
        now = datetime.now(ZoneInfo(settings.timezone)).strftime("%H:%M")
    except Exception:  # noqa: BLE001  (tzdata missing on some Windows installs)
        now = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%H:%M")
    start = cfg.get("call_window_start") or "00:00"
    end = cfg.get("call_window_end") or "23:59"
    return start <= now <= end if start <= end else (now >= start or now <= end)


class Dialer:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._provider_errors: dict[int, int] = defaultdict(int)  # campaign_id -> consecutive count

    def start(self) -> None:
        if self.on_call_done not in manager.done_hooks:
            manager.done_hooks.append(self.on_call_done)
        if self._task is None or self._task.done():
            self._loop = asyncio.get_running_loop()
            self._wake = asyncio.Event()  # bind to the current event loop
            self._task = asyncio.create_task(self._loop_forever())

    def wake(self) -> None:
        """Safe to call from any thread (routers run in a threadpool)."""
        if self._loop is None or self._loop.is_closed():
            return
        try:
            on_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            on_loop = False
        if on_loop:
            self._wake.set()
        else:
            self._loop.call_soon_threadsafe(self._wake.set)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop_forever(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception:  # noqa: BLE001
                log.exception("dialer tick failed")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=3)
            except asyncio.TimeoutError:
                pass

    # ---------------------------------------------------------------- planning
    @staticmethod
    def _heal_stuck(db, now) -> None:
        """'dialing' items whose call already ended (missed hook) or never got created.

        Safe because ticks are sequential: every dial of the previous tick has completed (or
        failed) before the next plan runs, so a 'dialing' item without a live call is stuck.
        """
        rows = db.execute(
            select(CampaignLead, Call).outerjoin(Call, Call.uuid == CampaignLead.last_call_uuid)
            .where(CampaignLead.status == "dialing")
        ).all()
        for it, call in rows:
            if call is not None and call.status in ACTIVE_CALL_STATUSES:
                continue
            camp = db.get(Campaign, it.campaign_id)
            talked = call is not None and call.status == "completed" and (call.metrics or {}).get("user_words", 0) > 0
            if talked:
                it.status = "done"
            elif call is None:  # dial never happened (crash between plan and dial): not an attempt
                it.status = "pending"
                it.attempts = max(0, it.attempts - 1)
            elif call.end_reason == "superseded":
                # the customer called us while we dialed them: not an attempt (same as _settle "superseded",
                # whose hook may simply not have run yet when this tick got here first)
                it.status = "pending"
                it.attempts = max(0, it.attempts - 1)
                it.next_attempt_at = now + timedelta(minutes=camp.retry_delay_min if camp else 30)
            elif camp and it.attempts < camp.max_attempts:
                it.status = "pending"
                it.next_attempt_at = now + timedelta(minutes=camp.retry_delay_min)
            else:
                it.status = "failed"

    @staticmethod
    def _free_leads(now) -> list:
        """Conditions on (CampaignLead, Lead) leaving out leads that must not be dialed right now: on a live
        phone call in either direction, or they called us in the last 30 minutes (an inbound conversation
        settles their item; otherwise they are dialed after the cool-down). Unlinked inbound calls (lead_id
        NULL while the lead was on another call) are matched by number."""
        recent = now - INBOUND_COOLDOWN
        live = select(Call.lead_id).where(Call.lead_id.is_not(None), Call.channel == "phone",
                                          Call.status.in_(ACTIVE_CALL_STATUSES))
        called_us = select(Call.lead_id).where(Call.lead_id.is_not(None), Call.channel == "phone",
                                               Call.direction == "inbound", Call.created_at >= recent)

        def inbound_numbers(when):  # (NOT IN needs a NULL-free list; separate queries keep the indexes usable)
            return select(Call.from_number).where(Call.channel == "phone", Call.direction == "inbound",
                                                  Call.from_number.is_not(None), Call.from_number != "", when)

        return [CampaignLead.lead_id.not_in(live), CampaignLead.lead_id.not_in(called_us),
                Lead.phone.not_in(inbound_numbers(Call.status.in_(ACTIVE_CALL_STATUSES))),
                Lead.phone.not_in(inbound_numbers(Call.created_at >= recent))]

    def _plan(self, window_ok: bool, reserve: int = 0) -> list[Planned]:
        """Items to dial now (marked 'dialing'). `reserve` lines stay free for inbound callers."""
        todo: list[Planned] = []
        now = utcnow()
        with SessionLocal() as db:
            self._heal_stuck(db, now)
            global_active = db.scalar(select(func.count()).select_from(Call).where(
                Call.status.in_(ACTIVE_CALL_STATUSES), Call.channel == "phone")) or 0
            budget = max(0, GLOBAL_MAX_CONCURRENT - reserve - global_active)
            free = self._free_leads(now)
            for c in db.scalars(select(Campaign).where(Campaign.status == "running")).all():
                remaining = db.scalar(select(func.count()).select_from(CampaignLead).where(
                    CampaignLead.campaign_id == c.id, CampaignLead.status.in_(("pending", "dialing")))) or 0
                if remaining == 0:
                    c.status = "completed"
                    c.completed_at = now
                    continue
                if (c.respect_call_window and not window_ok) or budget <= 0:
                    continue
                active = db.scalar(select(func.count()).select_from(Call).where(
                    Call.campaign_id == c.id, Call.status.in_(ACTIVE_CALL_STATUSES))) or 0
                dialing = db.scalar(select(func.count()).select_from(CampaignLead).where(
                    CampaignLead.campaign_id == c.id, CampaignLead.status == "dialing")) or 0
                slots = min(budget, max(0, c.concurrency - max(active, dialing)))
                if slots <= 0:
                    continue
                items = db.scalars(
                    select(CampaignLead).join(Lead, Lead.id == CampaignLead.lead_id).where(
                        CampaignLead.campaign_id == c.id, CampaignLead.status == "pending",
                        or_(CampaignLead.next_attempt_at.is_(None), CampaignLead.next_attempt_at <= now),
                        *free,
                    ).order_by(CampaignLead.attempts, CampaignLead.id).limit(slots)
                ).all()
                for it in items:
                    lead = db.get(Lead, it.lead_id)
                    if lead is None or lead.status == "dnc":
                        it.status = "skipped"
                        continue
                    previous = it.last_call_uuid
                    it.status = "dialing"
                    it.attempts += 1
                    it.last_call_uuid = uuidlib.uuid4().hex
                    todo.append((c.id, it.id, it.lead_id, it.last_call_uuid, previous))
                    budget -= 1
            db.commit()
        return todo

    async def _tick(self) -> None:
        cfg = load_agent_settings()
        if not cfg.get("outbound_enabled", True):
            return  # switched off: campaigns stay 'running' and carry on once it is switched back on
        reserve = INBOUND_RESERVE if cfg.get("inbound_enabled", True) else 0
        todo = await run_db(self._plan, in_call_window(cfg), reserve)
        touched = set()
        for i, (campaign_id, item_id, lead_id, call_uuid, previous) in enumerate(todo):
            touched.add(campaign_id)
            if not load_agent_settings().get("outbound_enabled", True):  # switched off mid-tick
                await run_db(self._requeue, todo[i:])
                break
            try:
                await manager.start_phone_call(lead_id, campaign_id=campaign_id, call_uuid=call_uuid)
            except CallError as e:
                log.warning("campaign %s lead %s: %s", campaign_id, lead_id, e)
                if e.reason == "outbound_off":  # not a problem with the list: no pause, no attempt used
                    await run_db(self._requeue, todo[i:])
                    break
                if e.reason in ("superseded", "canceled"):
                    continue  # the call row exists and its done-hook already settled this item
                if e.reason == "lead_busy":  # on another call right now: try again later, not an attempt
                    await run_db(self._requeue_later, item_id, call_uuid, previous)
                elif e.status_code == 400:  # configuration problem: don't burn the list
                    await run_db(self._set_item, item_id, {"status": "pending", "attempts_delta": -1,
                                                           "last_call_uuid": previous}, call_uuid)
                    await run_db(self._pause, campaign_id, f"paused: {e}")
                elif e.status_code in (404, 409):  # lead deleted / Do-Not-Call
                    await run_db(self._set_item, item_id, {"status": "skipped"}, call_uuid)
                # 502 (provider/dial failure): the call row exists and the done-hook already
                # settled this item (retry, provider-error back-off or failed)
            except Exception:  # noqa: BLE001
                log.exception("campaign %s lead %s: unexpected dial error", campaign_id, lead_id)
                await run_db(self._set_item, item_id, {"status": "pending", "attempts_delta": -1,
                                                        "next_attempt_at": utcnow() + PROVIDER_RETRY_DELAY}, call_uuid)
        for cid in touched:
            await run_db(publish_campaign, cid)

    # ---------------------------------------------------------------- db helpers
    @staticmethod
    def _set_item(item_id: int, fields: dict, call_uuid: str | None = None) -> None:
        """With call_uuid: only an item still 'dialing' that call (a hook may have settled it already)."""
        with SessionLocal() as db:
            it = db.get(CampaignLead, item_id)
            if not it or (call_uuid is not None and (it.status != "dialing" or it.last_call_uuid != call_uuid)):
                return
            fields = dict(fields)
            delta = fields.pop("attempts_delta", 0)
            it.attempts = max(0, it.attempts + delta)
            for k, v in fields.items():
                setattr(it, k, v)
            db.commit()

    @staticmethod
    def _requeue(entries: list[Planned]) -> None:
        """Planned items that were never dialed: back to pending, as if this tick never happened."""
        with SessionLocal() as db:
            for _cid, item_id, _lead, call_uuid, previous in entries:
                it = db.get(CampaignLead, item_id)
                if it and it.status == "dialing" and it.last_call_uuid == call_uuid:
                    it.status = "pending"
                    it.attempts = max(0, it.attempts - 1)
                    it.last_call_uuid = previous
            db.commit()

    @staticmethod
    def _requeue_later(item_id: int, call_uuid: str, previous: str | None) -> None:
        with SessionLocal() as db:
            it = db.get(CampaignLead, item_id)
            if not it or it.status != "dialing" or it.last_call_uuid != call_uuid:
                return
            camp = db.get(Campaign, it.campaign_id)
            it.status = "pending"
            it.attempts = max(0, it.attempts - 1)
            it.last_call_uuid = previous
            it.next_attempt_at = utcnow() + timedelta(minutes=camp.retry_delay_min if camp else 30)
            db.commit()

    @staticmethod
    def _pause(campaign_id: int, why: str = "") -> None:
        with SessionLocal() as db:
            c = db.get(Campaign, campaign_id)
            if c and c.status == "running":
                c.status = "paused"
                db.commit()
                log.warning("campaign %s %s", campaign_id, why)

    @staticmethod
    def _settle(call_uuid: str, outcome: str) -> tuple[int | None, bool]:
        """Update the campaign item of a finished call. Returns (campaign_id, provider_error)."""
        with SessionLocal() as db:
            it = db.scalar(select(CampaignLead).where(CampaignLead.last_call_uuid == call_uuid))
            if not it or it.status != "dialing":
                return (it.campaign_id if it else None), False
            c = db.get(Campaign, it.campaign_id)
            if outcome == "connected":
                it.status = "done"
            elif outcome == "provider_error":
                it.status = "pending"
                it.attempts = max(0, it.attempts - 1)  # not the lead's fault
                it.next_attempt_at = utcnow() + PROVIDER_RETRY_DELAY
            elif outcome == "superseded":
                # the customer called us while we dialed them: not an attempt, and never a provider error
                # (supersedes must not pause the campaign); the inbound cool-down also holds the redial
                it.status = "pending"
                it.attempts = max(0, it.attempts - 1)
                it.next_attempt_at = utcnow() + timedelta(minutes=c.retry_delay_min if c else 30)
            elif c and it.attempts < c.max_attempts:
                it.status = "pending"
                it.next_attempt_at = utcnow() + timedelta(minutes=c.retry_delay_min)
            else:
                it.status = "failed"
            db.commit()
            return it.campaign_id, outcome == "provider_error"

    @staticmethod
    def _courtesy_settle(call_uuid: str) -> list[int]:
        """An inbound call that ended in a conversation: the customer has spoken with us, so their PENDING
        items in running / paused campaigns are done (no cold call right after they rang us). Draft and
        completed campaigns are left alone. Returns the campaigns that changed."""
        with SessionLocal() as db:
            call = db.scalar(select(Call).where(Call.uuid == call_uuid))
            if (call is None or call.direction != "inbound" or call.channel != "phone" or not call.lead_id
                    or call.status != "completed" or not (call.metrics or {}).get("user_words")):
                return []
            items = db.scalars(select(CampaignLead).join(Campaign, Campaign.id == CampaignLead.campaign_id).where(
                CampaignLead.lead_id == call.lead_id, CampaignLead.status == "pending",
                Campaign.status.in_(("running", "paused")))).all()
            for it in items:
                it.status = "done"
                it.last_call_uuid = call.uuid
                it.next_attempt_at = None
            db.commit()
            return sorted({it.campaign_id for it in items})

    async def on_call_done(self, call_uuid: str, outcome: str) -> None:
        cid, provider_error = await run_db(self._settle, call_uuid, outcome)
        if cid:
            if provider_error:
                self._provider_errors[cid] += 1
                if self._provider_errors[cid] >= PROVIDER_ERROR_PAUSE:
                    self._provider_errors[cid] = 0
                    await run_db(self._pause, cid,
                                 f"paused after {PROVIDER_ERROR_PAUSE} provider errors in a row "
                                 "(check Settings > Test providers)")
            elif outcome == "connected":
                self._provider_errors[cid] = 0
            await run_db(publish_campaign, cid)
        if outcome == "connected":  # before wake(): the next tick must already see these items as done
            for courtesy_cid in await run_db(self._courtesy_settle, call_uuid):
                await run_db(publish_campaign, courtesy_cid)
        self.wake()


dialer = Dialer()
