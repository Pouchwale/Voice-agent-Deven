"""Campaigns (bulk auto-dialing of a lead list)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import insert, literal, select, update
from sqlalchemy.orm import Session, selectinload

from ..db import get_db, utcnow
from ..models import Campaign, CampaignLead, Lead
from ..schemas import CampaignCreate, lead_out
from ..services.call_manager import manager
from ..services.dialer import campaign_dict, dialer
from ..services.events import bus
from .leads import _filtered

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


def _get(db: Session, cid: int) -> Campaign:
    c = db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    return c


def _publish(db: Session, c: Campaign) -> dict:
    out = campaign_dict(db, c)
    bus.publish({"type": "campaign.update", "campaign": out})
    return out


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
    c = Campaign(name=body.name.strip() or "Campaign", concurrency=body.concurrency, max_attempts=body.max_attempts,
                 retry_delay_min=body.retry_delay_min, respect_call_window=body.respect_call_window)
    db.add(c)
    db.flush()
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
    db.execute(update(Lead).where(Lead.status == "new", Lead.id.in_(
        select(CampaignLead.lead_id).where(CampaignLead.campaign_id == c.id))).values(status="queued"))
    db.commit()
    db.refresh(c)
    return _publish(db, c)


@router.get("/{cid}")
def get_campaign(cid: int, db: Session = Depends(get_db)):
    c = _get(db, cid)
    out = campaign_dict(db, c)
    items = db.scalars(select(CampaignLead).options(selectinload(CampaignLead.lead))
                       .where(CampaignLead.campaign_id == cid).order_by(CampaignLead.id).limit(500)).all()
    out["items"] = [{"lead": lead_out(i.lead), "status": i.status, "attempts": i.attempts,
                     "last_call_uuid": i.last_call_uuid} for i in items if i.lead]
    return out


@router.post("/{cid}/start")
def start_campaign(cid: int, db: Session = Depends(get_db)):
    c = _get(db, cid)
    if c.status == "completed":
        raise HTTPException(409, "Campaign already completed")
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
