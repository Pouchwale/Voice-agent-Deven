"use client";

import { ArrowRight, Clock, Laptop, Phone } from "lucide-react";
import { Chip } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { InterestBadge } from "@/components/ui/InterestBadge";
import { RatingPill } from "@/components/ui/RatingPill";
import { callTitle, endReasonLabel, formatDuration, formatPhone, formatTime } from "@/lib/format";
import type { LiveCall } from "@/lib/live-store";

export function RecentlyEnded({ calls }: { calls: LiveCall[] }) {
  if (calls.length === 0) return null;
  return (
    <section data-anim aria-labelledby="recently-ended" className="mt-10">
      <h2 id="recently-ended" className="mb-3 font-heading text-base font-semibold">
        Recently ended
      </h2>
      <ul className="overflow-hidden rounded-xl border border-line bg-white">
        {calls.map(({ call }) => {
          const name = callTitle(call);
          const analysed = call.analysis_status === "done";
          return (
            <li
              key={call.uuid}
              className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line px-4 py-3 last:border-b-0"
            >
              <span className="shrink-0 [&_svg]:size-4" aria-hidden>
                {call.channel === "browser" ? <Laptop /> : <Phone />}
              </span>
              <div className="min-w-[160px] flex-1">
                <p className="truncate font-semibold">{name}</p>
                <p className="truncate text-[13px] font-normal text-muted">
                  {[call.lead_company, call.lead_phone ? formatPhone(call.lead_phone) : null].filter(Boolean).join(" · ") ||
                    "—"}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] font-medium">
                <span>{endReasonLabel(call.end_reason)}</span>
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
