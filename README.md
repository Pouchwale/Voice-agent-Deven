# Voice Agent — AI Outbound Calling in Hinglish

The voice agent calls your lead lists in natural **Hinglish** (Indian accent, Hindi + English), asks your qualification questions, and sorts every lead into **Cold / Warm / Hot** with a **1–10 rating**, so your BDE team only spends time on the hot ones. Every call's **recording and transcript** is saved for review. It also **answers inbound calls** to your number: it recognises the caller by their number, greets known customers by name and remembers what they needed on earlier calls. Every customer has a **timeline** of all calls in both directions.

```
Lead list ──► Campaign dialer ──► Plivo (or Vobiz) ──► customer's phone
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
| Frontend | Next.js 15 (React 19), Tailwind, anime.js, three.js, SWR. Fonts Inter + Hanken Grotesk, blue/white theme from the PouchWale logo; optional **PouchWale design** (switch in the top bar, Mada + Mulish) |
| Telephony | Plivo (active) or Vobiz, switched with `TELEPHONY_PROVIDER`: outbound and inbound calls, bidirectional media stream, call recording |
| Speech-to-text | Sarvam **Saaras v4** realtime streaming (`saaras:v4` with our key terms in `SARVAM_STT_KEYTERMS`, `codemix` = Hindi in Devanagari + English words in English). Chosen on 2026-11-25 over `saaras:v3-realtime`: word errors 12.8% -> 9.5%, key terms 91% -> 96% (`stt-benchmark/2026-11-25`, measured with `backend/tools/stt_benchmark.py`). A transcript with no human voice behind it (traffic, rumble) is not answered |
| Text-to-speech | Sarvam **Bulbul** streaming. The voice chosen in **Settings → Voice** decides the model: the "(v4)" voices (e.g. Sarika · conversational) use **Bulbul v4** (`bulbul:v4-flash`), the older voices use **Bulbul v3** (`SARVAM_TTS_MODEL`, default voice `suhani`) |
| Brain | OpenAI **gpt-4.1-mini** (`OPENAI_MODEL`), max 120 tokens, temperature 0.6. Chosen on 2026-11-20 over gpt-4o-mini: same reply quality, first sentence ~0.2 s sooner (`backend/tools/llm_compare.py` compares models on the real prompt) |

---

## 1. Run it locally

Ports **8100** (backend) and **3100** (frontend) are used so this runs next to your other projects.

Easiest: double-click **`start-all.bat`**. It starts the backend, the tunnel and the dashboard, each in its own window, and only the parts that are not running yet (so after a computer restart, or when one window was closed, just run it again). Or one by one:

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
| `OPENAI_API_KEY` | OpenAI key (live calls use `OPENAI_MODEL`, gpt-4.1-mini) |
| `SARVAM_API_KEY` | Sarvam AI subscription key (STT + TTS) |
| `TELEPHONY_PROVIDER` | `plivo` (default) or `vobiz`: which phone provider places and receives calls |
| `PLIVO_AUTH_ID`, `PLIVO_AUTH_TOKEN` | Plivo console → Overview → Auth ID / Auth Token |
| `PLIVO_FROM_NUMBER` | Your Plivo Indian number (caller ID), e.g. `+918071387423` |
| `VOBIZ_AUTH_ID`, `VOBIZ_AUTH_TOKEN`, `VOBIZ_FROM_NUMBER` | Only when `TELEPHONY_PROVIDER=vobiz` (kept so you can switch back) |
| `PUBLIC_BASE_URL` | Public HTTPS URL of this backend (your ngrok URL) |

Then open **Settings → Test providers**: OpenAI, Sarvam voice (REST + live streaming), Sarvam speech recognition and the active phone provider (Plivo or Vobiz) each show pass/fail with latency.

**Try it without a phone:** **Test agent** (top bar) → Start. You talk to the agent through your browser mic with the same brain, voice and silence rules as a real call. Use headphones.

**Voice samples:** `voice-samples/` has the same greeting in 8 Sarvam female voices. `voice-samples/compare/` puts the current voice (Bulbul v3 Suhani) next to Sarvam's newer **Bulbul v4** female personas made for customer and sales calls (Aparna, Aarti, Ritu, Simran, Ishita, Sarika), all saying the same greeting. Pick one in **Settings → Voice** (the "(v4)" entries) and use **Preview voice**; v4 voices stream just as fast on calls (~220 ms to first audio, measured).

---

## 2. Going live with phone calls (Plivo, or Vobiz)

The phone provider is chosen by `TELEPHONY_PROVIDER` in `backend/.env` (`plivo` or `vobiz`). Both read the same XML and stream the same call audio, so everything else (agent, recordings, inbound, reports) works the same with either; switching is one word plus a backend restart. Plivo's webhooks live under `/plivo/*`, Vobiz's under `/vobiz/*`.

1. **Public HTTPS URL:** `start-tunnel.ps1` (ngrok) and `PUBLIC_BASE_URL` in `backend/.env`. The provider calls the webhooks and opens the audio WebSocket on it.
2. **Production hosting must be in India** (e.g. AWS `ap-south-1` Mumbai). Indian call audio has to stay in India ("media anchoring", hangup cause 2070 otherwise). It also lowers reply latency.
3. **Provider account:** India-registered business with KYC approved and a rented Indian number as caller ID. On Plivo the account must be in the India data region, the number's compliance application must be *accepted*, and a trial account can only call its verified (sandbox) numbers. Promotional calls need a 140-series number.
4. **Compliance (TRAI):** call only consented leads, inside the calling window (default 09:30–19:30 IST). **DNC** leads are never dialed.
5. Place one call from **Leads → Call now**, watch it on **Live**, then start campaigns.

Every call is recorded twice: a local stereo WAV (left = customer, right = agent, `backend/data/recordings/`) and the provider's own recording as a backup.

**A call that would go dead is not dialed.** Two problems make a call ring and then drop at once: the voice service (Sarvam) has no credit left, or the ngrok tunnel is not running (the phone company then can't reach the agent). Just before dialing, the backend checks for both. When one is proven, the customer's phone never rings and **Call now** says why: *"Cannot place call: Sarvam (the voice service) has no credits left. Add credits in the Sarvam dashboard, then call again."* or *"Cannot place call: The ngrok tunnel is not running. Run start-all.bat, then call again."* Nothing is saved and the lead is not touched. A running campaign pauses instead of using up its list; resume it once the problem is fixed.
- The credit check runs only after something failed (a call that ended with an error, a greeting the voice service refused), so a normal call is not slowed down. It costs two characters of Sarvam credit, and the first good answer switches it off again.
- The tunnel check asks the public link (`/plivo/ping`) at most once a minute.
- A check that gets no clear answer never holds a call back.
- A call that ended with an error while Sarvam had no credit says so on its call page.

### Inbound calls (customers calling your number)
1. Open **Inbound** → **Connect inbound on Plivo** (or Vobiz). This creates (or updates) the provider application `GP3-Voice-Agent-Inbound` with this backend's answer + hangup URLs and attaches your number to it. If the number currently goes somewhere else (an IVR app or a SIP trunk) you are shown where, and asked before it is moved; **Disconnect** puts the previous routing back.
2. Call your number from a mobile to test. The card shows the last inbound call received.
3. If the ngrok URL ever changes, the card says so: click **Connect** again. To revoke a leaked inbound URL, change `INBOUND_TOKEN_SALT` in `backend/.env` and connect again.

Notes: trial numbers and 140/160-series numbers can't receive inbound calls. The agent answers only while this computer, the backend and the tunnel are running; calls that ring while it is offline are added later as **missed** calls (from the provider's call records, every 10 minutes), so the team can call back.

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
- **The tunnel exposes only phone-provider paths.** `start-tunnel.ps1` uses `ngrok-policy.yml`: anyone opening the ngrok URL sees 404 for the dashboard, API and docs; only `/plivo/*` and `/vobiz/*` (with per-call secret tokens, and a separate secret for the inbound URLs) gets through.
- **Inbound privacy.** Caller ID can be faked, so on inbound calls the agent greets by name but shares specifics (quantities, quotes) only after the caller brings them up, never reads out internal notes, and stops if the caller says they are someone else. More than 5 calls from one number in 10 minutes get a busy tone.
- **Dashboard login.** On this computer (`localhost`) no login is needed. From any other machine the dashboard asks for `DASHBOARD_PASSWORD` (set it in `backend/.env`; empty = local only). `REQUIRE_LOGIN_LOCAL=true` asks for it on this computer too. Login attempts are rate-limited; requests from other websites are blocked.
- **Toll-fraud guard.** Only Indian `+91` numbers can be stored or dialed unless `ALLOW_INTERNATIONAL=true`.
- **Data safety.** CSV exports are protected against spreadsheet formula injection; recording downloads send provider credentials only to that provider's own hosts; uploads and audio frames are size-capped.
- Keep real keys only in `backend/.env` (never in `.env.example`).

---

## 3. How a call behaves

| Moment | What the agent does |
|---|---|
| Customer picks up | Waits ~1 s in case they say "Hello?", then the **permission-first** greeting (synthesised while ringing): "नमस्ते राहुल जी! मैं PouchWale से बोल रही हूँ। बस दो minute लूँगी, अभी बात कर सकते हैं?" (the company name is always spoken "पाउच वाले", however it is written: "PouchWale", "Pouch Wale", "pouchwale", "पाउच वाले"… in Hindi and English calls alike. This is built into `speakable()` in `services/sarvam.py`, not a Settings rule; other words are fixed in Settings → Pronunciation fixes) |
| "Haan, boliye" | One-line reason for the call, then the qualification questions, one at a time |
| Busy / call later | Asks a convenient time, confirms it, ends politely (callback time saved) |
| Customer talks | Live transcription; **350 ms** of silence = "done talking" |
| Customer pauses mid-sentence | "हमें pouch चाहिए, और…": the agent waits a moment instead of jumping in |
| Slow reply | A short, plainly spoken Hindi filler so the line never goes dead: "हम्म" after a question, "जी" / "अच्छा" after a statement. Never the same one twice in a row, not before every reply, never before a goodbye, and skipped once the answer is already with the voice engine (it would only make the answer wait) |
| AI slow to start | No first word from the AI after 1.2 s (normally 0.6-0.9 s; a rare stall lasts 5+ s): a second, identical request races the first and whichever answers first is used. Every answered turn writes a `timing on <call>, turn N` line to the backend log (speech end, final transcript, first word, first audio, filler) |
| Customer asks something | She answers **as a packaging expert** (pouch types, materials, finishes, design ideas, which pouch suits what and why) and **waits**: no next question in the same breath. Only an exact price, exact size or specs, a date, payment, complex custom work or a company fact she was not given goes to our expert, with no number or promise from her. When they react ("हाँ", "ठीक है") she goes on with her next question. If they stay quiet for 6 s she gently asks once "और कुछ जानना है आपको?"; after that the normal silence steps apply |
| Voice returns silence | Bulbul sometimes adds many seconds of pure silence to a line (a 1 s filler came back as 30 s). Any silent stretch is cut to 0.7 s before it reaches the call |
| Remember earlier calls | Settings switch (`caller_memory`, on by default). Off, for testing: every call starts as a first call (name and number only, new-caller greeting, no call-back apology). All calls, transcripts, recordings and lead details stay saved. Turn it back on before calling real customers |
| Customer interrupts | Stops instantly (barge-in). "haan" / "ji" / "ok" do **not** interrupt |
| Both start talking at once (false start) | A short sound from the customer in the first 1.5 s of the agent's reply can cut the agent off, and speech recognition then labels the fragment "ठीक है?" / "हाँ" / "Okay". That is not counted as the customer's words: the agent answers what they really said again, once (the call's `false_starts` metric counts it). Real words ("रुकिए…") and an acknowledgement later in the sentence are handled as before |
| Asked "which types / products do you have?" | Names two or three in one sentence, never a list. If the AI writes a bullet or numbered list anyway, it is spoken (and saved in the transcript) as one sentence with at most three items plus "वगैरह": a five-line list took 16 s on a live call, now about 9 s |
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
| **Calls** | Every call (filter by direction and outcome): rating ring, interest meter, summary, next action, qualification, recording player, clickable transcript, the review agent's verdict (its times jump the recording there), and the customer's timeline |
| **Inbound** | Phone-provider connection status + one-click connect; today / 7 / 30-day numbers (inbound calls, answered, missed, to call back, new and returning callers); live inbound calls; the inbound call list with a **To call back** tab, caller type, reason for calling and next step; click a call for its summary, a Call back button and the caller's timeline. A notice pops up when a call is missed |
| **Customer timeline** (lead page, call page, inbound drawer) | Every call in both directions grouped by day with IST time, outcome, talk time, summary and next step, plus "added to campaign" and "lead created"; the next promised step on top |
| **Live** | Ongoing calls (marked Inbound / Outbound, returning or new caller) with live transcript, levels, listen-in, hang-up |
| **Call review** | The **review agent** (a second AI): every few calls it reviews the finished calls and shows where and why customers were lost, with quotes and timings from the calls as proof, plus suggested script changes to **Approve**, **Edit & approve** or **Reject**. Also: every review with each call's verdict, decided suggestions, and how it evaluates. The sidebar badge counts suggestions waiting for you |
| **Activity log** | Every change anyone makes in the dashboard, newest first: **when** and **what**, with the old and new values; changes to Settings also show **who** made them. Search by person or change, filter by area (Settings, Leads, Campaigns, Calls, Inbound, Call review), **Download CSV**. Entries are kept forever and can't be edited or deleted |
| **Settings** | Voice, **tone & accent** (warm / professional / energetic / calm, Hinglish / mostly Hindi / mostly English), script, knowledge base, questions, objection handling, **learned rules**, interest rules, silence timings, fillers, pronunciation fixes, **calling switches + inbound greetings**, calling window, **review agent**, and **change history** (every change kept forever, with who made it, each with **Undo**). Saving, resetting and undoing ask for your name and the word **CONFIRM** first |

### Confirm popup and activity log
- **Type CONFIRM:** on the Settings page, **Save** (both buttons and Ctrl/Cmd + S), **Reset to defaults** and **Undo** open a popup that lists what is about to change. The button stays off until the person has given their name and typed `CONFIRM` (capitals not required). Cancel or Esc sends nothing.
- **The name, in Settings only:** changing a setting directly changes how the voice agent behaves on calls, so only those changes are made under a name. The dashboard has no user accounts: the popup asks for the name, remembers it on that computer (browser storage) to pre-fill the popup next time, and it can be corrected there every time. **No other page asks for a name or for CONFIRM**, and no other request carries one. It is a label, not a login: nothing checks that the name is true.
- **What is logged:** every successful change made through the dashboard: settings (save, reset, undo, approved review suggestion), leads (add, edit, board move, delete, bulk delete, CSV import), campaigns (create, start, pause, delete), calls (start, hang up, re-analyse, delete), inbound connect / disconnect, review runs and rejected suggestions. Settings saves, resets and undos carry the person's name; everything else is logged with what changed and when, without a name. Requests that fail or change nothing leave no entry. A deleted lead's or call's details stay readable in the log.
- **What is not:** what the system does by itself (dialing, call results, automatic reviews: the Calls and Call review pages are their record), previews and tests that change nothing, and sign-in.
- **Where:** table `activity_log` (append-only); settings changes also keep their full old → new values in `settings_changes`. Code: `backend/app/services/audit.py` (one middleware records every change, so the calling and campaign code is untouched; a route nobody described still gets a plain entry) and `routers/activity.py`; dashboard: `lib/actor.ts`, `components/ui/TypeConfirmDialog.tsx`, `components/activity/`.

### Two designs: Original and PouchWale
The switch in the top bar (**Original | PouchWale**; on phones and tablets a round switch with the PouchWale colour dot) changes only how the dashboard looks: same pages, data and buttons. The choice is remembered per browser (not a server setting) and applied before the page is drawn, so a reload never flashes the other design; other open tabs follow. Switching plays a **melt** (about 1.5 s): the design on screen melts away from the top like a thick layer, with molten drips and a soft shadow under its edge, throwing off sparks, ash and paper flakes and falling molten drops, and uncovers the other design underneath; it is skipped when the computer asks for reduced motion.
- **Original:** `frontend/src/app/globals.css`, unchanged.
- **PouchWale:** `frontend/src/app/pouchwale.css`, every rule scoped to `<html data-design="pouchwale">`. Calm for long sessions: charcoal-black sidebar, soft blue page, white cards with navy frames and soft grey shadows, deep PouchWale pink for the main action, Mada headings and Mulish text; every text colour passes WCAG AA. The animated blue logo stays as it is, and the theme's blue is that logo blue. Logic: `lib/design.ts` (applies the saved choice before the first paint) and `lib/design-switch.ts` (switch + melt); the switch is `components/shell/DesignToggle.tsx`. Chart colours in `lib/theme.ts` are CSS variables with the original colour as fallback.

### The review agent (Call review page; settings under Settings → Review agent)
1. **Which calls:** phone calls in which the customer spoke (outbound, plus inbound if switched on). Unanswered calls, voicemail and browser tests never count. Automatic reviews start every *N* new calls (default 5) and only look at calls made after the review agent was installed; **Run review now** takes the latest 5, 10 or 15 calls.
2. **Measure (no AI, free):** numbered transcript turns plus timing facts from the stereo recording: slow replies, monologues, talking over the customer, dead air, hanging up during an agent reply, customers asking to repeat, questions asked twice.
3. **Judge each call** (`review_model`, default gpt-4o-mini): outcome, whether / where / why the customer was lost, the agent's mistakes. **Proof check in code:** every quote must really be in the cited agent turn, every timing fact must exist; "lost because of the agent" needs a proven mistake at or before the moment the customer was lost. Normal reasons (not interested, wrong number, busy) never count against the agent.
4. **Patterns:** a problem becomes a suggestion only when it is proven in at least *N* calls (default 2), each piece of evidence backed by that call's verified finding. A suggestion is one small change to one script setting (opening / closing lines, call purpose, objections, knowledge base additions, questions, silence lines, or a **learned rule**), validated like the Settings page. It may never touch the voice, switches or timings, and never invents company facts (those become notes for the team).
5. **You decide:** only **Approve** writes a setting, recorded in **Settings → Change history** where it can be undone. Rejected changes are never suggested again. If the setting was edited after the suggestion, approval first shows what would be replaced and lets you edit (for added rules, starting from the current text with the new lines added).

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
- Automated tests: `cd backend; .\.venv\Scripts\python -m pytest` — simulated calls (greeting → conversation → end, barge-in, fillers, 10 s + 3 s silence hang-up, recording, scoring, picked-up-and-cut → cold), simulated Plivo and Vobiz media streams, browser calls, CSV import edge cases, board moves, analytics numbers, PDF/CSV reports, dialer concurrency and retries.

## Project layout
```
backend/app/
  main.py                    FastAPI app, startup (DB + migration, dialer, watchdog)
  services/session.py        real-time conversation engine (STT → LLM → TTS, barge-in, false starts, fillers, silence, spare TTS socket)
  services/sarvam.py         Saaras v3 realtime STT + Bulbul v3 streaming/REST TTS
  services/llm.py            GPT-4o-mini streaming, early first-clause chunking, prompt cache
  services/prompts.py        agent prompt (static part first for caching) + scoring prompt
  services/telephony.py      active phone provider (TELEPHONY_PROVIDER)   routers/telephony.py  webhooks + media stream (/plivo, /vobiz)
  services/plivo.py          Plivo REST                 services/vobiz.py  Vobiz REST + shared answer XML
  services/call_manager.py   call lifecycle             services/dialer.py  campaign auto-dialer
  services/preflight.py      checks before dialing: voice credit (after a failure), ngrok tunnel up (/plivo/ping)
  services/analysis.py       post-call scoring (cold/warm/hot, rating, objections)
  services/review/           review agent: evidence.py (recording + transcript facts, no AI), prompts.py (rubric use,
                             JSON formats), agent.py (runs, proof checks, suggestions, approval)   routers/review.py
  services/settings_rules.py settings validation (Settings page, review approvals)
  services/agent_config.py   settings + change history (kept forever) + undo
  services/audit.py          activity log: who changed what, for every change made in the dashboard   routers/activity.py
  services/reporting.py      date ranges + analytics    services/pdf_report.py  PDF report
  routers/leads.py           leads, CSV import, Kanban board
  routers/analytics.py       analytics + PDF/CSV report downloads
  assets/                    logo + fonts for the PDF (Inter, Hanken Grotesk, Noto Sans Devanagari; OFL)
frontend/src/                Next.js dashboard
frontend/prisma/             Prisma schema (same SQLite DB)
docs/                        API_CONTRACT.md, UAT_Test_Cases.xlsx
voice-samples/               Sarvam female voice samples
```
