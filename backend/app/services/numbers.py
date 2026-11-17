"""Numbers the way people say them on an Indian phone call (audio only; transcripts keep the digits).

Read as digits, "10,000 pouches", "2 मिनट" or "3:30 बजे" sound machine-made in a Hindi sentence. Here they
become "दस हज़ार pouches", "दो मिनट", "साढ़े तीन बजे". Long digit strings (phone numbers, GST, order ids) are
never read out at all: the agent says "इसी number पर" instead (see prompts.py and session.py).
"""
from __future__ import annotations

import re

_UNITS = (
    "शून्य एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस बीस "
    "इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस "
    "सैंतीस अड़तीस उनतालीस चालीस इकतालीस बयालीस तैंतालीस चवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास पचास "
    "इक्यावन बावन तिरेपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ इकसठ बासठ तिरेसठ चौंसठ पैंसठ छियासठ सड़सठ "
    "अड़सठ उनहत्तर सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उन्यासी अस्सी इक्यासी "
    "बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी नब्बे इक्यानवे बानवे तिरानवे चौरानवे पचानवे छियानवे "
    "सत्तानवे अट्ठानवे निन्यानवे"
).split()
assert len(_UNITS) == 100

LONG_DIGITS = re.compile(r"\+?\d(?:[\s-]?\d){7,}")  # 8+ digits: a phone number, GST, an order id


def hindi_number(n: int) -> str:
    """0 .. 99,99,99,99,999 in Hindi words, Indian grouping (हज़ार, लाख, करोड़)."""
    if n < 100:
        return _UNITS[n]
    parts = []
    for size, name in ((10_000_000, "करोड़"), (100_000, "लाख"), (1000, "हज़ार"), (100, "सौ")):
        if n >= size:
            parts.append(f"{hindi_number(n // size)} {name}")
            n %= size
    if n:
        parts.append(_UNITS[n])
    return " ".join(parts)


def _clock(h: int, m: int) -> str:
    """3:30 -> "साढ़े तीन", 1:30 -> "डेढ़", 2:30 -> "ढाई", 4:15 -> "सवा चार", 4:45 -> "पौने पाँच"."""
    h12 = h % 12 or 12
    if m == 0:
        return hindi_number(h12)
    if m == 30:
        return {1: "डेढ़", 2: "ढाई"}.get(h12, f"साढ़े {hindi_number(h12)}")
    if m == 15:
        return f"सवा {hindi_number(h12)}"
    if m == 45:
        return f"पौने {hindi_number(h12 % 12 + 1)}"
    return f"{hindi_number(h12)} बजकर {hindi_number(m)} मिनट"


_TIME = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])")
_DIMENSION = re.compile(r"(?<![\d.])(\d+)\s*[x×X]\s*(\d+)(?![\d.])")
_DECIMAL = re.compile(r"(?<![\d.])(\d+)\.(\d+)(?![\d.])")
_PERCENT = re.compile(r"(?<![\d.])(\d+)\s*%")
_PLAIN = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{2,3})+(?![\w,])|(?<![\w.])\d+(?![\w.])")
_DIGITS_IN_WORD = re.compile(r"(?<=\d)-(?=[^\W\d_])|(?<=[^\W\d_])-(?=\d)")  # "3-side" -> "3 side"


def _n(s: str) -> str:
    value = int(s.replace(",", ""))
    return hindi_number(value) if value < 10**11 else s


def _decimal(m: re.Match) -> str:
    """1.5 -> डेढ़, 2.5 -> ढाई, 4.5 -> साढ़े चार (as people say it); other decimals digit by digit."""
    whole, frac = m.group(1), m.group(2)
    if frac == "5" and int(whole) < 10**11:
        return {"1": "डेढ़", "2": "ढाई"}.get(whole, f"साढ़े {_n(whole)}")
    return f"{_n(whole)} point {' '.join(_UNITS[int(d)] for d in frac)}"


def speak_numbers_hindi(text: str) -> str:
    """Digits in a Hindi / Hinglish sentence -> the words a person would say."""
    text = LONG_DIGITS.sub("", text)  # never read out a phone number (the prompt avoids them; this is a net)
    text = _TIME.sub(lambda m: _clock(int(m.group(1)), int(m.group(2))) if int(m.group(1)) < 24 and int(m.group(2)) < 60
                     else m.group(0), text)
    text = _DIMENSION.sub(lambda m: f"{_n(m.group(1))} बाय {_n(m.group(2))}", text)
    text = _PERCENT.sub(lambda m: f"{_n(m.group(1))} percent", text)
    text = _DECIMAL.sub(_decimal, text)
    text = _DIGITS_IN_WORD.sub(" ", text)
    text = _PLAIN.sub(lambda m: _n(m.group(0)), text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()
