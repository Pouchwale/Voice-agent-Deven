"""Real-time conversation engine shared by phone (Plivo / Vobiz) and browser test calls.

Pipeline per customer turn:
  customer audio -> Sarvam Saaras v3 realtime STT (partials + server VAD)
  final transcript -> GPT-4o-mini (streamed, 120 max tokens, temp 0.6)
  -> sentence chunks -> Bulbul v3 streaming TTS -> transport (phone / browser)

Behaviours:
  * barge-in: customer talking over the agent (2+ words, or a non-backchannel word)
    stops agent audio instantly; short backchannels ("haan", "ji", "ok") don't.
  * fillers: if the first reply audio is late, a short pre-synthesised filler plays so the line
    never sounds dead: "हम्म, एक second" after a question, "जी" / "अच्छा" after a statement (never one that
    claims to have understood); never the same one twice in a row, and not on back-to-back replies unless the
    reply is clearly slow.
  * background voices (phone): speech much quieter than the customer's own voice (people talking near them)
    can't interrupt the agent and is not answered. Plivo's noise cancellation cleans the line before that.
  * language: once the customer asks for English (or speaks English two turns in a row) the whole rest of
    the call is English: replies, fillers, silence prompts, goodbye, with an Indian-English voice.
  * natural turn-taking: a customer who trails off mid-sentence ("...और") gets a moment to go on.
  * silence: N s of customer silence -> "are you on the call?" -> M s more ->
    goodbye line -> hang up (end_reason = silence_timeout).
  * the LLM ends the call by appending [END_CALL] to a goodbye line.
  * hard cap on call length (closing line, then hang up); inbound calls have their own cap and closing line.
  * inbound calls: the caller expects us to speak first, so the greeting starts almost at once.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import random
import re
import time
from abc import ABC, abstractmethod
from collections import deque
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from ..config import get_settings
from . import agent_config, repo
from .audio import CallRecorder, EnergyVAD, Resampler, pcm_rms, rms_level, scale_volume
from .events import bus, spawn
from .llm import SentenceChunker, stream_reply, strip_end_token, warmup
from .numbers import LONG_DIGITS
from .prompts import (ENGLISH_AUTO_NOTE, ENGLISH_FILLERS, ENGLISH_LINES, ENGLISH_PREFERRED_NOTE, ENGLISH_TURN_NOTE,
                      HEAR_YOU_REPLY, HELLO_REPLY, LISTENING_SOUNDS, MOOD_NOTES, MOOD_PACE, detect_mood,
                      fill_template, greeting_for, hello_check, language_request, lead_context, spoke_english,
                      spoke_hindi, static_system_prompt)
from .sarvam import RealtimeSTT, TTSStream, tts_rest

log = logging.getLogger("session")
settings = get_settings()

BACKCHANNELS = {
    "हाँ", "हां", "हा", "जी", "हाँजी", "हांजी", "जी हाँ", "हम्म", "हम", "अच्छा", "ओके", "ठीक", "ठीक है", "हूँ",
    "haan", "han", "ha", "ji", "hmm", "hm", "ok", "okay", "achha", "acha", "accha", "right", "yes", "yeah",
    "uh", "huh", "mm", "hmmm", "theek", "ठीक है जी",
}
GREETING_WORDS = {"hello", "helo", "hallo", "hi", "हेलो", "हैलो", "हेल्लो", "हलो", "बोलिए", "बोलो", "हाँ बोलिए", "जी बोलिए"}
_PUNCT = re.compile(r"[\s\.,!?।'\"…-]+")

# A turn that trails off on one of these is usually not finished ("मुझे pouch चाहिए, और..."): people pause
# mid-thought on the phone, and jumping in at that moment is the most robotic thing an agent can do.
_TRAILING_WORDS = {
    "और", "तो", "कि", "लेकिन", "पर", "मगर", "मतलब", "जैसे", "या", "क्योंकि", "अगर", "जो", "वो", "वह", "यानी",
    "का", "की", "के", "में", "से", "को", "भी", "हम्म", "अं", "उम",
    "aur", "toh", "ki", "lekin", "matlab", "jaise", "ya", "kyunki", "agar",
    "and", "but", "so", "or", "because", "like", "the", "a", "an", "of", "to", "with", "for", "um", "uh",
}
UNFINISHED_GRACE_SEC = 0.7  # extra wait for the customer to finish such a sentence

# Before the greeting: on our calls the customer often says "Hello?" first, so we give them a moment. A caller
# who rang us expects US to speak first: a short window that runs while the greeting is synthesised.
HELLO_WAIT_SEC = 0.9
INBOUND_HELLO_WAIT_SEC = 0.3

# Question detection picks a "thinking" filler (हम्म, एक second) over an acknowledgement (जी, अच्छा).
_QUESTION_WORDS = {"क्या", "कितना", "कितने", "कितनी", "कैसे", "कैसा", "कब", "कहाँ", "कहां", "कौन", "कौनसा", "क्यों",
                   "kya", "kitna", "kitne", "kaise", "kab", "kahan", "kaun", "kyun", "price", "rate", "cost"}
_QUESTION_STARTS = {"what", "how", "when", "where", "which", "who", "why", "can", "could", "do", "does", "is",
                    "are", "will", "would"}
_THINKING_MARKERS = ("हम्म", "hmm", "second", "सेकंड", "मिनट", "minute", "देखती", "देखता", "let me")

VOICED_RMS = 400  # a 20 ms phone frame louder than this has voice in it (line hiss sits far below)

ERROR_LINE = "माफ़ कीजिए, line पर थोड़ी problem आ रही है। हमारी team आपको जल्दी call back करेगी। धन्यवाद!"
CUT_LINE = "माफ़ कीजिए, आपकी बात थोड़ी cut हो गई। क्या आप दोबारा बता सकते हैं?"
_DEVANAGARI_CHAR = re.compile(r"[ऀ-ॿ]")


# Listening sounds ("हम्म", "जी" while the customer talks): only in a real pause of a long sentence, softly, and
# rarely, as a person on the phone would.
LISTENING_VOLUME = 0.6       # relative to the agent's normal voice
LISTEN_AFTER_SEC = 3.0       # the customer has been talking at least this long
LISTEN_GAP_SEC = 6.0         # at most one every this many seconds (and two per sentence)
LISTEN_PAUSE = (0.16, 0.3)   # s of quiet inside their sentence that invites one (STT ends a turn at 0.35 s)
QUICK_REPLY_LIMIT = 2        # "Hello?" answered without the LLM at most this often per 30 s
QUICK_REPLY_ECHO_SEC = 5.0   # another "Hello" this soon after the instant answer is the same one


def _turn_key(text: str) -> str:
    """What a customer turn says, ignoring case, punctuation and the spelling variants speech recognition
    flips between in a partial and its final transcript (आवाज / आवाज़, हां / हाँ)."""
    return " ".join(_norm_words(text)).replace("़", "").replace("ँ", "ं")


_OUT_OF_CREDIT = re.compile(r"credit|quota|insufficient|402", re.I)


def out_of_credit(err: BaseException | str) -> bool:
    """The voice / speech provider refused for lack of credit (Sarvam: 402 "No credits available")."""
    return bool(_OUT_OF_CREDIT.search(str(err)))


class _Prefetch:
    """A reply the LLM started writing the moment the customer stopped talking (STT's end of speech, ~0.4 s
    before the final transcript). It is only used if the final transcript says the same words and nothing
    else changed; otherwise it is cancelled. Its tokens are buffered, never spoken on their own."""

    def __init__(self, key: str, history_len: int, notes: tuple[str, ...]):
        self.key = key
        self.history_len = history_len
        self.notes = notes
        self.started = time.monotonic()
        self.tokens: list[str] = []
        self.finished = False
        self.error: BaseException | None = None
        self._changed = asyncio.Event()
        self.task: asyncio.Task | None = None

    async def run(self, messages: list[dict], cache_key: str) -> None:
        try:
            async for tok in stream_reply(messages, cache_key):
                self.tokens.append(tok)
                self._changed.set()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self.error = e
        finally:
            self.finished = True
            self._changed.set()

    async def stream(self) -> AsyncIterator[str]:
        i = 0
        while True:
            while i < len(self.tokens):
                yield self.tokens[i]
                i += 1
            if self.finished:
                if self.error is not None:
                    raise self.error
                return
            self._changed.clear()
            await self._changed.wait()

    def cancel(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()


def english_voice_cfg(cfg: dict) -> dict:
    """The same voice reading English the Indian-English way (numbers, abbreviations), without the
    Hindi-spelled pronunciation fixes ("PouchWale = पाउचवाले") that only make sense in a Hindi sentence."""
    rules = [r for r in cfg.get("pronunciations") or [] if not _DEVANAGARI_CHAR.search(str(r))]
    return {**cfg, "language_code": "en-IN", "pronunciations": rules}


def _norm_words(text: str) -> list[str]:
    return [w for w in _PUNCT.split(text.lower()) if w]


def sounds_unfinished(text: str) -> bool:
    """The customer stopped mid-sentence (trailing comma / "..." / connector like "और", "तो", "and")."""
    t = text.strip()
    if not t or t.endswith(("?", "？")):
        return False
    if t.endswith((",", "...", "…", "-", "–")):
        return True
    words = _norm_words(t)
    return bool(words) and words[-1] in _TRAILING_WORDS


def is_question(text: str) -> bool:
    t = text.strip()
    if t.endswith(("?", "？")):
        return True
    words = _norm_words(t)
    return bool(words) and (words[0] in _QUESTION_STARTS or any(w in _QUESTION_WORDS for w in words))


def is_backchannel(text: str, *, greeting: bool = False) -> bool:
    words = _norm_words(text)
    if not words:
        return True
    allowed = BACKCHANNELS | (GREETING_WORDS if greeting else set())
    joined = " ".join(words)
    if joined in allowed:
        return True
    return len(words) <= 2 and all(w in allowed for w in words)


# =====================================================================
# Transports
# =====================================================================
class Transport(ABC):
    channel: str = "phone"
    in_rate: int = 8000        # customer audio sample rate
    out_rate: int = 8000       # agent audio sample rate the transport wants
    stt_encoding: str = "mulaw"

    @abstractmethod
    async def send_audio(self, pcm16: bytes) -> None: ...

    @abstractmethod
    async def clear(self) -> None: ...

    async def send_event(self, data: dict) -> None:  # browser only
        return None

    @abstractmethod
    async def close(self, reason: str) -> None: ...

    def user_pcm16(self, raw: bytes) -> bytes:
        """Convert raw inbound audio to PCM16 at in_rate (for recording / levels / VAD)."""
        return raw


# =====================================================================
# Session
# =====================================================================
class CallSession:
    def __init__(
        self,
        *,
        call_id: int,
        call_uuid: str,
        lead: dict | None,
        cfg: dict,
        transport: Transport,
        greeting_pcm: bytes | None = None,
        on_finished: Callable[["CallSession"], Awaitable[None]] | None = None,
        direction: str = "outbound",
        memory: str = "",
        greeting_text: str | None = None,
        purpose: str | None = None,
    ):
        self.call_id = call_id
        self.uuid = call_uuid
        self.lead = lead or {}
        self.cfg = cfg
        self.t = transport
        self.on_finished = on_finished
        self.direction = direction  # "outbound" (we called) | "inbound" (the customer called us)
        # "outbound" (cold/qualification call) | "inbound" | "callback" (we return a missed inbound call)
        self.purpose = purpose or direction
        self.memory = memory        # what we already know about this customer (services/memory.py)
        if greeting_text is None:  # callers pass the greeting they prepared; this is the defensive default
            greeting_text = greeting_for(cfg, self.lead, direction=direction, purpose=self.purpose)
        self.greeting_text = greeting_text.strip()
        self.greeting_pcm = greeting_pcm

        self.history: list[dict] = []
        # Static prompt first (identical across calls with the same settings, direction and purpose -> OpenAI
        # prompt cache), per-call details second (time, customer, memory).
        self.system_prompt = static_system_prompt(cfg, direction, self.purpose)
        self.lead_prompt = lead_context(self.lead, memory, direction=direction)
        self.cache_key = "va-" + hashlib.sha1(self.system_prompt.encode("utf-8")).hexdigest()[:16]
        self._spare_tts: TTSStream | None = None
        self._spare_task: asyncio.Task | None = None
        self._last_speech_end = 0.0

        self.t0 = time.monotonic()
        self.state = "connecting"
        self.ended = asyncio.Event()
        self.ending = False
        self.end_reason: str | None = None

        # playback clock (when currently queued agent audio finishes playing)
        self.play_until = 0.0
        self._level_q: deque[tuple[float, float, float]] = deque(maxlen=400)
        self._user_level = 0.0

        # turn state
        self.greeted = False
        self.greeting_playing = False
        self.pending_user: list[str] = []
        self.user_speaking = False
        self.last_user_activity = time.monotonic()
        self.respond_after_playback = False
        self._respond_task: asyncio.Task | None = None
        self._debounce_task: asyncio.Task | None = None
        self._turn_seq = 0
        self._active_turn = 0  # turn whose audio is allowed to play
        self._agent_turn_db_id: int | None = None
        self._agent_turn_text = ""
        self._agent_turn_started = False
        self._turn_user_end = 0.0
        self._turn_first_audio = 0.0
        self._filler_played = False
        self.language_pref: str | None = None  # "english" / "hindi" once the customer asks for a language
        self._last_reply_had_filler = False  # fillers on back-to-back replies sound like a tic
        self._recent_fillers: deque[str] = deque(maxlen=2)  # never the same filler twice in a row
        self._filler_texts: dict[str, str] = {}
        self.latencies: list[int] = []
        self._pending_since = 0.0          # when pending_user got its first unanswered line
        self._last_stt_speech = 0.0        # last time STT itself heard speech (ground truth vs. line noise)
        self._cancelled_reply: asyncio.Task | None = None  # interrupted reply still cleaning up
        self.agent_audio_ms = 0            # audio actually sent to the customer (0 = they heard nothing)
        self._stt_speech_start_at = 0.0    # last STT vad.speech_start
        self._speaking_since = 0.0         # when the agent's current speech began
        self._barge_pending = False        # an overlap barge-in is already on its way

        # loudness of the customer's own speech (phone): decides who may cut the agent off (see _voice_level)
        self._frame_levels: deque[tuple[float, int]] = deque(maxlen=2000)  # (time, RMS) of the last ~40 s
        self._voice_levels: deque[int] = deque(maxlen=12)  # loudness of the customer's turns
        self._last_final_at = 0.0
        self.background_ignored = 0  # near-silent "speech" never answered (a TV far away, a whisper)

        # language: English once asked (locked) or after two English turns in a row (auto: ends on Hindi)
        self._lang_auto = False
        self._english_streak = 0
        self.tts_cfg = cfg  # what the voice engine reads with (English calls: en-IN)

        # human touches
        self.mood: str | None = None          # irritated / busy / friendly / brief (from their words)
        self._short_answers = 0               # answers of a word or two in a row
        self._quick_replies: deque[float] = deque(maxlen=4)  # "Hello?" answered without the LLM
        self._last_partial = ""
        self._last_voiced_at = 0.0
        self._last_listening_at = 0.0
        self._listening_in_segment = 0
        self._prefetch: _Prefetch | None = None  # reply started before the final transcript (fast_replies)
        self._reply_user_text = ""            # what the reply being prepared answers (taken back if they go on)
        self.prefetch_used = 0
        # facts for the call-review agent (saved in the call's metrics)
        self.settings_rev = agent_config.current_revision()  # which version of the settings this call used
        self.barge_ins = 0        # the customer cut the agent off
        self.replies_held = 0     # a reply taken back because the customer went on talking
        self.quick_replies = 0    # "Hello?" answered without the LLM

        # silence handling
        self.silence_stage = 0  # 0 normal, 1 asked "are you there?"
        self._silence_prompt_end = 0.0
        self._local_speech_since: float | None = None

        # audio plumbing
        self.rec_rate = 8000 if transport.in_rate == 8000 else 16000
        self.recorder = CallRecorder(self.rec_rate)
        self._user_to_rec = Resampler(transport.in_rate, self.rec_rate)
        self._agent_to_rec = Resampler(transport.out_rate, self.rec_rate)
        self.vad = EnergyVAD(transport.in_rate)
        self.clips: dict[str, bytes] = {}
        self.stt: RealtimeSTT | None = None
        self._tasks: list[asyncio.Task] = []
        self.user_words = 0
        self.agent_words = 0
        self.turns = 0

    # ------------------------------------------------------------------ utils
    def offset_ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    def agent_audible(self) -> bool:
        return time.monotonic() < self.play_until

    def responding(self) -> bool:
        return self._respond_task is not None and not self._respond_task.done()

    async def _set_state(self, state: str) -> None:
        if state == self.state:
            return
        if state == "speaking":
            self._speaking_since = time.monotonic()
        self.state = state
        bus.publish({"type": "call.state", "uuid": self.uuid, "state": state})
        await self.t.send_event({"type": "state", "state": state})

    async def _emit_transcript(self, role: str, text: str, final: bool, interrupted: bool = False) -> None:
        ev = {"role": role, "text": text, "final": final, "offset_ms": self.offset_ms()}
        if interrupted:
            ev["interrupted"] = True
        bus.publish({"type": "transcript", "uuid": self.uuid, **ev})
        await self.t.send_event({"type": "transcript", **ev})

    # ------------------------------------------------------------------ run
    async def run(self) -> None:
        log.info("session %s start (%s)", self.uuid, self.t.channel)
        await self._set_state("connecting")
        self.stt = RealtimeSTT(
            sample_rate=self.t.in_rate,
            encoding=self.t.stt_encoding,
            language_code=self.cfg.get("language_code") or "hi-IN",
            on_event=self._on_stt,
        )
        await self.stt.start()
        self._tasks += [
            asyncio.create_task(self._prepare_clips()),
            asyncio.create_task(self._watchdog()),
            asyncio.create_task(self._level_reporter()),
            asyncio.create_task(self._greet()),
            asyncio.create_task(warmup()),  # open the OpenAI connection before the first reply
        ]
        self._prepare_spare_tts()
        await self.ended.wait()

    async def _prepare_clips(self) -> None:
        """Pre-synthesise short lines so they play with zero latency."""
        texts: dict[str, str] = {
            "silence_prompt": self.cfg.get("silence_prompt_text", ""),
            "silence_goodbye": self.cfg.get("silence_goodbye_text", ""),
            "closing": self._closing_text(),
            "error": ERROR_LINE,
            "hello_reply": HELLO_REPLY["hindi"],
            "hear_you": HEAR_YOU_REPLY["hindi"],
        }
        if self.cfg.get("listening_sounds", True):
            for i, w in enumerate(LISTENING_SOUNDS["hindi"]):
                texts[f"listen_{i}"] = w
        if self.cfg.get("fillers_enabled", True):
            for i, w in enumerate(self.cfg.get("filler_words") or []):
                word = w.strip().rstrip(".…")
                # a filler plays before the reply exists: one that claims understanding ("समझ गई") is skipped
                if word and "समझ" not in word:
                    texts[f"filler_{i}"] = word + "..."  # trailing "..." = drawn-out, thinking tone
                    self._filler_texts[f"filler_{i}"] = word

        await self._build_clips(texts, self.cfg)

    async def _build_clips(self, texts: dict[str, str], cfg: dict) -> None:
        async def one(key: str, text: str) -> None:
            if not text.strip() or key in self.clips:
                return
            try:
                pcm = await tts_rest(text, cfg, self.t.out_rate)
                # listening sounds are said softly, under the customer's voice
                self.clips[key] = scale_volume(pcm, LISTENING_VOLUME) if "listen_" in key else pcm
            except Exception as e:  # noqa: BLE001
                log.warning("clip %s failed: %s", key, e)

        await asyncio.gather(*(one(k, v) for k, v in texts.items()))

    async def _prepare_english_clips(self) -> None:
        """The English call's fixed lines, fillers and listening sounds, built when the call switches."""
        texts = {f"en_{k}": v for k, v in ENGLISH_LINES.items()}
        if self.cfg.get("fillers_enabled", True):
            for i, w in enumerate(ENGLISH_FILLERS):
                texts[f"en_filler_{i}"] = w + "..."
                self._filler_texts[f"en_filler_{i}"] = w
        if self.cfg.get("listening_sounds", True):
            for i, w in enumerate(LISTENING_SOUNDS["english"]):
                texts[f"en_listen_{i}"] = w
        await self._build_clips(texts, english_voice_cfg(self.cfg))

    @property
    def english(self) -> bool:
        return self.language_pref == "english"

    def _set_language(self, lang: str | None, *, auto: bool) -> None:
        """Switch the call's language. English: replies, fillers and fixed lines in English, read with the
        Indian-English voice settings; the pre-opened voice socket is replaced (it was set up for Hindi)."""
        was_english = self.english
        self.language_pref = lang
        self._lang_auto = auto and lang == "english"
        if self.english == was_english:
            return
        log.info("session %s language -> %s%s", self.uuid, lang or "default", " (auto)" if self._lang_auto else "")
        self.tts_cfg = english_voice_cfg(self.cfg) if self.english else self.cfg
        self._refresh_spare_tts()
        if self.english:
            spawn(self._prepare_english_clips())

    def _update_language(self, user_text: str) -> str | None:
        """Track the language the customer wants; returns the note for this reply (None = default rules)."""
        asked = language_request(user_text)
        if asked:
            self._set_language(asked, auto=False)
        elif spoke_english(user_text):
            self._english_streak += 1
            if self.language_pref is None and self._english_streak >= 2:
                self._set_language("english", auto=True)  # two English turns in a row: they prefer English
        else:
            if spoke_hindi(user_text):
                self._english_streak = 0
                if self._lang_auto:
                    self._set_language(None, auto=False)  # back to Hindi on their own: follow them
        if self.english:
            return ENGLISH_AUTO_NOTE if self._lang_auto else ENGLISH_PREFERRED_NOTE
        if self.language_pref != "hindi" and spoke_english(user_text):
            return ENGLISH_TURN_NOTE
        return None

    def _system_line(self, key: str) -> tuple[str, str]:
        """(clip key, text) of a fixed line ("silence_prompt", "silence_goodbye", "closing", "error") in the
        call's language."""
        if self.english:
            en_key = "inbound_closing" if key == "closing" and self.direction == "inbound" else key
            return f"en_{en_key}", ENGLISH_LINES.get(en_key, "")
        texts = {
            "silence_prompt": self.cfg.get("silence_prompt_text", ""),
            "silence_goodbye": self.cfg.get("silence_goodbye_text", ""),
            "closing": self._closing_text(),
            "error": ERROR_LINE,
        }
        return key, texts.get(key, "")

    # ------------------------------------------------------------------ mood
    def _next_mood(self, user_text: str) -> tuple[str | None, int]:
        """The customer's mood after this turn (and the count of one- or two-word answers in a row)."""
        if not self.cfg.get("mood_matching", True):
            return None, 0
        found = detect_mood(user_text)
        short = self._short_answers + 1 if len(_norm_words(user_text)) <= 2 else 0
        mood = self.mood
        if found == "friendly":
            mood = "friendly"
        elif found in ("irritated", "busy"):
            if not (mood == "irritated" and found == "busy"):  # irritated outranks busy
                mood = found
        elif mood in (None, "brief", "friendly"):
            if short >= 3:
                mood = "brief"
            elif mood == "brief":
                mood = None  # they are talking normally again
        return mood, short

    def _update_mood(self, user_text: str) -> str | None:
        """Track the mood; returns the note for this reply. A calmer / quicker voice follows the mood."""
        before = self.mood
        self.mood, self._short_answers = self._next_mood(user_text)
        if self.mood != before:
            log.info("session %s mood -> %s", self.uuid, self.mood or "neutral")
            if MOOD_PACE.get(self.mood, 1.0) != MOOD_PACE.get(before, 1.0):
                self._refresh_spare_tts()
        return MOOD_NOTES.get(self.mood) if self.mood else None

    def _language_preview(self, text: str) -> str | None | bool:
        """The language note _update_language would return for `text`, without changing anything; False when
        this turn would switch the call's language (then nothing is prepared ahead)."""
        if language_request(text):
            return False
        if spoke_english(text) and self.language_pref is None and self._english_streak + 1 >= 2:
            return False
        if self._lang_auto and spoke_hindi(text):
            return False
        if self.english:
            return ENGLISH_AUTO_NOTE if self._lang_auto else ENGLISH_PREFERRED_NOTE
        if self.language_pref != "hindi" and spoke_english(text):
            return ENGLISH_TURN_NOTE
        return None

    def _messages(self, history: list[dict], notes: list[str]) -> list[dict]:
        """What the LLM sees: the cached static prompt, the call's details, the conversation, then this
        turn's notes (mood first, language last: the last word is the strongest)."""
        return [{"role": "system", "content": self.system_prompt},
                {"role": "system", "content": self.lead_prompt}, *history[-24:],
                *({"role": "system", "content": n} for n in notes)]

    # ------------------------------------------------------------------ fast replies (prefetch)
    def _maybe_prefetch(self) -> None:
        """The customer just stopped talking (our audio shows 0.3 s of quiet, or STT reports the end of speech):
        let the LLM start on the reply now, from the latest partial transcript. The final transcript, ~0.7 s
        after the voice ends on Sarvam (measured), decides whether it is used."""
        if (not self.cfg.get("fast_replies", True) or not settings.openai_ready or self.ending or not self.greeted
                or self.greeting_playing or self.agent_audible() or self.responding() or self.respond_after_playback
                or self.english):
            # English calls: the partial transcripts come in Devanagari ("Around फाइव थाउजेंड") while the final
            # one is English, so an early reply could never match
            return
        partial = self._last_partial.strip()
        if not partial:
            return
        text = " ".join([*self.pending_user, partial]).strip()
        key = _turn_key(text)
        pf = self._prefetch
        if pf is not None and (pf.key == key or len(key.split()) < len(pf.key.split())):
            return  # already writing this (or STT briefly re-decoded a shorter version: keep the longer one)
        if sounds_unfinished(text) or hello_check(text) or self._is_background(self._voice_level(time.monotonic())):
            return  # those take another path
        lang_note = self._language_preview(text)
        if lang_note is False:
            return
        mood, _ = self._next_mood(text)
        notes = [n for n in ((MOOD_NOTES.get(mood) if mood else None), lang_note) if n]
        self._drop_prefetch("replaced")
        pf = _Prefetch(_turn_key(text), len(self.history), tuple(notes))
        messages = self._messages([*self.history, {"role": "user", "content": text}], notes)
        pf.task = asyncio.get_running_loop().create_task(pf.run(messages, self.cache_key))
        self._prefetch = pf

    def _drop_prefetch(self, why: str = "") -> None:
        pf, self._prefetch = self._prefetch, None
        if pf is not None:
            pf.cancel()

    def _claim_prefetch(self, user_text: str, history_len: int, notes: list[str]) -> _Prefetch | None:
        """The early reply, if it was written for exactly this turn (same words, same conversation, same notes)."""
        pf, self._prefetch = self._prefetch, None
        if pf is None:
            return None
        if pf.key != _turn_key(user_text):  # logged to tune matching later (local log only)
            log.info("early reply not used on %s: final words differ (%r vs %r)", self.uuid, pf.key,
                     _turn_key(user_text))
        if pf.key == _turn_key(user_text) and pf.history_len == history_len and pf.notes == tuple(notes) \
                and pf.error is None:
            self.prefetch_used += 1
            log.info("early reply used on %s (started %.0f ms before the final transcript)", self.uuid,
                     (time.monotonic() - pf.started) * 1000)
            return pf
        pf.cancel()
        return None

    # ------------------------------------------------------------------ quick replies
    def _repeated_hello(self, text: str, now: float) -> bool:
        """Another plain "Hello" right after we answered one: the same check (STT often splits "Hello?
        Hello?" in two). It neither interrupts our answer nor gets a second one."""
        return (bool(self._quick_replies) and now - self._quick_replies[-1] < QUICK_REPLY_ECHO_SEC
                and hello_check(text) == "hello")

    def _try_quick_reply(self, text: str, now: float) -> bool:
        """"Hello? Hello?" / "are you there?" / "can you hear me?" mid-call: answer at once with a ready clip
        ("जी, मेरी आवाज़ आ रही है?") instead of the LLM carrying on with the script."""
        if (not self.greeted or self.greeting_playing or self.pending_user or self.agent_audible()
                or self.responding()):
            return False
        kind = hello_check(text)
        if kind is None or sum(1 for t in self._quick_replies if now - t < 30) >= QUICK_REPLY_LIMIT:
            return False
        self._quick_replies.append(now)
        self.quick_replies += 1
        self._drop_prefetch("quick reply")
        lang = "english" if self.english else "hindi"
        reply = (HEAR_YOU_REPLY if kind == "hear_me" else HELLO_REPLY)[lang]
        key = ("en_" if self.english else "") + ("hear_you" if kind == "hear_me" else "hello_reply")
        log.info("quick reply on %s (%s): %s", self.uuid, kind, reply)
        self.history.append({"role": "user", "content": text})
        self._respond_task = asyncio.get_running_loop().create_task(self._speak_system(key, reply))
        return True

    # ------------------------------------------------------------------ background voices
    def _filter_on(self) -> bool:
        return bool(settings.background_voice_filter) and self.t.channel == "phone"

    def _voice_level(self, now: float) -> int:
        """Loudness of the speech STT is reporting now: the 75th-percentile RMS of the VOICED 20 ms frames
        since it began. Only voiced frames count, so a short answer inside a long window of silence is not
        mistaken for a quiet one (that dropped a real "between 9 to 5" on a live call)."""
        start = self._stt_speech_start_at if self._stt_speech_start_at > self._last_final_at else self._last_final_at
        # a little before STT noticed the speech (its start is reported late), never into the previous sentence
        since = max(start - 0.4, self._last_final_at) if start else now - 4.0
        floor = max(VOICED_RMS, int(self.vad.noise_floor * 2))
        voiced = sorted(r for t, r in self._frame_levels if t >= since and r > floor)
        if len(voiced) < 3:
            return 0
        return voiced[int(0.75 * (len(voiced) - 1))]

    def _voice_ref(self) -> int | None:
        """How loud this customer usually is (median of their turns), or None before their first."""
        if not self._voice_levels:
            return None
        levels = sorted(self._voice_levels)
        return levels[len(levels) // 2]

    def _is_background(self, level: int) -> bool:
        """Near-silent: a TV far away, a whisper across the room. Only this is never answered.

        Measured on two live calls, customers' real answers and people talking near them overlap in
        loudness (a quiet customer: 1370 median, background talk: 1480), so ordinary loudness can't tell
        them apart. Unclear words are answered with a polite "could you repeat that?" instead (prompt).
        """
        return self._filter_on() and level < settings.min_voice_level

    def _too_far_to_barge(self, now: float) -> bool:
        """Cutting the agent off mid-sentence needs speech about as loud as this customer usually is (before
        we have heard them: clearly loud speech). A quiet voice that can't interrupt loses nothing: its words
        are answered as soon as the agent's sentence ends."""
        if not self._filter_on():
            return False
        ref = self._voice_ref()
        need = ref * settings.near_field_ratio if ref is not None else settings.first_barge_level
        return self._voice_level(now) < max(settings.min_voice_level, need)

    # ------------------------------------------------------------------ greeting
    async def _greet(self) -> None:
        # Inbound: the caller already waits for us, so the short "hello" window starts now and overlaps the
        # greeting synthesis. Outbound: the customer gets the full window after it (they often say "Hello?").
        inbound_deadline = time.monotonic() + INBOUND_HELLO_WAIT_SEC if self.direction == "inbound" else None
        try:
            if self.greeting_pcm is None and self.greeting_text:
                self.greeting_pcm = await tts_rest(self.greeting_text, self.cfg, self.t.out_rate)
        except Exception as e:  # noqa: BLE001
            # The customer would hear silence: end now instead of logging a greeting nobody heard.
            log.error("greeting TTS failed on %s: %s", self.uuid, e)
            await self.t.send_event({"type": "error", "detail": f"Voice (TTS) failed: {e}"})
            await self.end("error")
            return
        # Let the customer say "Hello?" first (common on Indian calls) — but don't wait long.
        await self._set_state("listening")
        deadline = inbound_deadline or time.monotonic() + HELLO_WAIT_SEC
        while time.monotonic() < deadline and not self.user_speaking:
            await asyncio.sleep(0.05)
        if self.user_speaking:  # already talking: let them finish (bounded), then greet
            hard = time.monotonic() + 2.5
            while self.user_speaking and time.monotonic() < hard:
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.15)
        if self.ending:
            return
        self.greeted = True
        if not self.greeting_text:  # no opening line configured: answer whatever they said first
            await self._after_agent_turn(end_call=False)
            return
        if self.greeting_text:
            self.greeting_playing = True
            self._turn_seq += 1
            self._active_turn = self._turn_seq
            await self._set_state("speaking")
            await self._play_pcm(self.greeting_pcm or b"", self._active_turn)
            self.history.append({"role": "assistant", "content": self.greeting_text})
            self.agent_words += len(self.greeting_text.split())
            self._agent_turn_db_id = await repo.run_db(repo.add_turn, self.call_id, "agent", self.greeting_text,
                                                       self.offset_ms())
            await self._emit_transcript("agent", self.greeting_text, True)
            await self._wait_playback(self._active_turn)
            self.greeting_playing = False
            if not self.ending and self.state == "speaking":
                await self._after_agent_turn(end_call=False)

    # ------------------------------------------------------------------ audio in
    def on_user_audio(self, raw: bytes) -> None:
        if self.ending:
            return
        if self.stt:
            self.stt.send_audio(raw)
        pcm = self.t.user_pcm16(raw)
        level = pcm_rms(pcm)
        self._frame_levels.append((time.monotonic(), level))
        if level > max(VOICED_RMS, int(self.vad.noise_floor * 2)):
            self._last_voiced_at = time.monotonic()
        rec = self._user_to_rec(pcm)
        self.recorder.add_user(rec)
        if bus.has_audio_listeners(self.uuid):
            bus.publish_audio(self.uuid, b"\x00" + rec)
        self._user_level = max(self._user_level * 0.6, rms_level(pcm))
        # Local energy VAD = backup barge-in when partial transcripts are slow. STT is the ground
        # truth: energy without any STT speech is line noise (street, fan, shop) and must neither
        # interrupt the agent nor hold off the silence timer.
        ev = self.vad.update(pcm)
        now = time.monotonic()
        if ev == "start":
            self._local_speech_since = now
        elif ev == "end":
            self._local_speech_since = None
        if self.vad.speaking and self._local_speech_since:
            if now - self._last_stt_speech < 1.5:
                self.last_user_activity = now
            elif now - self._local_speech_since > 3.0:
                self.vad.recalibrate(pcm)  # steady noise: raise the noise floor to this level
                self._local_speech_since = None
        # Backup barge-in (partials slow or absent): STT's own VAD says the customer started talking
        # AFTER the agent began speaking and is still talking 1 s later. Line noise can't fake that, and
        # people talking near the customer (quieter than the customer) don't count.
        if (self.user_speaking and self._stt_speech_start_at > self._speaking_since
                and now - self._stt_speech_start_at > 1.0 and self._can_barge_in()
                and not self.greeting_playing and not self._barge_pending and not self._too_far_to_barge(now)):
            self._barge_pending = True
            spawn(self.interrupt("speech_overlap"))
        self._maybe_listening_sound(now)
        # The voice stopped: start the reply from the latest partial now. STT's own end of speech comes ~0.5 s
        # later and its final transcript ~0.7 s after the voice ends (measured), which is when we'd start anyway.
        if self.user_speaking and self._last_partial and 0.3 <= now - self._last_voiced_at <= 1.5:
            self._maybe_prefetch()

    # ------------------------------------------------------------------ listening sounds
    def _maybe_listening_sound(self, now: float) -> None:
        """A soft "हम्म" / "जी" in a short pause of a long customer sentence: they hear someone is listening,
        as on a real call. Never while the agent talks or prepares a reply, at most two per sentence."""
        if (not self.cfg.get("listening_sounds", True) or self.ending or not self.greeted or self.greeting_playing
                or not self.user_speaking or self.agent_audible() or self.responding()
                or self._listening_in_segment >= 2):
            return
        if now - self._stt_speech_start_at < LISTEN_AFTER_SEC or now - self._last_listening_at < LISTEN_GAP_SEC:
            return
        if len(_norm_words(self._last_partial)) < 8:  # a real sentence, not a hesitant "हाँ... वो..."
            return
        if not LISTEN_PAUSE[0] <= now - self._last_voiced_at <= LISTEN_PAUSE[1]:
            return
        prefix = "en_listen_" if self.english else "listen_"
        keys = [k for k in self.clips if k.startswith(prefix)]
        if not keys:
            return
        self._last_listening_at = now
        self._listening_in_segment += 1
        spawn(self._play_listening(self.clips[random.choice(keys)]))

    async def _play_listening(self, pcm: bytes) -> None:
        """Not a turn: no state change and no playback clock, so the customer keeps the floor and their
        reply is never held back by it."""
        if self.ending or not pcm:
            return
        rec = self._agent_to_rec(pcm)
        self.recorder.add_agent(rec)
        if bus.has_audio_listeners(self.uuid):
            bus.publish_audio(self.uuid, b"\x01" + rec)
        await self.t.send_audio(pcm)

    def _can_barge_in(self) -> bool:
        return bool(self.cfg.get("barge_in_enabled", True)) and (self.agent_audible() or self.responding())

    # ------------------------------------------------------------------ STT events
    async def _on_stt(self, kind: str, data: dict) -> None:
        if self.ending:
            return
        now = time.monotonic()
        if kind in ("speech_start", "partial", "final"):
            self._last_stt_speech = now
        if kind == "speech_start":
            self.user_speaking = True
            self._stt_speech_start_at = now
            self.last_user_activity = now
            self.silence_stage = 0
            self._listening_in_segment = 0
            self._drop_prefetch("customer went on talking")
            if self._reply_user_text and self.responding() and not self._turn_first_audio                     and now - self._last_voiced_at < 0.3:
                await self._hold_reply()  # they paused mid-thought and went on: don't start talking over them
        elif kind == "speech_end":
            self.user_speaking = False
            self.last_user_activity = now
            self._last_speech_end = now  # reply latency is measured from here
            self._maybe_prefetch()  # start the reply now; the final transcript confirms it ~0.4 s later
        elif kind == "partial":
            text = data.get("text", "")
            if not text:
                return
            self.last_user_activity = now
            self.silence_stage = 0
            self._last_partial = text
            pf = self._prefetch
            if pf is not None and pf.key != _turn_key(" ".join([*self.pending_user, text])):
                self._drop_prefetch("words changed")
            await self._emit_transcript("user", text, False)
            if (self._can_barge_in() and not is_backchannel(text, greeting=self.greeting_playing or not self.greeted)
                    and not self._too_far_to_barge(now) and not self._repeated_hello(text, now)):
                if len(_norm_words(text)) >= 2 or not self.greeting_playing:
                    await self.interrupt("speech")
        elif kind == "final":
            text = data.get("text", "")
            self.user_speaking = False
            self._last_partial = ""
            if not text:
                return
            level = self._voice_level(now)
            self._last_final_at = now
            if self._is_background(level):
                # near-silent: shown live (so the team sees why), never answered, not saved as their words
                self.background_ignored += 1
                log.info("near-silent speech ignored on %s (level %d): %s", self.uuid, level, text)
                await self._emit_transcript("user", f"(background voice, ignored) {text}", True)
                return
            if self._filter_on():
                self._voice_levels.append(level)
            self.last_user_activity = now
            self.silence_stage = 0
            self.user_words += len(text.split())
            await self._emit_transcript("user", text, True)
            # fire-and-forget: a slow DB write must never delay the reply or the STT reader
            spawn(repo.run_db(repo.add_turn, self.call_id, "user", text, self.offset_ms()))
            if self._repeated_hello(text, now):
                return  # the same "Hello? Hello?" (split in two by STT) while we already answer it
            if self._try_quick_reply(text, now):
                return  # "Hello? Hello?": answered at once, in our voice, without the LLM
            if self._is_ignorable_backchannel(text, now):
                return  # "haan" / "ji" while the agent talks: logged, not answered, no interruption
            if not self.pending_user:
                self._pending_since = now
            self.pending_user.append(text)
            # a voice too quiet to cut the agent off (someone nearby, or a soft-spoken customer) waits for
            # the agent's sentence to end instead of interrupting it
            self._schedule_response(defer=self.agent_audible() and self._too_far_to_barge(now))
        elif kind == "error":
            log.error("STT failure on %s: %s", self.uuid, data)
            await self.t.send_event({"type": "error", "detail": data.get("message", "STT error")})
            if self.stt and self.stt.failed:
                await self._speak_clip_and_end("error", "error")

    def _is_ignorable_backchannel(self, text: str, now: float) -> bool:
        """A short acknowledgement said while the agent is talking is not a turn of its own.

        Exception: in the last second of the agent's audio it is usually the answer to the
        question being finished ("...बात कर सकते हैं?" -> "हाँ"), so it is answered after playback.
        """
        if not is_backchannel(text, greeting=not self.greeted or self.greeting_playing):
            return False
        if not self.greeted:
            return True  # "Hello?" before the greeting: the greeting is the answer
        if self.agent_audible():
            return self.play_until - now > 1.0
        return self.responding()  # agent is preparing a reply: "haan" adds nothing

    def _schedule_response(self, defer: bool = False) -> None:
        if not self.greeted or self.greeting_playing:
            # customer spoke during/before the greeting: answer after it (unless it was just "hello")
            self.respond_after_playback = True
            return
        if self.agent_audible() and (defer or not self.responding()):
            self.respond_after_playback = True  # answer once the agent finishes its sentence
            return
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = asyncio.get_running_loop().create_task(self._debounced_respond())

    async def _debounced_respond(self) -> None:
        try:
            await asyncio.sleep(0.04)
            if sounds_unfinished(" ".join(self.pending_user)):
                # trailed off mid-thought ("...और"): give them a moment before jumping in. If they go on,
                # speech_start holds the reply below and their next final restarts this debounce.
                grace = time.monotonic() + UNFINISHED_GRACE_SEC
                while not self.user_speaking and time.monotonic() < grace:
                    await asyncio.sleep(0.05)
            # customer continued talking: wait (bounded) — a new final restarts this debounce
            deadline = time.monotonic() + 2.5
            while self.user_speaking and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            if self.responding():
                await self.interrupt("new_input")
            self._start_response()
        except asyncio.CancelledError:
            pass

    def _take_pending(self) -> str:
        text = " ".join(t.strip() for t in self.pending_user if t.strip())
        self.pending_user.clear()
        self._pending_since = 0.0
        return text

    def _start_response(self) -> None:
        text = self._take_pending()
        if not text or self.ending:
            return
        now = time.monotonic()
        # measure from when the customer actually stopped talking (server VAD), not from now
        self._turn_user_end = self._last_speech_end if 0 < now - self._last_speech_end < 4 else now
        self._respond_task = asyncio.get_running_loop().create_task(self._respond(text))

    # ------------------------------------------------------------------ respond
    async def _respond(self, user_text: str) -> None:
        prev = self._cancelled_reply
        if prev is not None and not prev.done():
            # let the interrupted reply save its (cut) text first, so the history stays in order
            await asyncio.wait({prev}, timeout=1.0)
        self._cancelled_reply = None
        self._turn_seq += 1
        turn = self._turn_seq
        self._active_turn = turn
        self._agent_turn_text = ""
        self._agent_turn_started = False
        self._agent_turn_db_id = None
        self._turn_first_audio = 0.0
        self._filler_played = False
        history_len = len(self.history)
        self.history.append({"role": "user", "content": user_text})
        self._reply_user_text = user_text
        self.turns += 1
        await self._set_state("thinking")

        natural = bool(self.cfg.get("natural_variation", True))
        breath_due = False  # a sentence just ended: a short, slightly different breath before the next one

        async def on_audio(pcm: bytes) -> None:
            nonlocal breath_due
            if breath_due:
                breath_due = False
                # only when the sentences are queued back to back (otherwise there is a pause already)
                if self.play_until - time.monotonic() > 0.02 and turn == self._active_turn:
                    gap = int(self.t.out_rate * random.uniform(0.06, 0.18)) * 2
                    await self._agent_audio(b"\x00" * gap, turn, count_first=False)
            await self._agent_audio(pcm, turn)

        async def on_sentence_end(done: int) -> None:
            nonlocal breath_due
            breath_due = natural

        # the language note: for the whole rest of the call once they asked for English (or kept speaking it),
        # else for an English turn only (the Hindi greeting otherwise pulls the reply into Hinglish)
        language_note = self._update_language(user_text)
        mood_note = self._update_mood(user_text)
        notes = [n for n in (mood_note, language_note) if n]
        prefetch = self._claim_prefetch(user_text, history_len, notes)  # started when they stopped talking
        tts, connect_task = self._take_tts(on_audio)
        tts.on_sentence_end = on_sentence_end
        # fillers match the reply's language: none in front of a one-off English reply (the clips are Hindi),
        # English ones ("Hmm", "Right") once the call is in English
        filler_task = (asyncio.create_task(self._maybe_filler(turn, user_text))
                       if self.cfg.get("fillers_enabled", True) and (language_note is None or self.english)
                       else None)
        end_call = False
        voice_down = False
        spoken: list[str] = []
        use_rest = False
        try:
            messages = self._messages(self.history, notes)
            chunker = SentenceChunker()
            first = True
            raw = ""
            async for tok in self._reply_tokens(messages, turn, prefetch):
                raw += tok
                for sentence in chunker.feed(tok):
                    sentence, ended = strip_end_token(sentence)
                    end_call |= ended
                    sentence = self._no_long_numbers(sentence)
                    if first and self._filler_played:
                        sentence = self._strip_leading_filler(sentence)
                    first = False if sentence else first
                    if sentence:
                        spoken.append(sentence)
                        use_rest = await self._send_tts(tts, connect_task, sentence, use_rest, turn)
            tail, ended = strip_end_token(chunker.flush())
            end_call |= ended or strip_end_token(raw)[1]
            tail = self._no_long_numbers(tail)
            if tail:
                if first and self._filler_played:
                    tail = self._strip_leading_filler(tail)
                if tail:
                    spoken.append(tail)
                    use_rest = await self._send_tts(tts, connect_task, tail, use_rest, turn)
            if not use_rest and connect_task.done() and not connect_task.exception():
                await tts.finish()
            # Socket died or stayed silent: replay exactly the sentences whose audio never arrived.
            missing = tts.unfinished_sentences if (tts.error or not tts.audio_bytes) else []
            if missing and turn == self._active_turn:
                log.warning("TTS stream failed on %s (%s); REST voice for %d sentence(s)",
                            self.uuid, tts.error or "no audio", len(missing))
                for s in missing:
                    await self._play_rest(s, turn)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("respond failed on %s", self.uuid)
            if out_of_credit(e):
                # the voice can't speak any more this call: say the ready-made "line problem, we'll call back"
                # line (synthesised at the start) and hang up, instead of going half-silent
                voice_down = True
                await self.t.send_event({"type": "error", "detail": "Voice provider out of credit (Sarvam): add "
                                                                    "credits in the Sarvam dashboard."})
            else:
                if not spoken:
                    fallback = ENGLISH_LINES["cut"] if self.english else CUT_LINE
                    spoken.append(fallback)
                    with contextlib.suppress(Exception):
                        await self._play_rest(fallback, turn)
                await self.t.send_event({"type": "error", "detail": f"AI reply failed: {str(e)[:120]}"})
        finally:
            if filler_task:
                filler_task.cancel()
            if prefetch is not None:
                prefetch.cancel()  # cut off mid-reply: stop paying for tokens nobody hears
            if self._reply_user_text == user_text:
                self._reply_user_text = ""  # written and sent: no longer something to take back
            self._last_reply_had_filler = self._filler_played
            if not connect_task.done():
                connect_task.cancel()
            await tts.close()
            text = " ".join(spoken).strip()
            cut = turn != self._active_turn  # barge-in happened while generating
            if text and (not cut or self._turn_first_audio):
                self._agent_turn_text = text
                self.history.append({"role": "assistant", "content": text})
                self.agent_words += len(text.split())
                latency = int((self._turn_first_audio - self._turn_user_end) * 1000) if self._turn_first_audio else None
                if latency is not None and not cut:
                    self.latencies.append(latency)
                db_id = await repo.run_db(
                    lambda: repo.add_turn(self.call_id, "agent", text, self.offset_ms(), interrupted=cut,
                                          latency_ms=latency)
                )
                # an interrupted turn is fully recorded here; interrupt() must not mark it again
                self._agent_turn_db_id = None if cut else db_id
                await self._emit_transcript("agent", text, True, interrupted=cut)

        if voice_down and not self.ending:
            log.error("voice provider out of credit on %s: ending the call politely", self.uuid)
            spawn(self._speak_clip_and_end("error", "error"))  # its own task: this reply is finished
            return
        if turn != self._active_turn or self.ending:
            return
        await self._wait_playback(turn)
        if turn == self._active_turn and not self.ending:
            await self._after_agent_turn(end_call=end_call)

    async def _reply_tokens(self, messages: list[dict], turn: int,
                            prefetch: _Prefetch | None = None) -> AsyncIterator[str]:
        """LLM tokens with a first-token deadline: a slow OpenAI must not leave the line dead.

        A reply started early (prefetch) is used first, if its first token comes in time; otherwise it is
        dropped and a fresh request runs. Attempt 1 waits 4 s for the first token; then a filler keeps the
        line alive and one retry gets 6 s. After that the caller's error path apologises.
        """
        if prefetch is not None:
            early = prefetch.stream()
            try:
                first = await asyncio.wait_for(anext(early), max(0.5, 4.0 - (time.monotonic() - prefetch.started)))
            except (StopAsyncIteration, asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
                prefetch.cancel()
                log.info("early reply unusable on %s (%s): asking again", self.uuid, type(e).__name__)
            else:
                yield first
                async for tok in early:
                    yield tok
                return
        for attempt, deadline in enumerate((4.0, 6.0)):
            agen = stream_reply(messages, self.cache_key)
            try:
                first = await asyncio.wait_for(anext(agen), deadline)
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                with contextlib.suppress(Exception):
                    await agen.aclose()
                log.warning("LLM first token > %.0f s on %s (attempt %d)", deadline, self.uuid, attempt + 1)
                if attempt == 0:
                    if not self._filler_played and not self._turn_first_audio and turn == self._active_turn:
                        user_text = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
                        clip = self._pick_filler(user_text)
                        if clip:
                            self._filler_played = True
                            await self._agent_audio(clip, turn, count_first=False)
                    continue
                raise
            yield first
            async for tok in agen:
                yield tok
            return

    # ------------------------------------------------------------------ TTS socket pool
    def _prepare_spare_tts(self) -> None:
        """Keep one Bulbul socket connected and configured, so a reply never waits for a handshake."""
        if self.ending or not settings.sarvam_ready:
            return
        spare = TTSStream(self._voice_cfg(), self.t.out_rate, self._discard_audio)
        self._spare_tts = spare
        self._spare_task = asyncio.get_running_loop().create_task(spare.connect())

    def _refresh_spare_tts(self) -> None:
        """The open spare socket was set up for an older language / mood: replace it."""
        old = self._spare_tts
        self._spare_tts = self._spare_task = None
        if old is not None:
            spawn(old.close())
        self._prepare_spare_tts()

    def _voice_cfg(self) -> dict:
        """The voice for the next reply: the call's language, a calmer / quicker pace for an irritated /
        busy customer, and (natural_variation) a slightly different pace and expressiveness each time, so
        replies don't all sound stamped from one mould."""
        cfg = dict(self.tts_cfg)
        pace = float(cfg.get("tts_pace") or 1.0) * MOOD_PACE.get(self.mood or "", 1.0)
        temp = float(cfg.get("tts_temperature") or 0.6)
        if self.cfg.get("natural_variation", True):
            pace *= random.uniform(0.97, 1.03)
            temp += random.uniform(-0.05, 0.05)
        cfg["tts_pace"] = round(min(2.0, max(0.5, pace)), 3)
        cfg["tts_temperature"] = round(min(1.0, max(0.3, temp)), 3)
        return cfg

    @staticmethod
    async def _discard_audio(pcm: bytes) -> None:
        return None

    def _take_tts(self, on_audio: Callable[[bytes], Awaitable[None]]) -> tuple[TTSStream, asyncio.Task]:
        spare, task = self._spare_tts, self._spare_task
        self._spare_tts = self._spare_task = None
        usable = (spare is not None and task is not None and not spare.done.is_set()
                  and not (task.done() and (task.cancelled() or task.exception() is not None)))
        if usable:
            spare.on_audio = on_audio
            tts, connect_task = spare, task
        else:
            if spare is not None:
                spawn(spare.close())
            tts = TTSStream(self._voice_cfg(), self.t.out_rate, on_audio)
            connect_task = asyncio.get_running_loop().create_task(tts.connect())
        self._prepare_spare_tts()  # the next reply gets a fresh, already-open socket
        return tts, connect_task

    async def _send_tts(self, tts: TTSStream, connect_task: asyncio.Task, sentence: str, use_rest: bool,
                        turn: int) -> bool:
        if not use_rest:
            try:
                await connect_task
                await tts.send_sentence(sentence)
                return False
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("TTS websocket unavailable (%s); using REST", e)
        await self._play_rest(sentence, turn)
        return True

    def _no_long_numbers(self, sentence: str) -> str:
        """Safety net behind the prompt rule: a phone number or other long digit string is never spoken or
        saved as said ("आपका WhatsApp नंबर +9198750 03040 है" -> "आपका WhatsApp नंबर यही है")."""
        if not sentence or not LONG_DIGITS.search(sentence):
            return sentence
        return re.sub(r"[ 	]{2,}", " ", LONG_DIGITS.sub("this same one" if self.english else "यही", sentence)).strip()

    def _strip_leading_filler(self, sentence: str) -> str:
        fillers = ENGLISH_FILLERS if self.english else self.cfg.get("filler_words") or []
        words = [w.strip().lower() for w in fillers]
        s = sentence.strip()
        for w in sorted(words, key=len, reverse=True):
            if w and s.lower().startswith(w):
                rest = s[len(w):].lstrip(" ,.।!…-")
                return rest if rest else s
        return s

    def _pick_filler(self, user_text: str) -> bytes | None:
        """A filler that fits the moment: "thinking" (हम्म, एक second) after a question, an
        acknowledgement (जी, अच्छा) otherwise; never the one used just before. English calls use the
        English set ("Hmm", "Right", "Sure")."""
        prefix = "en_filler_" if self.english else "filler_"
        keys = [k for k in self.clips if k.startswith(prefix)]
        if not keys:
            return None
        thinking = [k for k in keys if any(m in self._filler_texts.get(k, "").lower() for m in _THINKING_MARKERS)]
        acks = [k for k in keys if k not in thinking]
        pool = (thinking if is_question(user_text) else acks) or keys
        last = self._recent_fillers[-1] if self._recent_fillers else None
        key = random.choice([k for k in pool if k != last] or pool)
        self._recent_fillers.append(key)
        return self.clips[key]

    async def _maybe_filler(self, turn: int, user_text: str = "") -> None:
        try:
            delay = max(0.2, float(self.cfg.get("filler_delay_ms", 800)) / 1000)
            if self._last_reply_had_filler:
                delay += 0.6  # a filler before every reply is a tic: only for a clearly slow one
            await asyncio.sleep(delay)
            if turn != self._active_turn or self._turn_first_audio or self.ending:
                return
            if time.monotonic() - self._last_listening_at < 1.5:
                return  # a listening sound just played: a filler on top would be a stutter
            clip = self._pick_filler(user_text)
            if not clip:
                return
            self._filler_played = True
            # one atomic send: a chunked filler could interleave with the reply's first audio
            await self._agent_audio(clip, turn, count_first=False)
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------ audio out
    async def _agent_audio(self, pcm: bytes, turn: int, *, count_first: bool = True) -> None:
        if turn != self._active_turn or self.ending or not pcm:
            return
        now = time.monotonic()
        if count_first and not self._turn_first_audio:
            self._turn_first_audio = now
        if self.state != "speaking":
            await self._set_state("speaking")
        start = max(now, self.play_until)
        dur = len(pcm) / 2 / self.t.out_rate
        self.play_until = start + dur
        self.agent_audio_ms += int(dur * 1000)
        self._level_q.append((start, start + dur, rms_level(pcm)))
        rec = self._agent_to_rec(pcm)
        self.recorder.add_agent(rec)
        if bus.has_audio_listeners(self.uuid):
            bus.publish_audio(self.uuid, b"\x01" + rec)
        await self.t.send_audio(pcm)

    async def _play_pcm(self, pcm: bytes, turn: int, *, count_first: bool = True) -> None:
        # chunk so barge-in can stop mid-clip on transports that pace sending
        step = self.t.out_rate * 2 // 5  # 200 ms
        for i in range(0, len(pcm), step):
            if turn != self._active_turn:
                return
            await self._agent_audio(pcm[i:i + step], turn, count_first=count_first)

    async def _play_rest(self, text: str, turn: int) -> None:
        pcm = await tts_rest(text, self.tts_cfg, self.t.out_rate)
        await self._play_pcm(pcm, turn)

    async def _wait_playback(self, turn: int) -> None:
        while turn == self._active_turn and not self.ending:
            remaining = self.play_until - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, 0.1))

    async def _after_agent_turn(self, *, end_call: bool) -> None:
        if end_call:
            await self.end("agent_ended")
            return
        await self._set_state("listening")
        self.last_user_activity = max(self.last_user_activity, time.monotonic())
        if self.respond_after_playback:
            self.respond_after_playback = False
            if self.pending_user:  # backchannels were already filtered when they arrived
                self._turn_user_end = time.monotonic()
                self._start_response()

    async def _hold_reply(self) -> None:
        """The customer paused (STT closed their sentence) and went on talking before our reply made a sound.
        Take the reply back and answer everything they said together once they finish, instead of starting
        to talk over them. Their words go back in the queue, so nothing is lost even if no more words come
        (the watchdog answers stranded input)."""
        text = self._reply_user_text
        if not text:
            return
        log.info("reply held on %s: the customer went on talking", self.uuid)
        self.replies_held += 1
        self._reply_user_text = ""
        await self.interrupt("customer went on")
        if self.history and self.history[-1] == {"role": "user", "content": text}:
            self.history.pop()  # the cancelled reply's question goes back with the rest of what they say
        self.turns = max(0, self.turns - 1)
        self.pending_user.insert(0, text)
        self._pending_since = self._pending_since or time.monotonic()

    async def interrupt(self, why: str) -> None:
        """Barge-in: stop the agent immediately.

        All state changes happen synchronously first, so input arriving while this coroutine
        awaits already sees a silent, free agent (no stale respond_after_playback flags). The
        cancelled reply saves its cut text in its own cleanup; the next reply waits for that.
        """
        self._barge_pending = False
        if not (self.agent_audible() or self.responding()) or self.ending:
            return
        log.info("barge-in on %s (%s)", self.uuid, why)
        if why in ("speech", "speech_overlap", "new_input"):
            self.barge_ins += 1
        self._drop_prefetch("barge-in")
        was_audible = self.agent_audible()
        unplayed_ms = max(0, int((self.play_until - time.monotonic()) * 1000))
        self._active_turn = -2  # invalidates audio of the current turn
        task = self._respond_task
        self._respond_task = None
        self.play_until = time.monotonic()
        self.agent_audio_ms = max(0, self.agent_audio_ms - unplayed_ms)
        self._level_q.clear()
        self.recorder.interrupt_agent()
        self.greeting_playing = False
        if task and not task.done():
            task.cancel()
            self._cancelled_reply = task
        await self.t.clear()
        if bus.has_audio_listeners(self.uuid):
            bus.publish_audio(self.uuid, '{"type":"clear"}')
        if was_audible and self._agent_turn_db_id:
            spawn(repo.run_db(repo.mark_turn_interrupted, self._agent_turn_db_id, None))
            await self._emit_transcript("agent", self._agent_turn_text or "", True, interrupted=True)
        await self._set_state("listening")

    # ------------------------------------------------------------------ watchdog
    def _closing_text(self) -> str:
        """Said when the call hits its maximum length. Inbound callers get their own line and limit (they
        chose to call, so a real enquiry may run longer; the line promises an expert call-back)."""
        key = "inbound_closing_line" if self.direction == "inbound" else "closing_line"
        return fill_template(self.cfg.get(key) or self.cfg.get("closing_line", ""), self.cfg, self.lead)

    async def _watchdog(self) -> None:
        if self.direction == "inbound":
            max_sec = int(self.cfg.get("inbound_max_call_sec") or 600)
        else:
            max_sec = int(self.cfg.get("max_call_sec") or 300)
        timeout = float(self.cfg.get("silence_timeout_sec") or 10)
        final_wait = float(self.cfg.get("silence_final_wait_sec") or 3)
        try:
            while not self.ending:
                await asyncio.sleep(0.25)
                now = time.monotonic()
                if now - self.t0 > max_sec:
                    await self._speak_clip_and_end("closing", "max_duration")
                    return
                if self.user_speaking and now - self.last_user_activity > 6:
                    self.user_speaking = False
                if not self.greeted or self.greeting_playing:
                    continue
                noise_only = now - self._last_stt_speech > 3.0  # energy VAD alone is not speech
                busy = (self.user_speaking or (self.vad.speaking and not noise_only)
                        or self.responding() or self.agent_audible())
                if (self.pending_user and not busy and self._pending_since and now - self._pending_since > 1.5
                        and not (self._debounce_task and not self._debounce_task.done())):
                    # self-heal: input that nothing is going to answer (lost race) gets answered now
                    log.info("answering stranded input on %s", self.uuid)
                    self.respond_after_playback = False
                    self._start_response()
                    continue
                if busy or self.pending_user:
                    continue
                quiet_since = max(self.last_user_activity, self.play_until)
                if self.silence_stage == 0 and now - quiet_since >= timeout:
                    self.silence_stage = 1
                    await self._speak_system(*self._system_line("silence_prompt"))
                    self._silence_prompt_end = self.play_until
                elif self.silence_stage == 1 and now - max(quiet_since, self._silence_prompt_end) >= final_wait:
                    await self._speak_clip_and_end("silence_goodbye", "silence_timeout")
                    return
        except asyncio.CancelledError:
            pass

    async def _speak_system(self, clip_key: str, text: str, *, end_after: bool = False) -> None:
        """Play a canned line (silence prompt etc.) and log it to the transcript."""
        if not text:
            return
        self._turn_seq += 1
        turn = self._turn_seq
        self._active_turn = turn
        pcm = self.clips.get(clip_key)
        try:
            if pcm is None:
                pcm = await tts_rest(text, self.tts_cfg, self.t.out_rate)
        except Exception as e:  # noqa: BLE001
            log.warning("system line TTS failed: %s", e)
            pcm = b""
        self.history.append({"role": "assistant", "content": text})
        self._agent_turn_text = text
        self._agent_turn_db_id = await repo.run_db(repo.add_turn, self.call_id, "agent", text, self.offset_ms())
        await self._emit_transcript("agent", text, True)
        await self._play_pcm(pcm, turn)
        await self._wait_playback(turn)
        if turn == self._active_turn and not self.ending and not end_after:
            await self._after_agent_turn(end_call=False)

    async def _speak_clip_and_end(self, clip_key: str, reason: str) -> None:
        if self.ending:
            return
        if self.responding():
            await self.interrupt("ending")
        clip_key, text = self._system_line(clip_key)  # in the call's language (English calls: en_* clips)
        if text:
            await self._speak_system(clip_key, text, end_after=True)
        await self.end(reason)

    async def _level_reporter(self) -> None:
        try:
            while not self.ending:
                await asyncio.sleep(0.125)
                now = time.monotonic()
                while self._level_q and self._level_q[0][1] < now:
                    self._level_q.popleft()
                agent = self._level_q[0][2] if self._level_q and self._level_q[0][0] <= now else 0.0
                user = self._user_level
                self._user_level *= 0.5
                bus.publish({"type": "level", "uuid": self.uuid, "user": round(user, 3), "agent": round(agent, 3)})
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------ end
    async def end(self, reason: str) -> None:
        """End the call. Shutdown runs in its own shielded task so a cancelled caller
        (e.g. a dropped websocket handler) can never skip saving the call."""
        if self.ending:
            await self.ended.wait()
            return
        self.ending = True
        self.end_reason = reason
        log.info("session %s ending: %s", self.uuid, reason)
        self._shutdown_task = spawn(self._shutdown(reason))
        await asyncio.shield(self._shutdown_task)

    async def _shutdown(self, reason: str) -> None:
        self._drop_prefetch("call ended")
        try:
            task = self._respond_task
            if task and not task.done():
                task.cancel()
            for t in self._tasks:
                t.cancel()
            if self._spare_task:
                self._spare_task.cancel()
            if self._spare_tts:
                with contextlib.suppress(Exception):
                    await self._spare_tts.close()
            if self.stt:
                with contextlib.suppress(Exception):
                    await self.stt.close()
            await self._set_state("ended")
            try:
                await self._finalize()  # save before telling the client, so its next fetch is complete
            except Exception:  # noqa: BLE001
                log.exception("finalize failed for %s", self.uuid)
            with contextlib.suppress(Exception):
                await self.t.close(reason)
        finally:
            self.ended.set()
            if self.on_finished:
                with contextlib.suppress(Exception):
                    await self.on_finished(self)

    async def _finalize(self) -> None:
        duration = time.monotonic() - self.t0
        path = settings.recordings_dir / f"{self.uuid}.wav"
        try:
            await asyncio.to_thread(self.recorder.save, path)
            rec_path: str | None = path.relative_to(settings.recordings_dir.parent).as_posix() if path.exists() else None
        except Exception as e:  # noqa: BLE001
            log.error("recording save failed: %s", e)
            rec_path = None
        metrics: dict[str, Any] = {
            "turns": self.turns,
            "user_words": self.user_words,
            "agent_words": self.agent_words,
            "agent_audio_ms": self.agent_audio_ms,  # 0 = the customer never heard the agent
        }
        if self.latencies:
            metrics["avg_latency_ms"] = int(sum(self.latencies) / len(self.latencies))
        metrics.update({
            "settings_rev": self.settings_rev, "barge_ins": self.barge_ins, "replies_held": self.replies_held,
            "quick_replies": self.quick_replies, "background_ignored": self.background_ignored,
            "early_replies": self.prefetch_used, "language": self.language_pref or "default",
            "mood": self.mood or "neutral",
        })
        status, end_reason = "completed", self.end_reason
        if self.direction == "inbound" and self.agent_audio_ms == 0 and self.t.channel == "phone":
            # The caller never heard us (greeting failed, or they hung up first): a missed call the team
            # calls back, not an answered one. The analysis words the summary from the original reason.
            # (A browser rehearsal never counts as missed and nobody calls a test back: it stays completed.)
            status, end_reason = "failed", "error"
            metrics["session_end_reason"] = self.end_reason
        await repo.run_db(
            lambda: repo.update_call(
                self.uuid,
                status=status,
                end_reason=end_reason,
                ended_at=repo.now(),
                duration_sec=round(duration, 1),
                recording_path=rec_path,
                metrics=metrics,
            )
        )


def recording_abs_path(rel: str | None) -> Path | None:
    if not rel:
        return None
    base = settings.recordings_dir.resolve()
    p = (settings.recordings_dir.parent / rel).resolve()
    if not p.is_relative_to(base):  # never serve or delete files outside the recordings folder
        return None
    return p if p.exists() else None
