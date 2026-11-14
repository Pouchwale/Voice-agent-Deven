"""Campaigns (bulk auto-dialing of a lead list)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, insert, literal, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db, utcnow
from ..models import Campaign, CampaignLead, Lead
from ..schemas import CampaignCreate, iso, lead_out
from ..services import lead_csv
from ..services.agent_config import load_agent_settings
from ..services.call_manager import manager
from ..services.campaign_stats import FILTERS, item_result, items_with_calls, result_condition
from ..services.dialer import campaign_dict, dialer
from ..services.events import bus
from ..services.phone import phone_problem
from .leads import _filtered, _phone

settings = get_settings()
router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])

PREVIEW_ROWS = 5000   # rows listed in a CSV preview (the summary always covers the whole file)
CHUNK = 2000          # rows per INSERT / IN (...) (SQLite caps bound variables per statement)


def _get(db: Session, cid: int) -> Campaign:
    c = db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    return c


def _publish(db: Session, c: Campaign) -> dict:
    out = campaign_dict(db, c)
    bus.publish({"type": "campaign.update", "campaign": out})
    return out


def _new_campaign(db: Session, name: str, concurrency: int, max_attempts: int, retry_delay_min: int,
                  respect_call_window: bool) -> Campaign:
    c = Campaign(name=name.strip()[:200] or "Campaign", concurrency=concurrency, max_attempts=max_attempts,
                 retry_delay_min=retry_delay_min, respect_call_window=respect_call_window)
    db.add(c)
    db.flush()
    return c


def _queue_leads(db: Session, c: Campaign) -> None:
    db.execute(update(Lead).where(Lead.status == "new", Lead.id.in_(
        select(CampaignLead.lead_id).where(CampaignLead.campaign_id == c.id))).values(status="queued"))


@router.get("")
def list_campaigns(db: Session = Depends(get_db)):
    return [campaign_dict(db, c) for c in db.scalars(select(Campaign).order_by(Campaign.id.desc())).all()]


@router.post("", status_code=201)
def create_campaign(body: CampaignCreate, db: Session = Depends(get_db)):
    """Lead selection runs inside SQLite (INSERT ... SELECT), so a 100k-lead campaign never hits
    SQLite's bound-variable limit or holds the write lock while Python builds rows."""
    if body.lead_ids is not None:
        if not body.lead_ids:
            raise HTTPException(400, "No leads selected")
        ids = list(dict.fromkeys(body.lead_ids))
        chunks = [ids[i:i + 5000] for i in range(0, len(ids), 5000)]
        selections = [select(Lead.id).where(Lead.id.in_(chunk), Lead.status != "dnc") for chunk in chunks]
    else:
        f = body.filter
        q = _filtered(f.search if f else None, f.status if f else None, f.interest if f else None)
        selections = [q.with_only_columns(Lead.id).where(Lead.status != "dnc")]
    c = _new_campaign(db, body.name, body.concurrency, body.max_attempts, body.retry_delay_min,
                      body.respect_call_window)
    added = 0
    for sel in selections:
        sub = sel.subquery()
        added += db.execute(
            insert(CampaignLead).from_select(
                ["campaign_id", "lead_id", "status", "attempts"],
                select(literal(c.id), sub.c.id, literal("pending"), literal(0)),
            ).prefix_with("OR IGNORE")
        ).rowcount or 0
    if not added:
        db.rollback()
        raise HTTPException(400, "No callable leads selected (Do-Not-Call leads are skipped)")
    _queue_leads(db, c)
    db.commit()
    db.refresh(c)
    return _publish(db, c)


# ---------------------------------------------------------------- CSV upload
@router.get("/template")
def csv_template():
    return StreamingResponse(iter([lead_csv.TEMPLATE_CSV]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="campaign_template.csv"'})


def _analyze_csv(db: Session, raw: bytes) -> tuple[list[tuple[dict, dict]], dict[str, int]]:
    """Every data row with its verdict, in file order:
    new (becomes a lead) | existing (already a lead) | duplicate (number repeats an earlier row)
    | invalid (no callable number) | dnc (existing lead marked Do-Not-Call).
    Fully blank rows (Excel's trailing ",,,,") are ignored."""
    parsed, mapping = lead_csv.parse(raw)
    first_line: dict[str, int] = {}
    rows: list[tuple[dict, dict]] = []
    for line, row in parsed:
        if not any(str(v or "").strip() for k, v in row.items() if k):
            continue
        raw_phone = lead_csv.raw_phone(row, mapping)
        fields = lead_csv.lead_fields(row, mapping)
        phone = _phone(raw_phone)
        entry: dict[str, Any] = {"line": line, "name": fields.get("name") or "", "raw_phone": raw_phone[:40],
                                 "phone": phone, "company": fields.get("company"), "city": fields.get("city"),
                                 "status": "new", "problem": None, "duplicate_of": None, "lead_id": None}
        if not phone:
            entry["status"] = "invalid"
            entry["problem"] = phone_problem(raw_phone, settings.allow_international) or "Not a valid number"
        elif phone in first_line:
            entry["status"] = "duplicate"
            entry["duplicate_of"] = first_line[phone]
            entry["problem"] = f"Same number as row {first_line[phone]}"
        else:
            first_line[phone] = line
        rows.append((entry, fields))

    known: dict[str, tuple[int, str]] = {}
    phones = list(first_line)
    for i in range(0, len(phones), CHUNK):
        for lead_id, phone, status in db.execute(
                select(Lead.id, Lead.phone, Lead.status).where(Lead.phone.in_(phones[i:i + CHUNK]))):
            known[phone] = (lead_id, status)
    for entry, _ in rows:
        if entry["status"] == "new" and entry["phone"] in known:
            lead_id, status = known[entry["phone"]]
            entry["lead_id"] = lead_id
            if status == "dnc":
                entry["status"] = "dnc"
                entry["problem"] = "Marked Do-Not-Call: will not be dialed"
            else:
                entry["status"] = "existing"

    summary = dict.fromkeys(("new", "existing", "duplicate", "invalid", "dnc"), 0)
    for entry, _ in rows:
        summary[entry["status"]] += 1
    summary["rows"] = len(rows)
    summary["unique"] = len(first_line)
    summary["callable"] = summary["new"] + summary["existing"]
    return rows, summary


@router.post("/csv/preview")
def preview_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Read-only check of a campaign CSV: every number with its verdict (nothing is saved)."""
    rows, summary = _analyze_csv(db, lead_csv.read_upload(file))
    return {"summary": summary, "rows": [e for e, _ in rows[:PREVIEW_ROWS]],
            "truncated": len(rows) > PREVIEW_ROWS}


@router.post("/csv", status_code=201)
def create_campaign_from_csv(
    file: UploadFile = File(...),
    name: str = Form(..., max_length=200),
    concurrency: int = Form(2, ge=1, le=10),
    max_attempts: int = Form(2, ge=1, le=5),
    retry_delay_min: int = Form(30, ge=1, le=1440),
    respect_call_window: bool = Form(True),
    db: Session = Depends(get_db),
):
    """Campaign straight from a CSV: duplicates and invalid numbers are dropped, numbers that are
    already leads are reused (blank name/company/city filled in from the file), new numbers become
    leads, Do-Not-Call leads are left out. Numbers are dialed in file order. All-or-nothing."""
    rows, summary = _analyze_csv(db, lead_csv.read_upload(file))
    if not summary["callable"]:
        raise HTTPException(400, "No callable numbers in this file "
                                 f"({summary['invalid']} invalid, {summary['duplicate']} duplicates, "
                                 f"{summary['dnc']} Do-Not-Call).")
    c = _new_campaign(db, name, concurrency, max_attempts, retry_delay_min, respect_call_window)
    now = utcnow()
    source = f"Campaign: {c.name}"[:120]

    new_rows = [{"name": f.get("name") or "", "phone": e["phone"], "company": f.get("company"),
                 "city": f.get("city"), "email": f.get("email"), "source": f.get("source") or source,
                 "notes": f.get("notes"), "extra": f["extra"], "status": "new", "interest": "unknown",
                 "attempts": 0, "created_at": now, "updated_at": now}
                for e, f in rows if e["status"] == "new"]
    for i in range(0, len(new_rows), CHUNK):  # OR IGNORE: a lead created meanwhile is simply reused
        db.execute(insert(Lead).prefix_with("OR IGNORE"), new_rows[i:i + CHUNK])

    wanted = [e["phone"] for e, _ in rows if e["status"] in ("new", "existing")]
    leads: dict[str, Lead] = {}
    for i in range(0, len(wanted), CHUNK):
        for lead in db.scalars(select(Lead).where(Lead.phone.in_(wanted[i:i + CHUNK]))):
            leads[lead.phone] = lead
    for e, f in rows:  # fill blanks of reused leads, never overwrite what the team typed
        lead = leads.get(e["phone"]) if e["status"] == "existing" else None
        if lead is None:
            continue
        for field in ("name", "company", "city", "email"):
            if f.get(field) and not getattr(lead, field):
                setattr(lead, field, f[field])
                lead.updated_at = now
    db.flush()

    items = [{"campaign_id": c.id, "lead_id": leads[p].id, "status": "pending", "attempts": 0}
             for p in wanted if p in leads and leads[p].status != "dnc"]
    if not items:
        db.rollback()
        raise HTTPException(400, "No callable numbers in this file")
    for i in range(0, len(items), CHUNK):  # file order = dial order
        db.execute(insert(CampaignLead).prefix_with("OR IGNORE"), items[i:i + CHUNK])
    _queue_leads(db, c)
    db.commit()
    db.refresh(c)
    out = _publish(db, c)
    out["upload"] = summary
    return out


# ---------------------------------------------------------------- one campaign
def _item_out(item: CampaignLead, call, lead: Lead) -> dict:
    answered = bool(call is not None and call.answered_at is not None)
    return {
        "lead": lead_out(lead),
        "status": item.status,
        "attempts": item.attempts,
        "last_call_uuid": item.last_call_uuid if call is not None else None,
        "next_attempt_at": iso(item.next_attempt_at) if item.status == "pending" else None,
        "result": item_result(item.status, call.status if call is not None else None, answered),
        "last_call": None if call is None else {
            "status": call.status, "end_reason": call.end_reason, "started_at": iso(call.started_at),
            "answered_at": iso(call.answered_at), "ended_at": iso(call.ended_at),
            "duration_sec": round(call.duration_sec, 1) if call.duration_sec is not None else None,
        },
    }


@router.get("/{cid}")
def get_campaign(
    cid: int,
    result: str | None = Query(None, description="waiting|ongoing|ringing|talking|picked_up|no_answer|busy|failed|skipped"),
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    c = _get(db, cid)
    out = campaign_dict(db, c)
    q = items_with_calls(cid)
    if result:
        if result not in FILTERS:
            raise HTTPException(422, f"Unknown result filter '{result}'")
        q = q.where(result_condition(result))
    out["items_total"] = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.execute(q.order_by(CampaignLead.id).offset((page - 1) * page_size).limit(page_size)).all()
    lead_ids = [item.lead_id for item, _ in rows]
    leads = {lead.id: lead for lead in db.scalars(select(Lead).where(Lead.id.in_(lead_ids)))} if lead_ids else {}
    out["items"] = [_item_out(item, call, leads[item.lead_id]) for item, call in rows if item.lead_id in leads]
    out["page"], out["page_size"] = page, page_size
    return out


@router.post("/{cid}/start")
def start_campaign(cid: int, db: Session = Depends(get_db)):
    c = _get(db, cid)
    if c.status == "completed":
        raise HTTPException(409, "Campaign already completed")
    if not load_agent_settings().get("outbound_enabled", True):
        raise HTTPException(400, "Outbound calling is turned off in Settings.")
    problems = manager.provider_problems(phone=True)
    if problems:
        raise HTTPException(400, "Cannot start campaign: " + "; ".join(problems))
    c.status = "running"
    c.started_at = c.started_at or utcnow()
    db.commit()
    dialer.wake()
    return _publish(db, c)


@router.post("/{cid}/pause")
def pause_campaign(cid: int, db: Session = Depends(get_db)):
    c = _get(db, cid)
    if c.status == "running":
        c.status = "paused"
        db.commit()
    return _publish(db, c)


@router.delete("/{cid}")
def delete_campaign(cid: int, db: Session = Depends(get_db)):
    c = _get(db, cid)
    if c.status == "running":
        raise HTTPException(409, "Pause the campaign before deleting it")
    db.delete(c)
    db.commit()
    return {"ok": True}
