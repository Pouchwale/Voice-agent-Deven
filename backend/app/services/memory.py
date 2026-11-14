"""Caller memory: a compact brief of everything we already know about a customer, for the agent's prompt.

Built when a call is prepared (inbound answer webhook, outbound dial, browser test with a lead) and passed
to the session, which puts it into the per-call lead message (prompts.lead_context) — never into the static
system prompt, so OpenAI's prompt cache keeps working.

What goes in (newest first, at most MAX_CHARS, oldest lines dropped first):
  * the turns of a call that dropped minutes ago and isn't analysed yet, so the agent carries on from there;
  * earlier conversations (phone call completed with the customer speaking): when, who called whom, how long,
    the summary, what our team planned, what we promised them, the call-back time they asked for;
  * every call without a conversation collapsed into ONE neutral line (never "they cut the call");
  * known answers, key facts and concerns merged across the last 3 analysed conversations;
  * internal team notes + CSV extras — outbound calls only, labelled as never to be said.
Never: ratings, interest labels, recording URLs, internal ids, email / GST / PAN values.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select

from ..db import SessionLocal, utcnow
from ..models import ACTIVE_CALL_STATUSES, Call, Lead, TranscriptTurn
from .prompts import NOTES_LABEL, day_label, redact_private, relative_age

MAX_CHARS = 1800                     # part of every LLM request (cached only within one call): keep it small
KEEP_CONVERSATIONS = 3               # the newest analysed conversations are never dropped for space
JUST_DROPPED = timedelta(minutes=15)
DROPPED_TURNS = 8
HISTORY_LIMIT = 200
HEADER = "# What we already know about this customer"
_E164 = re.compile(r"\+\d{8,15}")
# lead.extra columns that must never reach the agent (CSV imports carry GST, emails, addresses...)
_PRIVATE_KEY_PREFIXES = ("gst", "email", "mail", "address", "addr", "aadha", "pincode", "bank", "account",
                         "password", "caller")
_PRIVATE_KEY_WORDS = {"pan", "pin", "zip", "ifsc", "upi", "acct"}


def caller_memory(lead_id: int | None, exclude_call_uuid: str | None = None, *, from_number: str | None = None,
                  direction: str = "outbound", now: datetime | None = None) -> str:
    """Earlier calls, known answers, promises and concerns for this customer ("" when nothing is known).

    Calls = phone calls linked to the lead, plus inbound calls that couldn't be linked at the time (lead_id
    NULL, from_number == the lead's phone; or == from_number when there is no lead). The current call,
    browser tests, live calls and cancelled/superseded dials are left out.
    direction "inbound": internal team notes and CSV extras are left out (the caller's identity rests on an
    unsigned caller ID). Synchronous (DB): call it through run_db.
    """
    now = now or utcnow()
    with SessionLocal() as db:
        lead = db.get(Lead, lead_id) if lead_id else None
        number = lead.phone if lead is not None else _usable_number(from_number)
        linked = []
        if lead is not None:
            linked.append(Call.lead_id == lead.id)
        if number:
            linked.append(and_(Call.lead_id.is_(None), Call.direction == "inbound", Call.from_number == number))
        if not linked:
            return ""
        q = (select(Call)
             .where(or_(*linked), Call.channel == "phone",
                    Call.status.not_in((*ACTIVE_CALL_STATUSES, "canceled")),
                    or_(Call.end_reason.is_(None), Call.end_reason != "superseded"))
             .order_by(Call.id.desc()).limit(HISTORY_LIMIT))
        if exclude_call_uuid:
            q = q.where(Call.uuid != exclude_call_uuid)
        calls = sorted(db.scalars(q).all(), key=_when, reverse=True)
        dropped_call, turns = _just_dropped(db, calls, now)
        notes = _team_notes(lead) if lead is not None and direction != "inbound" else ""
        return _render(calls, dropped_call, turns, notes, leadless=lead is None, now=now)


# ---------------------------------------------------------------- loading helpers
def _usable_number(number: str | None) -> str | None:
    """Only a real E.164 number may link calls: hidden / short / raw caller IDs would join strangers."""
    n = (number or "").strip()
    return n if _E164.fullmatch(n) else None


def _when(c: Call) -> datetime:
    return c.started_at or c.created_at or datetime.min.replace(tzinfo=timezone.utc)


def _words(c: Call) -> int:
    m = c.metrics if isinstance(c.metrics, dict) else {}
    try:
        return int(m.get("user_words") or 0)
    except (TypeError, ValueError):
        return 0


def _is_conversation(c: Call) -> bool:
    return c.status == "completed" and _words(c) > 0


def _just_dropped(db, calls: list[Call], now: datetime) -> tuple[Call | None, list[tuple[str, str]]]:
    """The conversation that ended minutes ago and isn't analysed yet (typically: the line dropped and the
    customer redialled): its last turns let the agent continue instead of starting over."""
    for c in calls:  # newest first
        if not _is_conversation(c) or c.ended_at is None:
            continue  # e.g. a missed redial in between
        if now - c.ended_at > JUST_DROPPED or c.analysis_status not in ("pending", "failed"):
            return None, []  # too old, or analysed (its summary is in the memory already)
        rows = db.scalars(select(TranscriptTurn)
                          .where(TranscriptTurn.call_id == c.id, TranscriptTurn.role.in_(("agent", "user")))
                          .order_by(TranscriptTurn.id.desc()).limit(DROPPED_TURNS * 2)).all()
        turns = [(t.role, " ".join(t.text.split())) for t in rows if (t.text or "").strip()][:DROPPED_TURNS]
        return (c, list(reversed(turns))) if turns else (None, [])
    return None, []


def _private_key(key: str) -> bool:
    words = re.findall(r"[a-z]+", key.lower())
    return any(w in _PRIVATE_KEY_WORDS or w.startswith(_PRIVATE_KEY_PREFIXES) for w in words)


def _team_notes(lead: Lead) -> str:
    bits = []
    notes = " ".join((lead.notes or "").split())
    if notes:
        bits.append(_cap(notes, 400))
    extra = lead.extra if isinstance(lead.extra, dict) else {}
    for k, v in extra.items():
        value = " ".join(str(v if v is not None else "").split())
        if value and not _private_key(str(k)):
            bits.append(f"{_cap(str(k), 40)}: {_cap(value, 80)}")
    return redact_private("; ".join(bits))[:600] if bits else ""


# ---------------------------------------------------------------- rendering
def _cap(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip(" ,;.") + "…"


def _sentence(text: Any, limit: int) -> str:
    s = _cap(str(text or "").strip(), limit).rstrip()
    if s and s[-1] not in ".!?।…":
        s += "."
    return s


def _times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.casefold()).strip()


def _conversation_line(c: Call, now: datetime) -> str:
    when = _when(c)
    head = f"- {relative_age(when, now)} ({day_label(when, now)}), " + (
        "they called us" if c.direction == "inbound" else "we called them")
    if c.duration_sec:
        secs = int(round(c.duration_sec))
        head += f", talked {secs // 60}:{secs % 60:02d}"
    summary = _sentence(c.summary, 260) if c.analysis_status == "done" else ""
    line = f"{head}: {summary}" if summary else f"{head}."
    if c.analysis_status == "done":
        if c.next_action:
            line += f" Our team planned: {_sentence(c.next_action, 110)}"
        promised = (c.metrics or {}).get("promised_to_customer") if isinstance(c.metrics, dict) else None
        if promised:
            line += f" We promised them: {_sentence(promised, 110)}"
        if c.callback_time:
            line += f" Callback asked (on {day_label(when, now)}): {_sentence(c.callback_time, 70)}"
    return line


def _attempts_line(calls: list[Call], had_conversation: bool, now: datetime) -> str | None:
    """All calls without a conversation as ONE neutral line: no "cut the call", no "inbound was off"."""
    others = [c for c in calls if not _is_conversation(c)]
    tried = [c for c in others if c.direction != "inbound"]
    missed = [c for c in others if c.direction == "inbound"]
    we = "We also" if had_conversation else "We"
    if tried and missed:
        return (f"{we} tried calling {_times(len(tried))} without reaching them (latest "
                f"{relative_age(_when(tried[0]), now)}); they called {_times(len(missed))} when we couldn't pick up "
                f"(latest {relative_age(_when(missed[0]), now)}).")
    if tried:
        return (f"{we} tried calling {_times(len(tried))} without reaching them (latest "
                f"{relative_age(_when(tried[0]), now)}).")
    if missed:
        return (f"They {'also ' if had_conversation else ''}called {_times(len(missed))} when we couldn't pick up "
                f"(latest {relative_age(_when(missed[0]), now)}).")
    return None


def _known_answers(analysed: list[Call]) -> str | None:
    """Qualification answers from the last analysed conversations; the newest answer per question wins."""
    seen: dict[str, str] = {}
    for c in analysed:  # newest first
        for item in c.qualification if isinstance(c.qualification, list) else []:
            if not isinstance(item, dict) or item.get("status") not in ("answered", "partial"):
                continue
            q, a = _cap(item.get("question") or "", 60), _cap(item.get("answer") or "", 100)
            if q and a and a.casefold() not in ("null", "none") and _norm(q) not in seen:
                seen[_norm(q)] = f"{q.rstrip(' :?')}: {a}" + (" (partly)" if item.get("status") == "partial" else "")
    return ("Known answers: " + "; ".join(seen.values()) + ".") if seen else None


def _merged(analysed: list[Call], attr: str, limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for c in analysed:
        for x in getattr(c, attr) if isinstance(getattr(c, attr), list) else []:
            text = _cap(str(x or "").strip().rstrip("."), 100)
            if text and _norm(text) not in seen:
                seen.add(_norm(text))
                out.append(text)
    return out[:limit]


def _told_us(analysed: list[Call]) -> str | None:
    """No lead (e.g. an international caller): the name/company/city they gave on an earlier call."""
    for c in analysed:
        m = c.metrics if isinstance(c.metrics, dict) else {}
        bits = [f"{label} {_cap(m[k], 60)}" for k, label in
                (("customer_name", "name"), ("customer_company", "company"), ("customer_city", "city")) if m.get(k)]
        if bits:
            return "Earlier they told us their " + ", ".join(bits) + "."
    return None


def _render(calls: list[Call], dropped_call: Call | None, turns: list[tuple[str, str]], notes: str, *,
            leadless: bool, now: datetime) -> str:
    if not calls and not notes:
        return ""
    convs = [c for c in calls if _is_conversation(c)]
    analysed = [c for c in convs if c.analysis_status == "done"][:KEEP_CONVERSATIONS]
    parts: list[tuple[str, int | None]] = [(HEADER, None)]  # (text, drop rank: higher goes first; None = keep)
    if calls:
        first = _when(calls[-1])
        parts.append((f"Customer since {day_label(first, now, year=True)} ({relative_age(first, now)}).", None))
    if dropped_call is not None:
        block = ["The call that just dropped (continue from here):"]
        block += [f"{'AGENT' if role == 'agent' else 'CUSTOMER'}: {_cap(text, 140)}" for role, text in turns]
        while len(block) > 3 and sum(len(b) + 1 for b in block) > 700:
            del block[1]  # keep the latest turns
        parts.append(("\n".join(block), None))
    shown = [c for c in convs if c is not dropped_call]
    if shown:
        parts.append(("Earlier conversations (newest first):", None))
        keep = {c.id for c in analysed} | {shown[0].id}
        older = [c for c in shown if c.id not in keep]
        for c in shown:
            rank = None if c.id in keep else 100 + older.index(c)  # the oldest is dropped first
            parts.append((_conversation_line(c, now), rank))
    attempts = _attempts_line(calls, bool(convs), now)
    if attempts:
        parts.append((attempts, 30))
    answers = _known_answers(analysed)
    if answers:
        parts.append((answers, 10))
    facts = _merged(analysed, "key_points", 8)
    if facts:
        parts.append(("Key facts: " + "; ".join(facts) + ".", 40))
    concerns = _merged(analysed, "objections", 5)
    if concerns:
        parts.append(("Concerns they raised before (don't bring these up first): " + "; ".join(concerns) + ".", 50))
    if leadless:
        told = _told_us(analysed)
        if told:
            parts.append((told, 25))
    if notes:
        # dropped for space before facts: lead_context re-adds the notes when this line is missing
        parts.append((f"{NOTES_LABEL} {notes}", 60))
    return redact_private(_fit(parts, MAX_CHARS))


def _fit(parts: list[tuple[str, int | None]], limit: int) -> str:
    kept = list(parts)

    def size() -> int:
        return sum(len(text) for text, _ in kept) + len(kept) - 1

    for victim in sorted((p for p in parts if p[1] is not None), key=lambda p: p[1], reverse=True):
        if size() <= limit:
            break
        kept.remove(victim)
    text = "\n".join(t for t, _ in kept)
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"
