"""Agent settings, voice list/preview, provider health checks."""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response

from ..config import get_settings
from ..schemas import VoicePreview
from ..services import inbound_audio, telephony
from ..services.agent_config import (UndoError, list_history, load_agent_settings, reset_agent_settings,
                                     save_agent_settings, undo_change)
from ..services.audio import wav_bytes
from ..services.llm import client
from ..services.sarvam import (ALL_VOICES, VALID_VOICES, check_stt_stream, check_tts, check_tts_stream,
                               model_for, tts_rest, voice_for)
from ..services.settings_rules import LANGUAGES, SettingsError, validate_patch

router = APIRouter(prefix="/api/settings", tags=["settings"])
settings = get_settings()


@router.get("")
def get_agent_settings():
    return load_agent_settings()


@router.put("")
def put_agent_settings(patch: dict[str, Any] = Body(...)):
    try:
        clean = validate_patch(patch)
    except SettingsError as e:
        raise HTTPException(422, str(e)) from None
    saved = save_agent_settings(clean)
    inbound_audio.schedule_prewarm(saved)  # greetings / unavailable message ready before the next caller
    return saved


@router.post("/reset")
def reset():
    saved = reset_agent_settings()
    inbound_audio.schedule_prewarm(saved)
    return saved


@router.get("/history")
def history(limit: int = 300):
    """Saved changes within history_days, newest first (each: which settings, old -> new, Undo state)."""
    return {"days": int(load_agent_settings().get("history_days") or 30),
            "items": list_history(max(1, min(limit, 1000)))}


@router.post("/history/{change_id}/undo")
def undo(change_id: int, force: bool = False):
    """Put back what that change replaced. 409 with `conflicts` when one of those settings was changed again
    later (repeat with force=true to undo anyway)."""
    try:
        saved, entry = undo_change(change_id, force=force)
    except UndoError as e:
        raise HTTPException(e.status, {"message": str(e), "conflicts": e.conflicts}) from None
    inbound_audio.schedule_prewarm(saved)
    return {"settings": saved, "change": entry}


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
                      "plivo": settings.plivo_ready, "vobiz": settings.vobiz_ready,
                      "telephony": settings.telephony_ready},
        "telephony_provider": settings.provider,  # the active phone provider: "plivo" | "vobiz"
        "telephony_name": settings.provider_name,
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
    ok, detail = await telephony.check_auth()  # only the active phone provider (TELEPHONY_PROVIDER)
    out[settings.provider] = {"ok": ok, "detail": detail}
    return out
