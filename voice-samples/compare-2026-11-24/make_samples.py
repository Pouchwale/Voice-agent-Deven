"""Voice test, 2026-11-24: does she change tone on English words, and do the fillers sound like her?

For each voice, three real reply lines are synthesised through the live streaming path (8 kHz, pace 0.92, like a phone call):
  now   - the text exactly as the app sends it today (English words in Latin letters)
  deva  - the same words written in Devanagari ("pieces" -> "पीसेस")
  prep  - today's text with Sarvam's enable_preprocessing (current voice and one Hinglish voice only)
plus the fillers for the current voice, made the way the app makes them today (REST, ending in "...") and without "...".
Measures pitch and loudness per clip; "tone shift" = how far the pitch of 'now' moves away from 'deva' on the same
sounds (the two versions aligned frame by frame). Writes WAVs, results.json and index.html next to this file.
Costs Sarvam characters (printed at the end).   run from backend/:  .venv\\Scripts\\python ..\\voice-samples\\compare-2026-11-24\\make_samples.py
"""
from __future__ import annotations

import asyncio
import base64
import html
import json
import sqlite3
import sys
import urllib.parse
import wave
from pathlib import Path

import httpx
import numpy as np
from websockets.asyncio.client import connect

sys.path.insert(0, str(Path.cwd()))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from app.config import get_settings  # noqa: E402
from app.services.audio import parse_wav_header_any  # noqa: E402
from app.services.sarvam import _headers, model_for, speakable  # noqa: E402

settings = get_settings()
OUT = Path(__file__).parent
RATE = 8000
db = sqlite3.connect("file:data/voice_agent.db?mode=ro", uri=True)
cfg = json.loads(db.execute("select data from agent_settings order by rowid desc limit 1").fetchone()[0])
PACE = float(cfg.get("tts_pace") or 1.0)

LINES = [
    ("MOQ एक हज़ार pieces से start होता है, ताकि print और packaging की quality अच्छी बनी रहे।",
     "एम ओ क्यू एक हज़ार पीसेस से स्टार्ट होता है, ताकि प्रिंट और पैकेजिंग की क्वालिटी अच्छी बनी रहे।"),
    ("dry fruits के लिए standup pouch best रहेगा, क्योंकि ये moisture और aroma दोनों को अच्छे से रोकता है।",
     "ड्राई फ्रूट्स के लिए स्टैंड अप पाउच बेस्ट रहेगा, क्योंकि ये मॉइस्चर और अरोमा दोनों को अच्छे से रोकता है।"),
    ("हमारी team इसी number पर WhatsApp पर quotation और sample kit details भेज देगी।",
     "हमारी टीम इसी नंबर पर व्हाट्सऐप पर कोटेशन और सैंपल किट डिटेल्स भेज देगी।"),
]
VOICES = ["sarika_hi_conversation", "aparna_hi_customer", "ishita_enhi_customer", "simran_enhi_customer",
          "shalini_enhi_customer"]
PREP_VOICES = ["sarika_hi_conversation", "ishita_enhi_customer"]
FILLERS = ["हम्म", "जी", "अच्छा", "अच्छा जी", "हम्म, एक second"]
chars = 0


async def stream(voice: str, text: str, prep: bool = False) -> bytes:
    """One line through the WebSocket exactly like TTSStream (plus enable_preprocessing when asked)."""
    global chars
    chars += len(text)
    q = urllib.parse.urlencode({"model": "bulbul:v4-flash" if "_" in voice else model_for(voice), "send_completion_event": "true"})
    async with connect(f"{settings.sarvam_ws_url}/text-to-speech/ws?{q}", additional_headers=_headers(),
                       open_timeout=8, max_size=8 * 1024 * 1024, compression=None) as ws:
        data = {"speaker": voice, "language_code": "hi-IN", "pace": PACE, "speech_sample_rate": RATE,
                "output_audio_codec": "linear16", "min_buffer_size": 30, "max_chunk_length": 200}
        if prep:
            data["enable_preprocessing"] = True
        await ws.send(json.dumps({"type": "config", "data": data}))
        await ws.send(json.dumps({"type": "text", "data": {"text": text}}))
        await ws.send(json.dumps({"type": "flush"}))
        pcm = bytearray()
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "audio":
                pcm += base64.b64decode(msg["data"]["audio"])
            elif msg.get("type") == "event" and (msg.get("data") or {}).get("event_type") == "final":
                break
            elif msg.get("type") == "error":
                raise RuntimeError(str(msg)[:300])
        return bytes(pcm)


async def rest(voice: str, text: str) -> bytes:
    global chars
    chars += len(text)
    body = {"text": text, "target_language_code": "hi-IN", "speaker": voice, "model": model_for(voice), "pace": PACE,
            "speech_sample_rate": RATE}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{settings.sarvam_base_url}/text-to-speech", json=body, headers=_headers())
    r.raise_for_status()
    return b"".join(parse_wav_header_any(base64.b64decode(a))[0] for a in r.json()["audios"])


async def cached(name: str, make) -> bytes:
    """A clip made in an earlier run is reused (no new Sarvam characters)."""
    f = OUT / f"{name}.wav"
    if f.exists():
        with wave.open(str(f)) as w:
            return w.readframes(w.getnframes())
    pcm = await make()
    save(name, pcm)
    return pcm


def save(name: str, pcm: bytes) -> str:
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(RATE), w.writeframes(pcm)
    return f"{name}.wav"


def f0_track(x: np.ndarray) -> np.ndarray:
    """F0 (Hz) every 10 ms with a small YIN; 0 where unvoiced."""
    hop, win, lo, hi = RATE // 100, int(RATE * .04), int(RATE / 400), int(RATE / 110)
    out = []
    for i in range(0, len(x) - win - hi, hop):
        fr = x[i:i + win + hi]
        if np.sqrt((fr[:win] ** 2).mean()) < 300:
            out.append(0.0)
            continue
        d = np.array([((fr[:win] - fr[t:t + win]) ** 2).sum() for t in range(1, hi + 1)])
        cmnd = d * np.arange(1, hi + 1) / np.maximum(np.cumsum(d), 1e-9)
        cand = np.where(cmnd[lo - 1:] < 0.15)[0]
        out.append(RATE / (cand[0] + lo) if len(cand) else 0.0)
    return np.array(out)


def logmel(x: np.ndarray) -> np.ndarray:
    hop, n = RATE // 100, 256
    frames = np.array([x[i:i + n] * np.hanning(n) for i in range(0, len(x) - n, hop)])
    spec = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    mel = lambda f: 2595 * np.log10(1 + f / 700)  # noqa: E731
    pts = 700 * (10 ** (np.linspace(mel(80), mel(3800), 26) / 2595) - 1)
    bins = np.floor((n + 1) * pts / RATE).astype(int)
    fb = np.zeros((24, n // 2 + 1))
    for m in range(1, 25):
        a, b, c = bins[m - 1], bins[m], bins[m + 1]
        fb[m - 1, a:b] = (np.arange(a, b) - a) / max(b - a, 1)
        fb[m - 1, b:c] = (c - np.arange(b, c)) / max(c - b, 1)
    f = np.log(spec @ fb.T + 1e-6)
    return (f - f.mean(axis=1, keepdims=True)) / (f.std(axis=1, keepdims=True) + 1e-6)


def dtw_path(a: np.ndarray, b: np.ndarray) -> list[tuple[int, int]]:
    cost = 1 - (a @ b.T) / (np.linalg.norm(a, axis=1)[:, None] * np.linalg.norm(b, axis=1)[None, :] + 1e-9)
    n, m = cost.shape
    acc = np.full((n + 1, m + 1), np.inf)
    acc[0, 0] = 0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            acc[i, j] = cost[i - 1, j - 1] + min(acc[i - 1, j], acc[i, j - 1], acc[i - 1, j - 1])
    i, j, path = n, m, []
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        k = int(np.argmin([acc[i - 1, j - 1], acc[i - 1, j], acc[i, j - 1]]))
        i, j = (i - 1, j - 1) if k == 0 else (i - 1, j) if k == 1 else (i, j - 1)
    return path[::-1]


def stats(pcm: bytes) -> dict:
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    f0 = f0_track(x)
    v = f0[f0 > 0]
    hop = RATE // 100
    rms = np.sqrt((x[: len(x) // hop * hop].reshape(-1, hop) ** 2).mean(axis=1))
    voiced = rms[rms > 300]
    st = 12 * np.log2(v / np.median(v)) if len(v) else np.array([0.0])
    return {"sec": round(len(x) / RATE, 2), "f0_hz": round(float(np.median(v)), 0) if len(v) else None,
            "f0_spread_st": round(float(np.percentile(st, 90) - np.percentile(st, 10)), 2),
            "level": round(float(np.median(voiced)), 0) if len(voiced) else None}


def tone_shift(now: bytes, deva: bytes) -> dict:
    """How far the pitch of 'now' moves from 'deva' on the same sounds (semitones, after aligning the two)."""
    a, b = (np.frombuffer(p, dtype=np.int16).astype(np.float64) for p in (now, deva))
    fa, fb = f0_track(a), f0_track(b)
    path = dtw_path(logmel(a), logmel(b))
    d = [12 * np.log2(fa[i] / fb[j]) for i, j in path if i < len(fa) and j < len(fb) and fa[i] > 0 and fb[j] > 0]
    d = np.abs(np.array(d) - np.median(d)) if d else np.array([0.0])  # the same overall level does not count
    return {"median_st": round(float(np.median(d)), 2), "p90_st": round(float(np.percentile(d, 90)), 2),
            "share_over_2st": round(float((d > 2).mean()), 3)}


async def main() -> None:
    results: dict = {"voices": {}, "fillers": {}}
    for voice in VOICES:
        r = results["voices"][voice] = {"lines": []}
        for i, (latin, deva) in enumerate(LINES):
            now_text = speakable(latin, {**cfg, "voice": voice})
            now = await cached(f"{voice}_{i + 1}_now", lambda: stream(voice, now_text))
            dv = await cached(f"{voice}_{i + 1}_deva", lambda: stream(voice, deva))
            dv2 = await cached(f"{voice}_{i + 1}_deva2", lambda: stream(voice, deva))
            row = {"text_now": now_text, "text_deva": deva,
                   "now": {"file": save(f"{voice}_{i + 1}_now", now), **stats(now)},
                   "deva": {"file": save(f"{voice}_{i + 1}_deva", dv), **stats(dv)},
                   "shift_now_vs_deva": tone_shift(now, dv), "baseline_deva_vs_deva2": tone_shift(dv2, dv)}
            if voice in PREP_VOICES:
                pp = await cached(f"{voice}_{i + 1}_prep", lambda: stream(voice, now_text, prep=True))
                row["prep"] = {"file": save(f"{voice}_{i + 1}_prep", pp), **stats(pp)}
                row["shift_prep_vs_deva"] = tone_shift(pp, dv)
            r["lines"].append(row)
            print(voice, i + 1, "now-vs-deva", row["shift_now_vs_deva"], "| two takes", row["baseline_deva_vs_deva2"],
                  "| prep-vs-deva", row.get("shift_prep_vs_deva"), flush=True)
    voice = cfg["voice"]
    for word in FILLERS:
        k = len(results["fillers"])
        old = await cached(f"filler_{k}_today", lambda: rest(voice, word + "..."))
        plain = await cached(f"filler_{k}_plain", lambda: rest(voice, word + "।"))
        live = await cached(f"filler_{k}_live", lambda: stream(voice, word + "।"))
        results["fillers"][word] = {
            "today (REST, ...)": {"file": save(f"filler_{len(results['fillers'])}_today", old), **stats(old)},
            "no ellipsis (REST)": {"file": save(f"filler_{len(results['fillers'])}_plain", plain), **stats(plain)},
            "live voice path": {"file": save(f"filler_{len(results['fillers'])}_live", live), **stats(live)}}
        print(word, {k: (v["sec"], v["f0_hz"], v["level"]) for k, v in results["fillers"][word].items()}, flush=True)
    results["sarvam_characters"] = chars
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    rows = []
    for voice, r in results["voices"].items():
        for i, row in enumerate(r["lines"], 1):
            cells = "".join(f"<td><audio controls preload=none src='{html.escape(row[k]['file'])}'></audio><br>"
                            f"<small>{row[k]['f0_hz']} Hz</small></td>" for k in ("now", "deva", "prep") if k in row)
            rows.append(f"<tr><td>{html.escape(voice)}<br><small>line {i}</small></td>{cells}</tr>")
    frows = "".join(f"<tr><td>{html.escape(w)}</td>" + "".join(
        f"<td><audio controls preload=none src='{html.escape(v['file'])}'></audio><br><small>{v['sec']} s, {v['f0_hz']} Hz</small></td>"
        for v in d.values()) + "</tr>" for w, d in results["fillers"].items())
    (OUT / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Voice test 2026-11-24</title><body style='font-family:sans-serif'>"
        "<h2>English words: today vs written in Hindi script</h2><p>Columns: today | Hindi script | today + Sarvam "
        "preprocessing (two voices only)</p><table border=1 cellpadding=6>" + "".join(rows) + "</table>"
        "<h2>Fillers (current voice)</h2><p>Columns: today (with ...) | without ... | through the live voice path</p>"
        "<table border=1 cellpadding=6>" + frows + "</table>", encoding="utf-8")
    print("Sarvam characters:", chars)


if __name__ == "__main__":
    asyncio.run(main())
