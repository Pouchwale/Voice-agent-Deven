"""Back-fill inbound calls that rang while this backend or the tunnel was offline (Vobiz call records),
so every caller shows up as a missed call the team can call back.

Also keeps the few facts about the inbound connection that must survive a restart (our application id,
the application the number used before we connected, when we connected) in the agent-settings row under
"_inbound_*" keys (settings load/save keep keys they don't know).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid as uuidlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db import SessionLocal, utcnow
from ..defaults import SETTINGS_VERSION
from ..models import AgentSettings, Call
from ..schemas import call_list_out
from . import agent_config, vobiz
from .call_manager import OFFLINE_SUMMARY, inbound_caller, resolve_inbound_lead
from .events import bus
from .phone import caller_e164
from .repo import run_db

log = logging.getLogger("inbound.sync")
settings = get_settings()

INTERVAL_SEC = 600                 # every 10 minutes
FIRST_RUN_DELAY_SEC = 30           # at startup, once the tunnel is up and live webhooks have settled
LOOKBACK = timedelta(days=3)       # never back-fill older calls than this
SETTLE = timedelta(minutes=2)      # a call that ended this recently may still be with the live webhooks
SAME_CALL_WINDOW = timedelta(minutes=2)  # a record this close to a stored inbound call from that number is it
META_KEYS = ("_inbound_app_id", "_inbound_previous_app_id", "_inbound_connected_at", "_inbound_synced_until")

_task: asyncio.Task | None = None


# ---------------------------------------------------------------- persisted connection facts
def load_meta() -> dict:
    with SessionLocal() as db:
        row = db.get(AgentSettings, 1)
        data = row.data if row is not None and isinstance(row.data, dict) else {}
        return {k: data.get(k) for k in META_KEYS}


def save_meta(**values) -> None:
    """Merge into the settings row (None removes a key), under the settings lock so a concurrent settings
    save can't drop it."""
    unknown = set(values) - set(META_KEYS)
    if unknown:
        raise ValueError(f"unknown inbound meta keys: {unknown}")
    with agent_config._lock, SessionLocal() as db:
        row = db.get(AgentSettings, 1)
        data = dict(row.data) if row is not None and isinstance(row.data, dict) else {"_version": SETTINGS_VERSION}
        for k, v in values.items():
            if v is None:
                data.pop(k, None)
            else:
                data[k] = v
        if row is None:
            db.add(AgentSettings(id=1, data=data))
        else:
            row.data = data
        db.commit()


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- back-fill
def _anchor(meta: dict) -> datetime | None:
    """Where this pass starts: the high-water mark of the last successful pass, never before the moment we
    connected (calls before that went to another application). Deliberately NOT the newest stored call: a live
    call that arrives before the next pass would otherwise hide every earlier missed call for good.
    Without a mark or a connect time (number linked by hand) the first pass starts at the newest inbound call
    we know. None: nothing to compare with yet."""
    synced = _parse_time(meta.get("_inbound_synced_until"))
    connected = _parse_time(meta.get("_inbound_connected_at"))
    candidates = [t for t in (synced, connected) if t is not None]
    if candidates:
        return max(candidates)
    with SessionLocal() as db:
        return db.scalar(select(func.max(Call.created_at)).where(Call.direction == "inbound", Call.channel == "phone"))


async def _number_is_ours(number: str, meta: dict) -> bool:
    """Only while our DID is attached to OUR application are its calls ours to back-fill."""
    num = await vobiz.find_number(number)
    app_id = str((num or {}).get("application_id") or "")
    if not app_id:
        return False
    if meta.get("_inbound_app_id"):
        return app_id == str(meta["_inbound_app_id"])
    answer_url = vobiz.inbound_urls()["answer"]
    return any(str(a.get("app_id") or "") == app_id
               and (a.get("app_name") == vobiz.INBOUND_APP_NAME or a.get("answer_url") == answer_url)
               for a in await vobiz.list_applications())


def _store_missed(records: list[dict], our_number: str, start: datetime, now: datetime) -> list[dict]:
    """Insert a missed inbound row for every record we have no call for. Returns the new call dicts."""
    cfg = agent_config.load_agent_settings()
    added: list[dict] = []
    with SessionLocal() as db:
        known = set(db.scalars(select(Call.provider_call_id).where(
            Call.direction == "inbound", Call.provider_call_id.is_not(None),
            Call.created_at >= start - timedelta(days=1))).all())
    for rec in records:
        pid = str(rec.get("uuid") or rec.get("call_uuid") or "").strip()
        if not pid or pid in known or str(rec.get("call_direction") or "inbound").lower() != "inbound":
            continue
        dest = caller_e164(rec.get("destination_number"))
        if dest and dest != our_number:
            continue
        started = _parse_time(rec.get("start_time"))
        if started is None or started < start - SAME_CALL_WINDOW or started > now:
            continue
        ended = _parse_time(rec.get("end_time")) or started
        if now - ended < SETTLE:
            continue  # the next pass takes it if the live webhooks didn't
        caller = inbound_caller(rec.get("caller_id_number"), our_number, rec.get("caller_id_name"), cfg)
        with SessionLocal() as db:
            # Our own record of the same call under another id (CallUUID vs record uuid): never a duplicate.
            twin = db.scalar(select(Call.id).where(
                Call.direction == "inbound", Call.channel == "phone", Call.from_number == caller["from_number"],
                Call.created_at >= started - SAME_CALL_WINDOW, Call.created_at <= started + SAME_CALL_WINDOW).limit(1))
            if twin is not None:
                continue
            try:
                lead, caller_type = resolve_inbound_lead(db, caller, started)
                call = Call(uuid=uuidlib.uuid4().hex, provider_call_id=pid, lead_id=lead.id if lead else None,
                            channel="phone", direction="inbound", caller_type=caller_type, status="no_answer",
                            end_reason="offline", from_number=caller["from_number"], to_number=our_number,
                            started_at=started, ended_at=ended, created_at=started, analysis_status="skipped",
                            summary=OFFLINE_SUMMARY)
                if lead is not None and (lead.last_called_at is None or lead.last_called_at < started):
                    lead.last_called_at = started  # = last contact
                db.add(call)
                db.commit()
            except IntegrityError:  # stored meanwhile by the live webhook (one row per CallUUID)
                db.rollback()
                continue
            db.refresh(call)
            known.add(pid)
            added.append(call_list_out(call))
            log.warning("back-filled inbound call from %s at %s (agent was offline)",
                        caller["from_number"] or "a hidden number", started.isoformat())
    return added


async def sync_once() -> int:
    """One back-fill pass. Returns the number of missed calls added. Errors are logged, never raised."""
    if not settings.vobiz_ready:
        return 0
    number = caller_e164(settings.vobiz_from_number)
    if not number:
        return 0
    try:
        meta = await run_db(load_meta)
        anchor = await run_db(_anchor, meta)
        if anchor is None or not await _number_is_ours(number, meta):
            return 0
        now = utcnow()
        start = max(anchor, now - LOOKBACK)
        # record dates may be in IST or UTC: ask a day wider on both sides, filter exactly here
        records = await vobiz.inbound_call_records(number, (start - timedelta(days=1)).date().isoformat(),
                                                   (now + timedelta(days=1)).date().isoformat())
        added = await run_db(_store_missed, records, number, start, now)
        # Everything that ended before now - SETTLE is settled: the next pass starts there (records that
        # ended later were skipped above and are picked up next time). Duplicates are prevented by the
        # CallUUID / twin checks, so overlapping windows are harmless.
        await run_db(lambda: save_meta(_inbound_synced_until=(now - SETTLE).isoformat()))
    except vobiz.VobizError as e:
        log.warning("inbound back-fill skipped: %s", e)
        return 0
    except Exception:  # noqa: BLE001
        log.exception("inbound back-fill failed")
        return 0
    for out in added:
        bus.publish({"type": "call.update", "call": out})
    return len(added)


async def _run_forever() -> None:
    await asyncio.sleep(FIRST_RUN_DELAY_SEC)
    while True:
        await sync_once()
        await asyncio.sleep(INTERVAL_SEC)


def start() -> None:
    """Start the periodic sync (at startup, then every ~10 min) when enabled and Vobiz is configured."""
    global _task
    if not (settings.inbound_cdr_sync and settings.vobiz_ready):
        return
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_run_forever())


async def stop() -> None:
    global _task
    task, _task = _task, None
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
