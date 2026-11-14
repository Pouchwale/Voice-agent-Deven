"""Conversation brain for inbound calling: caller memory, direction / tone / language-aware prompts, the
inbound greeting and call length, never-heard inbound calls, and what the post-call analysis may change."""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db import SessionLocal, utcnow
from app.defaults import DEFAULT_AGENT_SETTINGS, LANGUAGE_MIXES, SPEAKING_TONES
from app.models import Call, Lead, TranscriptTurn
from app.schemas import lead_out
from app.services import analysis as analysis_mod
from app.services import session as session_mod
from app.services.analysis import analyze_call
from app.services.memory import MAX_CHARS, caller_memory
from app.services.prompts import (CALL_REASONS, LANGUAGE_STYLES, NOTES_LABEL, TONES, build_analysis_prompt, day_label,
                                  fill_template, greeting_for, lead_context, lead_name_ji, now_line, relative_age,
                                  static_system_prompt)
from app.services.session import CallSession

from .fakes import FakeSTT, FakeTransport, FakeTTSStream, ScriptedLLM, fake_tts_rest, tone
from .test_session import _cfg, _wait

# Fixed past "now" for memory/analysis rows: they never land in today's KPIs or other builders' date ranges.
NOW = datetime(2019, 6, 14, 10, 42, tzinfo=timezone.utc)  # Fri 14 Jun 2019, 4:12 pm IST
PAST = NOW - timedelta(days=1)
CFG = dict(DEFAULT_AGENT_SETTINGS)


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeSTT.instances.clear()
    FakeTTSStream.sentences.clear()
    monkeypatch.setattr(session_mod, "RealtimeSTT", FakeSTT)
    monkeypatch.setattr(session_mod, "TTSStream", FakeTTSStream)
    monkeypatch.setattr(session_mod, "tts_rest", fake_tts_rest)
    yield


# ---------------------------------------------------------------- helpers (BE-CONV numbers: +9194xxxxxxxx)
def _lead(phone: str, **fields) -> int:
    with SessionLocal() as db:
        lead = Lead(phone=phone, name=fields.pop("name", "Rahul Sharma"), **fields)
        db.add(lead)
        db.commit()
        return lead.id


def _call(lead_id: int | None, at: datetime, *, direction: str = "outbound", status: str = "completed",
          words: int = 0, analysis: str = "done", duration: float | None = None, channel: str = "phone",
          end_reason: str | None = "user_hangup", metrics: dict | None = None, turns=(),
          ended_at: datetime | None = None, **fields) -> str:
    call_uuid = f"cm-{uuid4().hex[:12]}"
    m = {"user_words": words, "agent_audio_ms": 3000 if status == "completed" else 0, **(metrics or {})}
    with SessionLocal() as db:
        c = Call(uuid=call_uuid, lead_id=lead_id, channel=channel, direction=direction, status=status,
                 end_reason=end_reason, started_at=at, created_at=at,
                 answered_at=at if status == "completed" else None,
                 ended_at=ended_at or at + timedelta(seconds=duration or 20), duration_sec=duration,
                 analysis_status=analysis, metrics=m, **fields)
        db.add(c)
        db.flush()
        for i, (role, text) in enumerate(turns):
            db.add(TranscriptTurn(call_id=c.id, role=role, text=text, offset_ms=i * 1000))
        db.commit()
    return call_uuid


def _get_call(call_uuid: str) -> Call:
    with SessionLocal() as db:
        return db.scalar(select(Call).where(Call.uuid == call_uuid))


def _get_lead(lead_id: int) -> Lead:
    with SessionLocal() as db:
        return db.get(Lead, lead_id)


def _agent_turns(call_uuid: str) -> list[str]:
    with SessionLocal() as db:
        cid = db.scalar(select(Call.id).where(Call.uuid == call_uuid))
        return [t.text for t in db.scalars(select(TranscriptTurn).where(TranscriptTurn.call_id == cid,
                                                                         TranscriptTurn.role == "agent")
                                           .order_by(TranscriptTurn.id))]


async def _session(phone: str, cfg: dict, llm: ScriptedLLM, monkeypatch, *, direction: str = "inbound",
                   purpose: str | None = None, memory: str = "", greeting_text: str | None = None,
                   greeting_pcm: bytes | None = None, channel: str = "browser") -> tuple[CallSession, FakeTransport]:
    """A live session. Browser channel by default (the "Customer calls us" rehearsal: never counts in KPIs);
    phone rows are dated in the past so they never land in today's numbers either."""
    monkeypatch.setattr(session_mod, "stream_reply", llm)
    lead_id = _lead(phone)
    call_uuid = f"cm-{uuid4().hex[:12]}"
    at = utcnow() if channel == "browser" else PAST
    with SessionLocal() as db:
        call = Call(uuid=call_uuid, lead_id=lead_id, channel=channel, direction=direction, status="in_progress",
                    started_at=at, created_at=at, answered_at=at)
        db.add(call)
        db.commit()
        call_id, lead = call.id, lead_out(db.get(Lead, lead_id))
    t = FakeTransport()
    t.channel = channel
    s = CallSession(call_id=call_id, call_uuid=call_uuid, lead=lead, cfg=cfg, transport=t, direction=direction,
                    purpose=purpose, memory=memory, greeting_text=greeting_text, greeting_pcm=greeting_pcm)
    return s, t


def _fake_analysis(monkeypatch, result: dict, seen: list | None = None) -> None:
    async def fake_json(messages, max_tokens=None):
        if seen is not None:
            seen.append(messages)
        return dict(result)

    monkeypatch.setattr(analysis_mod, "complete_json", fake_json)


TALK = (("agent", "नमस्ते जी! PouchWale से बोल रही हूँ, बताइए?"), ("user", "मुझे rice के लिए pouches चाहिए"))


# ---------------------------------------------------------------- memory
def test_caller_memory_content():
    phone = "+919400000001"
    lead_id = _lead(phone, company="Sharma Snacks", notes="Prefers WhatsApp")
    newest = _call(lead_id, NOW - timedelta(days=3), words=40, duration=151, interest="hot", rating=8,
                   summary="Rice brand needs 5000 stand-up pouches a month.",
                   next_action="Send a quote for 5000 zipper pouches", callback_time="Monday morning",
                   metrics={"promised_to_customer": "Expert will call with a quote"},
                   recording_url="https://rec.example/abc.mp3")
    oldest = _call(lead_id, NOW - timedelta(days=70), direction="inbound", words=15, duration=65,
                   summary="Asked about spout pouches for juice.")
    orphan = _call(None, NOW - timedelta(days=1), direction="inbound", words=12, duration=80, from_number=phone,
                   summary="Called back about the sample kit.")
    excluded = [
        _call(lead_id, NOW - timedelta(days=2), channel="browser", words=9, summary="BROWSER ROLEPLAY"),
        _call(lead_id, NOW - timedelta(days=2), status="canceled", end_reason="manual_hangup", summary="CANCELED ROW"),
        _call(lead_id, NOW - timedelta(days=2), status="no_answer", end_reason="superseded", summary="SUPERSEDED ROW"),
        _call(None, NOW - timedelta(days=2), direction="outbound", words=5, to_number=phone, summary="OUTBOUND ORPHAN"),
        _call(None, NOW - timedelta(days=2), direction="inbound", words=5, from_number="+919400000999",
              summary="OTHER NUMBER"),
    ]
    current = _call(lead_id, NOW - timedelta(minutes=1), direction="inbound", words=3, summary="CURRENT CALL")

    mem = caller_memory(lead_id, current, direction="outbound", now=NOW)
    assert mem.startswith("# What we already know about this customer\n")
    assert "Customer since Fri 5 Apr 2019 (2 months ago)." in mem
    assert ("- 3 days ago (Tue 11 Jun), we called them, talked 2:31: Rice brand needs 5000 stand-up pouches a "
            "month. Our team planned: Send a quote for 5000 zipper pouches. We promised them: Expert will call "
            "with a quote. Callback asked (on Tue 11 Jun): Monday morning.") in mem
    assert "- yesterday (Thu 13 Jun), they called us, talked 1:20: Called back about the sample kit." in mem
    assert "- 2 months ago (Fri 5 Apr), they called us, talked 1:05: Asked about spout pouches for juice." in mem
    assert mem.index("- yesterday") < mem.index("- 3 days ago") < mem.index("- 2 months ago")  # newest first
    for word in ("BROWSER ROLEPLAY", "CANCELED ROW", "SUPERSEDED ROW", "OUTBOUND ORPHAN", "OTHER NUMBER",
                 "CURRENT CALL", "https://", "8/10", "rating", "interest", *excluded, newest, oldest, orphan, current):
        assert word not in mem, word
    assert not re.search(r"\b(hot|warm|cold)\b", mem.lower())
    assert caller_memory(lead_id, current, direction="outbound", now=NOW) == mem  # deterministic

    # a lead created by the current call, with nothing else: nothing to remember
    fresh = _lead("+919400000002", name="")
    first = _call(fresh, NOW, direction="inbound", words=4)
    assert caller_memory(fresh, first, direction="inbound", now=NOW) == ""
    assert caller_memory(None, None, from_number="", direction="inbound", now=NOW) == ""
    assert caller_memory(None, None, from_number="12345", direction="inbound", now=NOW) == ""  # hidden: never linked

    # an international caller without a lead is remembered by number, with what they told us
    intl = "+447940000001"
    _call(None, NOW - timedelta(days=4), direction="inbound", words=20, duration=90, from_number=intl,
          summary="Exporter asking for spice pouches.",
          metrics={"customer_name": "John Smith", "customer_city": "Dubai"})
    remembered = caller_memory(None, None, from_number=intl, direction="inbound", now=NOW)
    assert "Exporter asking for spice pouches." in remembered
    assert "Earlier they told us their name John Smith, city Dubai." in remembered

    # many conversations: <= MAX_CHARS, oldest dropped first, the newest 3 analysed always kept
    busy = _lead("+919400000003")
    for i in range(12):
        _call(busy, NOW - timedelta(days=30 - i), words=30, duration=100,
              summary=f"S{i:02d} needs pouches. " + "More detail about the requirement. " * 6)
    long_mem = caller_memory(busy, None, now=NOW)
    assert len(long_mem) <= MAX_CHARS
    assert all(f"S{i:02d}" in long_mem for i in (9, 10, 11))
    assert "S00" not in long_mem and "Customer since" in long_mem


def test_memory_collapses_attempts_and_merges_answers():
    lead_id = _lead("+919400000011")
    _call(lead_id, NOW - timedelta(days=6), words=20, duration=120, summary="Rice brand, uses plain bags now.",
          qualification=[{"question": "Product", "answer": "Rice", "status": "answered"},
                         {"question": "Quantity", "answer": "2000 pieces", "status": "answered"},
                         {"question": "Timeline", "answer": None, "status": "not_asked"}],
          key_points=["Rice brand", "Uses plain bags", *(f"F{i}" for i in range(1, 9))], objections=["Price"])
    _call(lead_id, NOW - timedelta(days=5), status="no_answer", end_reason="no_answer", analysis="skipped")
    _call(lead_id, NOW - timedelta(days=4), words=0, summary="Picked up and disconnected without talking.",
          interest="cold", rating=1, next_action="Try again at a different time of day, or move on.")
    _call(lead_id, NOW - timedelta(days=3), direction="inbound", words=30, duration=95,
          summary="Wants zipper pouches for rice.",
          qualification=[{"question": "quantity?", "answer": "5000 per month", "status": "answered"},
                         {"question": "Pouch type", "answer": "Zipper", "status": "partial"}],
          key_points=["rice brand", "Wants zipper pouches"],
          objections=["price", "MOQ worry", "Delivery time", "Payment terms", "Design cost", "Sample cost"])
    _call(lead_id, NOW - timedelta(days=2), direction="inbound", status="no_answer", end_reason="inbound_off",
          analysis="skipped", summary="Missed inbound call — inbound calling was switched off")
    _call(lead_id, NOW - timedelta(days=1), status="busy", end_reason="busy", analysis="skipped")
    _call(lead_id, NOW - timedelta(hours=2), words=6, duration=30, summary="Was busy, asked us to call later.")

    mem = caller_memory(lead_id, None, now=NOW)
    assert ("We also tried calling 3 times without reaching them (latest yesterday); they called once when we "
            "couldn't pick up (latest 2 days ago).") in mem
    for leak in ("Picked up and disconnected", "disconnected", "cut the call", "switched off", "inbound_off",
                 "Try again at a different time", "Missed inbound"):
        assert leak not in mem, leak
    # the newest answer per question wins; answers survive a newest call that had none
    assert "Known answers: quantity: 5000 per month; Pouch type: Zipper (partly); Product: Rice." in mem
    assert "2000 pieces" not in mem
    facts = next(line for line in mem.splitlines() if line.startswith("Key facts: "))
    items = facts[len("Key facts: "):].rstrip(".").split("; ")
    assert len(items) == 8 and [i.casefold() for i in items].count("rice brand") == 1 and "F6" not in items
    concerns = next(line for line in mem.splitlines() if line.startswith("Concerns they raised before"))
    assert concerns.lower().count("price") == 1  # "price" and "Price" are one concern
    assert "Design cost" in concerns and "Sample cost" not in concerns  # at most 5


def test_memory_excludes_notes_on_inbound():
    lead_id = _lead("+919400000021", notes="Call after Diwali. GSTIN 24ABCDE1234F1Z5, mail rahul@example.com",
                    extra={"GST": "24ABC", "Email": "x@y.com", "Monthly qty": "5000", "caller_id_name": "RAHUL S",
                           "Delivery Address": "12 MG Road", "PAN": "ABCDE1234F"})
    _call(lead_id, NOW - timedelta(days=3), words=25, duration=60, summary="Interested in rice pouches.")
    outbound = caller_memory(lead_id, None, direction="outbound", now=NOW)
    inbound = caller_memory(lead_id, None, direction="inbound", now=NOW)
    assert f"{NOTES_LABEL} Call after Diwali." in outbound and "Monthly qty: 5000" in outbound
    for secret in ("24ABCDE1234F1Z5", "rahul@example.com", "24ABC", "x@y.com", "RAHUL S", "MG Road", "ABCDE1234F"):
        assert secret not in outbound, secret
    assert "Interested in rice pouches." in inbound
    for internal in (NOTES_LABEL, "Diwali", "Monthly qty", "RAHUL S"):
        assert internal not in inbound, internal

    lead = lead_out(_get_lead(lead_id))
    assert "Diwali" not in lead_context(lead, inbound, direction="inbound")
    assert "Diwali" not in lead_context(lead, "", direction="inbound")
    assert lead_context(lead, outbound).count("Call after Diwali") == 1  # notes once, not twice
    fallback = lead_context(lead, "")  # memory not ready: the notes still reach an outbound call
    assert f"{NOTES_LABEL} Call after Diwali." in fallback and "24ABCDE1234F1Z5" not in fallback
    # a first outbound call with team notes: the memory carries just the notes
    noted = _lead("+919400000022", notes="Owner is Mr Shah")
    assert caller_memory(noted, None, now=NOW).endswith(f"{NOTES_LABEL} Owner is Mr Shah")
    assert caller_memory(noted, None, direction="inbound", now=NOW) == ""


def test_memory_just_dropped_call():
    turns = [("agent" if i % 2 else "user", f"line {i:02d}") for i in range(1, 11)]
    lead_id = _lead("+919400000031")
    _call(lead_id, NOW - timedelta(days=2), words=20, duration=60, summary="Discussed rice pouches.")
    _call(lead_id, NOW - timedelta(minutes=7), direction="inbound", words=40, duration=120, analysis="pending",
          ended_at=NOW - timedelta(minutes=5), turns=turns)
    _call(lead_id, NOW - timedelta(minutes=2), direction="inbound", status="no_answer", analysis="skipped",
          end_reason="lines_busy")  # a missed redial in between
    mem = caller_memory(lead_id, None, direction="inbound", now=NOW)
    block = mem[mem.index("The call that just dropped (continue from here):"):]
    assert "AGENT: line 03" in block and "CUSTOMER: line 10" in block
    assert "line 02" not in mem and "line 01" not in mem  # the last 8 turns only
    assert block.index("line 03") < block.index("line 10")
    assert "Discussed rice pouches." in mem and "7 minutes ago" not in mem  # no second line for that call

    for phone, ended, status, expect in (("+919400000041", 20, "pending", False), ("+919400000042", 5, "done", False),
                                         ("+919400000043", 5, "failed", True)):
        other = _lead(phone)
        _call(other, NOW - timedelta(minutes=ended + 2), words=10, duration=100, analysis=status,
              ended_at=NOW - timedelta(minutes=ended), turns=turns, summary="Talked about spout pouches.")
        got = caller_memory(other, None, now=NOW)
        assert ("The call that just dropped" in got) is expect, (ended, status)
        if status == "done":
            assert "Talked about spout pouches." in got


# ---------------------------------------------------------------- prompts
HUMANISING_RULES = (
    "ALWAYS use feminine Hindi forms", "never masculine ones", "English nouns take Hindi gender",
    "SCRIPT RULE (critical", "NEVER write Hindi in Roman letters", "React first, then talk.",
    'NEVER say "ओह", "oh", "OK", "ओके", "okay", "got it", "yeah"', "Show you listened",
    "Keep every reply SHORT: at most TWO short sentences", "Ask only ONE question per reply",
    "Never repeat your own earlier sentence", "Put the most important words first",
    "Everyday spoken words, not formal or bookish Hindi", "Never use call-centre phrases",
    'Be respectful: "आप" and "जी" always', "If you could not understand them",
    "Punctuation shapes how your voice sounds", '"!" only for real warmth (at most one per reply)',
    "Numbers: write them as spoken words", "Never use lists, bullet points, emojis",
    "LANGUAGE RULE: answer in the language of the customer's LAST message", "reply fully in simple, warm Indian English",
    "Never invent prices", "say honestly that you are", "# Remembering the customer", "[END_CALL]",
)


def test_prompt_keeps_every_humanising_rule():
    # every choice the Settings page can save has its own wording
    assert set(TONES) == set(SPEAKING_TONES) and set(LANGUAGE_STYLES) == set(LANGUAGE_MIXES)
    for direction, purpose in (("outbound", None), ("inbound", "inbound"), ("outbound", "callback")):
        for speaking_tone in TONES:
            for mix in LANGUAGE_STYLES:
                cfg = {**CFG, "speaking_tone": speaking_tone, "language_mix": mix}
                sp = static_system_prompt(cfg, direction, purpose)
                for rule in HUMANISING_RULES:
                    assert rule in sp, (direction, purpose, speaking_tone, mix, rule)
                assert '"ओह, got it"' not in sp and "Now:" not in sp and "{" not in sp
                assert sp == static_system_prompt(cfg, direction, purpose)  # deterministic -> prompt cache


def test_prompt_inbound_has_no_outbound_phrases():
    inbound = static_system_prompt(CFG, "inbound", "inbound")
    for phrase in ("You called them", "calling a business owner", "say why you called", "disturb",
                   "Outbound call to a business lead"):
        assert phrase not in inbound, phrase
    assert "ANSWERING an INBOUND PHONE CALL in India: the customer called PouchWale's number and you picked up." in inbound
    assert "answering a business owner who called PouchWale" in inbound
    assert "Thank them for calling once, briefly, then help with what they asked" in inbound
    assert "offer to call them back at a time that suits them" in inbound
    assert 'first ask once "और कुछ जानना हो तो बताइए?"' in inbound
    assert '"Call करने के लिए thank you जी, आपका दिन अच्छा रहे! [END_CALL]"' in inbound
    assert fill_template(CFG["inbound_purpose"], CFG, None) in inbound
    assert "PRIVACY" in inbound and "only after the caller brings up the topic or confirms who they are" in inbound
    assert static_system_prompt(CFG, "inbound") == inbound  # purpose defaults to the direction
    assert static_system_prompt({**CFG, "inbound_purpose": "  "}, "inbound") == inbound  # blank -> the shipped flow

    outbound = static_system_prompt(CFG)
    assert "on a LIVE OUTBOUND PHONE CALL in India. You called them; they did not call you." in outbound
    assert "say why you called" in outbound and "disturb करने के लिए sorry" in outbound and "PRIVACY" not in outbound
    assert CFG["call_purpose"] in outbound
    assert static_system_prompt(CFG, "outbound", "outbound") == outbound
    # memory rules for every direction
    for sp in (inbound, outbound):
        assert "Confirm instead of re-asking" in sp and "don't introduce PouchWale again" in sp
        assert 'If the memory shows "The call that just dropped"' in sp
        assert "Recordings: don't bring them up, but if they ask whether the call is recorded, answer honestly." in sp
        assert "between 09:30 and 19:30 IST" in sp and '"आपका नाम जान सकती हूँ?"' in sp
        assert "hidden or international, also ask for a WhatsApp or phone number" in sp


def test_prompt_tone_and_language_mix():
    keys = set()
    for speaking_tone, (adjectives, sound) in TONES.items():
        sp = static_system_prompt({**CFG, "speaking_tone": speaking_tone})
        assert sound in sp and f"You are a {adjectives} tele-calling sales executive from PouchWale" in sp
        assert not any(other in sp for t, (_, other) in TONES.items() if t != speaking_tone)
        keys.add(hashlib.sha1(sp.encode()).hexdigest())
    assert len(keys) == len(TONES)  # each tone is its own cached prompt
    assert "one-'!'-per-reply rule still applies" in TONES["energetic"][1]
    assert static_system_prompt({**CFG, "speaking_tone": "shouty"}) == static_system_prompt(CFG)  # unknown -> warm

    hinglish = static_system_prompt(CFG)
    assert "- Speak everyday Hinglish the way urban Indians talk" in hinglish
    assert "If it is Hindi or Hinglish, reply in Hinglish." in hinglish
    hindi = static_system_prompt({**CFG, "language_mix": "hindi"})
    assert "- Speak mostly simple, everyday Hindi" in hindi and "Speak everyday Hinglish" not in hindi
    assert ("If it is Hindi or Hinglish, reply in mostly simple Hindi, English only for words people always say "
            "in English (WhatsApp, order, sample, pouch).") in hindi
    english = static_system_prompt({**CFG, "language_mix": "english"})
    assert "- Speak simple, warm Indian English" in english
    assert "If it is Hindi or Hinglish, reply in simple Indian English unless they clearly prefer Hindi" in english
    for sp in (hinglish, hindi, english):  # an English turn always gets English, whatever the mix
        assert 'If it is in English (e.g. "Yes, tell me. What do you do?"), reply fully in simple, warm Indian English' in sp
    male = static_system_prompt({**CFG, "agent_gender": "male", "speaking_tone": "calm"}, "inbound")
    assert "young Indian man" in male and '"आपका नाम जान सकता हूँ?"' in male and TONES["calm"][1] in male


def test_prompt_callback_purpose():
    cb = static_system_prompt(CFG, "outbound", "callback")
    assert "you are calling back a customer who called PouchWale earlier and couldn't be answered" in cb
    assert "calling back a business owner who called PouchWale earlier" in cb and "No cold pitch" in cb
    assert fill_template(CFG["inbound_purpose"], CFG, None) in cb
    for phrase in ("Outbound call to a business lead", "say why you called", "You called them; they did not call you"):
        assert phrase not in cb, phrase
    assert cb not in (static_system_prompt(CFG), static_system_prompt(CFG, "inbound", "inbound"))
    lead = {"name": "Mr. Rahul Sharma"}
    assert greeting_for(CFG, lead, purpose="callback").startswith("नमस्ते Rahul जी! PouchWale से बोल रही हूँ।")
    assert "आपने हमें call किया था" in greeting_for(CFG, lead, purpose="callback")
    assert greeting_for(CFG, lead, direction="inbound") == "नमस्ते Rahul जी! PouchWale से बोल रही हूँ, बताइए?"
    assert "फिर बात करके" in greeting_for(CFG, lead, direction="inbound", returning=True)
    assert greeting_for(CFG, None, direction="inbound") == "नमस्ते जी! PouchWale से बोल रही हूँ, बताइए?"
    assert greeting_for(CFG, lead) == fill_template(CFG["opening_line"], CFG, lead)


def test_lead_context_has_now_and_memory():
    now = datetime(2026, 11, 13, 10, 42, tzinfo=timezone.utc)
    lead = {"name": "Rahul Sharma", "company": "Sharma Snacks", "city": "Surat", "phone": "+919400000101",
            "notes": "VIP customer", "interest": "hot", "rating": 9, "email": "rahul@example.com",
            "last_summary": "Old summary text", "source": "IndiaMART"}
    memory = "# What we already know about this customer\n- yesterday (Thu 12 Nov), they called us: Needs pouches."
    ctx = lead_context(lead, memory, direction="inbound", now=now)
    lines = ctx.splitlines()
    assert lines[0] == "Now: Fri 13 Nov 2026, 4:12 pm IST"
    assert lines[2:7] == ["# Customer on this call", "Name: Rahul Sharma", "Company: Sharma Snacks", "City: Surat",
                          "Number: +919400000101"]
    assert ctx.endswith(memory) and ctx.index("# Customer on this call") < ctx.index(memory)
    for private in ("VIP customer", "hot", "9", "rahul@example.com", "Old summary text", "IndiaMART"):
        assert private not in ctx.replace("+919400000101", ""), private
    assert f"{NOTES_LABEL} VIP customer" in lead_context(lead, "", now=now)  # our own call: notes, labelled
    hidden = lead_context({"phone": ""}, direction="inbound", now=now)
    assert "Name: not known yet" in hidden and "Number: hidden (ask for a WhatsApp or phone number)" in hidden
    assert "(international" in lead_context({"phone": "+14155550100"}, direction="inbound", now=now)
    assert "Number:" not in lead_context({"name": "Test User", "company": "Demo Co", "city": ""}, now=now)
    # the static prompt never carries per-call data; the full prompt puts it after
    full = static_system_prompt(CFG, "inbound") + "\n" + ctx
    assert full.index("# Ending the call") < full.index("Now: ")

    assert now_line(datetime(2026, 11, 12, 18, 35, tzinfo=timezone.utc)) == "Now: Fri 13 Nov 2026, 12:05 am IST"
    assert now_line(datetime(2026, 11, 13, 6, 30, tzinfo=timezone.utc)) == "Now: Fri 13 Nov 2026, 12:00 pm IST"
    ages = {timedelta(seconds=20): "just now", timedelta(seconds=100): "a minute ago",
            timedelta(minutes=25): "25 minutes ago", timedelta(hours=1, minutes=5): "an hour ago",
            timedelta(hours=3): "3 hours ago", timedelta(hours=7): "earlier today", timedelta(days=1): "yesterday",
            timedelta(days=3): "3 days ago", timedelta(days=20): "2 weeks ago", timedelta(days=75): "2 months ago",
            timedelta(days=400): "over a year ago", timedelta(days=800): "over 2 years ago"}
    for delta, want in ages.items():
        assert relative_age(now - delta, now) == want, delta
    assert day_label(now - timedelta(days=3), now) == "Tue 10 Nov"
    assert day_label(now - timedelta(days=60), now, year=True) == "Mon 14 Sep 2026"
    assert day_label(datetime(2025, 12, 30, 12, tzinfo=timezone.utc), now) == "Tue 30 Dec 2025"


def test_lead_name_ji_strips_honorifics():
    cases = {
        "Rahul Sharma": "Rahul जी", "Mr. Ramesh Patel": "Ramesh जी", "Mr.Ramesh": "Ramesh जी", "mrs sunita": "Sunita जी",
        "Dr Anil Mehta": "Anil जी", "Smt. Sunita Devi": "Sunita जी", "Shri Mahesh": "Mahesh जी", "Sri Ram": "Ram जी",
        "Ms. Priya": "Priya जी", "Miss Neha": "Neha जी", "Kumari Pooja": "Pooja जी", "R. K. Sharma": "Sharma जी",
        "RAHUL SHARMA": "Rahul जी", "श्री राहुल शर्मा": "राहुल जी", "राहुल": "राहुल जी", "Rahul ji": "Rahul जी",
        "M/s Ramesh": "Ramesh जी",
        "M/s Shree Traders": "जी", "Patel Foods Pvt. Ltd.": "जी", "Sharma Enterprises": "जी", "ABC Industries": "जी",
        "Gujarat Packaging Co": "जी", "Shah & Sons": "जी", "Mehta Company": "जी", "XYZ LLP": "जी",
        "9876543210": "जी", "+91 98765": "जी", "": "जी", None: "जी", "Mr.": "जी", "  ": "जी",
    }
    for name, want in cases.items():
        assert lead_name_ji(name) == want, name
    assert fill_template("नमस्ते {lead_name_ji}!", CFG, {"name": "Mr. Ramesh Patel"}) == "नमस्ते Ramesh जी!"


# ---------------------------------------------------------------- live session (inbound)
def test_inbound_greeting_no_hello_wait(monkeypatch):
    greeting = tone(200, 24000)

    async def first_audio_delay(s: CallSession, t: FakeTransport, *, talking_for: float = 0.0) -> float:
        if talking_for:
            s.user_speaking = True  # the caller is already talking when we pick up
        t0 = time.monotonic()
        run = asyncio.create_task(s.run())
        if talking_for:
            await asyncio.sleep(talking_for)
            assert not t.audio  # never talks over a caller who is speaking
            s.user_speaking = False
        await _wait(lambda: len(t.audio) > 0, timeout=5, step=0.005)
        delay = time.monotonic() - t0
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        return delay

    async def scenario():
        cfg = _cfg(silence_timeout_sec=30)
        text = "नमस्ते जी! PouchWale से बोल रही हूँ, बताइए?"
        s1, t1 = await _session("+919400000201", cfg, ScriptedLLM([]), monkeypatch, greeting_text=text,
                                greeting_pcm=greeting)
        inbound = await first_audio_delay(s1, t1)
        s2, t2 = await _session("+919400000202", cfg, ScriptedLLM([]), monkeypatch, direction="outbound",
                                greeting_pcm=greeting)
        outbound = await first_audio_delay(s2, t2)
        s3, t3 = await _session("+919400000203", cfg, ScriptedLLM([]), monkeypatch, greeting_text=text,
                                greeting_pcm=greeting)
        talking = await first_audio_delay(s3, t3, talking_for=0.6)
        return inbound, outbound, talking, s1

    inbound, outbound, talking, s1 = asyncio.run(scenario())
    assert inbound < 0.7, inbound            # inbound: greet almost at once (0.3 s window)
    assert outbound >= 0.85, outbound        # outbound unchanged: room for the customer's "Hello?"
    assert talking >= 0.6, talking           # ...but never over a caller who is already speaking
    assert _agent_turns(s1.uuid)[0] == "नमस्ते जी! PouchWale से बोल रही हूँ, बताइए?"


def test_inbound_session_prompt_and_memory_reach_the_llm(monkeypatch):
    memory = "# What we already know about this customer\n- yesterday (Thu 13 Jun), they called us: Needs spout pouches."
    greeting = "नमस्ते Rahul जी! PouchWale से बोल रही हूँ, आपसे फिर बात करके अच्छा लगा। बताइए?"

    async def scenario():
        cfg = _cfg(silence_timeout_sec=30)
        llm = ScriptedLLM(["जी, spout pouch का exact rate हमारे expert बताएँगे।"])
        s, t = await _session("+919400000211", cfg, llm, monkeypatch, purpose="inbound", memory=memory,
                              greeting_text=greeting, greeting_pcm=tone(200, 24000))
        run = asyncio.create_task(s.run())
        await _wait(lambda: s.greeted and s.state == "listening")
        await FakeSTT.instances[-1].say("मुझे spout pouch का rate चाहिए")
        await _wait(lambda: len(llm.calls) == 1)
        await s.end("manual_hangup")
        await asyncio.wait_for(run, timeout=5)
        twin, _ = await _session("+919400000212", cfg, ScriptedLLM([]), monkeypatch)
        outbound, _ = await _session("+919400000213", cfg, ScriptedLLM([]), monkeypatch, direction="outbound")
        callback, _ = await _session("+919400000214", cfg, ScriptedLLM([]), monkeypatch, direction="outbound",
                                     purpose="callback")
        for never_run in (twin, outbound, callback):
            await never_run.end("manual_hangup")  # never leave a live row behind for later tests
        return cfg, s, llm, twin, outbound, callback

    cfg, s, llm, twin, outbound, callback = asyncio.run(scenario())
    system, lead_msg, first_turn = llm.calls[0][:3]
    assert system["content"] == static_system_prompt(cfg, "inbound", "inbound")
    assert lead_msg["content"].startswith("Now: ") and lead_msg["content"].endswith(memory)
    assert "Name: Rahul Sharma" in lead_msg["content"]
    assert first_turn == {"role": "assistant", "content": greeting}
    # same settings + direction + purpose = same cached prompt; the per-call part never changes the key
    assert llm.cache_keys[0] == s.cache_key == twin.cache_key
    assert len({s.cache_key, outbound.cache_key, callback.cache_key}) == 3
    # defensive defaults when no greeting is passed in
    assert outbound.greeting_text.startswith("नमस्ते Rahul जी! मैं PouchWale से बोल रही हूँ।")
    assert "आपने हमें call किया था" in callback.greeting_text
    assert twin.greeting_text == "नमस्ते Rahul जी! PouchWale से बोल रही हूँ, बताइए?"


def test_inbound_max_length_uses_inbound_closing(monkeypatch):
    async def scenario():
        inbound_cfg = _cfg(silence_timeout_sec=30, inbound_max_call_sec=1, max_call_sec=300)
        outbound_cfg = _cfg(silence_timeout_sec=30, inbound_max_call_sec=600, max_call_sec=1)
        s1, _ = await _session("+919400000221", inbound_cfg, ScriptedLLM([]), monkeypatch, greeting_text="नमस्ते जी!")
        s2, _ = await _session("+919400000222", outbound_cfg, ScriptedLLM([]), monkeypatch, direction="outbound")
        t0 = time.monotonic()
        await asyncio.wait_for(asyncio.gather(s1.run(), s2.run()), timeout=8)
        return s1, s2, time.monotonic() - t0

    s1, s2, elapsed = asyncio.run(scenario())
    assert s1.end_reason == s2.end_reason == "max_duration" and elapsed < 5
    inbound_closing = fill_template(DEFAULT_AGENT_SETTINGS["inbound_closing_line"], CFG, s1.lead)
    assert _agent_turns(s1.uuid)[-1] == inbound_closing and "Call करने के लिए thank you" in inbound_closing
    assert _agent_turns(s2.uuid)[-1] == DEFAULT_AGENT_SETTINGS["closing_line"]


def test_inbound_session_never_heard_is_failed(monkeypatch):
    async def broken_tts(text, cfg, sample_rate):
        raise RuntimeError("Sarvam TTS 503")

    async def hang_up_before_greeting(direction: str, phone: str, channel: str = "phone",
                                      reason: str = "user_hangup") -> CallSession:
        s, t = await _session(phone, _cfg(), ScriptedLLM([]), monkeypatch, direction=direction,
                              greeting_text="नमस्ते जी!", greeting_pcm=tone(200, 24000), channel=channel)
        run = asyncio.create_task(s.run())
        await asyncio.sleep(0.05)
        assert not t.audio
        await s.end(reason)
        await asyncio.wait_for(run, timeout=5)
        return s

    async def scenario():
        hung_up = await hang_up_before_greeting("inbound", "+919400000231")
        outbound = await hang_up_before_greeting("outbound", "+919400000232")
        rehearsal = await hang_up_before_greeting("inbound", "+919400000235", "browser", "manual_hangup")
        heard, t = await _session("+919400000233", _cfg(), ScriptedLLM([]), monkeypatch, greeting_text="नमस्ते जी!",
                                  greeting_pcm=tone(200, 24000), channel="phone")
        run = asyncio.create_task(heard.run())
        await _wait(lambda: len(t.audio) > 0)
        await heard.end("user_hangup")
        await asyncio.wait_for(run, timeout=5)
        monkeypatch.setattr(session_mod, "tts_rest", broken_tts)
        tts_down, _ = await _session("+919400000234", _cfg(), ScriptedLLM([]), monkeypatch, greeting_text="नमस्ते जी!",
                                     channel="phone")
        await asyncio.wait_for(tts_down.run(), timeout=5)
        for s in (hung_up, tts_down, rehearsal):
            await analyze_call(s.uuid)
        return hung_up, outbound, rehearsal, heard, tts_down

    hung_up, outbound, rehearsal, heard, tts_down = asyncio.run(scenario())
    c = _get_call(hung_up.uuid)
    assert (c.status, c.end_reason, c.metrics["agent_audio_ms"]) == ("failed", "error", 0)
    assert c.metrics["session_end_reason"] == "user_hangup"
    assert (c.analysis_status, c.summary) == ("skipped", "Caller hung up before the agent answered")
    c = _get_call(tts_down.uuid)
    assert (c.status, c.end_reason, tts_down.end_reason) == ("failed", "error", "error")
    assert (c.analysis_status, c.summary) == ("skipped", "Call failed on our side — call back")
    assert (_get_call(heard.uuid).status, _get_call(heard.uuid).end_reason) == ("completed", "user_hangup")
    assert (_get_call(outbound.uuid).status, _get_call(outbound.uuid).end_reason) == ("completed", "user_hangup")
    # a browser rehearsal never counts as missed (nobody calls a test back): it stays completed
    c = _get_call(rehearsal.uuid)
    assert (c.status, c.end_reason, c.summary) == ("completed", "manual_hangup", "Caller hung up before the agent answered")


# ---------------------------------------------------------------- analysis
def test_inbound_silent_caller_not_scored_cold(monkeypatch):
    _fake_analysis(monkeypatch, {"summary": "must never be called"})
    caller = _lead("+919400000301", name="", interest="warm", rating=6, last_summary="Earlier real summary")
    silent = _call(caller, PAST, direction="inbound", end_reason="user_hangup", metrics={"agent_audio_ms": 3500},
                   turns=TALK[:1])
    dialled = _lead("+919400000302", attempts=1)
    cut = _call(dialled, PAST, end_reason="user_hangup", metrics={"agent_audio_ms": 3500}, turns=TALK[:1])
    missed = {
        "Caller hung up before the agent answered": ("no_answer", "user_hangup"),
        "Missed while the agent was offline — call back": ("no_answer", "offline"),
        "Missed inbound call — inbound calling was switched off": ("no_answer", "inbound_off"),
    }
    missed_uuids = {_call(caller, PAST, direction="inbound", status=status, end_reason=reason, analysis="pending",
                          summary=summary): summary for summary, (status, reason) in missed.items()}
    ours = _call(caller, PAST, direction="inbound", status="failed", end_reason="error", analysis="pending")

    async def scenario():
        for u in (silent, cut, *missed_uuids, ours):
            await analyze_call(u)
        await analyze_call(next(iter(missed_uuids)))  # re-analyse: still the same

    asyncio.run(scenario())
    c = _get_call(silent)
    assert (c.analysis_status, c.summary, c.interest, c.rating) == ("skipped", "Caller didn't speak", "unknown", None)
    lead = _get_lead(caller)
    assert (lead.interest, lead.rating, lead.last_summary) == ("warm", 6, "Earlier real summary")  # untouched
    assert (_get_call(cut).interest, _get_call(cut).rating) == ("cold", 1)  # the outbound rule stays
    assert _get_lead(dialled).interest == "cold"
    for u, summary in missed_uuids.items():
        assert (_get_call(u).analysis_status, _get_call(u).summary) == ("skipped", summary)
    assert _get_call(ours).summary == "Call failed on our side — call back"


def test_analysis_fills_empty_lead_fields_only(monkeypatch):
    seen: list = []
    phone = "+919400000311"
    lead_id = _lead(phone, name="", company="Team Typed Co")
    call = _call(lead_id, PAST, direction="inbound", words=40, duration=120, from_number=phone, turns=TALK,
                 analysis="pending")
    _fake_analysis(monkeypatch, {
        "customer_name": "mr. ramesh patel", "customer_company": "Patel Foods", "customer_city": "surat",
        "customer_phone": "+91 98250 11111", "call_reason": "Order or sample",
        "promised_to_customer": "Expert will call with a quote tomorrow 11 am",
        "summary": "Wants 5000 zipper pouches for rice.", "rating": 7, "interest": "warm", "qualified": True,
        "sentiment": "positive", "next_action": "Share quote", "callback_time": "tomorrow 11 am",
        "qualification": [], "key_points": ["Rice", "5000 pcs"], "objections": [], "objection_tags": [],
    }, seen)
    asyncio.run(analyze_call(call))
    lead = _get_lead(lead_id)
    assert (lead.name, lead.company, lead.city) == ("Ramesh Patel", "Team Typed Co", "Surat")
    assert (lead.interest, lead.rating, lead.last_summary) == ("warm", 7, "Wants 5000 zipper pouches for rice.")
    m = _get_call(call).metrics
    assert m["call_reason"] == "order_or_sample" and m["customer_phone"] == "+919825011111"
    assert m["promised_to_customer"] == "Expert will call with a quote tomorrow 11 am"
    assert m["user_words"] == 40 and m["customer_name"] == "Ramesh Patel"
    system, user = seen[0][0]["content"], seen[0][1]["content"]
    assert "Call direction: inbound" in user and "AGENT = our AI agent, CUSTOMER = the person on the call" in user
    assert system.index('"customer_name"') < system.index('"summary"') and "This was an INBOUND call" in system

    # a second call never overwrites what is already there (the team's or an earlier call's)
    again = _call(lead_id, PAST, direction="inbound", words=10, from_number=phone, turns=TALK, analysis="pending")
    _fake_analysis(monkeypatch, {"customer_name": "Suresh", "customer_city": "Pune", "customer_phone": phone,
                                 "call_reason": "enquiry", "summary": "Follow-up.", "interest": "warm", "rating": 7})
    asyncio.run(analyze_call(again))
    lead = _get_lead(lead_id)
    assert (lead.name, lead.city) == ("Ramesh Patel", "Surat")
    assert _get_call(again).metrics["customer_phone"] is None  # the number of this call is nothing new

    # junk is never stored: titles, null-likes, our own company / agent name
    blank = _lead("+919400000312", name="")
    junk = _call(blank, PAST, words=12, turns=TALK, analysis="pending")
    _fake_analysis(monkeypatch, {"customer_name": "Sir", "customer_company": "pouchwale", "customer_city": "unknown",
                                 "call_reason": "complaint", "summary": "Short call.", "interest": "warm", "rating": 5})
    asyncio.run(analyze_call(junk))
    lead = _get_lead(blank)
    assert (lead.name, lead.company, lead.city) == ("", None, None)
    assert _get_call(junk).metrics["call_reason"] is None  # outbound calls have no call reason
    for value in ("N/A", "-", "none", "Mr.", "Ji"):
        assert analysis_mod.customer_field(value, "name", CFG) is None, value
    assert analysis_mod.customer_field("Riya", "name", {**CFG, "agent_name": "Riya"}) is None
    assert analysis_mod.customer_field("Riyaz Khan", "name", {**CFG, "agent_name": "Riya"}) == "Riyaz Khan"
    assert analysis_mod.customer_field("PouchWale Packaging", "company", CFG) is None
    assert analysis_mod.customer_field("M/s Shree Traders", "company", CFG) == "Shree Traders"
    assert analysis_mod.customer_field("Sir", "company", CFG) is None
    assert analysis_mod.customer_field("Shri Ramesh Patel ji", "name", CFG) == "Ramesh Patel"
    assert analysis_mod.customer_field("Ab" * 150, "city", CFG) == "Ab" * 60  # capped to the column size
    assert analysis_mod.customer_field("ahmedabad", "city", CFG) == "Ahmedabad"

    # no words on the call (metrics) -> nothing learnt, even with a transcript
    quiet = _lead("+919400000313", name="")
    q = _call(quiet, PAST, words=0, turns=TALK, analysis="pending")
    _fake_analysis(monkeypatch, {"customer_name": "Mahesh", "summary": "x", "interest": "unknown"})
    asyncio.run(analyze_call(q))
    assert _get_lead(quiet).name == ""


def test_analysis_browser_call_never_changes_lead(monkeypatch):
    lead_id = _lead("+919400000321", name="", interest="unknown")
    call = _call(lead_id, PAST, channel="browser", words=30, turns=TALK, analysis="pending")
    _fake_analysis(monkeypatch, {"customer_name": "Tester Name", "customer_company": "Role Play Co",
                                 "customer_city": "Mumbai", "summary": "Role-play conversation.", "rating": 9,
                                 "interest": "hot", "qualified": True})
    asyncio.run(analyze_call(call))
    c = _get_call(call)
    assert (c.analysis_status, c.interest, c.rating) == ("done", "hot", 9)  # the test call itself is scored
    lead = _get_lead(lead_id)
    assert (lead.name, lead.company, lead.city, lead.interest, lead.rating, lead.last_summary) == (
        "", None, None, "unknown", None, None)


def test_analysis_non_enquiry_never_lowers_interest(monkeypatch):
    def run(lead_kw: dict, direction: str, result: dict, phone: str) -> Lead:
        lead_id = _lead(phone, **lead_kw)
        call = _call(lead_id, PAST, direction=direction, words=20, turns=TALK, analysis="pending")
        _fake_analysis(monkeypatch, {"summary": "New summary.", "qualified": False, **result})
        asyncio.run(analyze_call(call))
        return _get_lead(lead_id)

    hot = {"interest": "hot", "rating": 8, "last_summary": "Old summary."}
    lead = run(hot, "inbound", {"call_reason": "complaint", "interest": "cold", "rating": 2}, "+919400000331")
    assert (lead.interest, lead.rating, lead.last_summary) == ("hot", 8, "New summary.")
    lead = run(hot, "inbound", {"call_reason": "order_or_sample", "interest": "warm", "rating": 9}, "+919400000332")
    assert (lead.interest, lead.rating) == ("hot", 9)  # may still go up
    lead = run({"interest": "warm", "rating": 5}, "inbound", {"call_reason": "wrong_number", "interest": "hot",
                                                             "rating": 7}, "+919400000333")
    assert (lead.interest, lead.rating) == ("hot", 7)
    lead = run(hot, "inbound", {"call_reason": "enquiry", "interest": "warm", "rating": 5}, "+919400000334")
    assert (lead.interest, lead.rating) == ("warm", 5)  # an enquiry is a normal re-score
    lead = run(hot, "outbound", {"call_reason": "complaint", "interest": "cold", "rating": 2}, "+919400000335")
    assert (lead.interest, lead.rating) == ("cold", 2)  # outbound: unchanged behaviour


def test_analysis_prompt_is_direction_aware():
    lead = {"name": "Rahul", "company": "Sharma Snacks", "city": "Surat"}
    inbound = build_analysis_prompt(CFG, lead, "AGENT: नमस्ते\nCUSTOMER: हाँ", "inbound")
    outbound = build_analysis_prompt(CFG, lead, "AGENT: नमस्ते\nCUSTOMER: हाँ")
    for system, user in ((m[0]["content"], m[1]["content"]) for m in (inbound, outbound)):
        assert "outbound qualification calls and inbound calls from customers" in system
        assert "AGENT = our AI agent, CUSTOMER = the person on the call" in user
        keys = re.findall(r'^ "(\w+)":', system, flags=re.M)
        assert keys[:6] == ["customer_name", "customer_company", "customer_city", "customer_phone", "call_reason",
                            "promised_to_customer"] and "summary" in keys
    assert "Call direction: inbound" in inbound[1]["content"] and "Call direction: outbound" in outbound[1]["content"]
    assert all(f'"{r}"' in inbound[0]["content"] for r in CALL_REASONS)
    assert "never rate them cold just because the call was short" in inbound[0]["content"].lower()
    assert '"call_reason": null (outbound call)' in outbound[0]["content"]
    assert 'Use "unknown" only if the customer never actually spoke.' in outbound[0]["content"]
    assert "This was an INBOUND call" not in outbound[0]["content"]
