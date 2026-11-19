"""The one money amount the agent may say: the sample kit's shipping charge, always the real one.

The agent never quotes rates (the team does), so the only rupee amount it ever says is the sample kit's
shipping charge from the business knowledge ("the customer pays only ₹199 shipping"). On a live call the
model still got it wrong once: "सिर्फ shipping charges एक सौ उनसठ रुपये लगेंगे" (₹159). The prompt says the
right amount; this guard makes sure of it. Any rupee amount in a reply about the sample kit / shipping becomes
"₹199", which the voice says as "one ninety nine रुपये / rupees" (pronunciation rules in Settings), so what
the customer hears and what the transcript keeps are both right.

Amounts given per piece ("दो रुपये per piece") are never touched: those are the customer's own figures.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("shipping_price")

# "the customer pays only ₹199 shipping" / "₹199 shipping" in the knowledge base
_KB_AMOUNT = re.compile(r"₹\s?(\d[\d,]*)\s*(?:shipping|शिपिंग)", re.I)
# a reply (or the turn around it) is about the sample kit or its shipping
_TOPIC = re.compile(r"sample|सैंपल|सेम्पल|सैम्पल|shipping|शिपिंग|courier|कूरियर|कुरियर|\bkit\b|किट", re.I)

_HINDI_NUMBERS = (
    "एक दो तीन चार पाँच पांच छह छः छे सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह पन्द्रह सोलह सत्रह अठारह उन्नीस बीस "
    "इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस अठ्ठाईस उनतीस तीस इकतीस बत्तीस तैंतीस तेंतीस चौंतीस पैंतीस "
    "छत्तीस सैंतीस अड़तीस अडतीस उनतालीस चालीस इकतालीस बयालीस तैंतालीस चवालीस चौवालीस पैंतालीस छियालीस सैंतालीस "
    "अड़तालीस अडतालीस उनचास पचास इक्यावन बावन तिरेपन तिरपन चौवन चौव्वन पचपन छप्पन सत्तावन अट्ठावन अठ्ठावन उनसठ साठ "
    "इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ सड़सठ सडसठ अड़सठ अडसठ उनहत्तर सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर "
    "छिहत्तर सतहत्तर अठहत्तर उन्यासी उनासी अस्सी इक्यासी बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी अठ्ठासी नवासी "
    "नब्बे इक्यानवे इक्यानबे बानवे बानबे तिरानवे तिरानबे चौरानवे चौरानबे पचानवे पंचानवे पचानबे छियानवे छियानबे सत्तानवे "
    "सत्तानबे अट्ठानवे अट्ठानबे अठ्ठानवे निन्यानवे निन्यानबे निन्यान्वे सौ हज़ार हजार डेढ़ डेढ ढाई साढ़े साढे सवा पौने"
).split()
_ENGLISH_NUMBERS = (
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand and a"
).split()
_NUM_WORD = "|".join(sorted(map(re.escape, _HINDI_NUMBERS + _ENGLISH_NUMBERS), key=len, reverse=True))
_TOKEN = rf"(?:\d[\d,]*|(?:{_NUM_WORD})(?![\wऀ-ॿ]))"
_CURRENCY = r"(?:रुपये|रुपए|रुपया|रूपये|रूपए|rupees?|rs\.?|inr)(?![\wऀ-ॿ])"
_PER_PIECE = r"(?!\s*(?:per\b|प्रति|/|pcs\b|pc\b|piece|पीस|a piece|each|का एक|हर piece))"
# "एक सौ उनसठ रुपये", "one ninety nine rupees", "159 rupees", "₹159", "Rs. 159", "₹ 1,59"
_AMOUNT = re.compile(
    rf"(?:₹\s?\d[\d,]*(?:\s*{_CURRENCY})?|(?:rs\.?|inr)\s?\d[\d,]*|{_TOKEN}(?:[\s-]+{_TOKEN})*\s*{_CURRENCY}){_PER_PIECE}",
    re.I,
)


def kit_shipping_amount(knowledge_base: str | None) -> int | None:
    """The sample kit's shipping charge as written in the business knowledge, or None (guard off)."""
    m = _KB_AMOUNT.search(knowledge_base or "")
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def fix_shipping_amount(sentence: str, amount: int | None, context: str = "") -> str:
    """Every rupee amount in a sentence about the sample kit / shipping becomes the real charge ("₹199").

    `context` is the text around the sentence (the customer's question, the agent's previous sentence): a
    sentence like "बस एक सौ उनसठ रुपये लगेंगे" is about the kit even when the word "kit" was said just before.
    """
    if not sentence or amount is None or not _TOPIC.search(f"{context} {sentence}"):
        return sentence
    right = f"₹{amount}"

    def repl(m: re.Match) -> str:
        said = m.group(0)
        lead = said[: len(said) - len(said.lstrip())]
        digits = re.sub(r"\D", "", said)
        if digits and int(digits) != amount:
            log.warning("sample kit shipping amount corrected: %r -> %s", said.strip(), right)
        elif said.strip() != right:  # in words ("one ninety nine rupees"): written the one way the voice reads
            log.debug("sample kit shipping amount %r written as %s", said.strip(), right)
        return lead + right

    return _AMOUNT.sub(repl, sentence)
