"""Dashboard aggregates."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..models import ACTIVE_CALL_STATUSES, Call, Lead
from ..schemas import call_list_out, lead_out

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
IST = timezone(timedelta(hours=5, minutes=30))


@router.get("/stats")
def stats(days: int = Query(14, ge=1, le=90), db: Session = Depends(get_db)):
    now_ist = datetime.now(IST)
    today_start = now_ist.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    since = (now_ist - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

    leads_total = db.scalar(select(func.count()).select_from(Lead)) or 0
    interest = {"unknown": 0, "cold": 0, "warm": 0, "hot": 0}
    for k, n in db.execute(select(Lead.interest, func.count()).group_by(Lead.interest)).all():
        interest[k] = interest.get(k, 0) + n

    phone_calls = select(Call).where(Call.channel == "phone")
    calls_total = db.scalar(select(func.count()).select_from(phone_calls.subquery())) or 0
    calls_today = db.scalar(select(func.count()).select_from(Call).where(
        Call.channel == "phone", Call.created_at >= today_start)) or 0
    connected = db.scalar(select(func.count()).select_from(Call).where(
        Call.channel == "phone", Call.answered_at.is_not(None))) or 0
    finished = db.scalar(select(func.count()).select_from(Call).where(
        Call.channel == "phone", Call.status.not_in(ACTIVE_CALL_STATUSES))) or 0
    # business metrics count real phone calls only; browser test calls would skew them
    phone = Call.channel == "phone"
    avg_rating = db.scalar(select(func.avg(Call.rating)).where(phone, Call.rating.is_not(None)))
    avg_dur = db.scalar(select(func.avg(Call.duration_sec)).where(phone, Call.duration_sec.is_not(None),
                                                                  Call.answered_at.is_not(None)))
    qualified = db.scalar(select(func.count()).select_from(Call).where(phone, Call.qualified.is_(True))) or 0
    active = db.scalar(select(func.count()).select_from(Call).where(Call.status.in_(ACTIVE_CALL_STATUSES))) or 0

    hist = {r: 0 for r in range(1, 11)}
    for r, n in db.execute(select(Call.rating, func.count()).where(phone, Call.rating.is_not(None))
                           .group_by(Call.rating)).all():
        if r in hist:
            hist[r] = n

    by_day: dict[str, dict] = {}
    for i in range(days):
        d = (since + timedelta(days=i)).strftime("%Y-%m-%d")
        by_day[d] = {"date": d, "total": 0, "connected": 0, "hot": 0}
    rows = db.execute(select(Call.created_at, Call.answered_at, Call.interest)
                      .where(phone, Call.created_at >= since.astimezone(timezone.utc))).all()
    for created, answered, intr in rows:
        d = created.astimezone(IST).strftime("%Y-%m-%d")
        if d in by_day:
            by_day[d]["total"] += 1
            if answered:
                by_day[d]["connected"] += 1
            if intr == "hot":
                by_day[d]["hot"] += 1

    reasons = [{"reason": r or "unknown", "count": n} for r, n in db.execute(
        select(Call.end_reason, func.count()).where(phone, Call.status.not_in(ACTIVE_CALL_STATUSES))
        .group_by(Call.end_reason).order_by(func.count().desc())).all()]

    recent = db.scalars(select(Call).options(selectinload(Call.lead)).order_by(Call.id.desc()).limit(8)).all()
    top = db.scalars(select(Lead).where(Lead.interest == "hot")
                     .order_by(Lead.rating.desc().nulls_last(), Lead.last_called_at.desc().nulls_last()).limit(8)).all()

    return {
        "totals": {
            "leads": leads_total, "calls": calls_total, "calls_today": calls_today, "connected": connected,
            "connect_rate": round(connected / finished, 3) if finished else 0,
            "avg_rating": round(float(avg_rating), 1) if avg_rating is not None else None,
            "avg_duration_sec": round(float(avg_dur), 1) if avg_dur is not None else None,
            "qualified": qualified, "active_calls": active,
        },
        "interest": interest,
        "rating_histogram": [{"rating": r, "count": n} for r, n in hist.items()],
        "calls_by_day": list(by_day.values()),
        "end_reasons": reasons,
        "recent_calls": [call_list_out(c) for c in recent],
        "top_leads": [lead_out(l) for l in top],
    }
