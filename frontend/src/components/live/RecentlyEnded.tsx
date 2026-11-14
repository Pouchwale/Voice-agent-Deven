"use client";

import { ArrowRight, Clock, Laptop, Phone, PhoneIncoming } from "lucide-react";
import { callerNumberLabel, callerTitle } from "@/components/inbound/caller";
import { Chip } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { InterestBadge } from "@/components/ui/InterestBadge";
import { RatingPill } from "@/components/ui/RatingPill";
import { callOutcome, endReasonLabel, formatDuration, formatTime } from "@/lib/format";
import type { LiveCall } from "@/lib/live-store";
import type { CallListItem } from "@/lib/types";

/** End reason; a missed inbound call says so first ("Customer hung up" alone would read as a normal ending). */
function endedLabel(call: CallListItem): string {
  const reason = endReasonLabel(call.end_reason);
  if (call.direction !== "inbound" || callOutcome(call) !== "missed") return reason;
  return reason.startsWith("Missed") ? reason : reason === "—" ? "Missed" : `Missed · ${reason}`;
}

export function RecentlyEnded({ calls }: { calls: LiveCall[] }) {
  if (calls.length === 0) return null;
  return (
    <section data-anim aria-labelledby="recently-ended" className="mt-10">
      <h2 id="recently-ended" className="mb-3 font-heading text-base font-semibold">
        Recently ended
      </h2>
      <ul className="overflow-hidden rounded-xl border border-line bg-white">
        {calls.map(({ call }) => {
          const inbound = call.direction === "inbound";
          const name = callerTitle(call, inbound ? "Unknown caller" : "Unknown lead");
          const number = call.channel === "phone" ? callerNumberLabel(call) : null;
          const analysed = call.analysis_status === "done";
          return (
            <li
              key={call.uuid}
              className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line px-4 py-3 last:border-b-0"
            >
              <span className="shrink-0 [&_svg]:size-4" title={inbound ? "Inbound call" : undefined}>
                {call.channel === "browser" ? <Laptop aria-hidden /> : inbound ? <PhoneIncoming aria-hidden /> : <Phone aria-hidden />}
                {inbound ? <span className="sr-only">Inbound call</span> : null}
              </span>
              <div className="min-w-[160px] flex-1">
                <p className="truncate font-semibold">{name}</p>
                <p className="truncate text-[13px] font-normal text-muted">
                  {[call.lead_company, number && number !== name ? number : null].filter(Boolean).join(" · ") || "—"}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] font-medium">
                <span>{endedLabel(call)}</span>
                <span className="tabular inline-flex items-center gap-1">
                  <Clock className="size-3.5" aria-hidden />
                  {formatDuration(call.duration_sec)}
                </span>
                <span className="tabular text-muted">{formatTime(call.ended_at)}</span>
              </div>
              <div className="flex items-center gap-2">
                {analysed ? (
                  <>
                    <InterestBadge interest={call.interest} size="xs" />
                    <RatingPill rating={call.rating} />
                  </>
                ) : call.analysis_status === "pending" ? (
                  <Chip size="xs" color="neutral">
                    Analysing…
                  </Chip>
                ) : null}
              </div>
              <ButtonLink href={`/calls/${call.uuid}`} size="sm" variant="secondary" iconRight={<ArrowRight />}>
                View summary
              </ButtonLink>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
