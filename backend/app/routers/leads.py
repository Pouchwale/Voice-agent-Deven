"""Leads: CRUD, search/filter/sort, CSV import/export."""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, insert, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db, utcnow
from ..models import Call, Lead
from ..schemas import IdList, LeadCreate, LeadUpdate, call_list_out, lead_out, lead_stage
from ..services.csvsafe import csv_response
from ..services.phone import normalize_phone

settings = get_settings()

router = APIRouter(prefix="/api/leads", tags=["leads"])

SORTS = {
    "created_at": Lead.created_at.asc(), "-created_at": Lead.created_at.desc(),
    "rating": Lead.rating.asc().nulls_first(), "-rating": Lead.rating.desc().nulls_last(),
    "last_called_at": Lead.last_called_at.asc().nulls_first(),
    "-last_called_at": Lead.last_called_at.desc().nulls_last(),
    "name": Lead.name.asc(), "-name": Lead.name.desc(),
}

COLUMN_ALIASES = {
    "name": ("name", "full name", "fullname", "customer", "customer name", "contact name", "lead name", "naam"),
    "phone": ("phone", "mobile", "number", "contact", "phone number", "mobile number", "contact number",
              "whatsapp", "mobile no", "phone no", "cell"),
    "company": ("company", "business", "brand", "company name", "business name", "firm", "organization",
                "organisation"),
    "city": ("city", "location", "town", "district", "area", "place"),
    "email": ("email", "e-mail", "email id", "mail"),
    "source": ("source", "lead source", "campaign", "channel"),
    "notes": ("notes", "remarks", "comment", "comments", "note", "requirement", "description"),
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
    content = ("name,phone,company,city,email,source,notes\n"
               "Rahul Sharma,9876543210,Sharma Snacks,Ahmedabad,rahul@example.com,IndiaMART,Namkeen brand\n"
               "Priya Patel,+91 98250 12345,Organic Roots,Pune,,Website,Needs stand-up pouches\n")
    return StreamingResponse(iter([content]), media_type="text/csv",
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


def _map_columns(headers: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    norm = {h: h.strip().lower().replace("_", " ") for h in headers}
    for field, aliases in COLUMN_ALIASES.items():
        for h, n in norm.items():
            if h in mapping.values():
                continue
            if n in aliases:
                mapping[field] = h
                break
    if "phone" not in mapping:  # fuzzy: any header containing phone/mobile/number
        for h, n in norm.items():
            if any(k in n for k in ("phone", "mobile", "number", "contact")) and h not in mapping.values():
                mapping["phone"] = h
                break
    return mapping


MAX_UPLOAD = 20 * 1024 * 1024


def _decode(raw: bytes) -> str:
    """UTF-8 (with/without BOM), UTF-16 only when it has a BOM, else Excel's Windows-1252."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


@router.post("/import")
def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Sync on purpose: FastAPI runs it in a worker thread, so a 100k-row import never freezes
    live calls on the event loop. All-or-nothing: an unreadable file imports nothing."""
    raw = file.file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (max 20 MB)")
    if not raw.strip():
        raise HTTPException(400, "The file is empty")
    text = _decode(raw)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    csv.field_size_limit(1_000_000)
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = reader.fieldnames or []
    mapping = _map_columns(headers)
    if "phone" not in mapping:
        raise HTTPException(400, f"No phone column found. Columns seen: {', '.join(headers) or 'none'}")

    existing = set(db.scalars(select(Lead.phone)).all())
    imported = duplicates = invalid = 0
    errors: list[str] = []
    rows: list[dict] = []
    line = 1
    try:
        for line, row in enumerate(reader, start=2):
            phone = _phone(row.get(mapping["phone"]))
            if not phone:
                invalid += 1
                if len(errors) < 20:
                    errors.append(f"Row {line}: invalid phone '{(row.get(mapping['phone']) or '').strip()[:40]}'")
                continue
            if phone in existing:
                duplicates += 1
                continue
            existing.add(phone)
            data: dict[str, Any] = {f: (row.get(col) or "").strip()[:500] or None
                                    for f, col in mapping.items() if f != "phone"}
            extra = {k: str(v)[:500] for k, v in row.items()
                     if k and k not in mapping.values() and v not in (None, "")}
            now = utcnow()
            rows.append({"name": data.get("name") or "", "phone": phone, "company": data.get("company"),
                         "city": data.get("city"), "email": data.get("email"),
                         "source": data.get("source") or "CSV import", "notes": data.get("notes"),
                         "extra": extra or None, "status": "new", "interest": "unknown", "attempts": 0,
                         "created_at": now, "updated_at": now})
    except csv.Error as e:
        raise HTTPException(400, f"The CSV is malformed near row {line + 1}: {e}. Nothing was imported.") from None
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


@router.get("/{lead_id}")
def get_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    calls = db.scalars(select(Call).where(Call.lead_id == lead_id).order_by(Call.id.desc()).limit(50)).all()
    out = lead_out(lead)
    for c in calls:
        c.lead = lead
    out["calls"] = [call_list_out(c) for c in calls]
    return out


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
