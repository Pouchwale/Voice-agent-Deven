"""Indian phone number normalisation -> E.164 (+91XXXXXXXXXX).

Numbers without '+' / '00' are treated as Indian only (10 digits starting 6-9, optionally
prefixed by 0 or 91). Anything else is rejected rather than guessed — a wrong guess means
dialing (and paying for) a random international number.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_SCI = re.compile(r"^(\d+)(?:\.(\d+))?[eE]\+?(\d+)$")


def _from_excel_scientific(s: str) -> str | None:
    """'9.876500003e9' -> '9876500003'; None when Excel already dropped digits ('9.88E+09')."""
    m = _SCI.match(s)
    if not m:
        return s
    mantissa = (m.group(1) + (m.group(2) or "")).lstrip("0")
    if len(mantissa) < 10:
        return None
    try:
        val = Decimal(s)
    except InvalidOperation:
        return None
    if val != val.to_integral_value():
        return None
    return str(int(val))


def normalize_phone(raw: str | None, allow_international: bool = False) -> str | None:
    """Indian numbers -> +91XXXXXXXXXX. Other countries only when explicitly allowed
    (protects against dialing expensive international / premium numbers by mistake or abuse)."""
    out = _normalize(raw)
    if out and not out.startswith("+91") and not allow_international:
        return None
    return out


def phone_problem(raw: str | None, allow_international: bool = False) -> str | None:
    """Why `raw` is not callable (shown next to rejected CSV rows), or None when it is fine."""
    s = str(raw or "").strip().replace(" ", "")
    if not s:
        return "No phone number"
    if _SCI.match(s) and _from_excel_scientific(s) is None:
        return "Excel shortened this number (format the column as Text and export again)"
    out = _normalize(s)
    if out and not out.startswith("+91") and not allow_international:
        return "International number (only Indian +91 numbers can be called)"
    if not out:
        return "Not a valid Indian mobile number"
    return None


# What carriers put in the caller-ID field when the number is hidden
_NO_CALLER_ID = {"anonymous", "private", "unknown", "restricted", "unavailable", "withheld", "blocked"}


def caller_e164(raw: str | None) -> str | None:
    """Caller ID from a telephony webhook / call record -> E.164, or None when hidden or unusable.

    Unlike normalize_phone (CSV imports, dialing), every Indian number is accepted, landlines too
    (+91 261 2345678, +91 22 ..., +91 11 ...): the caller really dialed us from it. Only the digits
    count, since a '+' in a form-encoded body arrives as a space. Foreign numbers are returned as
    '+<digits>'; whether we may use them is the caller's decision (see normalize_caller).
    """
    s = str(raw or "").strip()
    if not s or s.lower() in _NO_CALLER_ID:
        return None
    digits = re.sub(r"\D", "", s)
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    elif len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 10:
        # an Indian national number never starts with 0 (that is the trunk prefix)
        return "+91" + digits if digits[0] != "0" else None
    if 10 < len(digits) <= 15 and not digits.startswith("0"):
        return "+" + digits
    return None  # short codes, partial numbers, junk


def normalize_caller(raw: str | None, allow_international: bool = False) -> str | None:
    """Inbound caller number -> E.164 we may store as a lead, or None (hidden / foreign while
    ALLOW_INTERNATIONAL is off). Lookups must compare it exactly with Lead.phone."""
    e164 = caller_e164(raw)
    if e164 and not e164.startswith("+91") and not allow_international:
        return None
    return e164


def _normalize(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().replace(" ", "")
    if not s:
        return None
    s = _from_excel_scientific(s.replace(" ", ""))
    if s is None:
        return None
    if s.endswith(".0"):  # numbers that went through Excel as floats
        s = s[:-2]
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if plus or digits.startswith("00"):
        if digits.startswith("00"):
            digits = digits[2:]
        if digits.startswith("91") and len(digits) != 12:
            return None
        return "+" + digits if 8 <= len(digits) <= 15 else None
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return "+" + digits
    return None
