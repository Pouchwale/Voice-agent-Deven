"""Agent settings, voice list/preview, provider health checks."""
from __future__ import annotations

import math
import time
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response

from ..config import get_settings
from ..defaults import DEFAULT_AGENT_SETTINGS, INBOUND_OFF_ACTIONS, LANGUAGE_MIXES, SPEAKING_TONES
from ..schemas import VoicePreview
from ..services import inbound_audio, vobiz
from ..services.agent_config import load_agent_settings, reset_agent_settings, save_agent_settings
from ..services.audio import wav_bytes
from ..services.llm import client
from ..services.sarvam import (ALL_VOICES, VALID_VOICES, check_stt_stream, check_tts, check_tts_stream,
                               model_for, tts_rest, voice_for)

router = APIRouter(prefix="/api/settings", tags=["settings"])
settings = get_settings()

_NUMERIC_LIMITS = {
    "silence_timeout_sec": (3, 60), "silence_final_wait_sec": (1, 30), "filler_delay_ms": (200, 3000),
    "max_call_sec": (30, 1800), "tts_pace": (0.5, 2.0), "tts_temperature": (0.01, 1.0),
    "inbound_max_call_sec": (60, 3600),
}
# Lines spoken on the phone: kept short (a 600-char line is ~40 s of speech).
_SPOKEN_LINES = ("inbound_greeting", "inbound_greeting_known", "inbound_unavailable_message", "callback_greeting",
                 "inbound_closing_line")


LANGUAGES = ("hi-IN", "en-IN", "bn-IN", "gu-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN")
_TRUE, _FALSE = (True, 1, "true", "1", "yes", "on"), (False, 0, "false", "0", "no", "off")
_LIST_LIMITS = {"filler_words": (12, 40), "qualification_questions": (15, 300),
                "pronunciations": (40, 120)}  # (items, chars per item)


def _validate(patch: dict[str, Any]) -> dict[str, Any]:
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
                raise HTTPException(422, f"{k} must be true or false")
        elif isinstance(default, (int, float)) and not isinstance(default, bool):
            try:
                num = float(v)
                if not math.isfinite(num):
                    raise ValueError
                v = num if isinstance(default, float) else int(num)
            except (TypeError, ValueError, OverflowError):
                raise HTTPException(422, f"{k} must be a number") from None
            lo, hi = _NUMERIC_LIMITS.get(k, (None, None))
            if lo is not None and not (lo <= v <= hi):
                raise HTTPException(422, f"{k} must be between {lo} and {hi}")
        elif isinstance(default, list):
            if not isinstance(v, list):
                raise HTTPException(422, f"{k} must be a list")
            max_items, max_len = _LIST_LIMITS.get(k, (30, 500))
            v = [str(x).strip()[:max_len] for x in v if str(x).strip()][:max_items]
        else:
            v = str(v)
            if len(v) > 12000:
                raise HTTPException(422, f"{k} is too long")
        if k == "pronunciations":
            bad = next((line for line in v if "=" not in line or not all(p.strip() for p in line.split("=", 1))), None)
            if bad is not None:
                raise HTTPException(422, f"Pronunciation '{bad}' must look like: word = how to say it")
        if k == "voice" and v and v not in VALID_VOICES:
            raise HTTPException(422, f"Unknown voice '{v}'")
        if k == "language_code" and v not in LANGUAGES:
            raise HTTPException(422, f"language_code must be one of {', '.join(LANGUAGES)}")
        if k == "agent_gender" and v not in ("female", "male"):
            raise HTTPException(422, "agent_gender must be female or male")
        for key, allowed in (("speaking_tone", SPEAKING_TONES), ("language_mix", LANGUAGE_MIXES),
                             ("inbound_off_action", INBOUND_OFF_ACTIONS)):
            if k == key and v not in allowed:
                raise HTTPException(422, f"{k} must be one of {', '.join(allowed)}")
        if k in _SPOKEN_LINES and len(v) > 600:
            raise HTTPException(422, f"{k} is too long (max 600 characters: it is spoken on the phone)")
        if k in _SPOKEN_LINES and not v.strip():
            raise HTTPException(422, f"{k} can't be empty: callers would hear silence")
        if k in ("call_window_start", "call_window_end"):
            parts = v.split(":")
            if len(parts) != 2 or not all(p.isdigit() for p in parts) or int(parts[0]) > 23 or int(parts[1]) > 59:
                raise HTTPException(422, f"{k} must be HH:MM")
            v = f"{int(parts[0]):02d}:{int(parts[1]):02d}"
        clean[k] = v
    return clean


@router.get("")
def get_agent_settings():
    return load_agent_settings()


@router.put("")
def put_agent_settings(patch: dict[str, Any] = Body(...)):
    saved = save_agent_settings(_validate(patch))
    inbound_audio.schedule_prewarm(saved)  # greetings / unavailable message ready before the next caller
    return saved


@router.post("/reset")
def reset():
    saved = reset_agent_settings()
    inbound_audio.schedule_prewarm(saved)
    return saved


@router.get("/system")
def system_info():
    public_ok = settings.public_base_url.startswith("https://")
    return {
        "llm_model": settings.openai_model,
        "llm_max_tokens": settings.llm_max_tokens,
        "llm_temperature": settings.llm_temperature,
        "stt_model": settings.sarvam_stt_model,
        "stt_mode": settings.sarvam_stt_mode,
        "tts_model": model_for(voice_for(load_agent_settings())),  # the model of the voice in use
        "providers": {"openai": settings.openai_ready, "sarvam": settings.sarvam_ready,
                      "vobiz": settings.vobiz_ready},
        "public_base_url": settings.public_base_url,
        "public_url_ok": public_ok,
    }


@router.get("/voices")
def voices():
    return ALL_VOICES


@router.post("/preview-voice")
async def preview_voice(body: VoicePreview):
    cfg = load_agent_settings()
    if body.voice:
        if body.voice not in VALID_VOICES:
            raise HTTPException(422, "Unknown voice")
        cfg["voice"] = body.voice
    if body.pace:
        cfg["tts_pace"] = body.pace
    if body.temperature:
        cfg["tts_temperature"] = body.temperature
    if body.language_code:
        if body.language_code not in LANGUAGES:
            raise HTTPException(422, f"language_code must be one of {', '.join(LANGUAGES)}")
        cfg["language_code"] = body.language_code
    text = body.text.strip() or "जी, हम brands के लिए custom printed pouches बनाते हैं। आप किस product के लिए packaging ढूँढ रहे हैं?"
    try:
        pcm = await tts_rest(text, cfg, 24000)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Voice preview failed: {e}") from e
    return Response(content=wav_bytes(pcm, 24000), media_type="audio/wav")


@router.post("/test-providers")
async def test_providers():
    out: dict[str, Any] = {}
    if settings.openai_ready:
        t0 = time.monotonic()
        try:
            r = await client().chat.completions.create(
                model=settings.openai_model, max_tokens=5, temperature=0,
                messages=[{"role": "user", "content": "Reply with: OK"}])
            out["openai"] = {"ok": True, "detail": f"{settings.openai_model}: {r.choices[0].message.content!r}",
                             "latency_ms": int((time.monotonic() - t0) * 1000)}
        except Exception as e:  # noqa: BLE001
            out["openai"] = {"ok": False, "detail": str(e)[:200]}
    else:
        out["openai"] = {"ok": False, "detail": "OPENAI_API_KEY not set"}
    ok, detail, ms = await check_tts()
    out["sarvam_tts"] = {"ok": ok, "detail": detail, "latency_ms": ms}
    ok, detail, ms = await check_tts_stream(load_agent_settings())
    out["sarvam_tts_stream"] = {"ok": ok, "detail": detail, "latency_ms": ms}
    ok, detail, ms = await check_stt_stream()
    out["sarvam_stt"] = {"ok": ok, "detail": detail, "latency_ms": ms}
    ok, detail = await vobiz.check_auth()
    out["vobiz"] = {"ok": ok, "detail": detail}
    return out
