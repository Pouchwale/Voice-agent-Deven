"""Shared date ranges, call definitions (inbound outcome, conversation, follow-up) and analytics aggregation.

Used by /api/analytics + the reports, the dashboard, the inbound summary, the calls list and the customer
timeline, so every screen counts a call the same way.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import Session, aliased, selectinload

from ..models import ACTIVE_CALL_STATUSES, Call, Campaign, Lead, TranscriptTurn
from .prompts import OBJECTION_TAGS

IST = timezone(timedelta(hours=5, minutes=30))
END_REASON_LABELS = {
    "agent_ended": "Agent ended", "user_hangup": "Customer hung up", "silence_timeout": "No response (silence)",
    "max_duration": "Max length reached", "manual_hangup": "Hung up from dashboard", "error": "Error",
    "no_answer": "No answer", "busy": "Busy", "inbound_off": "Missed (inbound off)",
    "lines_busy": "Missed (lines busy)", "superseded": "Cancelled (customer called in)",
    "offline": "Missed (agent offline)", None: "Unknown",
}
CALLER_TYPES = ("returning", "known", "new", "international", "anonymous")
OUTCOMES = ("answered", "missed", "live")


# ---------------------------------------------------------------- shared call definitions
# Inbound outcome predicate (inbound summary, dashboard, analytics, timeline, calls filter):
#   live = still active; answered = status "completed" (the caller talked with the agent);
#   missed = ended any other way (switched off, lines busy, offline, failed, hung up before the agent).
#   Invariant: calls = answered + missed + live.
def call_outcome(status: str | None) -> str:
    if status in ACTIVE_CALL_STATUSES:
        return "live"
    return "answered" if status == "completed" else "missed"


def outcome_condition(outcome: str, call=Call):
    """SQL version of call_outcome() == outcome."""
    if outcome == "live":
        return call.status.in_(ACTIVE_CALL_STATUSES)
    if outcome == "answered":
        return call.status == "completed"
    if outcome == "missed":
        return and_(call.status.not_in(ACTIVE_CALL_STATUSES), call.status != "completed")
    raise HTTPException(422, f"outcome must be one of: {', '.join(OUTCOMES)}")


def is_outbound(call=Call):
    """Outbound rows in SQL. A row from before inbound calling existed (direction NULL on a database the
    startup migration hasn't normalised yet) is outbound: a plain `!= 'inbound'` would drop it."""
    return func.coalesce(call.direction, "outbound") != "inbound"


def outbound_call(c: Call) -> bool:
    return (c.direction or "outbound") != "inbound"


def user_words(call=Call):
    """metrics.user_words in SQL; 0 when the metrics JSON is missing or unreadable."""
    return case((func.json_valid(call.metrics) == 1, func.coalesce(func.json_extract(call.metrics, "$.user_words"), 0)),
                else_=0)


def metric_user_words(metrics) -> int:
    if not isinstance(metrics, dict):
        return 0
    try:
        return int(metrics.get("user_words") or 0)
    except (TypeError, ValueError):
        return 0


def conversation_condition(call=Call):
    """Conversation = a phone call that completed with the customer saying something (either direction)."""
    return and_(call.channel == "phone", call.status == "completed", user_words(call) > 0)


def talked(c: Call) -> bool:
    return metric_user_words(c.metrics) > 0


def had_conversation(c: Call) -> bool:
    """Python twin of conversation_condition() without the channel check (callers scope the channel)."""
    return c.status == "completed" and talked(c)


def customer_number(c: Call) -> str:
    """The customer's number on a call: the caller (inbound) or the dialed number (outbound); never our own
    DID. Falls back to the linked lead's phone."""
    own = c.from_number if c.direction == "inbound" else c.to_number
    return own or (c.lead.phone if c.lead is not None else "") or ""


# Follow-up of a missed inbound call (null for every other call):
#   done = a LATER conversation exists for that customer; tried = a later outbound call exists (not a
#   superseded one) but no conversation yet; pending = neither ("To call back").
# "That customer" = the same lead, or the same caller number on an inbound call, or, for a caller who had
# no lead, the lead that has that number now. Outbound rows that lost their lead are never matched by
# number: they may belong to a deleted lead / recycled number (same rule as the timeline).
def follow_up_applies(call=Call):
    """Missed inbound phone calls from a number we can call back."""
    return and_(call.direction == "inbound", call.channel == "phone", outcome_condition("missed", call),
                func.coalesce(call.caller_type, "") != "anonymous",
                or_(call.lead_id.is_not(None), call.from_number.like("+%")))


def follow_up_applies_to(c: Call) -> bool:
    """Python twin of follow_up_applies()."""
    return (c.direction == "inbound" and c.channel == "phone" and call_outcome(c.status) == "missed"
            and (c.caller_type or "") != "anonymous"
            and (c.lead_id is not None or (c.from_number or "").startswith("+")))


def follow_up_pending_condition(call=Call):
    """SQL: the call is a missed inbound call nobody has spoken with, or even tried, since ("To call back")."""
    later = aliased(Call)
    owner = aliased(Lead)  # an alias: never correlate with a Lead the outer query may join
    number_ok = call.from_number.like("+%")
    # Explicit correlate(): auto-correlation only reaches the IMMEDIATELY enclosing SELECT, so the nested
    # owner lookup would otherwise cross-join a second, unrelated `calls`.
    owner_id = select(owner.id).where(owner.phone == call.from_number).correlate(call).scalar_subquery()
    # coalesce() on direction keeps SQLite on ix_calls_from_number (not the much wider direction index)
    same_customer = or_(
        and_(call.lead_id.is_not(None), later.lead_id == call.lead_id),
        and_(number_ok, func.coalesce(later.direction, "") == "inbound", later.from_number == call.from_number,
             or_(later.lead_id.is_(None), call.lead_id.is_(None))),
        and_(call.lead_id.is_(None), number_ok, later.lead_id == owner_id),
    )
    contact = or_(conversation_condition(later),
                  and_(is_outbound(later), func.coalesce(later.end_reason, "") != "superseded"))
    return and_(follow_up_applies(call),
                ~exists().where(later.channel == "phone", later.created_at > call.created_at, same_customer, contact)
                .correlate(call))


def follow_ups(db: Session, calls: list[Call]) -> dict[str, str]:
    """follow_up ("pending" | "tried" | "done") of every missed inbound call in `calls`, by uuid.
    Two queries for the whole list (no N+1); same rules as follow_up_pending_condition()."""
    targets = [c for c in calls if follow_up_applies_to(c) and c.created_at is not None]
    if not targets:
        return {}
    numbers = sorted({c.from_number for c in targets if (c.from_number or "").startswith("+")})
    lead_by_phone: dict[str, int] = {}
    if numbers:
        lead_by_phone = {p: i for p, i in db.execute(select(Lead.phone, Lead.id).where(Lead.phone.in_(numbers)))}
    lead_ids = {c.lead_id for c in targets if c.lead_id is not None}
    lead_ids |= {lead_by_phone[c.from_number] for c in targets if c.lead_id is None and c.from_number in lead_by_phone}
    match = []
    if lead_ids:
        match.append(Call.lead_id.in_(lead_ids))
    if numbers:  # coalesce(): stay on ix_calls_from_number, as above
        match.append(and_(func.coalesce(Call.direction, "") == "inbound", Call.from_number.in_(numbers)))
    since = min(c.created_at for c in targets)
    later_rows = db.execute(
        select(Call.lead_id, Call.direction, Call.from_number, Call.status, Call.end_reason, Call.metrics,
               Call.created_at)
        .where(Call.channel == "phone", Call.created_at > since, or_(*match))).all() if match else []

    out: dict[str, str] = {}
    for c in targets:
        number = c.from_number if (c.from_number or "").startswith("+") else None
        owner = lead_by_phone.get(number) if c.lead_id is None and number else None
        state = "pending"
        for r in later_rows:
            if r.created_at <= c.created_at:
                continue
            same = ((c.lead_id is not None and r.lead_id == c.lead_id)
                    or (number is not None and r.direction == "inbound" and r.from_number == number
                        and (r.lead_id is None or c.lead_id is None))
                    or (owner is not None and r.lead_id == owner))
            if not same:
                continue
            if r.status == "completed" and metric_user_words(r.metrics) > 0:
                state = "done"
                break
            if (r.direction or "outbound") != "inbound" and (r.end_reason or "") != "superseded":
                state = "tried"
        out[c.uuid] = state
    return out


@dataclass
class Range:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def start_utc(self) -> datetime:
        return datetime.combine(self.start, dtime.min, IST).astimezone(timezone.utc)

    @property
    def end_utc(self) -> datetime:  # exclusive
        return datetime.combine(self.end + timedelta(days=1), dtime.min, IST).astimezone(timezone.utc)

    def label(self) -> str:
        if self.start == self.end:
            return self.start.strftime("%d %b %Y")
        return f"{self.start.strftime('%d %b %Y')} – {self.end.strftime('%d %b %Y')}"


def ist_day_start(d: date) -> datetime:
    """UTC instant of IST midnight at the start of `d` (every day boundary is IST)."""
    return datetime.combine(d, dtime.min, IST).astimezone(timezone.utc)


def parse_day(value: str | None, name: str) -> date | None:
    """An optional IST calendar date parameter (YYYY-MM-DD); 422 when it isn't a date."""
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(422, f"'{name}' must be a date like 2026-11-13 (YYYY-MM-DD)") from None


def parse_range(from_: str | None, to: str | None, default_days: int = 30, *, strict: bool = False) -> Range:
    """IST date range, both ends inclusive. Lenient (analytics/reports): a reversed range is swapped.
    strict (new endpoints): from > to is refused, and the range may span at most 366 days."""
    today = datetime.now(IST).date()
    end = parse_day(to, "to") or today
    start = parse_day(from_, "from") or end - timedelta(days=default_days - 1)
    if start > end:
        if strict:
            raise HTTPException(422, "'from' must be on or before 'to'")
        start, end = end, start
    span = (end - start).days + 1  # days, both ends included
    if span > (366 if strict else 367):
        raise HTTPException(422, "Date range can be at most 1 year")
    return Range(start, end)


def calls_in_range(db: Session, rng: Range, campaign_id: int | None = None, include_tests: bool = False,
                   with_turns: bool = False) -> list[Call]:
    opts = [selectinload(Call.lead)]
    if with_turns:
        opts.append(selectinload(Call.turns))
    q = (select(Call).options(*opts)
         .where(Call.created_at >= rng.start_utc, Call.created_at < rng.end_utc,
                Call.status.not_in(ACTIVE_CALL_STATUSES)))
    if not include_tests:
        q = q.where(Call.channel == "phone")
    if campaign_id:
        q = q.where(Call.campaign_id == campaign_id)
    return list(db.scalars(q.order_by(Call.created_at)).all())


def ist(dt: datetime | None) -> datetime | None:
    return dt.astimezone(IST) if dt else None


def _pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


def compute_analytics(db: Session, calls: list[Call], rng: Range) -> dict:
    """Metric scopes: dialed / picked up / connect rate / the funnel / per-day dialed+connected / per-hour /
    campaigns are OUTBOUND calls (an inbound call was never dialed); quality numbers (conversations, talk
    time, ratings, interest, end reasons, sentiment, objections, qualification, latency) cover ALL the calls;
    inbound calls get their own totals (calls in the range are ended ones, so inbound = answered + missed)."""
    outbound = [c for c in calls if outbound_call(c)]
    inbound = [c for c in calls if not outbound_call(c)]
    connected = [c for c in outbound if c.answered_at is not None]
    convo = [c for c in calls if had_conversation(c)]
    # funnel stages are nested subsets, so the funnel can never widen
    funnel_convo = [c for c in connected if had_conversation(c)]
    funnel_interest = Counter(c.interest for c in funnel_convo)
    rated = [c.rating for c in calls if c.rating is not None]
    durations = [c.duration_sec for c in calls if c.answered_at is not None and c.duration_sec is not None]
    interest = Counter(c.interest for c in calls)
    inbound_answered = sum(1 for c in inbound if call_outcome(c.status) == "answered")

    # per-turn response latency (customer stops -> agent voice starts)
    ids = [c.id for c in calls]
    lat_rows: list[tuple[int, int]] = []
    for i in range(0, len(ids), 500):
        lat_rows += db.execute(select(TranscriptTurn.call_id, TranscriptTurn.latency_ms)
                               .where(TranscriptTurn.call_id.in_(ids[i:i + 500]),
                                      TranscriptTurn.latency_ms.is_not(None))).all()
    lat_all = [ms for _, ms in lat_rows]
    call_day = {c.id: ist(c.created_at).strftime("%Y-%m-%d") for c in calls}
    lat_by_day: dict[str, list[int]] = defaultdict(list)
    for cid, ms in lat_rows:
        lat_by_day[call_day[cid]].append(ms)

    days = [(rng.start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(rng.days)]
    by_day = {d: {"date": d, "dialed": 0, "connected": 0, "hot": 0, "warm": 0, "cold": 0, "_r": [], "inbound": 0}
              for d in days}
    by_hour = [{"hour": h, "dialed": 0, "connected": 0} for h in range(24)]
    for c in calls:
        t = ist(c.created_at)
        out = outbound_call(c)
        d = by_day.get(t.strftime("%Y-%m-%d"))
        if d:
            if out:
                d["dialed"] += 1
                d["connected"] += c.answered_at is not None
            else:
                d["inbound"] += 1
            if c.interest in ("hot", "warm", "cold"):
                d[c.interest] += 1
            if c.rating is not None:
                d["_r"].append(c.rating)
        if out:  # "best time to call" is about dialing
            by_hour[t.hour]["dialed"] += 1
            by_hour[t.hour]["connected"] += c.answered_at is not None
    day_rows = []
    for d in by_day.values():
        r = d.pop("_r")
        d["avg_rating"] = round(sum(r) / len(r), 1) if r else None
        day_rows.append(d)

    buckets = [("<30s", 0, 30), ("30s–1m", 30, 60), ("1–2m", 60, 120), ("2–5m", 120, 300), ("5m+", 300, 10 ** 9)]
    duration_buckets = [{"label": lbl, "count": sum(1 for x in durations if lo <= x < hi)} for lbl, lo, hi in buckets]

    tags = Counter(t for c in calls if isinstance(c.metrics, dict) for t in c.metrics.get("objection_tags") or [])
    objections = [{"tag": t, "label": OBJECTION_TAGS.get(t, t), "count": n} for t, n in tags.most_common()]

    qual: dict[str, Counter] = defaultdict(Counter)
    for c in calls:
        for item in c.qualification or []:
            if not isinstance(item, dict):
                continue
            q = str(item.get("question") or "").strip()
            if q:
                qual[q][item.get("status") or "not_asked"] += 1
    qualification = sorted(
        ({"question": q, "answered": cnt["answered"], "partial": cnt["partial"], "not_asked": cnt["not_asked"]}
         for q, cnt in qual.items()),
        key=lambda r: -(r["answered"] + r["partial"] + r["not_asked"]))[:8]

    camp_names = {cid: name for cid, name in db.execute(select(Campaign.id, Campaign.name)).all()}
    camp: dict[int, dict] = {}
    for c in outbound:
        if not c.campaign_id:
            continue
        row = camp.setdefault(c.campaign_id, {"id": c.campaign_id, "name": camp_names.get(c.campaign_id, "Deleted"),
                                              "dialed": 0, "connected": 0, "hot": 0, "warm": 0, "cold": 0, "_r": []})
        row["dialed"] += 1
        row["connected"] += c.answered_at is not None
        if c.interest in ("hot", "warm", "cold"):
            row[c.interest] += 1
        if c.rating is not None:
            row["_r"].append(c.rating)
    campaigns = []
    for row in camp.values():
        r = row.pop("_r")
        row["avg_rating"] = round(sum(r) / len(r), 1) if r else None
        campaigns.append(row)
    campaigns.sort(key=lambda r: -r["dialed"])

    dialed = len(outbound)
    return {
        "range": {"from": rng.start.isoformat(), "to": rng.end.isoformat(), "days": rng.days},
        "totals": {
            "dialed": dialed, "connected": len(connected),
            "connect_rate": round(len(connected) / dialed, 3) if dialed else 0,
            "conversations": len(convo),
            "talk_minutes": round(sum(durations) / 60, 1),
            "avg_duration_sec": round(sum(durations) / len(durations), 1) if durations else None,
            "avg_rating": round(sum(rated) / len(rated), 1) if rated else None,
            "hot": interest["hot"], "warm": interest["warm"], "cold": interest["cold"],
            "qualified": sum(1 for c in calls if c.qualified),
            "avg_latency_ms": int(sum(lat_all) / len(lat_all)) if lat_all else None,
            "p90_latency_ms": _pct(lat_all, 0.9),
            "inbound": len(inbound), "inbound_answered": inbound_answered,
            "inbound_missed": len(inbound) - inbound_answered,
        },
        "funnel": [  # outbound only
            {"stage": "dialed", "label": "Dialed", "count": dialed},
            {"stage": "connected", "label": "Picked up", "count": len(connected)},
            {"stage": "conversation", "label": "Had a conversation", "count": len(funnel_convo)},
            {"stage": "warm_or_hot", "label": "Warm or hot",
             "count": funnel_interest["warm"] + funnel_interest["hot"]},
            {"stage": "hot", "label": "Hot", "count": funnel_interest["hot"]},
        ],
        "by_day": day_rows,
        "by_hour": by_hour,
        "interest": {k: interest.get(k, 0) for k in ("unknown", "cold", "warm", "hot")},
        "rating_histogram": [{"rating": r, "count": rated.count(r)} for r in range(1, 11)],
        "duration_buckets": duration_buckets,
        "end_reasons": [{"reason": END_REASON_LABELS.get(r, r or "Unknown"), "count": n}
                        for r, n in Counter(c.end_reason for c in calls).most_common()],
        "sentiment": {k: sum(1 for c in calls if c.sentiment == k) for k in ("positive", "neutral", "negative")},
        "objections": objections,
        "qualification": qualification,
        "campaigns": campaigns,
        "latency_by_day": [{"date": d, "avg_ms": int(sum(v) / len(v)) if (v := lat_by_day.get(d)) else None}
                           for d in days],
    }
