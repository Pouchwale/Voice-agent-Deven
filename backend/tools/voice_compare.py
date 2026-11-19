"""Listen to candidate voices side by side, at phone quality, before changing the live voice.

Generates the same lines with several voice setups: the saved greeting and closing (filled in as on a real call)
and one typical reply, the reply three times per setup, because a generative voice performs every take
differently. Each clip is saved twice: "studio" (24 kHz, what Settings -> Play gives) and "phone" (8 kHz through
the u-law codec of a phone line, what the customer hears). Speed, liveliness (pitch movement) and pauses are
measured for each clip, and an index.html is written to listen and compare in the browser.

    cd backend
    .\\.venv\\Scripts\\python tools\\voice_compare.py                     # default setups -> ../voice-samples/compare-<date>/
    .\\.venv\\Scripts\\python tools\\voice_compare.py --only sarika_now aparna --takes 2

Costs Sarvam characters: about 520 per setup with the default lines and 3 takes (the total is printed at the end).
Reads the saved settings read-only and changes nothing in the app. Setups that send "temperature" to a v4 voice
test whether v4 honours it (Sarvam's API reference says it does, its v4 guide says it is ignored; live calls do
not send it).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import datetime as dt
import html
import json
import sqlite3
import sys
import wave
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.config import get_settings  # noqa: E402
from app.services import sarvam  # noqa: E402
from app.services.audio import (match_loudness, parse_wav_header_any, pcm16_to_ulaw, resample_once,  # noqa: E402
                                ulaw_to_pcm16, wav_bytes)
from app.services.prompts import fill_template  # noqa: E402

try:
    import numpy as np
except ImportError:  # measurements are optional: the audio files are the point
    np = None

STUDIO_RATE = 24000
PHONE_RATE = 8000
SAMPLE_LEAD = {"name": "Rahul", "company": "", "city": ""}
REPLY = "जी बिल्कुल, हम custom printed pouch बनाते हैं। आप किस product के लिए packaging ढूँढ रहे हैं?"
SETUPS = [
    {"key": "sarika_now", "voice": "sarika_hi_conversation", "pace": 1.0,
     "label": "Sarika (v4) · pace 1.00", "note": "today's setting"},
    {"key": "sarika_092", "voice": "sarika_hi_conversation", "pace": 0.92,
     "label": "Sarika (v4) · pace 0.92", "note": "slower: the speed of the take you liked"},
    {"key": "sarika_092_steady", "voice": "sarika_hi_conversation", "pace": 0.92, "temperature": 0.3, "force": True,
     "label": "Sarika (v4) · pace 0.92 · expressiveness 0.3",
     "note": "test: is v4 steadier when told to be? (live calls don't send this to v4)"},
    {"key": "aparna", "voice": "aparna_hi_customer", "pace": 1.0,
     "label": "Aparna (v4) · customer care · pace 1.00", "note": "Sarvam's first pick for Hindi sales calls"},
    {"key": "aarti", "voice": "aarti_hi_customer", "pace": 1.0,
     "label": "Aarti (v4) · customer care · pace 1.00", "note": "Sarvam's second pick for Hindi customer care"},
    {"key": "suhani_v3", "voice": "suhani", "pace": 1.0, "temperature": 0.4,
     "label": "Suhani (v3) · expressiveness 0.4", "note": "older model, the only one with a documented steadiness control"},
]
chars_spent = 0


def saved_settings() -> dict:
    con = sqlite3.connect(f"file:{(BACKEND / 'data' / 'voice_agent.db').as_posix()}?mode=ro", uri=True)
    try:
        return json.loads(con.execute("select data from agent_settings where id=1").fetchone()[0])
    finally:
        con.close()


async def synth(text: str, cfg: dict, temperature: float | None, force: bool) -> bytes:
    """One fresh take at studio quality, built like the live voice (sarvam.tts_rest) but never from its cache,
    and with `temperature` sent to a v4 voice too when `force` is set."""
    global chars_spent
    spoken = sarvam.speakable(text.strip(), cfg)
    voice = sarvam.voice_for(cfg)
    model = sarvam.model_for(voice)
    body = {"text": spoken, "target_language_code": "hi-IN", "speaker": voice, "model": model,
            "pace": float(cfg["tts_pace"]), "speech_sample_rate": STUDIO_RATE}
    if model != sarvam.V4_MODEL or force:
        body["temperature"] = float(temperature if temperature is not None else cfg.get("tts_temperature") or 0.6)
    chars_spent += len(spoken)
    resp = await sarvam.http().post("/text-to-speech", json=body, headers=sarvam._headers(), timeout=60)
    if resp.status_code >= 300:
        raise RuntimeError(f"Sarvam TTS {resp.status_code}: {resp.text[:200]}")
    pcm, rate = bytearray(), STUDIO_RATE
    for b64 in resp.json().get("audios") or []:
        raw = base64.b64decode(b64)
        parsed = parse_wav_header_any(raw)
        chunk, rate = parsed if parsed else (raw, rate)
        pcm.extend(chunk)
    return match_loudness(resample_once(bytes(pcm), rate, STUDIO_RATE), STUDIO_RATE)


def phone(pcm_studio: bytes) -> bytes:
    """What the customer hears: 8 kHz through the phone line's u-law codec."""
    return ulaw_to_pcm16(pcm16_to_ulaw(resample_once(pcm_studio, STUDIO_RATE, PHONE_RATE)))


def measure(pcm: bytes, rate: int, text: str) -> dict:
    if np is None or not pcm:
        return {}
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    frame, hop = int(rate * 0.04), int(rate * 0.01)
    rms = np.array([np.sqrt(np.mean(x[i:i + frame] ** 2)) for i in range(0, len(x) - frame, hop)])
    voiced = rms > rms.max() * 0.08
    idx = np.where(voiced)[0]
    if len(idx) < 2:
        return {}
    speech = (idx[-1] - idx[0]) * hop / rate
    pauses = run = 0
    for v in voiced[idx[0]:idx[-1]]:
        run = run + 1 if not v else 0
        pauses += run == 25  # a silence of 250 ms inside the speech
    lo, hi = int(rate / 400), int(rate / 120)  # pitch between 120 and 400 Hz
    f0 = []
    for k in idx:
        seg = x[k * hop:k * hop + frame]
        seg = seg - seg.mean()
        ac = np.correlate(seg, seg, "full")[frame - 1:]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] > 0.5:
            f0.append(rate / lag)
    f0 = np.array(f0) if f0 else np.array([0.0])
    return {"seconds": round(len(x) / rate, 1), "speed": round(float(len(text.replace(" ", "")) / speech), 1),
            "liveliness": int(round(float(np.percentile(f0, 90) - np.percentile(f0, 10)))), "pauses": int(pauses),
            "pitch": int(round(float(np.median(f0))))}


def save(path: Path, pcm: bytes, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_bytes(pcm, rate))


def read_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path)) as w:
        return w.readframes(w.getnframes()), w.getframerate()


async def run(setups: list[dict], takes: int, out: Path, reference: Path | None) -> dict:
    cfg_saved = saved_settings()
    lines = {"greeting": fill_template(cfg_saved.get("opening_line") or "", cfg_saved, SAMPLE_LEAD).strip(),
             "reply": REPLY,
             "closing": fill_template(cfg_saved.get("closing_line") or "", cfg_saved, SAMPLE_LEAD).strip()}
    result = {"made": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "lines": lines, "takes": takes, "setups": []}
    if reference is not None and reference.exists():
        pcm, rate = read_wav(reference)
        studio = resample_once(pcm, rate, STUDIO_RATE)
        save(out / "reference" / "reply_studio.wav", studio, STUDIO_RATE)
        save(out / "reference" / "reply_phone.wav", phone(studio), PHONE_RATE)
        result["reference"] = measure(studio, STUDIO_RATE, REPLY)
    for s in setups:
        cfg = {**cfg_saved, "voice": s["voice"], "tts_pace": s["pace"], "language_code": "hi-IN"}
        entry = {**s, "model": sarvam.model_for(s["voice"]), "clips": []}
        print(f"{s['label']} ...", flush=True)
        try:
            plan = [("greeting", 1), ("reply", takes), ("closing", 1)]
            for name, count in plan:
                for n in range(1, count + 1):
                    studio = await synth(lines[name], cfg, s.get("temperature"), s.get("force", False))
                    stem = f"{name}_take{n}" if count > 1 else name
                    save(out / s["key"] / f"{stem}_studio.wav", studio, STUDIO_RATE)
                    save(out / s["key"] / f"{stem}_phone.wav", phone(studio), PHONE_RATE)
                    entry["clips"].append({"line": name, "take": n, "stem": stem,
                                           **measure(studio, STUDIO_RATE, lines[name])})
        except Exception as e:  # noqa: BLE001  (one setup failing must not lose the others)
            entry["error"] = str(e)[:300]
            print(f"   failed: {entry['error']}", flush=True)
        result["setups"].append(entry)
    result["sarvam_characters"] = chars_spent
    (out / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "index.html").write_text(page(result), encoding="utf-8")
    return result


def page(r: dict) -> str:
    """The listening page: one block per setup, phone quality first."""
    e = html.escape

    def numbers(c: dict) -> str:
        if not c.get("speed"):
            return ""
        return (f'<span class="n">{c["seconds"]} s · {c["speed"]} letters/s · liveliness {c["liveliness"]} Hz'
                f'{" · " + str(c["pauses"]) + " pause" + ("s" if c["pauses"] != 1 else "") if c["pauses"] else ""}</span>')

    def player(src: str, label: str) -> str:
        return f'<div class="p"><span class="k">{label}</span><audio controls preload="none" src="{e(src)}"></audio></div>'

    def spread(clips: list[dict]) -> str:
        replies = [c for c in clips if c["line"] == "reply" and c.get("speed")]
        if len(replies) < 2:
            return ""
        sp = [c["speed"] for c in replies]
        lv = [c["liveliness"] for c in replies]
        return (f'<p class="sum">Reply, {len(replies)} takes: speed {min(sp)}–{max(sp)} letters/s '
                f'(takes differ by {round((max(sp) - min(sp)) / min(sp) * 100)}%), liveliness {min(lv)}–{max(lv)} Hz.</p>')

    blocks = []
    ref = r.get("reference")
    if ref is not None:
        blocks.append(
            '<section class="ref"><h2>The take you liked</h2><p class="note">Sarika (v4), pace 1.00 — the same reply '
            'line, from the earlier test (same_line_take1.wav).</p><div class="grid"><div class="cell">'
            f'<h3>Reply</h3>{player("reference/reply_phone.wav", "Phone")}{player("reference/reply_studio.wav", "Studio")}'
            f'{numbers(ref)}</div></div></section>')
    for s in r["setups"]:
        cells = []
        for c in s["clips"]:
            title = {"greeting": "Greeting", "closing": "Closing"}.get(c["line"], f"Reply · take {c['take']}")
            cells.append(f'<div class="cell"><h3>{e(title)}</h3>'
                         f'{player(s["key"] + "/" + c["stem"] + "_phone.wav", "Phone")}'
                         f'{player(s["key"] + "/" + c["stem"] + "_studio.wav", "Studio")}{numbers(c)}</div>')
        err = f'<p class="err">Not made: {e(s["error"])}</p>' if s.get("error") else ""
        blocks.append(f'<section><h2>{e(s["label"])}</h2><p class="note">{e(s["note"])} · model {e(s["model"])}</p>'
                      f'{err}{spread(s["clips"])}<div class="grid">{"".join(cells)}</div></section>')
    lines = "".join(f'<li><b>{e(k.title())}:</b> {e(v)}</li>' for k, v in r["lines"].items())
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Voice comparison</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --fg:#16181d; --muted:#5b6170; --line:#dfe2e8; --accent:#1f6feb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111317; --card:#1a1d23; --fg:#e8eaee; --muted:#a3a9b6; --line:#2c313a; --accent:#58a6ff; }} }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, "Segoe UI", sans-serif; }}
main {{ max-width:1180px; margin:0 auto; padding:24px 16px 48px; }}
h1 {{ font-size:24px; margin:0 0 6px; }} h2 {{ font-size:17px; margin:0 0 2px; }} h3 {{ font-size:13px; margin:0 0 6px; color:var(--muted); font-weight:600; }}
.intro, .note {{ color:var(--muted); }} .note {{ margin:0 0 10px; font-size:13px; }}
section {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin:16px 0; }}
section.ref {{ border-color:var(--accent); }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(210px, 1fr)); gap:12px; }}
.cell {{ border:1px solid var(--line); border-radius:10px; padding:10px; }}
.p {{ display:flex; align-items:center; gap:6px; margin:4px 0; }} .k {{ font-size:12px; width:42px; color:var(--muted); }}
audio {{ width:100%; height:32px; }} .n {{ display:block; font-size:12px; color:var(--muted); margin-top:4px; }}
.sum {{ margin:0 0 10px; font-size:13px; }} .err {{ color:#c62828; }} ul {{ padding-left:18px; }}
</style></head>
<body><main>
<h1>Voice comparison</h1>
<p class="intro">Made {e(r["made"])}. Every block says the same lines. <b>Phone</b> is what a customer hears on the call
(8 kHz phone line); <b>Studio</b> is what Settings → Play gives. The reply was generated {r["takes"]} times with identical
settings in each block, so the difference between its takes is the "different mood every time" you noticed.
Speed is letters per second (the take you liked: about 15); liveliness is how much the pitch moves (the take you liked:
about 60 Hz). Pronunciation of the company name uses the new built-in rule.</p>
<ul>{lines}</ul>
{"".join(blocks)}
<p class="intro">Sarvam characters used to make this page: {r["sarvam_characters"]}.</p>
</main></body></html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", nargs="*", help="setup keys to make (default: all)")
    ap.add_argument("--takes", type=int, default=3, help="takes of the reply per setup (default 3)")
    ap.add_argument("--out", type=Path, default=BACKEND.parent / "voice-samples" / f"compare-{dt.date.today()}")
    ap.add_argument("--reference", type=Path, help="a WAV of a take you liked, shown first for comparison")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Hindi in a message must not crash a paid run
    if not get_settings().sarvam_ready:
        sys.exit("SARVAM_API_KEY is not set in backend/.env")
    setups = [s for s in SETUPS if not args.only or s["key"] in args.only]
    args.out.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(run(setups, max(1, args.takes), args.out, args.reference))
    print(f"Sarvam characters used: {result['sarvam_characters']}")
    print(f"Open: {(args.out / 'index.html').resolve()}")


if __name__ == "__main__":  # importing this file must never spend credit
    main()
