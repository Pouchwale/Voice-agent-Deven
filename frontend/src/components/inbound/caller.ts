import { formatDateTime, formatPhone } from "@/lib/format";
import type { CallListItem, LeadStatus } from "@/lib/types";

type CallLike = Pick<
  CallListItem,
  "direction" | "channel" | "caller_type" | "from_number" | "to_number" | "lead_phone" | "lead_name"
>;

/** A real customer calling us. Browser rehearsals of an inbound call never count as customer calls. */
export function isInboundPhone(call: Pick<CallListItem, "direction" | "channel">): boolean {
  return call.direction === "inbound" && call.channel === "phone";
}

/** The customer's number on a call (caller on inbound, dialled number on outbound); null when the caller hid it. */
export function customerNumber(call: CallLike): string | null {
  if (call.direction === "inbound") {
    if (call.caller_type === "anonymous") return null;
    return call.from_number?.trim() || call.lead_phone?.trim() || null;
  }
  return call.lead_phone?.trim() || call.to_number?.trim() || null;
}

/** What a hidden-number caller actually sent (short codes, garbage, our own DID) - for the team, never dialled. */
export function rawCallerId(call: CallLike): string | null {
  return call.direction === "inbound" && call.caller_type === "anonymous" ? call.from_number?.trim() || null : null;
}

/** The caller's number for display: "+91 98765 43210", or "Hidden number". */
export function callerNumberLabel(call: CallLike): string {
  const n = customerNumber(call);
  return n ? formatPhone(n) : call.direction === "inbound" ? "Hidden number" : "—";
}

/**
 * Who is on the call: the lead's name, else their number, else a neutral label (never an empty string).
 * Next to a caller chip (which already says "Hidden number") pass `hidden = "Unknown caller"`.
 */
export function callerTitle(call: CallLike, unnamed = "Unknown caller", hidden = "Hidden number"): string {
  const name = call.lead_name?.trim();
  if (name) return name;
  if (call.channel === "browser") return "Browser test";
  if (call.direction === "inbound" && call.caller_type === "anonymous") return hidden;
  const n = customerNumber(call);
  return n ? formatPhone(n) : unnamed;
}

/** Why an inbound call was missed, as a short sentence (null when the end reason adds nothing). */
const MISSED_REASON: Partial<Record<NonNullable<CallListItem["end_reason"]>, string>> = {
  inbound_off: "Inbound calling was off",
  lines_busy: "All lines were busy",
  offline: "The agent was offline",
  error: "Failed on our side",
  user_hangup: "Hung up before the agent answered",
};

/** Same, as a fragment for titles: "Missed call · lines busy". */
const MISSED_SHORT: Partial<Record<NonNullable<CallListItem["end_reason"]>, string>> = {
  inbound_off: "inbound was off",
  lines_busy: "lines busy",
  offline: "agent offline",
  error: "failed on our side",
  user_hangup: "hung up before we answered",
};

export function missedReason(call: Pick<CallListItem, "end_reason">): string | null {
  return call.end_reason ? (MISSED_REASON[call.end_reason] ?? null) : null;
}

export function missedReasonShort(call: Pick<CallListItem, "end_reason">): string | null {
  return call.end_reason ? (MISSED_SHORT[call.end_reason] ?? null) : null;
}

/** callback_time may be an ISO timestamp or free text ("tomorrow 4 pm"). */
export function formatCallback(v: string | null | undefined): string | null {
  if (!v || !v.trim()) return null;
  return /^\d{4}-\d{2}-\d{2}T/.test(v) ? `${formatDateTime(v)} IST` : v.trim();
}

/**
 * Epoch ms for an API timestamp (UTC ISO; a missing zone suffix counts as UTC, like lib/format).
 * Only for arithmetic (ages, ordering) - every displayed time goes through the IST formatters.
 */
export function epochMs(v: string | null | undefined): number {
  if (!v) return NaN;
  return new Date(/T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(v) ? `${v}Z` : v).getTime();
}

/**
 * Why "Call back" can't be used for this call, or null when it can. The most permanent reason wins,
 * so the team isn't told to switch outbound on for a number that can never be dialled anyway.
 */
export function callBackBlock(opts: {
  call: Pick<CallListItem, "caller_type" | "lead_id">;
  leadStatus?: LeadStatus | null;
  onLiveCall: boolean;
  outboundHint: string | null;
}): string | null {
  const { call } = opts;
  if (call.caller_type === "anonymous") return "Hidden number: there's no number to call back.";
  if (!call.lead_id) {
    return call.caller_type === "international"
      ? "International number: the agent can't call numbers outside India."
      : "No lead is linked to this call yet.";
  }
  if (opts.leadStatus === "dnc") return "This customer is on the do-not-call list.";
  if (opts.onLiveCall) return "They're on a live call right now.";
  return opts.outboundHint;
}
