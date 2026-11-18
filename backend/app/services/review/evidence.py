"""Evidence for the call-review agent: what happened on a call, measured from the recording and the transcript.

Nothing here uses AI. It turns one call into an "evidence pack":
  * numbered transcript turns (T1, T2, ...) with their time, word count and flags;
  * timing facts measured from the stereo recording (left = customer, right = agent) and the transcript
    (F1, F2, ...): slow replies, long monologues, talking over the customer, dead air, hanging up while the
    agent was talking, customers asking to repeat, the agent asking the same thing twice...
  * the call's data (direction, length, how it ended, the post-call analysis).

The reviewer may only cite these turns and facts, and the review code checks every quote it copies against
the transcript (see agent.py), so a finding without proof can't get through.
"""
from __future__ import annotations

import audioop
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FRAME_S = 0.02            # 20 ms analysis frames
SLOW_REPLY_S = 2.5        # the customer waited longer than this for the agent's answer
MONOLOGUE_S = 15.0        # the agent talked this long without the customer getting a word in
OVERLAP_S = 0.5           # both talking at once for at least this long
DEAD_AIR_S = 4.0          # nobody talking (mid-call)
LONG_REPLY_WORDS = 35     # a reply this long is a speech, not a phone answer
MAX_TURNS = 140
MAX_TURN_CHARS = 500

END_REASONS = {
    "user_hangup": "the customer hung up",
    "agent_ended": "the agent ended the call (goodbye)",
    "silence_timeout": "ended after the customer stayed silent (\"are you there?\" check)",
    "max_duration": "reached the maximum call length",
    "manual_hangup": "hung up from the dashboard",
    "error": "ended by a technical error",
}

# Customer words that show friction: they didn't hear / understand, feel unheard, are irritated or rushed.
FRICTION = {
    "asked to repeat / couldn't hear": ("सुनाई नहीं", "आवाज़ नहीं", "आवाज नहीं", "फिर से बोल", "दोबारा बोल", "फिर से बता",
                                        "क्या बोल", "क्या कह", "repeat", "can't hear", "cannot hear", "what did you say",
                                        "say that again", "hello?"),
    "didn't understand": ("समझ नहीं", "समझा नहीं", "मतलब क्या", "didn't understand", "don't understand",
                          "not clear", "confusing"),
    "already told you": ("पहले ही बताया", "बता तो दिया", "बताया ना", "बोला ना", "already told", "i told you",
                         "already said"),
    "irritated": ("बार बार", "बार-बार", "परेशान", "time waste", "टाइम वेस्ट", "बकवास", "फालतू", "irritat",
                  "मत करो", "मत कीजिए", "don't call", "stop calling", "दिमाग"),
    "suspects a robot": ("robot", "रोबोट", "recorded", "रिकॉर्डेड", "machine", "मशीन", "real person",
                         "इंसान", "insaan"),
    "in a hurry": ("जल्दी", "busy", "बिज़ी", "बिजी", "meeting", "मीटिंग", "बाद में", "later", "driving"),
}

_WORD = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)


def norm_text(text: str) -> str:
    """Lower case, no punctuation, no nukta / chandrabindu variants: for comparing quotes with turns."""
    words = _WORD.findall(str(text or "").lower())
    return " ".join(words).replace("़", "").replace("ँ", "ं")


@dataclass
class Segment:
    start: float
    end: float

    @property
    def dur(self) -> float:
        return self.end - self.start


def _levels(pcm: bytes, rate: int) -> list[int]:
    n = max(2, int(rate * FRAME_S) * 2)
    return [audioop.rms(pcm[i:i + n], 2) for i in range(0, len(pcm) - n + 1, n)]


def speech_segments(levels: list[int], *, min_level: int = 300, factor: float = 3.0, bridge: float = 0.4,
                    min_speech: float = 0.15, floor_quantile: float = 0.1) -> list[Segment]:
    """Stretches of speech on one channel: frames above the channel's own noise floor x factor (and an absolute
    minimum), pauses shorter than `bridge` joined, blips shorter than `min_speech` dropped.

    The noise floor is a low percentile of the channel's 20 ms frame levels. It must stay low even when one side
    talks for most of the call (a monologue is exactly what has to be caught): a higher percentile would land on
    speech, push the threshold above the speech itself and hide the whole stretch."""
    if not levels:
        return []
    floor = sorted(levels)[int(len(levels) * floor_quantile)]
    threshold = max(min_level, floor * factor)
    out: list[Segment] = []
    start: float | None = None
    last = 0.0
    for i, r in enumerate(levels):
        t = i * FRAME_S
        if r > threshold:
            if start is None:
                start = t
            last = t + FRAME_S
        elif start is not None and t - last > bridge:
            out.append(Segment(start, last))
            start = None
    if start is not None:
        out.append(Segment(start, last))
    return [s for s in out if s.dur >= min_speech]


def read_recording(path: Path) -> tuple[list[Segment], list[Segment], float] | None:
    """(customer segments, agent segments, length in s) from our stereo call recording, or None."""
    try:
        with wave.open(str(path), "rb") as w:
            if w.getnchannels() != 2 or w.getsampwidth() != 2:
                return None
            rate = w.getframerate()
            pcm = w.readframes(w.getnframes())
    except (OSError, wave.Error, EOFError):
        return None
    if not pcm:
        return None
    customer = audioop.tomono(pcm, 2, 1, 0)
    agent = audioop.tomono(pcm, 2, 0, 1)
    length = len(customer) / 2 / rate
    # the agent channel is clean synthetic speech: sentence pauses inside one reply are bridged
    return (speech_segments(_levels(customer, rate), min_level=350),
            speech_segments(_levels(agent, rate), min_level=200, factor=2.0, bridge=0.8),
            length)


def _mmss(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    return f"{s // 60}:{s % 60:02d}"


def _questions(text: str) -> int:
    return text.count("?") + text.count("？")


def _active(segments: list[Segment], t: float) -> Segment | None:
    for s in segments:
        if s.start <= t <= s.end:
            return s
    return None


def _jaccard(a: str, b: str) -> float:
    x, y = set(norm_text(a).split()), set(norm_text(b).split())
    return len(x & y) / len(x | y) if x and y else 0.0


def _question_part(text: str) -> str:
    parts = re.split(r"(?<=[?？।.!])\s+", text.strip())
    qs = [p for p in parts if "?" in p or "？" in p]
    return qs[-1] if qs else ""


def build_evidence(call: dict[str, Any], turns: list[dict[str, Any]], recording: Path | None) -> dict[str, Any]:
    """The evidence pack of one call. `call`: uuid, direction, duration_sec, end_reason, interest, rating,
    sentiment, qualified, summary, objections, metrics. `turns`: role, text, offset_ms, interrupted, latency_ms."""
    turns = [t for t in turns if str(t.get("text") or "").strip() and t.get("role") in ("agent", "user")]
    turns = turns[:MAX_TURNS]
    items: list[dict[str, Any]] = []
    for i, t in enumerate(turns, start=1):
        text = str(t["text"]).strip()
        item = {"id": f"T{i}", "role": "agent" if t["role"] == "agent" else "customer",
                "at": _mmss((t.get("offset_ms") or 0) / 1000), "t": round((t.get("offset_ms") or 0) / 1000, 1),
                "text": text[:MAX_TURN_CHARS], "words": len(_WORD.findall(text))}
        if t["role"] == "agent":
            item["questions"] = _questions(text)
            if t.get("latency_ms"):
                item["reply_ms"] = int(t["latency_ms"])
            if t.get("interrupted"):
                item["cut_off_by_customer"] = True
        items.append(item)

    facts: list[dict[str, Any]] = []

    def fact(kind: str, text: str, turn: str | None = None, at: float | None = None) -> None:
        f = {"id": f"F{len(facts) + 1}", "type": kind, "text": text}
        if turn:
            f["turn"] = turn
        if at is not None:
            f["at"] = _mmss(at)
        facts.append(f)

    agents = [it for it in items if it["role"] == "agent"]
    customers = [it for it in items if it["role"] == "customer"]

    def agent_turn_near(t: float) -> dict[str, Any] | None:
        """The agent turn whose transcript time is closest to `t` (within 6 s), or None."""
        best = min(agents, key=lambda a: abs(a["t"] - t), default=None)
        return best if best is not None and abs(best["t"] - t) <= 6 else None

    # ---------------------------------------------------------------- transcript facts
    for a in agents:
        if a["words"] > LONG_REPLY_WORDS:
            fact("long_reply", f"Agent reply of {a['words']} words.", a["id"])
        if a.get("questions", 0) >= 2:
            fact("many_questions", f"{a['questions']} questions in one agent reply.", a["id"])
        if a.get("reply_ms", 0) > SLOW_REPLY_S * 1000:
            fact("slow_reply", f"The agent took {a['reply_ms'] / 1000:.1f} s to start answering (system measure).",
                 a["id"])
        if a.get("cut_off_by_customer"):
            fact("cut_off", "The customer talked over this agent reply and cut it off.", a["id"])
    asked: list[tuple[str, str]] = []
    for a in agents:
        q = _question_part(a["text"])
        if len(norm_text(q).split()) < 3:
            continue
        for earlier_id, earlier_q in asked:
            if _jaccard(q, earlier_q) >= 0.6:
                fact("repeated_question", f"The agent asked nearly the same question as in {earlier_id}.", a["id"])
                break
        asked.append((a["id"], q))
    for c in customers:
        low = c["text"].lower()
        for label, phrases in FRICTION.items():
            hit = next((p for p in phrases if p in low), None)
            if hit:
                fact("customer_friction", f"The customer's words show: {label} (\"{hit}\").", c["id"])
                break
    for prev, cur in zip(customers, customers[1:]):
        if prev["words"] >= 4 and _jaccard(prev["text"], cur["text"]) >= 0.7:
            fact("customer_repeated", f"The customer repeated what they said in {prev['id']}.", cur["id"])
    if len(customers) >= 4:
        third = max(1, len(customers) // 3)
        first = sum(c["words"] for c in customers[:third]) / third
        last = sum(c["words"] for c in customers[-third:]) / third
        if first >= 4 and last < first * 0.4:
            fact("engagement_drop", f"The customer's answers shrank from about {first:.0f} words to {last:.0f} "
                                    "words per turn over the call.")

    # ---------------------------------------------------------------- recording facts
    timing: dict[str, Any] = {"recording": False}
    rec = read_recording(recording) if recording else None
    end_reason = str(call.get("end_reason") or "")
    if rec is not None:
        cust, agent, length = rec
        talk_c = sum(s.dur for s in cust)
        talk_a = sum(s.dur for s in agent)
        timing = {"recording": True, "length_s": round(length, 1), "customer_talk_s": round(talk_c, 1),
                  "agent_talk_s": round(talk_a, 1),
                  "agent_share": round(talk_a / (talk_a + talk_c), 2) if talk_a + talk_c else None}
        gaps: list[float] = []
        for c in cust:
            nxt = next((a for a in agent if a.start >= c.end - 0.05), None)
            nxt_c = next((x for x in cust if x.start > c.end), None)
            if nxt is None or (nxt_c is not None and nxt_c.start < nxt.start):
                continue  # the customer spoke again before the agent answered: not a reply gap
            gap = nxt.start - c.end
            if gap > 8:
                continue
            gaps.append(gap)
            if gap > SLOW_REPLY_S:
                turn = agent_turn_near(nxt.start)
                fact("slow_reply", f"The customer waited {gap:.1f} s in silence for the agent's answer.",
                     turn["id"] if turn else None, nxt.start)
        if gaps:
            timing["avg_reply_gap_s"] = round(sum(gaps) / len(gaps), 1)
            timing["max_reply_gap_s"] = round(max(gaps), 1)
        for a in agent:
            if a.dur > MONOLOGUE_S:
                turn = agent_turn_near(a.start)
                fact("monologue", f"The agent talked for {a.dur:.0f} s without a break.",
                     turn["id"] if turn else None, a.start)
            # the agent started while the customer was still talking
            c = _active(cust, a.start)
            if c is not None and a.start - c.start > 0.3:
                overlap = min(a.end, c.end) - a.start
                if overlap >= OVERLAP_S:
                    turn = agent_turn_near(a.start)
                    fact("talked_over", f"The agent started talking while the customer was still speaking "
                                        f"({overlap:.1f} s overlap).", turn["id"] if turn else None, a.start)
        # dead air in the middle of the call
        events = sorted([(s.start, s.end) for s in cust + agent])
        cursor = events[0][1] if events else 0.0
        for start, end in events[1:]:
            if start - cursor > DEAD_AIR_S and end < length - 1:
                fact("dead_air", f"{start - cursor:.0f} s with nobody talking.", None, cursor)
            cursor = max(cursor, end)
        # how the call ended
        if end_reason == "user_hangup" and agent:
            last_agent = agents[-1]["id"] if agents else None
            a_end = agent[-1]
            if a_end.end >= length - 0.6:
                into = length - a_end.start
                fact("hangup_during_agent", f"The customer hung up {into:.1f} s into an agent reply "
                                            f"({a_end.dur:.1f} s of speech so far).", last_agent, length)
            elif length - a_end.end <= 3 and (not cust or cust[-1].end <= a_end.end):
                fact("hangup_after_agent", f"The customer hung up {length - a_end.end:.1f} s after the agent "
                                           "finished, without answering.", last_agent, length)
    if end_reason == "silence_timeout":
        fact("went_silent", "The customer stopped responding; the call ended after the \"are you there?\" check.",
             agents[-1]["id"] if agents else None)

    m = call.get("metrics") or {}
    data = {
        "direction": call.get("direction") or "outbound",
        "length": _mmss(float(call.get("duration_sec") or 0)),
        "ended": END_REASONS.get(end_reason, end_reason or "unknown"),
        "outcome": {k: call.get(k) for k in ("interest", "rating", "sentiment", "qualified")},
        "summary": call.get("summary") or "",
        "objections": call.get("objections") or [],
        "language": m.get("language"), "mood": m.get("mood"),
        "agent_cut_off_by_customer": m.get("barge_ins"), "hello_checks": m.get("quick_replies"),
        "settings_version": m.get("settings_rev"),
    }
    return {"call": data, "turns": items, "facts": facts, "timing": timing}


def quote_in_turn(quote: str, turn_text: str) -> bool:
    """Whether a quote the reviewer copied really is in that turn (case, punctuation, nukta-insensitive)."""
    q = norm_text(quote)
    return len(q) >= 3 and q in norm_text(turn_text)
