"""Post-call analysis: summary, 1-10 rating, cold/warm/hot, qualification answers, objection tags, why an
inbound caller called, what we promised them, and the customer's name / company / city."""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..config import get_settings
from ..db import SessionLocal
from ..models import INTERESTS, Call
from .agent_config import load_agent_settings
from .events import bus
from .llm import complete_json
from .prompts import CALL_REASONS, OBJECTION_TAGS, build_analysis_prompt
from .repo import run_db
from ..schemas import call_list_out, lead_out

log = logging.getLogger("analysis")
settings = get_settings()

# Inbound calls that aren't a buying enquiry may raise a lead's interest / rating but never lower it: a hot
# lead asking about their order is still hot.
NON_ENQUIRY_REASONS = {"order_or_sample", "complaint", "vendor_or_job", "wrong_number"}
_RANK = {"unknown": 0, "cold": 1, "warm": 2, "hot": 3}
SUMMARY_SILENT_CALLER = "Caller didn't speak"
SUMMARY_OUR_FAILURE = "Call failed on our side — call back"
SUMMARY_HUNG_UP_FIRST = "Caller hung up before the agent answered"

# What the model writes when it doesn't know: never stored as a name / company / city.
_NULLISH = {"", "null", "none", "nil", "unknown", "not known", "n/a", "na", "-", "--", "?", "not mentioned",
            "not given", "not provided", "not said", "not specified", "no name", "customer", "caller"}
# Titles around a name ("Mr. Ramesh", "Ramesh ji", "Sir"): stripped; a title alone is no name.
_TITLES = {"mr", "mrs", "ms", "miss", "dr", "shri", "sri", "smt", "kumari", "ji", "जी", "sir", "madam", "mam",
           "maam", "ma'am", "sahab", "saheb", "saab", "bhai", "bhaiya", "didi", "m/s", "श्री", "श्रीमती", "सर", "मैडम"}
_FIELD_CAPS = {"name": 200, "company": 200, "city": 120}  # = the Lead column sizes


def _load(call_uuid: str) -> tuple[dict | None, list[tuple[str, str]], dict | None]:
    with SessionLocal() as db:
        call = db.scalar(select(Call).options(selectinload(Call.turns), selectinload(Call.lead))
                         .where(Call.uuid == call_uuid))
        if not call:
            return None, [], None
        turns = [(t.role, t.text) for t in call.turns if t.role in ("agent", "user") and t.text.strip()]
        m = call.metrics if isinstance(call.metrics, dict) else {}
        meta = {"id": call.id, "channel": call.channel, "direction": call.direction or "outbound",
                "status": call.status, "answered": call.answered_at is not None, "end_reason": call.end_reason,
                "agent_audio_ms": m.get("agent_audio_ms", 0), "session_end_reason": m.get("session_end_reason"),
                "summary": call.summary}
        return meta, turns, (lead_out(call.lead) if call.lead else None)


def _clamp_rating(v: Any) -> int | None:
    try:
        r = int(round(float(v)))
    except (TypeError, ValueError):
        return None
    return max(1, min(10, r))


def _as_list(v: Any) -> list:
    if isinstance(v, list):
        return [x for x in v if x not in (None, "")]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def _plain(v: Any, cap: int = 500) -> str | None:
    """A model value as clean text, or None for null-like answers ("unknown", "N/A", "-")."""
    if v is None or isinstance(v, (bool, dict, list)):
        return None
    s = " ".join(str(v).split()).strip(" .,;:'\"-")
    return None if s.casefold() in _NULLISH else s[:cap]


def _tidy_case(s: str) -> str:
    return s.title() if s.isascii() and (s.islower() or s.isupper()) else s  # "ramesh patel" -> "Ramesh Patel"


def customer_field(value: Any, kind: str, cfg: dict) -> str | None:
    """The customer's name / company / city as the analysis heard it, or None when it isn't usable:
    null-like, only a title ("Sir"), our own company or agent name, digits or an email in a name."""
    s = _plain(value)
    if not s or all(w.casefold().rstrip(".") in _TITLES for w in s.split()):
        return None  # nothing, or only a title ("Sir", "Madam ji")
    if kind == "name":
        words = s.split()
        while words and words[0].casefold().rstrip(".") in _TITLES:
            words.pop(0)
        while words and words[-1].casefold().rstrip(".") in _TITLES:
            words.pop()
        s = " ".join(words)
        if any(ch.isdigit() for ch in s) or "@" in s:
            return None
        s = _tidy_case(s)
    elif kind == "company":
        s = re.sub(r"^m/s\.?\s*", "", s, flags=re.IGNORECASE)  # "M/s Shree Traders" -> "Shree Traders"
    else:
        s = _tidy_case(s)
    if not s or _plain(s) is None:
        return None
    low = s.casefold()
    company = (cfg.get("company_name") or "").strip().casefold()
    agent = (cfg.get("agent_name") or "").strip().casefold()
    # our side of the call, not the customer's ("PouchWale", "PouchWale Packaging", the agent's own name)
    if (agent and low == agent) or (company and (low == company or (len(company) >= 4 and company in low))):
        return None
    return s[:_FIELD_CAPS[kind]]


def _customer_phone(value: Any, call_number: str | None) -> str | None:
    """Another number the customer gave (WhatsApp, office) — not the one this call used."""
    s = _plain(value, 40)
    digits = re.sub(r"\D", "", s or "")
    if not 8 <= len(digits) <= 15:
        return None
    own = re.sub(r"\D", "", call_number or "")
    if own and digits[-10:] == own[-10:]:
        return None
    return ("+" if (s or "").startswith("+") else "") + digits


def _save(call_uuid: str, result: dict[str, Any], status: str, error: str | None = None,
          cfg: dict | None = None) -> dict | None:
    if status == "done" and cfg is None:
        cfg = load_agent_settings()  # (outside the DB session below: it may read the settings row)
    with SessionLocal() as db:
        call = db.scalar(select(Call).options(selectinload(Call.lead)).where(Call.uuid == call_uuid))
        if not call:
            return None
        call.analysis_status = status
        call.error = error if error else call.error
        inbound = call.direction == "inbound"
        metrics = dict(call.metrics) if isinstance(call.metrics, dict) else {}
        try:
            words = int(metrics.get("user_words") or 0)
        except (TypeError, ValueError):
            words = 0
        found: dict[str, str | None] = {}
        if status == "done":
            interest = str(result.get("interest") or "unknown").lower().strip()
            call.interest = interest if interest in INTERESTS else "unknown"
            call.rating = _clamp_rating(result.get("rating"))
            call.summary = (result.get("summary") or "").strip() or None
            q = result.get("qualified")
            call.qualified = bool(q) if q is not None else None
            sent = str(result.get("sentiment") or "").lower()
            call.sentiment = sent if sent in ("positive", "neutral", "negative") else None
            call.next_action = (result.get("next_action") or None)
            cb = result.get("callback_time")
            call.callback_time = str(cb)[:120] if cb not in (None, "", "null") else None
            quals = []
            for item in _as_list(result.get("qualification")):
                if isinstance(item, dict):
                    st = str(item.get("status") or "not_asked")
                    quals.append({
                        "question": str(item.get("question") or "")[:200],
                        "answer": (str(item["answer"])[:500] if item.get("answer") not in (None, "", "null") else None),
                        "status": st if st in ("answered", "partial", "not_asked") else "not_asked",
                    })
            call.qualification = quals or None
            call.key_points = [str(x)[:200] for x in _as_list(result.get("key_points"))][:12] or None
            call.objections = [str(x)[:200] for x in _as_list(result.get("objections"))][:10] or None
            tags = [str(t).strip().lower() for t in _as_list(result.get("objection_tags"))]
            tags = sorted({t if t in OBJECTION_TAGS else "other" for t in tags})
            reason = re.sub(r"[\s/-]+", "_", (_plain(result.get("call_reason"), 40) or "").lower())
            found = {k: customer_field(result.get(f"customer_{k}"), k, cfg) for k in ("name", "company", "city")}
            call.metrics = {
                **metrics,
                "objection_tags": tags,
                # why an inbound caller called (Inbound page); outbound calls have none
                "call_reason": (reason if reason in CALL_REASONS else "other" if reason else None) if inbound else None,
                "promised_to_customer": _plain(result.get("promised_to_customer"), 300),
                "customer_phone": _customer_phone(result.get("customer_phone"),
                                                  call.from_number if inbound else call.to_number),
                # also kept on the call: a caller without a lead (international) is remembered by number
                **{f"customer_{k}": v for k, v in found.items()},
            }
        elif status == "skipped":
            call.interest = "unknown"
            call.summary = result.get("summary")
        # Roll the outcome up to the lead so BDEs can sort/filter leads directly — analysed PHONE calls only:
        # a browser test (role-play) or a skipped / failed analysis never changes the lead.
        lead = call.lead
        if lead is not None and call.channel == "phone" and status == "done":
            # a pickup-and-cut must never wipe out an earlier warm/hot conversation
            protect = not words and lead.interest in ("warm", "hot")
            never_lower = inbound and (call.metrics or {}).get("call_reason") in NON_ENQUIRY_REASONS
            if not protect:
                if call.interest != "unknown" and not (
                        never_lower and _RANK.get(call.interest, 0) < _RANK.get(lead.interest, 0)):
                    lead.interest = call.interest
                if call.rating is not None and not (
                        never_lower and lead.rating is not None and call.rating < lead.rating):
                    lead.rating = call.rating
                if call.summary:
                    lead.last_summary = call.summary
            if words > 0:
                # learn what the team hasn't typed yet; never overwrite what they did
                for k, v in found.items():
                    if v and not (getattr(lead, k) or "").strip():
                        setattr(lead, k, v)
        db.commit()
        db.refresh(call)
        out = call_list_out(call)
    bus.publish({"type": "call.update", "call": out})
    return out


def _inbound_no_talk_summary(meta: dict) -> str:
    """Summary of an inbound call where the caller said nothing (never "not interested": they called us)."""
    if meta["status"] != "completed" and meta["summary"]:
        return meta["summary"]  # a missed call keeps what its handler wrote — a re-analyse too
    heard = (meta.get("agent_audio_ms") or 0) > 0
    # in a browser rehearsal the tester plays the caller: their hang-up button is the caller hanging up
    by_caller = ("user_hangup", "manual_hangup") if meta["channel"] == "browser" else ("user_hangup",)
    if not heard and ({meta["end_reason"], meta.get("session_end_reason")} & set(by_caller)):
        return SUMMARY_HUNG_UP_FIRST
    if heard and meta["status"] == "completed" and meta["end_reason"] != "error":
        return SUMMARY_SILENT_CALLER
    return SUMMARY_OUR_FAILURE


async def analyze_call(call_uuid: str) -> None:
    meta, turns, lead = await run_db(_load, call_uuid)
    if meta is None:
        return
    user_turns = [t for t in turns if t[0] == "user"]
    if not user_turns:
        if meta["direction"] == "inbound":
            # skipped: the lead stays untouched and the call counts as missed or silent, never as cold
            await run_db(_save, call_uuid, {"summary": _inbound_no_talk_summary(meta)}, "skipped")
            return
        heard_agent = (meta.get("agent_audio_ms") or 0) > 0
        customer_ended = meta.get("end_reason") in ("user_hangup", "silence_timeout")
        if meta.get("channel") == "phone" and meta.get("answered") and heard_agent and customer_ended:
            # Outbound: picked up, heard us, then cut the call / never spoke -> cold (client rule), lowest
            # rating. Not applied to our own failures (TTS/STT outage, errors, manual hang-up): those are skipped.
            silent = meta.get("end_reason") == "silence_timeout"
            await run_db(_save, call_uuid, {
                "summary": ("Picked up but did not respond; the agent ended the call after the silence check."
                            if silent else "Picked up and disconnected without talking."),
                "rating": 1, "interest": "cold", "qualified": False, "sentiment": "neutral",
                "next_action": "Try again at a different time of day, or move on.",
                "objection_tags": ["not_interested"] if not silent else [],
            }, "done")
            return
        await run_db(_save, call_uuid, {"summary": "No conversation — the customer did not speak on the call."},
                     "skipped")
        return
    if not settings.openai_ready:
        await run_db(_save, call_uuid, {}, "failed", "OPENAI_API_KEY not set — analysis skipped")
        return
    cfg = load_agent_settings()
    transcript = "\n".join(f"{'AGENT' if r == 'agent' else 'CUSTOMER'}: {t}" for r, t in turns)
    try:
        result = await complete_json(build_analysis_prompt(cfg, lead, transcript[-12000:], meta["direction"]))
        if not result:
            raise ValueError("empty analysis")
        await run_db(_save, call_uuid, result, "done", None, cfg)
    except Exception as e:  # noqa: BLE001
        log.exception("analysis failed for %s", call_uuid)
        await run_db(_save, call_uuid, {}, "failed", f"Analysis failed: {str(e)[:200]}")
