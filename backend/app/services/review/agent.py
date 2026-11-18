"""The call-review agent ("second agent"): reviews batches of finished calls, finds where and why customers
were lost, and proposes settings changes that a person approves or rejects.

How a review works (also shown on the Review page):
  1. Pick the calls: phone calls where the customer spoke (outbound, plus inbound if enabled). Automatic
     reviews take the calls made since the last review once there are `review_every_calls` of them;
     "Run review now" takes the most recent ones.
  2. Measure each call (evidence.py, no AI): numbered transcript turns + timing facts from the recording.
  3. Judge each call (AI, `review_model`): outcome, whether / where / why the customer was lost, the agent's
     mistakes. The code then CHECKS the proof: every quote must really be in the cited agent turn, every
     fact id must exist. Findings without proof are removed, and "lost because of the agent" without a
     proven mistake is downgraded to "unclear".
  4. Find patterns across the batch (AI): a problem must be proven in at least `review_min_evidence`
     different calls (checked again by code) to become a suggestion. Each suggestion is one minimal,
     validated change to one setting (the same validation as the Settings page).
  5. A person approves (optionally after editing) or rejects each suggestion. Only an approval writes the
     setting, recorded in the settings history (with Undo) like any other change.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, select, update

from ...config import get_settings
from ...db import SessionLocal, utcnow
from ...defaults import DEFAULT_AGENT_SETTINGS, SETTING_LABELS
from ...models import (AgentSettings, Call, Lead, ReviewCallReport, ReviewRun, ReviewSuggestion, SettingsChange,
                       TranscriptTurn)
from ...schemas import iso
from .. import agent_config
from ..events import bus, spawn
from ..llm import client
from ..prompts import static_system_prompt
from ..repo import run_db
from ..settings_rules import SettingsError, validate_patch
from .evidence import build_evidence, norm_text, quote_in_turn
from .prompts import (FIELD_RULES, ISSUE_CATEGORIES, LOSS_CAUSES, OUTCOMES, TECHNICAL_CATEGORIES, TECHNICAL_HINT,
                      TOO_LONG_MIN_WORDS, batch_messages, call_review_messages)

log = logging.getLogger("review")
settings = get_settings()

MAX_CALLS_PER_RUN = 15
MAX_SUGGESTIONS = 4
CALL_CONCURRENCY = 3
SINCE_KEY = "_review_since"      # automatic reviews only look at calls made after the agent was installed
AUTO_RETRY_AFTER = timedelta(minutes=30)  # after a failed review, automatic reviews wait this long
INTERRUPTED = "Interrupted by a backend restart. Run it again."
# Measured facts (evidence.py) that prove a mistake category at an agent turn: issues of these categories need
# their measurement, and a pattern's evidence may be such a fact when the call's review didn't list the issue.
PROVING_FACTS: dict[str, set[str]] = {
    "too_long": {"long_reply", "monologue"},
    "many_questions": {"many_questions"},
    "slow_reply": {"slow_reply"},
    "talked_over": {"talked_over"},
    "repeated_question": {"repeated_question"},
}
# Sections of the agent's prompt that settings hold (sent to the batch review as editable fields instead)
FIELD_SECTIONS = ("# Goal of this call", "# Qualification questions", "# Objection handling", "# Business knowledge",
                  "# Learned rules")
# Models offered in Settings (any other OpenAI model name can be typed in too)
MODEL_CHOICES = (
    ("gpt-4o-mini", "Cheapest; the model the live calls use. Verdicts vary more."),
    ("gpt-5.6-luna", "Newest small model: fast and cheap, careful reasoning."),
    ("gpt-5.6-terra", "About as good as gpt-5.5 at half its price: good for automatic reviews."),
    ("gpt-5.5", "Strong, careful judgement."),
    ("gpt-5.6-sol", "Newest flagship: best judgement, same price as gpt-5.5."),
)


class ReviewError(Exception):
    def __init__(self, message: str, status: int = 400, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.data = data  # extra detail for the dashboard (e.g. the setting's current value on a stale approval)


# ---------------------------------------------------------------------- which calls
def _eligible(cfg: dict[str, Any]) -> list[Any]:
    directions = ("outbound", "inbound") if cfg.get("review_include_inbound", True) else ("outbound",)
    spoke = exists(select(TranscriptTurn.id).where(TranscriptTurn.call_id == Call.id, TranscriptTurn.role == "user"))
    return [Call.channel == "phone", Call.status == "completed", Call.answered_at.is_not(None),
            Call.analysis_status != "pending", Call.direction.in_(directions), spoke]


def _reviewed() -> Any:
    return exists(select(ReviewCallReport.id).join(ReviewRun, ReviewRun.id == ReviewCallReport.run_id)
                  .where(ReviewCallReport.call_id == Call.id, ReviewRun.status == "done"))


def _since() -> Any:
    with SessionLocal() as db:
        row = db.get(AgentSettings, 1)
        raw = (row.data or {}).get(SINCE_KEY) if row is not None and isinstance(row.data, dict) else None
    try:
        return datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def ensure_since() -> None:
    """Remember when the review agent was installed: automatic reviews never judge older calls (made with an
    older version of the agent). Kept in the settings row like other internal facts (not a setting)."""
    if _since() is not None:
        return
    with agent_config._lock, SessionLocal() as db:
        row = db.get(AgentSettings, 1)
        if row is None:
            db.add(AgentSettings(id=1, data={**copy.deepcopy(DEFAULT_AGENT_SETTINGS), SINCE_KEY: iso(utcnow())}))
        else:
            data = dict(row.data) if isinstance(row.data, dict) else {}
            data[SINCE_KEY] = iso(utcnow())
            row.data = data
        db.commit()


def new_call_count(cfg: dict[str, Any]) -> int:
    """Calls waiting for the next automatic review."""
    since = _since()
    with SessionLocal() as db:
        q = select(func.count(Call.id)).where(*_eligible(cfg), ~_reviewed())
        if since is not None:
            q = q.where(Call.created_at >= since)
        return int(db.scalar(q) or 0)


def _pick_calls(cfg: dict[str, Any], trigger: str, limit: int) -> list[int]:
    with SessionLocal() as db:
        if trigger == "auto":
            q = select(Call.id).where(*_eligible(cfg), ~_reviewed())
            since = _since()
            if since is not None:
                q = q.where(Call.created_at >= since)
            return list(db.scalars(q.order_by(Call.created_at.asc()).limit(limit)).all())
        ids = list(db.scalars(select(Call.id).where(*_eligible(cfg)).order_by(Call.created_at.desc())
                              .limit(limit)).all())
        return list(reversed(ids))


# ---------------------------------------------------------------------- the model
def _is_reasoning(model: str) -> bool:
    return model.startswith(("gpt-5", "o1", "o3", "o4"))


async def _ask_json(model: str, messages: list[dict], max_out: int) -> tuple[dict, int]:
    """One JSON answer from the review model, with the right parameters for reasoning models (gpt-5.x / o-series
    take max_completion_tokens and no temperature). One retry if the answer is cut off or isn't JSON."""
    reasoning = _is_reasoning(model)
    budget = max_out * (4 if reasoning else 1)
    tokens = 0
    msgs = list(messages)
    for attempt in range(2):
        kwargs: dict[str, Any] = {"model": model, "messages": msgs, "response_format": {"type": "json_object"},
                                  "max_completion_tokens": budget}
        if not reasoning:
            kwargs["temperature"] = 0  # the same calls should get the same verdict
        resp = await client().with_options(timeout=240.0, max_retries=2).chat.completions.create(**kwargs)
        tokens += resp.usage.total_tokens if resp.usage else 0
        choice = resp.choices[0]
        text = choice.message.content or ""
        if choice.finish_reason == "length":
            budget *= 2
            continue
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data, tokens
        except json.JSONDecodeError:
            pass
        msgs = [*messages, {"role": "assistant", "content": text[:4000]},
                {"role": "user", "content": "That was not a valid JSON object. Return only the JSON object."}]
    raise ReviewError(f"The review model ({model}) didn't return a usable answer. Try again.", 502)


# ---------------------------------------------------------------------- loading calls
def _load_packs(call_ids: list[int]) -> list[dict[str, Any]]:
    """Evidence packs (evidence.py) for the calls, in call order: {call_id, uuid, label, ...pack}."""
    out = []
    with SessionLocal() as db:
        for call_id in call_ids:
            c = db.get(Call, call_id)
            if c is None:
                continue
            lead = db.get(Lead, c.lead_id) if c.lead_id else None
            turns = [{"role": t.role, "text": t.text, "offset_ms": t.offset_ms, "interrupted": t.interrupted,
                      "latency_ms": t.latency_ms}
                     for t in db.scalars(select(TranscriptTurn).where(TranscriptTurn.call_id == c.id)
                                         .order_by(TranscriptTurn.id)).all()]
            call = {"uuid": c.uuid, "direction": c.direction, "duration_sec": c.duration_sec,
                    "end_reason": c.end_reason, "interest": c.interest, "rating": c.rating,
                    "sentiment": c.sentiment, "qualified": c.qualified, "summary": c.summary,
                    "objections": c.objections, "metrics": c.metrics}
            recording = settings.recordings_dir.parent / c.recording_path if c.recording_path else None
            pack = build_evidence(call, turns, recording)
            out.append({"call_id": c.id, "uuid": c.uuid, "created_at": iso(c.created_at),
                        "lead": (lead.name or lead.phone) if lead else "Unknown caller",
                        "direction": c.direction, "pack": pack})
    return out


# ---------------------------------------------------------------------- checking the proof
def _agent_turns(pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {t["id"]: t for t in pack["turns"] if t["role"] == "agent"}


def _locate_quote(quote: str, turn_id: str, pack: dict[str, Any]) -> str | None:
    """The agent turn that really contains the quote: the cited one, else any agent turn (the model sometimes
    cites the neighbouring turn), else None (not in the call: the finding is unproven)."""
    agents = _agent_turns(pack)
    if not quote or len(norm_text(quote)) < 3:
        return None
    if turn_id in agents and quote_in_turn(quote, agents[turn_id]["text"]):
        return turn_id
    for tid, t in agents.items():
        if quote_in_turn(quote, t["text"]):
            return tid
    return None


def _nearest_agent_turn(turn_id: str | None, pack: dict[str, Any]) -> str | None:
    ids = [t["id"] for t in pack["turns"]]
    if not turn_id or turn_id not in ids:
        return None
    agents = _agent_turns(pack)
    for tid in reversed(ids[:ids.index(turn_id) + 1]):
        if tid in agents:
            return tid
    return None


def _clip(value: Any, n: int) -> str:
    return str(value or "").strip()[:n]


def _severity(value: Any) -> int:
    try:
        return min(3, max(1, int(float(value))))
    except (TypeError, ValueError):
        return 1


def _reaction_turns(turn: str, pack: dict[str, Any]) -> set[str]:
    """The agent turn and the customer turns right after it (the customer's reaction to it)."""
    ids = [t["id"] for t in pack["turns"]]
    out = {turn}
    for t in pack["turns"][ids.index(turn) + 1:]:
        if t["role"] == "agent":
            break
        out.add(t["id"])
    return out


def _earlier_turn(value: Any, turn: str, category: str, pack: dict[str, Any]) -> str | None:
    """A valid "earlier" reference: a turn before this one. For an ignored question it must be the customer's
    turn with the question, at most 2 agent replies back."""
    ids = [t["id"] for t in pack["turns"]]
    e = str(value or "").strip().upper()
    if e not in ids or ids.index(e) >= ids.index(turn):
        return None
    if category == "ignored_question":
        between = pack["turns"][ids.index(e) + 1:ids.index(turn) + 1]
        if pack["turns"][ids.index(e)]["role"] != "customer" or sum(t["role"] == "agent" for t in between) > 2:
            return None
    return e


def _not_measured(category: str, turn: str, pack: dict[str, Any], earlier: str | None) -> str | None:
    """Why a measurable mistake is NOT backed by the measurement at this agent turn (None: backed, or the
    category isn't measurable and rests on its quote)."""
    t = _agent_turns(pack)[turn]
    kinds = {f["type"] for f in pack["facts"] if f.get("turn") == turn}
    if category == "too_long" and t["words"] < TOO_LONG_MIN_WORDS and not kinds & PROVING_FACTS["too_long"]:
        return f"the reply has only {t['words']} words"
    if category == "many_questions" and t.get("questions", 0) < 2:
        return f"the reply asks {t.get('questions', 0)} question(s)"
    if category == "slow_reply" and "slow_reply" not in kinds:
        return "no slow reply was measured there"
    if category == "talked_over" and "talked_over" not in kinds:
        return "no talking over the customer was measured there"
    if category == "repeated_question" and "repeated_question" not in kinds and not earlier:
        return "no earlier turn shows it was already asked or answered"
    if category == "ignored_question" and not earlier:
        return "the customer's question isn't cited"
    return None


def validate_call_review(raw: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
    """Keep only what the evidence proves. Returns the cleaned verdict with a `proof` note.

    An issue stays only when (1) its quote really is in the cited agent turn (or measured facts about that turn
    back it), (2) a measurable category is backed by its measurement (a "too long" reply has the words, "many
    questions" has the question marks, a slow reply was measured...), and (3) a repeated or ignored question
    points to the earlier turn. Cited facts must be about that moment of the call."""
    fact_turn = {f["id"]: f.get("turn") for f in pack["facts"]}
    texts = {t["id"]: t["text"] for t in pack["turns"]}
    agents = _agent_turns(pack)
    dropped: list[str] = []

    issues: list[dict[str, Any]] = []
    for it in raw.get("issues") or []:
        if not isinstance(it, dict):
            continue
        category = it.get("category") if it.get("category") in ISSUE_CATEGORIES else "other"
        cited_turn = str(it.get("turn") or "")
        turn = _nearest_agent_turn(cited_turn, pack)
        quote = _clip(it.get("quote"), 200)
        located = _locate_quote(quote, turn or "", pack)
        cited = [f for f in (it.get("facts") or []) if isinstance(f, str) and f in fact_turn]
        if located:
            turn = located
        elif turn and any(fact_turn[f] == turn for f in cited):
            quote = ""  # proven by measured facts about this turn; the words didn't match exactly
        else:
            dropped.append(f"{category} at {cited_turn or '?'} (quote not found in the call)")
            continue
        earlier = _earlier_turn(it.get("earlier"), turn, category, pack)
        why_not = _not_measured(category, turn, pack, earlier)
        if why_not:
            dropped.append(f"{category} at {turn} ({why_not})")
            continue
        if any(i["category"] == category and i["turn"] == turn for i in issues):
            continue  # the same finding twice
        near = _reaction_turns(turn, pack)
        facts = [f for f in cited if fact_turn[f] is None or fact_turn[f] in near]
        # show the measurement that proves it, even when the reviewer didn't cite it
        facts += [f["id"] for f in pack["facts"]
                  if f.get("turn") == turn and f["type"] in PROVING_FACTS.get(category, ()) and f["id"] not in facts]
        item = {"category": category, "turn": turn, "at": agents[turn]["at"], "quote": quote, "facts": facts,
                "customer_reaction": _clip(it.get("customer_reaction"), 300) or None,
                "severity": _severity(it.get("severity")), "why": _clip(it.get("why"), 300)}
        if earlier:
            item["earlier"] = earlier
            item["earlier_text"] = _clip(texts.get(earlier), 200)
        issues.append(item)
    issues.sort(key=lambda i: -i["severity"])

    strengths = []
    for it in raw.get("strengths") or []:
        if not isinstance(it, dict):
            continue
        located = _locate_quote(_clip(it.get("quote"), 200), str(it.get("turn") or ""), pack)
        if located:
            strengths.append({"turn": located, "at": agents[located]["at"], "quote": _clip(it.get("quote"), 200),
                              "why": _clip(it.get("why"), 300)})

    intent_lost = bool(raw.get("intent_lost"))
    cause = raw.get("loss_cause") if raw.get("loss_cause") in LOSS_CAUSES else None
    lost_at = _nearest_agent_turn(str(raw.get("lost_at") or ""), pack) if intent_lost else None
    if intent_lost and cause is None:
        cause = "unclear"
    # "lost because of the agent" needs WHERE (lost_at) and a proven, NAMED mistake (not "other", severity 2+)
    # at that moment: the agent reply right before the customer disengaged, or the reply before it. A mistake
    # earlier or later in the call didn't cause it. When the only proven mistakes there are speed / turn-taking
    # problems, the cause is technical (the script can't fix it).
    if cause == "agent":
        agent_ids = [t["id"] for t in pack["turns"] if t["role"] == "agent"]
        window: set[str] = set()
        if lost_at in agent_ids:
            k = agent_ids.index(lost_at)
            window = set(agent_ids[max(0, k - 1):k + 1])
        named = [i for i in issues if i["turn"] in window and i["severity"] >= 2 and i["category"] != "other"]
        if not named:
            cause = "unclear"
            dropped.append("\"lost because of the agent\" (no proven mistake at the moment the customer was lost)")
        elif all(i["category"] in TECHNICAL_CATEGORIES for i in named):
            cause = "technical"
            dropped.append("\"lost because of the agent\" became technical (the proven problem there is speed or "
                           "turn-taking, not the script)")
    return {
        "outcome": raw.get("outcome") if raw.get("outcome") in OUTCOMES else "neutral",
        "customer_intent": _clip(raw.get("customer_intent"), 300),
        "engagement": raw.get("engagement") if raw.get("engagement") in ("rising", "steady", "falling", "dropped")
        else "steady",
        "intent_lost": intent_lost,
        "lost_at": lost_at,
        "lost_at_time": agents[lost_at]["at"] if lost_at else None,
        "loss_cause": cause if intent_lost else None,
        "customer_side_reason": _clip(raw.get("customer_side_reason"), 300) or None if cause == "customer_side"
        else None,
        "verdict": _clip(raw.get("verdict"), 900),
        "issues": issues[:6],
        "strengths": strengths[:3],
        "proof": {"removed": dropped},
    }


def batch_stats(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Numbers measured by code across the batch (not by the model)."""
    n = len(items)
    evals = [it["evaluation"] for it in items]
    timing = [it["pack"]["timing"] for it in items if it["pack"]["timing"].get("recording")]
    agent_words = [t["words"] for it in items for t in it["pack"]["turns"] if t["role"] == "agent"]
    categories: dict[str, int] = {}
    for ev in evals:
        for cat in {i["category"] for i in ev["issues"]}:
            categories[cat] = categories.get(cat, 0) + 1
    outcomes: dict[str, int] = {}
    for ev in evals:
        outcomes[ev["outcome"]] = outcomes.get(ev["outcome"], 0) + 1

    def avg(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 1) if values else None

    return {
        "calls": n,
        "outcomes": outcomes,
        "lost_because_of_agent": sum(1 for ev in evals if ev["loss_cause"] == "agent"),
        "lost_customer_side": sum(1 for ev in evals if ev["loss_cause"] == "customer_side"),
        "lost_unclear_or_technical": sum(1 for ev in evals if ev["loss_cause"] in ("unclear", "technical")),
        "calls_with_issue": categories,
        "hung_up_during_agent_reply": sum(1 for it in items if any(f["type"] == "hangup_during_agent"
                                                                    for f in it["pack"]["facts"])),
        "avg_reply_gap_s": avg([t["avg_reply_gap_s"] for t in timing if t.get("avg_reply_gap_s") is not None]),
        "avg_agent_talk_share": avg([t["agent_share"] for t in timing if t.get("agent_share") is not None]),
        "avg_agent_words_per_reply": avg([float(w) for w in agent_words]),
        "replies_over_35_words_pct": round(100 * sum(1 for w in agent_words if w > 35) / len(agent_words))
        if agent_words else None,
    }


def _apply_operation(field: str, op: str, s: dict[str, Any], current: Any) -> Any:
    """The complete new value of a field for one suggested operation (raises ValueError if it can't apply)."""
    if op not in FIELD_RULES[field]:
        raise ValueError(f"{op} is not allowed for {field}")
    if field == "qualification_questions":
        value = s.get("value")
        if isinstance(value, str):
            value = [line.strip(" -•\t") for line in value.splitlines()]
        if not isinstance(value, list) or not [v for v in value if str(v).strip()]:
            raise ValueError("the questions must be a list")
        return [str(v).strip() for v in value if str(v).strip()]
    current = str(current or "")
    if op == "replace":
        value = str(s.get("value") or "").strip()
        if not value:
            raise ValueError("empty value")
        return value
    if op == "append":
        value = str(s.get("value") or "").strip()
        if not value:
            raise ValueError("nothing to add")
        if field == "learned_rules":
            value = "\n".join(line if line.lstrip().startswith(("-", "•")) else f"- {line.strip()}"
                              for line in value.splitlines() if line.strip())
        return f"{current.rstrip()}\n{value}".strip() if current.strip() else value
    find = str(s.get("find") or "")
    if not find.strip():
        raise ValueError("nothing to find")
    if current.count(find) != 1:
        raise ValueError("the text to change wasn't found exactly once")
    return current.replace(find, str(s.get("replace_with") or ""), 1)


def _finding_at(c: dict[str, Any], turn: str, category: str) -> str | None:
    """What proves THIS problem at this agent turn of the call: a verified mistake of the same category from the
    call's own review, or the measured fact for that category. None: that problem wasn't proven there (another
    problem at the same moment doesn't count: the proof is "the same problem in N calls")."""
    for i in (c.get("evaluation") or {}).get("issues") or []:
        if i.get("turn") == turn and i.get("category") == category:
            return i.get("why") or category
    for f in c["pack"]["facts"]:
        if f.get("turn") == turn and f["type"] in PROVING_FACTS.get(category, ()):
            return f["text"]
    return None


def validate_batch(raw: dict[str, Any], calls: dict[str, dict[str, Any]], cfg: dict[str, Any],
                   min_evidence: int, rejected: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Check the patterns' proof again across calls, then turn the suggestions into complete, validated new
    values. Returns (report, suggestions ready to store).

    A piece of evidence counts only if its quote really is in that call AND something was proven wrong at that
    turn (a verified finding of the call's own review, or a measured fact): a real but harmless line is no proof."""
    removed: list[str] = []
    patterns, watch = [], []
    for p in raw.get("patterns") or []:
        if not isinstance(p, dict):
            continue
        category = p.get("category") if p.get("category") in ISSUE_CATEGORIES else "other"
        evidence, proven_calls = [], set()
        for e in p.get("evidence") or []:
            if not isinstance(e, dict) or e.get("call") not in calls:
                continue
            c = calls[e["call"]]
            turn = _locate_quote(_clip(e.get("quote"), 200), str(e.get("turn") or ""), c["pack"])
            finding = _finding_at(c, turn, category) if turn else None
            if turn is None or finding is None:
                continue
            agents = _agent_turns(c["pack"])
            evidence.append({"call": e["call"], "call_uuid": c["uuid"], "lead": c["lead"], "call_at": c["created_at"],
                             "turn": turn, "at": agents[turn]["at"], "quote": _clip(e.get("quote"), 200),
                             "finding": _clip(finding, 300)})
            proven_calls.add(e["call"])
        item = {"id": _clip(p.get("id"), 10) or f"P{len(patterns) + len(watch) + 1}",
                "title": _clip(p.get("title"), 200), "category": category,
                "calls": sorted(proven_calls, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0),
                "evidence": evidence[:12], "impact": _clip(p.get("impact"), 400)}
        if category == "other":  # "other" problems in different calls aren't "the same problem"
            removed.append(f"pattern \"{item['title']}\" (no specific problem named)")
            watch.append({"title": item["title"], "calls": item["calls"],
                          "note": "Too vague to act on: no specific mistake category."})
        elif len(proven_calls) >= min_evidence:
            patterns.append(item)
        else:
            removed.append(f"pattern \"{item['title']}\" proven in {len(proven_calls)} call(s), "
                           f"{min_evidence} needed")
            watch.append({"title": item["title"], "calls": item["calls"],
                          "note": f"Proven in {len(proven_calls)} call(s); {min_evidence} needed for a suggestion."})
    for w in raw.get("watch_list") or []:
        if isinstance(w, dict) and w.get("title"):
            watch.append({"title": _clip(w.get("title"), 200),
                          "calls": [c for c in (w.get("calls") or []) if c in calls],
                          "note": _clip(w.get("note"), 300)})

    by_id = {p["id"]: p for p in patterns}
    rejected_values = {(r["field"], json.dumps(r.get("proposed"), ensure_ascii=False, sort_keys=True))
                       for r in rejected}
    suggestions: list[dict[str, Any]] = []
    for s in raw.get("suggestions") or []:
        if not isinstance(s, dict) or len(suggestions) >= MAX_SUGGESTIONS:
            continue
        field, pattern = s.get("field"), by_id.get(str(s.get("pattern") or ""))
        title = _clip(s.get("title"), 200) or (pattern["title"] if pattern else "Suggestion")
        if pattern is None:
            removed.append(f"suggestion \"{title}\" (its problem wasn't proven in enough calls)")
            continue
        if pattern["category"] in TECHNICAL_CATEGORIES:
            removed.append(f"suggestion \"{title}\" (its problem is technical, not the script: it goes to the team "
                           "notes instead)")
            continue
        if field not in FIELD_RULES:
            removed.append(f"suggestion \"{title}\" (the review agent may not change {field})")
            continue
        current = cfg.get(field)
        try:
            new_value = _apply_operation(field, str(s.get("operation") or ""), s, current)
            clean = validate_patch({field: new_value})[field]
        except (ValueError, SettingsError) as e:
            removed.append(f"suggestion \"{title}\" ({e})")
            continue
        if clean == current:
            removed.append(f"suggestion \"{title}\" (it wouldn't change anything)")
            continue
        if (field, json.dumps(clean, ensure_ascii=False, sort_keys=True)) in rejected_values:
            removed.append(f"suggestion \"{title}\" (the team rejected this exact change before)")
            continue
        if any(x["field"] == field for x in suggestions):
            removed.append(f"suggestion \"{title}\" (another suggestion already changes {field})")
            continue
        suggestions.append({"field": field, "title": title, "rationale": _clip(s.get("why"), 1200),
                            "expected_effect": _clip(s.get("expected_effect"), 600), "evidence": pattern["evidence"],
                            "base_value": copy.deepcopy(current), "proposed_value": clean,
                            "pattern": pattern["id"]})
    # proven technical problems always reach the team (no settings change can fix them)
    notes = [f"Technical, not the script: {p['title']} (proven in {len(p['calls'])} calls). "
             f"{TECHNICAL_HINT[p['category']]}" for p in patterns if p["category"] in TECHNICAL_CATEGORIES]
    notes += [_clip(n, 400) for n in (raw.get("team_notes") or []) if isinstance(n, str) and n.strip()][:6]
    report = {"summary": _clip(raw.get("summary"), 1500), "patterns": patterns, "watch_list": watch[:10],
              "team_notes": notes, "impact_of_recent_changes": _clip(raw.get("impact_of_recent_changes"), 600) or None,
              "proof": {"removed": removed}}
    return report, suggestions


# ---------------------------------------------------------------------- runs
_run_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None


def _lock() -> asyncio.Lock:
    global _run_lock
    loop = asyncio.get_running_loop()
    if _run_lock is None or _run_lock[0] is not loop:
        _run_lock = (loop, asyncio.Lock())
    return _run_lock[1]


def _running_run_id() -> int | None:
    with SessionLocal() as db:
        return db.scalar(select(ReviewRun.id).where(ReviewRun.status == "running").limit(1))


def _create_run(trigger: str, model: str, call_ids: list[int]) -> int:
    with SessionLocal() as db:
        run = ReviewRun(trigger=trigger, status="running", model=model, call_count=len(call_ids),
                        stage="Measuring the calls")
        db.add(run)
        db.commit()
        return run.id


async def start_run(trigger: str = "manual", limit: int | None = None) -> dict[str, Any]:
    """Start a review in the background. Raises ReviewError when one is running or there's nothing to review."""
    if not settings.openai_ready:
        raise ReviewError("OPENAI_API_KEY is not set: the review agent needs OpenAI.")
    cfg = agent_config.load_agent_settings()
    async with _lock():
        if await run_db(_running_run_id) is not None:
            raise ReviewError("A review is already running.", 409)
        n = max(1, min(int(limit or (MAX_CALLS_PER_RUN if trigger == "auto" else 10)), MAX_CALLS_PER_RUN))
        call_ids = await run_db(_pick_calls, cfg, trigger, n)
        if not call_ids:
            raise ReviewError("No calls to review yet: reviews need phone calls in which the customer spoke.")
        model = str(cfg.get("review_model") or DEFAULT_AGENT_SETTINGS["review_model"])
        run_id = await run_db(_create_run, trigger, model, call_ids)
    log.info("review #%d started (%s, %d calls, %s)", run_id, trigger, len(call_ids), model)
    spawn(_execute(run_id, call_ids, cfg, model))
    await publish()
    return await run_db(run_summary_by_id, run_id)


def _set_stage(run_id: int, stage: str) -> None:
    with SessionLocal() as db:
        db.execute(update(ReviewRun).where(ReviewRun.id == run_id).values(stage=stage[:200]))
        db.commit()


def _context(cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Decisions on recent suggestions and recent settings changes, for the batch review."""
    cutoff = utcnow() - timedelta(days=60)
    with SessionLocal() as db:
        decided = db.scalars(select(ReviewSuggestion).where(ReviewSuggestion.status.in_(("approved", "rejected")),
                                                            ReviewSuggestion.decided_at >= cutoff)
                             .order_by(ReviewSuggestion.id.desc()).limit(20)).all()
        decisions = [{"field": s.field, "title": s.title, "status": s.status, "decided_at": iso(s.decided_at),
                      "note": s.decision_note, "proposed": s.proposed_value} for s in decided]
        changes = db.scalars(select(SettingsChange).where(SettingsChange.created_at >= utcnow() - timedelta(days=14))
                             .order_by(SettingsChange.id.desc()).limit(15)).all()
        recent = [{"settings_version": c.id, "at": iso(c.created_at), "what": c.label, "source": c.source,
                   "fields": [SETTING_LABELS.get(k, k) for k in (c.changes or {})]} for c in changes]
    return decisions, recent


def _core_rules(cfg: dict[str, Any]) -> str:
    """The agent's built-in instructions that no setting holds (how it talks, listens, ends calls), so the batch
    review doesn't suggest rules the agent already has. Sections held by settings are sent as fields instead."""
    try:
        text = static_system_prompt(cfg, "outbound", "outbound")
    except Exception:  # noqa: BLE001  (never let this block a review)
        log.exception("couldn't build the core rules for the review")
        return ""
    parts = re.split(r"(?m)^(?=# )", text)
    return "\n".join(p.strip() for p in parts if p.startswith("# ") and not p.startswith(FIELD_SECTIONS))[:9000]


def _store_call_report(run_id: int, call_id: int, pack: dict[str, Any], evaluation: dict[str, Any]) -> None:
    with SessionLocal() as db:
        db.add(ReviewCallReport(run_id=run_id, call_id=call_id, evidence=pack, evaluation=evaluation))
        db.commit()


def _finish(run_id: int, report: dict[str, Any] | None, suggestions: list[dict[str, Any]], tokens: int,
            error: str | None) -> None:
    with SessionLocal() as db:
        run = db.get(ReviewRun, run_id)
        if run is None:
            return
        run.finished_at = utcnow()
        run.tokens = tokens
        if error:
            run.status, run.stage, run.error = "failed", "Failed", error[:1000]
        else:
            run.status, run.stage, run.report = "done", "Done", report
            # a newer suggestion for the same setting replaces an older one still waiting
            fields = {s["field"] for s in suggestions}
            if fields:
                db.execute(update(ReviewSuggestion).where(ReviewSuggestion.status == "pending",
                                                          ReviewSuggestion.field.in_(fields))
                           .values(status="superseded", decided_at=utcnow(),
                                   decision_note=f"Replaced by a newer suggestion (review #{run_id})"))
            for s in suggestions:
                db.add(ReviewSuggestion(run_id=run_id, field=s["field"], title=s["title"], rationale=s["rationale"],
                                        expected_effect=s["expected_effect"], evidence=s["evidence"],
                                        base_value=s["base_value"], proposed_value=s["proposed_value"]))
        db.commit()


async def _execute(run_id: int, call_ids: list[int], cfg: dict[str, Any], model: str) -> None:
    tokens = 0
    try:
        items = await asyncio.to_thread(_load_packs, call_ids)
        if not items:
            raise ReviewError("The calls to review were deleted.")
        rubric = str(cfg.get("review_rubric") or DEFAULT_AGENT_SETTINGS["review_rubric"])
        company = str(cfg.get("company_name") or "our company")
        knowledge = str(cfg.get("knowledge_base") or "")
        sem = asyncio.Semaphore(CALL_CONCURRENCY)
        done = 0

        async def review_one(item: dict[str, Any]) -> None:
            nonlocal tokens, done
            async with sem:
                raw, used = await _ask_json(model, call_review_messages(rubric, company, knowledge, item["pack"]),
                                            2500)
            tokens += used
            item["evaluation"] = validate_call_review(raw, item["pack"])
            await run_db(_store_call_report, run_id, item["call_id"], item["pack"], item["evaluation"])
            done += 1
            await run_db(_set_stage, run_id, f"Reviewed {done} of {len(items)} calls")
            await publish()

        results = await asyncio.gather(*(review_one(it) for it in items), return_exceptions=True)
        failures = [r for r in results if isinstance(r, BaseException)]
        reviewed = [it for it in items if "evaluation" in it]
        if not reviewed:
            raise failures[0] if failures else ReviewError("No call could be reviewed.")
        await run_db(_set_stage, run_id, "Finding patterns across the calls")
        await publish()
        calls: dict[str, dict[str, Any]] = {}
        compact = []
        for i, it in enumerate(reviewed, start=1):
            cid = f"C{i}"
            calls[cid] = it
            ev = it["evaluation"]
            compact.append({"id": cid, "direction": it["direction"], "length": it["pack"]["call"]["length"],
                            "ended": it["pack"]["call"]["ended"], "settings_version":
                            it["pack"]["call"].get("settings_version"),
                            **{k: ev[k] for k in ("outcome", "customer_intent", "engagement", "intent_lost", "lost_at",
                                                  "loss_cause", "customer_side_reason", "verdict", "issues",
                                                  "strengths")}})
        stats = batch_stats(reviewed)
        min_evidence = max(1, min(len(reviewed), int(cfg.get("review_min_evidence") or 2)))
        decisions, recent = await run_db(_context, cfg)
        fields = {f: cfg.get(f) for f in FIELD_RULES}
        raw, used = await _ask_json(model, batch_messages(rubric, company, min_evidence, compact, stats, fields,
                                                          decisions, recent, _core_rules(cfg)), 4000)
        tokens += used
        report, suggestions = validate_batch(raw, calls, cfg, min_evidence,
                                             [d for d in decisions if d["status"] == "rejected"])
        report["stats"] = stats
        report["min_evidence"] = min_evidence
        report["calls"] = [{"id": cid, "call_uuid": it["uuid"], "lead": it["lead"], "call_at": it["created_at"],
                            "direction": it["direction"]} for cid, it in calls.items()]
        if failures:
            report["proof"]["removed"].append(f"{len(failures)} call(s) couldn't be reviewed: {failures[0]}")
        await run_db(_finish, run_id, report, suggestions, tokens, None)
        log.info("review #%d done: %d calls, %d patterns, %d suggestions, %d tokens", run_id, len(reviewed),
                 len(report["patterns"]), len(suggestions), tokens)
    except Exception as e:  # noqa: BLE001
        log.exception("review #%d failed", run_id)
        message = str(e) if isinstance(e, ReviewError) else f"{type(e).__name__}: {str(e)[:300]}"
        if "model" in message.lower() and ("not found" in message.lower() or "does not exist" in message.lower()):
            message += " (check Settings > Review agent > Model)"
        await run_db(_finish, run_id, None, [], tokens, message)
    await publish()


def auto_paused_until() -> datetime | None:
    """When the last review failed (no access to the model, OpenAI down, ...), automatic reviews wait
    AUTO_RETRY_AFTER instead of failing again after every call. "Run review now" still works. A review cut short
    by a backend restart doesn't count."""
    with SessionLocal() as db:
        last = db.scalars(select(ReviewRun).where(ReviewRun.status != "running")
                          .order_by(ReviewRun.id.desc()).limit(1)).first()
        if last is None or last.status != "failed" or last.error == INTERRUPTED or last.finished_at is None:
            return None
        until = last.finished_at + AUTO_RETRY_AFTER
    return until if until > utcnow() else None


async def maybe_auto_run() -> None:
    """After every call analysis (and at startup): start an automatic review once enough new calls wait."""
    try:
        cfg = agent_config.load_agent_settings()
        if not cfg.get("review_enabled", True) or not settings.openai_ready:
            return
        if await run_db(_running_run_id) is not None or await run_db(auto_paused_until) is not None:
            return
        every = int(cfg.get("review_every_calls") or 5)
        if await run_db(new_call_count, cfg) < every:
            return
        await start_run("auto")
    except ReviewError:
        pass
    except Exception:  # noqa: BLE001
        log.exception("automatic review check failed")


def recover_on_startup() -> int:
    """Reviews still 'running' from a previous process can't finish: mark them failed."""
    with SessionLocal() as db:
        n = db.execute(update(ReviewRun).where(ReviewRun.status == "running")
                       .values(status="failed", stage="Failed", finished_at=utcnow(), error=INTERRUPTED))
        db.commit()
        return int(n.rowcount or 0)


# ---------------------------------------------------------------------- approval
def _suggestion_out(s: ReviewSuggestion) -> dict[str, Any]:
    return {"id": s.id, "run_id": s.run_id, "created_at": iso(s.created_at), "field": s.field,
            "field_label": SETTING_LABELS.get(s.field, s.field), "title": s.title, "rationale": s.rationale,
            "expected_effect": s.expected_effect, "evidence": s.evidence or [], "base_value": s.base_value,
            "proposed_value": s.proposed_value, "status": s.status, "decided_at": iso(s.decided_at),
            "decision_note": s.decision_note, "applied_value": s.applied_value, "change_id": s.change_id}


def list_suggestions(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        q = select(ReviewSuggestion)
        if status:
            q = q.where(ReviewSuggestion.status == status)
        return [_suggestion_out(s) for s in db.scalars(q.order_by(ReviewSuggestion.id.desc()).limit(limit)).all()]


def approve(suggestion_id: int, value: Any = None, force: bool = False) -> dict[str, Any]:
    """Write the suggested (or edited) value into the settings: only ever after a person approved it."""
    with SessionLocal() as db:
        s = db.get(ReviewSuggestion, suggestion_id)
        if s is None:
            raise ReviewError("Suggestion not found.", 404)
        if s.status != "pending":
            raise ReviewError(f"This suggestion was already {s.status}.", 409)
        field, title, base, proposed = s.field, s.title, s.base_value, s.proposed_value
    current = agent_config.load_agent_settings().get(field)
    if current != base and not force:
        # approving would replace that newer edit: the dashboard shows it and offers to edit first
        raise ReviewError(f"{SETTING_LABELS.get(field, field)} was changed after this suggestion was made. "
                          "Approve anyway?", 409, {"stale": True, "current": current})
    final = proposed if value is None else value
    try:
        clean = validate_patch({field: final})
    except SettingsError as e:
        raise ReviewError(str(e), 422) from None
    if field not in clean:
        raise ReviewError("Nothing to save.", 422)
    edited = clean[field] != proposed
    _, change_id = agent_config.apply_settings_change(
        clean, source="review", label=f"Review suggestion #{suggestion_id}{' (edited)' if edited else ''}: {title}",
        ref_id=suggestion_id)
    with SessionLocal() as db:
        s = db.get(ReviewSuggestion, suggestion_id)
        s.status, s.decided_at, s.applied_value, s.change_id = "approved", utcnow(), clean[field], change_id
        if edited:
            s.decision_note = "Approved with edits"
        db.commit()
        return _suggestion_out(s)


def reject(suggestion_id: int, note: str | None = None) -> dict[str, Any]:
    with SessionLocal() as db:
        s = db.get(ReviewSuggestion, suggestion_id)
        if s is None:
            raise ReviewError("Suggestion not found.", 404)
        if s.status != "pending":
            raise ReviewError(f"This suggestion was already {s.status}.", 409)
        s.status, s.decided_at, s.decision_note = "rejected", utcnow(), (note or "").strip()[:500] or None
        db.commit()
        return _suggestion_out(s)


# ---------------------------------------------------------------------- reading
def run_summary(run: ReviewRun, db) -> dict[str, Any]:
    counts = dict(db.execute(select(ReviewSuggestion.status, func.count(ReviewSuggestion.id))
                             .where(ReviewSuggestion.run_id == run.id).group_by(ReviewSuggestion.status)).all())
    report = run.report or {}
    return {"id": run.id, "created_at": iso(run.created_at), "finished_at": iso(run.finished_at),
            "trigger": run.trigger, "status": run.status, "stage": run.stage, "model": run.model,
            "call_count": run.call_count, "tokens": run.tokens, "error": run.error,
            "summary": report.get("summary"), "patterns": len(report.get("patterns") or []),
            "suggestions": sum(counts.values()), "pending": counts.get("pending", 0)}


def run_summary_by_id(run_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        run = db.get(ReviewRun, run_id)
        if run is None:
            raise ReviewError("Review not found.", 404)
        return run_summary(run, db)


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        return [run_summary(r, db) for r in db.scalars(select(ReviewRun).order_by(ReviewRun.id.desc())
                                                       .limit(limit)).all()]


def run_detail(run_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        run = db.get(ReviewRun, run_id)
        if run is None:
            raise ReviewError("Review not found.", 404)
        out = run_summary(run, db)
        out["report"] = run.report or {}
        reports = db.execute(select(ReviewCallReport, Call).join(Call, Call.id == ReviewCallReport.call_id)
                             .where(ReviewCallReport.run_id == run_id).order_by(Call.created_at)).all()
        out["calls"] = []
        for rep, call in reports:
            lead = db.get(Lead, call.lead_id) if call.lead_id else None
            out["calls"].append({"call_uuid": call.uuid, "lead": (lead.name or lead.phone) if lead else "Unknown caller",
                                 "call_at": iso(call.created_at), "direction": call.direction,
                                 "duration_sec": call.duration_sec, "evaluation": rep.evaluation,
                                 "facts": (rep.evidence or {}).get("facts", []),
                                 "timing": (rep.evidence or {}).get("timing", {})})
        out["suggestion_items"] = [_suggestion_out(s) for s in db.scalars(
            select(ReviewSuggestion).where(ReviewSuggestion.run_id == run_id).order_by(ReviewSuggestion.id)).all()]
        return out


def status() -> dict[str, Any]:
    cfg = agent_config.load_agent_settings()
    with SessionLocal() as db:
        running = db.scalars(select(ReviewRun).where(ReviewRun.status == "running").limit(1)).first()
        last = db.scalars(select(ReviewRun).where(ReviewRun.status != "running")
                          .order_by(ReviewRun.id.desc()).limit(1)).first()
        pending = int(db.scalar(select(func.count(ReviewSuggestion.id))
                                .where(ReviewSuggestion.status == "pending")) or 0)
        running_out = run_summary(running, db) if running else None
        last_out = run_summary(last, db) if last else None
    return {"enabled": bool(cfg.get("review_enabled", True)), "openai_ready": settings.openai_ready,
            "every": int(cfg.get("review_every_calls") or 5), "min_evidence": int(cfg.get("review_min_evidence") or 2),
            "model": cfg.get("review_model"), "include_inbound": bool(cfg.get("review_include_inbound", True)),
            "new_calls": new_call_count(cfg), "running": running_out, "last_run": last_out,
            "auto_paused_until": iso(auto_paused_until()), "pending_suggestions": pending}


async def publish() -> None:
    """Live update for the dashboard (Review page, the pending-suggestions badge)."""
    try:
        bus.publish({"type": "review.update", "status": await run_db(status)})
    except Exception:  # noqa: BLE001
        log.exception("review status publish failed")
