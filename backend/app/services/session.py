"""Real-time conversation engine shared by phone (Vobiz) and browser test calls.

Pipeline per customer turn:
  customer audio -> Sarvam Saaras v3 realtime STT (partials + server VAD)
  final transcript -> GPT-4o-mini (streamed, 120 max tokens, temp 0.6)
  -> sentence chunks -> Bulbul v3 streaming TTS -> transport (phone / browser)

Behaviours:
  * barge-in: customer talking over the agent (2+ words, or a non-backchannel word)
    stops agent audio instantly; short backchannels ("haan", "ji", "ok") don't.
  * fillers: if the first reply audio is late, a short pre-synthesised filler plays so the line
    never sounds dead: "हम्म, एक second" after a question, "जी, समझ गई" after a statement; never the
    same one twice in a row, and not on back-to-back replies unless the reply is clearly slow.
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
from . import repo
from .audio import CallRecorder, EnergyVAD, Resampler, rms_level
from .events import bus, spawn
from .llm import SentenceChunker, stream_reply, strip_end_token, warmup
from .prompts import (ENGLISH_PREFERRED_NOTE, ENGLISH_TURN_NOTE, fill_template, greeting_for, language_request,
                      lead_context, spoke_english, static_system_prompt)
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

# Question detection picks a "thinking" filler (हम्म, एक second) over an acknowledgement (जी, समझ गई).
_QUESTION_WORDS = {"क्या", "कितना", "कितने", "कितनी", "कैसे", "कैसा", "कब", "कहाँ", "कहां", "कौन", "कौनसा", "क्यों",
                   "kya", "kitna", "kitne", "kaise", "kab", "kahan", "kaun", "kyun", "price", "rate", "cost"}
_QUESTION_STARTS = {"what", "how", "when", "where", "which", "who", "why", "can", "could", "do", "does", "is",
                    "are", "will", "would"}
_THINKING_MARKERS = ("हम्म", "second", "सेकंड", "मिनट", "minute", "देखती", "देखता", "let me")


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
            "error": "माफ़ कीजिए, line पर थोड़ी problem आ रही है। हमारी team आपको जल्दी call back करेगी। धन्यवाद!",
        }
        if self.cfg.get("fillers_enabled", True):
            for i, w in enumerate(self.cfg.get("filler_words") or []):
                word = w.strip().rstrip(".…")
                if word:
                    texts[f"filler_{i}"] = word + "..."  # trailing "..." = drawn-out, thinking tone
                    self._filler_texts[f"filler_{i}"] = word

        async def one(key: str, text: str) -> None:
            if not text.strip():
                return
            try:
                self.clips[key] = await tts_rest(text, self.cfg, self.t.out_rate)
            except Exception as e:  # noqa: BLE001
                log.warning("clip %s failed: %s", key, e)

        await asyncio.gather(*(one(k, v) for k, v in texts.items()))

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
        # AFTER the agent began speaking and is still talking 1 s later. Line noise can't fake that.
        if (self.user_speaking and self._stt_speech_start_at > self._speaking_since
                and now - self._stt_speech_start_at > 1.0 and self._can_barge_in()
                and not self.greeting_playing and not self._barge_pending):
            self._barge_pending = True
            spawn(self.interrupt("speech_overlap"))

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
        elif kind == "speech_end":
            self.user_speaking = False
            self.last_user_activity = now
            self._last_speech_end = now  # reply latency is measured from here
        elif kind == "partial":
            text = data.get("text", "")
            if not text:
                return
            self.last_user_activity = now
            self.silence_stage = 0
            await self._emit_transcript("user", text, False)
            if self._can_barge_in() and not is_backchannel(text, greeting=self.greeting_playing or not self.greeted):
                if len(_norm_words(text)) >= 2 or not self.greeting_playing:
                    await self.interrupt("speech")
        elif kind == "final":
            text = data.get("text", "")
            self.user_speaking = False
            if not text:
                return
            self.last_user_activity = now
            self.silence_stage = 0
            self.user_words += len(text.split())
            await self._emit_transcript("user", text, True)
            # fire-and-forget: a slow DB write must never delay the reply or the STT reader
            spawn(repo.run_db(repo.add_turn, self.call_id, "user", text, self.offset_ms()))
            if self._is_ignorable_backchannel(text, now):
                return  # "haan" / "ji" while the agent talks: logged, not answered, no interruption
            if not self.pending_user:
                self._pending_since = now
            self.pending_user.append(text)
            self._schedule_response()
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

    def _schedule_response(self) -> None:
        if not self.greeted or self.greeting_playing:
            # customer spoke during/before the greeting: answer after it (unless it was just "hello")
            self.respond_after_playback = True
            return
        if self.agent_audible() and not self.responding():
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
        self.history.append({"role": "user", "content": user_text})
        self.turns += 1
        await self._set_state("thinking")

        async def on_audio(pcm: bytes) -> None:
            await self._agent_audio(pcm, turn)

        asked = language_request(user_text)
        if asked:
            self.language_pref = asked
        # the language note: sticky once they asked for English, else for an English turn only (the Hindi
        # greeting otherwise pulls the reply into Hinglish)
        if self.language_pref == "english":
            language_note = ENGLISH_PREFERRED_NOTE
        elif self.language_pref != "hindi" and spoke_english(user_text):
            language_note = ENGLISH_TURN_NOTE
        else:
            language_note = None
        tts, connect_task = self._take_tts(on_audio)
        # the filler clips are Hindi ("जी, समझ गई"): never in front of an English reply
        filler_task = (asyncio.create_task(self._maybe_filler(turn, user_text))
                       if self.cfg.get("fillers_enabled", True) and language_note is None else None)
        end_call = False
        spoken: list[str] = []
        use_rest = False
        try:
            messages = [{"role": "system", "content": self.system_prompt},
                        {"role": "system", "content": self.lead_prompt}, *self.history[-24:]]
            if language_note:
                messages.append({"role": "system", "content": language_note})
            chunker = SentenceChunker()
            first = True
            raw = ""
            async for tok in self._reply_tokens(messages, turn):
                raw += tok
                for sentence in chunker.feed(tok):
                    sentence, ended = strip_end_token(sentence)
                    end_call |= ended
                    if first and self._filler_played:
                        sentence = self._strip_leading_filler(sentence)
                    first = False if sentence else first
                    if sentence:
                        spoken.append(sentence)
                        use_rest = await self._send_tts(tts, connect_task, sentence, use_rest, turn)
            tail, ended = strip_end_token(chunker.flush())
            end_call |= ended or strip_end_token(raw)[1]
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
            if not spoken:
                fallback = "माफ़ कीजिए, आपकी बात थोड़ी cut हो गई। क्या आप दोबारा बता सकते हैं?"
                spoken.append(fallback)
                with contextlib.suppress(Exception):
                    await self._play_rest(fallback, turn)
            await self.t.send_event({"type": "error", "detail": f"AI reply failed: {str(e)[:120]}"})
        finally:
            if filler_task:
                filler_task.cancel()
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

        if turn != self._active_turn or self.ending:
            return
        await self._wait_playback(turn)
        if turn == self._active_turn and not self.ending:
            await self._after_agent_turn(end_call=end_call)

    async def _reply_tokens(self, messages: list[dict], turn: int) -> AsyncIterator[str]:
        """LLM tokens with a first-token deadline: a slow OpenAI must not leave the line dead.

        Attempt 1 waits 4 s for the first token; then a filler keeps the line alive and one
        retry gets 6 s. After that the caller's error path apologises.
        """
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
        spare = TTSStream(self.cfg, self.t.out_rate, self._discard_audio)
        self._spare_tts = spare
        self._spare_task = asyncio.get_running_loop().create_task(spare.connect())

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
            tts = TTSStream(self.cfg, self.t.out_rate, on_audio)
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

    def _strip_leading_filler(self, sentence: str) -> str:
        words = [w.strip().lower() for w in self.cfg.get("filler_words") or []]
        s = sentence.strip()
        for w in sorted(words, key=len, reverse=True):
            if w and s.lower().startswith(w):
                rest = s[len(w):].lstrip(" ,.।!…-")
                return rest if rest else s
        return s

    def _pick_filler(self, user_text: str) -> bytes | None:
        """A filler that fits the moment: "thinking" (हम्म, एक second) after a question, an
        acknowledgement (जी, समझ गई) otherwise; never the one used just before."""
        keys = [k for k in self.clips if k.startswith("filler_")]
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
        pcm = await tts_rest(text, self.cfg, self.t.out_rate)
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
                    await self._speak_system("silence_prompt", self.cfg.get("silence_prompt_text", ""))
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
                pcm = await tts_rest(text, self.cfg, self.t.out_rate)
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
        text = {
            "closing": self._closing_text(),
            "silence_goodbye": self.cfg.get("silence_goodbye_text", ""),
            "error": "",
        }.get(clip_key, "")
        if clip_key == "error":
            text = "माफ़ कीजिए, line पर थोड़ी problem आ रही है। हमारी team आपको जल्दी call back करेगी। धन्यवाद!"
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
