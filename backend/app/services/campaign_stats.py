"""Per-number call results of a campaign, judged by each number's latest call.

Every campaign number is in exactly one bucket, so the buckets add up to the campaign total:
  waiting    not dialed yet
  ringing    latest call is being placed / ringing
  talking    latest call is connected right now
  picked_up  latest call was answered (incl. picked up and cut)
  no_answer  latest call rang out / was not answered
  busy       line busy / rejected
  failed     the call could not be placed (network, provider, invalid route)
  skipped    never dialed (Do-Not-Call, lead deleted)
A number whose latest call was not answered may still be retried (see `next_attempt_at`).
"""
from __future__ import annotations

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..models import ACTIVE_CALL_STATUSES, Call, CampaignLead

RESULTS = ("waiting", "ringing", "talking", "picked_up", "no_answer", "busy", "failed", "skipped")
RINGING = tuple(s for s in ACTIVE_CALL_STATUSES if s != "in_progress")


def item_result(item_status: str, call_status: str | None, answered: bool) -> str:
    if item_status == "skipped":
        return "skipped"
    if call_status is None:
        return "waiting"
    if call_status == "in_progress":
        return "talking"
    if call_status in RINGING:
        return "ringing"
    if answered or call_status == "completed":
        return "picked_up"
    if call_status == "busy":
        return "busy"
    if call_status == "failed":
        return "failed"
    return "no_answer"  # no_answer, canceled


def _conditions():
    """SQL versions of item_result(), for filtering the joined CampaignLead + Call rows."""
    kept = CampaignLead.status != "skipped"
    not_answered = Call.answered_at.is_(None)
    return {
        "skipped": CampaignLead.status == "skipped",
        "waiting": and_(kept, Call.id.is_(None)),
        "ongoing": and_(kept, Call.status.in_(ACTIVE_CALL_STATUSES)),
        "ringing": and_(kept, Call.status.in_(RINGING)),
        "talking": and_(kept, Call.status == "in_progress"),
        "picked_up": and_(kept, Call.status.not_in(ACTIVE_CALL_STATUSES),
                          or_(Call.answered_at.is_not(None), Call.status == "completed")),
        "no_answer": and_(kept, Call.status.in_(("no_answer", "canceled")), not_answered),
        "busy": and_(kept, Call.status == "busy", not_answered),
        "failed": and_(kept, Call.status == "failed", not_answered),
    }


FILTERS = tuple(_conditions())


def result_condition(name: str):
    return _conditions()[name]


def items_with_calls(campaign_id: int):
    """SELECT CampaignLead, Call (latest call of each number, may be NULL) for one campaign."""
    return (select(CampaignLead, Call)
            .outerjoin(Call, Call.uuid == CampaignLead.last_call_uuid)
            .where(CampaignLead.campaign_id == campaign_id))


def outcome_counts(db: Session, campaign_id: int) -> dict[str, int]:
    answered = Call.answered_at.is_not(None)
    rows = db.execute(
        select(CampaignLead.status, Call.status, answered, func.count())
        .select_from(CampaignLead)
        .outerjoin(Call, Call.uuid == CampaignLead.last_call_uuid)
        .where(CampaignLead.campaign_id == campaign_id)
        .group_by(CampaignLead.status, Call.status, answered)
    ).all()
    out = dict.fromkeys(RESULTS, 0)
    for item_status, call_status, was_answered, n in rows:
        out[item_result(item_status, call_status, bool(was_answered))] += n
    return out


def calls_placed(db: Session, campaign_id: int) -> int:
    """Every dial attempt (retries included)."""
    return db.scalar(select(func.count()).select_from(Call).where(Call.campaign_id == campaign_id)) or 0
