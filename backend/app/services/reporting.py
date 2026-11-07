"""Shared date-range querying + analytics aggregation (used by /api/analytics and the reports)."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import ACTIVE_CALL_STATUSES, Call, Campaign, TranscriptTurn
from .prompts import OBJECTION_TAGS

IST = timezone(timedelta(hours=5, minutes=30))
END_REASON_LABELS = {
    "agent_ended": "Agent ended", "user_hangup": "Customer hung up", "silence_timeout": "No response (silence)",
    "max_duration": "Max length reached", "manual_hangup": "Hung up from dashboard", "error": "Error",
    "no_answer": "No answer", "busy": "Busy", None: "Unknown",
}


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


def parse_range(from_: str | None, to: str | None, default_days: int = 30) -> Range:
    today = datetime.now(IST).date()
    try:
        end = date.fromisoformat(to) if to else today
        start = date.fromisoformat(from_) if from_ else end - timedelta(days=default_days - 1)
    except ValueError:
        raise HTTPException(422, "Dates must be YYYY-MM-DD") from None
    if start > end:
        start, end = end, start
    if (end - start).days > 366:
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


def talked(c: Call) -> bool:
    return bool((c.metrics or {}).get("user_words"))


def _pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


def compute_analytics(db: Session, calls: list[Call], rng: Range) -> dict:
    connected = [c for c in calls if c.answered_at is not None]
    convo = [c for c in calls if talked(c)]
    rated = [c.rating for c in calls if c.rating is not None]
    durations = [c.duration_sec for c in connected if c.duration_sec is not None]
    interest = Counter(c.interest for c in calls)

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
    by_day = {d: {"date": d, "dialed": 0, "connected": 0, "hot": 0, "warm": 0, "cold": 0, "_r": []} for d in days}
    by_hour = [{"hour": h, "dialed": 0, "connected": 0} for h in range(24)]
    for c in calls:
        t = ist(c.created_at)
        d = by_day.get(t.strftime("%Y-%m-%d"))
        if d:
            d["dialed"] += 1
            d["connected"] += c.answered_at is not None
            if c.interest in ("hot", "warm", "cold"):
                d[c.interest] += 1
            if c.rating is not None:
                d["_r"].append(c.rating)
        by_hour[t.hour]["dialed"] += 1
        by_hour[t.hour]["connected"] += c.answered_at is not None
    day_rows = []
    for d in by_day.values():
        r = d.pop("_r")
        d["avg_rating"] = round(sum(r) / len(r), 1) if r else None
        day_rows.append(d)

    buckets = [("<30s", 0, 30), ("30s–1m", 30, 60), ("1–2m", 60, 120), ("2–5m", 120, 300), ("5m+", 300, 10 ** 9)]
    duration_buckets = [{"label": lbl, "count": sum(1 for x in durations if lo <= x < hi)} for lbl, lo, hi in buckets]

    tags = Counter(t for c in calls for t in (c.metrics or {}).get("objection_tags", []))
    objections = [{"tag": t, "label": OBJECTION_TAGS.get(t, t), "count": n} for t, n in tags.most_common()]

    qual: dict[str, Counter] = defaultdict(Counter)
    for c in calls:
        for item in c.qualification or []:
            q = str(item.get("question") or "").strip()
            if q:
                qual[q][item.get("status") or "not_asked"] += 1
    qualification = sorted(
        ({"question": q, "answered": cnt["answered"], "partial": cnt["partial"], "not_asked": cnt["not_asked"]}
         for q, cnt in qual.items()),
        key=lambda r: -(r["answered"] + r["partial"] + r["not_asked"]))[:8]

    camp_names = {cid: name for cid, name in db.execute(select(Campaign.id, Campaign.name)).all()}
    camp: dict[int, dict] = {}
    for c in calls:
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

    dialed = len(calls)
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
        },
        "funnel": [
            {"stage": "dialed", "label": "Dialed", "count": dialed},
            {"stage": "connected", "label": "Picked up", "count": len(connected)},
            {"stage": "conversation", "label": "Had a conversation", "count": len(convo)},
            {"stage": "warm_or_hot", "label": "Warm or hot", "count": interest["warm"] + interest["hot"]},
            {"stage": "hot", "label": "Hot", "count": interest["hot"]},
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
