# Voice Agent — AI Outbound Calling in Hinglish

The voice agent calls your lead lists in natural **Hinglish** (Indian accent, Hindi + English), asks your qualification questions, and sorts every lead into **Cold / Warm / Hot** with a **1–10 rating**, so your BDE team only spends time on the hot ones. Every call's **recording and transcript** is saved for review. It also **answers inbound calls** to your number: it recognises the caller by their number, greets known customers by name and remembers what they needed on earlier calls. Every customer has a **timeline** of all calls in both directions.

```
Lead list ──► Campaign dialer ──► Vobiz (Indian PSTN) ──► customer's phone
                                      │  live audio (μ-law 8 kHz, both ways)
                                      ▼
            Sarvam Saaras v3 realtime STT ──► GPT-4o-mini ──► Sarvam Bulbul v3 TTS
                                      │
                                      ▼
     SQLite: transcript + recording ──► post-call AI summary, rating /10, Cold / Warm / Hot
                                      │
                                      ▼
   Next.js dashboard: Lead board (Kanban), Analytics, PDF/CSV reports, Live monitor, Settings
```

| Part | Tech |
|---|---|
| Backend | Python 3.13, FastAPI, SQLAlchemy 2 + SQLite (WAL), Prisma-compatible schema |
| Frontend | Next.js 15 (React 19), Tailwind, anime.js, three.js, SWR. Fonts Inter + Hanken Grotesk, blue/white theme from the PouchWale logo |
| Telephony | Vobiz: outbound and inbound calls, bidirectional media stream, call recording |
| Speech-to-text | Sarvam **Saaras v3** realtime streaming (`saaras:v3-realtime`, `codemix` = Hindi in Devanagari + English words in English) |
| Text-to-speech | Sarvam **Bulbul v3** streaming, female voice **`suhani`** (warm, temperature 0.7) |
| Brain | OpenAI **gpt-4o-mini**, max 120 tokens, temperature 0.6 |

---

## 1. Run it locally

Ports **8100** (backend) and **3100** (frontend) are used so this runs next to your other projects.

```powershell
.\start-backend.ps1     # creates the venv on first run, then http://localhost:8100
.\start-tunnel.ps1      # ngrok: https://enjoyable-cubbyhole-straw.ngrok-free.dev -> :8100
.\start-frontend.ps1    # http://localhost:3100
```

Manual equivalent:
```powershell
cd backend; python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env                                   # fill in the keys below
.\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8100
cd ..\frontend; npm install; npm run dev                 # :3100
```

### Keys (`backend/.env`, never in `.env.example`)
| Variable | What |
|---|---|
| `OPENAI_API_KEY` | OpenAI key (gpt-4o-mini) |
| `SARVAM_API_KEY` | Sarvam AI subscription key (STT + TTS) |
| `VOBIZ_AUTH_ID`, `VOBIZ_AUTH_TOKEN` | Vobiz console → API credentials |
| `VOBIZ_FROM_NUMBER` | Your Vobiz Indian DID (caller ID), e.g. `+918071387423` |
| `PUBLIC_BASE_URL` | Public HTTPS URL of this backend (your ngrok URL) |

Then open **Settings → Test providers**: OpenAI, Sarvam voice (REST + live streaming), Sarvam speech recognition and Vobiz each show pass/fail with latency.

**Try it without a phone:** **Test agent** (top bar) → Start. You talk to the agent through your browser mic with the same brain, voice and silence rules as a real call. Use headphones.

**Voice samples:** `voice-samples/` has the same greeting in 8 Sarvam female voices. `voice-samples/compare/` puts the current voice (Bulbul v3 Suhani) next to Sarvam's newer **Bulbul v4** female personas made for customer and sales calls (Aparna, Aarti, Ritu, Simran, Ishita, Sarika), all saying the same greeting. Pick one in **Settings → Voice** (the "(v4)" entries) and use **Preview voice**; v4 voices stream just as fast on calls (~220 ms to first audio, measured).

---

## 2. Going live with phone calls (Vobiz)

1. **Public HTTPS URL:** `start-tunnel.ps1` (ngrok) and `PUBLIC_BASE_URL` in `backend/.env`. Vobiz calls the webhooks and opens the audio WebSocket on it.
2. **Production hosting must be in India** (e.g. AWS `ap-south-1` Mumbai). Vobiz keeps Indian call audio in India ("media anchoring", hangup cause 2070 otherwise). It also lowers reply latency.
3. **Vobiz account:** India-registered business with KYC done, a rented Indian DID as caller ID (about 2 minutes to activate).
4. **Compliance (TRAI):** call only consented leads, inside the calling window (default 09:30–19:30 IST). **DNC** leads are never dialed.
5. Place one call from **Leads → Call now**, watch it on **Live**, then start campaigns.

Every call is recorded twice: a local stereo WAV (left = customer, right = agent, `backend/data/recordings/`) and Vobiz's own recording as a backup.

### Inbound calls (customers calling your number)
1. Open **Inbound** → **Connect inbound on Vobiz**. This creates (or updates) the Vobiz application `GP3-Voice-Agent-Inbound` with this backend's answer + hangup URLs and attaches your number to it. If the number currently goes somewhere else (an IVR app or a SIP trunk) you are shown where, and asked before it is moved; **Disconnect** puts the previous routing back.
2. Call your number from a mobile to test. The card shows the last inbound call received.
3. If the ngrok URL ever changes, the card says so: click **Connect** again. To revoke a leaked inbound URL, change `INBOUND_TOKEN_SALT` in `backend/.env` and connect again.

Notes: trial numbers and 140/160-series numbers can't receive inbound calls. The agent answers only while this computer, the backend and the tunnel are running; calls that ring while it is offline are added later as **missed** calls (from Vobiz call records, every 10 minutes), so the team can call back.

| Situation | What happens |
|---|---|
| Known customer calls | Number matched (mobile or landline, any format) → greeted by name; the agent remembers earlier calls (product, quantity, what we promised, call-back time) and confirms instead of re-asking |
| New number calls | A lead is created (source "Inbound call"); the agent asks their name; after the call name/company/city are filled in from the conversation |
| Hidden / international number | Answered normally; no lead is created for hidden numbers (or foreign numbers unless `ALLOW_INTERNATIONAL=true`) |
| Inbound switched off, all lines busy, or AI keys missing | The caller hears the unavailable message (or a busy tone, see Settings → Calling) and the call is logged as **missed** for call-back |
| Caller hangs up before the agent speaks | Logged as missed, shown under **To call back** |
| A campaign is dialing the same person while they call us | Our dial is cancelled (no attempt used); after an inbound conversation their pending campaign items are marked done, so they aren't cold-called right after |
| We call back a missed caller | The agent opens with "आपने हमें call किया था…" instead of the cold-call opener |

Two lines are always kept free for inbound callers while campaigns run. Both **Inbound calling** and **Outbound calling** can be switched off in Settings → Calling (both are on by default).

### Security
- **The tunnel exposes only Vobiz paths.** `start-tunnel.ps1` uses `ngrok-policy.yml`: anyone opening the ngrok URL sees 404 for the dashboard, API and docs; only `/vobiz/*` (with per-call secret tokens, and a separate secret for the inbound URLs) gets through.
- **Inbound privacy.** Caller ID can be faked, so on inbound calls the agent greets by name but shares specifics (quantities, quotes) only after the caller brings them up, never reads out internal notes, and stops if the caller says they are someone else. More than 5 calls from one number in 10 minutes get a busy tone.
- **Dashboard login.** On this computer (`localhost`) no login is needed. From any other machine the dashboard asks for `DASHBOARD_PASSWORD` (set it in `backend/.env`; empty = local only). `REQUIRE_LOGIN_LOCAL=true` asks for it on this computer too. Login attempts are rate-limited; requests from other websites are blocked.
- **Toll-fraud guard.** Only Indian `+91` numbers can be stored or dialed unless `ALLOW_INTERNATIONAL=true`.
- **Data safety.** CSV exports are protected against spreadsheet formula injection; recording downloads send Vobiz credentials only to Vobiz; uploads and audio frames are size-capped.
- Keep real keys only in `backend/.env` (never in `.env.example`).

---

## 3. How a call behaves

| Moment | What the agent does |
|---|---|
| Customer picks up | Waits ~1 s in case they say "Hello?", then the **permission-first** greeting (synthesised while ringing): "नमस्ते राहुल जी! मैं PouchWale से बोल रही हूँ। बस दो minute लूँगी, अभी बात कर सकते हैं?" ("PouchWale" is spoken as पाउचवाले, see Settings → Pronunciation fixes) |
| "Haan, boliye" | One-line reason for the call, then the qualification questions, one at a time |
| Busy / call later | Asks a convenient time, confirms it, ends politely (callback time saved) |
| Customer talks | Live transcription; **350 ms** of silence = "done talking" |
| Customer pauses mid-sentence | "हमें pouch चाहिए, और…": the agent waits a moment instead of jumping in |
| Slow reply | Natural Hindi filler so the line never goes dead: "हम्म, एक second…" after a question, "जी, समझ गई…" after a statement. Never the same one twice in a row, not before every reply |
| Customer interrupts | Stops instantly (barge-in). "haan" / "ji" / "ok" do **not** interrupt |
| **10 s silence** | "हेलो…? मेरी आवाज़ आ रही है आपको?" |
| **3 s more silence** | "लगता है line पर आवाज़ नहीं आ रही है। कोई बात नहीं, आपका दिन अच्छा रहे। Thank you!" → hangs up |
| Done / not interested / wrong number | Short goodbye → hangs up |
| Max call length (default 5 min) | Closing line → hangs up |

All lines and timings are editable in **Settings**. The agent talks like a warm Indian tele-caller: always feminine Hindi forms (बोल रही हूँ, समझ गई), everyday Hinglish instead of formal Hindi, a short natural reaction before answering ("अच्छा", "जी", "समझ गई"), key details repeated back, and never "ओह / OK / got it". It mirrors the customer's language, stays on your business topic, never invents prices or MOQs (offers an expert call-back), uses no personal name unless you set one, and says honestly it is an AI assistant if asked.

### Reply speed (target under 1.5 s)
Measured with your real Sarvam key; the LLM figure is the expected range until your OpenAI key is added.

| Step | Time |
|---|---|
| Customer stops → final transcript (Saaras v3, 350 ms pause) | ~0.40 s |
| Hand-off to the brain | 0.04 s |
| GPT-4o-mini first words (prompt cache + pre-warmed connection, first clause sent early) | ~0.3–0.6 s |
| First voice audio (Bulbul v3 on a pre-opened socket) | **0.17 s** measured |
| **Total** | **~0.9–1.2 s** + ~0.2 s phone network |

Every call shows its real average response time on the call summary, and **Analytics** charts it per day against the 1.5 s target.

### Lead scoring after each call (3–5 s)
- **Hot**: strong interest (need + quantity or timeline soon, asks for quote/sample/expert).
- **Warm**: normal interest (wants details, timeline later, must check internally).
- **Cold**: picked up and cut the call, never spoke, not interested, wrong person.
- Rating 1–10, summary, next action for sales, callback time, key points, objection categories, qualification checklist.

---

## 4. The dashboard

| Page | What it's for |
|---|---|
| **Dashboard** | Today's numbers, live calls, top hot leads |
| **Analytics** | Custom date range: funnel (dialed → picked up → conversation → warm/hot → hot), calls per day, best time to call, interest split, ratings, durations, end reasons, objections, qualification coverage, campaign comparison, response-time trend |
| **Export report** (on Analytics) | **PDF** (KPIs, charts, hot/warm lead tables, all calls, optional full transcripts, Hindi rendered correctly) · **Calls CSV** (every field + transcript) · **Leads CSV** (with board stage). Any custom date range |
| **Leads** | Import CSV (columns auto-detected, Indian numbers normalised, Excel-mangled numbers caught), add/edit, export |
| **Lead board** | Kanban: **New → No answer → Cold → Warm → Hot**. Cards move automatically after each call; your team can drag them (or use the card menu) |
| **Campaigns** | Auto-dialer: concurrency, retries for no-answer/busy, calling window, pause/resume |
| **Calls** | Every call (filter by direction and outcome): rating ring, interest meter, summary, next action, qualification, recording player, clickable transcript, and the customer's timeline |
| **Inbound** | Vobiz connection status + one-click connect; today / 7 / 30-day numbers (inbound calls, answered, missed, to call back, new and returning callers); live inbound calls; the inbound call list with a **To call back** tab, caller type, reason for calling and next step; click a call for its summary, a Call back button and the caller's timeline. A notice pops up when a call is missed |
| **Customer timeline** (lead page, call page, inbound drawer) | Every call in both directions grouped by day with IST time, outcome, talk time, summary and next step, plus "added to campaign" and "lead created"; the next promised step on top |
| **Live** | Ongoing calls (marked Inbound / Outbound, returning or new caller) with live transcript, levels, listen-in, hang-up |
| **Settings** | Voice, **tone & accent** (warm / professional / energetic / calm, Hinglish / mostly Hindi / mostly English), script, knowledge base, questions, objection handling, interest rules, silence timings, fillers, pronunciation fixes, **calling switches + inbound greetings**, calling window |

Business metrics count real phone calls only; browser test calls are kept out (toggle "Include test calls" in Analytics). "Dialed", "picked up", the funnel and "best time to call" count **outbound** calls only; inbound calls have their own numbers. The browser **Test agent** can rehearse both directions ("Customer calls us").

---

## 5. Before the first real campaign

- `Settings → Knowledge base` has `[FILL IN]` placeholders for **MOQ, pricing and lead time**. Until filled, the agent never quotes numbers.
- Run the **UAT**: `docs/UAT_Test_Cases.xlsx` — 296 test cases across 26 areas (telephony, STT, TTS, conversation, latency, scoring, leads, board, campaigns, analytics, reports, settings, database, security, UI, reliability, compliance, **inbound calls, memory & timeline**, **calling on/off switches**, **voice tone, accent & fillers**). Fill in Status/Tester/Date/Notes; the Summary sheet shows the go-live verdict.

---

## 6. Data, Prisma, tests

- Database: `backend/data/voice_agent.db` (SQLite, WAL). Tables are created/migrated on start.
- **Prisma:** `frontend/prisma/schema.prisma` mirrors the tables (verified with `prisma db pull`). `DATABASE_URL="file:../../backend/data/voice_agent.db" npx prisma generate`. Don't run `prisma migrate`; the backend owns the schema.
- API: `docs/API_CONTRACT.md` and `http://localhost:8100/docs`.
- Automated tests: `cd backend; .\.venv\Scripts\python -m pytest` — simulated calls (greeting → conversation → end, barge-in, fillers, 10 s + 3 s silence hang-up, recording, scoring, picked-up-and-cut → cold), a simulated Vobiz media stream, browser calls, CSV import edge cases, board moves, analytics numbers, PDF/CSV reports, dialer concurrency and retries.

## Project layout
```
backend/app/
  main.py                    FastAPI app, startup (DB + migration, dialer, watchdog)
  services/session.py        real-time conversation engine (STT → LLM → TTS, barge-in, fillers, silence, spare TTS socket)
  services/sarvam.py         Saaras v3 realtime STT + Bulbul v3 streaming/REST TTS
  services/llm.py            GPT-4o-mini streaming, early first-clause chunking, prompt cache
  services/prompts.py        agent prompt (static part first for caching) + scoring prompt
  services/vobiz.py          Vobiz REST + answer XML    routers/vobiz.py  webhooks + media stream
  services/call_manager.py   call lifecycle             services/dialer.py  campaign auto-dialer
  services/analysis.py       post-call scoring (cold/warm/hot, rating, objections)
  services/reporting.py      date ranges + analytics    services/pdf_report.py  PDF report
  routers/leads.py           leads, CSV import, Kanban board
  routers/analytics.py       analytics + PDF/CSV report downloads
  assets/                    logo + fonts for the PDF (Inter, Hanken Grotesk, Noto Sans Devanagari; OFL)
frontend/src/                Next.js dashboard
frontend/prisma/             Prisma schema (same SQLite DB)
docs/                        API_CONTRACT.md, UAT_Test_Cases.xlsx
voice-samples/               Sarvam female voice samples
```
