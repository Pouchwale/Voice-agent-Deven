"""LLM brain: GPT-4o-mini, streamed and cut into speakable sentences."""
from __future__ import annotations

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
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content


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
