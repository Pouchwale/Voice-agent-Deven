"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  CalendarClock,
  CircleAlert,
  CircleCheck,
  CircleX,
  Clock,
  Gauge,
  LoaderCircle,
  MessageSquareWarning,
  PhoneCall,
  PhoneOff,
  Radio,
  RefreshCw,
  Smile,
  Trash2,
  UserRound,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import useSWR, { useSWRConfig } from "swr";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Chip, type ChipColor } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { InterestBadge } from "@/components/ui/InterestBadge";
import { ConfirmDialog } from "@/components/ui/Modal";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { CallStatusChip } from "@/components/ui/StatusChip";
import { useToast } from "@/components/ui/Toast";
import { useBoundedInterval } from "@/hooks/useBoundedInterval";
import { useEntrance } from "@/hooks/useEntrance";
import { api, ApiError, errorMessage, keys } from "@/lib/api";
import { cn } from "@/lib/cn";
import { apiUrl } from "@/lib/config";
import {
  callTitle,
  endReasonLabel,
  formatDateTime,
  formatDuration,
  formatLatency,
  formatNumber,
  formatPhone,
  isCallActive,
  leadName,
} from "@/lib/format";
import type { CallDetail } from "@/lib/types";
import { AudioPlayer, type AudioPlayerHandle } from "./AudioPlayer";
import { ChannelIcon } from "./CallsTable";
import { ChatBubble } from "./ChatBubble";
import { InterestMeter } from "./InterestMeter";
import { QualificationChecklist } from "./Qualification";
import { RatingGauge } from "./RatingGauge";
import { useCallNow } from "./useCallNow";

const SENTIMENT: Record<NonNullable<CallDetail["sentiment"]>, { label: string; color: ChipColor }> = {
  positive: { label: "Positive", color: "primary" },
  neutral: { label: "Neutral", color: "white" },
  negative: { label: "Negative", color: "neutral" },
};

/** callback_time may be an ISO timestamp or free text ("tomorrow 4 pm"). */
function formatCallback(v: string | null): string {
  if (!v) return "—";
  return /^\d{4}-\d{2}-\d{2}T/.test(v) ? `${formatDateTime(v)} IST` : v;
}

function Fact({ icon, label, value }: { icon: ReactNode; label: string; value: ReactNode }) {
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-line bg-white px-3 py-2.5">
      <span className="mt-0.5 shrink-0 text-muted [&_svg]:size-4" aria-hidden>
        {icon}
      </span>
      <div className="min-w-0">
        <p className="text-xs text-muted">{label}</p>
        <p className="tabular mt-0.5 truncate text-sm font-medium">{value}</p>
      </div>
    </div>
  );
}

function AnalysisSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-2.5" aria-hidden>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className={cn("h-4", i === lines - 1 ? "w-2/3" : "w-full")} />
      ))}
    </div>
  );
}

export function CallDetailView({ uuid }: { uuid: string }) {
  const router = useRouter();
  const toast = useToast();
  const { mutate: globalMutate } = useSWRConfig();
  const { callNow, callingId } = useCallNow();
  // Poll while the analysis runs (2 s) or the call is still live (5 s; the live feed also refreshes it),
  // but stop after a while so a stuck "pending" doesn't poll forever.
  const [pollEvery, setPollEvery] = useState(0);
  const { data, error, isLoading, mutate } = useSWR<CallDetail>(uuid ? keys.call(uuid) : null, {
    refreshInterval: pollEvery,
    // The global 4 s dedupe window would silently swallow every other 2 s poll.
    dedupingInterval: 1000,
  });
  const pendingNow = data?.analysis_status === "pending";
  const liveNow = !!data && isCallActive(data.status);
  const poll = useBoundedInterval(pendingNow || liveNow, liveNow ? 5000 : 2000, liveNow ? 75 * 60_000 : 3 * 60_000);
  useEffect(() => setPollEvery(poll.interval), [poll.interval]);
  const [reanalyzing, setReanalyzing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [activeTurnId, setActiveTurnId] = useState<number | null>(null);
  const playerRef = useRef<AudioPlayerHandle>(null);
  const rootRef = useEntrance<HTMLDivElement>(!!data);

  const transcript = useMemo(
    () => [...(Array.isArray(data?.transcript) ? data.transcript : [])].sort((a, b) => a.offset_ms - b.offset_ms),
    [data?.transcript],
  );
  const transcriptRef = useRef(transcript);
  useEffect(() => {
    transcriptRef.current = transcript;
  }, [transcript]);

  // `timeupdate` fires ~4x/s; only re-render (a long transcript) when the highlighted turn actually changes.
  const onPlayerTime = useCallback((ms: number, playing: boolean) => {
    if (!playing && ms <= 0) {
      setActiveTurnId(null);
      return;
    }
    let id: number | null = null;
    for (const t of transcriptRef.current) {
      if (t.role === "system") continue;
      if (t.offset_ms <= ms + 150) id = t.id;
      else break;
    }
    setActiveTurnId(id);
  }, []);

  async function reanalyze() {
    setReanalyzing(true);
    try {
      const d = await api.calls.reanalyze(uuid);
      await mutate(d, { revalidate: false });
      toast({ tone: "success", title: d.analysis_status === "pending" ? "Re-analysis started" : "Summary refreshed" });
    } catch (err) {
      toast({ tone: "error", title: "Couldn't re-analyse", description: errorMessage(err) });
    } finally {
      setReanalyzing(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.calls.remove(uuid);
      toast({ tone: "success", title: "Call deleted" });
      void globalMutate((key) => typeof key === "string" && (key.startsWith("/api/calls") || key.startsWith("/api/dashboard") || key.startsWith("/api/leads")));
      router.push("/calls");
    } catch (err) {
      toast({ tone: "error", title: "Couldn't delete call", description: errorMessage(err) });
      setBusy(false);
    }
  }

  const back = (
    <Link href="/calls" className="mb-3 inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-fg">
      <ArrowLeft className="size-4" aria-hidden /> All calls
    </Link>
  );

  // A 404 wins even over cached data: the call was deleted (e.g. from another tab) while this page was open.
  if (!data || (error instanceof ApiError && error.status === 404)) {
    if (error instanceof ApiError && error.status === 404) {
      return (
        <div>
          {back}
          <EmptyState icon={<PhoneOff />} title="Call not found" body="It may have been deleted." action={<ButtonLink href="/calls">Back to calls</ButtonLink>} />
        </div>
      );
    }
    return (
      <div>
        {back}
        {error ? (
          <ErrorState error={error} onRetry={() => void mutate()} title="Couldn't load this call" />
        ) : isLoading ? (
          <div className="space-y-5" role="status" aria-label="Loading call">
            <Skeleton className="h-12 w-80" />
            <div className="grid gap-5 lg:grid-cols-12">
              <Skeleton className="h-80 rounded-xl lg:col-span-4" />
              <Skeleton className="h-80 rounded-xl lg:col-span-8" />
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  const call = data;
  const pending = call.analysis_status === "pending";
  const live = isCallActive(call.status);
  const recordingSrc = call.recording_url
    ? apiUrl(call.recording_url)
    : call.has_recording
      ? apiUrl(`/api/calls/${encodeURIComponent(call.uuid)}/recording`)
      : null;
  const title =
    call.lead_name?.trim() ||
    call.lead?.name?.trim() ||
    (call.channel === "browser" ? "Browser test call" : callTitle({ ...call, lead_phone: call.lead_phone ?? call.to_number }, "Unknown caller"));
  const callAgainName = call.lead_name?.trim() || (call.lead ? leadName(call.lead) : "lead");
  const avgLatency = call.metrics?.avg_latency_ms ?? null;
  const qualification = Array.isArray(call.qualification) ? call.qualification : [];
  const keyPoints = Array.isArray(call.key_points) ? call.key_points : [];
  const objections = Array.isArray(call.objections) ? call.objections : [];

  return (
    <div ref={rootRef}>
      {back}

      {/* Header */}
      <header className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <p className="mb-1 text-[13px] text-muted">Call summary</p>
          <h1 className="font-heading text-[22px] font-semibold tracking-tight break-words sm:text-2xl">{title}</h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-muted">
            {call.lead_company ? <span>{call.lead_company}</span> : null}
            {call.lead_phone ? <span className="tabular">{formatPhone(call.lead_phone)}</span> : null}
            <span>{formatDateTime(call.started_at ?? call.created_at)} IST</span>
            <ChannelIcon channel={call.channel} />
            <CallStatusChip status={call.status} />
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {call.lead_id ? (
            <ButtonLink href={`/leads/${call.lead_id}`} icon={<UserRound />}>
              Lead
            </ButtonLink>
          ) : null}
          <Button icon={<RefreshCw />} loading={reanalyzing} disabled={live || pending} onClick={() => void reanalyze()}>
            Re-analyze
          </Button>
          {call.lead_id ? (
            <Button
              variant="primary"
              icon={<PhoneCall />}
              loading={callingId === call.lead_id}
              disabled={live}
              onClick={() => void callNow({ id: call.lead_id!, name: callAgainName })}
            >
              Call again
            </Button>
          ) : null}
          <Button variant="danger" icon={<Trash2 />} onClick={() => setDeleting(true)}>
            Delete
          </Button>
        </div>
      </header>

      {/* Status banners */}
      {live ? (
        <div className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-primary-tint-strong bg-primary-tint px-4 py-3" role="status">
          <Radio className="size-4 text-primary" aria-hidden />
          <p className="flex-1 text-sm font-medium">This call is happening right now.</p>
          <ButtonLink href="/live" size="sm">
            Watch live
          </ButtonLink>
        </div>
      ) : pending && poll.expired ? (
        <div className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-line bg-subtle px-4 py-3" role="status">
          <CircleAlert className="size-4 text-fg" aria-hidden />
          <p className="flex-1 text-sm">The analysis is taking much longer than usual. It may have stalled on the backend.</p>
          <Button
            size="sm"
            icon={<RefreshCw />}
            onClick={() => {
              poll.restart();
              void mutate();
            }}
          >
            Check again
          </Button>
        </div>
      ) : pending ? (
        <div className="mb-5 flex items-center gap-3 rounded-xl border border-line bg-white px-4 py-3" role="status" aria-live="polite">
          <LoaderCircle className="spin size-4 text-primary" aria-hidden />
          <p className="text-sm">Analysing this call. The summary, rating and next steps will appear in a few seconds.</p>
        </div>
      ) : call.analysis_status === "failed" ? (
        <div className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-line bg-subtle px-4 py-3" role="alert">
          <CircleAlert className="size-4 text-fg" aria-hidden />
          <p className="flex-1 text-sm font-medium">The analysis failed for this call.</p>
          <Button size="sm" icon={<RefreshCw />} loading={reanalyzing} onClick={() => void reanalyze()}>
            Try again
          </Button>
        </div>
      ) : call.analysis_status === "skipped" ? (
        <div className="mb-5 rounded-xl border border-line bg-white px-4 py-3 text-sm text-muted">
          Not analysed - the call didn&apos;t connect or had nothing to analyse.
        </div>
      ) : null}
      {call.error ? (
        <div className="mb-5 rounded-xl border border-line bg-subtle px-4 py-3 text-sm text-fg" role="alert">
          Error: {call.error}
        </div>
      ) : null}

      {/* Rating + interest */}
      <div className="grid gap-5 lg:grid-cols-12">
        <Card className="flex flex-col items-center justify-center lg:col-span-4">
          {pending ? (
            <div className="flex w-full flex-col items-center gap-3 py-6">
              <Skeleton className="size-40 rounded-full" />
              <Skeleton className="h-5 w-32" />
            </div>
          ) : (
            <RatingGauge rating={call.rating} interest={call.interest} />
          )}
          <div className="mt-4 flex flex-wrap justify-center gap-1.5">
            {call.qualified === null ? null : call.qualified ? (
              <Chip color="primary" size="sm" icon={<CircleCheck aria-hidden />}>
                Qualified
              </Chip>
            ) : (
              <Chip color="white" size="sm" icon={<CircleX aria-hidden />}>
                Not qualified
              </Chip>
            )}
            <InterestBadge interest={call.interest} size="sm" />
            {call.sentiment ? (
              <Chip color={SENTIMENT[call.sentiment].color} size="sm" icon={<Smile aria-hidden />}>
                {SENTIMENT[call.sentiment].label}
              </Chip>
            ) : null}
          </div>
        </Card>

        <div className="flex min-w-0 flex-col gap-5 lg:col-span-8">
          <Card>
            <CardHeader title="Interest level" subtitle="Where the agent placed this lead after the call" />
            {pending ? <Skeleton className="h-14 w-full rounded-lg" /> : <InterestMeter interest={call.interest} />}
          </Card>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            <Fact icon={<Clock />} label="Duration" value={formatDuration(call.duration_sec)} />
            <Fact icon={<PhoneOff />} label="End reason" value={endReasonLabel(call.end_reason)} />
            <Fact icon={<Zap />} label="Avg response" value={formatLatency(avgLatency)} />
            <Fact icon={<Gauge />} label="Turns" value={call.metrics?.turns !== undefined ? formatNumber(call.metrics.turns) : formatNumber(transcript.length)} />
            <Fact
              icon={<Smile />}
              label="Sentiment"
              value={call.sentiment ? SENTIMENT[call.sentiment].label : pending ? "…" : "—"}
            />
            <Fact icon={<CalendarClock />} label="Callback" value={formatCallback(call.callback_time)} />
          </div>
        </div>
      </div>

      {/* Summary + next action */}
      <div className="mt-5 grid gap-5 lg:grid-cols-12">
        <Card className="lg:col-span-7">
          <CardHeader title="Summary" />
          {pending ? (
            <AnalysisSkeleton lines={4} />
          ) : call.summary ? (
            <p className="text-mixed text-[15px] leading-relaxed">{call.summary}</p>
          ) : (
            <p className="text-sm font-normal text-muted">No summary for this call.</p>
          )}

          {keyPoints.length ? (
            <div className="mt-5">
              <p className="field-label">Key points</p>
              <ul className="flex flex-wrap gap-2">
                {keyPoints.map((k, i) => (
                  <li key={i} className="text-mixed rounded-full border border-line bg-canvas px-2.5 py-0.5 text-[13px]">
                    {k}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {objections.length ? (
            <div className="mt-5">
              <p className="field-label">Objections raised</p>
              <ul className="space-y-1.5">
                {objections.map((o, i) => (
                  <li key={i} className="text-mixed flex items-start gap-2 text-sm">
                    <MessageSquareWarning className="mt-0.5 size-4 shrink-0 text-fg" aria-hidden />
                    {o}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </Card>

        <section className="rounded-xl border border-primary-tint-strong bg-primary-tint p-5 lg:col-span-5" aria-labelledby="next-action-title">
          <p id="next-action-title" className="text-[13px] font-medium text-primary">
            Next action for sales
          </p>
          {pending ? (
            <div className="mt-3 space-y-2.5">
              <Skeleton className="h-5 w-full !bg-primary-tint-strong" />
              <Skeleton className="h-5 w-3/4 !bg-primary-tint-strong" />
            </div>
          ) : (
            <p className="text-mixed mt-2 font-heading text-lg leading-snug font-semibold">
              {call.next_action ?? "No follow-up suggested for this call."}
            </p>
          )}
          {call.callback_time ? (
            <p className="mt-3 inline-flex items-center gap-2 text-[13px] font-medium text-primary">
              <CalendarClock className="size-4" aria-hidden />
              Callback: {formatCallback(call.callback_time)}
            </p>
          ) : null}
          {call.lead ? (
            <div className="mt-4 rounded-lg border border-primary-tint-strong bg-white p-3 text-sm">
              <p className="font-medium break-words">{leadName(call.lead)}</p>
              <p className="text-muted">
                {[call.lead.company, call.lead.city, formatPhone(call.lead.phone)].filter(Boolean).join(" · ")}
              </p>
            </div>
          ) : null}
        </section>
      </div>

      {/* Qualification + recording/transcript */}
      <div className="mt-5 grid gap-5 lg:grid-cols-12">
        <Card className="lg:col-span-5">
          <CardHeader title="Qualification" subtitle="Questions from Settings and what the customer said" />
          {pending ? (
            <AnalysisSkeleton lines={6} />
          ) : qualification.length ? (
            <QualificationChecklist items={qualification} />
          ) : (
            <p className="text-sm font-normal text-muted">No qualification data for this call.</p>
          )}
        </Card>

        <Card className="flex min-w-0 flex-col lg:col-span-7">
          <CardHeader
            title="Recording & transcript"
            subtitle={recordingSrc && transcript.length ? "Click any line to jump there in the recording." : undefined}
          />
          {recordingSrc ? (
            <div className="mb-4">
              <AudioPlayer
                ref={playerRef}
                src={recordingSrc}
                fallbackDuration={call.duration_sec}
                onTime={onPlayerTime}
              />
            </div>
          ) : (
            <p className="mb-4 rounded-lg border border-dashed border-line px-3 py-2 text-sm font-normal text-muted">
              {live ? "The recording will be available when the call ends." : "No recording for this call."}
            </p>
          )}
          {transcript.length ? (
            <div className="scroll-thin max-h-[640px] space-y-2.5 overflow-y-auto rounded-lg border border-line bg-canvas p-3" role="log" aria-label="Transcript">
              {transcript.map((t) => (
                <ChatBubble
                  key={t.id}
                  role={t.role}
                  text={t.text}
                  offsetMs={t.offset_ms}
                  interrupted={t.interrupted}
                  active={activeTurnId === t.id}
                  onClick={recordingSrc && t.role !== "system" ? () => playerRef.current?.seekTo(t.offset_ms) : undefined}
                />
              ))}
            </div>
          ) : (
            <p className="text-sm font-normal text-muted">{live ? "Transcript is streaming on the Live page." : "No transcript for this call."}</p>
          )}
        </Card>
      </div>

      <ConfirmDialog
        open={deleting}
        onClose={() => setDeleting(false)}
        onConfirm={() => void remove()}
        title="Delete this call?"
        body="The recording, transcript and summary will be removed. This can't be undone."
        confirmLabel="Delete"
        danger
        busy={busy}
      />
    </div>
  );
}
