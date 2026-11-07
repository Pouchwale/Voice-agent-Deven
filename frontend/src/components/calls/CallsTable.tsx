"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Globe, LoaderCircle, Phone } from "lucide-react";
import { InterestBadge } from "@/components/ui/InterestBadge";
import { RatingPill } from "@/components/ui/RatingPill";
import { CallStatusChip } from "@/components/ui/StatusChip";
import { cn } from "@/lib/cn";
import { callTitle, endReasonLabel, formatDuration, formatPhone, formatShortDateTime } from "@/lib/format";
import type { CallListItem } from "@/lib/types";

export function ChannelIcon({ channel }: { channel: CallListItem["channel"] }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[13px] text-subtle-fg"
      title={channel === "browser" ? "Browser test call" : "Phone call"}
    >
      <span className="inline-flex text-muted [&_svg]:size-4">
        {channel === "browser" ? <Globe aria-hidden /> : <Phone aria-hidden />}
      </span>
      <span className="sr-only sm:not-sr-only">{channel === "browser" ? "Browser" : "Phone"}</span>
    </span>
  );
}

/** Call log table. Row click opens the call summary. */
export function CallsTable({ calls, showLead = true, dim = false }: { calls: CallListItem[]; showLead?: boolean; dim?: boolean }) {
  const router = useRouter();
  return (
    <div className={cn("relative overflow-x-auto transition-opacity", dim && "opacity-70")}>
      <table className={cn("data-table", showLead ? "min-w-[920px]" : "min-w-[760px]")}>
        <thead>
          <tr>
            <th>Time (IST)</th>
            {showLead ? <th>Lead</th> : null}
            <th>Channel</th>
            <th>Status</th>
            <th>Interest</th>
            <th>Rating</th>
            <th className="text-right">Duration</th>
            <th>End reason</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((c) => (
            <tr key={c.uuid} className="row-link" onClick={() => router.push(`/calls/${c.uuid}`)}>
              <td className="tabular whitespace-nowrap font-normal">
                <Link href={`/calls/${c.uuid}`} className="hover:underline" onClick={(e) => e.stopPropagation()}>
                  {formatShortDateTime(c.started_at ?? c.created_at)}
                </Link>
              </td>
              {showLead ? (
                <td className="max-w-[240px]">
                  <span className="block truncate font-medium">{callTitle(c)}</span>
                  <span className="block truncate text-xs font-normal text-muted">
                    {[c.lead_company, formatPhone(c.lead_phone, "")].filter(Boolean).join(" · ")}
                  </span>
                </td>
              ) : null}
              <td>
                <ChannelIcon channel={c.channel} />
              </td>
              <td>
                <CallStatusChip status={c.status} />
              </td>
              <td>
                {c.analysis_status === "pending" ? (
                  <span className="inline-flex items-center gap-1 text-xs font-medium text-muted">
                    <LoaderCircle className="spin size-3.5" aria-hidden /> Analysing
                  </span>
                ) : (
                  <InterestBadge interest={c.interest} size="xs" />
                )}
              </td>
              <td>
                <RatingPill rating={c.rating} />
              </td>
              <td className="tabular text-right font-medium">{formatDuration(c.duration_sec)}</td>
              <td className="whitespace-nowrap font-normal">{endReasonLabel(c.end_reason)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
