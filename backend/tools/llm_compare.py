"""Compare AI models for the live conversation before changing OPENAI_MODEL: how fast each one starts answering
with the agent's real prompt, and what it answers.

    cd backend
    .\\.venv\\Scripts\\python tools\\llm_compare.py                                   # the default candidates
    .\\.venv\\Scripts\\python tools\\llm_compare.py --models gpt-4o-mini gpt-4.1-mini --repeats 3

Each request is built the way session.py builds a reply: the fixed prompt (Settings), the call details, the
greeting, the customer's line and this turn's notes (mood, "don't call", misheard words). It is streamed and timed:
first word, first full sentence (what the voice can start on), whole reply. Models take turns line by line, so a
slow minute on OpenAI's side hits every model alike. The first request per model only warms up its prompt cache (a
live call has a warm cache after its first reply) and is not counted.

Changes nothing in the app. Costs a little OpenAI usage: the tokens per model are printed at the end.
Writes results.json and index.html (every reply side by side) to --out.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import re
import statistics
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings  # noqa: E402
from app.services import llm  # noqa: E402
from app.services.prompts import (MOOD_NOTES, NO_CALLS_NOTE, asked_no_calls, detect_mood, fill_template,  # noqa: E402
                                  lead_context, misheard_note, static_system_prompt)

DEFAULT_MODELS = ["gpt-4o-mini", "gpt-4.1-mini", "gpt-5.4-mini", "gpt-5.4-nano"]
LEAD = {"name": "Rahul", "company": "", "city": ""}
LINES = [  # what customers say on our calls, one turn after the greeting
    "हाँ जी बोलिए, कौन बोल रहे हैं?",
    "मुझे अपने spices के लिए pouch चाहिए था।",
    "Rate क्या है आपका? एक pouch कितने का पड़ेगा?",
    "Minimum order कितना लेना पड़ेगा?",
    "Sample मिल सकता है क्या पहले?",
    "अभी मैं थोड़ा busy हूँ, बाद में बात करते हैं।",
    "मेरे पास already एक supplier है।",
    "देखिए, मुझे दोबारा call मत करना।",
]
SENTENCE_END = re.compile(r"[।?!]|\.(\s|$)")
# quick flags for the page (the judgement is a read of the replies, not these)
FLAGS = {
    "speaks as a man": re.compile(r"रहा हूँ|गया हूँ|दूँगा|करूँगा|सकता हूँ|बताता हूँ"),
    "list / bullets": re.compile(r"(^|\n)\s*([-*•]|\d+[.)])\s"),
    "a price": re.compile(r"₹\s?\d|\d\s?(रुपये|रुपए|rupees|/-)"),
    "2+ questions": re.compile(r"\?[^?]*\?"),
}


def notes_for(text: str) -> list[str]:
    """This turn's notes, in session.py's order: mood, don't-call, misheard words (all-Hindi lines: no language note)."""
    mood = detect_mood(text)
    out = [MOOD_NOTES.get(mood) if mood else None, NO_CALLS_NOTE if asked_no_calls(text) else None, misheard_note(text)]
    return [n for n in out if n]


def request_params(model: str, effort: str | None, settings) -> dict:
    if model.startswith("gpt-5"):  # reasoning family: no temperature, completion budget, least thinking
        p = {"max_completion_tokens": settings.llm_max_tokens}
        if effort:
            p["reasoning_effort"] = effort
        return p
    return {"max_tokens": settings.llm_max_tokens, "temperature": settings.llm_temperature}


async def ask(model: str, messages: list[dict], params: dict, key: str) -> dict:
    t0 = time.perf_counter()
    stream = await llm.client().chat.completions.create(
        model=model, messages=messages, stream=True, stream_options={"include_usage": True},
        prompt_cache_key=key, **params)
    first = sentence = None
    text, usage = "", None
    async for chunk in stream:
        if chunk.usage:
            usage = chunk.usage
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            now = time.perf_counter() - t0
            first = first if first is not None else now
            text += chunk.choices[0].delta.content
            if sentence is None and SENTENCE_END.search(text):
                sentence = now
    total = time.perf_counter() - t0
    details = getattr(usage, "prompt_tokens_details", None)
    return {"first_ms": round((first if first is not None else total) * 1000),
            "sentence_ms": round((sentence if sentence is not None else total) * 1000),
            "total_ms": round(total * 1000), "text": text.strip(),
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "cached_tokens": getattr(details, "cached_tokens", 0) or 0,
            "output_tokens": getattr(usage, "completion_tokens", 0) or 0}


async def warm_up(model: str, messages: list[dict], key: str, settings) -> tuple[str | None, str]:
    """The first request per model (fills its prompt cache). For the gpt-5 family, finds the lowest reasoning
    setting the model accepts. Returns (effort, error)."""
    efforts = ["none", "minimal", "low"] if model.startswith("gpt-5") else [None]
    last = ""
    for effort in efforts:
        try:
            await ask(model, messages, request_params(model, effort, settings), key)
            return effort, ""
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {str(e)[:200]}"
    return None, last


def pct(values: list[int], p: float) -> int:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


def page(models: list[str], results: dict, out: Path, meta: dict) -> None:
    esc = html.escape
    rows = []
    for m in models:
        r = results[m]
        if r.get("error"):
            rows.append(f"<tr><td>{esc(m)}</td><td colspan=6>not usable: {esc(r['error'])}</td></tr>")
            continue
        firsts = [x["first_ms"] for x in r["runs"]]
        sents = [x["sentence_ms"] for x in r["runs"]]
        words = [len(x["text"].split()) for x in r["runs"]]
        flags = sum(1 for x in r["runs"] for rx in FLAGS.values() if rx.search(x["text"]))
        rows.append(f"<tr><td><b>{esc(m)}</b>{' (now)' if m == meta['current'] else ''}"
                    f"{'<br><small>reasoning: ' + esc(r['effort']) + '</small>' if r.get('effort') else ''}</td>"
                    f"<td>{statistics.median(firsts):.0f} ms</td><td>{pct(firsts, 0.9)} ms</td><td>{max(firsts)} ms</td>"
                    f"<td>{statistics.median(sents):.0f} ms</td><td>{statistics.mean(words):.0f}</td><td>{flags}</td></tr>")
    blocks = []
    for i, line in enumerate(LINES):
        cells = []
        for m in models:
            runs = [x for x in results[m].get("runs", []) if x["line"] == i]
            body = "".join(
                f"<p>{esc(x['text'])}<br><small>first word {x['first_ms']} ms · sentence {x['sentence_ms']} ms"
                f"{' · ' + ', '.join(k for k, rx in FLAGS.items() if rx.search(x['text'])) if any(rx.search(x['text']) for rx in FLAGS.values()) else ''}"
                f"</small></p>" for x in runs) or "<p>-</p>"
            cells.append(f"<td><b>{esc(m)}</b>{body}</td>")
        blocks.append(f"<h3>Customer: {esc(line)}</h3><table><tr>{''.join(cells)}</tr></table>")
    out.joinpath("index.html").write_text(f"""<!doctype html><html lang="hi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>AI model comparison</title>
<style>body{{font:15px/1.5 system-ui,'Nirmala UI',sans-serif;margin:24px auto;max-width:1200px;padding:0 16px;color:#1d1d1f;background:#fff}}
table{{border-collapse:collapse;width:100%;margin:8px 0 20px}}td,th{{border:1px solid #ddd;padding:8px;vertical-align:top;text-align:left}}
th{{background:#f4f4f6}}small{{color:#666}}h3{{margin-top:28px}}</style></head><body>
<h1>AI model comparison: how fast each starts answering</h1>
<p>{esc(meta['when'])}. Every model got the agent's real prompt ({meta['prompt_tokens']:,} tokens), the greeting and the
same {len(LINES)} customer lines, {meta['repeats']}× each. "First word" is when the model starts writing; the voice can start
on the first full sentence. Flags are quick hints only: read the replies.</p>
<table><tr><th>Model</th><th>First word (median)</th><th>9 in 10 within</th><th>Slowest</th><th>First sentence (median)</th>
<th>Words per reply</th><th>Flags</th></tr>{''.join(rows)}</table>
{''.join(blocks)}</body></html>""", encoding="utf-8")


async def run(models: list[str], repeats: int, out: Path) -> None:
    settings = get_settings()
    cfg = json.load(urllib.request.urlopen("http://127.0.0.1:8100/api/settings", timeout=5))  # the saved settings
    static = static_system_prompt(cfg, "outbound", None)
    key = "va-" + hashlib.sha1(static.encode("utf-8")).hexdigest()[:16]  # as session.py
    base = [{"role": "system", "content": static},
            {"role": "system", "content": lead_context(LEAD, "", direction="outbound")},
            {"role": "assistant", "content": fill_template(cfg["opening_line"], cfg, LEAD)}]
    convo = [[*base, {"role": "user", "content": line}, *({"role": "system", "content": n} for n in notes_for(line))]
             for line in LINES]
    await llm.warmup()
    results: dict = {}
    for m in models:
        effort, error = await warm_up(m, convo[0], key, settings)
        results[m] = {"effort": effort, "error": error, "runs": []}
        print(f"{m}: {'warmed up' + (f' (reasoning {effort})' if effort else '') if not error else 'NOT USABLE - ' + error}")
    usable = [m for m in models if not results[m]["error"]]
    for rep in range(repeats):
        for i, messages in enumerate(convo):
            for m in usable:
                try:
                    r = await ask(m, messages, request_params(m, results[m]["effort"], settings), key)
                except Exception as e:  # noqa: BLE001
                    print(f"  {m} line {i + 1}: failed ({type(e).__name__}: {str(e)[:120]})")
                    continue
                r.update(line=i, repeat=rep)
                results[m]["runs"].append(r)
            print(f"  round {rep + 1}, line {i + 1}/{len(LINES)} done")
    out.mkdir(parents=True, exist_ok=True)
    prompt_tokens = next((r["prompt_tokens"] for m in usable for r in results[m]["runs"]), 0)
    meta = {"when": time.strftime("%Y-%m-%d %H:%M"), "current": settings.openai_model, "repeats": repeats,
            "prompt_tokens": prompt_tokens, "lines": LINES}
    out.joinpath("results.json").write_text(json.dumps({"meta": meta, "results": results}, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    page(models, results, out, meta)
    print("\nmodel            first word: median  p90   max | first sentence median | tokens in (cached) / out")
    for m in models:
        runs = results[m]["runs"]
        if not runs:
            print(f"{m:16s} not usable: {results[m]['error']}")
            continue
        f = [x["first_ms"] for x in runs]
        print(f"{m:16s} {statistics.median(f):17.0f} {pct(f, 0.9):5d} {max(f):5d} | {statistics.median([x['sentence_ms'] for x in runs]):21.0f} | "
              f"{sum(x['prompt_tokens'] for x in runs):,} ({sum(x['cached_tokens'] for x in runs):,}) / "
              f"{sum(x['output_tokens'] for x in runs):,}  (+1 warm-up request)")
    print(f"\npage: {out / 'index.html'}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parents[2] / "llm-samples" / f"compare-{date.today():%Y-%m-%d}")
    a = ap.parse_args()
    asyncio.run(run(a.models, a.repeats, a.out))


if __name__ == "__main__":
    main()
