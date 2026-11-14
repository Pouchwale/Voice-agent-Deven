import type {
  CallDirection,
  CallerType,
  CallOutcome,
  CallReason,
  CallStatus,
  CampaignResult,
  EndReason,
  FollowUp,
  Interest,
  LeadStatus,
  Stage,
} from "./types";

const TZ = "Asia/Kolkata";

const dateTimeFmt = new Intl.DateTimeFormat("en-IN", {
  timeZone: TZ,
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
});
const dateFmt = new Intl.DateTimeFormat("en-IN", { timeZone: TZ, day: "2-digit", month: "short", year: "numeric" });
const shortDateTimeFmt = new Intl.DateTimeFormat("en-IN", {
  timeZone: TZ,
  day: "2-digit",
  month: "short",
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
});
const timeFmt = new Intl.DateTimeFormat("en-IN", { timeZone: TZ, hour: "numeric", minute: "2-digit", hour12: true });
const ymdFmt = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });
const numberFmt = new Intl.NumberFormat("en-IN");

function toDate(value: string | Date | null | undefined): Date | null {
  if (!value) return null;
  // The API sends UTC ISO strings; if a timezone suffix is ever missing, treat it as UTC.
  const d =
    value instanceof Date
      ? value
      : new Date(/T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(value) ? `${value}Z` : value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "02 Nov 2026, 3:45 pm" in IST. */
export function formatDateTime(value: string | Date | null | undefined, fallback = "—"): string {
  const d = toDate(value);
  return d ? dateTimeFmt.format(d) : fallback;
}

/** "02 Nov, 3:45 pm" in IST - for dense tables. */
export function formatShortDateTime(value: string | Date | null | undefined, fallback = "—"): string {
  const d = toDate(value);
  return d ? shortDateTimeFmt.format(d) : fallback;
}

export function formatDate(value: string | Date | null | undefined, fallback = "—"): string {
  const d = toDate(value);
  return d ? dateFmt.format(d) : fallback;
}

export function formatTime(value: string | Date | null | undefined, fallback = "—"): string {
  const d = toDate(value);
  return d ? timeFmt.format(d) : fallback;
}

/** YYYY-MM-DD for a date in IST. */
export function ymdIST(value: Date = new Date()): string {
  return ymdFmt.format(value);
}

/** "12 Nov" from a "YYYY-MM-DD" (IST calendar date - no tz shift). */
export function formatDayLabel(ymd: string): string {
  const [y, m, d] = ymd.split("-").map(Number);
  if (!y || !m || !d) return ymd;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${d} ${months[m - 1]}`;
}

/** "just now", "5 min ago", "3 h ago", "2 d ago", else the IST date. */
export function formatRelative(value: string | Date | null | undefined, now = Date.now(), fallback = "—"): string {
  const d = toDate(value);
  if (!d) return fallback;
  const diff = Math.round((now - d.getTime()) / 1000);
  // A server clock a little ahead of this browser must not turn "just now" into a date.
  if (diff < -120) return formatShortDateTime(d);
  if (diff < 45) return "just now";
  if (diff < 3600) return `${Math.max(1, Math.round(diff / 60))} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  if (diff < 86400 * 7) return `${Math.round(diff / 86400)} d ago`;
  return formatDate(d);
}

/** Seconds -> "m:ss" (or "h:mm:ss" past an hour). */
export function formatDuration(totalSec: number | null | undefined, fallback = "—"): string {
  if (totalSec === null || totalSec === undefined || !Number.isFinite(totalSec)) return fallback;
  const s = Math.max(0, Math.round(totalSec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** Milliseconds -> "mm:ss". */
export function formatOffset(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "00:00";
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function formatNumber(n: number | null | undefined, fallback = "—"): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return fallback;
  return numberFmt.format(n);
}

/** 0..1 -> "42%". */
export function formatPercent(ratio: number | null | undefined, digits = 0, fallback = "—"): string {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return fallback;
  return `${(ratio * 100).toFixed(digits)}%`;
}

export function formatLatency(ms: number | null | undefined, fallback = "—"): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return fallback;
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

/**
 * Indian-friendly phone formatting:
 *  9876543210 / 09876543210 / 919876543210 / +91 98765-43210  ->  "+91 98765 43210"
 * Anything else (incl. any other "+<country>" number) is returned trimmed as typed.
 */
export function formatPhone(raw: string | null | undefined, fallback = "—"): string {
  if (!raw || !raw.trim()) return fallback;
  const trimmed = raw.trim();
  // An explicit country code that isn't +91 is foreign: "+65 9123 4567" has 10 digits but is not Indian.
  if ((trimmed.startsWith("+") || trimmed.startsWith("00")) && !/^(\+|00)\s*9\s*1/.test(trimmed)) return trimmed;
  const digits = raw.replace(/\D/g, "");
  let local: string | null = null;
  if (digits.length === 10) local = digits;
  else if (digits.length === 11 && digits.startsWith("0")) local = digits.slice(1);
  else if (digits.length === 12 && digits.startsWith("91")) local = digits.slice(2);
  if (local && /^[6-9]/.test(local)) return `+91 ${local.slice(0, 5)} ${local.slice(5)}`;
  if (local) return `+91 ${local.slice(0, 2)} ${local.slice(2, 6)} ${local.slice(6)}`;
  return raw.trim();
}

/** tel: href for a phone number. */
export function telHref(raw: string | null | undefined): string | undefined {
  if (!raw) return undefined;
  const digits = raw.replace(/[^\d+]/g, "");
  return digits ? `tel:${digits}` : undefined;
}

/** Display name for a lead: leads imported without a name fall back to their phone number. */
export function leadName(lead: { name?: string | null; phone?: string | null } | null | undefined, fallback = "Unnamed lead"): string {
  return lead?.name?.trim() || formatPhone(lead?.phone, fallback);
}

/** Display title for a call: lead name, else "Browser test" for browser calls, else the phone number. */
export function callTitle(
  call: { lead_name: string | null; lead_phone: string | null; channel: string },
  fallback = "Unknown lead",
): string {
  return call.lead_name?.trim() || (call.channel === "browser" ? "Browser test" : formatPhone(call.lead_phone, fallback));
}

export function initials(name: string | null | undefined): string {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

export function pluralize(n: number, one: string, many = `${one}s`): string {
  return `${formatNumber(n)} ${n === 1 ? one : many}`;
}

// ---------------------------------------------------------------------------
// Labels
// ---------------------------------------------------------------------------

export const INTEREST_ORDER: Interest[] = ["cold", "warm", "hot"];
export const INTEREST_ALL: Interest[] = ["unknown", "cold", "warm", "hot"];

export const INTEREST_LABEL: Record<Interest, string> = {
  unknown: "Unknown",
  cold: "Cold",
  warm: "Warm",
  hot: "Hot",
};

export const STAGES: Stage[] = ["new", "no_answer", "cold", "warm", "hot"];
export const STAGE_LABEL: Record<Stage, string> = {
  new: "New",
  no_answer: "No answer",
  cold: "Cold",
  warm: "Warm",
  hot: "Hot",
};
export const STAGE_HINT: Record<Stage, string> = {
  new: "Not called yet",
  no_answer: "Didn't pick up or busy",
  cold: "Picked up & cut, or not interested",
  warm: "Normal interest",
  hot: "Strong interest",
};

export const LEAD_STATUSES: LeadStatus[] = ["new", "queued", "calling", "called", "failed", "dnc"];
export const LEAD_STATUS_LABEL: Record<LeadStatus, string> = {
  new: "New",
  queued: "Queued",
  calling: "Calling",
  called: "Called",
  failed: "Failed",
  dnc: "Do not call",
};

export const CALL_STATUSES: CallStatus[] = [
  "queued",
  "initiated",
  "ringing",
  "in_progress",
  "completed",
  "no_answer",
  "busy",
  "failed",
  "canceled",
];
export const CALL_STATUS_LABEL: Record<CallStatus, string> = {
  queued: "Queued",
  initiated: "Dialing",
  ringing: "Ringing",
  in_progress: "In progress",
  completed: "Completed",
  no_answer: "No answer",
  busy: "Busy",
  failed: "Failed",
  canceled: "Canceled",
};

/** Campaign number results (by the number's latest call). */
export const CAMPAIGN_RESULT_LABEL: Record<CampaignResult, string> = {
  waiting: "Waiting",
  ringing: "Ringing",
  talking: "On call",
  picked_up: "Picked up",
  no_answer: "No answer",
  busy: "Busy",
  failed: "Failed",
  skipped: "Skipped",
};

export const ACTIVE_CALL_STATUSES: CallStatus[] = ["queued", "initiated", "ringing", "in_progress"];
export function isCallActive(status: CallStatus): boolean {
  return ACTIVE_CALL_STATUSES.includes(status);
}

const END_REASON_LABEL: Record<NonNullable<EndReason>, string> = {
  agent_ended: "Agent ended",
  user_hangup: "Customer hung up",
  silence_timeout: "Silence timeout",
  max_duration: "Max duration",
  manual_hangup: "Manual hang-up",
  error: "Error",
  no_answer: "No answer",
  busy: "Busy",
  inbound_off: "Missed (inbound off)",
  lines_busy: "Missed (lines busy)",
  superseded: "Cancelled (customer called in)",
  offline: "Missed (agent offline)",
};

/** Chip text for an inbound caller. */
export const CALLER_TYPE_LABEL: Record<CallerType, string> = {
  returning: "Returning caller",
  known: "Existing lead",
  new: "New caller",
  international: "International",
  anonymous: "Hidden number",
};

export const CALL_REASON_LABEL: Record<CallReason, string> = {
  enquiry: "Enquiry",
  callback_of_our_call: "Returned our call",
  order_or_sample: "Order / sample",
  complaint: "Complaint",
  vendor_or_job: "Vendor / job",
  wrong_number: "Wrong number",
  other: "Other",
};

export const FOLLOW_UP_LABEL: Record<FollowUp, string> = {
  pending: "To call back",
  tried: "Tried, no answer yet",
  done: "Spoke since",
};

export const DIRECTION_LABEL: Record<CallDirection, string> = {
  inbound: "Inbound",
  outbound: "Outbound",
};

/** Text for the inbound outcome predicate (see callOutcome), e.g. in filters. */
export const CALL_OUTCOME_LABEL: Record<CallOutcome, string> = {
  answered: "Answered",
  missed: "Missed",
  live: "Live",
};

/** The inbound outcome predicate used everywhere (KPIs, filters, timeline). */
export function callOutcome(call: { status: CallStatus }): CallOutcome {
  if (isCallActive(call.status)) return "live";
  return call.status === "completed" ? "answered" : "missed";
}

/**
 * Status text that reads right in both directions: an inbound call that never connected is "Missed",
 * not "No answer" (which would read as "the customer didn't pick up").
 */
export function callStatusLabel(call: { status: CallStatus; direction?: CallDirection }): string {
  if (call.direction === "inbound" && callOutcome(call) === "missed") return "Missed";
  return CALL_STATUS_LABEL[call.status] ?? call.status;
}

export function endReasonLabel(reason: string | null | undefined): string {
  if (!reason) return "—";
  return (
    END_REASON_LABEL[reason as NonNullable<EndReason>] ??
    reason.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase())
  );
}

export function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
