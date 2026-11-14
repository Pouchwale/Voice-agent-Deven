"use client";

import Link from "next/link";
import { CircleAlert, Headphones, Hourglass, PhoneCall, PhoneIncoming, PhoneMissed, PhoneOff } from "lucide-react";
import type { ReactNode } from "react";
import { useLive } from "@/components/providers/LiveProvider";
import { Dot } from "@/components/ui/Badge";
import { useNow } from "@/hooks/useNow";
import { cn } from "@/lib/cn";
import { callTitle, formatDuration, formatNumber, formatPercent, formatPhone } from "@/lib/format";
import type { Campaign, CampaignOutcomes as Outcomes, CampaignResultFilter } from "@/lib/types";

interface Tile {
  key: CampaignResultFilter;
  label: string;
  value: number;
  caption: string;
  icon: ReactNode;
  tone?: "live" | "brand";
}

function tiles(o: Outcomes): Tile[] {
  const ongoing = o.ringing + o.talking;
  return [
    {
      key: "ongoing",
      label: "On call now",
      value: ongoing,
      caption: ongoing ? `${formatNumber(o.ringing)} ringing · ${formatNumber(o.talking)} talking` : "No live calls",
      icon: <PhoneCall />,
      tone: ongoing ? "live" : undefined,
    },
    // Includes numbers settled because the customer called us and talked (no need to dial them any more).
    { key: "picked_up", label: "Picked up", value: o.picked_up, caption: "Answered, or called us", icon: <PhoneIncoming />, tone: "brand" },
    { key: "no_answer", label: "No answer", value: o.no_answer, caption: "Rang, not picked up", icon: <PhoneMissed /> },
    { key: "busy", label: "Busy", value: o.busy, caption: "Line busy / rejected", icon: <PhoneOff /> },
    { key: "failed", label: "Failed", value: o.failed, caption: "Couldn't connect", icon: <CircleAlert /> },
    {
      key: "waiting",
      label: "Waiting",
      value: o.waiting,
      caption: o.skipped ? `Not dialed yet · ${formatNumber(o.skipped)} skipped` : "Not dialed yet",
      icon: <Hourglass />,
    },
  ];
}

/**
 * Live per-number results of a campaign. Each number counts once (its latest call), so the tiles add up
 * to the campaign total. Clicking a tile shows those numbers in the list below.
 */
export function CampaignOutcomes({
  campaign,
  active,
  onPick,
}: {
  campaign: Campaign;
  active: CampaignResultFilter | "";
  onPick: (f: CampaignResultFilter) => void;
}) {
  const o = campaign.outcomes;
  if (!o) return null;
  const finished = o.picked_up + o.no_answer + o.busy + o.failed;
  const pickupRate = finished > 0 ? o.picked_up / finished : null;

  return (
    <div>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 xl:grid-cols-6" role="group" aria-label={`${campaign.name} call results`}>
        {tiles(o).map((t) => {
          const selected = active === t.key;
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => onPick(t.key)}
              aria-pressed={selected}
              title={`Show ${t.label.toLowerCase()} numbers`}
              className={cn(
                "group rounded-xl border p-3 text-left transition-colors",
                selected
                  ? "border-primary bg-primary-tint"
                  : t.tone === "live"
                    ? "border-primary-tint-strong bg-primary-tint hover:border-primary"
                    : "border-line bg-white hover:border-primary-tint-strong hover:bg-canvas",
              )}
            >
              <span className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 text-[12px] font-medium text-muted">
                  {t.tone === "live" ? <Dot className="bg-primary" live /> : null}
                  {t.label}
                </span>
                <span className={cn("[&_svg]:size-4", t.tone === "live" ? "text-primary" : "text-muted")} aria-hidden>
                  {t.icon}
                </span>
              </span>
              <span
                className={cn(
                  "tabular mt-1.5 block font-heading text-2xl leading-none font-semibold tracking-tight",
                  t.value === 0 && "text-faint",
                  t.value > 0 && t.tone === "brand" && "text-brand",
                  t.value > 0 && t.tone === "live" && "text-primary",
                )}
              >
                {formatNumber(t.value)}
              </span>
              <span className="mt-1 block truncate text-[11px] font-normal text-muted">{t.caption}</span>
            </button>
          );
        })}
      </div>
      <p className="tabular mt-2 text-[12px] font-normal text-muted">
        {formatNumber(campaign.calls_placed ?? 0)} calls placed (retries included)
        {pickupRate !== null ? ` · pickup rate ${formatPercent(pickupRate)} of numbers reached so far` : ""}
      </p>
    </div>
  );
}

/** Calls of this campaign that are ringing or connected right now (from the live feed). */
export function CampaignLiveCalls({ campaignId }: { campaignId: number }) {
  const live = useLive();
  const now = useNow(1000);
  const calls = live.active.filter((lc) => lc.call.campaign_id === campaignId);
  if (calls.length === 0) return null;

  return (
    <div className="rounded-xl border border-primary-tint-strong bg-white">
      <div className="flex items-center justify-between gap-3 border-b border-line px-3 py-2">
        <p className="flex items-center gap-2 text-[12px] font-semibold">
          <Dot className="bg-primary" live />
          Live now ({calls.length})
        </p>
        <Link
          href="/live"
          className="inline-flex items-center gap-1 text-[12px] font-semibold text-primary underline-offset-4 hover:underline [&_svg]:size-3.5"
        >
          <Headphones aria-hidden />
          Open live monitor
        </Link>
      </div>
      <ul className="divide-y divide-line">
        {calls.slice(0, 8).map(({ call, state }) => {
          const talking = call.status === "in_progress";
          const since = Date.parse((talking ? call.answered_at : call.started_at) ?? call.created_at);
          const secs = Number.isFinite(since) ? (now - since) / 1000 : null;
          return (
            <li key={call.uuid} className="flex items-center gap-3 px-3 py-2 text-[13px]">
              <span className="min-w-0 flex-1">
                <span className="block truncate font-semibold">{callTitle(call)}</span>
                <span className="tabular block text-[12px] text-muted">{formatPhone(call.lead_phone)}</span>
              </span>
              <span
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full border px-2 py-px text-[12px] font-medium",
                  talking ? "border-primary bg-primary text-white" : "border-primary-tint-strong bg-primary-tint text-primary",
                )}
              >
                <Dot className={talking ? "bg-white" : "bg-primary"} live />
                {talking ? (state === "speaking" ? "Agent speaking" : state === "listening" ? "Listening" : "Talking") : "Ringing"}
              </span>
              <span className="tabular w-12 text-right text-[12px] font-medium text-muted">{formatDuration(secs)}</span>
            </li>
          );
        })}
      </ul>
      {calls.length > 8 ? (
        <p className="border-t border-line px-3 py-1.5 text-[12px] text-muted">+{calls.length - 8} more on the live monitor</p>
      ) : null}
    </div>
  );
}
