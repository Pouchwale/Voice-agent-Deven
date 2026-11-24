"""After the fix, through the app's own code: the fillers and the greeting as ready-made clips (REST, new wording and
loudness) and two replies through the live voice stream (English words now in Hindi script), current voice. Prints pitch
and loudness so the clips can be compared with the replies, and adds an "after the fix" section to index.html.
Costs a few hundred Sarvam characters.   run from backend/."""
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import make_samples as M  # noqa: E402  (its paid run is inside main(), so importing it spends nothing)
from app.services import sarvam  # noqa: E402
from app.services.prompts import fill_template  # noqa: E402

db = sqlite3.connect("file:data/voice_agent.db?mode=ro", uri=True)
cfg = json.loads(db.execute("select data from agent_settings order by rowid desc limit 1").fetchone()[0])
REPLIES = ["MOQ एक हज़ार pieces से start होता है, ताकि print और packaging की quality अच्छी बनी रहे।",
           "हमारी team इसी number पर WhatsApp पर quotation और sample kit details भेज देगी।"]


async def streamed(text: str) -> bytes:
    got = bytearray()

    async def on_audio(pcm: bytes) -> None:
        got.extend(pcm)

    s = sarvam.TTSStream(cfg, 8000, on_audio)
    await s.connect()
    await s.send_sentence(text)
    await s.finish()
    await s.close()
    return bytes(got)


async def main() -> None:
    rows = []
    for i, line in enumerate(REPLIES, 1):
        M.chars += len(sarvam.speakable(line, cfg))
        pcm = await M.cached(f"after_reply_{i}", lambda: streamed(line))
        rows.append(("reply", sarvam.speakable(line, cfg), pcm))
    for i, w in enumerate(cfg["filler_words"]):
        M.chars += len(w) + 1
        pcm = await M.cached(f"after_filler_{i}", lambda: sarvam.tts_rest(w + "।", cfg, 8000))
        rows.append(("filler", w + "।", pcm))
    greeting = fill_template(cfg["opening_line"], cfg, {"name": "Rahul"})
    M.chars += len(greeting)
    rows.append(("greeting", greeting, await M.cached("after_greeting", lambda: sarvam.tts_rest(greeting, cfg, 8000))))
    print("kind      pitch Hz  loudness  sec   text")
    for kind, text, pcm in rows:
        st = M.stats(pcm)
        print(f"{kind:9s} {st['f0_hz']!s:>8} {st['level']!s:>9} {st['sec']:5.2f}  {text[:70]}")
    page = M.OUT / "index.html"
    html = page.read_text(encoding="utf-8")
    if "After the fix" not in html:
        cells = "".join(f"<tr><td>{kind}</td><td>{M.html.escape(text)}</td><td><audio controls preload=none "
                        f"src='after_{'reply_' + str(n) if kind == 'reply' else ('filler_' + str(n) if kind == 'filler' else 'greeting')}.wav'></audio></td></tr>"
                        for n, (kind, text, _) in ((n, r) for n, r in enumerate(rows, 1)) if kind != "filler")
        fillers = "".join(f"<tr><td>filler</td><td>{M.html.escape(text)}</td><td><audio controls preload=none "
                          f"src='after_filler_{j}.wav'></audio></td></tr>" for j, (_, text, _) in
                          enumerate(r for r in rows if r[0] == "filler"))
        html += ("<h2>After the fix (current voice, through the app)</h2><table border=1 cellpadding=6>" + cells + fillers
                 + "</table>")
        page.write_text(html, encoding="utf-8")
    print("Sarvam characters:", M.chars)


if __name__ == "__main__":
    asyncio.run(main())
