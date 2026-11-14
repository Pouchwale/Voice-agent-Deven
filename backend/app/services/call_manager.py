"""Lifecycle of calls: start (Vobiz outbound, inbound answer, browser), live session registry, cleanup,
analysis hand-off.

Inbound calls (a customer dials our Vobiz number) are answered by answer_inbound(): one row per Vobiz
CallUUID, the caller found by an exact E.164 match, the AI's availability gated, and everything slow
(personalised greeting TTS, caller memory) started in the background and handed to the media stream
through `prepared`, so the answer webhook replies within milliseconds.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import re
import time
import uuid as uuidlib
from datetime import timedelta
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx
from sqlalchemy import and_, func, literal, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..config import get_settings
from ..db import SessionLocal, UTCDateTime, utcnow
from ..defaults import DEFAULT_AGENT_SETTINGS
from ..models import ACTIVE_CALL_STATUSES, Call, CampaignLead, Lead, TranscriptTurn
from ..schemas import call_list_out, lead_out
from . import inbound_audio, vobiz
from .agent_config import load_agent_settings
from .analysis import analyze_call
from .events import bus, spawn
from .memory import caller_memory
from .phone import caller_e164
from .prompts import fill_template
from .repo import run_db
from .sarvam import speakable, tts_rest
from .session import CallSession

log = logging.getLogger("calls")
settings = get_settings()

# (call_uuid, outcome: connected | no_answer | busy | failed | provider_error | superseded)
DoneHook = Callable[[str, str], Awaitable[None]]

RINGING_STATUSES = ("queued", "initiated", "ringing")  # an outbound call nobody has picked up yet
INBOUND_RATE_LIMIT = 5                       # more inbound calls than this from one number ...
INBOUND_RATE_WINDOW = timedelta(minutes=10)  # ... within this window are rejected as busy (robocalls, spam)
EARLY_HANGUP_TTL = 120.0                     # s a hangup that arrived before its answer request is remembered
INBOUND_GREETING_WAIT = 1.0                  # s the personalised greeting may still take once the stream is up
OUTBOUND_GREETING_WAIT = 4.0                 # outbound greetings were synthesised while the phone rang
MEMORY_WAIT = 1.5
CALLBACK_WINDOW = timedelta(days=7)          # our call returns a missed inbound call this recent
_OUR_SIDE_HANGUP = {"4010", "7011", "8011"}  # stream ended / answer URL unreachable / invalid XML

FAILED_SUMMARY = "Call failed on our side — call back"
HUNG_UP_SUMMARY = "Caller hung up before the agent answered"
RESTART_SUMMARY = "Call dropped (server restarted) — call back"
OFFLINE_SUMMARY = "Missed while the agent was offline — call back"
SUPERSEDED_SUMMARY = "Cancelled: the customer called us at the same moment"
RATE_LIMIT_SUMMARY = (f"Missed inbound call — rejected: more than {INBOUND_RATE_LIMIT} calls from this number "
                      "in 10 minutes")
_UNAVAILABLE_SUMMARY = {
    "inbound_off": "Missed inbound call — inbound calling was switched off",
    "lines_busy": "Missed inbound call — all lines were busy",
}


class CallError(Exception):
    def __init__(self, message: str, status_code: int = 400, retryable: bool = False, reason: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable  # provider-side problem: retry later, don't count the attempt
        # what the dialer needs to know: outbound_off | not_found | dnc | lead_busy | superseded | canceled
        self.reason = reason


# ---------------------------------------------------------------------- shared definitions (SQL)
def conversation_clause():
    """A phone call where the customer actually talked (status completed, user_words > 0)."""
    return and_(Call.channel == "phone", Call.status == "completed",
                func.coalesce(func.json_extract(Call.metrics, "$.user_words"), 0) > 0)


def calls_of(lead: Lead):
    """The lead's calls, plus inbound calls from their number that were stored without the lead."""
    return or_(Call.lead_id == lead.id,
               and_(Call.lead_id.is_(None), Call.direction == "inbound", Call.from_number == lead.phone))


def had_conversation(db, lead: Lead) -> bool:
    return db.scalar(select(Call.id).where(calls_of(lead), conversation_clause()).limit(1)) is not None


def callback_due(db, lead: Lead) -> bool:
    """Our call returns a missed inbound call: their latest inbound call of the last 7 days was missed and
    nobody has spoken with them since. The greeting then apologises instead of cold-pitching."""
    last_in = db.scalar(select(Call).where(calls_of(lead), Call.channel == "phone", Call.direction == "inbound",
                                           Call.created_at >= utcnow() - CALLBACK_WINDOW)
                        .order_by(Call.created_at.desc(), Call.id.desc()).limit(1))
    if last_in is None or last_in.status in ACTIVE_CALL_STATUSES or last_in.status == "completed":
        return False
    spoke_since = db.scalar(select(Call.id).where(calls_of(lead), conversation_clause(),
                                                  Call.created_at > last_in.created_at).limit(1))
    return spoke_since is None


# ---------------------------------------------------------------------- inbound caller
_NAME = re.compile(r"^[A-Za-z][A-Za-z .'\-]{1,59}$")
_NOT_A_NAME = {"anonymous", "unknown", "private", "restricted", "unavailable", "withheld", "blocked", "caller",
               "wireless caller", "no caller id", "out of area", "unknown caller", "vobiz"}


def caller_id_name(raw: Any, cfg: dict) -> str | None:
    """A CallerName that looks like a person's or business's real name (kept for the team, never spoken)."""
    name = " ".join(str(raw or "").split())
    if not _NAME.match(name) or name.lower() in _NOT_A_NAME:
        return None
    ours = {str(cfg.get(k) or "").strip().lower() for k in ("company_name", "agent_name")} - {""}
    return None if name.lower() in ours else name


def inbound_caller(raw_from: Any, raw_to: Any, raw_name: Any, cfg: dict) -> dict:
    """Who is calling. kind: None (usable number: the lead lookup decides) | anonymous | international.
    Lookups only ever compare Lead.phone with the exact E.164 (never suffix matching)."""
    e164 = caller_e164(raw_from)
    to_number = caller_e164(raw_to) or caller_e164(settings.vobiz_from_number) or (settings.vobiz_from_number or None)
    ours = {n for n in (caller_e164(raw_to), caller_e164(settings.vobiz_from_number)) if n}
    if e164 is None or e164 in ours:
        kind = "anonymous"  # hidden / unusable, or our own DID: never a lead (a campaign could dial itself)
    elif not e164.startswith("+91") and not settings.allow_international:
        kind = "international"  # number kept on the call, no lead
    else:
        kind = None
    digits = re.sub(r"\D", "", str(raw_from or ""))[:32]
    return {"lead_phone": e164 if kind is None else None, "from_number": e164 or digits, "kind": kind,
            "to_number": to_number, "name": caller_id_name(raw_name, cfg)}


def resolve_inbound_lead(db, caller: dict, when) -> tuple[Lead | None, str]:
    """The caller's lead (created for a new number) and their caller_type. Lead creation commits on its own:
    when a parallel first call from the same number wins the unique phone, its lead is used."""
    if caller["kind"]:
        return None, caller["kind"]
    phone = caller["lead_phone"]
    lead = db.scalar(select(Lead).where(Lead.phone == phone))
    created = False
    if lead is None:
        lead = Lead(name="", phone=phone, source="Inbound call", status="new", interest="unknown", attempts=0,
                    last_called_at=when)
        db.add(lead)
        try:
            db.commit()
            created = True
        except IntegrityError:
            db.rollback()
            lead = db.scalar(select(Lead).where(Lead.phone == phone))
            if lead is None:
                raise
    if caller.get("name"):
        extra = dict(lead.extra) if isinstance(lead.extra, dict) else {}
        if extra.get("caller_id_name") != caller["name"]:
            extra["caller_id_name"] = caller["name"]  # caller ID can be spoofed: for the team only
            lead.extra = extra
            db.commit()
    if created:
        return lead, "new"
    return lead, ("returning" if had_conversation(db, lead) else "known")


def inbound_greeting(cfg: dict, lead: dict | None, caller_type: str | None) -> str:
    """'Nice to talk again' only for callers who really spoke with us before; everyone else gets the
    first-caller greeting (with their name when the number is a lead with a usable name)."""
    key = "inbound_greeting_known" if caller_type == "returning" else "inbound_greeting"
    return fill_template(cfg.get(key) or DEFAULT_AGENT_SETTINGS[key], cfg, lead).strip()


def relink_inbound(db, call: Call) -> None:
    """An inbound call stored without its lead (the lead was on another live call at that moment) gets the
    lead back once it has ended: the one-live-call-per-lead index only covers active rows."""
    if (call.lead_id or call.direction != "inbound" or call.channel != "phone" or not call.from_number
            or call.status in ACTIVE_CALL_STATUSES or call.caller_type not in ("returning", "known", "new")):
        return
    lead_id = db.scalar(select(Lead.id).where(Lead.phone == call.from_number))
    if lead_id:
        call.lead_id = lead_id


def _missed_by_hangup(data: dict) -> tuple[str, str, str]:
    """(status, end_reason, summary) of an inbound call that ended before its media stream started."""
    if str(data.get("HangupCauseCode") or "").strip() in _OUR_SIDE_HANGUP:
        return "failed", "error", FAILED_SUMMARY
    return "no_answer", "user_hangup", HUNG_UP_SUMMARY  # the caller hung up / cancelled while it connected


def _inbound_missed_summary(status: str, end_reason: str) -> str:
    if status == "failed":
        return FAILED_SUMMARY
    if end_reason == "user_hangup":
        return HUNG_UP_SUMMARY
    if end_reason == "manual_hangup":
        return "Ended from the dashboard before the agent answered"
    return "Missed inbound call — call back"


def _replay_info(call: Call) -> dict:
    return {"kind": "replay", "uuid": call.uuid, "status": call.status, "end_reason": call.end_reason,
            "reply": (call.metrics or {}).get("inbound_reply") if isinstance(call.metrics, dict) else None}


async def settled(task: asyncio.Future | None, timeout: float) -> Any:
    """Result of a background task within `timeout`, else None. Never cancels the task itself."""
    if task is None:
        return None
    try:
        return await asyncio.wait_for(asyncio.shield(task), max(0.0, timeout))
    except asyncio.TimeoutError:
        return None
    except asyncio.CancelledError:
        if task.cancelled():
            return None  # the task was dropped, not us
        raise
    except Exception:  # noqa: BLE001
        return None


class CallManager:
    def __init__(self) -> None:
        self.sessions: dict[str, CallSession] = {}
        self.prepared: dict[str, dict] = {}  # phone calls waiting for the media stream
        self.done_hooks: list[DoneHook] = []
        self._finished: set[str] = set()
        self._analyzed: set[str] = set()
        self.hangup_received: set[str] = set()
        self._watchdog_task: asyncio.Task | None = None
        # Vobiz CallUUID -> [lock, users]: one answer / hangup handler at a time per inbound call
        self._inbound_locks: dict[str, list] = {}
        # Vobiz CallUUID -> (monotonic time, payload): hangups that arrived before their answer request
        self._early_hangups: dict[str, tuple[float, dict]] = {}

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

    def inbound_blockers(self) -> list[str]:
        """Why the AI can't take an inbound call right now (callers then get the unavailable reply)."""
        out = []
        if not settings.openai_ready:
            out.append("OPENAI_API_KEY is not set")
        if not settings.sarvam_ready:
            out.append("SARVAM_API_KEY is not set")
        if not settings.public_base_url.startswith("https://"):
            out.append("PUBLIC_BASE_URL is not https")
        return out

    def active_count(self, campaign_id: int | None = None) -> int:
        with SessionLocal() as db:
            q = select(Call.id).where(Call.status.in_(ACTIVE_CALL_STATUSES))
            if campaign_id is not None:
                q = q.where(Call.campaign_id == campaign_id)
            return len(db.scalars(q).all())

    def _tts_task(self, text: str, cfg: dict) -> asyncio.Task | None:
        """8 kHz greeting synthesis in the background (None result on failure: the session retries)."""
        if not text:
            return None

        async def synth() -> bytes | None:
            try:
                return await tts_rest(text, cfg, 8000)
            except Exception as e:  # noqa: BLE001
                log.warning("greeting TTS failed: %s", e)
                return None

        return asyncio.create_task(synth())

    def _memory_task(self, lead_id: int | None, call_uuid: str, from_number: str | None,
                     direction: str) -> asyncio.Task | None:
        """Caller memory built in a worker thread (a failure just means an empty memory)."""
        if not lead_id and not from_number:
            return None

        async def build() -> str:
            try:
                return await run_db(lambda: caller_memory(lead_id, call_uuid, from_number=from_number,
                                                          direction=direction)) or ""
            except Exception:  # noqa: BLE001
                log.exception("caller memory failed for %s", call_uuid)
                return ""

        return asyncio.create_task(build())

    def _drop_prepared(self, call_uuid: str) -> None:
        prep = self.prepared.pop(call_uuid, None)
        for key in ("greeting", "memory"):
            task = (prep or {}).get(key)
            if task is not None and not task.done():
                task.cancel()

    # ------------------------------------------------------------------ phone (outbound)
    async def start_phone_call(self, lead_id: int, campaign_id: int | None = None,
                               call_uuid: str | None = None) -> dict:
        cfg = load_agent_settings()
        if not cfg.get("outbound_enabled", True):
            raise CallError("Outbound calling is turned off in Settings.", 400, reason="outbound_off")
        problems = self.provider_problems(phone=True)
        if problems:
            raise CallError("Cannot place call: " + "; ".join(problems))
        call_uuid = call_uuid or uuidlib.uuid4().hex

        def create() -> tuple[dict, dict, bool]:
            with SessionLocal() as db:
                lead = db.get(Lead, lead_id)
                if not lead:
                    raise CallError("Lead not found", 404, reason="not_found")
                if lead.status == "dnc":
                    raise CallError("Lead is marked Do-Not-Call", 409, reason="dnc")
                # calls_of: also an inbound call from their number stored without the lead (a redial while
                # another call was live) — never dial someone who is talking to the agent right now
                busy = db.scalar(select(Call.id).where(calls_of(lead), Call.channel == "phone",
                                                       Call.status.in_(ACTIVE_CALL_STATUSES)).limit(1))
                if busy:
                    raise CallError("This lead already has a call in progress", 409, reason="lead_busy")
                callback = callback_due(db, lead)
                call = Call(uuid=call_uuid, lead_id=lead.id, campaign_id=campaign_id, channel="phone",
                            direction="outbound", status="queued", from_number=settings.vobiz_from_number,
                            to_number=lead.phone, started_at=utcnow())
                lead.status = "calling"
                lead.attempts = (lead.attempts or 0) + 1
                lead.last_called_at = utcnow()
                db.add(call)
                try:
                    db.commit()
                except IntegrityError:
                    # uq_calls_active_lead: a concurrent request (or an inbound call) took the lead first
                    db.rollback()
                    raise CallError("This lead already has a call in progress", 409, reason="lead_busy") from None
                db.refresh(call)
                return call_list_out(call), lead_out(lead), callback

        out, lead, callback = await run_db(create)
        bus.publish({"type": "call.update", "call": out})
        # A call back to someone whose inbound call we missed opens with an apology, not a cold pitch.
        line = cfg.get("callback_greeting") if callback else cfg.get("opening_line")
        greeting_text = fill_template(line or "", cfg, lead).strip()
        if callback and not greeting_text:
            greeting_text = fill_template(cfg.get("opening_line", ""), cfg, lead).strip()
        self.prepared[call_uuid] = {
            "cfg": cfg, "lead": lead, "direction": "outbound", "purpose": "callback" if callback else "outbound",
            "greeting_text": greeting_text, "greeting": self._tts_task(greeting_text, cfg),
            "memory": self._memory_task(lead["id"], call_uuid, None, "outbound"),
        }
        try:
            provider_id = await vobiz.make_call(call_uuid, lead["phone"], int(cfg.get("max_call_sec") or 300),
                                                caller_name=cfg.get("company_name", ""))
        except Exception as e:  # noqa: BLE001
            self._drop_prepared(call_uuid)
            retryable = isinstance(e, httpx.HTTPError) or (isinstance(e, vobiz.VobizError) and e.retryable)
            await self.finish_unanswered(call_uuid, "failed", "error", error=str(e)[:500],
                                         outcome="provider_error" if retryable else "failed")
            raise CallError(str(e), 502, retryable=retryable) from e
        out, active, end_reason = await run_db(self._mark_dialed, call_uuid, provider_id)
        if not active:
            # Closed while Vobiz was dialing (the customer called us at that moment, or a manual hang-up):
            # the real call is ringing anyway, so drop it now instead of resurrecting the row.
            self._drop_prepared(call_uuid)
            if provider_id:
                spawn(vobiz.hangup_call(provider_id))
            if end_reason == "superseded":
                raise CallError("The customer called us at this moment: the AI is talking to them on their call, "
                                "so this call was cancelled.", 409, reason="superseded")
            raise CallError("This call was cancelled while it was being dialed.", 409, reason="canceled")
        bus.publish({"type": "call.update", "call": out})
        return out

    @staticmethod
    def _mark_dialed(call_uuid: str, provider_id: str) -> tuple[dict, bool, str | None]:
        """Guarded queued -> initiated after make_call: a row closed meanwhile is never brought back."""
        with SessionLocal() as db:
            res = db.execute(update(Call).where(Call.uuid == call_uuid, Call.status == "queued")
                             .values(status="initiated", provider_call_id=provider_id)
                             .execution_options(synchronize_session=False))
            if not res.rowcount and provider_id:
                # ring / answer webhooks may have moved it on already: only fill in the provider id
                db.execute(update(Call).where(Call.uuid == call_uuid,
                                              or_(Call.provider_call_id.is_(None), Call.provider_call_id == ""))
                           .values(provider_call_id=provider_id).execution_options(synchronize_session=False))
            db.commit()
            call = db.scalar(select(Call).options(selectinload(Call.lead)).where(Call.uuid == call_uuid))
            if call is None:
                return {}, False, None
            return call_list_out(call), call.status in ACTIVE_CALL_STATUSES, call.end_reason

    def mark_in_progress(self, call_uuid: str, provider_call_id: str | None = None) -> dict | None:
        """Guarded write (worker thread): in_progress, answered_at set the first time, ONLY while the call is
        still active, so a hangup / cancel / supersede that won the race keeps its final state.
        Returns the call dict, or None when the call is no longer active."""
        now = utcnow()
        values: dict[str, Any] = {"status": "in_progress",
                                  "answered_at": func.coalesce(Call.answered_at, literal(now, UTCDateTime()))}
        where = [Call.uuid == call_uuid, Call.status.in_(ACTIVE_CALL_STATUSES)]
        if provider_call_id:  # outbound answer webhook; an inbound row's CallUUID is its identity, never rewritten
            values["provider_call_id"] = provider_call_id
            where.append(Call.direction != "inbound")
        with SessionLocal() as db:
            res = db.execute(update(Call).where(*where).values(**values).execution_options(synchronize_session=False))
            db.commit()
            if not res.rowcount:
                return None
            call = db.scalar(select(Call).options(selectinload(Call.lead)).where(Call.uuid == call_uuid))
            out = call_list_out(call)
        bus.publish({"type": "call.update", "call": out})
        return out

    async def take_prepared(self, call_uuid: str) -> dict:
        """What a phone call's session needs once its media stream connects: cfg, lead, greeting (text +
        pre-synthesised audio), direction, purpose and caller memory. Never cancels the greeting synthesis."""
        prep = self.prepared.pop(call_uuid, None)
        if prep is None:  # defensive: the answer handler failed after storing the call, or a restart
            return await self._rebuild_prepared(call_uuid)
        cfg = prep["cfg"]
        inbound = prep.get("direction") == "inbound"
        task = prep.get("greeting")
        wait = INBOUND_GREETING_WAIT if inbound else OUTBOUND_GREETING_WAIT
        pcm, memory = await asyncio.gather(settled(task, wait), settled(prep.get("memory"), MEMORY_WAIT))
        text = prep.get("greeting_text") or ""
        pcm = prep.get("greeting_pcm") or pcm
        if pcm is None and inbound and text:
            generic_text, generic_pcm = inbound_audio.cached_greeting(cfg)
            if generic_pcm:  # the caller is waiting: the cached generic greeting now beats a personal one later
                text, pcm = generic_text, generic_pcm
            elif task is not None:  # nothing cached: the synthesis already in flight beats starting another
                pcm = await settled(task, OUTBOUND_GREETING_WAIT - wait)
        return {"cfg": cfg, "lead": prep.get("lead"), "greeting_pcm": pcm, "greeting_text": text or None,
                "direction": prep.get("direction") or "outbound", "purpose": prep.get("purpose"),
                "memory": memory or ""}

    async def _rebuild_prepared(self, call_uuid: str) -> dict:
        cfg = load_agent_settings()

        def load() -> dict | None:
            with SessionLocal() as db:
                call = db.scalar(select(Call).options(selectinload(Call.lead)).where(Call.uuid == call_uuid))
                if call is None:
                    return None
                lead = call.lead
                if lead is None and call.direction == "inbound" and call.from_number \
                        and call.caller_type in ("returning", "known", "new"):
                    lead = db.scalar(select(Lead).where(Lead.phone == call.from_number))
                return {"direction": call.direction or "outbound", "caller_type": call.caller_type,
                        "from_number": call.from_number, "lead": lead_out(lead) if lead else None,
                        "callback": bool(call.direction != "inbound" and lead is not None and callback_due(db, lead))}

        info = await run_db(load)
        if info is None:
            return {"cfg": cfg, "lead": None, "greeting_pcm": None, "greeting_text": None, "direction": "outbound",
                    "purpose": "outbound", "memory": ""}
        lead = info["lead"]
        pcm = None
        if info["direction"] == "inbound":
            purpose = "inbound"
            text = inbound_greeting(cfg, lead, info["caller_type"])
            generic_text, generic_pcm = inbound_audio.cached_greeting(cfg)
            pcm = generic_pcm if text == generic_text else None
            number = info["from_number"] if info["caller_type"] != "anonymous" else None
        else:
            purpose = "callback" if info["callback"] else "outbound"
            line = cfg.get("callback_greeting") if info["callback"] else cfg.get("opening_line")
            text = fill_template(line or "", cfg, lead).strip()
            number = None
        memory = await settled(self._memory_task(lead["id"] if lead else None, call_uuid, number, info["direction"]),
                               MEMORY_WAIT)
        if lead is None and number:
            lead = {"phone": number}
        return {"cfg": cfg, "lead": lead, "greeting_pcm": pcm, "greeting_text": text or None,
                "direction": info["direction"], "purpose": purpose, "memory": memory or ""}

    def call_id_for(self, call_uuid: str) -> int | None:
        with SessionLocal() as db:
            return db.scalar(select(Call.id).where(Call.uuid == call_uuid))

    # ------------------------------------------------------------------ browser
    async def create_browser_call(self, lead_id: int | None, name: str | None, company: str | None,
                                  direction: str = "outbound") -> tuple[dict, dict]:
        """A test call in the browser. direction "inbound" rehearses a customer calling US (stored as an
        inbound row, still channel 'browser', so it never counts in KPIs)."""
        problems = self.provider_problems(phone=False)
        if problems:
            raise CallError("Cannot start test call: " + "; ".join(problems))
        call_uuid = uuidlib.uuid4().hex
        inbound = direction == "inbound"

        def create() -> tuple[dict, dict]:
            with SessionLocal() as db:
                lead = db.get(Lead, lead_id) if lead_id else None
                lead_d = lead_out(lead) if lead else {"name": name or "", "company": company or "", "city": ""}
                caller_type = None
                if inbound and lead is not None:
                    caller_type = "returning" if had_conversation(db, lead) else "known"
                call = Call(uuid=call_uuid, lead_id=lead.id if lead else None, channel="browser",
                            direction="inbound" if inbound else "outbound", caller_type=caller_type,
                            status="in_progress", started_at=utcnow(), answered_at=utcnow(),
                            from_number=(lead.phone if lead else None) if inbound else None,
                            to_number=(settings.vobiz_from_number or None) if inbound else (lead.phone if lead else None))
                db.add(call)
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    raise CallError("This lead is on a live call", 409, reason="lead_busy") from None
                db.refresh(call)
                return call_list_out(call), lead_d

        out, lead_d = await run_db(create)
        bus.publish({"type": "call.update", "call": out})
        return out, lead_d

    async def browser_session_extras(self, call: dict, lead: dict, cfg: dict) -> dict:
        """direction / purpose / memory / greeting for a browser test session (same rules as phone calls)."""
        inbound = call.get("direction") == "inbound"
        number = (lead or {}).get("phone") if inbound else None
        memory = await settled(self._memory_task(call.get("lead_id"), call["uuid"], number,
                                                 "inbound" if inbound else "outbound"), MEMORY_WAIT) or ""
        if not inbound:
            return {"direction": "outbound", "purpose": "outbound", "memory": memory, "greeting_text": None}
        return {"direction": "inbound", "purpose": "inbound", "memory": memory,
                "greeting_text": inbound_greeting(cfg, lead, call.get("caller_type"))}

    # ------------------------------------------------------------------ inbound (Vobiz answer_url)
    @contextlib.asynccontextmanager
    async def _inbound_lock(self, provider_id: str) -> AsyncIterator[None]:
        entry = self._inbound_locks.get(provider_id)
        if entry is None:
            entry = self._inbound_locks[provider_id] = [asyncio.Lock(), 0]
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] <= 0 and self._inbound_locks.get(provider_id) is entry:
                del self._inbound_locks[provider_id]

    @staticmethod
    def _inbound_row(db, provider_id: str) -> Call | None:
        return db.scalar(select(Call).where(Call.direction == "inbound", Call.provider_call_id == provider_id)
                         .order_by(Call.id).limit(1))

    async def answer_inbound(self, data: dict) -> str:
        """VobizXML for an inbound answer request (CallUUID required). Never raises: on any failure the caller
        hears the unavailable message and the call is logged as missed, so the team can call back."""
        pid = str(data.get("CallUUID") or "").strip()
        async with self._inbound_lock(pid):  # a Vobiz retry waits for the first attempt, then replays it
            try:
                return await self._answer_inbound(pid, data)
            except Exception:  # noqa: BLE001
                log.exception("inbound answer failed for %s", pid)
                return await self._inbound_failed(pid, data)

    async def _answer_inbound(self, pid: str, data: dict) -> str:
        cfg = load_agent_settings()
        caller = inbound_caller(data.get("From"), data.get("To"), data.get("CallerName"), cfg)
        res = await run_db(self._inbound_db, pid, caller, cfg)
        if res["kind"] == "replay":
            log.info("inbound %s: repeated answer request, replaying the first decision", pid)
            return self._replay_xml(res, cfg)
        if res.get("superseded"):
            self._after_supersede(res["superseded"])
        bus.publish({"type": "call.update", "call": res["call"]})
        call_uuid = res["uuid"]
        if res["kind"] == "unavailable":
            log.info("inbound %s from %s not answered by the AI: %s", pid, caller["from_number"] or "hidden",
                     res["call"].get("summary"))
            return self.unavailable_xml(cfg, res["reply"])
        early = self._take_early_hangup(pid)
        if early is not None:  # the caller hung up before this answer request reached us
            status, end_reason, summary = _missed_by_hangup(early)
            self.mark_hangup_received(call_uuid)
            await self.finish_unanswered(call_uuid, status, end_reason, summary=summary)
            return vobiz.hangup_xml()
        self._prepare_inbound(call_uuid, cfg, res, caller)
        return vobiz.answer_xml(call_uuid, int(cfg.get("inbound_max_call_sec") or 600))

    def _inbound_db(self, pid: str, caller: dict, cfg: dict) -> dict:
        """Everything the answer needs from the database in ONE worker-thread hop: idempotency, the caller's
        lead, the availability gate and the call row."""
        now = utcnow()
        with SessionLocal() as db:
            prior = self._inbound_row(db, pid)
            if prior is not None:
                return _replay_info(prior)
            lead, caller_type = resolve_inbound_lead(db, caller, now)
            gate = self._inbound_gate(db, caller, cfg, lead, now)
            lead_id = lead.id if lead is not None else None
        return self._insert_inbound(pid, caller, lead_id, caller_type, gate, now)

    def _inbound_gate(self, db, caller: dict, cfg: dict, lead: Lead | None, now) -> tuple[str, str, str] | None:
        """Why the AI won't take this call: (end_reason, summary, reply), or None to answer it."""
        if caller["from_number"]:
            # calls that rang while we were offline (back-filled) never reached the agent: a customer who kept
            # trying during an outage is not a spammer
            recent = db.scalar(select(func.count()).select_from(Call).where(
                Call.direction == "inbound", Call.channel == "phone", Call.from_number == caller["from_number"],
                Call.created_at >= now - INBOUND_RATE_WINDOW,
                or_(Call.end_reason.is_(None), Call.end_reason != "offline"))) or 0
            if recent >= INBOUND_RATE_LIMIT:
                log.warning("inbound from %s rejected: %d calls in the last 10 minutes", caller["from_number"], recent)
                return "lines_busy", RATE_LIMIT_SUMMARY, "busy"
        reply = "reject" if cfg.get("inbound_off_action") == "reject" else "message"
        if not cfg.get("inbound_enabled", True):
            return "inbound_off", _UNAVAILABLE_SUMMARY["inbound_off"], reply
        blockers = self.inbound_blockers()
        if blockers:
            log.error("inbound call turned away: %s", "; ".join(blockers))
            return "inbound_off", f"Missed inbound call — the agent couldn't answer ({'; '.join(blockers)})", reply
        from .dialer import GLOBAL_MAX_CONCURRENT  # (dialer imports this module)
        busy = db.scalar(select(func.count()).select_from(Call).where(
            Call.channel == "phone", Call.status.in_(ACTIVE_CALL_STATUSES))) or 0
        if lead is not None:  # the caller's own outbound call that is about to be superseded doesn't count
            own = db.scalar(select(func.count()).select_from(Call).where(
                Call.lead_id == lead.id, Call.channel == "phone", Call.direction != "inbound",
                Call.status.in_(RINGING_STATUSES))) or 0
            busy -= own
        if busy >= GLOBAL_MAX_CONCURRENT:
            return "lines_busy", _UNAVAILABLE_SUMMARY["lines_busy"], reply
        return None

    def _insert_inbound(self, pid: str, caller: dict, lead_id: int | None, caller_type: str,
                        gate: tuple[str, str, str] | None, now) -> dict:
        base = {"uuid": uuidlib.uuid4().hex, "provider_call_id": pid, "channel": "phone", "direction": "inbound",
                "caller_type": caller_type, "from_number": caller["from_number"], "to_number": caller["to_number"],
                "started_at": now}
        for attempt in range(3):
            with SessionLocal() as db:
                prior = self._inbound_row(db, pid)  # (another process stored it: uq_calls_inbound_provider)
                if prior is not None:
                    return _replay_info(prior)
                lead = db.get(Lead, lead_id) if lead_id else None
                link = lead.id if lead is not None else None
                superseded = None
                if gate is not None:  # an ended row: never in the one-live-call-per-lead index
                    end_reason, summary, reply = gate
                    call = Call(**base, lead_id=link, status="no_answer", end_reason=end_reason, ended_at=now,
                                analysis_status="skipped", summary=summary, metrics={"inbound_reply": reply})
                else:
                    if lead is not None:
                        live = None if attempt == 2 else db.scalar(select(Call).where(
                            Call.lead_id == lead.id, Call.channel == "phone",
                            Call.status.in_(ACTIVE_CALL_STATUSES)).limit(1))
                        if attempt == 2:
                            link = None  # repeated conflicts: store it unlinked, re-linked when it ends
                        elif live is not None and live.direction != "inbound" and live.status in RINGING_STATUSES:
                            superseded = self._supersede(db, live, lead, now)
                        elif live is not None:
                            # already on a live call (a redial after a drop, a shared office line): the session
                            # still gets the lead, memory and greeting; the row is re-linked when it ends
                            link = None
                    call = Call(**base, lead_id=link, status="in_progress")
                if lead is not None:
                    lead.last_called_at = now  # = last contact; attempts / status are for OUR calls
                db.add(call)
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    log.info("inbound %s: insert conflict (attempt %d), retrying", pid, attempt + 1)
                    continue
                db.refresh(call)
                return {"kind": "unavailable" if gate else "answer", "uuid": call.uuid, "call": call_list_out(call),
                        "lead": lead_out(lead) if lead is not None else None, "lead_id": lead_id,
                        "caller_type": caller_type, "reply": gate[2] if gate else None, "superseded": superseded}
        raise RuntimeError(f"could not store inbound call {pid}")

    @staticmethod
    def _supersede(db, live: Call, lead: Lead, now) -> dict:
        """Our dialer is calling this customer right now while they call us: cancel our attempt (same
        transaction as the inbound insert) and refund the lead's attempt."""
        live.status = "canceled"
        live.end_reason = "superseded"
        live.ended_at = now
        live.analysis_status = "skipped"
        live.summary = SUPERSEDED_SUMMARY
        lead.attempts = max(0, (lead.attempts or 0) - 1)
        if lead.status == "calling":
            lead.status = "called"
        db.flush()  # the outbound row leaves the active-lead index before the inbound row joins it
        return {"uuid": live.uuid, "provider_call_id": live.provider_call_id, "call": call_list_out(live)}

    def _after_supersede(self, sup: dict) -> None:
        self._drop_prepared(sup["uuid"])
        bus.publish({"type": "call.update", "call": sup["call"]})
        if sup.get("provider_call_id"):
            spawn(vobiz.hangup_call(sup["provider_call_id"]))
        # finish_unanswered ignores rows that are no longer active: settle the campaign item directly
        spawn(self._run_hooks(sup["uuid"], "superseded"))

    def _prepare_inbound(self, call_uuid: str, cfg: dict, res: dict, caller: dict) -> None:
        lead = res["lead"]
        text = inbound_greeting(cfg, lead, res["caller_type"])
        generic_text = inbound_audio.greeting_text(cfg)
        inbound_audio.ensure(generic_text, cfg)  # rebuilt in the background if missing: the next caller gets it
        generic_pcm = inbound_audio.get(inbound_audio.clip_key(generic_text, cfg))
        pcm = generic_pcm if text == generic_text else None
        number = None if caller["kind"] == "anonymous" else (caller["from_number"] or None)
        self.prepared[call_uuid] = {
            "cfg": cfg, "lead": lead or ({"phone": number} if number else {}), "direction": "inbound",
            "purpose": "inbound", "greeting_text": text, "greeting_pcm": pcm,
            "greeting": None if pcm is not None else self._tts_task(text, cfg),
            "memory": self._memory_task(res["lead_id"], call_uuid, number, "inbound"),
        }

    def _replay_xml(self, info: dict, cfg: dict) -> str:
        if info["status"] in ACTIVE_CALL_STATUSES:
            return vobiz.answer_xml(info["uuid"], int(cfg.get("inbound_max_call_sec") or 600))
        if info["end_reason"] in ("inbound_off", "lines_busy"):
            default = "reject" if cfg.get("inbound_off_action") == "reject" else "message"
            return self.unavailable_xml(cfg, info.get("reply") or default)
        return vobiz.hangup_xml()

    def unavailable_xml(self, cfg: dict, reply: str) -> str:
        """busy/reject: busy tone, never answered (not billed). message: our own voice when the clip is
        cached, else Vobiz's Hindi voice right away (and our clip is built for the next caller)."""
        if reply in ("busy", "reject"):
            return vobiz.hangup_xml("busy")
        text = inbound_audio.message_text(cfg)
        key = inbound_audio.clip_key(text, cfg)
        if inbound_audio.get(key) is not None:
            return vobiz.play_xml(vobiz.inbound_audio_url(key))
        inbound_audio.ensure(text, cfg)
        return vobiz.speak_xml(speakable(text, cfg))

    def inbound_failure_xml(self, cfg: dict | None = None) -> str:
        """Our side broke: the caller still hears a polite message (never a 500, never invalid XML)."""
        try:
            cfg = cfg or load_agent_settings()
            return vobiz.speak_xml(speakable(inbound_audio.message_text(cfg), cfg))
        except Exception:  # noqa: BLE001
            try:
                d = DEFAULT_AGENT_SETTINGS
                return vobiz.speak_xml(fill_template(d["inbound_unavailable_message"], d, None))
            except Exception:  # noqa: BLE001
                return vobiz.hangup_xml()

    async def _inbound_failed(self, pid: str, data: dict) -> str:
        try:
            cfg = load_agent_settings()
        except Exception:  # noqa: BLE001
            cfg = copy.deepcopy(DEFAULT_AGENT_SETTINGS)
        try:
            stored = await run_db(self._store_failed_inbound, pid, data, cfg)
            if stored:
                call_uuid, out = stored
                self._drop_prepared(call_uuid)
                bus.publish({"type": "call.update", "call": out})
        except Exception:  # noqa: BLE001
            log.exception("could not record the failed inbound call %s", pid)
        return self.inbound_failure_xml(cfg)

    def _store_failed_inbound(self, pid: str, data: dict, cfg: dict) -> tuple[str, dict] | None:
        now = utcnow()
        with SessionLocal() as db:
            call = self._inbound_row(db, pid)
            if call is not None:
                if call.status not in ACTIVE_CALL_STATUSES:
                    return None
            else:
                caller = inbound_caller(data.get("From"), data.get("To"), data.get("CallerName"), cfg)
                lead, caller_type = None, caller["kind"]
                try:  # the lead lets the team call back; best effort, the failure may be the database itself
                    lead, caller_type = resolve_inbound_lead(db, caller, now)
                except Exception:  # noqa: BLE001
                    db.rollback()
                call = Call(uuid=uuidlib.uuid4().hex, provider_call_id=pid, lead_id=lead.id if lead else None,
                            channel="phone", direction="inbound", caller_type=caller_type,
                            from_number=caller["from_number"], to_number=caller["to_number"], started_at=now)
                if lead is not None:
                    lead.last_called_at = now
                db.add(call)
            call.status = "failed"
            call.end_reason = "error"
            call.ended_at = now
            call.analysis_status = "skipped"
            call.summary = FAILED_SUMMARY
            call.error = "The inbound answer handler failed (see the backend log)."
            db.commit()
            db.refresh(call)
            return call.uuid, call_list_out(call)

    # ------------------------------------------------------------------ inbound hangups
    def _take_early_hangup(self, provider_id: str) -> dict | None:
        self._prune_early_hangups()
        hit = self._early_hangups.pop(provider_id, None)
        return hit[1] if hit else None

    def _prune_early_hangups(self) -> None:
        cutoff = time.monotonic() - EARLY_HANGUP_TTL
        for key in [k for k, (t, _) in self._early_hangups.items() if t < cutoff]:
            del self._early_hangups[key]

    def _inbound_row_info(self, provider_id: str) -> dict | None:
        with SessionLocal() as db:
            call = self._inbound_row(db, provider_id)
            return {"uuid": call.uuid, "status": call.status} if call is not None else None

    async def inbound_hangup(self, data: dict) -> None:
        """Vobiz says an inbound call ended (application hangup_url, or a hangup posted to the answer URL)."""
        pid = str(data.get("CallUUID") or "").strip()
        if not pid:
            return
        try:
            async with self._inbound_lock(pid):
                row = await run_db(self._inbound_row_info, pid)
                if row is None:  # the answer request is late (or still on its way): it finishes the call
                    self._prune_early_hangups()
                    self._early_hangups[pid] = (time.monotonic(), dict(data))
                    return
                self.mark_hangup_received(row["uuid"])  # the line is down: no REST hang-up needed later
                session = self.sessions.get(row["uuid"])
                if session is not None:
                    if not session.ending:
                        await session.end("user_hangup")
                    return
                if row["status"] not in ACTIVE_CALL_STATUSES:
                    return
                status, end_reason, summary = _missed_by_hangup(data)
                cause = str(data.get("HangupCause") or data.get("HangupCauseCode") or "").strip()
                await self.finish_unanswered(
                    row["uuid"], status, end_reason, summary=summary,
                    error=f"Vobiz ended the call before the audio stream started ({cause})." if status == "failed" else None)
        except Exception:  # noqa: BLE001
            log.exception("inbound hangup failed for %s", pid)

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
                if call is None or call.channel != "phone":
                    return  # browser test calls never touch the lead
                relink_inbound(db, call)  # before hooks and analysis, so both see the lead
                lead = db.get(Lead, call.lead_id) if call.lead_id else None
                talked = call.status == "completed" and session.user_words > 0
                # inbound: only a real conversation counts as "called"; a missed inbound call is never 'failed'
                if lead is not None and lead.status != "dnc" and (call.direction != "inbound" or talked):
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
                                duration: float | None = None, outcome: str | None = None,
                                summary: str | None = None) -> None:
        """Call ended before a conversation (no answer / busy / failed / canceled / missed inbound). Idempotent."""
        self._drop_prepared(call_uuid)

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
                if call.direction == "inbound":
                    # a missed inbound call: the row says why, the lead is never marked 'failed' (call back)
                    call.summary = summary or _inbound_missed_summary(status, end_reason)
                    relink_inbound(db, call)
                else:
                    if summary:
                        call.summary = summary
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
                if c.direction == "inbound" and c.channel == "phone":
                    spoke = c.answered_at is not None and db.scalar(select(TranscriptTurn.id).where(
                        TranscriptTurn.call_id == c.id, TranscriptTurn.role == "user").limit(1)) is not None
                    if not spoke:  # never streamed, or the caller never spoke: a missed call to return
                        c.analysis_status = "skipped"
                        c.summary = RESTART_SUMMARY
                    relink_inbound(db, c)
            db.flush()  # (autoflush is off) the calls closed above must show up in the next query
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
        self._prune_early_hangups()

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
