"""Analytics (charts) + report downloads (PDF / CSV) for a custom date range."""
from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models import Campaign, Lead
from ..schemas import lead_stage
from ..services.agent_config import load_agent_settings
from ..services.csvsafe import csv_response
from ..services.pdf_report import build_report
from ..services.reporting import IST, calls_in_range, compute_analytics, ist, parse_range

router = APIRouter(tags=["analytics"])
_pdf_slots = asyncio.Semaphore(1)  # PDF builds are CPU-heavy: one at a time keeps live calls smooth


@router.get("/api/analytics")
def analytics(from_: str | None = Query(None, alias="from"), to: str | None = None, campaign_id: int | None = None,
              include_tests: bool = False, db: Session = Depends(get_db)):
    rng = parse_range(from_, to)
    calls = calls_in_range(db, rng, campaign_id, include_tests)
    return compute_analytics(db, calls, rng)


def _filename(kind: str, ext: str, rng) -> str:
    return f"voice-agent-{kind}_{rng.start.isoformat()}_to_{rng.end.isoformat()}.{ext}"


def _csv_response(rows: list[list], headers: list[str], filename: str) -> StreamingResponse:
    return csv_response(headers, rows, filename)  # Excel-safe cells (no formula injection)


def _fmt(dt: datetime | None) -> str:
    return ist(dt).strftime("%Y-%m-%d %H:%M:%S") if dt else ""


@router.get("/api/reports/calls.csv")
def calls_csv(from_: str | None = Query(None, alias="from"), to: str | None = None, campaign_id: int | None = None,
              include_transcripts: bool = True, include_tests: bool = False, db: Session = Depends(get_db)):
    rng = parse_range(from_, to)
    calls = calls_in_range(db, rng, campaign_id, include_tests, with_turns=include_transcripts)
    camp = {cid: name for cid, name in db.execute(select(Campaign.id, Campaign.name)).all()}
    headers = ["call_id", "date_time_ist", "lead_name", "phone", "company", "city", "campaign", "channel", "status",
               "end_reason", "duration_sec", "interest", "rating", "qualified", "sentiment", "summary",
               "next_action", "callback_time", "key_points", "objections", "objection_tags", "qualification",
               "avg_response_ms", "recording_url"]
    if include_transcripts:
        headers.append("transcript")
    rows = []
    for c in calls:
        lead = c.lead
        qual = " | ".join(f"{q.get('question')}: {q.get('answer') or '-'} ({q.get('status')})"
                          for q in (c.qualification or []))
        row = [c.uuid, _fmt(c.created_at), lead.name if lead else "", lead.phone if lead else (c.to_number or ""),
               (lead.company if lead else "") or "", (lead.city if lead else "") or "",
               camp.get(c.campaign_id, "") if c.campaign_id else "", c.channel, c.status, c.end_reason or "",
               c.duration_sec if c.duration_sec is not None else "", c.interest,
               c.rating if c.rating is not None else "", "" if c.qualified is None else ("yes" if c.qualified else "no"),
               c.sentiment or "", c.summary or "", c.next_action or "", c.callback_time or "",
               "; ".join(c.key_points or []), "; ".join(c.objections or []),
               ", ".join((c.metrics or {}).get("objection_tags", [])), qual,
               (c.metrics or {}).get("avg_latency_ms", ""),
               f"/api/calls/{c.uuid}/recording" if (c.recording_path or c.recording_url) else ""]
        if include_transcripts:
            row.append("\n".join(f"[{t.offset_ms // 60000:02d}:{t.offset_ms // 1000 % 60:02d}] "
                                 f"{'Agent' if t.role == 'agent' else 'Customer'}: {t.text}"
                                 for t in c.turns if t.role in ("agent", "user")))
        rows.append(row)
    return _csv_response(rows, headers, _filename("calls", "csv", rng))


@router.get("/api/reports/leads.csv")
def leads_csv(from_: str | None = Query(None, alias="from"), to: str | None = None, stage: str | None = None,
              db: Session = Depends(get_db)):
    q = select(Lead)
    rng = None
    if from_ or to:
        rng = parse_range(from_, to)
        q = q.where(Lead.last_called_at >= rng.start_utc, Lead.last_called_at < rng.end_utc)
    leads = list(db.scalars(q.order_by(Lead.rating.desc().nulls_last(), Lead.id)).all())
    if stage:
        leads = [l for l in leads if lead_stage(l) == stage]
    headers = ["name", "phone", "company", "city", "email", "source", "stage", "interest", "rating", "status",
               "attempts", "last_called_ist", "last_summary", "notes", "created_ist"]
    rows = [[l.name, l.phone, l.company or "", l.city or "", l.email or "", l.source or "", lead_stage(l),
             l.interest, l.rating if l.rating is not None else "", l.status, l.attempts, _fmt(l.last_called_at),
             l.last_summary or "", l.notes or "", _fmt(l.created_at)] for l in leads]
    name = _filename("leads", "csv", rng) if rng else f"voice-agent-leads_{datetime.now(IST).date()}.csv"
    return _csv_response(rows, headers, name)


@router.get("/api/reports/summary.pdf")
async def summary_pdf(from_: str | None = Query(None, alias="from"), to: str | None = None,
                      campaign_id: int | None = None, include_transcripts: bool = False, include_tests: bool = False):
    rng = parse_range(from_, to)

    def build() -> bytes:
        with SessionLocal() as db:
            calls = calls_in_range(db, rng, campaign_id, include_tests, with_turns=include_transcripts)
            data = compute_analytics(db, calls, rng)
            camp = db.get(Campaign, campaign_id).name if campaign_id and db.get(Campaign, campaign_id) else None
            cfg = load_agent_settings()
            return build_report(calls, data, rng, campaign_name=camp, include_transcripts=include_transcripts,
                                company=cfg.get("company_name") or "")

    async with _pdf_slots:
        pdf = await asyncio.to_thread(build)  # CPU-bound: keep the event loop (live calls) responsive
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{_filename("report", "pdf", rng)}"'})
