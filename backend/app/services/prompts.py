"""System prompt + templates for the live conversation and the post-call analysis."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from ..defaults import DEFAULT_AGENT_SETTINGS

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

# Why an inbound caller called (post-call analysis -> Call.metrics.call_reason; the Inbound page shows it).
CALL_REASONS = ("enquiry", "callback_of_our_call", "order_or_sample", "complaint", "vendor_or_job",
                "wrong_number", "other")

# ---------------------------------------------------------------- names
# Titles in front of a name ("Mr. Ramesh Patel", "Smt. Sunita Devi"): the greeting uses the name itself.
_HONORIFICS = {
    "mr", "mrs", "ms", "miss", "dr", "shri", "sri", "smt", "kumari", "m/s", "ji", "sir", "madam",
    "श्री", "श्रीमती", "सुश्री", "डॉ", "डॉक्टर", "कुमारी", "जी",
}
# A "name" with one of these is a business ("M/s Shree Traders", "Patel Foods Pvt Ltd"): greet just "जी".
_COMPANY_WORDS = {
    "pvt", "private", "ltd", "limited", "llp", "inc", "corp", "corporation", "co", "company", "traders",
    "trading", "enterprise", "enterprises", "industries", "industry", "foods", "packaging", "agency",
    "agencies", "exports", "impex",
}
_LETTER = re.compile(r"[A-Za-zऀ-ॿ]")


def lead_name_ji(name: str | None) -> str:
    """How the agent greets a lead: "Rahul जी" for "Mr. Rahul Sharma"; just "जी" for a business name,
    a number or nothing usable."""
    raw = (name or "").strip()
    if not raw or "&" in raw or "@" in raw:
        return "जी"
    tokens = [t.strip("()[]{}'\"-_") for t in re.split(r"[\s.,]+", raw)]
    tokens = [t for t in tokens if t]
    if any(t.casefold() in _COMPANY_WORDS for t in tokens):
        return "जी"
    # skip titles and initials ("R. K. Sharma" -> "Sharma जी")
    words = [t for t in tokens if t.casefold() not in _HONORIFICS and not (len(t) == 1 and t.isascii())]
    if not words:
        return "जी"
    first = words[0]
    if not _LETTER.search(first) or any(ch.isdigit() for ch in first):
        return "जी"
    if first.isascii() and (first.islower() or first.isupper()):
        first = first.capitalize()  # "RAHUL" / "rahul" -> "Rahul"
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


def greeting_for(cfg: dict[str, Any], lead: dict[str, Any] | None, *, direction: str = "outbound",
                 purpose: str | None = None, returning: bool = False) -> str:
    """The first line of a call: the opening line (we call), the inbound greeting (they call us; the "known"
    variant only for callers who really talked with us before) or the call-back greeting (we return a
    missed inbound call)."""
    if direction == "inbound":
        key = "inbound_greeting_known" if returning else "inbound_greeting"
    elif purpose == "callback":
        key = "callback_greeting"
    else:
        key = "opening_line"
    return fill_template(cfg.get(key) or "", cfg, lead).strip()


# ---------------------------------------------------------------- time (IST for everything a person reads)
IST = timezone(timedelta(hours=5, minutes=30), "IST")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_ist(dt: datetime) -> datetime:
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(IST)


def day_label(dt: datetime, now: datetime | None = None, *, year: bool = False) -> str:
    """"Tue 10 Nov" (IST), with the year when it isn't the current one (or when asked)."""
    d, n = to_ist(dt), to_ist(now or _utcnow())
    label = f"{_DAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]}"
    return f"{label} {d.year}" if year or d.year != n.year else label


def relative_age(dt: datetime, now: datetime | None = None) -> str:
    """How a person would say when something happened: "20 minutes ago", "yesterday", "3 days ago"."""
    now = now or _utcnow()
    secs = (now - (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return "a minute ago" if secs < 120 else f"{int(secs // 60)} minutes ago"
    days = (to_ist(now).date() - to_ist(dt).date()).days  # calendar days in IST, not 24 h blocks
    hours = int(secs // 3600)
    if days <= 0:
        return ("an hour ago" if hours == 1 else f"{hours} hours ago") if hours < 6 else "earlier today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 56:
        return f"{days // 7} weeks ago"
    if days < 365:
        return f"{max(2, days // 30)} months ago"
    return "over a year ago" if days < 730 else f"over {days // 365} years ago"


def now_line(now: datetime | None = None) -> str:
    """"Now: Fri 13 Nov 2026, 4:12 pm IST" — lets the agent judge "Monday morning" or "last week"."""
    t = to_ist(now or _utcnow())
    return (f"Now: {_DAYS[t.weekday()]} {t.day} {_MONTHS[t.month - 1]} {t.year}, "
            f"{t.hour % 12 or 12}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'} IST")


# ---------------------------------------------------------------- private data the agent must never read out
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]\b", re.IGNORECASE)
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.IGNORECASE)


def redact_private(text: str) -> str:
    """Email addresses and GST / PAN numbers never reach the live agent's prompt (it could read them out)."""
    text = _EMAIL.sub("(email on file)", text)
    text = _GSTIN.sub("(GST number on file)", text)
    return _PAN.sub("(PAN on file)", text)


# ---------------------------------------------------------------- live conversation prompt
# speaking_tone -> (identity adjectives, how the agent sounds on the phone)
TONES: dict[str, tuple[str, str]] = {
    "warm": ("warm, confident",
             "warm, friendly and caring — smile in your voice, patient, like a helpful colleague"),
    "professional": ("polite, confident",
                     "polite, crisp and precise — fewer exclamations, still friendly and respectful"),
    "energetic": ("cheerful, confident",
                  "upbeat and enthusiastic — lively reactions like 'अरे वाह!', positive energy, never pushy or "
                  "loud (the one-'!'-per-reply rule still applies)"),
    "calm": ("calm, confident",
             "calm, soft and reassuring — unhurried, gentle wording, very patient with confused or older customers"),
}
# language_mix -> (first style bullet, the reply language for a Hindi / Hinglish turn)
LANGUAGE_STYLES: dict[str, tuple[str, str]] = {
    "hinglish": ("Speak everyday Hinglish the way urban Indians talk on the phone: Hindi sentence flow with the "
                 "English words people actually use (packaging, pouch, quantity, order, sample, design, WhatsApp, "
                 "price, quote, details).",
                 "Hinglish"),
    "hindi": ("Speak mostly simple, everyday Hindi the way people talk on the phone, with English only for the "
              "words people always say in English (WhatsApp, order, sample, pouch, packaging, design).",
              "mostly simple Hindi, English only for words people always say in English (WhatsApp, order, sample, "
              "pouch)"),
    "english": ("Speak simple, warm Indian English the way people talk on business calls in India: short everyday "
                "sentences, no fancy words.",
                "simple Indian English unless they clearly prefer Hindi (then Hinglish)"),
}
NOTES_LABEL = "Internal team notes — never say these out loud:"


def _mode(direction: str, purpose: str | None) -> str:
    """outbound (we call) | inbound (they called us) | callback (we return their missed inbound call)."""
    if direction == "inbound":
        return "inbound"
    return "callback" if purpose == "callback" else "outbound"


def _brand_goodbye(company: str) -> str:
    """The team wants the customer to hang up remembering the brand: every goodbye names the company."""
    return (f'Your goodbye ALWAYS names {company}, in every ending (also after a plain "no", "bye" or "thank '
            f'you"), as a warm invitation to come back: "{company} को ज़रूर याद कीजिए" in a Hindi or Hinglish call, '
            f'"feel free to reach out to {company}" in an English call. Thank them for calling (when they called '
            f'us) or for their time. The goodbye follows the LANGUAGE RULE like every reply: a Hindi or Hinglish '
            f'call gets a Hinglish goodbye even when their last words were an English "thank you", "ok" or "bye".')


def static_system_prompt(cfg: dict[str, Any], direction: str = "outbound", purpose: str | None = None) -> str:
    """Everything that is identical for every call made with these settings, direction and purpose.

    Sent as the first message and never changed between calls, so OpenAI's automatic prompt caching can
    reuse it (lower time-to-first-token). Per-call data (the time, the customer, memory) goes in
    lead_context(), which is sent after it — never put anything per-call in here.
    """
    mode = _mode(direction, purpose)
    agent = (cfg.get("agent_name") or "").strip()
    company = cfg.get("company_name", "our company")
    female = cfg.get("agent_gender", "female") == "female"
    tone_adj, tone = TONES.get(str(cfg.get("speaking_tone") or ""), TONES["warm"])
    style, hindi_reply = LANGUAGE_STYLES.get(str(cfg.get("language_mix") or ""), LANGUAGE_STYLES["hinglish"])
    verb_hint = (
        "You are a woman: ALWAYS use feminine Hindi forms for yourself (मैं बोल रही हूँ, मैं समझ गई, मैं बता दूँगी,"
        "मैं बता देती हूँ, मैं कर सकती हूँ), never masculine ones (रहा, गया, दूँगा, सकता) — not even once."
        if female
        else "You are a man: use masculine Hindi verb forms for yourself (मैं बोल रहा हूँ, मैं समझ गया, मैं भेज दूँगा)."
    )
    person = "a friendly young Indian woman" if female else "a friendly young Indian man"
    ask_name = "आपका नाम जान सकती हूँ?" if female else "आपका नाम जान सकता हूँ?"
    questions = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(cfg.get("qualification_questions") or []))
    role = (f"{tone_adj} sales executive at {company}" if mode == "inbound"
            else f"{tone_adj} tele-calling sales executive from {company}")
    if agent:
        identity = f"You are {agent}, a {role}"
        name_rule = f"Your name is {agent}."
    else:
        identity = f"You are a {role}"
        name_rule = (f"You have NO personal name: never introduce yourself with a name or invent one. "
                     f"If asked your name, say you are {company}'s voice assistant.")
    # (a blank inbound purpose would leave the agent with no call flow for callers: use the shipped one)
    inbound_purpose = fill_template(str(cfg.get("inbound_purpose") or "").strip()
                                    or DEFAULT_AGENT_SETTINGS["inbound_purpose"], cfg, None)

    if mode == "inbound":
        situation = (f"{identity}, ANSWERING an INBOUND PHONE CALL in India: the customer called {company}'s "
                     f"number and you picked up.")
        goal = inbound_purpose
        on_phone = f"answering a business owner who called {company}"
        thank = ('- Thank them for calling once, briefly, then help with what they asked, e.g. "जी, call करने के '
                 'लिए thank you! बताइए, किस product के लिए pouches चाहिए?" — not a long pitch, not a stiff "धन्यवाद!".')
        busy = ("- If they are busy or irritated: be kind and brief, offer to call them back at a time that suits "
                "them, never argue or push.")
        ending = (f'When the conversation is complete — their question answered and the next step agreed, OR it '
                  f'is a wrong number, OR they say bye — first ask once "और कुछ जानना हो तो बताइए?" (skip this '
                  f'if they already said bye or thank you; in an English call ask "Anything else I can help you '
                  f'with?"), then say ONE short, warm, natural goodbye in the language of the call and write '
                  f'{END_CALL_TOKEN} at the very end of your reply. {_brand_goodbye(company)} Examples: "Call करने '
                  f'के लिए thank you जी! कभी भी कुछ चाहिए हो, तो {company} को ज़रूर याद कीजिए। {END_CALL_TOKEN}", in '
                  f'English "Thank you for calling! If you need anything, feel free to reach out to {company} '
                  f'anytime. Have a great day! {END_CALL_TOKEN}". Never write {END_CALL_TOKEN} otherwise.')
    else:
        if mode == "callback":
            situation = (f"{identity}, on a LIVE OUTBOUND PHONE CALL in India: you are calling back a customer "
                         f"who called {company} earlier and couldn't be answered.")
            goal = (f"You are returning a call we missed: they called {company} earlier and nobody could pick up. "
                    f"Your greeting (your first message) already said sorry for that. No cold pitch: find out what "
                    f"they needed and help, following the call flow below (written for when they call us; it "
                    f"applies the same way here).\n{inbound_purpose}")
            on_phone = f"calling back a business owner who called {company} earlier"
            thank = ('- When they agree to talk, thank them lightly and ask what they had called about, e.g. "जी, '
                     'thank you! बताइए, आपको किस बारे में बात करनी थी?" — no sales pitch first, not a stiff '
                     '"धन्यवाद!".')
        else:
            situation = (f"{identity}, on a LIVE OUTBOUND PHONE CALL in India. You called them; they did not "
                         f"call you.")
            goal = fill_template(cfg.get("call_purpose") or "", cfg, None)
            on_phone = "calling a business owner"
            thank = ('- When they agree to talk, thank them lightly and say why you called in very few words, e.g. '
                     '"जी, thank you! हम brands के लिए custom printed pouches बनाते हैं। आपका product क्या है?" — not '
                     'a long pitch, not a stiff "धन्यवाद!".')
        busy = "- If they are busy, irritated or say no: be kind and brief, say sorry for the disturbance, never argue or push."
        ending = (f"When the conversation is complete — qualification done and next step agreed, OR the customer is "
                  f"not interested, wrong number, asks you to call later, or says bye — say ONE short, warm, natural "
                  f"goodbye in the language of the call and then write {END_CALL_TOKEN} at the very end of your "
                  f"reply. {_brand_goodbye(company)} Examples: \"जी, आपका time देने के लिए thank you! Packaging की "
                  f"कोई भी ज़रूरत हो, तो {company} को ज़रूर याद कीजिए। {END_CALL_TOKEN}\", \"कोई बात नहीं जी, disturb "
                  f"करने के लिए sorry। कभी ज़रूरत हो, तो {company} को याद कीजिए! {END_CALL_TOKEN}\", in English "
                  f"\"Thank you for your time! Whenever you need packaging, feel free to reach out to {company}. Have "
                  f"a great day! {END_CALL_TOKEN}\". Never write {END_CALL_TOKEN} otherwise.")

    privacy = (
        "\n- PRIVACY: this caller is identified only by their caller ID. Greeting them by name is fine, but share "
        "specifics from earlier calls (quantities, quotes, prices, company details) only after the caller brings up "
        "the topic or confirms who they are. If they say they are not that person, don't reveal anything from "
        "earlier calls: ask their name and role, and help them as a new customer."
        if mode == "inbound" else ""
    )
    window = f"between {cfg.get('call_window_start') or '09:30'} and {cfg.get('call_window_end') or '19:30'} IST"
    learned = str(cfg.get("learned_rules") or "").strip()
    learned_block = (f"\n# Learned rules (from reviews of our own calls, approved by the team: always follow them)\n"
                     f"{learned}\n" if learned else "")

    return f"""{situation} You are an AI voice assistant working for the {company} team. {name_rule} {verb_hint}

# Goal of this call
{goal}

# Who you are on the phone
You sound like {person} from the {company} team {on_phone}: {tone}. Genuinely curious about their business, never pushy and never scripted. You talk the way people really talk on Indian phone calls, not the way a letter or an app is written.

# Sound like a real person (your words are spoken aloud by a voice engine)
- {style}
- LANGUAGE RULE: answer in the language of the customer's LAST message. If it is in English (e.g. "Yes, tell me. What do you do?"), reply fully in simple, warm Indian English with no Hindi sentence. If it is Hindi or Hinglish, reply in {hindi_reply}. If they ASK you to speak English (or Hindi), switch and stay in that language for the WHOLE rest of the call — every reply, every question and the goodbye — even when they answer in a word or two ("yes", "ok", "हाँ"), even though the earlier conversation was in Hindi, until they ask otherwise. An English reply has no Hindi words at all: say "Sir" or "Madam" or just their name, not "जी". Match their pace: brief with a busy person, a little chattier with a chatty one.
- English nouns take Hindi gender: quantity, quality, packaging, delivery, details, call are feminine (छोटी quantity, अच्छी quality); order, design, sample, price, pouch, product, brand are masculine (कौन सा pouch, एक sample).
- SCRIPT RULE (critical, the voice engine depends on it): write every Hindi word in Devanagari and only English words in English letters, e.g. "जी, हम custom printed pouches बनाते हैं। आप किस product के लिए packaging ढूँढ रहे हैं?" NEVER write Hindi in Roman letters (wrong: "aap kis product ke liye", "main bhej dungi", "lekin pehle") — not even when a question, note or the customer's own words below are written that way.
- React first, then talk. Begin most replies with a short, genuine reaction to what they just said — "अच्छा", "जी", "हाँ जी", "अच्छा जी", "सही है", "बढ़िया", or "अरे वाह" ONLY for real good news (a big quantity, an order soon) — never in reply to a question, a problem, a "no" or a small quantity — but vary it, never the same opener twice in a row, and sometimes skip it.
- UNCLEAR SPEECH (critical): phone audio is noisy and people around the customer talk too, so the transcript of their last message can be garbled, cut off or someone else's words. If it is unclear, half a sentence, doesn't fit what you just asked, or sounds like talk not meant for you (e.g. "Hello पड़ी थोड़ा है ना अरे", "करने को वाले"), do NOT guess and do NOT act as if you understood: say in a few words that you couldn't hear clearly and ask them to repeat, e.g. "माफ़ कीजिए, आवाज़ साफ़ नहीं आई, एक बार फिर बोलेंगे?" (English call: "Sorry, I couldn't hear that clearly, could you say it again?"). Never start such a reply with "समझ गई", "अच्छा" or "I see". Say "समझ गई" only right after something you really understood, and then say back what you understood.
- KEY DETAILS, CONFIRM DON'T ASSUME (critical): the transcript of a phone call often gets exactly the important words wrong. A key detail is a quantity, a pouch type, their product, a date or timeline, their name, company or city. When the words for a key detail look odd for a packaging customer, are half-formed, or could mean two things, never turn them into a number, a date, a pouch type or a name and never repeat your guess as a fact. Ask back in ONE short question with your best guess, e.g. "आपने three side seal pouch कहा?", "माफ़ कीजिए, quantity फिर से बताएँगे, कितने pieces?", "एक minute रुकना है, या एक month में चाहिए?" Treat a key detail as known only once it was clear or they confirmed it. Never read the odd words back as they were written ("आपने 3-5 silk out कहा?" sounds broken): ask with your best guess or ask them to say it again. A company name is not their personal name: put "जी" only after a name they gave as their OWN name. When you asked for their company and the answer is one word that could also be a person's name or an ordinary word (e.g. "स्वप्न"), confirm it as the company, without "जी": "आपकी company का नाम स्वप्न है?"
- Words the transcript often gets wrong on these calls (what was most likely said): "silk", "सिल", "सील", "wheel", "field" next to pouch or side = "seal" or "pouch" ("3-5 silk out" = probably "three side seal pouch", "stand up field" = "standup pouch"); "power pouch", "south pouch", "पाव टों" = probably "spout pouch"; "पाउट", "पौच", "पाउच" = "pouch"; "rescue", "SQ" next to a number = probably "per month" / "MOQ" / "pieces"; "माई" after a number = "minute" or "मई", ask; a place, person or religious word that doesn't fit the sentence (e.g. "Masjid") is usually a misheard filler like "मतलब": ignore it or ask. Use this list only to make a better guess for your question, never to state the detail as fact.
- NEVER say "ओह", "oh", "OK", "ओके", "okay", "got it", "yeah", "cool", "awesome" or "no worries": they sound foreign and robotic on an Indian business call. With an English-speaking customer use "sure", "right", "I see" instead.
- Show you listened: now and then repeat the key detail back in a few words before moving on ("पाँच हज़ार pouches हर महीने, समझ गई।").
- Keep every reply SHORT: at most TWO short sentences and about 20 words (a busy owner hangs up on long speeches). One idea per turn: never stack a reaction, an explanation, an offer and a question in one reply — the offer can wait for the next turn. Usually end with one easy question so the customer talks next.
{thank}
- Ask only ONE question per reply, casually and in your own words, the way people talk ("और ये pouches लगभग कितने चाहिए होंगे, हर महीने?"), never like reading a form. If a listed question asks two things, ask them on separate turns.
- Never repeat your own earlier sentence word for word: if you must ask something again, say it differently.
- Put the most important words first: your first few words are spoken while the rest is still being generated.
- Everyday spoken words, not formal or bookish Hindi: say "बताइए", "ज़रूर", "थोड़ा", "काम", "मदद", "बता देती हूँ" — not "कृपया", "उपलब्ध", "सहायता", "प्रदान", "हेतु", "अवश्य", "आपकी query". Never use call-centre phrases like "मैं आपकी कैसे सहायता कर सकती हूँ", "I understand your concern" or "आपके धैर्य के लिए धन्यवाद".
- Be respectful: "आप" and "जी" always. Use the customer's name very rarely (at most once more in the whole call), always as "name जी". Say "हम" for the company and "मैं" for yourself.
{busy}
- If you could not understand them, ask naturally: "माफ़ कीजिए, आवाज़ थोड़ी cut हो गई, एक बार फिर बताएँगे?"
- If they only say "hello?" or ask whether you are there, answer that first in a few words ("जी, मेरी आवाज़ आ रही है?") and then repeat your last question in different words; don't jump ahead.
- Punctuation shapes how your voice sounds: put a comma where a person would pause for breath, "।" at the end of a Hindi sentence, "?" on questions, "!" only for real warmth (at most one per reply). Use "..." very rarely, only for a soft thinking pause.
- Numbers: write them as spoken words ("पाँच हज़ार pieces", "दो हफ़्ते", "साढ़े तीन बजे"). NEVER say a phone number, WhatsApp number, GST number, pincode, order id or any other long number aloud — not even to confirm it. If they say their WhatsApp is on this same number, just say "जी, हमारी team इसी number पर WhatsApp कर देगी" (English: "Sure, our team will send it on this same number"). If they give a different number, say "जी, note कर लिया" without repeating it.
- Never use lists, bullet points, emojis, markdown, brackets, URLs or symbols (except the end-call marker).
- NEVER RECITE A LIST (critical: a list read aloud takes 15 seconds and the customer stops listening). The lists in your business knowledge and questions are for YOU to look things up. When they ask which pouch types or products you have — even if they say "all of them" — name only TWO or THREE (the ones your knowledge suggests for their product if you know it, otherwise the common ones), joined by commas inside one normal sentence, then ask which one they want. Give more names only if they ask again.

# Stay in context
- Only talk about {company}, packaging, and this call's purpose. If asked something unrelated, answer in a few words at most and steer back politely.
- FACTS: tell customers only what the business knowledge below says (MOQ, the sample kit and its shipping charge, which pouch suits which product). Never invent prices, discounts, delivery dates or policies. Prices are always per piece (pcs), never per kg, and you never say a rate yourself: the exact per-piece quote comes from our team.
- DEFLECTION: for anything the business knowledge doesn't cover — an exact rate, a discount, an unusual size or spec, a material or finish, a delivery date, a product not on the list, machines, factory capacity, clients — never guess. Say warmly, in ONE short sentence and in your own words, that you can't confirm that exact detail right now but our team will cover it with them, then move on. Vary the wording every time (e.g. "ये exact detail मैं अभी confirm नहीं कर पाऊँगी, हमारी team आपको बता देगी।", "इसके बारे में हमारी team आपसे detail में बात कर लेगी।", "That exact detail our team will confirm with you."); never repeat the same line in a call.
- Never confirm a time, a call-back or any next step the customer has not clearly agreed to: ask, wait for their yes, then confirm it once.
- ORDERS: you cannot take, book, place or confirm an order, and you never agree a quantity, price or delivery with them. If they want to go ahead ("एक हज़ार रख लो", "order कर दो"), welcome it warmly and say our team will call to finalise the order and the exact quote — e.g. "बढ़िया जी! हमारी team आपको call करके order और exact quote finalise कर देगी।". NEVER say "order confirm कर लिया", "order रख लिया", "order book कर दिया", "order note हो गया" or "I've placed your order".
- WHO DOES IT: you are only on the phone. You cannot send WhatsApp messages, details, catalogues, quotes or the sample kit, and you cannot book or schedule a call. Our team does all of that after this call, so every promise names our team as the one who does it: "हमारी team आपको WhatsApp पर details भेज देगी", "हमारी team आपको sample kit की details भेज देगी", "हमारी team आपको कल ग्यारह बजे call करेगी" (English: "our team will send you the details on WhatsApp", "our team will call you tomorrow at eleven"). Never promise that YOU will send, share, schedule or arrange something.
- You cannot see orders, quotes, samples, payments or deliveries. Never say an order is confirmed, placed, in production, dispatched or ready, and never guess a status. If they ask where their order or enquiry stands, say what was discussed in your own words ("आपने पाँच सौ pouches की बात की थी") and that our team will confirm the exact status, and offer a call back.
- If asked whether you are a real person, say honestly that you are {company}'s AI assistant, and offer a call back from a human expert.
- If the customer is busy, ask for a convenient time to call back, confirm it, and end.
- NO MORE CALLS: once the customer says they don't want to be called (e.g. "मत call करना", "don't call me"), never offer, suggest or ask about a call back again in this call. Say sorry once, then answer their question now if the business knowledge covers it, or offer that our team sends the details on WhatsApp instead. If they don't want that either, thank them and close politely.

# Remembering the customer
The next message gives the current time (IST), the customer on this call and what we already know about them from earlier calls. Use it like a person who remembers, not like a database:
- Mention at most ONE relevant earlier detail, naturally and only when it helps ("पिछली बार आपने rice के pouches की बात की थी ना?"). Never read the memory out like a list.
- Confirm instead of re-asking what you already know ("अभी भी पाँच हज़ार pouches हर महीने चाहिए?"), and skip questions that are already answered.
- If the memory shows "The call that just dropped", the line dropped a few minutes ago: carry on from where it stopped (a short "जी, line drop हो गई थी" is fine), don't start over.
- If you have spoken with them before, don't introduce {company} again and don't pitch from scratch: continue from the last conversation (this overrides any introduction step in the call flow above).
- The newest information wins over older lines and team notes. If details conflict or you are not sure, ask.
- The memory is short notes from earlier calls, not an order system: repeat only what it says, never make it sound more certain than it is (an enquiry or "yes, I still need it" is not a confirmed order).
- Never mention dates or times as data, ratings, scores, internal labels (hot, warm, cold, lead, campaign, source), how many times we called, missed or cut calls, earlier refusals or objections (unless they bring them up first), internal team notes, or their email, address or GST number (confirm one only if the customer says it first).
- Recordings: don't bring them up, but if they ask whether the call is recorded, answer honestly.
- If the last conversation was more than about two months ago, check that the need is still current. If a call-back we promised has passed with no later call, apologise once and fix a new time. Promise call-backs only at sensible business hours ({window}), never late at night.
- If you don't know their name, ask politely early ("{ask_name}") and repeat it back once ("Ramesh जी, thank you"). If their number is hidden or international, also ask for a WhatsApp or phone number.{privacy}

# Qualification questions (ask naturally, ONE at a time, skip what is already answered, don't sound like a form)
{questions}

# Objection handling
{cfg.get('objection_handling', '')}

# Business knowledge
{cfg.get('knowledge_base', '')}
{learned_block}
# Ending the call
{ending}
"""


_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_ENGLISH_STYLE = (" At most TWO short sentences (about 20 words) and one question. Vary your openers and don't "
                  "start every reply with 'Great!' or 'Sure!'; never say 'Got it', 'Okay', 'Cool' or 'Awesome' "
                  "(say 'Right', 'I see' or 'Sure').")
ENGLISH_TURN_NOTE = ("The customer just spoke in English: reply in simple, warm Indian English this turn "
                     "(no Hindi sentences)." + _ENGLISH_STYLE)


# Hindi typed in Roman letters (browser tests, other STT modes): such a turn is Hinglish, not English.
_ROMAN_HINDI = {
    "hai", "hain", "kya", "kitna", "kitne", "kitni", "aap", "aapka", "aapki", "aapke", "nahi", "nahin", "haan",
    "han", "ji", "mein", "mai", "main", "ka", "ki", "ke", "ko", "se", "hum", "hamara", "hamare", "humko", "mujhe",
    "chahiye", "bhi", "toh", "theek", "thik", "accha", "acha", "achha", "karo", "kar", "karna", "bolo", "boliye",
    "batao", "bataiye", "ho", "raha", "rahe", "rahi", "hoon", "hu", "kab", "kaise", "kaun", "kyun", "abhi", "baad",
    "lekin", "aur", "matlab", "wala", "wale", "sab", "kuch", "yeh", "ye", "woh", "wo", "tha", "thi",
}


def spoke_english(text: str) -> bool:
    """Speech recognition writes Hindi in Devanagari, so a turn of 3+ words with no Devanagari at all
    (and no Hindi typed in Roman letters) means the customer actually spoke English."""
    words = [w.lower() for w in re.findall(r"[A-Za-z']+", text)]
    return len(words) >= 3 and not _DEVANAGARI.search(text) and not any(w in _ROMAN_HINDI for w in words)


ENGLISH_PREFERRED_NOTE = ("The customer asked you to speak English: reply ONLY in simple, warm Indian English (not "
                          "one Hindi word, even though the earlier conversation was in Hindi) for the rest of the "
                          "call, short answers and the goodbye included, until they ask for Hindi." + _ENGLISH_STYLE)
ENGLISH_AUTO_NOTE = ("The customer is talking to you in English: reply ONLY in simple, warm Indian English (not one "
                     "Hindi word), even to short answers like 'yes' or 'ok', until they speak Hindi again." + _ENGLISH_STYLE)

# The fixed lines of an English call (the Hindi ones come from Settings). "closing" is said at the maximum
# call length; "error" when the line fails. Templates: fill_template() fills {company_name} before use.
ENGLISH_LINES: dict[str, str] = {
    # calm: "Hello? Are you there? Can you hear me?" came out loud and stretched on a live call
    "silence_prompt": "Hello, are you still on the line?",
    "silence_goodbye": ("I think the line isn't clear, so I'll end the call now. Thank you from {company_name}, "
                        "have a good day."),
    "closing": ("I won't take more of your time, it was nice talking to you! Whenever you need packaging, feel free "
                "to reach out to {company_name}."),
    "inbound_closing": ("Thank you for calling {company_name}! Our packaging expert will call you back with the "
                        "details. Have a great day!"),
    "error": "Sorry, there's a problem on the line. Our team will call you back soon. Thank you!",
    "cut": "Sorry, I couldn't hear that clearly. Could you say it again?",
    "hello_reply": "Yes, I'm here. Can you hear me?",
    "hear_you": "Yes, I can hear you. Please go ahead.",
}
# Soft "I'm listening" sounds while the customer talks for a long time.
LISTENING_SOUNDS = {"hindi": ["हम्म।", "जी।", "अच्छा।"], "english": ["Mm-hmm.", "Right."]}
# Fillers for English calls (the Hindi ones would sound odd there). None of them claims to have understood.
ENGLISH_FILLERS = ["Hmm", "Right", "Sure", "Hmm, one second"]


def spoke_hindi(text: str) -> bool:
    """A real Hindi sentence (3+ Devanagari words, not just "हाँ जी"): ends an English stretch the agent
    switched to on its own."""
    words = [w for w in re.findall(r"\S+", text) if _DEVANAGARI.search(w)]
    return len(words) >= 3


# ---------------------------------------------------------------- quick replies without the LLM
# "Hello? Hello?" mid-call means the customer isn't sure we are still there: answer at once, in our voice,
# instead of carrying on with the script. Asked whether we can hear THEM: say so.
HELLO_REPLY = {"hindi": "जी, मेरी आवाज़ आ रही है?", "english": "Yes, I'm here. Can you hear me?"}
HEAR_YOU_REPLY = {"hindi": "जी, आपकी आवाज़ आ रही है, बताइए।", "english": "Yes, I can hear you. Please go ahead."}
_HELLO_WORDS = {"hello", "helo", "hallo", "hullo", "hi", "हेलो", "हैलो", "हेल्लो", "हलो", "हल्लो", "हेल्लो"}
_HELLO_EXTRA = {"जी", "ji", "sir", "madam", "maam", "सर", "मैडम", "भैया", "हाँ", "हां", "haan", "are", "अरे"}
_THERE_PHRASES = ("are you there", "you there", "anyone there", "कोई है", "हो क्या लाइन पर", "line पर हो",
                  "लाइन पर हो", "line पर हैं", "लाइन पर हैं")
_HEAR_ME_PHRASES = ("can you hear me", "are you able to hear me", "मेरी आवाज़ आ रही", "मेरी आवाज आ रही",
                    "सुनाई दे रहा", "सुनाई दे रही", "मेरी बात सुन")


def hello_check(text: str) -> str | None:
    """"hello" when the customer is checking whether we are there ("Hello? Hello?", "are you there?"),
    "hear_me" when they ask whether we can hear them, else None (a normal turn for the LLM)."""
    t = " ".join(text.lower().split())
    words = [w for w in re.split(r"[\s,.!?।…'\"-]+", t) if w]
    if not words or len(words) > 8:
        return None
    if any(p in t for p in _HEAR_ME_PHRASES) and "नहीं" not in t and "not" not in words:
        return "hear_me"
    if any(p in t for p in _THERE_PHRASES):
        return "hello"
    if len(words) <= 4 and all(w in _HELLO_WORDS | _HELLO_EXTRA for w in words) and any(w in _HELLO_WORDS for w in words):
        return "hello"
    return None


# ---------------------------------------------------------------- the customer's mood
# Read from their words (speech recognition gives text, not tone). Irritated and busy stay for the rest of
# the call unless they warm up; "brief" = several answers of a word or two.
_IRRITATED = ("परेशान", "बार बार", "बार-बार", "disturb मत", "मत करो", "मत कीजिए", "call मत", "कॉल मत",
              "don't call", "stop calling", "irritat", "दिमाग", "फालतू", "बकवास", "bakwas", "गुस्सा",
              "waste of time", "time waste", "टाइम वेस्ट", "pagal", "पागल", "नहीं चाहिए बोला", "बोला ना",
              "कितनी बार", "how many times")
_BUSY = ("busy", "बिज़ी", "बिजी", "meeting", "मीटिंग", "driving", "ड्राइव कर", "गाड़ी चला", "बाद में",
         "later", "जल्दी बोलो", "जल्दी बताओ", "जल्दी बताइए", "time नहीं", "टाइम नहीं", "समय नहीं",
         "in a hurry", "quickly", "short में", "काम में हूँ", "काम में हूं")
_FRIENDLY = ("हाहा", "haha", "hehe", "बढ़िया", "wonderful", "lovely", "अच्छा लगा", "thank you so much",
             "बहुत अच्छा", "वाह", "wow", "great to hear", "मज़ा", "खुशी")
MOOD_NOTES = {
    "irritated": ("The customer sounds irritated: be extra calm and polite, say sorry once for the disturbance, "
                  "reply in ONE short sentence, don't push or pitch; offer to call at a better time or end politely."),
    "busy": ("The customer seems busy: ONE short sentence, straight to the point, and offer a quick call back at "
             "a time that suits them."),
    "friendly": ("The customer is friendly and relaxed: you may sound a little warmer and more personal (a short "
                 "friendly remark is fine), still short."),
    "brief": "The customer answers in very few words: keep your replies very short and easy to answer.",
}
# Voice pace for a mood: calmer for an irritated customer, a touch quicker for a busy one.
MOOD_PACE = {"irritated": 0.96, "busy": 1.05}


def detect_mood(text: str) -> str | None:
    t = " ".join(text.lower().split())
    if any(p in t for p in _IRRITATED):
        return "irritated"
    if any(p in t for p in _BUSY):
        return "busy"
    if any(p in t for p in _FRIENDLY):
        return "friendly"
    return None
# "Don't call me" (call 45: "अरे मत call करना आप।", then the agent asked "आपको कब call करूँ?" and lost them).
# Once said, every later reply of the call gets NO_CALLS_NOTE: a rule far up the prompt was not enough.
_NO_CALLS = re.compile(
    r"मत\s+(?:call|कॉल|काल|phone|फ़ोन|फोन)"  # not "नहीं call": "मैंने नहीं call किया" is a wrong-number caller
    r"|(?:call|कॉल|काल|phone|फ़ोन|फोन)\s+(?:मत|ना\s+कर|नहीं\s+(?:करना|करो|कीजिए|चाहिए))"
    r"|(?:call|कॉल)\s*back\s+(?:मत|नहीं)"
    r"|\b(?:don'?t|do\s+not|never|stop)\s+(?:call|calling|phone|ring)\b"
    r"|\bno\s+(?:more\s+)?(?:calls?|call\s*backs?)\b",
    re.I)
NO_CALLS_NOTE = ("The customer said they don't want to be called. For the rest of this call: never offer, suggest "
                 "or ask about a call back or a time to call. Answer their question now if the business knowledge "
                 "covers it, otherwise offer that our team sends the details on WhatsApp; if they don't want that "
                 "either, thank them and end the call.")


# Words phone speech recognition produced for something else on real calls (call 49: "3-5 silk out" was "three
# side seal pouch", "1 माई" was "एक मिनट", "Masjid" was "मतलब"; calls 19 / 21 / 37: "silk", "field", "wheel",
# "power pouch", "rescue", "SQ"). Such a turn gets MISHEARD_NOTE: the prompt rule alone was not followed reliably.
_DV = "ऀ-ॣ०-९ॱ-ॿ"  # Devanagari letters and digits, not the danda "।" / "॥" (a word may end there)
_MISHEARD: list[tuple[re.Pattern, str]] = [(re.compile(p, re.I), meant) for p, meant in (
    (rf"\bsilk(?:\s*out)?\b|सिल्क(?:\s*आउट)?|(?<![{_DV}])सिल(?![{_DV}])|\bwheel\b|व्हील", '"seal" (three side seal / center seal pouch)'),
    (r"\bfield\b|फील्ड", '"pouch" (e.g. standup pouch)'),
    (r"\b(?:power|south)\s+pouch|पावर\s+पाउच|साउथ\s+पाउच|पाव\s+टों", '"spout pouch"'),
    (r"\brescue\b|\bSQ\b", '"per month", "MOQ" or "pieces"'),
    (rf"(?:\d|(?<![{_DV}])(?:एक|दो|तीन|चार|पाँच|पांच))\s*माई(?![{_DV}])", '"minute" (एक मिनट = wait) or the month मई: ask which'),
    (r"\bmasjid\b|मस्जिद|\bmandir\b|मंदिर", 'a filler like "मतलब": ignore it unless it clearly fits'),
)]
MISHEARD_NOTE = ("Speech recognition probably misheard part of the customer's last message: {words}. Do not treat "
                 "that part as a fact, do not build a number, date, pouch type or name on it, and do not quote the "
                 "odd words back. Ask ONE short question with the likely meaning instead (e.g. \"आपने three side "
                 "seal pouch कहा?\", \"एक minute रुकना है, या एक month में चाहिए?\"), and answer the rest of their "
                 "message normally.")


def misheard_note(text: str) -> str | None:
    """The note for a customer turn that contains a typical speech-recognition mistake, else None."""
    found = [f'"{m.group(0)}" = probably {meant}' for rx, meant in _MISHEARD for m in [rx.search(text)] if m]
    return MISHEARD_NOTE.format(words="; ".join(found)) if found else None


def asked_no_calls(text: str) -> bool:
    """The customer asked not to be called ("मत call करना", "call मत करो", "don't call me")."""
    return bool(_NO_CALLS.search(" ".join(text.lower().split())))


# A request needs an ask word next to the language: "English labels chahiye" mentions English, it doesn't ask.
# (Devanagari words get explicit edges: \b treats vowel signs as non-word, and "में" hides inside "हमें")
_LANG_ASK = re.compile(r"\b(speak|talk|tell|say|reply|answer|continue|explain|please|can|could|bol\w*|baat|me|mein|mai)\b"
                       r"|(?<![ऀ-ॿ])(बोल|बात|समझा|स्पीक|टॉक|प्लीज़?)|(?<![ऀ-ॿ])में(?![ऀ-ॿ])", re.I)
# A very short message that is only the language plus these words is a request too ("In English", "English only",
# "English sir"); a longer one needs an ask word ("English labels" is about labels).
_SHORT_ASK_WORDS = {"in", "only", "sir", "madam", "maam", "ma'am", "ji", "जी", "इन", "ओनली", "sir.", "language",
                    "लैंग्वेज", "please", "plz", "pls", "हिंदी", "हिन्दी", "इंग्लिश", "english", "hindi", "अंग्रेज़ी",
                    "अंग्रेजी"}
_ENGLISH_WORD = re.compile(r"\benglish\b|इंग्लिश|अंग्रेज", re.I)  # अंग्रेज: अंग्रेज़ी / अंग्रेजी, with or without nukta
_HINDI_WORD = re.compile(r"\bhindi\b|हिंदी|हिन्दी", re.I)


def language_request(text: str) -> str | None:
    """"english" / "hindi" when the customer asks the agent to switch language ("Can you speak in English?",
    "हिंदी में बोलिए"), else None. The session keeps it for the rest of the call."""
    english, hindi = bool(_ENGLISH_WORD.search(text)), bool(_HINDI_WORD.search(text))
    if english == hindi:
        return None
    words = [w for w in re.split(r"[\s,.!?।]+", text.lower()) if w]
    short = 0 < len(words) <= 4 and all(w in _SHORT_ASK_WORDS for w in words)
    if not (short or _LANG_ASK.search(text)):
        return None
    return "english" if english else "hindi"


def _number_line(lead: dict[str, Any], direction: str) -> str | None:
    phone = str(lead.get("phone") or "").strip()
    kind = lead.get("caller_type")
    if kind == "anonymous" or (direction == "inbound" and not phone.startswith("+")):
        return "Number: hidden (ask for a WhatsApp or phone number)"
    if not phone:
        return None  # a browser test with a typed name
    if kind == "international" or (phone.startswith("+") and not phone.startswith("+91")):
        return "Number: an international number (ask for a WhatsApp number; never say any number aloud)"
    # the digits are never given: an agent that has them reads them back, digit by digit (robotic, and private)
    return "Number: their Indian mobile (WhatsApp usually on the same number; never say any number aloud)"


def lead_context(lead: dict[str, Any] | None, memory: str = "", *, direction: str = "outbound",
                 now: datetime | None = None) -> str:
    """Per-call details, sent right after the static prompt: the current time (IST), who is on the line and
    what we remember about them (services/memory.py). Changes every call, so it never goes into
    static_system_prompt (that would break prompt caching)."""
    lead = lead or {}
    lines = [
        now_line(now),
        "",
        "# Customer on this call",
        f"Name: {str(lead.get('name') or '').strip() or 'not known yet'}",
        f"Company: {str(lead.get('company') or '').strip() or 'not known yet'}",
        f"City: {str(lead.get('city') or '').strip() or 'not known yet'}",
    ]
    number = _number_line(lead, direction)
    if number:
        lines.append(number)
    notes = str(lead.get("notes") or "").strip()
    # Team notes only on our own calls: an inbound caller's identity rests on an unsigned caller ID. Once
    # only: the memory already carries them unless it was cut for space or isn't ready.
    if direction != "inbound" and notes and NOTES_LABEL not in (memory or ""):
        lines.append(f"{NOTES_LABEL} {redact_private(' '.join(notes.split()))[:600]}")
    if memory:
        lines += ["", memory.strip()]
    return "\n".join(lines)


def build_system_prompt(cfg: dict[str, Any], lead: dict[str, Any] | None = None, *, direction: str = "outbound",
                        purpose: str | None = None, memory: str = "") -> str:
    """Full prompt as one string (static part + per-call details)."""
    return static_system_prompt(cfg, direction, purpose) + "\n" + lead_context(lead, memory, direction=direction)


def build_analysis_prompt(cfg: dict[str, Any], lead: dict[str, Any] | None, transcript: str,
                          direction: str = "outbound") -> list[dict]:
    inbound = direction == "inbound"
    company = cfg.get("company_name") or "our company"
    agent = (cfg.get("agent_name") or "").strip()
    not_us = f"'{company}'" + (f" or '{agent}'" if agent else "")
    questions = "\n".join(f"- {q}" for q in cfg.get("qualification_questions") or [])
    tags = ", ".join(f'"{k}" ({v})' for k, v in OBJECTION_TAGS.items())
    if inbound:
        direction_rules = (
            "This was an INBOUND call: the customer chose to call us.\n"
            "- Never rate them cold just because the call was short, or because it was about an existing order, a "
            "sample, a delivery, a payment or a complaint. Use \"unknown\" when buying intent wasn't discussed.\n"
            "- \"cold\" only when they clearly have no packaging need (e.g. a vendor, a job seeker or a wrong "
            "number). The \"picked up and cut the call\" rule is for outbound calls only.\n\n"
            "Rating: integer 1-10 lead quality score (10 = ready to buy now with a large, clear requirement; 7-9 "
            "hot; 4-6 warm; 1-3 cold). null if the customer never spoke or buying intent wasn't discussed."
        )
        reason = (" | ".join(f'"{r}"' for r in CALL_REASONS)
                  + " (why they called; callback_of_our_call = they were returning a call or missed call from us)")
    else:
        direction_rules = (
            'Use "unknown" only if the customer never actually spoke.\n\n'
            "Rating: integer 1-10 lead quality score (10 = ready to buy now with a large, clear requirement; 7-9 "
            "hot; 4-6 warm; 1-3 cold). null only if the customer never spoke."
        )
        reason = "null (outbound call)"
    system = f"""You are a sales-operations analyst for {company}. You review transcripts of AI phone calls — outbound qualification calls and inbound calls from customers — and score each customer so the human sales team only spends time on valuable leads.

The CUSTOMER lines come from phone speech recognition and can contain misheard words ("3-5 silk out" for "three side seal pouch", "Masjid" for "मतलब"). Report a detail (quantity, pouch type, date, name, company, city) only when it is clear in the transcript or the customer confirmed it; when the agent had to ask again, use the customer's answer to that question; never report a garbled word as a fact.

Interest categories (pick exactly one):
{cfg.get('interest_rules', '')}
{direction_rules}

Qualification questions:
{questions}

Return ONLY a JSON object with exactly these keys:
{{
 "customer_name": "the customer's own name exactly as they said it, in Roman (English) letters, e.g. 'Ramesh Patel'; null if they didn't clearly say it (never {not_us}, never just a title like sir, madam or bhaiya)",
 "customer_company": "their business or brand name as they said it, in Roman letters; null if it was unclear, the agent had to ask again and never got a clear answer, or it is an ordinary word the speech recognition may have misheard",
 "customer_city": "the city where their business is (not a delivery destination), in Roman letters; only a real Indian city or town they clearly named as theirs (a stray word like 'Masjid' in the transcript is speech recognition noise, not a city), else null",
 "customer_phone": "a phone or WhatsApp number they gave that is different from the number of this call, digits only, else null",
 "call_reason": {reason},
 "promised_to_customer": "what WE promised the customer, short English (e.g. 'Expert will call with an exact quote tomorrow 11 am'), else null",
 "summary": "2-3 sentence English summary of the call for the sales team. Report only what was actually said: interest, an enquiry or 'yes, I still need it' is NOT an order — never write that an order was confirmed or placed unless the customer clearly said they placed one with our team",
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
        f"Call direction: {'inbound' if inbound else 'outbound'}\n"
        f"Customer on record: {lead.get('name') or 'unknown'} | {lead.get('company') or ''} | {lead.get('city') or ''}\n\n"
        f"Transcript (AGENT = our AI agent, CUSTOMER = the person on the call):\n{transcript}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
