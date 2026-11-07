"""In-process pub/sub that feeds the Live Monitor WebSocket and audio listen-in."""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._audio_subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """The app's event loop: publishes from worker threads are handed over to it."""
        self._loop = loop

    def _off_loop(self) -> bool:
        if self._loop is None or self._loop.is_closed():
            return False
        try:
            return asyncio.get_running_loop() is not self._loop
        except RuntimeError:  # no loop in this thread (to_thread / threadpool worker)
            return True

    # ---- JSON events (all calls) ----
    def subscribe(self) -> asyncio.Queue:
        if self._loop is None:  # subscribers live on the app loop: remember it even without lifespan
            self._loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        msg = json.dumps(event, ensure_ascii=False, default=str)
        if self._off_loop():  # asyncio queues are not thread-safe
            self._loop.call_soon_threadsafe(self._deliver, msg)
        else:
            self._deliver(msg)

    def _deliver(self, msg: str) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:  # slow consumer: drop oldest
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except Exception:
                    pass

    # ---- audio listen-in (per call) ----
    def subscribe_audio(self, call_uuid: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=400)
        self._audio_subs[call_uuid].add(q)
        return q

    def unsubscribe_audio(self, call_uuid: str, q: asyncio.Queue) -> None:
        subs = self._audio_subs.get(call_uuid)
        if subs:
            subs.discard(q)
            if not subs:
                self._audio_subs.pop(call_uuid, None)

    def has_audio_listeners(self, call_uuid: str) -> bool:
        return bool(self._audio_subs.get(call_uuid))

    def publish_audio(self, call_uuid: str, frame: bytes | str) -> None:
        """frame: bytes = [1-byte track (0 user, 1 agent)] + PCM16; str = JSON control."""
        for q in list(self._audio_subs.get(call_uuid, ())):
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                pass


bus = EventBus()

# Fire-and-forget tasks must be referenced, or asyncio may garbage-collect them mid-run.
_background: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    task = asyncio.get_running_loop().create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task
