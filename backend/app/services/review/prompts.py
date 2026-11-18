"""Prompts of the call-review agent: (1) judge one call from its evidence pack, (2) find patterns across a
batch of judged calls and propose minimal settings changes. The judging criteria come from the editable
"How calls are judged" rubric (Settings -> Review agent); the answer formats are fixed here because the
review code validates them."""
from __future__ import annotations

import json
from typing import Any

ISSUE_CATEGORIES = ("too_long", "many_questions", "ignored_question", "repeated_question", "pushy", "wrong_info",
                    "misheard", "unnatural", "slow_reply", "talked_over", "weak_opening", "no_next_step", "other")
OUTCOMES = ("converted", "interested", "neutral", "lost", "not_a_fit")
LOSS_CAUSES = ("agent", "customer_side", "technical", "unclear")
# Problems of speed and turn-taking: real, and reported to the team, but no script change can fix them.
TECHNICAL_CATEGORIES = ("slow_reply", "talked_over")
TECHNICAL_HINT = {
    "slow_reply": "Customers waited too long for answers: check the reply speed (AI model and voice latency, "
                  "internet connection).",
    "talked_over": "The agent started talking before customers had finished: the end-of-speech timing needs a look "
                   "(tell whoever maintains the agent).",
}
TOO_LONG_MIN_WORDS = 25  # a reply called "too long" has at least this many words (or a measured monologue)

# What the review agent may change, and how. Long texts are edited in place (find -> replace, or append), so
# a suggestion can never silently drop the rest of a long text; short spoken lines are replaced whole.
FIELD_RULES: dict[str, tuple[str, ...]] = {
    "learned_rules": ("append", "edit", "replace"),
    "opening_line": ("replace",),
    "closing_line": ("replace",),
    "silence_prompt_text": ("replace",),
    "silence_goodbye_text": ("replace",),
    "inbound_closing_line": ("replace",),
    "call_purpose": ("edit", "append"),
    "inbound_purpose": ("edit", "append"),
    "objection_handling": ("edit", "append"),
    "knowledge_base": ("edit", "append"),
    "qualification_questions": ("replace",),
}
FIELD_HELP = {
    "learned_rules": "Extra rules added to the agent's core instructions, one per line starting with \"- \". "
                     "The usual place to fix HOW the agent talks (length, questions, tone, listening).",
    "opening_line": "The first thing the agent says on an outbound call (spoken; keep it short; keep placeholders "
                    "such as {lead_name_ji} and {company_name}).",
    "closing_line": "Said when an outbound call reaches its maximum length (spoken).",
    "silence_prompt_text": "Said when the customer goes silent (\"are you there?\").",
    "silence_goodbye_text": "Said before hanging up on a silent line.",
    "inbound_closing_line": "Said when an inbound call reaches its maximum length (spoken).",
    "call_purpose": "The outbound call flow (steps the agent follows).",
    "inbound_purpose": "The inbound call flow (when customers call us).",
    "objection_handling": "How to answer objections (price, MOQ, supplier, busy...).",
    "knowledge_base": "Company facts the agent may tell customers. NEVER add new facts (prices, MOQ, timelines, "
                      "products, policies): only clarify or reorganise what is already there.",
    "qualification_questions": "The questions the agent asks, one at a time (a list of short lines).",
}


def call_review_messages(rubric: str, company: str, knowledge: str, pack: dict[str, Any]) -> list[dict]:
    system = f"""You are a senior conversation analyst. You review recorded phone calls made by the AI voice agent of {company} (custom printed packaging pouches, India; calls in Hindi, Hinglish or English). You decide whether the customer's interest was lost because of the agent, find the exact moment, and list the agent's mistakes, each backed by proof.

# How to judge (from the team)
{rubric.strip()}

# The evidence you get
- "call": the call's data (direction, length, how it ended, the post-call analysis).
- "turns": the numbered transcript (T1..Tn), AGENT and CUSTOMER, with the time ("at") and word counts. Customer lines come from speech recognition and may contain recognition errors: don't blame the agent for a garbled customer line unless the agent's own reply was wrong.
- "facts": timing and transcript facts measured by code (F1..Fn): slow replies, monologues, talking over the customer, dead air, hanging up during an agent reply, friction words, repeated questions. They are reliable measurements; cite them by id.
- "timing": overall measures from the call recording.

# What the agent was allowed to say (company facts), to judge wrong_info
{knowledge.strip()[:3500]}

# Answer
Return ONLY a JSON object:
{{
 "outcome": one of {json.dumps(OUTCOMES)},
 "customer_intent": "one sentence: what the customer wanted or was open to at the start",
 "engagement": "rising" | "steady" | "falling" | "dropped",
 "intent_lost": true | false,
 "lost_at": "T#" (the AGENT turn right before they disengaged) or null,
 "loss_cause": one of {json.dumps(LOSS_CAUSES)} or null (null when intent_lost is false),
 "customer_side_reason": "short reason when loss_cause is customer_side, else null",
 "verdict": "2-3 plain-English sentences for the sales team: what happened and why",
 "issues": [{{"category": one of {json.dumps(ISSUE_CATEGORIES)}, "turn": "T#" (an AGENT turn), "quote": "words copied EXACTLY from that agent turn (3-15 words)", "facts": ["F#", ...], "earlier": "T#" or null (see the rules), "customer_reaction": "what the customer did right after, citing T#/F#, or null", "severity": 1 | 2 | 3, "why": "one sentence"}}],
 "strengths": [{{"turn": "T#", "quote": "exact words from that turn", "why": "one sentence"}}]
}}
Rules:
- Every issue needs proof: a quote copied exactly from the cited agent turn, and/or facts (F#) about that turn. No proof, no issue.
- Some categories are MEASURED, and the code removes the issue when the measurement doesn't back it:
  too_long = the reply has {TOO_LONG_MIN_WORDS}+ words (see "words") or a monologue fact; many_questions = 2 or more questions in that one reply (see "questions"); slow_reply = a slow_reply fact at that turn; talked_over = a talked_over fact at that turn; repeated_question = a repeated_question fact at that turn, OR "earlier": the earlier turn where the same thing was already asked or answered; ignored_question = "earlier": the CUSTOMER turn whose question the agent didn't answer (at most 2 agent replies before this turn).
- The customer cutting the agent off, going quiet or hanging up is a REACTION (put it in customer_reaction), not a mistake. Name the agent's mistake that caused it, or report no issue.
- wrong_info: the agent said something false, or a fact that isn't in the company facts above (invented numbers, sizes, prices, promises).
- intent_lost with loss_cause "agent" only when the customer's engagement visibly dropped right after a specific agent turn (they hung up during or just after it, got curt or annoyed, repeated themselves, said they weren't heard, stopped answering) AND that turn did something wrong. Otherwise use customer_side, technical or unclear.
- Normal reasons to end a call are NOT the agent's fault (see the rubric): report them as customer_side. A customer who is busy and asks for a call back is "neutral", not "lost" or "not_a_fit".
- severity: 3 = this lost the customer (only the mistake right before they were lost), 2 = clearly hurt the conversation, 1 = minor.
- At most 6 issues (the most important) and 3 strengths. Be specific and fair; no generic advice."""
    user = json.dumps(pack, ensure_ascii=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def batch_messages(rubric: str, company: str, min_evidence: int, calls: list[dict[str, Any]],
                   stats: dict[str, Any], fields: dict[str, Any], decisions: list[dict[str, Any]],
                   recent_changes: list[dict[str, Any]], core_rules: str = "") -> list[dict]:
    allowed = "\n".join(f"- {name} ({', '.join(FIELD_RULES[name])}): {FIELD_HELP[name]}" for name in FIELD_RULES)
    system = f"""You are the lead conversation designer of the {company} AI voice agent. You get the reviews of a batch of its recent calls. Find the problems that keep repeating and propose precise, minimal changes to the agent's settings that would keep customers engaged. A person approves or rejects every suggestion; nothing changes without approval.

# How calls were judged (from the team)
{rubric.strip()}

# Your inputs
- "calls": the reviewed calls (C1..Cn), each with its verdict and verified findings (agent turn T#, exact quote, facts).
- "stats": numbers measured by code across the batch.
- "fields": the settings you may change, with their CURRENT values.
- "decisions": suggestions the team approved or rejected recently (respect them: don't propose a rejected change again unless the new evidence is much stronger, and then say so).
- "recent_changes": settings changes made recently, and which calls ran on which settings version (settings_version) — use this to judge whether a recent change helped.
- "core_rules": the agent's built-in instructions that no setting holds (how it talks, listens and ends calls). It already follows these.

# Rules
- A pattern counts only if it shows up in at least {min_evidence} different calls, each backed by one of THAT call's verified findings: cite the call, the finding's agent turn and its exact quote. Evidence that doesn't point at a verified finding of that call is discarded by the code. Weaker or one-off problems go to "watch_list".
- Only patterns where the AGENT lost or hurt the conversation count. Customers who simply weren't a fit are not a pattern to fix.
- Use the pattern's category for the problem it shows (the calls' findings carry categories); evidence for a pattern must be findings of that same category.
- {' and '.join(TECHNICAL_CATEGORIES)} are technical problems (speed, turn-taking): list them as patterns so the team sees them, but never propose a settings change for them.
- At most 4 suggestions, the most impactful first. Each fixes ITS OWN pattern's problem, changes as little as possible, and keeps the agent's style (warm Hinglish; Hindi words in Devanagari; short spoken lines).
- Don't propose something the agent already does or is already told (see "core_rules" and the current field values). If the instructions exist but the calls show the agent breaking them, propose a sharper, more specific rule for the exact situation where it happened.
- A learned rule must be specific and actionable: in which situation, what to say or do instead, with a short example line in the agent's style. Generic advice ("be concise", "listen better", "be polite") is not allowed.
- Never invent company facts (prices, MOQ, timelines, products, policies). If customers keep asking for a fact we don't have, write a "team_notes" item instead of a suggestion.
- How each field may be changed:
{allowed}
  Operations: "append" adds "value" (text) at the end; "edit" replaces the exact text "find" (copied from the current value, appearing exactly once) with "replace_with"; "replace" gives the complete new "value" (for qualification_questions: the complete new list of questions).

# Answer
Return ONLY a JSON object:
{{
 "summary": "3-5 plain-English sentences for the team: how the calls went, where customers were lost and why",
 "patterns": [{{"id": "P1", "title": "short name", "category": "one of the mistake categories", "calls": ["C#", ...], "evidence": [{{"call": "C#", "turn": "T#", "quote": "exact words from that agent turn"}}], "impact": "what it cost us"}}],
 "watch_list": [{{"title": "...", "calls": ["C#"], "note": "..."}}],
 "team_notes": ["..."],
 "impact_of_recent_changes": "one or two sentences, or null if there were no recent changes",
 "suggestions": [{{"pattern": "P#", "field": "one of the fields", "operation": "append" | "edit" | "replace", "value": "...", "find": "...", "replace_with": "...", "title": "what to change, in a few words", "why": "why, citing the evidence (calls)", "expected_effect": "what should improve"}}]
}}"""
    user = json.dumps({"calls": calls, "stats": stats, "fields": fields, "decisions": decisions,
                       "recent_changes": recent_changes, "core_rules": core_rules}, ensure_ascii=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
