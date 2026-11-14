"""Pre-synthesised inbound audio: the first-time-caller greeting and the unavailable message.

An inbound call is answered the moment we reply to Vobiz, so there is no ringing time to synthesise in;
these clips are prepared at startup and after every settings save so callers never hear dead air.

Clips are keyed by their content (spoken text + voice + pace + expressiveness + language): a settings edit
simply produces a new key, so a Vobiz <Play> URL always names exactly the audio it was issued for.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import re
from collections import OrderedDict
from typing import Any

from ..config import get_settings
from ..defaults import DEFAULT_AGENT_SETTINGS
from .audio import wav_bytes
from .prompts import fill_template
from .sarvam import speakable, tts_rest, voice_for

log = logging.getLogger("inbound.audio")
settings = get_settings()

RATE = 8000      # phone audio: Vobiz streams and plays 8 kHz
MAX_CLIPS = 8    # the current greeting + message, plus a few earlier versions still referenced by live calls
_KEY = re.compile(r"^[0-9a-f]{40}$")

_clips: OrderedDict[str, bytes] = OrderedDict()
_building: dict[str, asyncio.Task] = {}
_loop: asyncio.AbstractEventLoop | None = None  # the app's loop (settings saves arrive on worker threads)


def greeting_text(cfg: dict[str, Any]) -> str:
    """What every caller without a usable name hears ({lead_name_ji} becomes just "जी")."""
    line = cfg.get("inbound_greeting") or DEFAULT_AGENT_SETTINGS["inbound_greeting"]
    return fill_template(line, cfg, None).strip()


def message_text(cfg: dict[str, Any]) -> str:
    """The unavailable message: company / agent placeholders only, so one clip serves every caller."""
    text = fill_template(cfg.get("inbound_unavailable_message") or "", cfg, None).strip()
    return text or fill_template(DEFAULT_AGENT_SETTINGS["inbound_unavailable_message"], cfg, None).strip()


def clip_key(text: str, cfg: dict[str, Any]) -> str:
    """Content key. Uses the SPOKEN text, so a pronunciation fix also produces a fresh clip."""
    parts = (speakable(text, cfg), voice_for(cfg), f"{float(cfg.get('tts_pace') or 1.0):.2f}",
             f"{float(cfg.get('tts_temperature') or 0.6):.2f}", str(cfg.get("language_code") or "hi-IN"))
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def get(key: str) -> bytes | None:
    pcm = _clips.get(key)
    if pcm is not None:
        _clips.move_to_end(key)
    return pcm


def wav(key: str) -> bytes | None:
    """8 kHz mono PCM16 WAV for a clip this process generated (None: never generated, or long evicted)."""
    if not _KEY.match(key or ""):
        return None
    pcm = get(key)
    return wav_bytes(pcm, RATE) if pcm else None


def cached_greeting(cfg: dict[str, Any]) -> tuple[str, bytes | None]:
    text = greeting_text(cfg)
    return text, get(clip_key(text, cfg))


def _store(key: str, pcm: bytes) -> None:
    _clips[key] = pcm
    _clips.move_to_end(key)
    while len(_clips) > MAX_CLIPS:
        _clips.popitem(last=False)


async def build(text: str, cfg: dict[str, Any]) -> bytes | None:
    """Synthesise and cache one clip now. None on failure: callers fall back, nothing raises."""
    key = clip_key(text, cfg)
    pcm = get(key)
    if pcm is not None:
        return pcm
    try:
        pcm = await tts_rest(text, cfg, RATE)
    except Exception as e:  # noqa: BLE001
        log.warning("inbound clip failed (%s...): %s", text[:30], e)
        return None
    if not pcm:
        return None
    _store(key, pcm)
    return pcm


def _forget_task(key: str, task: asyncio.Task) -> None:
    if _building.get(key) is task:
        del _building[key]


def ensure(text: str, cfg: dict[str, Any]) -> str:
    """The clip's key; starts building it in the background when it is missing (one task per key).
    Call on the event loop."""
    key = clip_key(text, cfg)
    if not text or key in _clips or not settings.sarvam_ready:
        return key
    task = _building.get(key)
    if task is None or task.done():
        task = asyncio.get_running_loop().create_task(build(text, copy.deepcopy(cfg)))
        _building[key] = task
        task.add_done_callback(lambda t, k=key: _forget_task(k, t))
    return key


def _prewarm(cfg: dict[str, Any]) -> None:
    try:
        ensure(greeting_text(cfg), cfg)
        ensure(message_text(cfg), cfg)
    except Exception:  # noqa: BLE001
        log.exception("inbound clip pre-warm failed")


def schedule_prewarm(cfg: dict[str, Any]) -> None:
    """Thread-safe: (re)build the cached inbound clips for these settings in the background.

    Called at startup and after every settings save / reset. No-op without SARVAM_API_KEY; never raises.
    """
    global _loop
    try:
        if not settings.sarvam_ready:
            return
        cfg = copy.deepcopy(cfg)
        try:
            loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:  # a settings save, running on a worker thread
            loop = None
        if loop is not None:
            _loop = loop
            _prewarm(cfg)
        elif _loop is not None and not _loop.is_closed():
            _loop.call_soon_threadsafe(_prewarm, cfg)
    except Exception:  # noqa: BLE001
        log.exception("inbound clip pre-warm could not be scheduled")
