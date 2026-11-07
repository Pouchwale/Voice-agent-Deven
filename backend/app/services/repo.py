"""Small data-access helpers used by the async call engine (run in a worker thread)."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..db import SessionLocal, utcnow
from ..models import Call, Lead, TranscriptTurn
from ..schemas import call_detail_out, call_list_out, lead_out
from .events import bus

T = TypeVar("T")


async def run_db(fn: Callable[..., T], *args: Any) -> T:
    return await asyncio.to_thread(fn, *args)


def get_call_list_dict(call_uuid: str) -> dict | None:
    with SessionLocal() as db:
        call = db.scalar(select(Call).options(selectinload(Call.lead)).where(Call.uuid == call_uuid))
        return call_list_out(call) if call else None


def get_call_detail_dict(call_uuid: str) -> dict | None:
    with SessionLocal() as db:
        call = db.scalar(
            select(Call).options(selectinload(Call.lead), selectinload(Call.turns)).where(Call.uuid == call_uuid)
        )
        return call_detail_out(call) if call else None


def get_lead_dict(lead_id: int | None) -> dict | None:
    if not lead_id:
        return None
    with SessionLocal() as db:
        lead = db.get(Lead, lead_id)
        return lead_out(lead) if lead else None


def update_call(call_uuid: str, **fields: Any) -> dict | None:
    """Update columns of a call and return its list dict (also broadcast to the live feed)."""
    with SessionLocal() as db:
        call = db.scalar(select(Call).options(selectinload(Call.lead)).where(Call.uuid == call_uuid))
        if not call:
            return None
        for k, v in fields.items():
            setattr(call, k, v)
        db.commit()
        db.refresh(call)
        out = call_list_out(call)
    bus.publish({"type": "call.update", "call": out})
    return out


def add_turn(call_id: int, role: str, text: str, offset_ms: int, *, interrupted: bool = False,
             latency_ms: int | None = None) -> int:
    with SessionLocal() as db:
        t = TranscriptTurn(call_id=call_id, role=role, text=text, offset_ms=max(0, int(offset_ms)),
                           interrupted=interrupted, latency_ms=latency_ms)
        db.add(t)
        db.commit()
        return t.id


def mark_turn_interrupted(turn_id: int, text: str | None = None) -> None:
    with SessionLocal() as db:
        t = db.get(TranscriptTurn, turn_id)
        if t:
            t.interrupted = True
            if text:
                t.text = text
            db.commit()


def set_lead_fields(lead_id: int | None, **fields: Any) -> None:
    if not lead_id:
        return
    with SessionLocal() as db:
        lead = db.get(Lead, lead_id)
        if not lead:
            return
        for k, v in fields.items():
            setattr(lead, k, v)
        lead.updated_at = utcnow()
        db.commit()


def now() -> datetime:
    return utcnow()
