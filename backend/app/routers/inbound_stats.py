"""Inbound calling KPIs for the Inbound page (GET /api/inbound/summary)."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ACTIVE_CALL_STATUSES, Call
from ..services.reporting import call_outcome, follow_up_pending_condition, ist, parse_range

router = APIRouter(prefix="/api/inbound", tags=["inbound"])


@router.get("/summary")
def summary(from_: str | None = Query(None, alias="from"), to: str | None = None, db: Session = Depends(get_db)):
    """Inbound PHONE calls created in an IST date range (default: today). Browser rehearsals never count.
    Outcome per call: answered = status "completed", missed = ended any other way, live = still active."""
    rng = parse_range(from_, to, default_days=1, strict=True)
    in_range = and_(Call.direction == "inbound", Call.channel == "phone",
                    Call.created_at >= rng.start_utc, Call.created_at < rng.end_utc)
    rows = db.execute(select(Call.created_at, Call.status, Call.caller_type, Call.interest, Call.duration_sec,
                             Call.lead_id, Call.from_number).where(in_range)).all()

    days = [(rng.start + timedelta(days=i)).isoformat() for i in range(rng.days)]
    by_day = {d: {"date": d, "calls": 0, "answered": 0, "missed": 0} for d in days}
    totals = {"calls": 0, "answered": 0, "missed": 0, "anonymous": 0, "hot": 0, "warm": 0, "cold": 0}
    new_callers: set = set()
    returning_callers: set = set()
    durations: list[float] = []
    for created, status, caller_type, interest, duration, lead_id, from_number in rows:
        outcome = call_outcome(status)
        totals["calls"] += 1
        day = by_day.get(ist(created).date().isoformat()) if created else None
        if day is not None:
            day["calls"] += 1
        if outcome != "live":
            totals[outcome] += 1
            if day is not None:
                day[outcome] += 1
        if outcome == "answered" and duration is not None:
            durations.append(duration)
        if interest in ("hot", "warm", "cold"):
            totals[interest] += 1
        # a caller = their lead, else their number: one person calling three times is ONE caller
        caller = ("lead", lead_id) if lead_id is not None else ("number", from_number or "")
        if caller_type == "anonymous":
            totals["anonymous"] += 1
        elif caller_type == "new":
            new_callers.add(caller)
        elif caller_type == "returning":
            returning_callers.add(caller)

    to_call_back = db.scalar(select(func.count()).select_from(Call).where(in_range, follow_up_pending_condition())) or 0
    live = db.scalar(select(func.count()).select_from(Call).where(
        Call.direction == "inbound", Call.channel == "phone", Call.status.in_(ACTIVE_CALL_STATUSES))) or 0
    return {
        "from": rng.start.isoformat(),
        "to": rng.end.isoformat(),
        "totals": {
            "calls": totals["calls"], "answered": totals["answered"], "missed": totals["missed"],
            "to_call_back": to_call_back, "new_callers": len(new_callers), "returning_callers": len(returning_callers),
            "anonymous": totals["anonymous"],
            "avg_duration_sec": round(sum(durations) / len(durations), 1) if durations else None,
            "hot": totals["hot"], "warm": totals["warm"], "cold": totals["cold"],
        },
        "by_day": list(by_day.values()),
        "live": live,
    }
