"""Leads: CRUD, search/filter/sort, CSV import/export, customer timeline."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, case, delete, func, insert, or_, select
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..db import get_db, utcnow
from ..models import Call, Campaign, CampaignLead, Lead
from ..schemas import IdList, LeadCreate, LeadUpdate, call_list_out, iso, lead_out, lead_stage
from ..services import lead_csv
from ..services.csvsafe import csv_response
from ..services.phone import normalize_phone
from ..services.reporting import conversation_condition, follow_ups

settings = get_settings()

router = APIRouter(prefix="/api/leads", tags=["leads"])

SORTS = {
    "created_at": Lead.created_at.asc(), "-created_at": Lead.created_at.desc(),
    "rating": Lead.rating.asc().nulls_first(), "-rating": Lead.rating.desc().nulls_last(),
    "last_called_at": Lead.last_called_at.asc().nulls_first(),
    "-last_called_at": Lead.last_called_at.desc().nulls_last(),
    "name": Lead.name.asc(), "-name": Lead.name.desc(),
}

BOARD_COLUMNS = [
    ("new", "New", "Not called yet"),
    ("no_answer", "No answer", "Didn't pick up / busy"),
    ("cold", "Cold", "Picked up & cut, or not interested"),
    ("warm", "Warm", "Normal interest"),
    ("hot", "Hot", "Strong interest"),
]
STAGE_ORDER = {
    "new": (Lead.created_at.desc(),),
    "no_answer": (Lead.last_called_at.desc().nulls_last(),),
    "cold": (Lead.rating.desc().nulls_last(), Lead.last_called_at.desc().nulls_last()),
    "warm": (Lead.rating.desc().nulls_last(), Lead.last_called_at.desc().nulls_last()),
    "hot": (Lead.rating.desc().nulls_last(), Lead.last_called_at.desc().nulls_last()),
}


def stage_condition(stage: str):
    """SQL equivalent of schemas.lead_stage()."""
    if stage in ("cold", "warm", "hot"):
        return Lead.interest == stage
    if stage == "no_answer":
        return (Lead.interest == "unknown") & (Lead.attempts > 0)
    if stage == "new":
        return (Lead.interest == "unknown") & (Lead.attempts == 0)
    raise HTTPException(422, f"Unknown stage '{stage}'")


def _filtered(search: str | None, status: str | None, interest: str | None, stage: str | None = None):
    q = select(Lead)
    if search:
        like = f"%{search.strip()}%"
        q = q.where(or_(Lead.name.ilike(like), Lead.phone.ilike(like), Lead.company.ilike(like),
                        Lead.city.ilike(like)))
    if status:
        q = q.where(Lead.status == status)
    if interest:
        q = q.where(Lead.interest.in_(interest.split(",")))
    if stage:
        q = q.where(stage_condition(stage), Lead.status != "dnc")
    return q


@router.get("")
def list_leads(
    search: str | None = None, status: str | None = None, interest: str | None = None, stage: str | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500), sort: str | None = None,
    db: Session = Depends(get_db),
):
    q = _filtered(search, status, interest, stage)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    order = (SORTS[sort],) if sort in SORTS else (STAGE_ORDER[stage] if stage else (Lead.created_at.desc(),))
    rows = db.scalars(q.order_by(*order, Lead.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [lead_out(r) for r in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/board")
def board(search: str | None = None, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    """Kanban: 5 columns (Do-Not-Call leads are left out)."""
    columns = []
    for key, title, hint in BOARD_COLUMNS:
        q = _filtered(search, None, None, key)
        count = db.scalar(select(func.count()).select_from(q.subquery())) or 0
        rows = db.scalars(q.order_by(*STAGE_ORDER[key], Lead.id.desc()).limit(limit)).all()
        columns.append({"key": key, "title": title, "hint": hint, "count": count,
                        "leads": [lead_out(r) for r in rows]})
    return {"columns": columns}


def _phone(raw: str | None) -> str | None:
    """Normalised E.164 number, or None when it is not a callable number."""
    return normalize_phone(raw, allow_international=settings.allow_international)


@router.post("", status_code=201)
def create_lead(body: LeadCreate, db: Session = Depends(get_db)):
    phone = _phone(body.phone)
    if not phone:
        raise HTTPException(400, "Invalid phone number (Indian mobile numbers only)")
    if db.scalar(select(Lead.id).where(Lead.phone == phone)):
        raise HTTPException(409, "A lead with this phone number already exists")
    lead = Lead(**body.model_dump(exclude={"phone"}), phone=phone)
    lead.name = (lead.name or "").strip()
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead_out(lead)


@router.get("/template")
def template():
    return StreamingResponse(iter([lead_csv.TEMPLATE_CSV]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="leads_template.csv"'})


@router.get("/export")
def export(interest: str | None = None, status: str | None = None, search: str | None = None,
           db: Session = Depends(get_db)):
    q = _filtered(search, status, interest).order_by(Lead.rating.desc().nulls_last(), Lead.id)
    rows = ([l.name, l.phone, l.company or "", l.city or "", l.email or "", l.source or "", l.status,
             lead_stage(l), l.interest, l.rating if l.rating is not None else "", l.attempts,
             l.last_called_at.isoformat() if l.last_called_at else "", l.last_summary or "", l.notes or ""]
            for l in db.scalars(q).all())
    name = f"leads_{interest or 'all'}_{utcnow().strftime('%Y%m%d')}.csv".replace(",", "-")
    return csv_response(["name", "phone", "company", "city", "email", "source", "status", "stage", "interest",
                         "rating", "attempts", "last_called_at", "last_summary", "notes"], rows, name)


@router.post("/import")
def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Sync on purpose: FastAPI runs it in a worker thread, so a 100k-row import never freezes
    live calls on the event loop. All-or-nothing: an unreadable file imports nothing."""
    parsed, mapping = lead_csv.parse(lead_csv.read_upload(file))

    existing = set(db.scalars(select(Lead.phone)).all())
    imported = duplicates = invalid = 0
    errors: list[str] = []
    rows: list[dict] = []
    for line, row in parsed:
        phone = _phone(row.get(mapping["phone"]))
        if not phone:
            invalid += 1
            if len(errors) < 20:
                errors.append(f"Row {line}: invalid phone '{lead_csv.raw_phone(row, mapping)[:40]}'")
            continue
        if phone in existing:
            duplicates += 1
            continue
        existing.add(phone)
        data = lead_csv.lead_fields(row, mapping)
        now = utcnow()
        rows.append({"name": data.get("name") or "", "phone": phone, "company": data.get("company"),
                     "city": data.get("city"), "email": data.get("email"),
                     "source": data.get("source") or "CSV import", "notes": data.get("notes"),
                     "extra": data["extra"], "status": "new", "interest": "unknown", "attempts": 0,
                     "created_at": now, "updated_at": now})
    for i in range(0, len(rows), 2000):  # one transaction, chunked inserts
        db.execute(insert(Lead), rows[i:i + 2000])
    db.commit()
    imported = len(rows)
    return {"imported": imported, "duplicates": duplicates, "invalid": invalid, "errors": errors}


@router.post("/bulk-delete")
def bulk_delete(body: IdList, db: Session = Depends(get_db)):
    if not body.ids:
        return {"deleted": 0}
    deleted = 0
    for i in range(0, len(body.ids), 5000):  # SQLite allows ~32k bound variables per statement
        deleted += db.execute(delete(Lead).where(Lead.id.in_(body.ids[i:i + 5000]))).rowcount or 0
    db.commit()
    return {"deleted": deleted}


def _lead_calls(lead: Lead):
    """Every call of a lead: linked by lead_id, plus INBOUND calls from its number that have no lead (a
    caller who rang before the lead existed, or while it was on another call). Outbound rows without a lead
    are never matched by number: they may belong to a deleted lead whose number was recycled."""
    if not lead.phone:
        return Call.lead_id == lead.id
    # coalesce() on direction keeps SQLite on ix_calls_from_number instead of scanning every inbound row
    return or_(Call.lead_id == lead.id,
               and_(Call.lead_id.is_(None), func.coalesce(Call.direction, "") == "inbound",
                    Call.from_number == lead.phone))


def _call_items(db: Session, calls: list[Call]) -> list[dict]:
    states = follow_ups(db, calls)  # missed inbound calls: pending | tried | done
    items = []
    for c in calls:
        item = call_list_out(c)
        item["follow_up"] = states.get(c.uuid)
        items.append(item)
    return items


@router.get("/{lead_id}")
def get_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    calls = db.scalars(select(Call).options(selectinload(Call.lead)).where(_lead_calls(lead))
                       .order_by(Call.id.desc()).limit(50)).all()
    out = lead_out(lead)
    out["calls"] = _call_items(db, list(calls))
    return out


TIMELINE_MAX = 200
_KIND_RANK = {"call": 2, "campaign_added": 1, "lead_created": 0}  # same instant: the call is the newest
_NEVER = datetime.min.replace(tzinfo=timezone.utc)  # sort key for a row without any timestamp


@router.get("/{lead_id}/timeline")
def lead_timeline(lead_id: int, db: Session = Depends(get_db)):
    """Everything that happened with this customer, newest first: calls (both directions, browser tests
    included), campaigns they were added to, and when the lead was created."""
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    linked = _lead_calls(lead)
    when = func.coalesce(Call.started_at, Call.created_at)

    calls = list(db.scalars(select(Call).options(selectinload(Call.lead)).where(linked)
                            .order_by(when.desc(), Call.id.desc()).limit(TIMELINE_MAX)).all())
    states = follow_ups(db, calls)
    events: list[tuple[tuple, dict]] = []  # (sort key, event)
    for c in calls:
        at = c.started_at or c.created_at
        item = call_list_out(c)
        item["follow_up"] = states.get(c.uuid)
        key_points = [str(k) for k in c.key_points if k][:5] if isinstance(c.key_points, list) else []
        events.append(((at or _NEVER, _KIND_RANK["call"], c.id), {
            "id": f"call-{c.uuid}", "kind": "call", "at": iso(at), "call": item,
            "next_action": c.next_action or None, "callback_time": c.callback_time or None, "key_points": key_points}))

    # A CSV campaign is saved a moment BEFORE the leads it creates: never show "added" before "created".
    for camp_id, name, status, created in db.execute(
            select(Campaign.id, Campaign.name, Campaign.status, Campaign.created_at)
            .join(CampaignLead, CampaignLead.campaign_id == Campaign.id).where(CampaignLead.lead_id == lead.id)):
        at = max((t for t in (created, lead.created_at) if t is not None), default=None)
        events.append(((at or _NEVER, _KIND_RANK["campaign_added"], camp_id), {
            "id": f"campaign-{camp_id}", "kind": "campaign_added", "at": iso(at), "campaign_id": camp_id,
            "campaign_name": name, "campaign_status": status}))

    created = {"id": f"lead-{lead.id}", "kind": "lead_created", "at": iso(lead.created_at), "source": lead.source}
    events.append(((lead.created_at or _NEVER, _KIND_RANK["lead_created"], lead.id), created))
    events.sort(key=lambda e: e[0], reverse=True)
    ordered = [e for _, e in events]
    if len(ordered) > TIMELINE_MAX:  # cap the list, but "Lead created" is always kept
        head = ordered[:TIMELINE_MAX]
        ordered = head if any(e is created for e in head) else head[:-1] + [created]

    # Stats cover EVERY linked phone call (SQL aggregates), not just the events listed above.
    phone = and_(linked, Call.channel == "phone")
    answered = Call.status == "completed"
    n, inbound, n_answered, talk, first, last = db.execute(select(
        func.count(), func.sum(case((Call.direction == "inbound", 1), else_=0)), func.sum(case((answered, 1), else_=0)),
        func.sum(case((answered, func.coalesce(Call.duration_sec, 0)), else_=0)), func.min(when), func.max(when),
    ).where(phone)).one()
    n, inbound = n or 0, inbound or 0

    # Next step: the newest analysed conversation that left a next action or a callback time.
    nxt = db.scalars(select(Call).where(
        phone, conversation_condition(), Call.analysis_status == "done",
        or_(func.trim(func.coalesce(Call.next_action, "")) != "", func.trim(func.coalesce(Call.callback_time, "")) != ""))
        .order_by(when.desc(), Call.id.desc()).limit(1)).first()
    return {
        "lead": lead_out(lead),
        "events": ordered,
        "stats": {"calls": n, "inbound": inbound, "outbound": n - inbound, "answered": n_answered or 0,
                  "talk_sec": int(round(float(talk or 0))), "first_contact_at": iso(first), "last_contact_at": iso(last)},
        "next_step": None if nxt is None else {
            "next_action": (nxt.next_action or "").strip() or None, "callback_time": (nxt.callback_time or "").strip() or None,
            "call_uuid": nxt.uuid, "at": iso(nxt.started_at or nxt.created_at)},
    }


@router.patch("/{lead_id}")
def update_lead(lead_id: int, body: LeadUpdate, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    data = body.model_dump(exclude_unset=True)
    stage = data.pop("stage", None)
    if stage:  # Kanban move by the sales team (the next AI-scored call can move it again)
        if stage in ("cold", "warm", "hot"):
            data["interest"] = stage
        elif stage == "no_answer":
            data["interest"] = "unknown"
            lead.attempts = max(lead.attempts or 0, 1)
            if lead.status in ("new", "queued"):
                data.setdefault("status", "called")
        elif stage == "new":
            data["interest"] = "unknown"
            lead.attempts = 0
            if lead.status != "dnc":
                data.setdefault("status", "new")
    if "phone" in data:
        phone = _phone(data["phone"])
        if not phone:
            raise HTTPException(400, "Invalid phone number")
        dup = db.scalar(select(Lead.id).where(Lead.phone == phone, Lead.id != lead_id))
        if dup:
            raise HTTPException(409, "Another lead has this phone number")
        data["phone"] = phone
    for k, v in data.items():
        setattr(lead, k, v)
    lead.updated_at = utcnow()
    db.commit()
    db.refresh(lead)
    return lead_out(lead)


@router.delete("/{lead_id}")
def delete_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    db.delete(lead)
    db.commit()
    return {"ok": True}
