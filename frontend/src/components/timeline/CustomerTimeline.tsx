"use client";

import Link from "next/link";
import { CalendarClock, ListTree } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import useSWR from "swr";
import { shiftYmd } from "@/components/analytics/range";
import { formatCallback } from "@/components/inbound/caller";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { useBoundedInterval } from "@/hooks/useBoundedInterval";
import { useNow } from "@/hooks/useNow";
import { ApiError, keys } from "@/lib/api";
import {
  formatDate,
  formatDuration,
  formatNumber,
  formatRelative,
  formatShortDateTime,
  isCallActive,
  pluralize,
  ymdIST,
} from "@/lib/format";
import type { LeadTimeline } from "@/lib/types";
import { calledBackUuids, groupByDay } from "./describe";
import { TimelineRow } from "./TimelineRow";

/**
 * Everything that happened with one customer, newest first and grouped by IST day: every call (in, out,
 * missed, browser tests), campaigns they were added to and when the lead was created.
 * `compact` (drawers) hides the summary strip; `limit` shows that many events at a time.
 */
export function CustomerTimeline({
  leadId,
  highlightUuid,
  compact = false,
  limit,
}: {
  leadId: number;
  /** This call is marked "This call" (and always shown, even past `limit`). */
  highlightUuid?: string;
  compact?: boolean;
  limit?: number;
}) {
  const valid = Number.isInteger(leadId) && leadId > 0;
  const [pollEvery, setPollEvery] = useState(0);
  const { data, error, isLoading, mutate } = useSWR<LeadTimeline>(valid ? keys.leadTimeline(leadId) : null, {
    refreshInterval: pollEvery,
  });
  const events = useMemo(() => (Array.isArray(data?.events) ? data.events : []), [data?.events]);
  // The live feed revalidates this key on every call update; polling is only the fallback while something is
  // still running or being analysed (bounded, so a stuck call doesn't poll forever).
  const busy = events.some((e) => e.kind === "call" && (isCallActive(e.call.status) || e.call.analysis_status === "pending"));
  const poll = useBoundedInterval(busy, 5000, 15 * 60_000);
  useEffect(() => setPollEvery(poll.interval), [poll.interval]);

  const step = limit && limit > 0 ? Math.floor(limit) : Number.POSITIVE_INFINITY;
  const [shown, setShown] = useState(step);
  const calledBack = useMemo(() => calledBackUuids(events), [events]);
  const now = useNow(60_000);
  const listLabel = useId();

  if (!valid) return null;
  if (error && !data) {
    if (error instanceof ApiError && error.status === 404) {
      return <p className="text-sm text-muted">This lead no longer exists, so there is no timeline to show.</p>;
    }
    return <ErrorState compact error={error} onRetry={() => void mutate()} title="Couldn't load the timeline" />;
  }
  if (!data) return isLoading ? <TimelineSkeleton compact={compact} /> : null;

  const highlightIndex = highlightUuid ? events.findIndex((e) => e.kind === "call" && e.call.uuid === highlightUuid) : -1;
  const visible = events.slice(0, Math.max(shown, highlightIndex + 1));
  const hidden = events.length - visible.length;
  const more = Number.isFinite(step) ? Math.min(step, hidden) : hidden; // revealed by "Show older"
  const today = ymdIST(new Date(now));
  const groups = groupByDay(visible, today, shiftYmd(today, -1));
  // The call this view is about already shows its own next step.
  const nextStep = data.next_step && data.next_step.call_uuid !== highlightUuid ? data.next_step : null;
  const nextCallback = formatCallback(nextStep?.callback_time);

  return (
    <div className="flex flex-col gap-4">
      {error ? <ErrorState compact error={error} onRetry={() => void mutate()} title="Showing the last loaded timeline" /> : null}
      {!compact && data.stats ? <SummaryStrip stats={data.stats} now={now} /> : null}

      {nextStep && (nextStep.next_action || nextCallback) ? (
        <section aria-label="Next step" className="rounded-xl border border-primary-tint-strong bg-primary-tint p-4">
          <p className="text-[13px] font-medium text-primary">Next step</p>
          {nextStep.next_action ? (
            <p className="text-mixed mt-1 text-[15px] leading-snug font-medium">{nextStep.next_action}</p>
          ) : null}
          {nextCallback ? (
            <p className="mt-1.5 inline-flex items-center gap-1.5 text-[13px] font-medium text-primary">
              <CalendarClock className="size-4 shrink-0" aria-hidden />
              Callback: {nextCallback}
            </p>
          ) : null}
          <p className="mt-2 text-xs text-muted">
            From the conversation on {formatShortDateTime(nextStep.at)} IST ·{" "}
            <Link href={`/calls/${encodeURIComponent(nextStep.call_uuid)}`} className="font-medium text-primary hover:underline">
              Open call
            </Link>
          </p>
        </section>
      ) : null}

      {events.length === 0 ? (
        <EmptyState icon={<ListTree />} title="No history yet" body="Calls and campaigns for this customer will show up here." />
      ) : (
        <div>
          <p id={listLabel} className="mb-3 text-xs text-muted">
            Newest first · times in IST
          </p>
          <div className="flex flex-col gap-4" aria-labelledby={listLabel} role="group">
            {groups.map((g) => (
              <div key={g.key}>
                <p className="mb-2 text-xs font-semibold tracking-wide text-muted uppercase" id={`${listLabel}-${g.key}`}>
                  {g.label}
                </p>
                <ol aria-labelledby={`${listLabel}-${g.key}`}>
                  {g.events.map((e, i) => (
                    <TimelineRow
                      key={e.id}
                      event={e}
                      calledBack={e.kind === "call" && calledBack.has(e.call.uuid)}
                      highlight={!!highlightUuid && e.kind === "call" && e.call.uuid === highlightUuid}
                      compact={compact}
                      last={i === g.events.length - 1}
                    />
                  ))}
                </ol>
              </div>
            ))}
          </div>
          {hidden > 0 ? (
            <Button
              size="sm"
              variant="secondary"
              className="mt-1"
              onClick={() => setShown((s) => Math.max(s, highlightIndex + 1) + (Number.isFinite(step) ? step : hidden))}
            >
              Show {formatNumber(more)} older {more === 1 ? "event" : "events"}
            </Button>
          ) : null}
        </div>
      )}
    </div>
  );
}

function SummaryStrip({ stats, now }: { stats: LeadTimeline["stats"]; now: number }) {
  const items: { label: string; value: string; hint?: string }[] = [
    {
      label: "First contact",
      value: formatDate(stats.first_contact_at),
      hint: stats.first_contact_at ? undefined : "No calls yet",
    },
    {
      label: "Last contact",
      value: stats.last_contact_at ? formatRelative(stats.last_contact_at, now) : "—",
      hint: stats.last_contact_at ? `${formatShortDateTime(stats.last_contact_at)} IST` : undefined,
    },
    {
      label: "Calls",
      value: formatNumber(stats.calls),
      hint: `${formatNumber(stats.inbound)} in · ${formatNumber(stats.outbound)} out`,
    },
    {
      label: "Talk time",
      value: formatDuration(stats.talk_sec),
      hint: pluralize(stats.answered, "answered call"),
    },
  ];
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {items.map((it) => (
        <div key={it.label} className="min-w-0 rounded-lg border border-line bg-canvas px-3 py-2.5">
          <dt className="text-xs text-muted">{it.label}</dt>
          <dd className="tabular mt-0.5 truncate text-sm font-semibold">{it.value}</dd>
          {it.hint ? <dd className="truncate text-xs text-muted">{it.hint}</dd> : null}
        </div>
      ))}
    </dl>
  );
}

function TimelineSkeleton({ compact }: { compact: boolean }) {
  return (
    <div className="flex flex-col gap-4" role="status" aria-label="Loading timeline">
      {!compact ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-[68px] rounded-lg" />
          ))}
        </div>
      ) : null}
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="flex gap-3">
          <Skeleton className="size-8 shrink-0 rounded-full" />
          <div className="flex-1 space-y-2 pt-1">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-3 w-1/2" />
          </div>
        </div>
      ))}
    </div>
  );
}
