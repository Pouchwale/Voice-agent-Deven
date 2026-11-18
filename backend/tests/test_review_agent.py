"""The call-review agent: evidence from the recording, proof checks, patterns across calls, suggestions that only
apply after approval, automatic reviews every N calls, and its API. The AI model is replaced by a scripted fake.

The tests never change calls made by other tests: manual reviews take exactly the calls a test made (the newest
ones), and automatic reviews count from a "since" time each test sets."""
from __future__ import annotations

import asyncio
import json
import math
import struct
import time
import uuid as uuidlib
import wave
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.db import SessionLocal, utcnow
from app.defaults import DEFAULT_AGENT_SETTINGS
from app.main import app
from app.models import (AgentSettings, Call, Lead, ReviewCallReport, ReviewRun, ReviewSuggestion, SettingsChange,
                        TranscriptTurn)
from app.routers import review as review_router
from app.schemas import iso
from app.services import agent_config
from app.services.prompts import static_system_prompt
from app.services.repo import get_call_detail_dict
from app.services.review import agent as review
from app.services.review import evidence as ev

# the real functions, captured at import (conftest replaces both in every test: no automatic reviews, no OpenAI)
REAL_AUTO = review.maybe_auto_run
REAL_ASK = review._ask_json

LOCAL = ("127.0.0.1", 50000)
LONG_PITCH = ("जी, हम बहुत सारे pouches बनाते हैं, stand up pouch, center seal pouch, three side seal pouch, spout "
              "pouch, vacuum pouch और बहुत कुछ। आपका product क्या है? कितनी quantity चाहिए? और कब तक चाहिए?")
PITCH_QUOTE = "हम बहुत सारे pouches बनाते हैं"


# ---------------------------------------------------------------- helpers
def _tone(seconds: float, rate: int = 8000, amp: int = 6000) -> bytes:
    n = int(seconds * rate)
    return b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * 220 * i / rate))) for i in range(n))


def _silence(seconds: float, rate: int = 8000) -> bytes:
    return b"\x00\x00" * int(seconds * rate)


def _stereo_wav(path: Path, customer: bytes, agent: bytes, rate: int = 8000) -> None:
    import audioop
    n = max(len(customer), len(agent))
    customer, agent = customer.ljust(n, b"\x00"), agent.ljust(n, b"\x00")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(audioop.add(audioop.tostereo(customer, 2, 1, 0), audioop.tostereo(agent, 2, 0, 1), 2))


def _make_call(turns: list[tuple[str, str]], *, direction: str = "outbound", channel: str = "phone",
               created=None, end_reason: str = "user_hangup") -> str:
    when = created or utcnow()
    with SessionLocal() as db:
        lead = Lead(name="Test Lead", phone=f"+9196{uuidlib.uuid4().int % 10**8:08d}", company="Test Co")
        db.add(lead)
        db.flush()
        call = Call(uuid=uuidlib.uuid4().hex, lead_id=lead.id, channel=channel, direction=direction,
                    status="completed", end_reason=end_reason, started_at=when, answered_at=when, ended_at=utcnow(),
                    duration_sec=60.0, analysis_status="done", interest="cold", rating=3, summary="Test call.",
                    created_at=when, metrics={"user_words": sum(len(t.split()) for r, t in turns if r == "user")})
        db.add(call)
        db.flush()
        for i, (role, text) in enumerate(turns):
            db.add(TranscriptTurn(call_id=call.id, role=role, text=text, offset_ms=i * 4000,
                                  latency_ms=1200 if role == "agent" and i else None))
        db.commit()
        return call.uuid


def _pitch_call(**kw) -> str:
    return _make_call([("agent", "नमस्ते जी! मैं PouchWale से बोल रही हूँ। दो minute बात कर सकते हैं?"),
                       ("user", "हाँ बोलिए"),
                       ("agent", LONG_PITCH),
                       ("user", "ठीक है बाद में देखेंगे")], **kw)


def _set_since(when) -> None:
    """Pretend the review agent was installed at `when`: automatic reviews only count calls made after it."""
    with SessionLocal() as db:
        row = db.get(AgentSettings, 1)
        row.data = {**row.data, review.SINCE_KEY: iso(when)}
        db.commit()
    agent_config._cache = None


class FakeModel:
    """Scripted review model: a long first pitch that lost the customer, plus one made-up quote (must be removed)."""

    def __init__(self, suggestions: list[dict] | None = None, fail_calls: bool = False):
        self.calls: list[list[dict]] = []
        self.suggestions = suggestions
        self.fail_calls = fail_calls

    async def __call__(self, model, messages, max_out):
        self.calls.append(messages)
        system = messages[0]["content"]
        if "senior conversation analyst" in system:
            if self.fail_calls:
                raise review.ReviewError("model unavailable", 502)
            return {
                "outcome": "lost", "customer_intent": "Open to hearing about pouches.", "engagement": "dropped",
                "intent_lost": True, "lost_at": "T3", "loss_cause": "agent", "verdict": "Lost after a long pitch.",
                "issues": [
                    {"category": "too_long", "turn": "T3", "quote": PITCH_QUOTE, "facts": [], "severity": 3,
                     "customer_reaction": "T4: put it off", "why": "A list of every pouch instead of one line."},
                    {"category": "pushy", "turn": "T3", "quote": "this was never said on the call", "facts": [],
                     "severity": 3, "why": "invented"},
                ],
                "strengths": [{"turn": "T1", "quote": "दो minute बात कर सकते हैं", "why": "Polite permission ask."}],
            }, 100
        payload = json.loads(messages[1]["content"])
        ids = [c["id"] for c in payload["calls"]]
        return {
            "summary": "Customers were lost right after the long first pitch.",
            "patterns": [
                {"id": "P1", "title": "First pitch too long", "category": "too_long", "calls": ids,
                 "evidence": [{"call": cid, "turn": "T3", "quote": PITCH_QUOTE} for cid in ids], "impact": "Lost."},
                {"id": "P2", "title": "Only in one call", "category": "pushy", "calls": [ids[0]],
                 "evidence": [{"call": ids[0], "turn": "T3", "quote": PITCH_QUOTE}], "impact": "?"},
            ],
            "watch_list": [], "team_notes": ["Customers ask about delivery time; add a typical lead time."],
            "impact_of_recent_changes": None,
            "suggestions": self.suggestions if self.suggestions is not None else [
                {"pattern": "P1", "field": "learned_rules", "operation": "append",
                 "value": "Keep the first pitch to one short sentence, then ask one question.",
                 "title": "Shorter first pitch", "why": "3 of 3 calls lost after it.", "expected_effect": "More replies."},
                {"pattern": "P2", "field": "opening_line", "operation": "replace", "value": "नमस्ते!",
                 "title": "Unproven", "why": "one call", "expected_effect": "?"},
                {"pattern": "P1", "field": "voice", "operation": "replace", "value": "ishita",
                 "title": "Not allowed", "why": "-", "expected_effect": "-"},
                {"pattern": "P1", "field": "objection_handling", "operation": "edit", "find": "TEXT NOT THERE",
                 "replace_with": "x", "title": "Bad edit", "why": "-", "expected_effect": "-"},
            ],
        }, 200


async def _wait_run(run_id: int, timeout: float = 15) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        out = review.run_summary_by_id(run_id)
        if out["status"] != "running":
            return out
        await asyncio.sleep(0.05)
    raise AssertionError("review didn't finish")


async def _manual(limit: int) -> dict:
    run = await review.start_run("manual", limit)
    return await _wait_run(run["id"])


async def _auto() -> dict | None:
    """The check that runs after every call analysis; the finished review it started, or None."""
    before = review.list_runs(1)
    await review.maybe_auto_run()
    after = review.list_runs(1)
    if not after or (before and after[0]["id"] == before[0]["id"]):
        return None
    return await _wait_run(after[0]["id"])


@pytest.fixture()
def fresh():
    """No reviews or suggestions from other tests, default settings."""
    def clean() -> None:
        with SessionLocal() as db:
            db.query(ReviewSuggestion).delete()
            db.query(ReviewCallReport).delete()
            db.query(ReviewRun).delete()
            db.commit()
    clean()
    agent_config.reset_agent_settings()
    yield
    clean()
    agent_config.reset_agent_settings()


# ---------------------------------------------------------------- evidence
def test_evidence_from_the_recording(tmp_path):
    """Customer speaks 0-1 s, waits 3 s, the agent talks from 4 s until the customer hangs up mid-sentence."""
    wav = tmp_path / "call.wav"
    _stereo_wav(wav, _tone(1.0) + _silence(9.0), _silence(4.0) + _tone(6.0, amp=8000))
    turns = [{"role": "user", "text": "हाँ बोलिए", "offset_ms": 1000},
             {"role": "agent", "text": LONG_PITCH, "offset_ms": 4500, "latency_ms": 3100}]
    pack = ev.build_evidence({"end_reason": "user_hangup", "duration_sec": 10}, turns, wav)
    kinds = {f["type"]: f for f in pack["facts"]}
    assert pack["timing"]["recording"] and pack["timing"]["max_reply_gap_s"] == pytest.approx(3.0, abs=0.3)
    assert kinds["slow_reply"]["turn"] == "T2" and "waited 3.0 s" in kinds["slow_reply"]["text"]
    assert kinds["hangup_during_agent"]["turn"] == "T2" and "hung up 6.0 s into" in kinds["hangup_during_agent"]["text"]
    assert kinds["many_questions"]["turn"] == "T2"
    assert pack["turns"][0]["role"] == "customer" and pack["turns"][1]["questions"] == 3


def test_a_monologue_is_found_even_when_the_agent_talks_most_of_the_call(tmp_path):
    """The agent talks 17 of 20 s (the noise floor must not land on its speech), starting over the customer."""
    wav = tmp_path / "call.wav"
    _stereo_wav(wav, _tone(3.0) + _silence(17.0), _silence(1.5) + _tone(17.0, amp=8000) + _silence(1.5))
    turns = [{"role": "user", "text": "मुझे rice के लिए pouch चाहिए", "offset_ms": 1000},
             {"role": "agent", "text": "जी, rice के लिए stand up pouch अच्छा रहता है।", "offset_ms": 2000},
             {"role": "user", "text": "क्या बोल रहे हो, फिर से बोलो", "offset_ms": 19000}]
    pack = ev.build_evidence({"end_reason": "agent_ended", "duration_sec": 20}, turns, wav)
    kinds = {f["type"]: f for f in pack["facts"]}
    assert {"talked_over", "monologue", "customer_friction"} <= set(kinds)
    assert kinds["monologue"]["turn"] == "T2" and "17 s" in kinds["monologue"]["text"]
    assert "1.5 s overlap" in kinds["talked_over"]["text"]


def test_quote_matching_ignores_punctuation_and_spelling_variants():
    assert ev.quote_in_turn("हाँ, आवाज़ आ रही", "हां आवाज आ रही है।")
    assert ev.quote_in_turn("MOQ एक हज़ार", "हमारा moq एक हजार pieces से start होता है")
    assert not ev.quote_in_turn("price बहुत कम", "हमारा MOQ एक हज़ार है")
    assert not ev.quote_in_turn("है", "हमारा MOQ एक हज़ार है")  # too short to prove anything


def test_evidence_without_a_recording_still_has_transcript_facts():
    turns = [{"role": "agent", "text": "आपका product क्या है?", "offset_ms": 0},
             {"role": "user", "text": "namkeen", "offset_ms": 3000},
             {"role": "agent", "text": "अच्छा, आपका product क्या है?", "offset_ms": 6000}]
    pack = ev.build_evidence({"end_reason": "silence_timeout"}, turns, None)
    kinds = {f["type"] for f in pack["facts"]}
    assert pack["timing"] == {"recording": False}
    assert {"repeated_question", "went_silent"} <= kinds


# ---------------------------------------------------------------- proof checks
def _pack():
    turns = [{"role": "agent", "text": "नमस्ते जी! दो minute बात कर सकते हैं?", "offset_ms": 0},
             {"role": "user", "text": "हाँ बोलिए", "offset_ms": 3000},
             {"role": "agent", "text": LONG_PITCH, "offset_ms": 6000, "latency_ms": 3200},
             {"role": "user", "text": "बाद में", "offset_ms": 12000}]
    return ev.build_evidence({"end_reason": "user_hangup"}, turns, None)


def test_unproven_findings_are_removed():
    pack = _pack()
    slow = next(f["id"] for f in pack["facts"] if f["type"] == "slow_reply")
    out = review.validate_call_review({
        "outcome": "lost", "intent_lost": True, "lost_at": "T4", "loss_cause": "agent",
        "issues": [
            {"category": "too_long", "turn": "T1", "quote": PITCH_QUOTE, "severity": 3},       # wrong turn: moved to T3
            {"category": "pushy", "turn": "T3", "quote": "never said this", "severity": 3},    # not in the call: removed
            {"category": "slow_reply", "turn": "T3", "quote": "", "facts": [slow], "severity": 2},  # proven by a fact
            {"category": "made_up", "turn": "T3", "quote": "center seal pouch", "severity": "7"},  # -> other, severity 3
        ]}, pack)
    cats = [(i["category"], i["turn"]) for i in out["issues"]]
    assert ("too_long", "T3") in cats and ("slow_reply", "T3") in cats and ("other", "T3") in cats
    assert all(i["category"] != "pushy" for i in out["issues"])
    assert out["lost_at"] == "T3" and out["lost_at_time"] == "0:06"  # a customer turn -> the agent turn before it
    assert out["loss_cause"] == "agent" and len(out["proof"]["removed"]) == 1


def test_blaming_the_agent_needs_a_proven_mistake():
    out = review.validate_call_review({"outcome": "lost", "intent_lost": True, "lost_at": "T3", "loss_cause": "agent",
                                       "issues": [{"category": "pushy", "turn": "T3", "quote": "invented words here",
                                                   "severity": 3}]}, _pack())
    assert out["issues"] == [] and out["loss_cause"] == "unclear"
    assert any("no proven mistake" in r for r in out["proof"]["removed"])


def test_a_mistake_after_the_customer_was_lost_cant_be_the_cause():
    turns = [{"role": "agent", "text": "नमस्ते जी! दो minute बात कर सकते हैं?", "offset_ms": 0},
             {"role": "user", "text": "हाँ बोलिए", "offset_ms": 3000},
             {"role": "agent", "text": "जी, आपका product क्या है?", "offset_ms": 6000},
             {"role": "user", "text": "अभी कुछ नहीं चाहिए", "offset_ms": 9000},
             {"role": "agent", "text": LONG_PITCH, "offset_ms": 12000},
             {"role": "user", "text": "ok bye", "offset_ms": 20000}]
    pack = ev.build_evidence({"end_reason": "user_hangup"}, turns, None)
    out = review.validate_call_review({"outcome": "lost", "intent_lost": True, "lost_at": "T4", "loss_cause": "agent",
                                       "issues": [{"category": "too_long", "turn": "T5", "quote": PITCH_QUOTE,
                                                   "severity": 3}]}, pack)
    assert [i["turn"] for i in out["issues"]] == ["T5"]  # the mistake itself is real and stays listed
    assert out["lost_at"] == "T3" and out["loss_cause"] == "unclear"


def test_blame_needs_a_named_mistake_at_the_moment_of_the_loss():
    """Agent turns T1, T3, T5, T7; the customer is lost after T7."""
    turns = [{"role": "agent", "text": LONG_PITCH, "offset_ms": 0},
             {"role": "user", "text": "हाँ", "offset_ms": 9000},
             {"role": "agent", "text": "जी, आपका product क्या है? कितनी quantity चाहिए?", "offset_ms": 12000},
             {"role": "user", "text": "namkeen", "offset_ms": 15000},
             {"role": "agent", "text": "अच्छा जी।", "offset_ms": 18000, "latency_ms": 3400},
             {"role": "user", "text": "हम्म", "offset_ms": 21000},
             {"role": "agent", "text": "तो आप कब तक order करना चाहेंगे?", "offset_ms": 24000},
             {"role": "user", "text": "बाद में", "offset_ms": 27000}]
    pack = ev.build_evidence({"end_reason": "user_hangup"}, turns, None)

    def judge(issues, lost_at="T8"):
        return review.validate_call_review({"intent_lost": True, "lost_at": lost_at, "loss_cause": "agent",
                                            "issues": issues}, pack)

    long_t1 = {"category": "too_long", "turn": "T1", "quote": PITCH_QUOTE, "severity": 3}
    many_t3 = {"category": "many_questions", "turn": "T3", "quote": "आपका product क्या है", "severity": 2}
    slow_t5 = {"category": "slow_reply", "turn": "T5", "quote": "", "facts": [
        next(f["id"] for f in pack["facts"] if f["type"] == "slow_reply")], "severity": 2}
    other_t7 = {"category": "other", "turn": "T7", "quote": "कब तक order करना चाहेंगे", "severity": 3,
                "why": "The customer cut the agent off."}
    assert judge([long_t1])["loss_cause"] == "unclear"    # a real mistake, but 3 replies before the loss
    assert judge([other_t7])["loss_cause"] == "unclear"   # a reaction filed as "other" can't carry the blame
    assert judge([slow_t5])["loss_cause"] == "technical"  # the reply before the loss was slow: not the script
    assert judge([slow_t5, many_t3])["loss_cause"] == "technical"  # T3 is outside the moment of the loss
    assert judge([many_t3], lost_at="T4")["loss_cause"] == "agent"  # lost right after the double question
    assert judge([many_t3], lost_at=None)["loss_cause"] == "unclear"  # "where" is part of the proof


def test_measured_mistakes_need_their_measurement():
    """A real quote isn't enough when the label can be measured: 8 words aren't "too long", one question isn't
    "many", and a slow reply must have been measured at that turn."""
    pack = _pack()  # T1 agent: 8 words, 1 question · T2 customer · T3 agent: the long pitch, 3 questions, slow
    slow = next(f["id"] for f in pack["facts"] if f["type"] == "slow_reply")  # measured at T3
    out = review.validate_call_review({"issues": [
        {"category": "too_long", "turn": "T1", "quote": "दो minute बात कर सकते हैं", "severity": 2},
        {"category": "many_questions", "turn": "T1", "quote": "दो minute बात कर सकते हैं", "severity": 2},
        {"category": "slow_reply", "turn": "T1", "quote": "दो minute बात कर सकते हैं", "facts": [slow], "severity": 2},
        {"category": "many_questions", "turn": "T3", "quote": PITCH_QUOTE, "severity": 2},
        {"category": "ignored_question", "turn": "T3", "quote": PITCH_QUOTE, "earlier": "T2", "severity": 2},
        {"category": "ignored_question", "turn": "T3", "quote": "center seal pouch", "earlier": "T1", "severity": 2},
        {"category": "repeated_question", "turn": "T3", "quote": "spout pouch", "severity": 1},
        {"category": "too_long", "turn": "T3", "quote": PITCH_QUOTE, "severity": 3},
        {"category": "too_long", "turn": "T3", "quote": "vacuum pouch", "severity": 3},  # the same finding twice
    ]}, pack)
    assert {(i["category"], i["turn"]) for i in out["issues"]} == {
        ("many_questions", "T3"), ("ignored_question", "T3"), ("too_long", "T3")}
    removed = " | ".join(out["proof"]["removed"])
    for reason in ("only 8 words", "asks 1 question", "no slow reply was measured", "question isn't cited",
                   "already asked or answered"):
        assert reason in removed, reason
    ignored = next(i for i in out["issues"] if i["category"] == "ignored_question")
    assert ignored["earlier"] == "T2" and ignored["earlier_text"] == "हाँ बोलिए"
    many = next(i for i in out["issues"] if i["category"] == "many_questions")
    measured = {f["id"]: f for f in pack["facts"]}
    assert any(measured[f]["type"] == "many_questions" for f in many["facts"])  # the proof is attached


def test_customer_side_reasons_are_kept_apart():
    out = review.validate_call_review({"outcome": "not_a_fit", "intent_lost": True, "loss_cause": "customer_side",
                                       "customer_side_reason": "Already has a supplier.", "issues": []}, _pack())
    assert out["loss_cause"] == "customer_side" and out["customer_side_reason"] == "Already has a supplier."


# ---------------------------------------------------------------- suggestions
def test_operations_build_complete_validated_values():
    s = review._apply_operation
    assert s("learned_rules", "append", {"value": "Short pitch.\n- One question."}, "") == "- Short pitch.\n- One question."
    assert s("learned_rules", "append", {"value": "New rule."}, "- Old rule.") == "- Old rule.\n- New rule."
    assert s("call_purpose", "edit", {"find": "step two", "replace_with": "step 2"}, "step one, step two") == \
        "step one, step 2"
    with pytest.raises(ValueError):
        s("call_purpose", "edit", {"find": "missing", "replace_with": "x"}, "step one")
    with pytest.raises(ValueError):
        s("knowledge_base", "replace", {"value": "everything new"}, "facts")  # long text: never replaced whole
    assert s("qualification_questions", "replace", {"value": "- Product?\n- Quantity?"}, []) == ["Product?", "Quantity?"]


def test_bad_suggestions_never_reach_the_team():
    pack = _pack()
    # each call's own (verified) review found the long pitch at T3; the slow reply there is measured
    verdict = {"issues": [{"category": "too_long", "turn": "T3", "why": "A list of every pouch instead of one line."}]}
    calls = {f"C{i}": {"uuid": f"u{i}", "lead": "L", "created_at": None, "pack": pack, "evaluation": verdict}
             for i in (1, 2, 3)}
    cfg = dict(DEFAULT_AGENT_SETTINGS)
    raw = {"patterns": [{"id": "P1", "title": "Long pitch", "category": "too_long", "calls": ["C1", "C2", "C3"],
                         "evidence": [{"call": c, "turn": "T3", "quote": PITCH_QUOTE} for c in ("C1", "C2", "C3")]},
                        {"id": "P2", "title": "One call", "category": "pushy", "calls": ["C1"],
                         "evidence": [{"call": "C1", "turn": "T3", "quote": PITCH_QUOTE}]},
                        {"id": "P3", "title": "Made up", "category": "pushy", "calls": ["C1", "C2"],
                         "evidence": [{"call": c, "turn": "T3", "quote": "never said"} for c in ("C1", "C2")]},
                        # real words, but nothing was proven wrong at that moment of the calls
                        {"id": "P4", "title": "Harmless line", "category": "weak_opening", "calls": ["C1", "C2"],
                         "evidence": [{"call": c, "turn": "T1", "quote": "दो minute बात कर सकते हैं"}
                                      for c in ("C1", "C2")]},
                        # a real problem at T3, but a different one: "the same problem" is what counts
                        {"id": "P6", "title": "Pushy pitch", "category": "pushy", "calls": ["C1", "C2"],
                         "evidence": [{"call": c, "turn": "T3", "quote": PITCH_QUOTE} for c in ("C1", "C2")]},
                        # proven by the measured slow replies, but technical: a team note, never a script change
                        {"id": "P5", "title": "Slow answers", "category": "slow_reply", "calls": ["C1", "C2"],
                         "evidence": [{"call": c, "turn": "T3", "quote": PITCH_QUOTE} for c in ("C1", "C2")]}],
           "suggestions": [
               {"pattern": "P5", "field": "learned_rules", "operation": "append", "value": "Answer faster."},
               {"pattern": "P1", "field": "opening_line", "operation": "replace", "value": "नमस्ते {name}!"},
               {"pattern": "P1", "field": "learned_rules", "operation": "append", "value": "One sentence pitch."},
               {"pattern": "P1", "field": "learned_rules", "operation": "append", "value": "Second change, same field."},
               {"pattern": "P2", "field": "closing_line", "operation": "replace", "value": "Bye"},
               {"pattern": "P3", "field": "closing_line", "operation": "replace", "value": "Bye"},
               {"pattern": "P4", "field": "opening_line", "operation": "replace", "value": "नमस्ते!"},
               {"pattern": "P1", "field": "voice", "operation": "replace", "value": "ishita"},
               {"pattern": "P1", "field": "silence_prompt_text", "operation": "replace",
                "value": cfg["silence_prompt_text"]},  # no change
               {"pattern": "P1", "field": "closing_line", "operation": "replace", "value": "Rejected before."},
           ]}
    rejected = [{"field": "closing_line", "proposed": "Rejected before."}]
    report, suggestions = review.validate_batch(raw, calls, cfg, 2, rejected)
    assert [p["id"] for p in report["patterns"]] == ["P1", "P5"]
    assert {w["title"] for w in report["watch_list"]} >= {"One call", "Made up", "Harmless line", "Pushy pitch"}
    assert report["team_notes"][0].startswith("Technical, not the script: Slow answers (proven in 2 calls)")
    assert [s["field"] for s in suggestions] == ["learned_rules"]
    assert suggestions[0]["proposed_value"] == "- One sentence pitch." and suggestions[0]["base_value"] == ""
    proof = suggestions[0]["evidence"][0]
    assert proof["quote"] == PITCH_QUOTE and proof["turn"] == "T3" and proof["finding"]  # what was wrong there
    removed = " ".join(report["proof"]["removed"])
    for reason in ("unknown placeholder", "may not change voice", "rejected", "wouldn't change anything",
                   "already changes learned_rules", "proven in enough calls", "technical, not the script"):
        assert reason in removed, reason
    assert proof["finding"] == "A list of every pouch instead of one line."  # the same problem, from that call


# ---------------------------------------------------------------- runs, approval, automatic reviews
def test_a_full_review_and_approval(fresh, monkeypatch):
    monkeypatch.setattr(review, "_ask_json", FakeModel())
    pitched = {_pitch_call() for _ in range(3)}
    browser = _make_call([("agent", "नमस्ते!"), ("user", "हाँ")], channel="browser")  # browser tests: never reviewed
    _make_call([("agent", "नमस्ते! कोई है?")])                                          # nobody spoke: not reviewed

    out = asyncio.run(_manual(3))  # the 3 newest reviewable calls: the two newer ones above don't qualify
    assert out["status"] == "done", out["error"]
    assert out["call_count"] == 3 and out["patterns"] == 1 and out["pending"] == 1
    detail = review.run_detail(out["id"])
    assert {c["call_uuid"] for c in detail["calls"]} == pitched
    first = detail["calls"][0]["evaluation"]
    assert first["loss_cause"] == "agent" and first["lost_at"] == "T3"
    assert all(i["quote"] != "this was never said on the call" for c in detail["calls"] for i in c["evaluation"]["issues"])
    assert detail["report"]["stats"]["lost_because_of_agent"] == 3
    assert detail["report"]["team_notes"] == ["Customers ask about delivery time; add a typical lead time."]
    assert {c["call_uuid"] for c in detail["report"]["calls"]} == pitched
    [s] = detail["suggestion_items"]
    assert s["field"] == "learned_rules" and s["status"] == "pending"
    assert {e["call_uuid"] for e in s["evidence"]} == pitched
    assert agent_config.load_agent_settings()["learned_rules"] == ""  # nothing applied yet
    # each call's own page shows the verdict on it
    shown = get_call_detail_dict(next(iter(pitched)))["review"]
    assert shown["run_id"] == out["id"] and shown["evaluation"]["lost_at"] == "T3" and isinstance(shown["facts"], list)
    assert get_call_detail_dict(browser)["review"] is None

    approved = review.approve(s["id"])
    assert approved["status"] == "approved" and approved["change_id"]
    cfg = agent_config.load_agent_settings()
    assert cfg["learned_rules"] == "- Keep the first pitch to one short sentence, then ask one question."
    assert "Keep the first pitch to one short sentence" in static_system_prompt(cfg, "outbound", "outbound")
    with SessionLocal() as db:
        change = db.get(SettingsChange, approved["change_id"])
        assert change.source == "review" and change.ref_id == s["id"] and "Shorter first pitch" in change.label
    with pytest.raises(review.ReviewError):
        review.approve(s["id"])  # only once
    # the approved change can be undone from the settings history like any other
    agent_config.undo_change(approved["change_id"])
    assert agent_config.load_agent_settings()["learned_rules"] == ""


def test_reject_supersede_edit_and_stale(fresh, monkeypatch):
    sugg = [{"pattern": "P1", "field": "learned_rules", "operation": "append", "value": "Rule A.",
             "title": "A", "why": "w", "expected_effect": "e"}]
    monkeypatch.setattr(review, "_ask_json", FakeModel(suggestions=sugg))
    for _ in range(2):
        _pitch_call()

    asyncio.run(_manual(2))
    [a] = review.list_suggestions("pending")
    rejected = review.reject(a["id"], "We prefer the current style.")
    assert rejected["status"] == "rejected" and rejected["decision_note"] == "We prefer the current style."
    assert agent_config.load_agent_settings()["learned_rules"] == ""

    # the same change again is filtered out next time (the team said no)
    assert asyncio.run(_manual(2))["suggestions"] == 0

    # a newer suggestion for the same setting replaces one still waiting
    sugg[0]["value"] = "Rule B."
    asyncio.run(_manual(2))
    [b] = review.list_suggestions("pending")
    sugg[0]["value"] = "Rule C."
    asyncio.run(_manual(2))
    [c] = review.list_suggestions("pending")
    assert c["proposed_value"] == "- Rule C." and review.list_suggestions("superseded")[0]["id"] == b["id"]

    # the setting was edited by hand meanwhile: approving needs a confirmation, and is still validated
    agent_config.save_agent_settings({"learned_rules": "- Written by hand."})
    with pytest.raises(review.ReviewError) as e:
        review.approve(c["id"])
    assert e.value.status == 409 and e.value.data == {"stale": True, "current": "- Written by hand."}
    with pytest.raises(review.ReviewError) as e:
        review.approve(c["id"], value="x" * 5000, force=True)  # same limits as the Settings page
    assert e.value.status == 422
    out = review.approve(c["id"], value="- Written by hand.\n- Rule C, reworded.", force=True)
    assert out["status"] == "approved" and out["decision_note"] == "Approved with edits"
    assert agent_config.load_agent_settings()["learned_rules"] == "- Written by hand.\n- Rule C, reworded."


def test_failed_model_marks_the_run_failed(fresh, monkeypatch):
    monkeypatch.setattr(review, "_ask_json", FakeModel(fail_calls=True))
    _pitch_call()
    out = asyncio.run(_manual(1))
    assert out["status"] == "failed" and "model unavailable" in out["error"]


def test_automatic_review_after_every_n_new_calls(fresh, monkeypatch):
    monkeypatch.setattr(review, "_ask_json", FakeModel())
    monkeypatch.setattr(review, "maybe_auto_run", REAL_AUTO)
    agent_config.save_agent_settings({"review_every_calls": 3})
    _pitch_call(created=utcnow() - timedelta(days=2))  # made before the review agent was installed
    _set_since(utcnow())                                 # (calls made by earlier tests are older too)
    _pitch_call()
    _pitch_call(direction="inbound")
    _make_call([("agent", "नमस्ते!"), ("user", "हाँ")], channel="browser")  # browser tests never count
    _make_call([("agent", "नमस्ते! कोई है?")])                                # the customer never spoke
    assert review.new_call_count(agent_config.load_agent_settings()) == 2

    assert asyncio.run(_auto()) is None  # 2 of 3: not yet
    _pitch_call()
    out = asyncio.run(_auto())
    assert out["trigger"] == "auto" and out["call_count"] == 3 and out["status"] == "done"
    assert review.new_call_count(agent_config.load_agent_settings()) == 0  # reviewed calls don't count again

    agent_config.save_agent_settings({"review_include_inbound": False})  # inbound switched off: not counted
    _pitch_call(direction="inbound")
    assert review.new_call_count(agent_config.load_agent_settings()) == 0

    agent_config.save_agent_settings({"review_enabled": False, "review_include_inbound": True})
    for _ in range(3):
        _pitch_call()
    assert asyncio.run(_auto()) is None  # switched off: no automatic review however many calls wait


def test_a_failed_review_pauses_automatic_reviews(fresh, monkeypatch):
    fake = FakeModel(fail_calls=True)
    monkeypatch.setattr(review, "_ask_json", fake)
    monkeypatch.setattr(review, "maybe_auto_run", REAL_AUTO)
    agent_config.save_agent_settings({"review_every_calls": 3})
    _set_since(utcnow())
    for _ in range(3):
        _pitch_call()

    first = asyncio.run(_auto())
    assert first["status"] == "failed" and first["trigger"] == "auto"
    assert review.auto_paused_until() is not None and review.status()["auto_paused_until"]
    assert asyncio.run(_auto()) is None  # the next call doesn't start another failing review
    # half an hour later it tries again (and now the model works)
    with SessionLocal() as db:
        db.execute(update(ReviewRun).values(finished_at=utcnow() - timedelta(minutes=31)))
        db.commit()
    fake.fail_calls = False
    assert review.auto_paused_until() is None
    second = asyncio.run(_auto())
    assert second["id"] != first["id"] and second["status"] == "done" and second["call_count"] == 3


def test_restart_marks_running_reviews_failed_without_pausing(fresh):
    with SessionLocal() as db:
        db.add(ReviewRun(trigger="auto", status="running", model="gpt-4o-mini", call_count=5))
        db.commit()
    assert review.recover_on_startup() == 1
    assert review.list_runs()[0]["status"] == "failed" and "restart" in review.list_runs()[0]["error"]
    assert review.auto_paused_until() is None  # not the model's fault: automatic reviews carry on


def test_model_parameters_and_retries(monkeypatch):
    seen: list[dict] = []
    replies: list[tuple[str, str]] = []

    class Completions:
        async def create(self, **kw):
            seen.append(kw)
            finish, content = replies.pop(0) if replies else ("stop", '{"ok": true}')
            return SimpleNamespace(usage=SimpleNamespace(total_tokens=42), choices=[
                SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))])

    class Client:
        chat = SimpleNamespace(completions=Completions())

        def with_options(self, **kw):
            return self

    monkeypatch.setattr(review, "client", lambda: Client())
    msgs = [{"role": "user", "content": "x"}]
    assert asyncio.run(REAL_ASK("gpt-5.5", msgs, 100)) == ({"ok": True}, 42)
    assert asyncio.run(REAL_ASK("gpt-4o-mini", msgs, 100)) == ({"ok": True}, 42)
    assert "temperature" not in seen[0] and seen[0]["max_completion_tokens"] == 400  # reasoning models think first
    assert seen[1]["temperature"] == 0 and seen[1]["max_completion_tokens"] == 100
    assert seen[0]["response_format"] == {"type": "json_object"}
    # an answer cut off by the length limit is asked again with twice the room
    seen.clear()
    replies[:] = [("length", '{"cut'), ("stop", '{"ok": 1}')]
    assert asyncio.run(REAL_ASK("gpt-4o-mini", msgs, 100)) == ({"ok": 1}, 84)
    assert [k["max_completion_tokens"] for k in seen] == [100, 200]
    # an answer that isn't JSON is asked once more, then the review fails with a clear message
    seen.clear()
    replies[:] = [("stop", "sorry"), ("stop", "still not json")]
    with pytest.raises(review.ReviewError) as e:
        asyncio.run(REAL_ASK("gpt-4o-mini", msgs, 100))
    assert e.value.status == 502 and "gpt-4o-mini" in str(e.value)
    assert seen[1]["messages"][-1]["content"].startswith("That was not a valid JSON object")


# ---------------------------------------------------------------- API
def test_review_api(fresh, monkeypatch):
    monkeypatch.setattr(review, "_ask_json", FakeModel())

    class Models:
        async def list(self):
            return SimpleNamespace(data=[SimpleNamespace(id="gpt-4o-mini"), SimpleNamespace(id="gpt-5.5")])

    monkeypatch.setattr(review_router, "client", lambda: SimpleNamespace(models=Models()))
    monkeypatch.setitem(review_router._models_cache, "ids", None)
    for _ in range(2):
        _pitch_call()
    with TestClient(app, client=LOCAL) as c:
        st = c.get("/api/review/status").json()
        assert st["enabled"] and st["every"] == 5 and st["model"] == "gpt-4o-mini" and st["pending_suggestions"] == 0
        assert st["auto_paused_until"] is None and st["running"] is None
        method = c.get("/api/review/method").json()
        assert len(method["steps"]) == 6 and "too_long" in method["categories"]
        assert any(f["key"] == "learned_rules" for f in method["fields"]) and "Proof rules" in method["rubric"]
        models = {m["id"]: m["available"] for m in c.get("/api/review/models").json()["models"]}
        assert models == {"gpt-4o-mini": True, "gpt-5.6-luna": False, "gpt-5.6-terra": False, "gpt-5.5": True,
                          "gpt-5.6-sol": False}
        assert c.post("/api/review/runs", json={"limit": "many"}).status_code == 422
        r = c.post("/api/review/runs", json={"limit": 2})
        assert r.status_code == 201, r.text
        run_id = r.json()["id"]
        end = time.monotonic() + 15
        while c.get(f"/api/review/runs/{run_id}").json()["status"] == "running" and time.monotonic() < end:
            time.sleep(0.05)
        detail = c.get(f"/api/review/runs/{run_id}").json()
        assert detail["status"] == "done" and len(detail["calls"]) == 2
        pending = c.get("/api/review/suggestions", params={"status": "pending"}).json()
        assert len(pending) == 1 and pending[0]["field_label"] == "Learned rules"
        assert c.post(f"/api/review/suggestions/{pending[0]['id']}/reject", json={"note": "no"}).status_code == 200
        assert c.post(f"/api/review/suggestions/{pending[0]['id']}/approve", json={}).status_code == 409
        assert c.post("/api/review/suggestions/999999/approve", json={}).status_code == 404
        assert c.get("/api/review/status").json()["pending_suggestions"] == 0
        assert c.get("/api/review/runs").json()[0]["id"] == run_id
        assert c.get("/api/review/runs/999999").status_code == 404
