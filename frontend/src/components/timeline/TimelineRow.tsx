"use client";

import Link from "next/link";
import { CalendarClock, LoaderCircle } from "lucide-react";
import { useId, useState } from "react";
import { FollowUpChip, ReasonChip } from "@/components/inbound/chips";
import { formatCallback } from "@/components/inbound/caller";
import { Chip } from "@/components/ui/Badge";
import { InterestBadge } from "@/components/ui/InterestBadge";
import { RatingPill } from "@/components/ui/RatingPill";
import { CallStatusChip, CampaignStatusChip } from "@/components/ui/StatusChip";
import { cn } from "@/lib/cn";
import { formatDateTime, formatTime } from "@/lib/format";
import type { TimelineCallEvent, TimelineEvent } from "@/lib/types";
import { describeCall, describeOther, TONE_CLASS } from "./describe";

/** One event on the customer timeline: icon on the rail, title + IST time, chips, summary, next step, link. */
export function TimelineRow({
  event,
  calledBack,
  highlight,
  compact,
  last,
}: {
  event: TimelineEvent;
  calledBack: boolean;
  highlight: boolean;
  compact: boolean;
  /** Last row of its day: no rail line below the icon. */
  last: boolean;
}) {
  const look = event.kind === "call" ? describeCall(event.call, calledBack) : describeOther(event);
  const Icon = look.icon;

  return (
    <li className="relative flex gap-3">
      {!last ? <span className="absolute top-9 bottom-0 left-4 w-px -translate-x-1/2 bg-line" aria-hidden /> : null}
      <span
        className={cn(
          "relative mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-full border [&_svg]:size-4",
          TONE_CLASS[look.tone],
        )}
        aria-hidden
      >
        <Icon />
      </span>
      <div
        className={cn(
          "min-w-0 flex-1",
          highlight ? "mb-4 rounded-lg border border-primary-tint-strong bg-primary-tint px-3 py-2" : "pt-1 pb-5",
        )}
      >
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
          <p className="text-mixed min-w-0 text-sm font-medium">{look.title}</p>
          <time dateTime={event.at} className="tabular shrink-0 text-xs text-muted">
            {formatTime(event.at)}
          </time>
        </div>
        {event.kind === "call" ? (
          <CallDetails event={event} highlight={highlight} compact={compact} />
        ) : event.kind === "campaign_added" ? (
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <CampaignStatusChip status={event.campaign_status} />
            <Link href="/campaigns" className="text-[13px] font-medium text-primary hover:underline">
              Campaigns
            </Link>
          </div>
        ) : null}
      </div>
    </li>
  );
}

function CallDetails({ event, highlight, compact }: { event: TimelineCallEvent; highlight: boolean; compact: boolean }) {
  const { call } = event;
  const [expanded, setExpanded] = useState(false);
  const summaryId = useId();
  const summary = call.summary?.trim() || null;
  const keyPoints = Array.isArray(event.key_points) ? event.key_points.filter(Boolean).slice(0, 5) : [];
  const nextAction = event.next_action?.trim() || null;
  const callback = formatCallback(event.callback_time);
  // Only offer "Show more" when there is something hidden: a long summary or key points.
  const expandable = !!summary && (summary.length > (compact ? 110 : 170) || keyPoints.length > 0);

  return (
    <>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        {highlight ? (
          <Chip size="xs" color="primary-solid">
            This call
          </Chip>
        ) : null}
        <CallStatusChip status={call.status} direction={call.direction} />
        {call.analysis_status === "pending" ? (
          <Chip size="xs" color="white" icon={<LoaderCircle className="spin" aria-hidden />}>
            Analysing
          </Chip>
        ) : null}
        {call.analysis_status === "done" && call.interest !== "unknown" ? <InterestBadge interest={call.interest} size="xs" /> : null}
        {call.rating !== null && call.rating !== undefined ? <RatingPill rating={call.rating} /> : null}
        <ReasonChip reason={call.call_reason} />
        {call.direction === "inbound" ? <FollowUpChip value={call.follow_up} /> : null}
      </div>

      {summary ? (
        <div className="mt-2">
          <p
            id={summaryId}
            className={cn("text-mixed text-[13px] text-subtle-fg", !expanded && (compact ? "line-clamp-2" : "line-clamp-3"))}
          >
            {summary}
          </p>
          {expanded && keyPoints.length ? (
            <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Key points">
              {keyPoints.map((k, i) => (
                <li key={i} className="text-mixed rounded-full border border-line bg-canvas px-2 py-px text-xs">
                  {k}
                </li>
              ))}
            </ul>
          ) : null}
          {expandable ? (
            <button
              type="button"
              className="mt-0.5 text-xs font-medium text-primary hover:underline"
              aria-expanded={expanded}
              aria-controls={summaryId}
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? "Show less" : "Show more"}
            </button>
          ) : null}
        </div>
      ) : null}

      {nextAction || callback ? (
        <p className="mt-2 flex items-start gap-1.5 text-[13px]">
          <CalendarClock className="mt-0.5 size-3.5 shrink-0 text-primary" aria-hidden />
          <span className="text-mixed min-w-0">
            <span className="font-medium">Next step:</span>{" "}
            {[nextAction, callback ? `callback ${callback}` : null].filter(Boolean).join(" · ")}
          </span>
        </p>
      ) : null}

      {!highlight ? (
        <Link
          href={`/calls/${encodeURIComponent(call.uuid)}`}
          className="mt-1.5 inline-flex text-[13px] font-medium text-primary hover:underline"
        >
          Open call<span className="sr-only"> from {formatDateTime(event.at)} IST</span>
        </Link>
      ) : null}
    </>
  );
}
