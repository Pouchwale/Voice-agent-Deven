"""Two more measures for a speech-recognition setup, next to tools/stt_benchmark.py (same clips, same streaming):
1. Speed: streamed in real time, the time from the end of the customer's speech (the last voiced frame sent) to the
   final transcript. Reply delay depends on it directly (the agent starts answering when the final arrives).
2. Words from noise: clips with NO speech (traffic at a speaking level, and the faint hiss of a quiet line) - every
   word transcribed is invented, and is something the agent might answer.
usage (from backend/): python tools/stt_latency_noise.py <benchmark folder> [setup ...]   (spends ~2 min STT per setup)
"""
from __future__ import annotations

import asyncio
import base64
import json
import statistics
import sys
import time
import urllib.parse
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stt_benchmark as b  # noqa: E402
from app.services.sarvam import _headers, stt_hints  # noqa: E402
from websockets.asyncio.client import connect  # noqa: E402


async def timed(mulaw: bytes, speech_end_s: float, setup: dict) -> tuple[str, float | None]:
    """Stream at real time; seconds from the end of speech to the first final transcript after it."""
    q = {"model": setup["model"], "language_code": "hi-IN", "mode": b.settings.sarvam_stt_mode,
         "stream_type": b.settings.sarvam_stt_stream_type, "encoding": "mulaw", "sample_rate": str(b.RATE),
         "endpointing": "vad", "silence_duration_ms": str(b.settings.sarvam_stt_silence_ms), "min_speech_duration_ms": "200"}
    q.update(stt_hints(setup["model"], setup["keyterms"], ""))
    url = f"{b.settings.sarvam_ws_url}/speech-to-text-realtime/ws?{urllib.parse.urlencode(q)}"
    finals: list[tuple[float, str]] = []
    async with connect(url, additional_headers=_headers(), open_timeout=10, ping_interval=None) as ws:
        async def reader():
            async for msg in ws:
                if isinstance(msg, bytes):
                    continue
                data = json.loads(msg)
                if (data.get("event") or data.get("type")) == "transcript.final" and (data.get("text") or "").strip():
                    finals.append((time.monotonic(), data["text"].strip()))
        task = asyncio.create_task(reader())
        audio = mulaw + b"\xff" * int(b.RATE * 2.0)
        t0 = time.monotonic()
        for i in range(0, len(audio), 480):  # real time: 60 ms frames every 60 ms
            await ws.send(json.dumps({"event": "audio_input", "audio": base64.b64encode(audio[i:i + 480]).decode()}))
            target = t0 + (i + 480) / b.RATE
            await asyncio.sleep(max(0.0, target - time.monotonic()))
        await asyncio.sleep(1.0)
        await ws.send(json.dumps({"event": "end"}))
        try:
            await asyncio.wait_for(task, 4)
        except asyncio.TimeoutError:
            task.cancel()
    end = t0 + speech_end_s
    after = [t - end for t, _ in finals if t > end]
    return " ".join(x for _, x in finals), (after[0] if after else None)


def last_voiced(x: np.ndarray) -> float:
    n = len(x) // 160
    frames = np.sqrt((x[: n * 160].reshape(n, 160) ** 2).mean(axis=1))
    on = np.where(frames > max(200.0, frames.max() * 0.1))[0]
    return (on[-1] + 1) * 160 / b.RATE if len(on) else len(x) / b.RATE


async def run(folder: Path, names: list[str]) -> None:
    clean = await b.synth_clean(folder)
    rng = np.random.default_rng(7)
    pad = int(0.3 * b.RATE)
    speech = [np.concatenate([np.zeros(pad), c]) for c in clean[:10]]
    noise_only = [b.traffic(int(5 * b.RATE), rng) * 2500 for _ in range(3)]  # traffic at a speaking level, no voice
    noise_only += [rng.standard_normal(int(5 * b.RATE)) * 60 for _ in range(3)]  # the faint hiss of a quiet line
    out = {}
    for name in names:
        setup = b.SETUPS[name]
        delays, words = [], []
        for x in speech:
            _, d = await timed(b.phone(x), last_voiced(x), setup)
            delays.append(d)
        for x in noise_only:
            heard, _ = await timed(b.phone(x), 0.0, setup)
            words.append(heard)
        got = [d for d in delays if d is not None]
        out[name] = {"final_after_speech_s": {"median": round(statistics.median(got), 2), "max": round(max(got), 2),
                                              "missing": len(delays) - len(got)},
                     "noise_only_clips_with_words": sum(bool(w.strip()) for w in words), "noise_only_clips": len(words),
                     "invented": [w for w in words if w.strip()]}
        print(name, json.dumps(out[name], ensure_ascii=False), flush=True)
    (folder / "latency_noise.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":  # spends Sarvam credit: never on import
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(run(Path(sys.argv[1]), sys.argv[2:] or list(b.SETUPS)))
