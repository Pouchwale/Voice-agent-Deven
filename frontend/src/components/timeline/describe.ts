import { Megaphone, Monitor, PhoneIncoming, PhoneMissed, PhoneOutgoing, UserPlus } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { epochMs, missedReasonShort } from "@/components/inbound/caller";
import { callOutcome, formatDate, formatDuration, ymdIST } from "@/lib/format";
import type { CallListItem, TimelineEvent } from "@/lib/types";

export type EventTone = "live" | "answered" | "missed" | "neutral";

export interface EventLook {
  icon: LucideIcon;
  tone: EventTone;
  title: string;
}

/** Icon tile per tone. No red/green: "missed" is an ink outline, "answered" the blue tint. */
export const TONE_CLASS: Record<EventTone, string> = {
  live: "border-primary bg-primary text-white",
  answered: "border-primary-tint-strong bg-primary-tint text-primary",
  missed: "border-fg/25 bg-white text-fg",
  neutral: "border-line bg-subtle text-subtle-fg",
};

// Same window as the backend's call-back mode: an outbound call within 7 days of a missed inbound call returns it.
const CALLBACK_WINDOW_MS = 7 * 86_400_000;

function talked(call: CallListItem): string {
  return call.duration_sec && call.duration_sec > 0 ? ` · talked ${formatDuration(call.duration_sec)}` : "";
}

/** Title + icon for a call: "Customer called us · talked 2:31", "We called · no answer", "Missed call · lines busy"… */
export function describeCall(call: CallListItem, calledBack: boolean): EventLook {
  if (call.channel === "browser") {
    const inbound = call.direction === "inbound" ? " · customer calls us" : "";
    return { icon: Monitor, tone: "neutral", title: `Browser test call${inbound}${talked(call)}` };
  }
  const outcome = callOutcome(call);
  if (call.direction === "inbound") {
    if (outcome === "live") return { icon: PhoneIncoming, tone: "live", title: "Customer is calling now" };
    if (outcome === "answered") return { icon: PhoneIncoming, tone: "answered", title: `Customer called us${talked(call)}` };
    const why = missedReasonShort(call);
    return { icon: PhoneMissed, tone: "missed", title: why ? `Missed call · ${why}` : "Missed call" };
  }
  const verb = calledBack ? "We called back" : "We called";
  if (outcome === "live") {
    return { icon: PhoneOutgoing, tone: "live", title: `${verb} · ${call.status === "in_progress" ? "talking now" : "dialing"}` };
  }
  switch (call.status) {
    case "completed":
      return { icon: PhoneOutgoing, tone: "answered", title: `${verb}${talked(call)}` };
    case "canceled":
      return {
        icon: PhoneOutgoing,
        tone: "neutral",
        title: call.end_reason === "superseded" ? `${verb} · cancelled (they called us)` : `${verb} · cancelled`,
      };
    case "busy":
      return { icon: PhoneMissed, tone: "missed", title: `${verb} · busy` };
    case "failed":
      return { icon: PhoneMissed, tone: "missed", title: `${verb} · failed` };
    default:
      return { icon: PhoneMissed, tone: "missed", title: `${verb} · no answer` };
  }
}

export function describeOther(event: Exclude<TimelineEvent, { kind: "call" }>): EventLook {
  if (event.kind === "campaign_added") {
    return { icon: Megaphone, tone: "neutral", title: `Added to campaign ${event.campaign_name || `#${event.campaign_id}`}` };
  }
  const source = event.source?.trim();
  return { icon: UserPlus, tone: "neutral", title: source ? `Lead created · ${source}` : "Lead created" };
}

/**
 * Outbound phone calls that returned a missed inbound call: the newest inbound call before them was missed,
 * at most 7 days earlier, and nobody has had a conversation since. Events arrive newest first.
 */
export function calledBackUuids(events: TimelineEvent[]): Set<string> {
  const out = new Set<string>();
  let missedAt: number | null = null;
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.kind !== "call" || e.call.channel !== "phone") continue;
    const outcome = callOutcome(e.call);
    if (e.call.direction === "inbound") {
      if (outcome === "missed") missedAt = epochMs(e.at);
      else if (outcome === "answered") missedAt = null;
      continue;
    }
    const at = epochMs(e.at);
    if (missedAt !== null && Number.isFinite(missedAt) && at - missedAt <= CALLBACK_WINDOW_MS) out.add(e.call.uuid);
    if (outcome === "answered") missedAt = null;
  }
  return out;
}

export interface DayGroup {
  key: string;
  label: string;
  events: TimelineEvent[];
}

/** Groups (newest-first) events by IST calendar day: "Today", "Yesterday", "12 Nov 2026". */
export function groupByDay(events: TimelineEvent[], today: string, yesterday: string): DayGroup[] {
  const groups = new Map<string, DayGroup>();
  for (const e of events) {
    const ms = epochMs(e.at);
    const key = Number.isFinite(ms) ? ymdIST(new Date(ms)) : "unknown";
    let g = groups.get(key);
    if (!g) {
      const label =
        key === today ? "Today" : key === yesterday ? "Yesterday" : key === "unknown" ? "Date unknown" : formatDate(e.at);
      g = { key, label, events: [] };
      groups.set(key, g);
    }
    g.events.push(e);
  }
  return [...groups.values()];
}
