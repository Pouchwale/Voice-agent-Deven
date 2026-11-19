"""Activity log: every change a person makes through the dashboard is recorded (who, when, what).

Three parts:
- the name: the dashboard sends the person's name with every change (X-Actor header); `current_actor()` hands
  it to whatever records a change (the settings history stores it too)
- `record()`: adds one entry. Nothing in this module ever updates or deletes an entry.
- `AuditMiddleware`: records every successful POST / PUT / PATCH / DELETE on /api/ without touching the
  endpoints themselves. A spec per route says how to describe it (with the old and new values where that
  applies); a route without a spec still gets a plain entry, so a new endpoint can never change data unlogged.

Not logged: what the system does by itself (dialing, call results, automatic reviews: the Calls and Call review
pages are their record), previews and tests that change nothing, and sign-in / sign-out.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, unquote

from sqlalchemy import func, or_, select

from ..db import SessionLocal
from ..defaults import SETTING_LABELS
from ..models import ActivityLog, Call, Campaign, CampaignLead, Lead, ReviewSuggestion, SettingsChange
from ..schemas import iso

log = logging.getLogger("audit")

AREAS = ("settings", "leads", "campaigns", "calls", "inbound", "review", "other")
ACTOR_MAX = 60
SUMMARY_MAX = 400
BODY_LIMIT = 256 * 1024  # request / response bodies larger than this are not read for the description
NAMES_MAX = 200          # names listed for a bulk delete
MUTATING = ("POST", "PUT", "PATCH", "DELETE")
# Requests that change nothing (or must never be recorded: the sign-in form carries the password).
SKIP_PREFIXES = ("/api/auth/",)
SKIP_PATHS = frozenset({"/api/settings/preview-voice", "/api/settings/test-providers", "/api/campaigns/csv/preview"})
IST = timezone(timedelta(hours=5, minutes=30))
_ACTIVE_CALL = ("queued", "initiated", "ringing", "in_progress")

# The request being served: {"actor": name, "recorded": entries added so far}. Threads started for the request
# (sync endpoints, asyncio.to_thread) get a copy of the context that points at the same dict.
_state: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("audit_request", default=None)


# ---------------------------------------------------------------------- who
def clean_actor(raw: str | None) -> str:
    """The name as stored. The header is percent-encoded (headers are ASCII-only; names may be in Hindi):
    decoded here, control characters dropped, runs of spaces collapsed, at most 60 characters."""
    if not raw:
        return ""
    text = unquote(raw, errors="replace")
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Zl", "Zp", "Cs", "Co", "Cn"):
            out.append(" ")
        elif cat == "Cf" and ch not in "\u200c\u200d":  # joiners are part of Indic spelling; other format marks are not
            continue
        else:
            out.append(ch)
    return " ".join("".join(out).split())[:ACTOR_MAX]


def current_actor() -> str:
    """The name sent with the request being served ("" when none was given, or outside a request)."""
    state = _state.get()
    return state["actor"] if state else ""


# ---------------------------------------------------------------------- writing
def record(db, *, area: str, action: str, summary: str, target_type: str | None = None, target_id: Any = None,
           details: dict[str, Any] | None = None, actor: str | None = None) -> ActivityLog:
    """Add one entry inside the caller's transaction, so the entry exists exactly when the change does."""
    entry = ActivityLog(actor=current_actor() if actor is None else str(actor)[:ACTOR_MAX],
                        area=area if area in AREAS else "other", action=str(action)[:40],
                        summary=_clip(summary, SUMMARY_MAX), target_type=target_type,
                        target_id=None if target_id is None else str(target_id)[:60], details=details)
    db.add(entry)
    state = _state.get()
    if state is not None:
        state["recorded"] += 1
    return entry


def write(**fields: Any) -> None:
    """Add one entry in its own transaction (for changes that are already committed)."""
    with SessionLocal() as db:
        record(db, **fields)
        db.commit()


# ---------------------------------------------------------------------- reading
def _changes_for(db, change_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Settings entries keep their old -> new values in the settings history: fetched here for display."""
    if not change_ids:
        return {}
    rows = db.scalars(select(SettingsChange).where(SettingsChange.id.in_(change_ids))).all()
    return {r.id: [{"key": k, "label": SETTING_LABELS.get(k, k), "old": v.get("old"), "new": v.get("new")}
                   for k, v in (r.changes or {}).items() if isinstance(v, dict)]
            for r in rows}


def _change_id(r: ActivityLog) -> int | None:
    value = r.details.get("change_id") if isinstance(r.details, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def entry_out(r: ActivityLog, changes: dict[int, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    details = dict(r.details) if isinstance(r.details, dict) else {}
    change_id = _change_id(r)
    if change_id is not None and changes is not None:
        details["changes"] = changes.get(change_id, [])
    return {"id": r.id, "created_at": iso(r.created_at), "actor": r.actor or "", "area": r.area,
            "action": r.action, "summary": r.summary, "target_type": r.target_type, "target_id": r.target_id,
            "details": details}


def list_entries(limit: int = 50, before_id: int | None = None, area: str | None = None,
                 q: str | None = None) -> dict[str, Any]:
    """Newest first. `before_id` continues after the last entry of the previous page."""
    with SessionLocal() as db:
        cond = []
        if area:
            cond.append(ActivityLog.area == area)
        if q:
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            cond.append(or_(ActivityLog.summary.ilike(like, escape="\\"), ActivityLog.actor.ilike(like, escape="\\")))
        total = int(db.scalar(select(func.count(ActivityLog.id)).where(*cond)) or 0)
        query = select(ActivityLog).where(*cond)
        if before_id is not None:
            query = query.where(ActivityLog.id < before_id)
        rows = list(db.scalars(query.order_by(ActivityLog.id.desc()).limit(limit + 1)).all())
        has_more = len(rows) > limit
        rows = rows[:limit]
        changes = _changes_for(db, [cid for cid in map(_change_id, rows) if cid is not None])
        return {"items": [entry_out(r, changes) for r in rows], "has_more": has_more, "total": total}


EXPORT_HEADERS = ["id", "time_ist", "person", "area", "action", "what", "details"]


def export_rows() -> Iterator[list[Any]]:
    """Every entry, oldest first, as CSV rows."""
    last = 0
    while True:
        with SessionLocal() as db:
            rows = db.scalars(select(ActivityLog).where(ActivityLog.id > last)
                              .order_by(ActivityLog.id).limit(1000)).all()
            if not rows:
                return
            changes = _changes_for(db, [cid for cid in map(_change_id, rows) if cid is not None])
            page = [[r.id, _ist(r.created_at), r.actor or "", r.area, r.action, r.summary,
                     _details_text(entry_out(r, changes)["details"])] for r in rows]
            last = rows[-1].id
        yield from page


def _details_text(details: dict[str, Any]) -> str:
    parts: list[str] = []
    for c in details.get("changes") or []:
        if isinstance(c, dict):
            parts.append(f"{c.get('label') or c.get('key')}: {_short(c.get('old'))} -> {_short(c.get('new'))}")
    data = details.get("data")
    if isinstance(data, dict):
        parts.extend(f"{k}: {_short(v)}" for k, v in data.items())
    items = details.get("items")
    if isinstance(items, list) and items:
        more = details.get("more")
        parts.append(", ".join(str(x) for x in items) + (f" and {more} more" if more else ""))
    return _clip("; ".join(parts), 5000)


def _short(value: Any) -> str:
    if value is None or value == "":
        return "(empty)"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return _clip(" ".join(text.split()), 600)


# ---------------------------------------------------------------------- small helpers
def _clip(text: Any, n: int) -> str:
    s = str(text if text is not None else "")
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _ist(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime(fmt)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _entry(summary: str, target_type: str | None = None, target_id: Any = None,
           details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"summary": summary, "target_type": target_type, "target_id": target_id, "details": details}


# ---------------------------------------------------------------------- what each change is called
LEAD_LABELS = {"name": "Name", "phone": "Phone", "company": "Company", "city": "City", "email": "Email",
               "source": "Source", "notes": "Notes", "status": "Status", "interest": "Interest",
               "attempts": "Call attempts"}
STAGE_LABELS = {"new": "New", "no_answer": "No answer", "cold": "Cold", "warm": "Warm", "hot": "Hot"}
CAMPAIGN_LABELS = {"concurrency": "Calls at the same time", "max_attempts": "Attempts per number",
                   "retry_delay_min": "Retry after (minutes)", "respect_call_window": "Only in the calling window"}


def _lead_snapshot(db, lead_id: int | None) -> dict[str, Any] | None:
    lead = db.get(Lead, lead_id) if lead_id is not None else None
    return None if lead is None else {k: getattr(lead, k) for k in LEAD_LABELS}


def _lead_label(lead: dict[str, Any] | None, lead_id: Any = None) -> str:
    lead = lead or {}
    name, phone = str(lead.get("name") or "").strip(), str(lead.get("phone") or "").strip()
    if name and phone:
        return f"{name} ({phone})"
    return name or phone or (f"#{lead_id}" if lead_id is not None else "(no name)")


def _filled(snapshot: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    return {labels[k]: v for k, v in snapshot.items() if k in labels and v not in (None, "")}


def _lead_before(ctx: dict[str, Any]) -> dict[str, Any] | None:
    with SessionLocal() as db:
        return _lead_snapshot(db, _int(ctx["params"].get("lead_id")))


def _lead_created(ctx: dict[str, Any]) -> dict[str, Any] | None:
    res = _dict(ctx["response"])
    data = {LEAD_LABELS[k]: res[k] for k in ("name", "phone", "company", "city", "email", "source", "notes")
            if res.get(k)}
    return _entry(f"Added lead {_lead_label(res)}", "lead", res.get("id"), {"data": data})


def _lead_updated(ctx: dict[str, Any]) -> dict[str, Any] | None:
    lead_id = _int(ctx["params"].get("lead_id"))
    before = ctx["before"]
    with SessionLocal() as db:
        after = _lead_snapshot(db, lead_id)
    if not before or not after:
        return _entry(f"Edited lead {_lead_label(after or before, lead_id)}", "lead", lead_id)
    body = _dict(ctx["body"])
    # Only what this request set: a call finishing at the same moment must not be credited to the person.
    touched = (set(body) & set(LEAD_LABELS)) if body else set(LEAD_LABELS)
    if "stage" in body:  # a move on the lead board sets these
        touched |= {"interest", "status", "attempts"}
    changes = [{"key": k, "label": LEAD_LABELS[k], "old": before[k], "new": after[k]}
               for k in LEAD_LABELS if k in touched and before[k] != after[k]]
    if not changes:
        return None  # saved without changing anything
    label = _lead_label(after, lead_id)
    stage = STAGE_LABELS.get(str(body.get("stage") or ""))
    if stage and set(body) == {"stage"}:
        return _entry(f"Moved lead {label} to {stage}", "lead", lead_id, {"changes": changes})
    return _entry(f"Edited lead {label}: {', '.join(c['label'] for c in changes)}", "lead", lead_id,
                  {"changes": changes})


def _lead_deleted(ctx: dict[str, Any]) -> dict[str, Any] | None:
    lead_id = _int(ctx["params"].get("lead_id"))
    before = ctx["before"]
    return _entry(f"Deleted lead {_lead_label(before, lead_id)}", "lead", lead_id,
                  {"data": _filled(before, LEAD_LABELS)} if before else None)


def _leads_before_bulk(ctx: dict[str, Any]) -> list[str]:
    ids = [i for i in (_int(x) for x in (_dict(ctx["body"]).get("ids") or [])) if i is not None]
    if not ids:
        return []
    with SessionLocal() as db:
        rows = db.execute(select(Lead.name, Lead.phone).where(Lead.id.in_(ids[:NAMES_MAX])).order_by(Lead.id)).all()
    return [_lead_label({"name": name, "phone": phone}) for name, phone in rows]


def _leads_bulk_deleted(ctx: dict[str, Any]) -> dict[str, Any] | None:
    deleted = _int(_dict(ctx["response"]).get("deleted")) or 0
    if deleted <= 0:
        return None
    names = list(ctx["before"] or [])[:deleted]
    details: dict[str, Any] = {"items": names}
    if deleted > len(names):
        details["more"] = deleted - len(names)
    return _entry(f"Deleted {_plural(deleted, 'lead')}", "lead", None, details)


def _leads_imported(ctx: dict[str, Any]) -> dict[str, Any] | None:
    res = _dict(ctx["response"])
    added = _int(res.get("imported")) or 0
    if added <= 0:
        return None  # nothing was added
    data = {"Added": added, "Already in the list": _int(res.get("duplicates")) or 0,
            "Invalid numbers": _int(res.get("invalid")) or 0}
    return _entry(f"Imported {_plural(added, 'lead')} from a CSV file", "lead", None, {"data": data})


def _campaign_snapshot(db, cid: int | None) -> dict[str, Any] | None:
    c = db.get(Campaign, cid) if cid is not None else None
    if c is None:
        return None
    leads = int(db.scalar(select(func.count(CampaignLead.id)).where(CampaignLead.campaign_id == cid)) or 0)
    return {"name": c.name, "status": c.status, "leads": leads, **{k: getattr(c, k) for k in CAMPAIGN_LABELS}}


def _campaign_before(ctx: dict[str, Any]) -> dict[str, Any] | None:
    with SessionLocal() as db:
        return _campaign_snapshot(db, _int(ctx["params"].get("cid")))


def _campaign_created(how: str) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
    def describe(ctx: dict[str, Any]) -> dict[str, Any] | None:
        res = _dict(ctx["response"])
        cid = _int(res.get("id"))
        with SessionLocal() as db:
            snap = _campaign_snapshot(db, cid) or {}
        name = snap.get("name") or res.get("name") or f"#{cid}"
        leads = int(snap.get("leads") or 0)
        data = {"Leads": leads, **{label: snap.get(k, res.get(k)) for k, label in CAMPAIGN_LABELS.items()}}
        return _entry(f"Created campaign {name} ({_plural(leads, 'lead')}){how}", "campaign", cid, {"data": data})
    return describe


def _campaign_status(verb: str) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
    def describe(ctx: dict[str, Any]) -> dict[str, Any] | None:
        cid = _int(ctx["params"].get("cid"))
        before, res = _dict(ctx["before"]), _dict(ctx["response"])
        if before and res.get("status") == before.get("status"):
            return None  # it was already in that state
        name = res.get("name") or before.get("name") or f"#{cid}"
        details = ({"changes": [{"key": "status", "label": "Status", "old": before.get("status"),
                                 "new": res.get("status")}]} if before and res.get("status") else None)
        return _entry(f"{verb} campaign {name}", "campaign", cid, details)
    return describe


def _campaign_deleted(ctx: dict[str, Any]) -> dict[str, Any] | None:
    cid = _int(ctx["params"].get("cid"))
    before = _dict(ctx["before"])
    details = {"data": {"Status": before.get("status"), "Leads": before.get("leads")}} if before else None
    return _entry(f"Deleted campaign {before.get('name') or f'#{cid}'}", "campaign", cid, details)


def _call_snapshot(db, call_uuid: str | None) -> dict[str, Any] | None:
    call = db.scalar(select(Call).where(Call.uuid == call_uuid)) if call_uuid else None
    if call is None:
        return None
    lead = db.get(Lead, call.lead_id) if call.lead_id else None
    direction = call.direction or "outbound"
    number = call.from_number if direction == "inbound" else call.to_number
    if call.channel == "browser":
        phrase = "a browser test call"
    elif lead is not None:
        phrase = f"the call with {_lead_label({'name': lead.name, 'phone': lead.phone})}"
    else:
        phrase = f"the call with {number}" if number else "a call with an unknown number"
    return {"phrase": phrase, "status": call.status, "direction": direction,
            "when": _ist(call.started_at or call.created_at, "%d %b %Y, %H:%M") + " IST",
            "duration_sec": call.duration_sec, "summary": call.summary}


def _call_before(ctx: dict[str, Any]) -> dict[str, Any] | None:
    with SessionLocal() as db:
        return _call_snapshot(db, ctx["params"].get("call_uuid"))


def _call_lead_before(ctx: dict[str, Any]) -> dict[str, Any] | None:
    with SessionLocal() as db:
        return _lead_snapshot(db, _int(_dict(ctx["body"]).get("lead_id")))


def _call_started(ctx: dict[str, Any]) -> dict[str, Any] | None:
    lead_id = _int(_dict(ctx["body"]).get("lead_id"))
    return _entry(f"Started a call to {_lead_label(ctx['before'], lead_id)}", "call",
                  _dict(ctx["response"]).get("uuid"))


def _call_hung_up(ctx: dict[str, Any]) -> dict[str, Any] | None:
    before = ctx["before"]
    if not before or before.get("status") not in _ACTIVE_CALL:
        return None  # there was no call to end
    return _entry(f"Hung up {before['phrase']}", "call", ctx["params"].get("call_uuid"))


def _call_reanalyzed(ctx: dict[str, Any]) -> dict[str, Any] | None:
    phrase = _dict(ctx["before"]).get("phrase") or "a call"
    return _entry(f"Re-ran the analysis of {phrase}", "call", ctx["params"].get("call_uuid"))


def _call_deleted(ctx: dict[str, Any]) -> dict[str, Any] | None:
    before = _dict(ctx["before"])
    data: dict[str, Any] = {}
    if before:
        data = {"When": before.get("when"), "Direction": before.get("direction"), "Result": before.get("status")}
        if before.get("duration_sec") is not None:
            data["Length (seconds)"] = round(float(before["duration_sec"]))
        if before.get("summary"):
            data["Summary"] = _clip(before["summary"], 600)
    return _entry(f"Deleted {before.get('phrase') or 'a call'}", "call", ctx["params"].get("call_uuid"),
                  {"data": data} if data else None)


def _inbound_connected(ctx: dict[str, Any]) -> dict[str, Any] | None:
    forced = any(v.lower() in ("true", "1", "yes", "on") for v in ctx["query"].get("force", []))
    return _entry("Connected inbound calls to the agent"
                  + (" (took the number over from another application)" if forced else ""))


def _inbound_disconnected(_ctx: dict[str, Any]) -> dict[str, Any] | None:
    return _entry("Disconnected inbound calls from the agent")


def _suggestion_before(ctx: dict[str, Any]) -> dict[str, Any] | None:
    with SessionLocal() as db:
        s = db.get(ReviewSuggestion, _int(ctx["params"].get("suggestion_id")))
        return None if s is None else {"title": s.title}


def _suggestion(verb: str) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
    def describe(ctx: dict[str, Any]) -> dict[str, Any] | None:
        sid = _int(ctx["params"].get("suggestion_id"))
        title = str(_dict(ctx["before"]).get("title") or "").strip()
        note = str(_dict(ctx["body"]).get("note") or "").strip()
        return _entry(f"{verb} review suggestion #{sid}" + (f": {title}" if title else ""), "suggestion", sid,
                      {"data": {"Note": _clip(note, 600)}} if note else None)
    return describe


def _review_started(ctx: dict[str, Any]) -> dict[str, Any] | None:
    res = _dict(ctx["response"])
    reviewed = _int(res.get("call_count"))  # how many calls the run really took (fewer than asked when few qualify)
    if reviewed and reviewed > 0:
        return _entry(f"Started a call review of {_plural(reviewed, 'call')}", "review_run", res.get("id"))
    limit = _int(_dict(ctx["body"]).get("limit")) or 10
    return _entry(f"Started a call review of the last {_plural(limit, 'call')}", "review_run", res.get("id"))


class Spec:
    """How one route is recorded. `describe` returns the entry, or None when the request changed nothing.
    describe=None: the endpoint records its own entry in the same transaction as the change (settings)."""

    def __init__(self, method: str, template: str, area: str, action: str, title: str,
                 describe: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None, *,
                 before: Callable[[dict[str, Any]], Any] | None = None, body: bool = False):
        self.method, self.area, self.action, self.title = method, area, action, title
        self.describe, self.before, self.body = describe, before, body
        self.pattern = re.compile(re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", template))


SPECS: tuple[Spec, ...] = (
    # settings: recorded by services/agent_config.py together with the change (old -> new of every setting)
    Spec("PUT", "/api/settings", "settings", "settings.save", "Changed settings"),
    Spec("POST", "/api/settings/reset", "settings", "settings.reset", "Reset all settings to defaults"),
    Spec("POST", "/api/settings/history/{change_id}/undo", "settings", "settings.undo", "Undid a settings change"),
    Spec("POST", "/api/review/suggestions/{suggestion_id}/approve", "review", "suggestion.approve",
         "Approved a review suggestion", _suggestion("Approved"), before=_suggestion_before),
    Spec("POST", "/api/review/suggestions/{suggestion_id}/reject", "review", "suggestion.reject",
         "Rejected a review suggestion", _suggestion("Rejected"), before=_suggestion_before, body=True),
    Spec("POST", "/api/review/runs", "review", "review.start", "Started a call review", _review_started, body=True),
    Spec("POST", "/api/leads", "leads", "lead.create", "Added a lead", _lead_created),
    Spec("POST", "/api/leads/import", "leads", "lead.import", "Imported leads from a CSV file", _leads_imported),
    Spec("POST", "/api/leads/bulk-delete", "leads", "lead.bulk_delete", "Deleted several leads",
         _leads_bulk_deleted, before=_leads_before_bulk, body=True),
    Spec("PATCH", "/api/leads/{lead_id}", "leads", "lead.update", "Edited a lead", _lead_updated,
         before=_lead_before, body=True),
    Spec("DELETE", "/api/leads/{lead_id}", "leads", "lead.delete", "Deleted a lead", _lead_deleted,
         before=_lead_before),
    Spec("POST", "/api/campaigns", "campaigns", "campaign.create", "Created a campaign", _campaign_created("")),
    Spec("POST", "/api/campaigns/csv", "campaigns", "campaign.create", "Created a campaign from a CSV file",
         _campaign_created(" from a CSV file")),
    Spec("POST", "/api/campaigns/{cid}/start", "campaigns", "campaign.start", "Started a campaign",
         _campaign_status("Started"), before=_campaign_before),
    Spec("POST", "/api/campaigns/{cid}/pause", "campaigns", "campaign.pause", "Paused a campaign",
         _campaign_status("Paused"), before=_campaign_before),
    Spec("DELETE", "/api/campaigns/{cid}", "campaigns", "campaign.delete", "Deleted a campaign",
         _campaign_deleted, before=_campaign_before),
    Spec("POST", "/api/calls", "calls", "call.start", "Started a call", _call_started,
         before=_call_lead_before, body=True),
    Spec("POST", "/api/calls/{call_uuid}/hangup", "calls", "call.hangup", "Hung up a call", _call_hung_up,
         before=_call_before),
    Spec("POST", "/api/calls/{call_uuid}/reanalyze", "calls", "call.reanalyze", "Re-ran the analysis of a call",
         _call_reanalyzed, before=_call_before),
    Spec("DELETE", "/api/calls/{call_uuid}", "calls", "call.delete", "Deleted a call", _call_deleted,
         before=_call_before),
    Spec("POST", "/api/inbound/setup", "inbound", "inbound.connect", "Connected inbound calls",
         _inbound_connected),
    Spec("DELETE", "/api/inbound/setup", "inbound", "inbound.disconnect", "Disconnected inbound calls",
         _inbound_disconnected),
)


def match(method: str, path: str) -> tuple[Spec | None, dict[str, str]]:
    for spec in SPECS:
        if spec.method == method:
            m = spec.pattern.fullmatch(path)
            if m:
                return spec, m.groupdict()
    return None, {}


def _finish(spec: Spec | None, ctx: dict[str, Any], state: dict[str, Any]) -> None:
    """Record the request that just succeeded (runs in a worker thread, after the endpoint committed)."""
    if state["recorded"]:
        return  # the endpoint recorded it itself
    if spec is None:  # a route nobody described yet: still never unlogged
        write(area="other", action="other", summary=f"{ctx['method']} {ctx['path']}", actor=state["actor"])
        return
    if spec.describe is None:
        return
    try:
        described = spec.describe(ctx)
    except Exception:  # noqa: BLE001 - a description must never cost the entry itself
        log.exception("audit: could not describe %s %s", ctx["method"], ctx["path"])
        described = _entry(spec.title)
    if described is None:
        return  # the request succeeded but changed nothing
    write(area=spec.area, action=spec.action, actor=state["actor"], **described)


# ---------------------------------------------------------------------- the middleware
def _is_json(content_type: bytes | None) -> bool:
    return bool(content_type) and b"json" in content_type.lower()


def _parse(raw: bytes | None) -> Any:
    """The JSON value, or None for anything that isn't readable JSON. Never raises: a body the endpoint itself
    will refuse (broken, or nested so deeply that the parser gives up) must reach the endpoint, not fail here."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001 - ValueError for broken JSON, RecursionError for absurd nesting
        return None


async def _buffer_body(receive, limit: int):
    """Read the request body ahead of the endpoint (to describe the change) and hand back a `receive` that
    replays exactly what was read. Returns (the body, or None when it is larger than `limit`; that receive)."""
    messages: list[dict[str, Any]] = []
    size = 0
    complete = False
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            break  # the client went away
        size += len(message.get("body", b""))
        if size > limit:
            break
        if not message.get("more_body", False):
            complete = True
            break
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.request") if complete else None

    async def replay():
        if messages:
            return messages.pop(0)
        return await receive()

    return body, replay


class AuditMiddleware:
    """Pure ASGI. Added first in main.py, so it sits inside the access guard (only allowed requests reach it)
    and inside GZip (it sees plain JSON). Webhooks, WebSockets and every GET pass straight through.

    The entry is written when the endpoint has finished, just before the last piece of the response is passed
    on: when the dashboard hears that a change succeeded, its entry is already in the log. Nothing in here can
    fail a request: every problem is logged and the response goes out unchanged."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in MUTATING:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if not path.startswith("/api/") or path.startswith(SKIP_PREFIXES) or path in SKIP_PATHS:
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        state = {"actor": clean_actor(headers.get(b"x-actor", b"").decode("latin-1")), "recorded": 0}
        token = _state.set(state)
        try:
            await self._serve(scope, receive, send, headers, state)
        finally:
            _state.reset(token)

    async def _serve(self, scope, receive, send, headers, state) -> None:
        method, path = scope["method"], scope.get("path", "")
        spec, params = match(method, path)
        ctx: dict[str, Any] = {"method": method, "path": path, "params": params, "body": None, "response": None,
                               "before": None,
                               "query": parse_qs(scope.get("query_string", b"").decode("latin-1"))}
        if spec is not None and spec.body and _is_json(headers.get(b"content-type")):
            raw, receive = await _buffer_body(receive, BODY_LIMIT)
            ctx["body"] = _parse(raw)
        if spec is not None and spec.before is not None:
            try:
                ctx["before"] = await asyncio.to_thread(spec.before, ctx)
            except Exception:  # noqa: BLE001
                log.exception("audit: no snapshot before %s %s", method, path)

        info: dict[str, Any] = {"status": 0, "capture": False, "chunks": [], "size": 0, "done": False}

        async def send_and_record(message):
            kind = message["type"]
            if kind == "http.response.start":
                info["status"] = message["status"]
                content_type = dict(message.get("headers") or []).get(b"content-type", b"")
                info["capture"] = 200 <= info["status"] < 300 and _is_json(content_type)
            elif kind == "http.response.body":
                if info["capture"]:
                    chunk = message.get("body", b"")
                    info["size"] += len(chunk)
                    if info["size"] > BODY_LIMIT:
                        info["capture"], info["chunks"] = False, []
                    else:
                        info["chunks"].append(chunk)
                if not message.get("more_body", False) and not info["done"]:
                    info["done"] = True
                    if 200 <= info["status"] < 300:
                        try:
                            ctx["response"] = _parse(b"".join(info["chunks"]))
                            await asyncio.to_thread(_finish, spec, ctx, state)
                        except Exception:  # noqa: BLE001
                            log.exception("audit: could not record %s %s", method, path)
            await send(message)

        await self.app(scope, receive, send_and_record)
