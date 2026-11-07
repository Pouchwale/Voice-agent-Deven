"""Calls: start, list, detail (summary + transcript), recording, hang up, re-analyze."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..db import get_db
from ..models import ACTIVE_CALL_STATUSES, Call, Lead
from ..schemas import CallCreate, call_list_out
from ..services import vobiz
from ..services.analysis import analyze_call
from ..services.call_manager import CallError, manager
from ..services.repo import get_call_detail_dict, run_db, update_call
from ..services.session import recording_abs_path

router = APIRouter(prefix="/api/calls", tags=["calls"])
settings = get_settings()


@router.post("", status_code=201)
async def start_call(body: CallCreate):
    try:
        return await manager.start_phone_call(body.lead_id)
    except CallError as e:
        raise HTTPException(e.status_code, str(e)) from e


@router.get("")
def list_calls(
    search: str | None = None, status: str | None = None, interest: str | None = None,
    channel: str | None = None, lead_id: int | None = None, campaign_id: int | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
):
    q = select(Call).outerjoin(Lead, Lead.id == Call.lead_id)
    if search:
        like = f"%{search.strip()}%"
        q = q.where(or_(Lead.name.ilike(like), Lead.phone.ilike(like), Lead.company.ilike(like),
                        Call.summary.ilike(like)))
    if status:
        q = q.where(Call.status.in_(status.split(",")))
    if interest:
        q = q.where(Call.interest.in_(interest.split(",")))
    if channel:
        q = q.where(Call.channel == channel)
    if lead_id:
        q = q.where(Call.lead_id == lead_id)
    if campaign_id:
        q = q.where(Call.campaign_id == campaign_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.options(selectinload(Call.lead)).order_by(Call.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [call_list_out(c) for c in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/active")
def active(db: Session = Depends(get_db)):
    rows = db.scalars(select(Call).options(selectinload(Call.lead))
                      .where(Call.status.in_(ACTIVE_CALL_STATUSES)).order_by(Call.id.desc())).all()
    return [call_list_out(c) for c in rows]


@router.get("/{call_uuid}")
async def detail(call_uuid: str):
    out = await run_db(get_call_detail_dict, call_uuid)
    if not out:
        raise HTTPException(404, "Call not found")
    return out


@router.post("/{call_uuid}/hangup")
async def hangup(call_uuid: str):
    await manager.hangup(call_uuid)
    return {"ok": True}


@router.post("/{call_uuid}/reanalyze")
async def reanalyze(call_uuid: str):
    exists = await run_db(get_call_detail_dict, call_uuid)
    if not exists:
        raise HTTPException(404, "Call not found")
    if exists["status"] in ACTIVE_CALL_STATUSES:
        raise HTTPException(409, "Call is still in progress")
    await run_db(lambda: update_call(call_uuid, analysis_status="pending"))
    await analyze_call(call_uuid)
    return await run_db(get_call_detail_dict, call_uuid)


@router.get("/{call_uuid}/recording")
async def recording(call_uuid: str, db: Session = Depends(get_db)):
    call = db.scalar(select(Call).where(Call.uuid == call_uuid))
    if not call:
        raise HTTPException(404, "Call not found")
    path = recording_abs_path(call.recording_path)
    if path:
        return FileResponse(path, media_type="audio/wav", filename=f"call-{call_uuid[:8]}.wav")
    if call.recording_url:  # fall back to the provider's copy (needs Vobiz auth headers)
        data = await vobiz.download_recording(call.recording_url)
        if data:
            media = "audio/wav" if data[:4] == b"RIFF" else "audio/mpeg"
            ext = "wav" if media == "audio/wav" else "mp3"
            cache = settings.recordings_dir / f"{call_uuid}-provider.{ext}"
            await asyncio.to_thread(cache.write_bytes, data)
            call.recording_path = cache.relative_to(settings.recordings_dir.parent).as_posix()
            db.commit()
            return Response(content=data, media_type=media)
    raise HTTPException(404, "No recording for this call")


@router.delete("/{call_uuid}")
def delete_call(call_uuid: str, db: Session = Depends(get_db)):
    call = db.scalar(select(Call).where(Call.uuid == call_uuid))
    if not call:
        raise HTTPException(404, "Call not found")
    if call.status in ACTIVE_CALL_STATUSES:
        raise HTTPException(409, "Hang up the call before deleting it")
    path = recording_abs_path(call.recording_path)
    if path:
        path.unlink(missing_ok=True)
    db.delete(call)
    db.commit()
    return {"ok": True}
