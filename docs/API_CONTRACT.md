# Voice Agent — Backend API Contract

Backend: FastAPI at `http://localhost:8000` (env `NEXT_PUBLIC_API_URL`). All REST under `/api`.
WebSockets under `/ws`. Times are ISO-8601 UTC strings (`2026-11-02T10:15:30.123Z`). Display in IST (Asia/Kolkata).

## Shared types (TypeScript)

```ts
type Interest = "unknown" | "cold" | "warm" | "hot";        // "closed" was REMOVED
// Board column of a lead (derived): new = never called; no_answer = called but never reached / unscored;
// cold = picked up & cut, or not interested; warm = normal interest; hot = strong interest
type Stage = "new" | "no_answer" | "cold" | "warm" | "hot";
type LeadStatus = "new" | "queued" | "calling" | "called" | "failed" | "dnc";
type CallStatus = "queued" | "initiated" | "ringing" | "in_progress" | "completed"
                | "no_answer" | "busy" | "failed" | "canceled";
type EndReason = "agent_ended" | "user_hangup" | "silence_timeout" | "max_duration"
               | "manual_hangup" | "error" | "no_answer" | "busy" | null;
type Channel = "phone" | "browser";

interface Lead {
  id: number; name: string; phone: string;
  company: string | null; city: string | null; email: string | null;
  source: string | null; notes: string | null;
  status: LeadStatus; interest: Interest; stage: Stage; rating: number | null;   // rating 1..10
  attempts: number; last_called_at: string | null; last_summary: string | null;
  created_at: string; updated_at: string;
}

interface QualificationItem { question: string; answer: string | null; status: "answered" | "partial" | "not_asked"; }

interface CallListItem {
  uuid: string; lead_id: number | null; lead_name: string | null; lead_phone: string | null;
  lead_company: string | null; campaign_id: number | null;
  channel: Channel; status: CallStatus; end_reason: EndReason;
  started_at: string | null; answered_at: string | null; ended_at: string | null;
  duration_sec: number | null; rating: number | null; interest: Interest;
  qualified: boolean | null; summary: string | null; analysis_status: "pending" | "done" | "failed" | "skipped";
  has_recording: boolean; created_at: string;
}

interface TranscriptTurn {
  id: number; role: "agent" | "user" | "system"; text: string;
  offset_ms: number;            // ms since call start (use for timestamps + seek in recording)
  interrupted: boolean;         // agent turn cut off by customer barge-in
  latency_ms: number | null;    // agent response latency
  created_at: string;
}

interface CallDetail extends CallListItem {
  from_number: string | null; to_number: string | null;
  sentiment: "positive" | "neutral" | "negative" | null;
  next_action: string | null; callback_time: string | null;
  qualification: QualificationItem[] | null;
  key_points: string[] | null; objections: string[] | null;
  metrics: { avg_latency_ms?: number; turns?: number; user_words?: number; agent_words?: number } | null;
  error: string | null;
  recording_url: string | null;     // relative: "/api/calls/{uuid}/recording" -> prefix with API base
  transcript: TranscriptTurn[];
  lead: Lead | null;
}

interface Page<T> { items: T[]; total: number; page: number; page_size: number; }

interface Campaign {
  id: number; name: string; status: "draft" | "running" | "paused" | "completed";
  concurrency: number; max_attempts: number; retry_delay_min: number; respect_call_window: boolean;
  created_at: string; started_at: string | null; completed_at: string | null;
  counts: { total: number; pending: number; dialing: number; done: number; failed: number; skipped: number };
  interest_counts: { unknown: number; cold: number; warm: number; hot: number };
  active_calls: number;
}

interface AgentSettings {
  agent_name: string; company_name: string; agent_gender: "female" | "male";
  voice: string; language_code: string; tts_pace: number; tts_temperature: number;
  opening_line: string;            // supports {lead_name_ji} {lead_name} {agent_name} {company_name}
  call_purpose: string; knowledge_base: string;
  qualification_questions: string[];
  objection_handling: string; interest_rules: string; closing_line: string;
  silence_timeout_sec: number;     // default 10: ask "are you on the call?"
  silence_prompt_text: string;
  silence_final_wait_sec: number;  // default 3: then goodbye + hang up
  silence_goodbye_text: string;
  fillers_enabled: boolean; filler_words: string[]; filler_delay_ms: number;
  barge_in_enabled: boolean; max_call_sec: number;
  call_window_start: string; call_window_end: string;   // "HH:MM" IST, campaigns only
}

interface SystemInfo {           // GET /api/settings/system (read-only, from backend .env)
  llm_model: string; llm_max_tokens: number; llm_temperature: number;
  stt_model: string; stt_mode: string; tts_model: string;
  providers: { openai: boolean; sarvam: boolean; vobiz: boolean };
  public_base_url: string; public_url_ok: boolean;  // false if still localhost (Vobiz can't reach it)
}

interface Voice { id: string; name: string; gender: "female" | "male"; }
```

## Authentication (all `/api/*` except `/api/health` and `/api/auth/*`, all `/ws/*`)
- **Local use** (browser on the same computer → `localhost`): no login, everything works as before.
- **Remote use** (another PC, a server deployment): requests return **401** `{ detail }` until the user logs in.
  - `GET /api/auth/status` → `{ authenticated: boolean; local: boolean; password_set: boolean }`
    (`local` = this browser is exempt from login: direct use on the server's own computer, unless `REQUIRE_LOGIN_LOCAL=true`)
  - `POST /api/auth/login` `{ password }` → `{ ok: true }` and sets an HttpOnly session cookie (7 days). 401 wrong
    password, 429 too many attempts, 400 if no `DASHBOARD_PASSWORD` is configured on the server.
  - `POST /api/auth/logout` → clears the cookie.
  - All `fetch` calls must use `credentials: "include"` so the cookie is sent; WebSockets, `<audio src>` and
    download links carry it automatically.
- Requests whose browser `Origin` is not one of the configured dashboard origins get **403** (CSRF protection).
- Vobiz webhooks (`/vobiz/*`) use their own per-call tokens and are unaffected. The public ngrok tunnel only exposes `/vobiz/*`.

## REST endpoints

### Health / system
- `GET /api/health` → `{ status: "ok" }`
- `GET /api/settings/system` → `SystemInfo`

### Dashboard
- `GET /api/dashboard/stats?days=14` →
```ts
{
  totals: { leads: number; calls: number; calls_today: number; connected: number; connect_rate: number; // 0..1
            avg_rating: number | null; avg_duration_sec: number | null; qualified: number; active_calls: number };
  interest: { unknown: number; cold: number; warm: number; hot: number };   // over LEADS
  rating_histogram: { rating: number; count: number }[];   // ratings 1..10, always 10 entries
  calls_by_day: { date: string /* YYYY-MM-DD IST */; total: number; connected: number; hot: number }[];
  end_reasons: { reason: string; count: number }[];
  recent_calls: CallListItem[];   // last 8
  top_leads: Lead[];              // up to 8 hot leads by rating desc, then last_called_at desc
}
```

### Leads
- `GET /api/leads?search=&status=&interest=&stage=&page=1&page_size=50&sort=-created_at`  (`stage` filters one board column; use it for "Load more" in a column)
  sort ∈ `created_at, -created_at, rating, -rating, last_called_at, -last_called_at, name, -name`
  → `Page<Lead>`
- `POST /api/leads` body `{ name, phone, company?, city?, email?, source?, notes? }` → `Lead` (409 if phone exists)
- `GET /api/leads/{id}` → `Lead & { calls: CallListItem[] }`
- `PATCH /api/leads/{id}` partial `Lead` fields (name, phone, company, city, email, source, notes, status, interest, **stage**) → `Lead`
  Sending `{ "stage": "hot" }` is how a Kanban drag-and-drop moves a card (sales-team override; the next AI-scored call can move it again).
- `GET /api/leads/board?search=&limit=50` → `{ columns: { key: Stage; title: string; count: number; leads: Lead[] }[] }`
  Always 5 columns in order: new, no_answer, cold, warm, hot. `count` is the full column size; `leads` the first `limit`
  (hot/warm/cold sorted by rating desc then last_called_at desc; no_answer by last_called_at desc; new by created_at desc).
- `DELETE /api/leads/{id}` → `{ ok: true }`
- `POST /api/leads/bulk-delete` body `{ ids: number[] }` → `{ deleted: number }`
- `POST /api/leads/import` multipart `file` (.csv) → `{ imported: number; duplicates: number; invalid: number; errors: string[] }`
  Columns auto-detected (case-insensitive): name/full name/customer, phone/mobile/number/contact/phone number, company/business/brand, city/location, email, source, notes/remarks. Other columns kept in `extra`.
- `GET /api/leads/export?interest=hot&status=` → CSV file download
- `GET /api/leads/template` → sample CSV download

### Calls
- `POST /api/calls` body `{ lead_id: number }` → `CallListItem` (starts real phone call via Vobiz; 400 with `detail` if Vobiz not configured)
- `GET /api/calls?search=&status=&interest=&channel=&lead_id=&page=1&page_size=50` → `Page<CallListItem>` (newest first)
- `GET /api/calls/active` → `CallListItem[]` (calls in progress right now, incl. browser tests)
- `GET /api/calls/{uuid}` → `CallDetail`
- `POST /api/calls/{uuid}/hangup` → `{ ok: true }`
- `POST /api/calls/{uuid}/reanalyze` → `CallDetail` (re-runs summary/rating)
- `GET /api/calls/{uuid}/recording` → `audio/wav` (stereo: left = customer, right = agent). Supports Range requests.
- `DELETE /api/calls/{uuid}` → `{ ok: true }`

### Campaigns (bulk auto-dialer)
- `GET /api/campaigns` → `Campaign[]`
- `POST /api/campaigns` body `{ name: string; lead_ids?: number[]; filter?: { status?: LeadStatus; interest?: Interest; search?: string }; concurrency?: number; max_attempts?: number; retry_delay_min?: number; respect_call_window?: boolean }` → `Campaign`
  (if `lead_ids` given, uses them; else all leads matching `filter`; leads with status `dnc` are skipped)
- `GET /api/campaigns/{id}` → `Campaign & { items: { lead: Lead; status: string; attempts: number; last_call_uuid: string | null }[] }` (first 500 items)
- `POST /api/campaigns/{id}/start` → `Campaign`
- `POST /api/campaigns/{id}/pause` → `Campaign`
- `DELETE /api/campaigns/{id}` → `{ ok: true }`

### Settings
- `GET /api/settings` → `AgentSettings`
- `PUT /api/settings` body partial `AgentSettings` → `AgentSettings`
- `POST /api/settings/reset` → `AgentSettings` (back to defaults)
- `GET /api/settings/voices` → `Voice[]`
- `POST /api/settings/preview-voice` body `{ text: string; voice?: string; pace?: number }` → `audio/wav`
- `POST /api/settings/test-providers` → `Record<string, { ok: boolean; detail: string; latency_ms?: number }>` with keys
  `openai`, `sarvam_tts` (REST voice), `sarvam_tts_stream` (live streaming voice), `sarvam_stt` (realtime speech recognition), `vobiz`.
  Render every key returned (more may be added). Can take ~10 s.

## WebSockets

### `WS /ws/live` — live monitor feed (server → client JSON text frames)
```ts
type LiveEvent =
  | { type: "snapshot"; calls: CallListItem[] }                                   // sent on connect
  | { type: "call.update"; call: CallListItem }                                   // status changes, start, end, analysis done
  | { type: "call.state"; uuid: string; state: "connecting" | "listening" | "thinking" | "speaking" | "ended" }
  | { type: "transcript"; uuid: string; role: "agent" | "user"; text: string; final: boolean; offset_ms: number; interrupted?: boolean }
  | { type: "level"; uuid: string; user: number; agent: number }                 // 0..1, ~8/sec
  | { type: "campaign.update"; campaign: Campaign };
```
Client may send `{"type":"ping"}`; server replies `{"type":"pong"}`.

### `WS /ws/listen/{uuid}` — listen in to a live call (server → client)
Binary frames: byte 0 = track (0 = customer, 1 = agent), bytes 1.. = PCM16 little-endian mono.
First text frame: `{"type":"format","sample_rate":8000}`. Text frame `{"type":"clear"}` = agent audio was interrupted (stop queued agent audio).
Customer audio arrives in real time; agent audio arrives in bursts — schedule each track sequentially on its own playhead.

### `WS /ws/browser-call` — talk to the agent from the browser mic (test mode, no phone cost)
1. Client sends JSON text: `{"type":"start","lead_id"?: number,"name"?: string,"company"?: string}`.
2. Server replies `{"type":"ready","uuid": string,"input_sample_rate":16000,"output_sample_rate":24000}`.
3. Client streams mic audio as **binary** frames: PCM16 LE mono @ 16000 Hz (~20–100 ms per frame).
4. Server sends agent audio as **binary** frames: PCM16 LE mono @ `output_sample_rate`. Play sequentially.
5. Server JSON events: `{"type":"transcript",...}`, `{"type":"state",...}` (same shapes as /ws/live without uuid), `{"type":"clear"}` (stop all queued agent audio immediately — barge-in), `{"type":"ended","reason": EndReason}`, `{"type":"error","detail": string}`.
6. Client sends `{"type":"hangup"}` to end. After end, call detail is at `GET /api/calls/{uuid}` (analysis arrives a few seconds later).


## Analytics, reports (added)

All date params are IST calendar dates `YYYY-MM-DD`, inclusive. Default range = last 30 days. Analytics and reports count
**phone calls only**; pass `include_tests=true` to also include browser test calls.

### `GET /api/analytics?from=&to=&campaign_id=&include_tests=`
```ts
interface Analytics {
  range: { from: string; to: string; days: number };
  totals: {
    dialed: number; connected: number; connect_rate: number;      // 0..1 (connected / dialed)
    conversations: number;                                        // customer actually spoke
    talk_minutes: number; avg_duration_sec: number | null; avg_rating: number | null;
    hot: number; warm: number; cold: number; qualified: number;
    avg_latency_ms: number | null; p90_latency_ms: number | null; // customer stops talking -> agent voice starts
  };
  funnel: { stage: "dialed" | "connected" | "conversation" | "warm_or_hot" | "hot"; label: string; count: number }[];
  by_day: { date: string; dialed: number; connected: number; hot: number; warm: number; cold: number; avg_rating: number | null }[];
  by_hour: { hour: number; dialed: number; connected: number }[];          // 24 entries, IST hour 0..23
  interest: { unknown: number; cold: number; warm: number; hot: number };  // calls in range
  rating_histogram: { rating: number; count: number }[];                   // 10 entries
  duration_buckets: { label: string; count: number }[];                    // "<30s","30s–1m","1–2m","2–5m","5m+"
  end_reasons: { reason: string; count: number }[];
  sentiment: { positive: number; neutral: number; negative: number };
  objections: { tag: string; label: string; count: number }[];             // sorted desc; tags: price, moq, existing_supplier, timeline, not_interested, call_later, need_details, other
  qualification: { question: string; answered: number; partial: number; not_asked: number }[];
  campaigns: { id: number; name: string; dialed: number; connected: number; hot: number; warm: number; cold: number; avg_rating: number | null }[];
  latency_by_day: { date: string; avg_ms: number | null }[];
}
```

### Report downloads (plain GET links, `Content-Disposition: attachment` — use `<a href=... download>`)
- `GET /api/reports/summary.pdf?from=&to=&campaign_id=&include_transcripts=false&include_tests=` → PDF: KPIs, charts,
  hot / warm lead tables (name, phone, rating, next action), all calls table, optional full transcripts appendix.
- `GET /api/reports/calls.csv?from=&to=&campaign_id=&include_transcripts=true&include_tests=` → one row per call with every field
  (date/time IST, lead, phone, company, city, campaign, status, end reason, duration, interest, rating, qualified, sentiment,
  summary, next action, callback time, key points, objections, qualification Q&A, recording link, transcript).
- `GET /api/reports/leads.csv?from=&to=&stage=` → one row per lead (stage, interest, rating, attempts, last call, last summary…).
  With from/to: only leads called in that range.
