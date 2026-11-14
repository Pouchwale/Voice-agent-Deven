/**
 * Types copied from docs/API_CONTRACT.md (source of truth).
 * Keep the "Shared types" block in sync with the contract; extra request/response
 * shapes for individual endpoints follow below it.
 */

// ---------------------------------------------------------------------------
// Shared types (verbatim from the contract)
// ---------------------------------------------------------------------------

export type Interest = "unknown" | "cold" | "warm" | "hot"; // "closed" was removed
/** Board column of a lead (derived; the sales team can override it by dragging a card). */
export type Stage = "new" | "no_answer" | "cold" | "warm" | "hot";
export type LeadStatus = "new" | "queued" | "calling" | "called" | "failed" | "dnc";
export type CallStatus =
  | "queued"
  | "initiated"
  | "ringing"
  | "in_progress"
  | "completed"
  | "no_answer"
  | "busy"
  | "failed"
  | "canceled";
export type EndReason =
  | "agent_ended"
  | "user_hangup"
  | "silence_timeout"
  | "max_duration"
  | "manual_hangup"
  | "error"
  | "no_answer"
  | "busy"
  | "inbound_off" // inbound call arrived while inbound calling was switched off (missed)
  | "lines_busy" // inbound call arrived while every line was in use (missed)
  | "superseded" // outbound attempt cancelled because the customer called in at the same moment
  | "offline" // inbound call that rang while the backend / tunnel was offline (back-filled from Vobiz records)
  | null;
export type Channel = "phone" | "browser";
export type CallDirection = "inbound" | "outbound";
/**
 * Inbound only.
 * returning = the caller had a real conversation with us before (either direction);
 * known = the number was already a lead, but we never actually spoke;
 * new = lead created by this call; international = foreign number (no lead while ALLOW_INTERNATIONAL is off);
 * anonymous = hidden / unusable number.
 */
export type CallerType = "returning" | "known" | "new" | "international" | "anonymous";
/** Why an inbound caller called (post-call analysis). */
export type CallReason =
  | "enquiry"
  | "callback_of_our_call"
  | "order_or_sample"
  | "complaint"
  | "vendor_or_job"
  | "wrong_number"
  | "other";
/** Missed inbound calls only: pending = nobody has spoken with them since; tried = we called, no conversation yet; done = spoke since. */
export type FollowUp = "pending" | "tried" | "done";
/** Inbound outcome predicate used everywhere: answered = status "completed", missed = ended otherwise, live = active. */
export type CallOutcome = "answered" | "missed" | "live";

export interface Lead {
  id: number;
  name: string;
  phone: string;
  company: string | null;
  city: string | null;
  email: string | null;
  source: string | null;
  notes: string | null;
  status: LeadStatus;
  interest: Interest;
  stage: Stage;
  rating: number | null; // rating 1..10
  attempts: number;
  last_called_at: string | null;
  last_summary: string | null;
  created_at: string;
  updated_at: string;
}

export interface QualificationItem {
  question: string;
  answer: string | null;
  status: "answered" | "partial" | "not_asked";
}

export type AnalysisStatus = "pending" | "done" | "failed" | "skipped";

export interface CallListItem {
  uuid: string;
  lead_id: number | null;
  lead_name: string | null;
  lead_phone: string | null;
  lead_company: string | null;
  campaign_id: number | null;
  direction: CallDirection;
  caller_type: CallerType | null;
  /** Caller's number on inbound calls (our number on outbound). */
  from_number: string | null;
  /** Dialed number on outbound calls (our number on inbound). */
  to_number: string | null;
  channel: Channel;
  status: CallStatus;
  end_reason: EndReason;
  started_at: string | null;
  answered_at: string | null;
  ended_at: string | null;
  duration_sec: number | null;
  rating: number | null;
  interest: Interest;
  qualified: boolean | null;
  summary: string | null;
  next_action: string | null;
  callback_time: string | null;
  call_reason: CallReason | null;
  /** Filled by GET /api/calls and the timeline for missed inbound calls; null elsewhere (e.g. live events). */
  follow_up: FollowUp | null;
  analysis_status: AnalysisStatus;
  has_recording: boolean;
  created_at: string;
}

export interface TranscriptTurn {
  id: number;
  role: "agent" | "user" | "system";
  text: string;
  offset_ms: number; // ms since call start (use for timestamps + seek in recording)
  interrupted: boolean; // agent turn cut off by customer barge-in
  latency_ms: number | null; // agent response latency
  created_at: string;
}

export type Sentiment = "positive" | "neutral" | "negative";

export interface CallMetrics {
  avg_latency_ms?: number;
  turns?: number;
  user_words?: number;
  agent_words?: number;
  agent_audio_ms?: number;
  // set by the post-call analysis
  call_reason?: CallReason | null;
  promised_to_customer?: string | null; // what WE promised the customer
  customer_phone?: string | null; // another number the customer gave
  customer_name?: string | null;
  customer_company?: string | null;
  customer_city?: string | null;
}

export interface CallDetail extends CallListItem {
  sentiment: Sentiment | null;
  qualification: QualificationItem[] | null;
  key_points: string[] | null;
  objections: string[] | null;
  metrics: CallMetrics | null;
  error: string | null;
  recording_url: string | null; // relative: "/api/calls/{uuid}/recording" -> prefix with API base
  transcript: TranscriptTurn[];
  lead: Lead | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export type CampaignStatus = "draft" | "running" | "paused" | "completed";

export interface CampaignCounts {
  total: number;
  pending: number;
  dialing: number;
  done: number;
  failed: number;
  skipped: number;
}

export type InterestCounts = Record<Interest, number>;

/**
 * Where each campaign number stands, judged by its latest call. Every number is in exactly one
 * bucket, so they add up to `counts.total`.
 */
export type CampaignResult =
  | "waiting"
  | "ringing"
  | "talking"
  | "picked_up"
  | "no_answer"
  | "busy"
  | "failed"
  | "skipped";
export type CampaignOutcomes = Record<CampaignResult, number>;
/** Filters for GET /api/campaigns/{id}?result= ("ongoing" = ringing + talking). */
export type CampaignResultFilter = CampaignResult | "ongoing";

export interface Campaign {
  id: number;
  name: string;
  status: CampaignStatus;
  concurrency: number;
  max_attempts: number;
  retry_delay_min: number;
  respect_call_window: boolean;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  counts: CampaignCounts;
  interest_counts: InterestCounts;
  active_calls: number;
  /** Per number, by its latest call. Optional: absent in events from an older backend. */
  outcomes?: CampaignOutcomes;
  /** Every dial attempt, retries included. */
  calls_placed?: number;
}

export interface AgentSettings {
  agent_name: string;
  company_name: string;
  agent_gender: "female" | "male";
  voice: string;
  language_code: string;
  tts_pace: number;
  tts_temperature: number;
  /** "written = spoken" rules applied to the voice only, e.g. "PouchWale = पाउचवाले". */
  pronunciations: string[];
  opening_line: string; // supports {lead_name_ji} {lead_name} {agent_name} {company_name}
  call_purpose: string;
  knowledge_base: string;
  qualification_questions: string[];
  objection_handling: string;
  interest_rules: string;
  closing_line: string;
  silence_timeout_sec: number; // default 10: ask "are you on the call?"
  silence_prompt_text: string;
  silence_final_wait_sec: number; // default 3: then goodbye + hang up
  silence_goodbye_text: string;
  fillers_enabled: boolean;
  filler_words: string[];
  filler_delay_ms: number;
  barge_in_enabled: boolean;
  max_call_sec: number;
  call_window_start: string; // "HH:MM" IST, campaigns only
  call_window_end: string;
  /** Off: "Call now" and campaigns place no calls. Default true. */
  outbound_enabled: boolean;
  /** Off: callers hear the unavailable message (or a busy tone); the call is logged as missed. Default true. */
  inbound_enabled: boolean;
  /** Greeting for a first-time / unknown caller (placeholders like the opening line). */
  inbound_greeting: string;
  /** Greeting for a caller whose number is already a lead ({lead_name_ji} = their name). */
  inbound_greeting_known: string;
  /** What the agent should achieve on inbound calls (like call_purpose for outbound). */
  inbound_purpose: string;
  /** Said when an inbound call reaches inbound_max_call_sec. */
  inbound_closing_line: string;
  inbound_max_call_sec: number; // default 600
  /** Greeting when WE call back someone whose inbound call we missed. */
  callback_greeting: string;
  /** When inbound is off or every line is busy: "message" = play the unavailable message, "reject" = busy tone (not billed). */
  inbound_off_action: InboundOffAction;
  inbound_unavailable_message: string;
  speaking_tone: SpeakingTone;
  /** Default language style; the customer's own language still wins turn by turn. */
  language_mix: LanguageMix;
}

export type SpeakingTone = "warm" | "professional" | "energetic" | "calm";
export type LanguageMix = "hinglish" | "hindi" | "english";
export type InboundOffAction = "message" | "reject";

// ---------------------------------------------------------------------------
// Customer timeline (GET /api/leads/{id}/timeline)
// ---------------------------------------------------------------------------

export interface TimelineCallEvent {
  id: string; // "call-<uuid>"
  kind: "call";
  at: string; // ISO UTC: started_at (or created_at)
  call: CallListItem;
  next_action: string | null;
  callback_time: string | null;
  key_points: string[];
}

export interface TimelineLeadEvent {
  id: string; // "lead-<id>"
  kind: "lead_created";
  at: string;
  source: string | null;
}

export interface TimelineCampaignEvent {
  id: string; // "campaign-<id>"
  kind: "campaign_added";
  at: string; // max(campaign.created_at, lead.created_at): never "added" before the lead existed
  campaign_id: number;
  campaign_name: string;
  campaign_status: CampaignStatus;
}

export type TimelineEvent = TimelineCallEvent | TimelineLeadEvent | TimelineCampaignEvent;

export interface LeadTimeline {
  lead: Lead;
  /** Newest first, at most 200. Browser test calls are included (channel "browser"). */
  events: TimelineEvent[];
  stats: {
    calls: number; // phone calls only
    inbound: number;
    outbound: number;
    answered: number; // status "completed" (the shared outcome predicate)
    talk_sec: number;
    first_contact_at: string | null;
    last_contact_at: string | null;
  };
  /** From the latest analysed phone call that has a next action or a callback time. */
  next_step: { next_action: string | null; callback_time: string | null; call_uuid: string; at: string } | null;
}

// ---------------------------------------------------------------------------
// Inbound calling (GET /api/inbound/summary, GET|POST /api/inbound/setup)
// ---------------------------------------------------------------------------

export interface InboundSummary {
  from: string; // YYYY-MM-DD (IST)
  to: string;
  totals: {
    calls: number; // inbound calls (per call); calls = answered + missed + live
    answered: number; // status "completed": the caller talked with the agent
    missed: number; // ended any other way: switched off, lines busy, offline, failed, caller hung up first
    to_call_back: number; // missed calls nobody has spoken with since (follow_up "pending")
    new_callers: number; // DISTINCT callers whose lead was created by an inbound call in the range
    returning_callers: number; // DISTINCT callers who had spoken with us before
    anonymous: number; // calls from hidden / unusable numbers
    avg_duration_sec: number | null; // answered calls only
    hot: number;
    warm: number;
    cold: number;
  };
  by_day: { date: string; calls: number; answered: number; missed: number }[];
  /** Inbound calls in progress right now. */
  live: number;
}

export interface InboundSetup {
  enabled: boolean; // inbound_enabled setting
  number: string | null; // our Vobiz DID (E.164)
  answer_url: string; // paste into the Vobiz application (contains a secret token)
  hangup_url: string;
  public_url_ok: boolean; // PUBLIC_BASE_URL is https
  vobiz_ready: boolean; // Vobiz keys + number configured
  ai_ready: boolean; // OpenAI + Sarvam keys present (otherwise callers get the unavailable message)
  checked: boolean; // Vobiz was queried for the application / number (cached ~60 s)
  app_id: string | null; // our application "GP3-Voice-Agent-Inbound"
  app_name: string | null;
  app_urls_match: boolean | null; // the application's answer + hangup URLs equal ours
  number_linked: boolean | null; // our DID is attached to OUR application
  /** Where calls to our DID go right now (before connecting): another application's id/name, or a SIP trunk. */
  number_app_id: string | null;
  number_app_name: string | null;
  number_on_trunk: boolean | null;
  number_voice_enabled: boolean | null;
  number_status: string | null; // e.g. "active"
  previous_app_id: string | null; // the application the number was attached to before we connected (Disconnect restores it)
  concurrency_max: number | null; // Vobiz account concurrent-call limit
  last_inbound_at: string | null; // newest inbound call we received (ISO)
  rejected_requests: number; // inbound webhook calls with a wrong/old secret since startup
  last_rejected_at: string | null;
  problems: string[];
  ready: boolean; // enabled && public_url_ok && vobiz_ready && ai_ready && app_urls_match && number_linked
}

export interface SystemInfo {
  llm_model: string;
  llm_max_tokens: number;
  llm_temperature: number;
  stt_model: string;
  stt_mode: string;
  tts_model: string;
  providers: { openai: boolean; sarvam: boolean; vobiz: boolean };
  public_base_url: string;
  public_url_ok: boolean; // false if still localhost (Vobiz can't reach it)
}

export interface Voice {
  id: string;
  name: string;
  gender: "female" | "male";
  /** "bulbul:v3" or "bulbul:v4-flash" (newer personas with a built-in style). */
  model?: string;
}

// ---------------------------------------------------------------------------
// Endpoint-specific shapes
// ---------------------------------------------------------------------------

export interface DashboardStats {
  totals: {
    leads: number;
    calls: number;
    calls_today: number;
    connected: number;
    connect_rate: number; // 0..1
    avg_rating: number | null;
    avg_duration_sec: number | null;
    qualified: number;
    active_calls: number;
    inbound_today: number; // inbound calls today (IST)
    inbound_missed_today: number; // of those, missed (not "completed")
  };
  interest: InterestCounts; // over LEADS
  rating_histogram: { rating: number; count: number }[]; // ratings 1..10, always 10 entries
  /** total/connected/hot = OUTBOUND calls (dialed / picked up); inbound = inbound calls that day. */
  calls_by_day: { date: string /* YYYY-MM-DD IST */; total: number; connected: number; hot: number; inbound: number }[];
  end_reasons: { reason: string; count: number }[];
  recent_calls: CallListItem[]; // last 8
  top_leads: Lead[]; // up to 8 hot leads by rating desc
}

export type LeadSort =
  | "created_at"
  | "-created_at"
  | "rating"
  | "-rating"
  | "last_called_at"
  | "-last_called_at"
  | "name"
  | "-name";

export interface LeadListParams {
  search?: string;
  status?: LeadStatus | "";
  interest?: Interest | "";
  stage?: Stage | "";
  page?: number;
  page_size?: number;
  sort?: LeadSort;
}

export interface LeadCreate {
  name: string;
  phone: string;
  company?: string | null;
  city?: string | null;
  email?: string | null;
  source?: string | null;
  notes?: string | null;
}

export type LeadPatch = Partial<
  Pick<Lead, "name" | "phone" | "company" | "city" | "email" | "source" | "notes" | "status" | "interest" | "stage">
>;

export type LeadWithCalls = Lead & { calls: CallListItem[] };

export interface ImportResult {
  imported: number;
  duplicates: number;
  invalid: number;
  errors: string[];
}

export interface CallListParams {
  search?: string;
  status?: CallStatus | "";
  interest?: Interest | "";
  channel?: Channel | "";
  direction?: CallDirection | "";
  caller_type?: CallerType | "";
  /** Same predicate as the inbound KPIs (answered = completed, missed = ended otherwise, live = active). */
  outcome?: CallOutcome | "";
  /** "pending" = missed inbound calls nobody has spoken with since (the call-back list). */
  follow_up?: "pending" | "";
  /** IST dates (YYYY-MM-DD, inclusive) on created_at. */
  from?: string;
  to?: string;
  lead_id?: number;
  page?: number;
  page_size?: number;
}

export interface CampaignFilter {
  status?: LeadStatus;
  interest?: Interest;
  search?: string;
}

export interface CampaignCreate {
  name: string;
  lead_ids?: number[];
  filter?: CampaignFilter;
  concurrency?: number;
  max_attempts?: number;
  retry_delay_min?: number;
  respect_call_window?: boolean;
}

export interface CampaignItemCall {
  status: CallStatus;
  end_reason: EndReason;
  started_at: string | null;
  answered_at: string | null;
  ended_at: string | null;
  duration_sec: number | null;
}

export interface CampaignItem {
  lead: Lead;
  /** Queue state: pending | dialing | done | failed (gave up) | skipped */
  status: string;
  attempts: number;
  last_call_uuid: string | null;
  /** When a pending number will be retried (null = as soon as a line is free). */
  next_attempt_at: string | null;
  result: CampaignResult;
  last_call: CampaignItemCall | null;
}

export type CampaignDetail = Campaign & {
  items: CampaignItem[];
  /** Numbers matching the `result` filter (all numbers without one). */
  items_total: number;
  page: number;
  page_size: number;
};

/** Settings sent with a CSV when creating a campaign from it. */
export type CampaignCsvCreate = Omit<CampaignCreate, "lead_ids" | "filter">;

/** Verdict for one CSV row: callable (new / existing) or left out (duplicate / invalid / dnc). */
export type CsvRowStatus = "new" | "existing" | "duplicate" | "invalid" | "dnc";

export interface CsvPreviewRow {
  /** Spreadsheet row number (header = row 1). */
  line: number;
  name: string;
  raw_phone: string;
  /** Normalised +91 number, null when invalid. */
  phone: string | null;
  company: string | null;
  city: string | null;
  status: CsvRowStatus;
  problem: string | null;
  duplicate_of: number | null;
  lead_id: number | null;
}

export interface CsvSummary {
  rows: number;
  unique: number;
  callable: number;
  new: number;
  existing: number;
  duplicate: number;
  invalid: number;
  dnc: number;
}

export interface CsvPreview {
  summary: CsvSummary;
  /** First 5,000 rows (the summary covers the whole file). */
  rows: CsvPreviewRow[];
  truncated: boolean;
}

export type CampaignFromCsv = Campaign & { upload: CsvSummary };

export interface ProviderTest {
  ok: boolean;
  detail: string;
  latency_ms?: number;
}

/** Keys: openai, sarvam_tts, sarvam_tts_stream, sarvam_stt, vobiz (more may be added - render generically). */
export type TestProvidersResult = Record<string, ProviderTest>;

export interface PreviewVoiceBody {
  text: string;
  voice?: string;
  pace?: number;
  /** Unsaved form values, so a preview plays exactly what was just picked. */
  temperature?: number;
  language_code?: string;
}

// ---------------------------------------------------------------------------
// WebSockets
// ---------------------------------------------------------------------------

export type AgentState = "connecting" | "listening" | "thinking" | "speaking" | "ended";

export type LiveEvent =
  | { type: "snapshot"; calls: CallListItem[] } // sent on connect
  | { type: "call.update"; call: CallListItem } // status changes, start, end, analysis done
  | { type: "call.state"; uuid: string; state: AgentState }
  | {
      type: "transcript";
      uuid: string;
      role: "agent" | "user";
      text: string;
      final: boolean;
      offset_ms: number;
      interrupted?: boolean;
    }
  | { type: "level"; uuid: string; user: number; agent: number } // 0..1, ~8/sec
  | { type: "campaign.update"; campaign: Campaign }
  | { type: "pong" };

/** /ws/listen/{uuid} text frames */
export type ListenEvent = { type: "format"; sample_rate: number } | { type: "clear" };

/** /ws/browser-call client -> server */
export type BrowserCallClientMessage =
  /** direction "inbound" rehearses a customer calling US (inbound greeting, purpose and memory). */
  | { type: "start"; lead_id?: number; name?: string; company?: string; direction?: CallDirection }
  | { type: "hangup" };

/** /ws/browser-call server -> client (JSON frames) */
export type BrowserCallServerMessage =
  | { type: "ready"; uuid: string; input_sample_rate: number; output_sample_rate: number }
  | {
      type: "transcript";
      role: "agent" | "user";
      text: string;
      final: boolean;
      offset_ms: number;
      interrupted?: boolean;
    }
  | { type: "state"; state: AgentState }
  | { type: "clear" }
  | { type: "ended"; reason: EndReason }
  | { type: "error"; detail: string };

// ---------------------------------------------------------------------------
// Lead board (Kanban)
// ---------------------------------------------------------------------------

export interface BoardColumn {
  key: Stage;
  title: string;
  count: number; // full column size
  leads: Lead[]; // first `limit`
}

export interface BoardResponse {
  columns: BoardColumn[];
}

// ---------------------------------------------------------------------------
// Analytics + reports
// ---------------------------------------------------------------------------

export type FunnelStage = "dialed" | "connected" | "conversation" | "warm_or_hot" | "hot";

export interface Analytics {
  range: { from: string; to: string; days: number };
  totals: {
    dialed: number;
    connected: number;
    connect_rate: number; // 0..1 (connected / dialed)
    conversations: number; // customer actually spoke
    talk_minutes: number;
    avg_duration_sec: number | null;
    avg_rating: number | null;
    hot: number;
    warm: number;
    cold: number;
    qualified: number;
    avg_latency_ms: number | null;
    p90_latency_ms: number | null;
    inbound: number; // inbound calls in range (dialed/connected/funnel above are OUTBOUND only)
    inbound_answered: number;
    inbound_missed: number;
  };
  funnel: { stage: FunnelStage; label: string; count: number }[]; // outbound only, non-increasing
  by_day: {
    date: string;
    dialed: number; // outbound
    connected: number; // outbound
    hot: number;
    warm: number;
    cold: number;
    avg_rating: number | null;
    inbound: number;
  }[];
  by_hour: { hour: number; dialed: number; connected: number }[]; // 24 entries, IST hour 0..23
  interest: InterestCounts; // calls in range
  rating_histogram: { rating: number; count: number }[];
  duration_buckets: { label: string; count: number }[];
  end_reasons: { reason: string; count: number }[];
  sentiment: { positive: number; neutral: number; negative: number };
  objections: { tag: string; label: string; count: number }[];
  qualification: { question: string; answered: number; partial: number; not_asked: number }[];
  campaigns: { id: number; name: string; dialed: number; connected: number; hot: number; warm: number; cold: number; avg_rating: number | null }[];
  latency_by_day: { date: string; avg_ms: number | null }[];
}

export interface AnalyticsParams {
  from?: string; // YYYY-MM-DD (IST, inclusive)
  to?: string;
  campaign_id?: number | "";
  include_tests?: boolean;
}
