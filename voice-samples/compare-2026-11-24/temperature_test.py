"""Does bulbul:v4-flash honour `temperature`? Two takes of the same line at 0.01 and at 0.3, compared with the two takes
at the default (results.json 'baseline_deva_vs_deva2'). If it works, a low value makes every take sound the same.
Also prints each voice's pitch from results.json.   run from backend/."""
import asyncio
import base64
import json
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import make_samples as M  # noqa: E402  (its paid run is inside main(), so importing it spends nothing)
from websockets.asyncio.client import connect  # noqa: E402

VOICE = "sarika_hi_conversation"


async def take(text: str, temperature: float) -> bytes:
    M.chars += len(text)
    q = urllib.parse.urlencode({"model": "bulbul:v4-flash", "send_completion_event": "true"})
    async with connect(f"{M.settings.sarvam_ws_url}/text-to-speech/ws?{q}", additional_headers=M._headers(),
                       open_timeout=8, max_size=8 * 1024 * 1024, compression=None) as ws:
        await ws.send(json.dumps({"type": "config", "data": {
            "speaker": VOICE, "language_code": "hi-IN", "pace": M.PACE, "speech_sample_rate": M.RATE,
            "output_audio_codec": "linear16", "min_buffer_size": 30, "max_chunk_length": 200, "temperature": temperature}}))
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


async def main() -> None:
    res = json.loads((M.OUT / "results.json").read_text(encoding="utf-8"))
    print("voice                    pitch Hz (3 lines, Hindi script)   spread st   level")
    for v, r in res["voices"].items():
        print(f"  {v:24s} {[row['deva']['f0_hz'] for row in r['lines']]}  "
              f"{[row['deva']['f0_spread_st'] for row in r['lines']]}  {[row['deva']['level'] for row in r['lines']]}")
    default = [row["baseline_deva_vs_deva2"]["median_st"] for row in res["voices"][VOICE]["lines"]]
    print(f"two takes at the default: {default}")
    for t in (0.01, 0.3):
        out = []
        for i, (_, deva) in enumerate(M.LINES[:2]):
            a = await M.cached(f"temp{t}_{i + 1}_a", lambda: take(deva, t))
            b = await M.cached(f"temp{t}_{i + 1}_b", lambda: take(deva, t))
            out.append(M.tone_shift(a, b)["median_st"])
        print(f"two takes at temperature {t}: {out}")
    print("Sarvam characters:", M.chars)


if __name__ == "__main__":
    asyncio.run(main())
