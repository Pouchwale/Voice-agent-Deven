"""Post-call analysis: summary, 1-10 rating, cold/warm/hot, qualification answers, objection tags."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..config import get_settings
from ..db import SessionLocal
from ..models import INTERESTS, Call
from .agent_config import load_agent_settings
from .events import bus
from .llm import complete_json
from .prompts import OBJECTION_TAGS, build_analysis_prompt
from .repo import run_db
from ..schemas import call_list_out, lead_out

log = logging.getLogger("analysis")
settings = get_settings()


def _load(call_uuid: str) -> tuple[dict | None, list[tuple[str, str]], dict | None]:
    with SessionLocal() as db:
        call = db.scalar(select(Call).options(selectinload(Call.turns), selectinload(Call.lead))
                         .where(Call.uuid == call_uuid))
        if not call:
            return None, [], None
        turns = [(t.role, t.text) for t in call.turns if t.role in ("agent", "user") and t.text.strip()]
        meta = {"id": call.id, "channel": call.channel, "answered": call.answered_at is not None,
                "end_reason": call.end_reason, "agent_audio_ms": (call.metrics or {}).get("agent_audio_ms", 0)}
        return meta, turns, (lead_out(call.lead) if call.lead else None)


def _clamp_rating(v: Any) -> int | None:
    try:
        r = int(round(float(v)))
    except (TypeError, ValueError):
        return None
    return max(1, min(10, r))


def _as_list(v: Any) -> list:
    if isinstance(v, list):
        return [x for x in v if x not in (None, "")]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def _save(call_uuid: str, result: dict[str, Any], status: str, error: str | None = None) -> dict | None:
    with SessionLocal() as db:
        call = db.scalar(select(Call).options(selectinload(Call.lead)).where(Call.uuid == call_uuid))
        if not call:
            return None
        call.analysis_status = status
        call.error = error if error else call.error
        if status == "done":
            interest = str(result.get("interest") or "unknown").lower().strip()
            call.interest = interest if interest in INTERESTS else "unknown"
            call.rating = _clamp_rating(result.get("rating"))
            call.summary = (result.get("summary") or "").strip() or None
            q = result.get("qualified")
            call.qualified = bool(q) if q is not None else None
            sent = str(result.get("sentiment") or "").lower()
            call.sentiment = sent if sent in ("positive", "neutral", "negative") else None
            call.next_action = (result.get("next_action") or None)
            cb = result.get("callback_time")
            call.callback_time = str(cb)[:120] if cb not in (None, "", "null") else None
            quals = []
            for item in _as_list(result.get("qualification")):
                if isinstance(item, dict):
                    st = str(item.get("status") or "not_asked")
                    quals.append({
                        "question": str(item.get("question") or "")[:200],
                        "answer": (str(item["answer"])[:500] if item.get("answer") not in (None, "", "null") else None),
                        "status": st if st in ("answered", "partial", "not_asked") else "not_asked",
                    })
            call.qualification = quals or None
            call.key_points = [str(x)[:200] for x in _as_list(result.get("key_points"))][:12] or None
            call.objections = [str(x)[:200] for x in _as_list(result.get("objections"))][:10] or None
            tags = [str(t).strip().lower() for t in _as_list(result.get("objection_tags"))]
            tags = sorted({t if t in OBJECTION_TAGS else "other" for t in tags})
            call.metrics = {**(call.metrics or {}), "objection_tags": tags}
        elif status == "skipped":
            call.interest = "unknown"
            call.summary = result.get("summary")
        # roll the outcome up to the lead so BDEs can sort/filter leads directly
        lead = call.lead
        no_conversation = not (call.metrics or {}).get("user_words")
        protect = no_conversation and lead is not None and lead.interest in ("warm", "hot")
        if lead is not None and status in ("done", "skipped") and not protect:
            # (a pickup-and-cut must never wipe out an earlier warm/hot conversation)
            if call.interest != "unknown":
                lead.interest = call.interest
            if call.rating is not None:
                lead.rating = call.rating
            if call.summary:
                lead.last_summary = call.summary
        db.commit()
        db.refresh(call)
        out = call_list_out(call)
    bus.publish({"type": "call.update", "call": out})
    return out


async def analyze_call(call_uuid: str) -> None:
    meta, turns, lead = await run_db(_load, call_uuid)
    if meta is None:
        return
    user_turns = [t for t in turns if t[0] == "user"]
    if not user_turns:
        heard_agent = (meta.get("agent_audio_ms") or 0) > 0
        customer_ended = meta.get("end_reason") in ("user_hangup", "silence_timeout")
        if meta.get("channel") == "phone" and meta.get("answered") and heard_agent and customer_ended:
            # Picked up, heard us, then cut the call / never spoke -> cold (client rule), lowest rating.
            # Not applied to our own failures (TTS/STT outage, errors, manual hang-up): those are skipped.
            silent = meta.get("end_reason") == "silence_timeout"
            await run_db(_save, call_uuid, {
                "summary": ("Picked up but did not respond; the agent ended the call after the silence check."
                            if silent else "Picked up and disconnected without talking."),
                "rating": 1, "interest": "cold", "qualified": False, "sentiment": "neutral",
                "next_action": "Try again at a different time of day, or move on.",
                "objection_tags": ["not_interested"] if not silent else [],
            }, "done")
            return
        await run_db(_save, call_uuid, {"summary": "No conversation — the customer did not speak on the call."},
                     "skipped")
        return
    if not settings.openai_ready:
        await run_db(_save, call_uuid, {}, "failed", "OPENAI_API_KEY not set — analysis skipped")
        return
    cfg = load_agent_settings()
    transcript = "\n".join(f"{'AGENT' if r == 'agent' else 'CUSTOMER'}: {t}" for r, t in turns)
    try:
        result = await complete_json(build_analysis_prompt(cfg, lead, transcript[-12000:]))
        if not result:
            raise ValueError("empty analysis")
        await run_db(_save, call_uuid, result, "done")
    except Exception as e:  # noqa: BLE001
        log.exception("analysis failed for %s", call_uuid)
        await run_db(_save, call_uuid, {}, "failed", f"Analysis failed: {str(e)[:200]}")
