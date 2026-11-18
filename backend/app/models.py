"""SQLAlchemy models. Mirrored 1:1 in frontend/prisma/schema.prisma."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, JSONText, UTCDateTime, utcnow

# Lead pipeline values
LEAD_STATUSES = ("new", "queued", "calling", "called", "failed", "dnc")
INTERESTS = ("unknown", "cold", "warm", "hot")
# Kanban board columns (derived from interest + attempts, see schemas.lead_stage)
STAGES = ("new", "no_answer", "cold", "warm", "hot")
CALL_STATUSES = (
    "queued", "initiated", "ringing", "in_progress", "completed",
    "no_answer", "busy", "failed", "canceled",
)
ACTIVE_CALL_STATUSES = ("queued", "initiated", "ringing", "in_progress")


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    interest: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_called_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    last_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[Any] = mapped_column(JSONText, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    calls: Mapped[list["Call"]] = relationship(back_populates="lead", order_by="desc(Call.id)")


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)  # draft|running|paused|completed
    concurrency: Mapped[int] = mapped_column(Integer, default=2)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    retry_delay_min: Mapped[int] = mapped_column(Integer, default=30)
    respect_call_window: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    items: Mapped[list["CampaignLead"]] = relationship(back_populates="campaign", cascade="all, delete-orphan")


class CampaignLead(Base):
    __tablename__ = "campaign_leads"
    __table_args__ = (
        UniqueConstraint("campaign_id", "lead_id", name="uq_campaign_lead"),
        Index("ix_campaign_leads_campaign_status", "campaign_id", "status"),
        Index("ix_campaign_leads_last_call_uuid", "last_call_uuid"),
        Index("ix_campaign_leads_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|dialing|done|failed|skipped
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_call_uuid: Mapped[str | None] = mapped_column(String(40), nullable=True)

    campaign: Mapped[Campaign] = relationship(back_populates="items")
    lead: Mapped[Lead] = relationship()


class Call(Base):
    __tablename__ = "calls"
    __table_args__ = (
        Index("ix_calls_created", "created_at"),
        # At most ONE live PHONE call per lead, enforced by SQLite itself (no double-dial race). Browser
        # test calls are excluded so a test run never blocks a real customer call (or vice versa).
        # Also (re)created by main._migrate for databases that existed before this index.
        Index("uq_calls_active_lead", "lead_id", unique=True,
              sqlite_where=text("lead_id IS NOT NULL AND channel = 'phone' "
                                "AND status IN ('queued','initiated','ringing','in_progress')")),
        # One row per provider inbound call: answer-webhook retries can never create a second row.
        Index("uq_calls_inbound_provider", "provider_call_id", unique=True,
              sqlite_where=text("direction = 'inbound' AND provider_call_id IS NOT NULL AND provider_call_id <> ''")),
        Index("ix_calls_direction", "direction", "id"),
        Index("ix_calls_from_number", "from_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    provider_call_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(16), default="phone")  # phone|browser
    direction: Mapped[str] = mapped_column(String(16), default="outbound")  # outbound|inbound
    # inbound only: returning (spoke with us before) | known (lead existed, never spoke) | new (lead created by
    # this call) | international (foreign number, no lead) | anonymous (hidden / unusable number)
    caller_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    end_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    from_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    recording_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    recording_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # --- post-call analysis ---
    analysis_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|done|failed|skipped
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    interest: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    qualified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    next_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    callback_time: Mapped[str | None] = mapped_column(String(120), nullable=True)
    qualification: Mapped[Any] = mapped_column(JSONText, nullable=True)
    key_points: Mapped[Any] = mapped_column(JSONText, nullable=True)
    objections: Mapped[Any] = mapped_column(JSONText, nullable=True)
    metrics: Mapped[Any] = mapped_column(JSONText, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    lead: Mapped[Lead | None] = relationship(back_populates="calls")
    turns: Mapped[list["TranscriptTurn"]] = relationship(
        back_populates="call", order_by="TranscriptTurn.id", cascade="all, delete-orphan"
    )


class TranscriptTurn(Base):
    __tablename__ = "transcript_turns"
    __table_args__ = (Index("ix_turns_call", "call_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(10))  # agent|user|system
    text: Mapped[str] = mapped_column(Text)
    offset_ms: Mapped[int] = mapped_column(Integer, default=0)  # ms since call start
    interrupted: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # agent: user-end -> first audio
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    call: Mapped[Call] = relationship(back_populates="turns")


class AgentSettings(Base):
    """Single-row table (id=1) holding the agent's editable configuration."""

    __tablename__ = "agent_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[Any] = mapped_column(JSONText)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class SettingsChange(Base):
    """One saved change of the agent settings (Settings page, an approved review suggestion, an undo or a
    reset), with the old and new value of every setting it changed. Kept for SETTINGS_HISTORY_DAYS, then
    removed; any entry can be undone from the Settings page."""

    __tablename__ = "settings_changes"
    __table_args__ = (Index("ix_settings_changes_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(20), default="settings")  # settings|review|undo|reset
    label: Mapped[str] = mapped_column(String(300), default="")
    changes: Mapped[Any] = mapped_column(JSONText)  # {key: {"old": value, "new": value}}
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # review suggestion / undone change id
    undone_by: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the undo entry, once undone


class ReviewRun(Base):
    """One run of the call-review agent over a batch of calls (automatic every N calls, or started by hand)."""

    __tablename__ = "review_runs"
    __table_args__ = (Index("ix_review_runs_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    trigger: Mapped[str] = mapped_column(String(10), default="auto")  # auto|manual
    status: Mapped[str] = mapped_column(String(10), default="running", index=True)  # running|done|failed
    stage: Mapped[str] = mapped_column(String(200), default="")  # progress, e.g. "Reviewing call 3 of 5"
    model: Mapped[str] = mapped_column(String(80), default="")
    call_count: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[Any] = mapped_column(JSONText, nullable=True)  # summary, stats, patterns, team notes
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReviewCallReport(Base):
    """The review agent's verdict on one call of a run: outcome, where (and why) the customer was lost,
    and the agent's mistakes, each backed by a verified quote and/or a timing fact from the recording."""

    __tablename__ = "review_call_reports"
    __table_args__ = (UniqueConstraint("run_id", "call_id", name="uq_review_call_reports_run_call"),
                      Index("ix_review_call_reports_call", "call_id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("review_runs.id", ondelete="CASCADE"))
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    evidence: Mapped[Any] = mapped_column(JSONText, nullable=True)  # timing analysis + numbered transcript
    evaluation: Mapped[Any] = mapped_column(JSONText, nullable=True)  # the validated verdict
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ReviewSuggestion(Base):
    """A change the review agent proposes to one setting. Nothing is applied until a person approves it."""

    __tablename__ = "review_suggestions"
    __table_args__ = (Index("ix_review_suggestions_status", "status", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("review_runs.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    field: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(300), default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    expected_effect: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[Any] = mapped_column(JSONText, nullable=True)  # [{call_uuid, turn, quote, timing}]
    base_value: Mapped[Any] = mapped_column(JSONText, nullable=True)  # the setting when it was proposed
    proposed_value: Mapped[Any] = mapped_column(JSONText, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending|approved|rejected|superseded
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_value: Mapped[Any] = mapped_column(JSONText, nullable=True)  # what was saved (may be edited)
    change_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the settings history entry
