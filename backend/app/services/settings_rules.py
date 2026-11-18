"""Validation of agent-settings changes, shared by the Settings page and approved review suggestions
(both go through exactly the same rules before anything is saved)."""
from __future__ import annotations

import math
import re
from typing import Any

from ..defaults import DEFAULT_AGENT_SETTINGS, INBOUND_OFF_ACTIONS, LANGUAGE_MIXES, SPEAKING_TONES
from .sarvam import VALID_VOICES


class SettingsError(ValueError):
    """A setting value that can't be saved (shown to the user as-is)."""


_NUMERIC_LIMITS = {
    "silence_timeout_sec": (3, 60), "silence_final_wait_sec": (1, 30), "filler_delay_ms": (200, 3000),
    "max_call_sec": (30, 1800), "tts_pace": (0.5, 2.0), "tts_temperature": (0.01, 1.0),
    "inbound_max_call_sec": (60, 3600),
    "review_every_calls": (3, 50), "review_min_evidence": (1, 5), "history_days": (1, 365),
}
# Lines filled in with fill_template(): an unknown {placeholder} would be spoken literally on the call.
TEMPLATE_FIELDS = ("opening_line", "closing_line", "silence_prompt_text", "silence_goodbye_text", "call_purpose",
                   "inbound_purpose", "inbound_greeting", "inbound_greeting_known", "inbound_closing_line",
                   "callback_greeting", "inbound_unavailable_message")
PLACEHOLDERS = ("agent_name", "company_name", "lead_name", "lead_name_ji", "lead_company", "lead_city")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_MODEL_NAME = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,62}$")
_TEXT_LIMITS = {"learned_rules": 4000}  # (others: 12000)
# Lines spoken on the phone: kept short (a 600-char line is ~40 s of speech).
_SPOKEN_LINES = ("inbound_greeting", "inbound_greeting_known", "inbound_unavailable_message", "callback_greeting",
                 "inbound_closing_line")


LANGUAGES = ("hi-IN", "en-IN", "bn-IN", "gu-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN")
_TRUE, _FALSE = (True, 1, "true", "1", "yes", "on"), (False, 0, "false", "0", "no", "off")
_LIST_LIMITS = {"filler_words": (12, 40), "qualification_questions": (15, 300),
                "pronunciations": (40, 120)}  # (items, chars per item)


def validate_patch(patch: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for k, v in patch.items():
        if k not in DEFAULT_AGENT_SETTINGS or v is None:
            continue
        default = DEFAULT_AGENT_SETTINGS[k]
        if isinstance(default, bool):
            key = v.strip().lower() if isinstance(v, str) else v
            if key in _TRUE:
                v = True
            elif key in _FALSE:
                v = False
            else:
                raise SettingsError(f"{k} must be true or false")
        elif isinstance(default, (int, float)) and not isinstance(default, bool):
            try:
                num = float(v)
                if not math.isfinite(num):
                    raise ValueError
                v = num if isinstance(default, float) else int(num)
            except (TypeError, ValueError, OverflowError):
                raise SettingsError(f"{k} must be a number") from None
            lo, hi = _NUMERIC_LIMITS.get(k, (None, None))
            if lo is not None and not (lo <= v <= hi):
                raise SettingsError(f"{k} must be between {lo} and {hi}")
        elif isinstance(default, list):
            if not isinstance(v, list):
                raise SettingsError(f"{k} must be a list")
            max_items, max_len = _LIST_LIMITS.get(k, (30, 500))
            v = [str(x).strip()[:max_len] for x in v if str(x).strip()][:max_items]
        else:
            v = str(v)
            if len(v) > _TEXT_LIMITS.get(k, 12000):
                raise SettingsError(f"{k} is too long (max {_TEXT_LIMITS.get(k, 12000)} characters)")
            if k in TEMPLATE_FIELDS:
                unknown = [m for m in _PLACEHOLDER.findall(v) if m not in PLACEHOLDERS]
                if unknown:
                    raise SettingsError(f"{k}: unknown placeholder {{{unknown[0]}}} (it would be read out on the "
                                        f"call). Allowed: {', '.join('{' + x + '}' for x in PLACEHOLDERS)}")
            if k == "review_model" and not _MODEL_NAME.match(v.strip()):
                raise SettingsError("review_model must be an OpenAI model name such as gpt-4o-mini or gpt-5.5")
            if k == "review_model":
                v = v.strip()
        if k == "pronunciations":
            bad = next((line for line in v if "=" not in line or not all(p.strip() for p in line.split("=", 1))), None)
            if bad is not None:
                raise SettingsError(f"Pronunciation '{bad}' must look like: word = how to say it")
        if k == "voice" and v and v not in VALID_VOICES:
            raise SettingsError(f"Unknown voice '{v}'")
        if k == "language_code" and v not in LANGUAGES:
            raise SettingsError(f"language_code must be one of {', '.join(LANGUAGES)}")
        if k == "agent_gender" and v not in ("female", "male"):
            raise SettingsError("agent_gender must be female or male")
        for key, allowed in (("speaking_tone", SPEAKING_TONES), ("language_mix", LANGUAGE_MIXES),
                             ("inbound_off_action", INBOUND_OFF_ACTIONS)):
            if k == key and v not in allowed:
                raise SettingsError(f"{k} must be one of {', '.join(allowed)}")
        if k in _SPOKEN_LINES and len(v) > 600:
            raise SettingsError(f"{k} is too long (max 600 characters: it is spoken on the phone)")
        if k in _SPOKEN_LINES and not v.strip():
            raise SettingsError(f"{k} can't be empty: callers would hear silence")
        if k in ("call_window_start", "call_window_end"):
            parts = v.split(":")
            if len(parts) != 2 or not all(p.isdigit() for p in parts) or int(parts[0]) > 23 or int(parts[1]) > 59:
                raise SettingsError(f"{k} must be HH:MM")
            v = f"{int(parts[0]):02d}:{int(parts[1]):02d}"
        clean[k] = v
    return clean
