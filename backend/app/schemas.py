"""Request bodies + serializers (dicts match docs/API_CONTRACT.md)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .models import Call, Campaign, Lead, TranscriptTurn


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    from .db import UTCDateTime

    return UTCDateTime().process_bind_param(dt, None)


# ---------------- request bodies ----------------
class LeadCreate(BaseModel):
    name: str = ""
    phone: str
    company: str | None = None
    city: str | None = None
    email: str | None = None
    source: str | None = None
    notes: str | None = None


class LeadUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    company: str | None = None
    city: str | None = None
    email: str | None = None
    source: str | None = None
    notes: str | None = None
    status: Literal["new", "queued", "calling", "called", "failed", "dnc"] | None = None
    interest: Literal["unknown", "cold", "warm", "hot"] | None = None
    stage: Literal["new", "no_answer", "cold", "warm", "hot"] | None = None  # Kanban move


class IdList(BaseModel):
    ids: list[int] = Field(default_factory=list)


class CallCreate(BaseModel):
    lead_id: int


class CampaignFilter(BaseModel):
    status: str | None = None
    interest: str | None = None
    search: str | None = None


class CampaignCreate(BaseModel):
    name: str
    lead_ids: list[int] | None = None
    filter: CampaignFilter | None = None
    concurrency: int = Field(default=2, ge=1, le=10)
    max_attempts: int = Field(default=2, ge=1, le=5)
    retry_delay_min: int = Field(default=30, ge=1, le=1440)
    respect_call_window: bool = True


class VoicePreview(BaseModel):
    text: str = Field(max_length=500)
    voice: str | None = None
    pace: float | None = None


# ---------------- serializers ----------------
def lead_stage(lead: Lead) -> str:
    """Board column: scored leads by interest; otherwise new (never called) / no_answer (called, not reached)."""
    if lead.interest in ("cold", "warm", "hot"):
        return lead.interest
    return "no_answer" if (lead.attempts or 0) > 0 else "new"


def lead_out(lead: Lead) -> dict[str, Any]:
    return {
        "id": lead.id,
        "name": lead.name,
        "phone": lead.phone,
        "company": lead.company,
        "city": lead.city,
        "email": lead.email,
        "source": lead.source,
        "notes": lead.notes,
        "status": lead.status,
        "interest": lead.interest,
        "stage": lead_stage(lead),
        "rating": lead.rating,
        "attempts": lead.attempts,
        "last_called_at": iso(lead.last_called_at),
        "last_summary": lead.last_summary,
        "created_at": iso(lead.created_at),
        "updated_at": iso(lead.updated_at),
    }


def call_list_out(call: Call) -> dict[str, Any]:
    lead = call.lead
    return {
        "uuid": call.uuid,
        "lead_id": call.lead_id,
        "lead_name": lead.name if lead else None,
        "lead_phone": lead.phone if lead else call.to_number,
        "lead_company": lead.company if lead else None,
        "campaign_id": call.campaign_id,
        "channel": call.channel,
        "status": call.status,
        "end_reason": call.end_reason,
        "started_at": iso(call.started_at),
        "answered_at": iso(call.answered_at),
        "ended_at": iso(call.ended_at),
        "duration_sec": round(call.duration_sec, 1) if call.duration_sec is not None else None,
        "rating": call.rating,
        "interest": call.interest,
        "qualified": call.qualified,
        "summary": call.summary,
        "analysis_status": call.analysis_status,
        "has_recording": bool(call.recording_path or call.recording_url),
        "created_at": iso(call.created_at),
    }


def turn_out(t: TranscriptTurn) -> dict[str, Any]:
    return {
        "id": t.id,
        "role": t.role,
        "text": t.text,
        "offset_ms": t.offset_ms,
        "interrupted": t.interrupted,
        "latency_ms": t.latency_ms,
        "created_at": iso(t.created_at),
    }


def call_detail_out(call: Call) -> dict[str, Any]:
    data = call_list_out(call)
    data.update(
        {
            "from_number": call.from_number,
            "to_number": call.to_number,
            "sentiment": call.sentiment,
            "next_action": call.next_action,
            "callback_time": call.callback_time,
            "qualification": call.qualification,
            "key_points": call.key_points,
            "objections": call.objections,
            "metrics": call.metrics,
            "error": call.error,
            "recording_url": f"/api/calls/{call.uuid}/recording" if (call.recording_path or call.recording_url) else None,
            "transcript": [turn_out(t) for t in call.turns],
            "lead": lead_out(call.lead) if call.lead else None,
        }
    )
    return data


def campaign_out(c: Campaign, counts: dict[str, int], interest_counts: dict[str, int], active: int) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "status": c.status,
        "concurrency": c.concurrency,
        "max_attempts": c.max_attempts,
        "retry_delay_min": c.retry_delay_min,
        "respect_call_window": c.respect_call_window,
        "created_at": iso(c.created_at),
        "started_at": iso(c.started_at),
        "completed_at": iso(c.completed_at),
        "counts": counts,
        "interest_counts": interest_counts,
        "active_calls": active,
    }
