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
