"""Call-review agent: status, runs, the evaluation method, and approving / rejecting its suggestions.

Nothing here changes a setting except POST /suggestions/{id}/approve, i.e. a person's explicit approval.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from ..defaults import DEFAULT_AGENT_SETTINGS
from ..services import agent_config, inbound_audio
from ..services.llm import client
from ..services.repo import run_db
from ..services.review import agent as review
from ..services.review.prompts import FIELD_HELP, FIELD_RULES, ISSUE_CATEGORIES

router = APIRouter(prefix="/api/review", tags=["review"])

STEPS = [
    "Picks the calls: phone calls in which the customer spoke (outbound, plus inbound if switched on). An "
    "automatic review runs once enough new calls are waiting; \"Run review now\" takes the most recent calls.",
    "Measures each call from its recording and transcript (no AI): who talked how long, slow replies, long "
    "monologues, talking over the customer, dead air, hanging up during an agent reply, customers asking to "
    "repeat, questions asked twice.",
    "Judges each call with the review model, using \"How calls are judged\": the outcome, whether, where (which "
    "agent turn) and why the customer was lost, the agent's mistakes and what worked.",
    "Checks the proof: every quote must really be in the cited agent turn and every timing fact must exist. "
    "Findings without proof are removed; \"lost because of the agent\" without a proven mistake becomes "
    "\"unclear\". Normal reasons to end a call (not interested, wrong number, busy) never count against the agent.",
    "Looks across the calls: a problem must be proven in at least the required number of calls to become a "
    "suggestion. Each suggestion is one small, validated change to one setting.",
    "Waits for you: nothing changes until you approve a suggestion (you can edit it first). Approved changes "
    "appear in Settings > Change history, where they can be undone.",
]


def _raise(e: review.ReviewError) -> None:
    raise HTTPException(e.status, {"message": str(e), **e.data} if e.data else str(e)) from None


@router.get("/status")
async def get_status():
    return await run_db(review.status)


@router.get("/method")
def method():
    """How the review agent evaluates calls (shown on the Review page)."""
    cfg = agent_config.load_agent_settings()
    return {"steps": STEPS, "rubric": cfg.get("review_rubric"),
            "default_rubric": DEFAULT_AGENT_SETTINGS["review_rubric"], "categories": list(ISSUE_CATEGORIES),
            "fields": [{"key": k, "label": agent_config.SETTING_LABELS.get(k, k), "how": list(v), "help": FIELD_HELP[k]}
                       for k, v in FIELD_RULES.items()]}


_models_cache: dict[str, Any] = {"at": 0.0, "ids": None}


@router.get("/models")
async def models():
    """Models offered for reviews, and whether this OpenAI key can use them (checked at most once an hour)."""
    if _models_cache["ids"] is None or time.monotonic() - _models_cache["at"] > 3600:
        try:
            page = await client().models.list()
            _models_cache.update(at=time.monotonic(), ids={m.id for m in page.data})
        except Exception:  # noqa: BLE001
            _models_cache.update(at=time.monotonic(), ids=None)
    ids = _models_cache["ids"]
    return {"models": [{"id": m, "note": note, "available": None if ids is None else m in ids}
                       for m, note in review.MODEL_CHOICES]}


@router.get("/runs")
async def runs(limit: int = 20):
    return await run_db(review.list_runs, max(1, min(limit, 100)))


@router.get("/runs/{run_id}")
async def run_detail(run_id: int):
    try:
        return await run_db(review.run_detail, run_id)
    except review.ReviewError as e:
        _raise(e)


@router.post("/runs", status_code=201)
async def start(body: dict[str, Any] = Body(default={})):
    """Start a review now. body: {"limit": number of most recent calls (1-15, default 10)}."""
    try:
        limit = int(body.get("limit") or 10)
    except (TypeError, ValueError):
        raise HTTPException(422, "limit must be a number") from None
    try:
        return await review.start_run("manual", limit)
    except review.ReviewError as e:
        _raise(e)


@router.get("/suggestions")
async def suggestions(status: str | None = None, limit: int = 100):
    return await run_db(review.list_suggestions, status, max(1, min(limit, 500)))


@router.post("/suggestions/{suggestion_id}/approve")
async def approve(suggestion_id: int, body: dict[str, Any] = Body(default={})):
    """Approve (optionally with an edited value). 409 if the setting changed since: repeat with force=true."""
    try:
        out = await run_db(review.approve, suggestion_id, body.get("value"), bool(body.get("force")))
    except review.ReviewError as e:
        _raise(e)
    # like every settings change: keep the cached inbound clips in step (a no-op unless a spoken clip changed)
    inbound_audio.schedule_prewarm(await run_db(agent_config.load_agent_settings))
    await review.publish()
    return out


@router.post("/suggestions/{suggestion_id}/reject")
async def reject(suggestion_id: int, body: dict[str, Any] = Body(default={})):
    try:
        out = await run_db(review.reject, suggestion_id, body.get("note"))
    except review.ReviewError as e:
        _raise(e)
    await review.publish()
    return out
