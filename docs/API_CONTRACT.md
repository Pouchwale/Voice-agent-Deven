# Voice Agent — Backend API Contract

Backend: FastAPI at `http://localhost:8000` (env `NEXT_PUBLIC_API_URL`). All REST under `/api`.
WebSockets under `/ws`. Times are ISO-8601 UTC strings (`2026-11-02T10:15:30.123Z`). Display in IST (Asia/Kolkata).
Every day boundary (date filters, "today", per-day rows) is an IST calendar day.

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
               | "manual_hangup" | "error" | "no_answer" | "busy"
               | "inbound_off"   // inbound call while inbound calling was off / the AI couldn't answer (missed)
               | "lines_busy"    // inbound call while every line was in use, or > 5 calls from the number in 10 min (missed)
               | "superseded"    // our outbound attempt was cancelled because the customer called us at that moment
               | "offline"       // inbound call that rang while the backend / tunnel was down (back-filled from Vobiz records)
               | null;
type Channel = "phone" | "browser";
type CallDirection = "inbound" | "outbound";
// Inbound calls only (null on outbound rows):
//   returning = the caller had a real conversation with us before (either direction)
//   known = the number was already a lead, but we never actually spoke
//   new = the lead was created by this call
//   international = foreign number while ALLOW_INTERNATIONAL is off (number kept on the call, no lead)
//   anonymous = hidden / unusable number (also: a call "from" our own number)
type CallerType = "returning" | "known" | "new" | "international" | "anonymous";
// Why an inbound caller called (post-call analysis; null on outbound calls)
type CallReason = "enquiry" | "callback_of_our_call" | "order_or_sample" | "complaint"
                | "vendor_or_job" | "wrong_number" | "other";
// Missed inbound calls only (null otherwise): see "Definitions"
type FollowUp = "pending" | "tried" | "done";
type CallOutcome = "answered" | "missed" | "live";

interface Lead {
  id: number; name: string; phone: string;
  company: string | null; city: string | null; email: string | null;
  source: string | null; notes: string | null;     // source "Inbound call" = created by an inbound call
  status: LeadStatus; interest: Interest; stage: Stage; rating: number | null;   // rating 1..10
  attempts: number;                // OUR dial attempts (inbound calls never change it)
  last_called_at: string | null;   // last contact in either direction (inbound calls set it, missed ones too)
  last_summary: string | null;
  created_at: string; updated_at: string;
}

interface QualificationItem { question: string; answer: string | null; status: "answered" | "partial" | "not_asked"; }

interface CallListItem {
  uuid: string; lead_id: number | null; lead_name: string | null;
  lead_phone: string | null;       // the lead's phone; without a lead: the CUSTOMER's number (never our own DID)
  lead_company: string | null; campaign_id: number | null;
  direction: CallDirection;
  caller_type: CallerType | null;  // inbound only
  from_number: string | null;      // the caller on inbound calls (E.164 when derivable, raw digits, or ""); our DID on outbound
  to_number: string | null;        // the dialed number on outbound calls; our DID on inbound
  channel: Channel; status: CallStatus; end_reason: EndReason;
  started_at: string | null;
  answered_at: string | null;      // inbound: set when the audio stream starts (the caller hears the agent)
  ended_at: string | null;
  duration_sec: number | null; rating: number | null; interest: Interest;
  qualified: boolean | null; summary: string | null;
  next_action: string | null; callback_time: string | null;
  call_reason: CallReason | null;  // = metrics.call_reason
  follow_up: FollowUp | null;      // filled by GET /api/calls, GET /api/leads/{id} and the timeline; null in live events
  analysis_status: "pending" | "done" | "failed" | "skipped";
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
  sentiment: "positive" | "neutral" | "negative" | null;
  qualification: QualificationItem[] | null;
  key_points: string[] | null; objections: string[] | null;
  metrics: {
    avg_latency_ms?: number; turns?: number; user_words?: number; agent_words?: number; agent_audio_ms?: number;
    objection_tags?: string[];
    call_reason?: CallReason | null;         // inbound calls
    promised_to_customer?: string | null;    // what WE promised them (e.g. "Expert calls tomorrow 11 am with a quote")
    customer_phone?: string | null;          // a different number / WhatsApp the customer gave on the call
  } | null;
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
  // Each number once, by its LATEST call (buckets add up to counts.total):
  outcomes: { waiting: number; ringing: number; talking: number; picked_up: number;
              no_answer: number; busy: number; failed: number; skipped: number };
  calls_placed: number;            // every dial attempt, retries included
}

interface AgentSettings {
  agent_name: string; company_name: string; agent_gender: "female" | "male";
  voice: string; language_code: string; tts_pace: number; tts_temperature: number;
  opening_line: string;            // supports {lead_name_ji} {lead_name} {agent_name} {company_name} {lead_company} {lead_city}
  call_purpose: string; knowledge_base: string;
  qualification_questions: string[];
  objection_handling: string; interest_rules: string; closing_line: string;
  silence_timeout_sec: number;     // default 10: ask "are you on the call?"
  silence_prompt_text: string;
  silence_final_wait_sec: number;  // default 3: then goodbye + hang up
  silence_goodbye_text: string;
  fillers_enabled: boolean; filler_words: string[]; filler_delay_ms: number;
  pronunciations: string[];        // "written = spoken" rules for the voice only, e.g. "PouchWale = पाउचवाले"
  barge_in_enabled: boolean; max_call_sec: number;
  call_window_start: string; call_window_end: string;   // "HH:MM" IST, campaigns only
  // ---- calling switches (both default true) ----
  outbound_enabled: boolean;       // off: "Call now" and campaigns place no calls (browser tests still work)
  inbound_enabled: boolean;        // off: callers get inbound_off_action, the call is logged as missed
  // ---- inbound calls ----
  inbound_greeting: string;        // everyone who never spoke with us ({lead_name_ji} = "Rahul जी" for a named lead, else "जी")
  inbound_greeting_known: string;  // ONLY callers who had a real conversation with us before
  inbound_purpose: string;         // the goal / call flow on inbound calls (like call_purpose for outbound)
  inbound_closing_line: string;    // said when an inbound call reaches inbound_max_call_sec
  inbound_max_call_sec: number;    // default 600 (60..3600)
  callback_greeting: string;       // OUR call back to a caller whose inbound call we missed (see "Callback mode")
  inbound_off_action: "message" | "reject";  // message = say inbound_unavailable_message, then hang up (answered, billed);
                                             // reject = busy tone (never answered, not billed)
  inbound_unavailable_message: string;       // company / agent placeholders only (no lead placeholders)
  // ---- tone & accent ----
  speaking_tone: "warm" | "professional" | "energetic" | "calm";
  language_mix: "hinglish" | "hindi" | "english";   // default style; the customer's own language still wins turn by turn
}

interface SystemInfo {           // GET /api/settings/system (read-only, from backend .env)
  llm_model: string; llm_max_tokens: number; llm_temperature: number;
  stt_model: string; stt_mode: string; tts_model: string;
  providers: { openai: boolean; sarvam: boolean; vobiz: boolean };
  public_base_url: string; public_url_ok: boolean;  // false if still localhost (Vobiz can't reach it)
}

interface Voice { id: string; name: string; gender: "female" | "male"; }
```

## Definitions (every screen, report and filter uses exactly these)

- **Phone call** = `channel` "phone". Browser test calls (`channel` "browser") never count in KPIs, analytics, reports'
  phone-only scopes or follow-ups, and never change lead fields. They are still listed (calls list, timeline).
- **Inbound outcome** (inbound summary, dashboard, analytics, timeline stats, calls `outcome` filter):
  `live` = status in queued / initiated / ringing / in_progress; `answered` = status `completed`;
  `missed` = ended with any other status (switched off, lines busy, offline, failed on our side, caller hung up
  before the agent spoke). Invariant: **calls = answered + missed + live**. (Outbound KPIs keep their meaning:
  *connected / picked up* = `answered_at` set.)
- **Conversation** = a phone call with status `completed` AND `metrics.user_words > 0` (either direction).
  **Analysed conversation** = a conversation whose `analysis_status` is `done`.
- **caller_type**: see `CallerType` above (decided when the call arrives; "returning" needs an earlier conversation).
- **Customer number** = `from_number` on inbound calls, `to_number` on outbound calls (our own DID is never shown or
  searched as a customer's number).
- **Calls of a lead** (lead page, timeline, follow-ups): every call with `lead_id` = the lead, PLUS inbound calls with
  no lead whose `from_number` = the lead's phone (a caller who rang before the lead existed, or while it was on another
  live call). Outbound rows that lost their lead are never matched by number (deleted lead / recycled number).
- **follow_up** (missed inbound phone calls from a callable number, i.e. not `anonymous` and a lead or a `+…` number;
  null for every other call): `done` = a LATER conversation exists with that customer; `tried` = a later outbound call
  exists (not a `superseded` one) but no conversation yet; `pending` = neither. **"To call back" = follow_up pending.**
  "That customer" = the same lead, or an inbound call from the same number, or, for a caller who had no lead, the
  lead that now has that number. A browser test never clears a missed call.

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
- Vobiz webhooks (`/vobiz/*`) use their own tokens (per call, or the static inbound secret) and are unaffected.
  The public ngrok tunnel only exposes `/vobiz/*`.

## REST endpoints

### Health / system
- `GET /api/health` → `{ status: "ok" }`
- `GET /api/settings/system` → `SystemInfo`

### Dashboard
- `GET /api/dashboard/stats?days=14` →
```ts
{
  totals: {
    leads: number;
    calls: number;            // OUTBOUND phone calls (all time)
    calls_today: number;      // OUTBOUND phone calls today (IST)
    connected: number;        // OUTBOUND phone calls picked up (answered_at set)
    connect_rate: number;     // 0..1 = connected / finished OUTBOUND calls
    avg_rating: number | null; avg_duration_sec: number | null; qualified: number;   // ALL phone calls
    active_calls: number;     // live right now (incl. browser tests)
    inbound_today: number;        // inbound phone calls today (IST), live ones included
    inbound_missed_today: number; // of those, missed (ended, not "completed")
  };
  interest: { unknown: number; cold: number; warm: number; hot: number };   // over LEADS
  rating_histogram: { rating: number; count: number }[];   // ratings 1..10, always 10 entries (ALL phone calls)
  // total/connected/hot = OUTBOUND phone calls (dialed / picked up / hot); inbound = inbound phone calls that day.
  // The last entry is today: calls_by_day[-1].total == totals.calls_today, calls_by_day[-1].inbound == totals.inbound_today
  calls_by_day: { date: string /* YYYY-MM-DD IST */; total: number; connected: number; hot: number; inbound: number }[];
  end_reasons: { reason: string; count: number }[];   // ALL ended phone calls; raw EndReason codes ("unknown" for null)
  recent_calls: CallListItem[];   // last 8 (follow_up null)
  top_leads: Lead[];              // up to 8 hot leads by rating desc, then last_called_at desc
}
```

### Leads
- `GET /api/leads?search=&status=&interest=&stage=&page=1&page_size=50&sort=-created_at`  (`stage` filters one board column; use it for "Load more" in a column)
  sort ∈ `created_at, -created_at, rating, -rating, last_called_at, -last_called_at, name, -name`
  → `Page<Lead>`
- `POST /api/leads` body `{ name, phone, company?, city?, email?, source?, notes? }` → `Lead` (409 if phone exists)
- `GET /api/leads/{id}` → `Lead & { calls: CallListItem[] }` — the last 50 **calls of the lead** (see Definitions:
  inbound calls from its number without a lead are included), newest first, with `follow_up` filled.
- `GET /api/leads/{id}/timeline` → `LeadTimeline` (404 `Lead not found` for an unknown lead):
```ts
interface TimelineCallEvent {
  id: string;            // "call-<uuid>"
  kind: "call";
  at: string;            // the call's started_at (created_at when missing)
  call: CallListItem;    // with follow_up for missed inbound calls
  next_action: string | null; callback_time: string | null;
  key_points: string[];  // at most 5
}
interface TimelineCampaignEvent {
  id: string;            // "campaign-<campaign id>" (one per campaign the lead is in)
  kind: "campaign_added";
  at: string;            // max(campaign.created_at, lead.created_at): a CSV campaign is saved a moment before the
                         // leads it creates, and "added" must never read as happening before "created"
  campaign_id: number; campaign_name: string; campaign_status: "draft" | "running" | "paused" | "completed";
}
interface TimelineLeadEvent { id: string /* "lead-<id>" */; kind: "lead_created"; at: string; source: string | null; }
interface LeadTimeline {
  lead: Lead;
  // Every call of the lead (both directions; browser tests included, channel "browser"), its campaigns and its
  // creation. Newest first; ties at the same instant: call > campaign_added > lead_created, then the newer row first.
  // At most 200 events, but lead_created is ALWAYS kept (it replaces the 200th when it would fall off).
  events: (TimelineCallEvent | TimelineCampaignEvent | TimelineLeadEvent)[];
  stats: {               // SQL totals over ALL the lead's PHONE calls (not just the 200 listed)
    calls: number; inbound: number; outbound: number;
    answered: number;    // status "completed" (the shared outcome predicate)
    talk_sec: number;    // sum of duration_sec of answered calls (whole seconds)
    first_contact_at: string | null; last_contact_at: string | null;   // earliest / latest phone call (any outcome)
  };
  // The newest ANALYSED CONVERSATION (phone) that left a next action or a callback time; null if none.
  next_step: { next_action: string | null; callback_time: string | null; call_uuid: string; at: string } | null;
}
```
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
- Leads created by inbound calls: `name ""`, `phone` = the caller's E.164 (Indian landlines included), `source "Inbound call"`,
  `status "new"`, `interest "unknown"`, `attempts 0`. A real-looking caller-ID name is kept in `extra.caller_id_name`
  (for the team, never spoken). After a phone conversation the analysis fills ONLY empty `name` / `company` / `city`
  (never what the team typed; never from browser tests).

### Calls
- `POST /api/calls` body `{ lead_id: number }` → `CallListItem` (starts a real phone call via Vobiz).
  400 with `detail` if Vobiz/AI is not configured, or **"Outbound calling is turned off in Settings."** when
  `outbound_enabled` is false; 404 unknown lead; 409 Do-Not-Call lead or a lead already on a live call (either direction).
  *Callback mode*: when the lead's latest inbound call in the last 7 days was missed and nobody has spoken with them
  since, the agent opens with `callback_greeting` and follows `inbound_purpose` (direction stays "outbound"). Same for
  campaign dials.
- `GET /api/calls` → `Page<CallListItem>` (newest first; `follow_up` filled for missed inbound calls on the page). Query:
  | param | meaning |
  |---|---|
  | `search` | text match on lead name / phone / company and the call summary (unchanged). When the term has **≥ 4 digits** it also matches the **customer number** by digits (inbound `from_number`, outbound `to_number`) and the lead's phone; a typed trunk `0` or `00` prefix is ignored (`0261 2345678`, `98765 43210`, `+91 98765-43210` all work). Our own DID never matches. |
  | `status`, `interest` | comma lists (`status=no_answer,busy`) |
  | `channel` | `phone` \| `browser` (the Inbound page should pass `phone`: browser rehearsals never count in its KPIs) |
  | `direction` | `inbound` \| `outbound` (rows from before inbound calling are outbound) |
  | `caller_type` | comma list of `CallerType` |
  | `outcome` | comma list of `answered` \| `missed` \| `live` (the shared predicate, any direction) |
  | `follow_up` | `pending` = the "To call back" list (missed inbound calls nobody has spoken with or tried since) |
  | `from`, `to` | IST dates `YYYY-MM-DD`, inclusive, on `created_at`; either may be omitted |
  | `lead_id`, `campaign_id`, `page` (≥ 1), `page_size` (1..200) | as before |
  Invalid `direction` / `caller_type` / `outcome` / `follow_up` values, a bad date or `from` after `to` → **422**.
- `GET /api/calls/active` → `CallListItem[]` (calls in progress right now, both directions, incl. browser tests)
- `GET /api/calls/{uuid}` → `CallDetail`
- `POST /api/calls/{uuid}/hangup` → `{ ok: true }`
- `POST /api/calls/{uuid}/reanalyze` → `CallDetail` (re-runs summary/rating; a missed inbound call keeps its summary)
- `GET /api/calls/{uuid}/recording` → `audio/wav` (stereo: left = customer, right = agent). Supports Range requests.
- `DELETE /api/calls/{uuid}` → `{ ok: true }`

### Inbound calls
- `GET /api/inbound/summary?from=&to=` → `InboundSummary`. IST dates, inclusive, default **today..today**; `from` after
  `to`, a range longer than 366 days or a bad date → **422**. Counts inbound **phone** calls by `created_at`.
```ts
interface InboundSummary {
  from: string; to: string;          // YYYY-MM-DD (IST)
  totals: {
    calls: number;                   // per call; calls = answered + missed + live (in the range)
    answered: number;                // status "completed"
    missed: number;                  // ended any other way
    to_call_back: number;            // calls in the range whose follow_up is "pending" (later calls count, even after `to`)
    new_callers: number;             // DISTINCT callers (lead, else number) with a caller_type "new" call in the range
    returning_callers: number;       // DISTINCT callers with a caller_type "returning" call in the range
    anonymous: number;               // calls from hidden / unusable numbers
    avg_duration_sec: number | null; // answered calls only
    hot: number; warm: number; cold: number;   // call.interest of the inbound calls
  };
  by_day: { date: string; calls: number; answered: number; missed: number }[];   // one row per IST day, zeros included
  live: number;                      // inbound phone calls in progress right now (not limited to the range)
}
```
- `GET /api/inbound/setup?check=true` → `InboundSetup` (the Vobiz part is cached ~60 s server-side; `check=false` never
  queries Vobiz: it returns the last cached Vobiz state, or null Vobiz fields with `checked=false` until one check has
  run since the backend started — treat that as "unknown", not "not connected"; use it for any periodic refresh).
  Never 500 on Vobiz errors: they are listed in `problems`, whose last item is always the
  "answers only while this computer, the backend and the tunnel are running" note.
```ts
interface InboundSetup {
  enabled: boolean;               // inbound_enabled setting
  number: string | null;          // our Vobiz DID (E.164)
  answer_url: string; hangup_url: string;   // paste into a Vobiz application: they contain a SECRET token
  public_url_ok: boolean;         // PUBLIC_BASE_URL is https
  vobiz_ready: boolean;           // Vobiz keys + number configured
  ai_ready: boolean;              // OpenAI + Sarvam keys present (otherwise callers get the unavailable response)
  checked: boolean;               // Vobiz was queried
  app_id: string | null; app_name: string | null;   // our application "GP3-Voice-Agent-Inbound"
  app_urls_match: boolean | null; // its answer + hangup URLs equal ours (false after the tunnel URL changed)
  number_linked: boolean | null;  // our DID is attached to OUR application
  number_app_id: string | null; number_app_name: string | null;   // where calls to the DID go right now
  number_on_trunk: boolean | null; number_voice_enabled: boolean | null; number_status: string | null;
  previous_app_id: string | null; // the application the DID was on before we connected (Disconnect restores it)
  concurrency_max: number | null; // Vobiz account concurrent-call limit
  last_inbound_at: string | null; // newest inbound call we received
  rejected_requests: number;      // inbound webhook calls with a wrong/old secret since startup
  last_rejected_at: string | null;
  problems: string[];             // plain English, e.g. "AI keys missing: callers are being turned away"
  ready: boolean;                 // enabled && public_url_ok && vobiz_ready && ai_ready && app_urls_match && number_linked
}
```
- `POST /api/inbound/setup?force=false` → `InboundSetup` (fresh check). Creates or updates our Vobiz application
  (answer/hangup URLs, both methods POST) and attaches our DID to it. 400 when `PUBLIC_BASE_URL` is not https or Vobiz
  isn't configured; **409** with a detail naming the current routing ("Calls to +91… currently go to 'X'. Connecting
  moves them to the AI agent.") when the DID is attached to another application or a SIP trunk, unless `force=true`.
  The previous application id is remembered for Disconnect.
- `DELETE /api/inbound/setup` → `InboundSetup`: re-attaches the previous application when known, else detaches the DID.

### Campaigns (bulk auto-dialer)
- `GET /api/campaigns` → `Campaign[]`
- `POST /api/campaigns` body `{ name: string; lead_ids?: number[]; filter?: { status?: LeadStatus; interest?: Interest; search?: string }; concurrency?: number; max_attempts?: number; retry_delay_min?: number; respect_call_window?: boolean }` → `Campaign`
  (if `lead_ids` given, uses them; else all leads matching `filter`; leads with status `dnc` are skipped)
- `GET /api/campaigns/template` → sample CSV download (same columns as the lead import)
- `POST /api/campaigns/csv/preview` multipart `file` → read-only check, nothing is saved:
  `{ summary: { rows; unique; callable; new; existing; duplicate; invalid; dnc }; rows: { line: number; name: string; raw_phone: string; phone: string | null; company: string | null; city: string | null; status: "new" | "existing" | "duplicate" | "invalid" | "dnc"; problem: string | null; duplicate_of: number | null; lead_id: number | null }[]; truncated: boolean }`
  (first 5,000 rows listed; the summary covers the whole file. `line` = spreadsheet row, header = 1. Duplicates are matched after
  normalising, so `9876543210` and `+91 98765 43210` are the same number. Fully blank rows are ignored.)
- `POST /api/campaigns/csv` multipart `file` + form fields `name`, `concurrency`, `max_attempts`, `retry_delay_min`, `respect_call_window`
  → `Campaign & { upload: <summary as above> }` (201). New numbers become leads (source "Campaign: <name>"), numbers that are already
  leads are reused (only their blank name/company/city/email are filled in), duplicates / invalid / Do-Not-Call are left out,
  dialing follows file order. 400 when no callable number is left; nothing is saved then.
- `GET /api/campaigns/{id}?result=&page=1&page_size=500` → `Campaign & { items: CampaignItem[]; items_total: number; page: number; page_size: number }`
  where `CampaignItem = { lead: Lead; status: "pending" | "dialing" | "done" | "failed" | "skipped"; attempts: number; last_call_uuid: string | null;
  next_attempt_at: string | null; result: keyof Campaign["outcomes"]; last_call: { status: CallStatus; end_reason: EndReason; started_at; answered_at; ended_at; duration_sec } | null }`.
  `result` filter: `waiting | ongoing (= ringing + talking) | ringing | talking | picked_up | no_answer | busy | failed | skipped`; items in dial order.
- `POST /api/campaigns/{id}/start` → `Campaign` (400 "Outbound calling is turned off in Settings." while outbound is off)
- `POST /api/campaigns/{id}/pause` → `Campaign`
- `DELETE /api/campaigns/{id}` → `{ ok: true }`
- Dialer rules with inbound calling:
  - Outbound off: running campaigns stay `running` but place no calls; they resume by themselves when it is switched
    back on (an item caught mid-dial is re-queued without using an attempt; the campaign is never paused for it).
  - While inbound is on, the dialer leaves 2 lines free for callers (budget = 10 − 2 − active phone calls).
  - A number whose lead is on a call in either direction, or called us in the last 30 minutes, waits (no attempt used);
    a "lead already has a call in progress" race re-queues it (never "skipped").
  - `superseded` (the customer called us while we dialed them): the item goes back to pending, the attempt is refunded,
    and it never counts as a provider error (never pauses the campaign).
  - Courtesy: an inbound call that ends with a conversation settles that lead's PENDING items in running or paused
    campaigns as done (`last_call_uuid` = the inbound call, so the number shows as `picked_up` — "Answered, or called
    us"). Draft and completed campaigns are left alone.

### Settings
- `GET /api/settings` → `AgentSettings`
- `PUT /api/settings` body partial `AgentSettings` → `AgentSettings`. 422 on invalid values: enums (`speaking_tone`,
  `language_mix`, `inbound_off_action`, `agent_gender`, `language_code`, `voice`), numbers out of range
  (`inbound_max_call_sec` 60..3600, `max_call_sec` 30..1800, …), and the spoken lines `inbound_greeting`,
  `inbound_greeting_known`, `inbound_unavailable_message`, `callback_greeting`, `inbound_closing_line`
  (empty, or longer than 600 characters). Saving re-builds the cached inbound audio in the background.
- `POST /api/settings/reset` → `AgentSettings` (back to defaults)
- `GET /api/settings/voices` → `Voice[]` where `Voice = { id: string; name: string; gender: "female" | "male"; model: "bulbul:v3" | "bulbul:v4-flash" }`.
  v4 ids are persona ids (`aparna_hi_customer`); the backend picks the TTS model from the voice id and sends no temperature for v4.
- `POST /api/settings/preview-voice` body `{ text: string; voice?: string; pace?: number; temperature?: number; language_code?: string }`
  → `audio/wav`. `temperature` / `language_code` are UNSAVED form values (e.g. a tone preset) so the preview plays exactly
  what was just picked.
- `POST /api/settings/test-providers` → `Record<string, { ok: boolean; detail: string; latency_ms?: number }>` with keys
  `openai`, `sarvam_tts` (REST voice), `sarvam_tts_stream` (live streaming voice), `sarvam_stt` (realtime speech recognition), `vobiz`.
  Render every key returned (more may be added). Can take ~10 s.

## Vobiz inbound webhooks (not under `/api`; for the Vobiz application only)

Static secret: `{token}` = HMAC of `INBOUND_TOKEN_SALT` (default `inbound-v1`) with `VOBIZ_AUTH_TOKEN`. A wrong token →
**403** (counted in `InboundSetup.rejected_requests`); with no `VOBIZ_AUTH_TOKEN` every request is refused. To revoke a
leaked URL, change `INBOUND_TOKEN_SALT` and click "Connect inbound on Vobiz" again.
- `POST /vobiz/inbound/{token}/answer` (also `GET` with query params; form-encoded or JSON body with `CallUUID`, `From`,
  `To`, `Direction`, `CallStatus`, `Event`, `HangupCause`, `HangupCauseCode`, `CallerName`) → VobizXML, always **200**
  and valid XML (never a 500): the bidirectional `<Stream>` (the AI answers), `<Hangup reason="busy"/>` (reject, not
  billed), or `<Play>`/`<Speak>` of the unavailable message then `<Hangup/>`. A hangup posted to this URL (Vobiz does
  that when the application has no hangup URL) is handled as a hangup and creates nothing. One row per `CallUUID`:
  retries replay the first decision. `Direction` other than inbound or a missing `CallUUID` → 400.
- `POST /vobiz/inbound/{token}/hangup` → `"ok"` immediately (processed in the background). A call that ends before its
  audio stream starts is missed: `no_answer` / `user_hangup` ("Caller hung up before the agent answered"), or `failed` /
  `error` for our-side causes 4010 / 7011 / 8011 ("Call failed on our side — call back").
- `GET /vobiz/inbound/{token}/audio/{key}.wav` → `audio/wav` (8 kHz mono PCM16, `Content-Length`) of a cached
  unavailable-message clip; 404 for keys never generated.
- Inbound rows: `direction "inbound"`, `channel "phone"`, `provider_call_id` = Vobiz CallUUID, `from_number` = the
  caller, `to_number` = our DID, `caller_type` set; `answered_at` is set when the audio stream starts. Missed rows
  carry a plain summary for the team ("Missed inbound call — inbound calling was switched off", "… all lines were busy",
  "Missed while the agent was offline — call back", …) and `analysis_status "skipped"`. Calls that rang while the
  backend or tunnel was down are back-filled from Vobiz call records at startup and every 10 minutes (`end_reason
  "offline"`; env `INBOUND_CDR_SYNC=false` turns this off).

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
Client may send `{"type":"ping"}`; server replies `{"type":"pong"}`. Calls in events carry `direction`, `caller_type`,
`from_number`, `to_number` and `call_reason` (`follow_up` is always null in events: refetch the list for it). An inbound
call appears as soon as it is answered (status `in_progress`), and again when it ends (missed or answered).

### `WS /ws/listen/{uuid}` — listen in to a live call (server → client)
Binary frames: byte 0 = track (0 = customer, 1 = agent), bytes 1.. = PCM16 little-endian mono.
First text frame: `{"type":"format","sample_rate":8000}`. Text frame `{"type":"clear"}` = agent audio was interrupted (stop queued agent audio).
Customer audio arrives in real time; agent audio arrives in bursts — schedule each track sequentially on its own playhead.

### `WS /ws/browser-call` — talk to the agent from the browser mic (test mode, no phone cost)
1. Client sends JSON text: `{"type":"start","lead_id"?: number,"name"?: string,"company"?: string,"direction"?: "inbound" | "outbound"}`.
   `direction: "inbound"` rehearses a customer calling US: inbound greeting (returning / first-caller rules), inbound
   purpose and caller memory for the chosen lead (or a blank caller). The row is stored with `direction "inbound"` and
   `channel "browser"` (never counted in KPIs). A browser test never blocks (or is blocked by) a real phone call.
2. Server replies `{"type":"ready","uuid": string,"input_sample_rate":16000,"output_sample_rate":24000}`.
3. Client streams mic audio as **binary** frames: PCM16 LE mono @ 16000 Hz (~20–100 ms per frame).
4. Server sends agent audio as **binary** frames: PCM16 LE mono @ `output_sample_rate`. Play sequentially.
5. Server JSON events: `{"type":"transcript",...}`, `{"type":"state",...}` (same shapes as /ws/live without uuid), `{"type":"clear"}` (stop all queued agent audio immediately — barge-in), `{"type":"ended","reason": EndReason}`, `{"type":"error","detail": string}`.
6. Client sends `{"type":"hangup"}` to end. After end, call detail is at `GET /api/calls/{uuid}` (analysis arrives a few seconds later).


## Analytics, reports

All date params are IST calendar dates `YYYY-MM-DD`, inclusive. Default range = last 30 days (a reversed range is
swapped; more than a year → 422). Analytics and reports count **ended phone calls** only; pass `include_tests=true`
to also include browser test calls.

**Metric scopes.** OUTBOUND (direction not inbound): dialed, connected, connect_rate, all 5 funnel stages,
by_day.dialed/connected, by_hour, campaigns — an inbound call was never dialed and never changes them. ALL calls in
scope (both directions): conversations, talk_minutes, avg_duration_sec, avg_rating, hot/warm/cold, qualified,
interest, rating_histogram, duration_buckets, end_reasons, sentiment, objections, qualification, latency,
by_day.hot/warm/cold/avg_rating. INBOUND: totals.inbound / inbound_answered / inbound_missed, by_day.inbound.

### `GET /api/analytics?from=&to=&campaign_id=&include_tests=`
```ts
interface Analytics {
  range: { from: string; to: string; days: number };
  totals: {
    dialed: number; connected: number; connect_rate: number;      // OUTBOUND; 0..1 (connected / dialed)
    conversations: number;                                        // customer actually spoke (status completed, user_words > 0)
    talk_minutes: number; avg_duration_sec: number | null; avg_rating: number | null;
    hot: number; warm: number; cold: number; qualified: number;
    avg_latency_ms: number | null; p90_latency_ms: number | null; // customer stops talking -> agent voice starts
    inbound: number; inbound_answered: number; inbound_missed: number;   // inbound = answered + missed (ended calls only)
  };
  // OUTBOUND, nested (never widens): dialed ⊇ picked up ⊇ had a conversation ⊇ warm or hot ⊇ hot
  funnel: { stage: "dialed" | "connected" | "conversation" | "warm_or_hot" | "hot"; label: string; count: number }[];
  by_day: { date: string; dialed: number; connected: number; hot: number; warm: number; cold: number; avg_rating: number | null;
            inbound: number }[];
  by_hour: { hour: number; dialed: number; connected: number }[];          // 24 entries, IST hour 0..23, OUTBOUND
  interest: { unknown: number; cold: number; warm: number; hot: number };  // calls in range
  rating_histogram: { rating: number; count: number }[];                   // 10 entries
  duration_buckets: { label: string; count: number }[];                    // "<30s","30s–1m","1–2m","2–5m","5m+"
  end_reasons: { reason: string; count: number }[];                        // human labels, e.g. "Missed (lines busy)"
  sentiment: { positive: number; neutral: number; negative: number };
  objections: { tag: string; label: string; count: number }[];             // sorted desc; tags: price, moq, existing_supplier, timeline, not_interested, call_later, need_details, other
  qualification: { question: string; answered: number; partial: number; not_asked: number }[];
  campaigns: { id: number; name: string; dialed: number; connected: number; hot: number; warm: number; cold: number; avg_rating: number | null }[];
  latency_by_day: { date: string; avg_ms: number | null }[];
}
```
End-reason labels: agent_ended "Agent ended", user_hangup "Customer hung up", silence_timeout "No response (silence)",
max_duration "Max length reached", manual_hangup "Hung up from dashboard", error "Error", no_answer "No answer",
busy "Busy", inbound_off "Missed (inbound off)", lines_busy "Missed (lines busy)", superseded "Cancelled (customer
called in)", offline "Missed (agent offline)", none "Unknown".

### Report downloads (plain GET links, `Content-Disposition: attachment` — use `<a href=... download>`)
- `GET /api/reports/summary.pdf?from=&to=&campaign_id=&include_transcripts=false&include_tests=` → PDF: KPIs (Dialed,
  Picked up, **Inbound calls** with the missed count, Conversations, Avg rating, Avg response, Hot / Warm / Cold),
  funnel (outbound), calls per day (dialed vs picked up, plus inbound), interest and end-reason bars (percent of ALL
  calls in the range, so never above 100%), best time to call (outbound), hot / warm lead tables (name, phone, rating,
  next action, **Last call**), all calls table with an **In/Out** column and the customer number (hidden callers print
  "Hidden number"; our own DID is never printed), optional transcripts appendix. Builds for inbound-only ranges too.
- `GET /api/reports/calls.csv?from=&to=&campaign_id=&include_transcripts=true&include_tests=` → one row per call with every field
  (call_id, date/time IST, **direction**, **caller_type**, lead name, phone (the lead's, else the customer number),
  **customer_number** (the number on this call: caller on inbound, dialed on outbound), company, city, campaign,
  channel, status, end reason, duration, interest, rating, qualified, sentiment, **call_reason**, summary, next action,
  callback time, key points, objections, objection tags, qualification Q&A, avg response ms, recording link,
  transcript). Cells starting with `+ = - @` are prefixed with `'` (Excel-safe; +91 numbers stay text).
- `GET /api/reports/leads.csv?from=&to=&stage=` → one row per lead (stage, interest, rating, attempts, last call, last summary…).
  With from/to: only leads whose last contact (`last_called_at`: our calls and inbound calls, missed ones included) is in that range.

## Configuration (backend `.env`) relevant to inbound calling
- `INBOUND_TOKEN_SALT` (default `inbound-v1`): part of the inbound webhook secret; change it to revoke a leaked URL.
- `INBOUND_CDR_SYNC` (default true): back-fill inbound calls missed while offline from Vobiz call records.
- `ALLOW_INTERNATIONAL` (default false): foreign callers are logged (`caller_type "international"`) but get no lead.
- `PUBLIC_BASE_URL` must be https for inbound calls to be answered by the AI; the agent answers only while this
  computer, the backend and the tunnel are running.
