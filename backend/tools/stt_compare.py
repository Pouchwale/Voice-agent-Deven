"""Compare speech-recognition settings on real recorded calls, before changing what live calls use.

Replays the CUSTOMER side of call recordings (left channel, 8 kHz) through Sarvam's realtime STT with several
settings and prints, per setting, what it heard, next to what the live call heard (transcript_turns).

    cd backend
    .\\.venv\\Scripts\\python tools\\stt_compare.py 49 45 43            # call ids (default: the last 5 phone calls)
    .\\.venv\\Scripts\\python tools\\stt_compare.py 49 --only v4-terms   # one setting

Costs Sarvam STT minutes (each setting = the customers' talk time of each call). Reads the database read-only;
changes nothing. Settings to try live in CONFIGS below; the winner goes into backend/.env
(SARVAM_STT_MODEL, SARVAM_STT_KEYTERMS, SARVAM_STT_PROMPT, SARVAM_STT_SILENCE_MS) after a listen-check.
"""
from __future__ import annotations

import argparse
import asyncio
import audioop
import base64
import json
import os
import sqlite3
import sys
import urllib.parse
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from websockets.asyncio.client import connect  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.sarvam import _headers, stt_hints  # noqa: E402

settings = get_settings()
KEYTERMS = ("PouchWale, pouch, standup pouch, stand up pouch, center seal pouch, three side seal pouch, 3 side seal, "
            "quad seal, spout pouch, zipper, sachet, MOQ, pieces, quantity, per month, per order, sample kit, "
            "WhatsApp, makhana, namkeen, spices, masala, dry fruits, milk powder, khakhra, mukhwas, variants, SKU, "
            "design, printing, packaging, quote")
PROMPT = "Packaging sales call: pouches (standup, center seal, three side seal, quad seal, spout), quantities, MOQ."
CONFIGS: dict[str, dict] = {
    "live-now": {},  # what live calls use today (backend/.env)
    "v4": {"model": "saaras:v4"},
    "v4-terms": {"model": "saaras:v4", "keyterms": KEYTERMS},
    "v4-terms-600ms": {"model": "saaras:v4", "keyterms": KEYTERMS, "silence_duration_ms": "600"},
    "v3-prompt": {"prompt": PROMPT},
}


def url_for(over: dict) -> str:
    model = over.get("model", settings.sarvam_stt_model)
    q = {"model": model, "language_code": over.get("language_code", "hi-IN"),
         "mode": over.get("mode", settings.sarvam_stt_mode), "stream_type": settings.sarvam_stt_stream_type,
         "encoding": "mulaw", "sample_rate": "8000", "endpointing": "vad",
         "silence_duration_ms": over.get("silence_duration_ms", str(settings.sarvam_stt_silence_ms)),
         "min_speech_duration_ms": "200"}
    q.update(stt_hints(model, over.get("keyterms", settings.sarvam_stt_keyterms),
                       over.get("prompt", settings.sarvam_stt_prompt)))
    return f"{settings.sarvam_ws_url}/speech-to-text-realtime/ws?{urllib.parse.urlencode(q)}"


def customer_mulaw(path: str) -> bytes:
    with wave.open(path) as w:
        frames, width, rate = w.readframes(w.getnframes()), w.getsampwidth(), w.getframerate()
        ch = w.getnchannels()
    pcm = audioop.tomono(frames, width, 1, 0) if ch == 2 else frames  # left = customer
    if rate != 8000:
        pcm, _ = audioop.ratecv(pcm, width, 1, rate, 8000, None)
    return audioop.lin2ulaw(pcm, width)


async def transcribe(audio: bytes, over: dict, speed: float = 2.0) -> list[str]:
    finals: list[str] = []
    async with connect(url_for(over), additional_headers=_headers(), open_timeout=10, max_size=2 ** 21) as ws:
        async def reader():
            async for msg in ws:
                if isinstance(msg, bytes):
                    continue
                data = json.loads(msg)
                ev = data.get("event") or data.get("type")
                if ev == "transcript.final" and (data.get("text") or "").strip():
                    finals.append(data["text"].strip())
                elif ev == "error":
                    raise RuntimeError(data.get("message") or data)
                elif ev == "session.end":
                    return

        task = asyncio.create_task(reader())
        chunk = 800  # 100 ms of 8 kHz mulaw
        for i in range(0, len(audio) + 8000 * 2, chunk):  # + 2 s of silence so the last sentence ends
            part = audio[i:i + chunk] or b"\xff" * chunk  # 0xff = mulaw silence
            await ws.send(json.dumps({"event": "audio_input", "audio": base64.b64encode(part).decode()}))
            await asyncio.sleep(0.1 / speed)
            if task.done():
                break
        if not task.done():
            await asyncio.sleep(2.5)
        if task.done() and not task.cancelled() and task.exception():
            raise task.exception()
        task.cancel()
        if not finals:  # an empty result must never read as "the customer said nothing"
            raise RuntimeError(f"no transcript (socket close code {ws.close_code}, reason {ws.close_reason!r})")
    return finals


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("calls", nargs="*", type=int)
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    db = sqlite3.connect(f"file:{settings.database_url.replace('sqlite:///', '')}?mode=ro", uri=True)
    ids = args.calls or [r[0] for r in db.execute(
        "select id from calls where channel='phone' and recording_path is not null order by id desc limit 5")]
    configs = {k: v for k, v in CONFIGS.items() if not args.only or k in args.only}
    sys.stdout.reconfigure(encoding="utf-8")
    for cid in ids:
        row = db.execute("select recording_path from calls where id=?", (cid,)).fetchone()
        rec = str(settings.recordings_dir.parent / row[0]) if row and row[0] else None  # "recordings/<uuid>.wav"
        if not rec or not os.path.exists(rec):
            print(f"\n=== call {cid}: no recording")
            continue
        audio = customer_mulaw(rec)
        print(f"\n=== call {cid}  ({len(audio) / 8000:.0f} s)")
        heard = [t for (t,) in db.execute("select text from transcript_turns where call_id=? and role='user' "
                                          "order by id", (cid,))]
        print(f"  [heard live]      {' | '.join(heard)}")
        for name, over in configs.items():
            try:
                out = asyncio.run(transcribe(audio, over))
                print(f"  [{name:16}] {' | '.join(out)}")
            except Exception as e:  # noqa: BLE001
                print(f"  [{name:16}] FAILED: {str(e)[:160]}")


if __name__ == "__main__":
    main()
