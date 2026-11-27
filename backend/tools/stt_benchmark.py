"""Speech-recognition benchmark for our phone calls: how well a Sarvam realtime STT setup hears customers, on a clean
line and with traffic or other people's voices behind them. Every change to speech recognition is measured with this,
the same way, and the numbers are written to a folder (results.json + report.md).

Method (written 2026-11-25):
1. Test set: SENTENCES, things customers say on our calls, each with its key terms (the words that must come out right:
   MOQ, pouch types, numbers, names; several accepted spellings each). Spoken by four Sarvam voices (two male, two
   female), synthesised once and kept as WAV under <out>/clean/ (a re-run reuses them: no new voice charges).
2. Phone line: 8 kHz, 16-bit, then mu-law encoded (as our phone provider sends audio).
3. Noise, mixed BEFORE the phone codec (it is in the customer's surroundings), at a fixed signal-to-noise ratio (SNR, dB,
   RMS of the voiced speech vs RMS of the noise):
   - traffic: synthetic road noise (brown-noise rumble, engine hum with drift, tyre/wind hiss, one horn blast), seeded;
   - voices: two other people talking (two other test sentences in other voices, offset in time), the "mixed voices" case.
4. Each clip is streamed to the realtime STT exactly as on a call (same URL parameters as RealtimeSTT, 60 ms mu-law
   frames, at twice real time), followed by 1.2 s of line silence; every final transcript is joined.
5. Scores, after the same normalisation of truth and transcript (lower case, no punctuation, English words in the
   Devanagari spelling the partials use, Hindi number words as digits, digit groups joined):
   - WER: word error rate = word-level edit distance / number of words in the truth (0 % is perfect);
   - key terms: share of key terms found (each term matches any of its accepted spellings).
   Numbers are reported per condition (clean, traffic 10/5 dB, voices 10/5 dB) and overall, per setup.

Setups compared (SETUPS): the current one (saaras:v3-realtime), saaras:v4, and saaras:v4 with our key terms (v4 only).

usage (from backend/):  python tools/stt_benchmark.py <out folder> [setup ...]
It spends Sarvam credit: voice synthesis once (~700 characters), then about 8-9 minutes of STT audio per setup.
"""
from __future__ import annotations

import asyncio
import base64
import json
import math
import random
import re
import sys
import time
import urllib.parse
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings  # noqa: E402
from app.services.audio import pcm16_to_ulaw, ulaw_to_pcm16  # noqa: E402
from app.services.hindi_words import WORDS  # noqa: E402
from app.services.sarvam import _headers, stt_hints, tts_rest  # noqa: E402
from websockets.asyncio.client import connect  # noqa: E402

settings = get_settings()
RATE = 8000

# (truth, key terms (each a list of accepted spellings, matched without spaces), voice)
SENTENCES: list[tuple[str, list[list[str]], str]] = [
    ("हाँ जी बोलिए", [["हां"], ["जी"]], "rahul"),
    ("हाँ जी", [["हां"], ["जी"]], "priya"),
    ("आपका MOQ कितना है?", [["moq", "एमओक्यू"], ["कितना"]], "amit"),
    ("मुझे makhana के लिए standup pouch चाहिए", [["makhana", "मखाना", "मखाने"], ["standup", "स्टैंडअप", "स्टैंडअप"],
                                               ["पाउच"]], "aarti_hi_customer"),
    ("हमें हर महीने एक लाख pieces चाहिए", [["100000"], ["पीसेस", "पीस"]], "rahul"),
    ("हमारी company का नाम The Holy Nutty है", [["होली", "holy"], ["नट्टी", "नटी", "nutty"]], "priya"),
    ("क्या आप zipper pouch भी बनाते हैं?", [["ज़िपर", "जिपर", "zipper"], ["पाउच"]], "amit"),
    ("हमें दस हज़ार pouch चाहिए दो हफ्ते में", [["10000"], ["2हफ्ते", "2हफ़्ते"]], "aarti_hi_customer"),
    ("sample kit मंगवाना है, WhatsApp पर भेज दीजिए", [["सैंपल", "sample"], ["किट", "kit"],
                                                   ["व्हाट्सऐप", "व्हाट्सएप", "whatsapp"]], "rahul"),
    ("matte finish अच्छा रहेगा या gloss?", [["मैट", "matte"], ["ग्लॉस", "ग्लोस", "gloss"]], "priya"),
    ("मेरा namkeen का business है, center seal pouch चाहिए", [["नमकीन", "namkeen"], ["सेंटरसील", "centerseal"]], "amit"),
    ("अभी हम spices के लिए किसी और company से packaging लेते हैं", [["स्पाइसेस", "spices", "मसाले"],
                                                               ["पैकेजिंग", "packaging"]], "aarti_hi_customer"),
    ("नहीं, अभी design ready नहीं है", [["डिज़ाइन", "डिजाइन", "design"], ["रेडी", "ready"]], "rahul"),
    ("शाम को पाँच बजे call कीजिए", [["5बजे"], ["कॉल", "call"]], "priya"),
    ("price per piece कितना पड़ेगा?", [["प्राइस", "price"], ["पीस", "piece"]], "amit"),
    ("हम dry fruits और cashews pack करते हैं", [["ड्राईफ्रूट्स", "ड्राईफ़्रूट्स", "dryfruits"], ["काजू", "कैशूज़", "कैशू",
                                                                                         "cashews"]], "aarti_hi_customer"),
    ("आप लोग कहाँ से हो, Ahmedabad के पास?", [["अहमदाबाद", "ahmedabad"]], "rahul"),
    ("ठीक है, मैं सोचकर बताता हूँ", [["ठीक"], ["सोचकर", "सोच"]], "priya"),
    ("3 side seal pouch में कितना खर्चा आएगा?", [["3साइड", "थ्रीसाइड", "3side"], ["सील", "seal"]], "amit"),
    ("मेरी quantity लगभग पच्चीस हज़ार है", [["25000"], ["क्वांटिटी", "quantity"]], "aarti_hi_customer"),
]
CONDITIONS = [("clean", None, None), ("traffic 10 dB", "traffic", 10), ("traffic 5 dB", "traffic", 5),
              ("voices 10 dB", "voices", 10), ("voices 5 dB", "voices", 5)]
# Words customers use with us (not tuned to the test sentences): what production would send as key terms.
KEYTERMS = ("MOQ, pouch, standup pouch, zipper pouch, center seal pouch, 3 side seal pouch, quad seal bag, spout pouch, "
            "vacuum pouch, Pouch Wale, namkeen, makhana, dry fruits, spices, masala, matte, gloss, sample kit, WhatsApp, "
            "SKU, packaging, laminated, Hybrid LED, per piece, quotation")
SETUPS = {
    "current": {"model": "saaras:v3-realtime", "keyterms": ""},
    "v4": {"model": "saaras:v4", "keyterms": ""},
    "v4+terms": {"model": "saaras:v4", "keyterms": KEYTERMS},
}

# ---------------------------------------------------------------- normalisation and scores
_NUM = {"एक": "1", "दो": "2", "तीन": "3", "चार": "4", "पांच": "5", "पाँच": "5", "छह": "6", "सात": "7", "आठ": "8",
        "नौ": "9", "दस": "10", "पंद्रह": "15", "बीस": "20", "पच्चीस": "25", "पचास": "50", "सौ": "100"}
_PUNCT = re.compile(r"[\s\.,!?।'\"…\-:;()₹]+")


_SCALE = {"हजार": 1000, "हज़ार": 1000, "लाख": 100000}


def norm(text: str) -> list[str]:
    text = re.sub(r"(?<=\d)[,\s](?=\d{2,3}\b)", "", text)  # "1,00,000" / "1 00 000" -> "100000"
    out: list[str] = []
    for w in _PUNCT.split(text.lower()):
        if not w:
            continue
        for part in WORDS.get(w, w).split():  # "moq" -> "एम ओ क्यू": three words, as a Devanagari transcript writes it
            part = _NUM.get(part, part).replace("़", "").replace("ँ", "ं")
            if part in _SCALE and out and out[-1].isdigit():  # "दस हज़ार" / "10 हज़ार" -> "10000"
                out[-1] = str(int(out[-1]) * _SCALE[part])
                continue
            out.append(part)
    return out


def edit_distance(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def key_hits(keys: list[list[str]], hyp: str) -> int:
    flat = "".join(norm(hyp))
    return sum(any("".join(norm(k)) in flat for k in alts) for alts in keys)


# ---------------------------------------------------------------- audio
def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64)


def write_wav(path: Path, x: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.clip(x, -32768, 32767).astype(np.int16).tobytes())


def voiced_rms(x: np.ndarray) -> float:
    n = len(x) // 160
    frames = np.sqrt((x[: n * 160].reshape(n, 160) ** 2).mean(axis=1))
    voiced = frames[frames > max(200.0, frames.max() * 0.1)]
    return float(np.sqrt((voiced ** 2).mean())) if len(voiced) else float(np.sqrt((x ** 2).mean()))


def lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / RATE)
    spec[f > cutoff] = 0
    return np.fft.irfft(spec, len(x))


def bandpass(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / RATE)
    spec[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(spec, len(x))


def traffic(n: int, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(n) / RATE
    rumble = lowpass(np.cumsum(rng.standard_normal(n)), 300)
    rumble -= rumble.mean()
    rumble /= np.abs(rumble).max() + 1e-9
    f0 = 55 + 8 * np.sin(2 * np.pi * 0.3 * t + rng.uniform(0, 6))  # engine hum, drifting
    phase = 2 * np.pi * np.cumsum(f0) / RATE
    engine = sum(np.sin(k * phase) / k for k in (1, 2, 3, 4))
    hiss = bandpass(rng.standard_normal(n), 500, 3000)
    hiss /= np.abs(hiss).max() + 1e-9
    horn = np.zeros(n)
    start = int(rng.uniform(0.1, 0.6) * n)
    length = min(int(0.45 * RATE), n - start)
    tt = np.arange(length) / RATE
    horn[start:start + length] = (np.sin(2 * np.pi * 420 * tt) + 0.6 * np.sin(2 * np.pi * 840 * tt)) * np.hanning(length)
    mix = 1.0 * rumble + 0.35 * engine / 2 + 0.25 * hiss + 0.5 * horn
    return mix / (np.sqrt((mix ** 2).mean()) + 1e-9)


def voices(n: int, others: list[np.ndarray], rng: np.random.Generator) -> np.ndarray:
    mix = np.zeros(n)
    for o in others:
        off = int(rng.uniform(-0.3, 0.3) * len(o))
        piece = np.roll(np.pad(o, (0, max(0, n - len(o))))[:n], off)
        mix += piece * rng.uniform(0.7, 1.0)
    return mix / (np.sqrt((mix ** 2).mean()) + 1e-9)


def at_snr(speech: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    gain = voiced_rms(speech) / (10 ** (snr_db / 20)) / (np.sqrt((noise ** 2).mean()) + 1e-9)
    return speech + noise * gain


def phone(x: np.ndarray) -> bytes:
    """8 kHz mu-law, as the phone provider sends it."""
    pcm = np.clip(x, -32768, 32767).astype(np.int16).tobytes()
    return pcm16_to_ulaw(pcm)


# ---------------------------------------------------------------- speech recognition, as on a call
async def transcribe(mulaw: bytes, setup: dict) -> str:
    q = {"model": setup["model"], "language_code": "hi-IN", "mode": settings.sarvam_stt_mode,
         "stream_type": settings.sarvam_stt_stream_type, "encoding": "mulaw", "sample_rate": str(RATE),
         "endpointing": "vad", "silence_duration_ms": str(settings.sarvam_stt_silence_ms), "min_speech_duration_ms": "200"}
    q.update(stt_hints(setup["model"], setup["keyterms"], ""))
    url = f"{settings.sarvam_ws_url}/speech-to-text-realtime/ws?{urllib.parse.urlencode(q)}"
    finals: list[str] = []
    async with connect(url, additional_headers=_headers(), open_timeout=10, ping_interval=None,
                       max_size=2 * 1024 * 1024) as ws:
        async def reader():
            async for msg in ws:
                if isinstance(msg, bytes):
                    continue
                data = json.loads(msg)
                ev = data.get("event") or data.get("type")
                if ev == "transcript.final" and (data.get("text") or "").strip():
                    finals.append(data["text"].strip())
                elif ev == "error":
                    raise RuntimeError(f"STT error: {data}")
                elif ev == "session.end":
                    return
        task = asyncio.create_task(reader())
        audio = mulaw + b"\xff" * int(RATE * 1.2)  # then line silence, so the end of speech is seen
        for i in range(0, len(audio), 480):  # 60 ms frames, at twice real time
            await ws.send(json.dumps({"event": "audio_input", "audio": base64.b64encode(audio[i:i + 480]).decode()}))
            await asyncio.sleep(0.03)
        await asyncio.sleep(2.5)  # the last final transcript
        await ws.send(json.dumps({"event": "end"}))
        try:
            await asyncio.wait_for(task, 5)
        except asyncio.TimeoutError:
            task.cancel()
    return " ".join(finals)


# ---------------------------------------------------------------- the run
async def synth_clean(out: Path) -> list[np.ndarray]:
    clips = []
    for i, (text, _, voice) in enumerate(SENTENCES):
        path = out / "clean" / f"{i:02d}.wav"
        if not path.exists():
            cfg = {"language_code": "hi-IN", "voice": voice, "tts_pace": 1.0}
            write_wav(path, np.frombuffer(await tts_rest(text, cfg, RATE), dtype=np.int16).astype(np.float64))
        clips.append(read_wav(path))
    return clips


def build_clips(clean: list[np.ndarray]) -> list[tuple[int, str, bytes]]:
    rng = np.random.default_rng(20261125)
    clips = []
    for i, speech in enumerate(clean):
        pad = np.concatenate([np.zeros(int(0.3 * RATE)), speech, np.zeros(int(0.3 * RATE))])
        others = [clean[j] for j in ((i + 5) % len(clean), (i + 11) % len(clean))]
        for name, kind, snr in CONDITIONS:
            if kind is None:
                x = pad
            elif kind == "traffic":
                x = at_snr(pad, traffic(len(pad), rng), snr)
            else:
                x = at_snr(pad, voices(len(pad), others, rng), snr)
            clips.append((i, name, phone(x)))
    return clips


async def run(out: Path, names: list[str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    clean = await synth_clean(out)
    clips = build_clips(clean)
    results_path = out / "results.json"
    results = json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else {}
    sem = asyncio.Semaphore(3)
    for name in names:
        setup = SETUPS[name]
        t0 = time.monotonic()

        async def one(i, cond, audio):
            async with sem:
                for attempt in range(3):
                    try:
                        return i, cond, await transcribe(audio, setup)
                    except Exception as e:  # noqa: BLE001
                        if attempt == 2:
                            return i, cond, f"<error: {e}>"
                        await asyncio.sleep(2)
        rows = await asyncio.gather(*(one(i, c, a) for i, c, a in clips))
        results[name] = {"setup": setup, "rows": [{"i": i, "condition": c, "heard": h} for i, c, h in rows],
                         "audio_seconds": round(sum(len(a) for _, _, a in clips) / RATE + 1.2 * len(clips), 1)}
        results_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{name}: {len(rows)} clips in {time.monotonic() - t0:.0f} s", flush=True)
    write_report(out, results)


def scores(rows: list[dict]) -> dict:
    by: dict[str, list] = {}
    for r in rows:
        truth, keys, _ = SENTENCES[r["i"]]
        t = norm(truth)
        by.setdefault(r["condition"], []).append((edit_distance(t, norm(r["heard"])), len(t), key_hits(keys, r["heard"]),
                                                  len(keys)))
    out = {}
    for cond, xs in [*by.items(), ("all", [x for v in by.values() for x in v])]:
        out[cond] = {"wer": round(100 * sum(x[0] for x in xs) / sum(x[1] for x in xs), 1),
                     "key_terms": round(100 * sum(x[2] for x in xs) / sum(x[3] for x in xs), 1), "clips": len(xs)}
    return out


def write_report(out: Path, results: dict) -> None:
    lines = ["# Speech-recognition benchmark", "", "Method: see the docstring of `backend/tools/stt_benchmark.py`.",
             "WER = word error rate (lower is better); key terms = share of key words heard right (higher is better).",
             "", "| setup | condition | WER % | key terms % | clips |", "|---|---|---|---|---|"]
    table = {}
    for name, res in results.items():
        table[name] = scores(res["rows"])
        for cond in [c for c, _, _ in CONDITIONS] + ["all"]:
            s = table[name].get(cond)
            if s:
                lines.append(f"| {name} | {cond} | {s['wer']} | {s['key_terms']} | {s['clips']} |")
    lines += ["", "## What each setup heard", ""]
    for i, (truth, _, voice) in enumerate(SENTENCES):
        lines.append(f"**{i:02d} ({voice})** truth: {truth}")
        for name, res in results.items():
            for r in res["rows"]:
                if r["i"] == i and r["condition"] in ("clean", "traffic 5 dB", "voices 5 dB"):
                    lines.append(f"- {name} / {r['condition']}: {r['heard'] or '(nothing)'}")
        lines.append("")
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    (out / "scores.json").write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, s in table.items():
        print(name, json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":  # spends Sarvam credit: never on import
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    folder = Path(sys.argv[1])
    asyncio.run(run(folder, sys.argv[2:] or list(SETUPS)))
