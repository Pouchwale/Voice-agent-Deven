"use client";

import Link from "next/link";
import { ArrowRight, CalendarClock, PhoneCall, PhoneOff, UserRound } from "lucide-react";
import { useId } from "react";
import useSWR from "swr";
import { useCallNow } from "@/components/calls/useCallNow";
import { CustomerTimeline } from "@/components/timeline/CustomerTimeline";
import { Button, ButtonLink } from "@/components/ui/Button";
import { InterestBadge } from "@/components/ui/InterestBadge";
import { Drawer } from "@/components/ui/Modal";
import { RatingPill } from "@/components/ui/RatingPill";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { CallStatusChip } from "@/components/ui/StatusChip";
import { ApiError, keys } from "@/lib/api";
import { callOutcome, endReasonLabel, formatDateTime, formatDuration, formatPhone, telHref } from "@/lib/format";
import type { CallDetail, CallListItem } from "@/lib/types";
import { callBackBlock, callerTitle, customerNumber, formatCallback, missedReason, rawCallerId } from "./caller";
import { CallerChip, FollowUpChip, ReasonChip } from "./chips";
import { useLeadOnLiveCall } from "./useLiveInbound";

/**
 * One inbound call, opened from the table, the toast or a shared link (?call=<uuid>): who called, what happened,
 * the next step, "Call back", and the customer's timeline.
 */
export function InboundCallDrawer({
  uuid,
  fallback,
  onClose,
}: {
  uuid: string | null;
  /** The table row, if loaded: shown at once, and the only source of follow_up (detail responses leave it null). */
  fallback?: CallListItem;
  onClose: () => void;
}) {
  const { data, error, isLoading, mutate } = useSWR<CallDetail>(uuid ? keys.call(uuid) : null, { dedupingInterval: 1000 });
  const { callNow, callingId, outboundHint } = useCallNow();
  const reasonId = useId();

  const detail = data && data.uuid === uuid ? data : undefined;
  const call: CallListItem | undefined = detail ?? (fallback && fallback.uuid === uuid ? fallback : undefined);
  const notFound = error instanceof ApiError && error.status === 404;
  // The caller chip below says "Hidden number", so the title doesn't repeat it.
  const title = call ? callerTitle(call, "Unknown caller", "Unknown caller") : "Inbound call";
  const number = call ? customerNumber(call) : null;
  const raw = call ? rawCallerId(call) : null;
  const leadId = call?.lead_id ?? null;
  const onLiveCall = useLeadOnLiveCall(leadId);
  const block = call ? callBackBlock({ call, leadStatus: detail?.lead?.status ?? null, onLiveCall, outboundHint }) : null;
  const followUp = call?.follow_up ?? (fallback && fallback.uuid === uuid ? fallback.follow_up : null);
  const nextAction = call?.next_action?.trim() || null;
  const callback = formatCallback(call?.callback_time);
  const outcome = call ? callOutcome(call) : null;

  return (
    <Drawer
      open={!!uuid}
      onClose={onClose}
      title={title}
      description={
        call
          ? [
              call.lead_company,
              number && formatPhone(number) !== title ? formatPhone(number) : null,
              `${formatDateTime(call.started_at ?? call.created_at)} IST`,
            ]
              .filter(Boolean)
              .join(" · ")
          : undefined
      }
      width="max-w-[560px]"
      footer={
        call ? (
          <div className="flex flex-wrap gap-2">
            <ButtonLink href={`/calls/${encodeURIComponent(call.uuid)}`} size="sm" iconRight={<ArrowRight />}>
              Full call details
            </ButtonLink>
            {leadId ? (
              <ButtonLink href={`/leads/${leadId}`} size="sm" variant="ghost" icon={<UserRound />}>
                Open lead
              </ButtonLink>
            ) : null}
          </div>
        ) : undefined
      }
    >
      {notFound ? (
        <EmptyState icon={<PhoneOff />} title="Call not found" body="It may have been deleted." />
      ) : !call ? (
        error ? (
          <div className="p-5">
            <ErrorState error={error} onRetry={() => void mutate()} title="Couldn't load this call" />
          </div>
        ) : isLoading ? (
          <div className="space-y-4 p-5" role="status" aria-label="Loading call">
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="h-24 w-full rounded-lg" />
            <Skeleton className="h-40 w-full rounded-lg" />
          </div>
        ) : null
      ) : (
        <div className="flex flex-col gap-5 p-5">
          <div className="flex flex-wrap items-center gap-1.5">
            <CallerChip type={call.caller_type} size="sm" />
            <CallStatusChip status={call.status} direction={call.direction} />
            <ReasonChip reason={call.call_reason} size="sm" />
            {call.analysis_status === "done" && call.interest !== "unknown" ? <InterestBadge interest={call.interest} size="sm" /> : null}
            {call.rating !== null ? <RatingPill rating={call.rating} /> : null}
            {outcome === "missed" ? <FollowUpChip value={followUp} size="sm" /> : null}
          </div>

          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="primary"
                icon={<PhoneCall />}
                loading={!!leadId && callingId === leadId}
                disabled={!!block}
                aria-describedby={block ? reasonId : undefined}
                onClick={() => {
                  if (leadId) void callNow({ id: leadId, name: title });
                }}
              >
                Call back
              </Button>
              {number ? (
                <a href={telHref(number)} className="tabular text-sm font-medium text-primary hover:underline">
                  {formatPhone(number)}
                </a>
              ) : null}
            </div>
            {block ? (
              <p id={reasonId} className="text-[13px] text-muted">
                {block}
                {block === outboundHint ? (
                  <>
                    {" "}
                    <Link href="/settings#calling" className="font-medium text-primary hover:underline">
                      Open Settings
                    </Link>
                  </>
                ) : null}
              </p>
            ) : (
              <p className="text-[13px] text-muted">The AI agent calls them right away from your Vobiz number.</p>
            )}
            {raw ? <p className="text-[13px] text-muted">Caller ID sent by the network: {raw}</p> : null}
          </div>

          <section aria-labelledby={`${reasonId}-this`} className="rounded-xl border border-line bg-canvas p-4">
            <h3 id={`${reasonId}-this`} className="text-[13px] font-medium text-muted">
              This call
            </h3>
            <p className="mt-1 text-sm">
              {outcome === "missed"
                ? ["Missed", missedReason(call) ?? (call.end_reason ? endReasonLabel(call.end_reason) : null)].filter(Boolean).join(" · ")
                : outcome === "live"
                  ? "In progress right now"
                  : [`Talked ${formatDuration(call.duration_sec)}`, call.end_reason ? endReasonLabel(call.end_reason) : null]
                      .filter(Boolean)
                      .join(" · ")}
            </p>
            {call.analysis_status === "pending" ? (
              <p className="mt-2 text-sm text-muted">Analysing the call. The summary appears in a few seconds.</p>
            ) : call.summary ? (
              <p className="text-mixed mt-2 text-[15px] leading-relaxed">{call.summary}</p>
            ) : null}
          </section>

          {nextAction || callback ? (
            <section aria-label="Next step" className="rounded-xl border border-primary-tint-strong bg-primary-tint p-4">
              <p className="text-[13px] font-medium text-primary">Next step</p>
              {nextAction ? <p className="text-mixed mt-1 font-medium leading-snug">{nextAction}</p> : null}
              {callback ? (
                <p className="mt-1.5 inline-flex items-center gap-1.5 text-[13px] font-medium text-primary">
                  <CalendarClock className="size-4 shrink-0" aria-hidden />
                  Callback: {callback}
                </p>
              ) : null}
            </section>
          ) : null}

          {leadId ? (
            <section aria-labelledby={`${reasonId}-timeline`}>
              <h3 id={`${reasonId}-timeline`} className="mb-3 font-heading text-sm font-semibold">
                Customer timeline
              </h3>
              <CustomerTimeline leadId={leadId} highlightUuid={call.uuid} compact limit={6} />
            </section>
          ) : (
            <p className="rounded-lg border border-dashed border-line px-3 py-2 text-[13px] text-muted">
              {call.caller_type === "anonymous"
                ? "The caller hid their number, so this call isn't linked to a lead. If the agent got a number during the call, it's in the summary."
                : "This call isn't linked to a lead, so there's no timeline yet."}
            </p>
          )}
        </div>
      )}
    </Drawer>
  );
}
