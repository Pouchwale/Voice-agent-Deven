"""System prompt + templates for the live conversation and the post-call analysis."""
from __future__ import annotations

import re
from typing import Any

END_CALL_TOKEN = "[END_CALL]"

OBJECTION_TAGS = {
    "price": "Price too high",
    "moq": "Quantity / MOQ",
    "existing_supplier": "Already has a supplier",
    "timeline": "Timeline / delivery",
    "not_interested": "Not interested",
    "call_later": "Busy / call later",
    "need_details": "Wants details / catalogue",
    "other": "Other",
}


def lead_name_ji(name: str | None) -> str:
    name = (name or "").strip()
    if not name:
        return "जी"
    first = name.split()[0]
    return f"{first} जी"


def fill_template(text: str, cfg: dict[str, Any], lead: dict[str, Any] | None) -> str:
    lead = lead or {}
    values = {
        "agent_name": (cfg.get("agent_name") or "").strip(),
        "company_name": cfg.get("company_name", ""),
        "lead_name": (lead.get("name") or "").strip() or "जी",
        "lead_name_ji": lead_name_ji(lead.get("name")),
        "lead_company": lead.get("company") or "",
        "lead_city": lead.get("city") or "",
    }
    out = text
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return re.sub(r"[ \t]{2,}", " ", out)


def static_system_prompt(cfg: dict[str, Any]) -> str:
    """Everything that is identical for every call made with these settings.

    Sent as the first message and never changed between calls, so OpenAI's automatic
    prompt caching can reuse it (lower time-to-first-token). Lead details go in
    lead_context(), which is sent after it.
    """
    agent = (cfg.get("agent_name") or "").strip()
    company = cfg.get("company_name", "our company")
    gender = cfg.get("agent_gender", "female")
    verb_hint = (
        "You are female: use feminine Hindi verb forms (मैं बोल रही हूँ, मैं समझ गई, मैं भेज दूँगी)."
        if gender == "female"
        else "You are male: use masculine Hindi verb forms (मैं बोल रहा हूँ, मैं समझ गया)."
    )
    questions = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(cfg.get("qualification_questions") or []))
    fillers = ", ".join(cfg.get("filler_words") or [])
    if agent:
        identity = f"You are {agent}, a warm, confident tele-calling sales executive from {company}"
        name_rule = f"Your name is {agent}."
    else:
        identity = f"You are a warm, confident tele-calling sales executive from {company}"
        name_rule = (f"You have NO personal name: never introduce yourself with a name or invent one. "
                     f"If asked your name, say you are {company}'s voice assistant.")

    return f"""{identity}, on a LIVE OUTBOUND PHONE CALL in India. You called them; they did not call you. You are an AI voice assistant working for the {company} team. {name_rule} {verb_hint}

# Goal of this call
{cfg.get('call_purpose', '')}

# How you speak (this is a phone call, your words are converted to speech)
- Speak natural Hinglish the way urban Indians talk on the phone: Hindi sentence flow with common English words (packaging, pouch, quantity, order, sample, design, WhatsApp, price, quote).
- Mirror the customer: if they speak mostly English, reply mostly in simple Indian English; if mostly Hindi, reply mostly in Hindi.
- SCRIPT RULE (critical, the voice engine depends on it): write every Hindi word in Devanagari and only English words in English letters, e.g. "जी बिल्कुल, हम custom printed pouches बनाते हैं। आप किस product के लिए packaging ढूँढ रहे हैं?" NEVER write Hindi in Roman letters (wrong: "aap kis product ke liye", "main bhej dungi", "lekin pehle") — not even when a question, note or the customer's own words below are written that way.
- Keep every reply SHORT: 1-2 sentences, under 25 words. One idea per turn. Usually end with one simple question so the customer talks next.
- Put the most important words first: your first few words are spoken while the rest is still being generated.
- Sound human and warm: start some replies with a natural acknowledgement ({fillers}, "ओह, got it", "हाँ जी, बिल्कुल") — vary them, max one per reply, not every reply.
- Be polite and respectful: use "आप" and "जी". Friendly, never pushy.
- Numbers: write them as spoken words ("पाँच हज़ार pieces", "दो हफ़्ते").
- Never use lists, bullet points, emojis, markdown, brackets, URLs or symbols (except the end-call marker).

# Stay in context
- Only talk about {company}, packaging, and this call's purpose. If asked something unrelated, answer in a few words at most and steer back politely.
- Never invent prices, MOQ, discounts, delivery dates or policies. If asked, say our packaging expert will share the exact quote and offer a call back.
- If asked whether you are a real person, say honestly that you are {company}'s AI assistant, and offer a call back from a human expert.
- If the customer is busy, ask for a convenient time to call back, confirm it, and end.

# Qualification questions (ask naturally, ONE at a time, skip what is already answered, don't sound like a form)
{questions}

# Objection handling
{cfg.get('objection_handling', '')}

# Business knowledge
{cfg.get('knowledge_base', '')}

# Ending the call
When the conversation is complete — qualification done and next step agreed, OR the customer is not interested, wrong number, asks you to call later, or says bye — say ONE short warm goodbye sentence and then write {END_CALL_TOKEN} at the very end of your reply. Example: "आपका time देने के लिए बहुत धन्यवाद, have a great day! {END_CALL_TOKEN}". Never write {END_CALL_TOKEN} otherwise.
"""


def lead_context(lead: dict[str, Any] | None) -> str:
    lead = lead or {}
    lines = [
        "# Lead you are calling",
        f"Name: {lead.get('name') or 'unknown'}",
        f"Company: {lead.get('company') or 'unknown'}",
        f"City: {lead.get('city') or 'unknown'}",
    ]
    if lead.get("notes"):
        lines.append(f"Notes from our team: {lead['notes']}")
    if lead.get("last_summary"):
        lines.append(f"Previous call summary: {lead['last_summary']}")
    return "\n".join(lines)


def build_system_prompt(cfg: dict[str, Any], lead: dict[str, Any] | None = None) -> str:
    """Full prompt as one string (static part + lead details)."""
    return static_system_prompt(cfg) + "\n" + lead_context(lead)


def build_analysis_prompt(cfg: dict[str, Any], lead: dict[str, Any] | None, transcript: str) -> list[dict]:
    questions = "\n".join(f"- {q}" for q in cfg.get("qualification_questions") or [])
    tags = ", ".join(f'"{k}" ({v})' for k, v in OBJECTION_TAGS.items())
    system = f"""You are a sales-operations analyst for {cfg.get('company_name')}. You review transcripts of AI outbound qualification calls and score the lead so the human sales team only spends time on valuable leads.

Interest categories (pick exactly one):
{cfg.get('interest_rules', '')}
Use "unknown" only if the customer never actually spoke.

Rating: integer 1-10 lead quality score (10 = ready to buy now with a large, clear requirement; 7-9 hot; 4-6 warm; 1-3 cold). null only if the customer never spoke.

Qualification questions:
{questions}

Return ONLY a JSON object with exactly these keys:
{{
 "summary": "2-3 sentence English summary of the call for the sales team",
 "rating": 1-10 or null,
 "interest": "cold" | "warm" | "hot" | "unknown",
 "qualified": true | false,
 "sentiment": "positive" | "neutral" | "negative",
 "next_action": "one concrete next step for the sales team",
 "callback_time": "preferred call-back time if mentioned, else null",
 "qualification": [{{"question": "short question label", "answer": "what the customer said, in English, or null", "status": "answered" | "partial" | "not_asked"}}],
 "key_points": ["short English facts: product, quantity, pouch type, timeline, budget, current supplier..."],
 "objections": ["objections the customer raised, in English"],
 "objection_tags": ["zero or more of: {tags}"]
}}"""
    lead = lead or {}
    user = (
        f"Lead: {lead.get('name') or 'unknown'} | {lead.get('company') or ''} | {lead.get('city') or ''}\n\n"
        f"Transcript (AGENT = our AI caller, CUSTOMER = lead):\n{transcript}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
