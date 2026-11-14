"use client";

import { CalendarClock, LoaderCircle } from "lucide-react";
import { memo } from "react";
import { InterestBadge } from "@/components/ui/InterestBadge";
import { RatingPill } from "@/components/ui/RatingPill";
import { CallStatusChip } from "@/components/ui/StatusChip";
import { cn } from "@/lib/cn";
import { callOutcome, endReasonLabel, formatDuration, formatPhone, formatShortDateTime } from "@/lib/format";
import type { CallListItem } from "@/lib/types";
import { customerNumber, formatCallback, missedReason } from "./caller";
import { CallerChip, FollowUpChip, ReasonChip } from "./chips";

/** Second line under the outcome chip: why it was missed, or how an answered call ended. */
function outcomeDetail(c: CallListItem): string | null {
  const outcome = callOutcome(c);
  if (outcome === "live") return c.answered_at ? "On the call now" : "Picking up";
  if (outcome === "missed") return missedReason(c);
  return c.end_reason ? endReasonLabel(c.end_reason) : null;
}

/**
 * Inbound call log. Selecting a row (click, or the time button for keyboard users) opens the call drawer.
 * Memoised: typing in the search box or a live-feed tick must not re-render up to 50 rows.
 */
export const InboundCallsTable = memo(function InboundCallsTable({
  calls,
  selectedUuid,
  onOpen,
  dim = false,
}: {
  calls: CallListItem[];
  selectedUuid: string | null;
  onOpen: (call: CallListItem) => void;
  dim?: boolean;
}) {
  return (
    <div className={cn("relative overflow-x-auto transition-opacity", dim && "opacity-70")}>
      <table className="data-table min-w-[1120px]">
        <caption className="sr-only">Inbound calls, newest first. Select a call to see details and call back.</caption>
        <thead>
          <tr>
            <th scope="col">Time (IST)</th>
            <th scope="col">Caller</th>
            <th scope="col">Reason</th>
            <th scope="col" className="text-right">
              Duration
            </th>
            <th scope="col">Outcome</th>
            <th scope="col">Interest</th>
            <th scope="col">Rating</th>
            <th scope="col">Next step</th>
            <th scope="col">Follow-up</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((c) => {
            const name = c.lead_name?.trim() || "Unknown caller";
            const number = customerNumber(c); // null for hidden numbers: the caller chip says so
            const detail = outcomeDetail(c);
            const callback = formatCallback(c.callback_time);
            const nextAction = c.next_action?.trim() || null;
            return (
              <tr
                key={c.uuid}
                className="row-link"
                data-selected={selectedUuid === c.uuid ? "true" : undefined}
                onClick={() => onOpen(c)}
              >
                <td className="tabular whitespace-nowrap font-normal">
                  <button
                    type="button"
                    className="rounded-sm text-left hover:underline"
                    onClick={(e) => {
                      e.stopPropagation();
                      onOpen(c);
                    }}
                    aria-label={`${formatShortDateTime(c.started_at ?? c.created_at)} IST, ${name}: open details`}
                  >
                    {formatShortDateTime(c.started_at ?? c.created_at)}
                  </button>
                </td>
                <td className="max-w-[260px]">
                  <span className="block truncate font-medium">{name}</span>
                  <span className="mt-0.5 flex min-w-0 items-center gap-1.5">
                    {number ? <span className="tabular truncate text-xs font-normal text-muted">{formatPhone(number)}</span> : null}
                    <CallerChip type={c.caller_type} />
                  </span>
                </td>
                <td>
                  {c.call_reason ? (
                    <ReasonChip reason={c.call_reason} />
                  ) : c.analysis_status === "pending" ? (
                    <span className="text-xs text-muted">…</span>
                  ) : (
                    <span className="text-muted">—</span>
                  )}
                </td>
                <td className="tabular text-right font-medium">{formatDuration(c.duration_sec)}</td>
                <td>
                  <span className="flex flex-col items-start gap-0.5">
                    <CallStatusChip status={c.status} direction={c.direction} />
                    {detail ? <span className="text-xs whitespace-nowrap text-muted">{detail}</span> : null}
                  </span>
                </td>
                <td>
                  {c.analysis_status === "pending" ? (
                    <span className="inline-flex items-center gap-1 text-xs font-medium text-muted">
                      <LoaderCircle className="spin size-3.5" aria-hidden /> Analysing
                    </span>
                  ) : c.analysis_status === "done" ? (
                    <InterestBadge interest={c.interest} size="xs" />
                  ) : (
                    <span className="text-muted">—</span>
                  )}
                </td>
                <td>
                  <RatingPill rating={c.rating} />
                </td>
                <td className="max-w-[280px]">
                  {nextAction || callback ? (
                    <span className="block">
                      {nextAction ? <span className="text-mixed line-clamp-2 text-[13px]">{nextAction}</span> : null}
                      {callback ? (
                        <span className="mt-0.5 flex items-center gap-1 text-xs font-medium text-primary">
                          <CalendarClock className="size-3.5 shrink-0" aria-hidden />
                          <span className="truncate">{callback}</span>
                        </span>
                      ) : null}
                    </span>
                  ) : (
                    <span className="text-muted">—</span>
                  )}
                </td>
                <td>{c.follow_up ? <FollowUpChip value={c.follow_up} /> : <span className="text-muted">—</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
});
