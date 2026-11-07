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
  | null;
export type Channel = "phone" | "browser";

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
}

export interface CallDetail extends CallListItem {
  from_number: string | null;
  to_number: string | null;
  sentiment: Sentiment | null;
  next_action: string | null;
  callback_time: string | null;
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
}

export interface AgentSettings {
  agent_name: string;
  company_name: string;
  agent_gender: "female" | "male";
  voice: string;
  language_code: string;
  tts_pace: number;
  tts_temperature: number;
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
  };
  interest: InterestCounts; // over LEADS
  rating_histogram: { rating: number; count: number }[]; // ratings 1..10, always 10 entries
  calls_by_day: { date: string /* YYYY-MM-DD IST */; total: number; connected: number; hot: number }[];
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

export interface CampaignItem {
  lead: Lead;
  status: string;
  attempts: number;
  last_call_uuid: string | null;
}

export type CampaignDetail = Campaign & { items: CampaignItem[] };

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
  | { type: "start"; lead_id?: number; name?: string; company?: string }
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
  };
  funnel: { stage: FunnelStage; label: string; count: number }[];
  by_day: { date: string; dialed: number; connected: number; hot: number; warm: number; cold: number; avg_rating: number | null }[];
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
