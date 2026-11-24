"""LLM brain: GPT-4o-mini, streamed and cut into speakable sentences."""
from __future__ import annotations

import contextlib
import json
import logging
import re
from typing import AsyncIterator

from openai import AsyncOpenAI

from ..config import get_settings
from .prompts import END_CALL_TOKEN

log = logging.getLogger("llm")
settings = get_settings()

_client: AsyncOpenAI | None = None


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key or "missing", timeout=20.0, max_retries=1)
    return _client


# Sentence boundaries for Hindi (।) and English; flush early on commas if the buffer is long
_HARD_END = re.compile(r"(.+?[।.!?](?:\s+|$))", re.S)
# Only the explicit marker: "[END_CALL]", "[END CALL]" or a bare "END_CALL" token — never plain "end call"
# text (that also appears inside phrases like "send call details").
_END_RE = re.compile(r"\[\s*END[_ ]?CALL\s*\]|(?<![A-Za-z_])END_CALL(?![A-Za-z_])", re.I)


def strip_end_token(text: str) -> tuple[str, bool]:
    if _END_RE.search(text):
        return _END_RE.sub("", text).strip(), True
    return text, False


_FIRST_CLAUSE = re.compile(r"(.+?[,，।.!?](?:\s+|$))", re.S)


class SentenceChunker:
    """Accumulates streamed tokens and yields speakable chunks for TTS.

    Latency: the FIRST chunk of a reply is cut at the first clause boundary (comma or
    sentence end, at least ~8 chars) or after ~40 chars at a word boundary, so the voice
    can start while the LLM is still writing. Later chunks are whole sentences.
    """

    def __init__(self, soft_limit: int = 90, first_min: int = 8, first_max: int = 40):
        self.buf = ""
        self.soft_limit = soft_limit
        self.first_min = first_min
        self.first_max = first_max
        self.emitted = 0

    def feed(self, token: str) -> list[str]:
        self.buf += token
        out = self._first_chunk() if self.emitted == 0 else []
        out += self._sentences()
        self.emitted += len(out)
        return out

    def _first_chunk(self) -> list[str]:
        if "[" in self.buf:  # possible [END_CALL]: let the normal path handle it
            return []
        m = _FIRST_CLAUSE.match(self.buf)
        if m and len(m.group(1).strip()) >= self.first_min:
            self.buf = self.buf[m.end():]
            return [m.group(1).strip()]
        if len(self.buf) >= self.first_max:
            cut = self.buf.rfind(" ", 0, len(self.buf) - 1)
            if cut >= self.first_min:
                chunk, self.buf = self.buf[:cut].strip(), self.buf[cut + 1:]
                return [chunk] if chunk else []
        return []

    def _sentences(self) -> list[str]:
        out: list[str] = []
        while True:
            # hold back while a possible [END_CALL] marker is still incomplete
            if "[" in self.buf and "]" not in self.buf[self.buf.rfind("["):]:
                head = self.buf[: self.buf.rfind("[")]
                m = _HARD_END.match(head)
                if m:
                    out.append(m.group(1).strip())
                    self.buf = self.buf[m.end():]
                    continue
                break
            m = _HARD_END.match(self.buf)
            if m and len(m.group(1).strip()) >= 2:
                out.append(m.group(1).strip())
                self.buf = self.buf[m.end():]
                continue
            if len(self.buf) > self.soft_limit:
                cut = max(self.buf.rfind(", "), self.buf.rfind("， "))
                if cut > 20:
                    out.append(self.buf[: cut + 1].strip())
                    self.buf = self.buf[cut + 1:]
                    continue
            break
        return [s for s in out if s]

    def flush(self) -> str:
        s, self.buf = self.buf.strip(), ""
        return s


# =====================================================================
# A list the model wrote anyway -> one sentence a person would say
# =====================================================================
_LIST_ITEM = re.compile(r"[ \t]*(?:[-*•–—]|\d{1,2}[.)])[ \t]+(?=\S)")     # "- x", "* x", "• x", "1. x", "2) x"
_MAYBE_ITEM = re.compile(r"[ \t]*(?:[-*•–—]|\d{1,2}[.)]?)?[ \t]*")         # could still become one of those
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_SENTENCE_END = "।.!?"
_ITEM_TAIL = " \t.;,।:"  # what a list line may end with: dropped (the commas between items replace it)
_BARE_MARKERS = ("-", "*", "•", "–", "—")


class ListFlattener:
    """Turns a bullet or numbered list in the reply into one flowing sentence, as the reply streams.

    The prompt forbids lists, but with lists in the business knowledge the model still writes one now and
    then ("which pouch types do you have?"). The voice then reads it line by line with a pause after every
    line: measured on a live call, a five-line list took 15 s. Here the markers and line breaks go, the items
    are joined with commas, and at most `max_items` are said, followed by "वगैरह" ("and more" in an English
    reply) - the two or three examples a person would give on the phone.

    Text without line breaks comes out exactly as it went in. The result is what is spoken AND what is kept
    as the agent's words (transcript, history), so the two never differ.
    """

    def __init__(self, max_items: int = 3):
        self.max_items = max_items
        self._line = ""      # the start of the current line, held back until we know if it is a list item
        self._kind = ""      # "" not known yet | "text" | "item" | "drop"
        self._held = ""      # punctuation / spaces at the end of an item so far (dropped if the line ends there)
        self._in_list = False
        self._items = 0
        self._dropped = 0
        self._tail = ""      # the last character given out
        self._hindi = False  # the reply has Devanagari in it: "वगैरह।" rather than "and more."

    def feed(self, piece: str) -> str:
        out: list[str] = []
        for ch in piece:
            if ch == "\r":
                continue
            if ch == "\n":
                self._end_line(out)
            else:
                self._char(ch, out)
        return "".join(out)

    def flush(self) -> str:
        """Whatever is still held when the reply ends (and the closing of an unfinished list)."""
        out: list[str] = []
        self._end_line(out)
        if self._in_list:
            self._close_list(out, last=True)
        return "".join(out)

    def _put(self, out: list[str], text: str) -> None:
        if text:
            out.append(text)
            self._tail = text[-1]
            if not self._hindi and _DEVANAGARI.search(text):
                self._hindi = True

    def _char(self, ch: str, out: list[str]) -> None:
        if self._kind == "text":
            self._put(out, ch)
        elif self._kind == "item":
            self._item_char(ch, out)
        elif self._kind == "":
            self._line += ch
            m = _LIST_ITEM.match(self._line)
            if m:
                self._begin_item(out)
                if self._kind == "item":
                    for c in self._line[m.end():]:
                        self._item_char(c, out)
            elif not _MAYBE_ITEM.fullmatch(self._line):
                self._begin_text(out)
                self._put(out, self._line)
        # "drop": an item beyond max_items is not said

    def _item_char(self, ch: str, out: list[str]) -> None:
        if ch in _ITEM_TAIL:
            self._held += ch
        else:
            self._put(out, self._held + ch)
            self._held = ""

    def _begin_item(self, out: list[str]) -> None:
        self._in_list = True
        self._items += 1
        if self._items > self.max_items:
            self._dropped += 1
            self._kind = "drop"
            return
        self._kind = "item"
        if self._items > 1:
            self._put(out, ", ")
        elif self._tail and not self._tail.isspace():
            self._put(out, " ")

    def _begin_text(self, out: list[str]) -> None:
        if self._in_list:
            self._close_list(out)
        elif self._tail and not self._tail.isspace():
            self._put(out, " ")  # a plain line break is just a space when spoken
        self._kind = "text"

    def _close_list(self, out: list[str], last: bool = False) -> None:
        if self._dropped:
            self._put(out, " वगैरह" if self._hindi else " and more")
        if self._tail not in _SENTENCE_END:
            self._put(out, "।" if self._hindi else ".")
        if not last:
            self._put(out, " ")
        self._in_list, self._items, self._dropped = False, 0, 0

    def _end_line(self, out: list[str]) -> None:
        if self._kind == "":
            held = self._line.strip()
            if held and held not in _BARE_MARKERS:  # a short line that never became an item ("12", "1.")
                self._begin_text(out)
                self._put(out, held)
        self._line, self._kind, self._held = "", "", ""


async def warmup() -> None:
    """Open the HTTPS connection to OpenAI ahead of the first reply (free metadata call)."""
    if not settings.openai_ready:
        return
    try:
        await client().models.retrieve(settings.openai_model)
    except Exception as e:  # noqa: BLE001
        log.debug("openai warmup failed: %s", e)


async def stream_reply(messages: list[dict], cache_key: str | None = None) -> AsyncIterator[str]:
    """Yield raw text tokens of the agent's next reply."""
    extra: dict = {"prompt_cache_key": cache_key} if cache_key else {}
    stream = await client().chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        max_tokens=settings.llm_max_tokens,
        temperature=settings.llm_temperature,
        stream=True,
        **extra,
    )
    try:
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
    finally:
        with contextlib.suppress(Exception):
            await stream.close()  # stopped early (a reply cut off, or a request that lost the race): end the HTTP stream


async def complete_json(messages: list[dict], max_tokens: int | None = None) -> dict:
    resp = await client().chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        max_tokens=max_tokens or settings.analysis_max_tokens,
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        log.warning("analysis returned non-JSON: %s", content[:200])
        return {}


__all__ = ["stream_reply", "complete_json", "SentenceChunker", "strip_end_token", "END_CALL_TOKEN"]
