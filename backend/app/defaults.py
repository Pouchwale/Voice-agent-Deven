"""Default, editable agent configuration (Settings page writes over these).

Facts about PouchWale come from the PouchWale team (MOQ, per-piece pricing, sample kit, which pouch for
which product) and the brand kit. Anything not written in the knowledge base (exact rates, delivery dates,
unusual sizes or finishes) is never quoted: the agent says the team will cover it.
"""
from .defaults_legacy import V4_TEXTS, V6_TEXTS

# How the call-review agent judges a call (Settings -> Review agent, editable). The review code adds the
# evidence (numbered transcript turns T#, timing facts F# from the recording) and the answer format.
REVIEW_RUBRIC = """\
Goal: find where and why our voice agent lost the customer's interest, with proof. A customer who simply
isn't a fit is not the agent's fault, and a normal reason to end a call is not a finding.

Lost because of the agent (needs proof): the customer was engaged or neutral, and right after a specific
agent turn their engagement dropped. They hung up during or just after it, got curt or annoyed, had to repeat
themselves, said they weren't understood, or stopped answering, and that agent turn did something wrong.

Not the agent's fault (report as the customer's side, never count against the agent): not interested before
hearing any pitch; wrong number or wrong person; busy, call later; already has a supplier and firmly declines;
a real price / MOQ / product mismatch the agent handled politely; the customer's own bad line; a test call.

Agent mistakes (category: what it looks like):
- too_long: a reply far longer than needed (about 25+ words, several sentences) or a monologue.
- many_questions: two or more questions in one reply.
- ignored_question: the customer asked something and the reply didn't answer it.
- repeated_question: asked for something the customer had already told us.
- pushy: kept pitching after a clear no, or rushed them to a next step.
- wrong_info: said something the knowledge base doesn't say, contradicted it, promised what we don't offer,
  or claimed an order / booking was confirmed.
- misheard: replied as if the customer had said something else.
- unnatural: robotic, formal or scripted wording, numbers read out, the wrong language for this customer.
- slow_reply: the customer waited too long for an answer (proof: a response gap from the recording).
- talked_over: the agent spoke over the customer (proof: an overlap from the recording).
- weak_opening: the greeting or first pitch is where they disengaged.
- no_next_step: an interested customer was let go without a clear next step.
- other: anything else, explained.

Proof rules: every finding names the agent turn (T#) and copies an exact quote from it, and/or cites a timing
fact (F#) from the recording. No proof, no finding. Never guess what the customer felt or meant beyond what
they said and did. Also note what worked well, with the same kind of proof.
"""

DEFAULT_AGENT_SETTINGS: dict = {
    # ---------------- identity ----------------
    "agent_name": "",  # optional personal name; empty = introduce only the company
    "company_name": "PouchWale",
    "agent_gender": "female",
    "voice": "suhani",  # Bulbul v3 female speaker chosen by the client (sweet, natural Hindi)
    "language_code": "hi-IN",
    "tts_pace": 1.0,         # Sarvam: 1.0 = natural pace for conversational agents
    "tts_temperature": 0.7,  # Sarvam: 0.7-0.8 = warm, conversational (lower = steadier, flatter)
    # How the voice should say words it would otherwise mispronounce ("written = spoken"). Applied to
    # the audio only; transcripts keep the original spelling. Brand names in Devanagari sound native.
    # Hindi calls use the Devanagari spellings; English calls skip those and use the Latin ones below them.
    "pronunciations": [
        "PouchWale = पाउचवाले",
        "GP3 = जी पी थ्री",
        "MOQ = एम ओ क्यू",
        "D2C = डी टू सी",
        "GST = जी एस टी",
        "ISO = आई एस ओ",
        "GMP = जी एम पी",
        "BOPP = बी ओ पी पी",
        "LDPE = एल डी पी ई",
        "gsm = जी एस एम",
        "kg = किलो",
        "Mehsana = मेहसाणा",
        "EcoVadis = इको वैडिस",
        "info@pouchwale.com = इन्फो at पाउचवाले dot com",
        "₹199 = one ninety nine रुपये",
        "3-side seal = थ्री साइड सील",
        "3 side seal = थ्री साइड सील",
        "three side seal = थ्री साइड सील",
        "side seal = साइड सील",
        "center seal = सेंटर सील",
        "centre seal = सेंटर सील",
        "quad seal = क्वाड सील",
        "stand-up = स्टैंड अप",
        "standup = स्टैंड अप",
        "spout = स्पाउट",
        "PouchWale = Pouch Waale",
        "GP3 = G P 3",
        "MOQ = M O Q",
        "D2C = D to C",
        "GST = G S T",
        "BOPP = B O P P",
        "LDPE = L D P E",
        "gsm = G S M",
        "kg = kilo",
        "info@pouchwale.com = info at Pouch Waale dot com",
        "₹199 = one ninety nine rupees",
        "3-side seal = three side seal",
        "3 side seal = three side seal",
    ],

    # ---------------- call script ----------------
    # Permission-first opener (~4 s): short and polite, asks for time before pitching.
    "opening_line": "नमस्ते {lead_name_ji}! मैं {company_name} से बोल रही हूँ। बस दो minute लूँगी, अभी बात कर सकते हैं?",
    "call_purpose": (
        "Outbound call to a business lead. Find out if they need custom printed flexible packaging (pouches), "
        "qualify them, and set up the next step.\n"
        "Call flow:\n"
        "1) The opening line already asked if they have two minutes.\n"
        "2) If yes: in ONE short sentence say why you called, e.g. \"हम custom pouch बनाते हैं — जैसे namkeen, "
        "dry fruits, spices के packets होते हैं, वैसे आपके brand के लिए custom printed\", then ask the first "
        "qualification question.\n"
        "3) If busy / call later: ask for a convenient time, confirm it, end politely.\n"
        "4) If not interested or wrong person: thank them and end politely.\n"
        "5) Ask the qualification questions one at a time; once you know their product, suggest the right pouch "
        "from the product list in the business knowledge. Then offer the next step: a call back from our team "
        "with an exact per-piece quote, details on WhatsApp, or the free sample kit (only one ninety nine "
        "rupees shipping) — suggest the sample kit especially if they are undecided."
    ),
    # The company facts the agent may tell customers (from the PouchWale team). Anything not written here is
    # never invented: the agent says the team will cover it (see the deflection rule in the prompt).
    "knowledge_base": (
        "Company: PouchWale (pouchwale.com), Gujarat, India. Contact: info@pouchwale.com.\n"
        "What we do: custom printed pouches for brands. Say it like: \"हम custom pouch बनाते हैं — जैसे namkeen, "
        "dry fruits, spices के packets होते हैं, वैसे आपके brand के लिए custom printed.\"\n"
        "Why us: low MOQ and fast turnaround.\n"
        "MOQ: starts from 1,000 pieces (\"MOQ एक हज़ार pieces से start होता है\").\n"
        "Price / rate: always quoted PER PIECE (pcs), NEVER per kg. There are no fixed rates to tell: our team "
        "shares the exact per-piece quote once they know the size, quantity and print.\n"
        "Size / dimensions: the customer gives us the pouch size they need, and our team also helps them choose "
        "the right size.\n"
        "Free sample kit: the kit is free, the customer pays only ₹199 shipping (\"Sample kit free है, बस one "
        "ninety nine rupees shipping charges लगते हैं\"). The shipping charge is exactly one hundred and ninety-nine "
        "rupees: always say it as \"one ninety nine rupees\", in Hindi calls too. Our team sends the sample kit "
        "details on WhatsApp after the call. Suggest it to customers who are undecided or comparing.\n"
        "Certifications: GMP, ISO 9001, ISO 14001, ISO 45001, EcoVadis Silver 2024.\n"
        "\n"
        "Which pouch for which product (suggest these; for a product not listed, the team advises):\n"
        "- Milk powder, beverage mixes, hot chocolate: 3-side seal pouch or center seal pouch\n"
        "- Coffee, tea: quad seal bag\n"
        "- Rice (premium / basmati): quad seal bag or center seal pouch\n"
        "- Nuts, dry fruits, cashews: standup pouch\n"
        "- Namkeen, snacks, popcorn, bakery: standup pouch, 3-side seal pouch or center seal pouch\n"
        "- Chocolates, candy: 3-side seal pouch or standup pouch\n"
        "- Grains, flour: center seal pouch\n"
        "- Spices, seasonings, masalas: standup pouch, sachets or center seal pouch\n"
        "- Paneer, tofu, dairy products: vacuum / high-barrier pouch\n"
        "- Frozen or fried foods: freeze-grade pouch\n"
        "- Beverages, juices: spout pouch\n"
        "- Sauces, ketchup: spout pouch\n"
        "- Nutritional supplements: standup pouch or center seal pouch\n"
        "- Cosmetics, beauty: special shape pouch or standup pouch; 3-side seal pouch for items like face masks\n"
        "- Pet food (dog food, cat food): standup pouch or center seal pouch\n"
        "- Seeds, fertilizers, agro chemicals: center seal pouch, standup pouch or quad seal bag\n"
        "- Khakhra, papad: standup pouch or 3-side seal pouch\n"
        "- Medicines, pharmaceuticals, ayurvedic supplements: center seal pouch or standup pouch\n"
        "- Mukhwas, supari, mouth freshener: standup pouch (retail) or sachet (small packs)\n"
        "- Ready-to-eat / instant meals: standup pouch or 3-side seal pouch\n"
        "- Oats, granola, breakfast cereals: standup pouch"
    ),
    # Written in Devanagari + English words: the agent copies this style, and Bulbul pronounces
    # Devanagari Hindi far more naturally than Hindi typed in Roman letters.
    "qualification_questions": [
        "आप किस product के लिए packaging ढूँढ रहे हैं? (product category)",
        "अभी कौन सी packaging use कर रहे हैं, और कौन सा pouch type चाहिए?",
        "लगभग कितनी quantity चाहिए — per order या per month?",
        "Packaging कब तक चाहिए? (timeline / urgency)",
        "क्या packaging का decision आप लेते हैं, या किसी और से भी बात करनी होगी?",
    ],
    "objection_handling": (
        "Use Acknowledge -> Clarify -> Respond -> Advance. Never argue, never disparage competitors.\n"
        "- Price too high / what is the rate: we quote per piece (never per kg); acknowledge, explain value "
        "(print quality, low MOQ, fast turnaround), offer an exact per-piece quote from our team.\n"
        "- Asks the price per kg: say politely that we quote per piece, not per kg, and the team will share it.\n"
        "- MOQ too high / small quantity: our MOQ starts from just 1,000 pieces; ask their quantity per order or "
        "per month.\n"
        "- Already have a supplier: respect it, ask what they would improve, suggest the free sample kit (only "
        "₹199 shipping) to compare.\n"
        "- Undecided / wants to see the quality first: suggest the free sample kit (only ₹199 shipping).\n"
        "- Need it urgently / lead time: we have fast turnaround; ask the date they need it by; the exact delivery "
        "date the team confirms.\n"
        "- Send details / catalogue: agree that our team will send them on WhatsApp, confirm WhatsApp on this "
        "number, ask one qualifying question first.\n"
        "- Call later / busy: ask a convenient time, confirm it, end politely.\n"
        "If they push back twice on the same thing, or ask for a final price, a discount or firm dates, hand off: "
        "offer a call back from our team."
    ),
    "interest_rules": (
        "hot: strong interest — clear packaging need plus a quantity or a timeline within about 30 days, OR asks "
        "for a quote / sample / expert call, OR agrees to order.\n"
        "warm: normal interest — open to hearing more or getting details, but the timeline is later or unclear, "
        "or they need to check internally.\n"
        "cold: picked up and cut the call, did not engage, not interested, no need, wrong person/number, or "
        "asked not to be called."
    ),
    # Said when the call hits its maximum length: wraps up warmly, promises nothing, names the brand last.
    "closing_line": ("अच्छा जी, मैं आपका ज़्यादा time नहीं लूँगी, आपसे बात करके अच्छा लगा! Packaging की कोई भी ज़रूरत "
                     "हो, तो {company_name} को ज़रूर याद कीजिए।"),

    # ---------------- silence handling ----------------
    "silence_timeout_sec": 10,
    # Calm on purpose: "हेलो...?" with drawn-out dots came out as a loud, stretched "हेलोooo?" on a live call.
    "silence_prompt_text": "हेलो, मेरी आवाज़ आ रही है आपको?",
    "silence_final_wait_sec": 3,
    "silence_goodbye_text": "लगता है line पर आवाज़ नहीं आ रही है। कोई बात नहीं, {company_name} की तरफ़ से thank you, आपका दिन अच्छा रहे!",

    # ---------------- conversation feel ----------------
    # Played only when a reply is slow. Neutral on purpose: they play before the reply is known, so
    # none of them may agree ("बिल्कुल") or disagree. "हम्म" / "एक second" = thinking (after a
    # question); the others acknowledge what the customer just said.
    "fillers_enabled": True,
    # None of these claims to have understood: a filler plays BEFORE the reply exists, so "जी, समझ गई" would
    # be said even to words the agent didn't catch.
    # no "हम्म, एक second": a long, drawn-out filler the user disliked (2026-11-24)
    "filler_words": ["हम्म", "जी", "अच्छा", "हाँ जी", "अच्छा जी"],
    "filler_delay_ms": 800,
    "barge_in_enabled": True,
    # Human touches (each can be switched off on its own):
    "caller_memory": True,       # off = every call is treated as a first call (for testing); nothing is deleted
    "fast_replies": True,        # start writing the reply the moment the customer stops talking (~0.4 s sooner)
    "listening_sounds": True,    # a soft "हम्म" / "जी" in the pauses of a long customer sentence
    "natural_variation": True,   # slightly different pace per reply + short natural pauses between sentences
    "mood_matching": True,       # shorter and calmer with a busy / irritated customer, warmer with a friendly one
    "max_call_sec": 300,

    # ---------------- campaign dialing ----------------
    "call_window_start": "09:30",
    "call_window_end": "19:30",

    # ---------------- calling switches ----------------
    "outbound_enabled": True,  # off: "Call now" and campaigns place no calls
    "inbound_enabled": True,   # off: callers hear the unavailable message (or a busy tone), logged as missed

    # ---------------- inbound calls ----------------
    # Every caller who has not spoken with us before ({lead_name_ji} = "Rahul जी" when the number is a lead
    # with a name, else just "जी"). Short on purpose: the caller expects us to speak first, then talks.
    "inbound_greeting": "नमस्ते {lead_name_ji}! {company_name} से बोल रही हूँ, बताइए?",
    # Only for callers who had a real conversation with us before (never claim a talk that didn't happen).
    "inbound_greeting_known": "नमस्ते {lead_name_ji}! {company_name} से बोल रही हूँ, आपसे फिर बात करके अच्छा लगा। बताइए?",
    "inbound_purpose": (
        "Inbound call: the customer called {company_name}'s number and you answered.\n"
        "Call flow:\n"
        "0) If they say they got or missed a call from this number: yes, we had called them. Give the reason "
        "in one short sentence, ask if now is a good time, then continue like an enquiry.\n"
        "1) Find out why they called and help with that first — answer questions about our pouches, "
        "printing, samples or ordering in a few words.\n"
        "2) If you don't know their name yet, ask it politely early on, and their business / product.\n"
        "3) While helping, qualify them naturally with the qualification questions (skip what is already known).\n"
        "4) Once you know their product, suggest the right pouch from the product list in the business "
        "knowledge. Offer the next step: a call back from our team with an exact per-piece quote, details on "
        "WhatsApp, or the free sample kit (only one ninety nine rupees shipping). Confirm a convenient time for "
        "the call back.\n"
        "5) An existing customer asking about an order, sample, delivery or payment, or making a complaint: "
        "apologise if needed, take the details, never guess a status, promise a call back from our team "
        "with a time.\n"
        "6) They want a specific person or a human: take a short message and a convenient call-back time.\n"
        "7) A vendor, a job seeker or a wrong number: answer briefly and close politely.\n"
        "8) If they only wanted to check something: answer, ask once if there is anything else, thank them, end."
    ),
    # Said when an inbound call reaches inbound_max_call_sec.
    "inbound_closing_line": (
        "अच्छा जी, मैं आपकी details हमारे expert को दे देती हूँ, वो आपको जल्दी call करेंगे। {company_name} को call करने "
        "के लिए thank you!"
    ),
    "inbound_max_call_sec": 600,  # callers with a real enquiry talk longer than cold calls
    # Our call back to someone whose inbound call we missed (an OUTBOUND call, but they called us first).
    "callback_greeting": (
        "नमस्ते {lead_name_ji}! {company_name} से बोल रही हूँ। आपने हमें call किया था, उस वक़्त हम बात नहीं कर पाए, "
        "sorry! अभी दो minute बात हो सकती है?"
    ),
    "inbound_off_action": "message",  # message: play the unavailable message then hang up | reject: busy tone
    "inbound_unavailable_message": (
        "नमस्ते! {company_name} में call करने के लिए thank you। अभी हम call नहीं ले पा रहे हैं, "
        "हमारी team जल्दी आपको call back करेगी।"
    ),

    # ---------------- tone & accent ----------------
    "speaking_tone": "warm",      # warm | professional | energetic | calm
    "language_mix": "hinglish",   # hinglish | hindi | english (the customer's own language still wins)

    # ---------------- learned rules ----------------
    # Extra rules added to the agent's core instructions. The call-review agent suggests them (each one only
    # after a person approves it); the team can also write them by hand.
    "learned_rules": "",

    # ---------------- call-review agent ----------------
    # Reviews finished calls (recording timing + transcript + call data), finds where and why customers were
    # lost, and suggests script changes. Nothing is changed until a person approves a suggestion.
    "review_enabled": True,
    "review_every_calls": 5,         # an automatic review after this many new calls with a conversation
    "review_min_evidence": 2,        # a problem must show up in at least this many calls to become a suggestion
    "review_model": "gpt-4o-mini",   # OpenAI model for reviews (live calls keep OPENAI_MODEL)
    "review_include_inbound": True,  # inbound calls are reviewed too (outbound always)
    "review_rubric": REVIEW_RUBRIC,  # how calls are judged (editable)
}

# Allowed values of the choice settings (validated on save, used by the prompt).
# What each setting is called on the Settings page (settings history and review suggestions show these).
SETTING_LABELS: dict[str, str] = {
    "agent_name": "Agent name", "company_name": "Company name", "agent_gender": "Agent gender",
    "voice": "Voice", "language_code": "Language", "tts_pace": "Speaking pace",
    "tts_temperature": "Voice expressiveness", "pronunciations": "Pronunciations",
    "opening_line": "Opening line", "call_purpose": "Call script (outbound)", "knowledge_base": "Knowledge base",
    "qualification_questions": "Qualification questions", "objection_handling": "Objection handling",
    "interest_rules": "Interest rules", "closing_line": "Closing line (outbound)",
    "silence_timeout_sec": "Silence timeout", "silence_prompt_text": "\"Are you there?\" line",
    "silence_final_wait_sec": "Wait after \"are you there?\"", "silence_goodbye_text": "Silence goodbye line",
    "fillers_enabled": "Fillers", "filler_words": "Filler words", "filler_delay_ms": "Filler delay",
    "barge_in_enabled": "Barge-in", "caller_memory": "Remember earlier calls", "fast_replies": "Fast replies", "listening_sounds": "Listening sounds",
    "natural_variation": "Natural voice variation", "mood_matching": "Mood matching",
    "max_call_sec": "Max outbound call length", "call_window_start": "Campaign window start",
    "call_window_end": "Campaign window end", "outbound_enabled": "Outbound calling",
    "inbound_enabled": "Inbound calling", "inbound_greeting": "Inbound greeting (new caller)",
    "inbound_greeting_known": "Inbound greeting (known caller)", "inbound_purpose": "Call script (inbound)",
    "inbound_closing_line": "Closing line (inbound)", "inbound_max_call_sec": "Max inbound call length",
    "callback_greeting": "Call-back greeting", "inbound_off_action": "When inbound is off",
    "inbound_unavailable_message": "Unavailable message", "speaking_tone": "Speaking tone",
    "language_mix": "Language mix", "learned_rules": "Learned rules", "review_enabled": "Review agent",
    "review_every_calls": "Review every N calls", "review_min_evidence": "Proof needed (calls)",
    "review_model": "Review model", "review_include_inbound": "Review inbound calls",
    "review_rubric": "How calls are judged",
    # no longer a setting (the history is kept forever); the name stays for old history entries that list it
    "history_days": "Keep history (days)",
}

SPEAKING_TONES = ("warm", "professional", "energetic", "calm")
LANGUAGE_MIXES = ("hinglish", "hindi", "english")
INBOUND_OFF_ACTIONS = ("message", "reject")

# Earlier shipped defaults. A saved value still equal to one of these was never customised, so the
# one-time upgrade (services/agent_config.py) replaces it with the current default.
SETTINGS_VERSION = 8
LEGACY_DEFAULTS: dict[str, list] = {
    # version 4 texts, before the PouchWale team's facts (MOQ, per-piece pricing, sample kit, product -> pouch);
    # version 6-7 texts, before "our team sends the details" and the exact ₹199 wording
    **{k: [v] + ([V6_TEXTS[k]] if k in V6_TEXTS else []) for k, v in V4_TEXTS.items()},
    "opening_line": ["नमस्ते {lead_name_ji}! मैं {company_name} से बोल रही हूँ। क्या अभी दो minute बात कर सकते हैं?"],
    # version 6 goodbyes (no company name: the team wants every goodbye to name the brand)
    "closing_line": ["आपका time देने के लिए बहुत-बहुत धन्यवाद! Have a great day.",
                     "अच्छा जी, मैं आपका ज़्यादा time नहीं लूँगी। आपसे बात करके अच्छा लगा, thank you! आपका दिन अच्छा रहे।"],
    "inbound_closing_line": ["अच्छा जी, मैं आपकी details हमारे expert को दे देती हूँ, वो आपको जल्दी call करेंगे। "
                             "Call करने के लिए thank you!"],
    "silence_prompt_text": ["Hello? क्या आप call पर हैं? मेरी आवाज़ आ रही है?", "हेलो...? मेरी आवाज़ आ रही है आपको?"],
    "silence_goodbye_text": ["आप respond नहीं कर रहे हैं, इसलिए हम अभी call end कर रहे हैं। Thank you!",
                             "लगता है line पर आवाज़ नहीं आ रही है। कोई बात नहीं, आपका दिन अच्छा रहे। Thank you!"],
    "filler_words": [["हम्म", "अच्छा", "जी", "ओके", "हाँ जी", "ओह, got it"]],
    "filler_delay_ms": [650],
}
# Fillers the team asked to remove: they sound foreign / robotic on an Indian business call.
# "समझ गई" too: a filler plays before the reply exists, so it claimed to understand words it never caught.
DROPPED_FILLERS = {"ओके", "ओह", "ओह, got it", "ok", "okay", "oh", "oh yeah", "yeah", "got it", "ओह यस", "ओके जी",
                   "जी, समझ गई", "समझ गई", "जी समझ गई", "समझ गया"}

# Settings exposed as read-only info (come from .env, not editable in UI)
LLM_FIXED = {"max_tokens_note": "LLM reply budget is fixed by LLM_MAX_TOKENS in backend/.env"}
