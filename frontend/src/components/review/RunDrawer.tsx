"use client";

import { ExternalLink, Lightbulb, Quote } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";
import useSWR from "swr";
import { Chip } from "@/components/ui/Badge";
import { Drawer } from "@/components/ui/Modal";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/States";
import { keys } from "@/lib/api";
import { formatDuration, formatShortDateTime, pluralize } from "@/lib/format";
import type { ReviewCall, ReviewRunDetail, ReviewStats } from "@/lib/types";
import { CAUSE_LABEL, categoryLabel, OUTCOME_LABEL, SEVERITY_LABEL, SUGGESTION_STATUS_LABEL } from "./labels";
import { EvidenceList } from "./SuggestionCard";
import { ValueChange } from "./ValueChange";

export function RunDrawer({ runId, onClose }: { runId: number | null; onClose: () => void }) {
  const { data, error, mutate } = useSWR<ReviewRunDetail>(runId ? keys.reviewRun(runId) : null, {
    revalidateOnFocus: false,
    // while it runs, the live review.update events refetch it; this is a fallback for a dropped socket
    refreshInterval: (latest) => (latest?.status === "running" ? 5000 : 0),
  });
  return (
    <Drawer
      open={runId !== null}
      onClose={onClose}
      title={runId ? `Review #${runId}` : undefined}
      description={
        data
          ? `${data.trigger === "auto" ? "Automatic" : "Run by hand"} · ${pluralize(data.call_count, "call")} · ${data.model} · ${formatShortDateTime(data.created_at)}`
          : undefined
      }
      width="max-w-[760px]"
    >
      <div className="space-y-6 px-5 py-5">
        {error && !data ? (
          <ErrorState error={error} onRetry={() => void mutate()} title="Couldn't load this review" compact />
        ) : !data ? (
          <div className="space-y-3" role="status" aria-label="Loading the review">
            <Skeleton className="h-20 rounded-lg" />
            <Skeleton className="h-40 rounded-lg" />
          </div>
        ) : (
          <RunBody run={data} />
        )}
      </div>
    </Drawer>
  );
}

function RunBody({ run }: { run: ReviewRunDetail }) {
  const report = run.report ?? {};
  if (run.status === "running") {
    return (
      <p className="rounded-lg border border-line bg-canvas p-4 text-sm" role="status">
        <span className="font-semibold">Working: </span>
        {run.stage ?? "starting"}…
      </p>
    );
  }
  if (run.status === "failed") {
    return (
      <div className="rounded-lg border border-fg p-4 text-sm">
        <p className="font-semibold">This review failed</p>
        <p className="text-mixed mt-1">{run.error}</p>
      </div>
    );
  }
  const byUuid = new Map(run.calls.map((c) => [c.call_uuid, c]));
  return (
    <>
      {report.summary ? <p className="text-mixed text-sm leading-relaxed">{report.summary}</p> : null}
      {report.stats ? <StatsGrid stats={report.stats} /> : null}

      <Block title={`Problems proven in ${report.min_evidence ?? 2}+ calls`}>
        {report.patterns?.length ? (
          <ul className="space-y-4">
            {report.patterns.map((p) => (
              <li key={p.id} className="rounded-lg border border-line p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-mixed font-semibold">{p.title}</p>
                  <Chip size="xs">{categoryLabel(p.category)}</Chip>
                  <span className="text-[12px] text-muted">{pluralize(new Set(p.evidence.map((e) => e.call_uuid)).size, "call")}</span>
                </div>
                {p.impact ? <p className="text-mixed mt-1 text-[13px] text-subtle-fg">{p.impact}</p> : null}
                <div className="mt-3">
                  <EvidenceList evidence={p.evidence} max={3} />
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">No problem was proven in enough calls this time.</p>
        )}
      </Block>

      {run.suggestion_items.length ? (
        <Block title="Suggestions from this review">
          <ul className="space-y-3">
            {run.suggestion_items.map((s) => (
              <li key={s.id} className="rounded-lg border border-line p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-mixed font-semibold">{s.title}</p>
                  <Chip size="xs" color="primary">
                    {s.field_label}
                  </Chip>
                  <Chip size="xs" color={s.status === "pending" ? "brand" : "white"}>
                    {SUGGESTION_STATUS_LABEL[s.status]}
                  </Chip>
                </div>
                <ValueChange before={s.base_value} after={s.status === "approved" ? s.applied_value : s.proposed_value} className="mt-2" />
                {s.decision_note ? <p className="mt-2 text-[12px] text-muted">Note: {s.decision_note}</p> : null}
              </li>
            ))}
          </ul>
        </Block>
      ) : null}

      {report.watch_list?.length ? (
        <Block title="Watching (not proven enough yet)">
          <ul className="space-y-2 text-[13px]">
            {report.watch_list.map((w, i) => (
              <li key={i} className="text-mixed">
                <span className="font-medium">{w.title}</span>
                {w.note ? <span className="text-muted"> · {w.note}</span> : null}
              </li>
            ))}
          </ul>
        </Block>
      ) : null}

      {report.team_notes?.length ? (
        <Block title="For the team">
          <ul className="space-y-2 text-[13px]">
            {report.team_notes.map((n, i) => (
              <li key={i} className="text-mixed flex gap-2">
                <Lightbulb className="mt-0.5 size-3.5 shrink-0 text-primary" aria-hidden />
                {n}
              </li>
            ))}
          </ul>
        </Block>
      ) : null}

      {report.impact_of_recent_changes ? (
        <Block title="Effect of recent settings changes">
          <p className="text-mixed text-[13px]">{report.impact_of_recent_changes}</p>
        </Block>
      ) : null}

      <Block title={`Each call (${run.calls.length})`}>
        <ul className="space-y-3">
          {(report.calls ?? []).map((ref) => {
            const call = byUuid.get(ref.call_uuid);
            return call ? <CallVerdict key={ref.call_uuid} call={call} /> : null;
          })}
          {/* calls not in the report index (e.g. an older report shape) */}
          {run.calls
            .filter((c) => !(report.calls ?? []).some((r) => r.call_uuid === c.call_uuid))
            .map((c) => (
              <CallVerdict key={c.call_uuid} call={c} />
            ))}
        </ul>
      </Block>

      {report.proof?.removed?.length ? (
        <details className="rounded-lg border border-line p-3 text-[13px]">
          <summary className="cursor-pointer font-medium">
            Removed by the proof check ({report.proof.removed.length})
          </summary>
          <ul className="text-mixed mt-2 list-disc space-y-1 pl-5 text-muted">
            {report.proof.removed.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 font-heading text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function StatsGrid({ stats }: { stats: ReviewStats }) {
  const tiles: { label: string; value: string }[] = [
    { label: "Lost because of the agent", value: `${stats.lost_because_of_agent} of ${stats.calls}` },
    { label: "Customer's own reason", value: String(stats.lost_customer_side) },
    { label: "Cause not proven / technical", value: String(stats.lost_unclear_or_technical) },
    { label: "Hung up during an agent reply", value: String(stats.hung_up_during_agent_reply) },
    { label: "Words per agent reply (avg)", value: stats.avg_agent_words_per_reply == null ? "—" : String(stats.avg_agent_words_per_reply) },
    { label: "Replies over 35 words", value: stats.replies_over_35_words_pct == null ? "—" : `${stats.replies_over_35_words_pct}%` },
    { label: "Wait for an answer (avg)", value: stats.avg_reply_gap_s == null ? "—" : `${stats.avg_reply_gap_s} s` },
    { label: "Agent's share of talking", value: stats.avg_agent_talk_share == null ? "—" : `${Math.round(stats.avg_agent_talk_share * 100)}%` },
  ];
  return (
    <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {tiles.map((t) => (
        <div key={t.label} className="rounded-lg border border-line p-2.5">
          <dt className="text-[11px] leading-tight text-muted">{t.label}</dt>
          <dd className="tabular mt-1 font-heading text-lg font-semibold">{t.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function CallVerdict({ call }: { call: ReviewCall }) {
  const ev = call.evaluation;
  const [open, setOpen] = useState(ev.loss_cause === "agent");
  return (
    <li className="rounded-lg border border-line p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="font-medium">{call.lead}</span>
          <Chip size="xs" color={ev.outcome === "converted" || ev.outcome === "interested" ? "primary" : "neutral"}>
            {OUTCOME_LABEL[ev.outcome] ?? ev.outcome}
          </Chip>
          {ev.intent_lost && ev.loss_cause ? (
            <Chip size="xs" color={ev.loss_cause === "agent" ? "dark" : "white"}>
              {CAUSE_LABEL[ev.loss_cause]}
            </Chip>
          ) : null}
        </div>
        <span className="text-[12px] text-muted">
          {call.direction === "inbound" ? "Inbound" : "Outbound"} · {formatDuration(call.duration_sec)} · {formatShortDateTime(call.call_at)}
        </span>
      </div>
      {ev.verdict ? <p className="text-mixed mt-2 text-[13px]">{ev.verdict}</p> : null}
      {ev.intent_lost && ev.lost_at ? (
        <p className="mt-1 text-[12px] text-subtle-fg">
          Customer lost at {ev.lost_at} ({ev.lost_at_time})
          {ev.loss_cause === "customer_side" && ev.customer_side_reason ? ` · ${ev.customer_side_reason}` : ""}
        </p>
      ) : null}
      <div className="mt-2 flex flex-wrap items-center gap-3">
        {ev.issues.length || ev.strengths.length ? (
          <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="text-[12px] font-medium text-primary hover:underline">
            {open ? "Hide details" : `Details (${pluralize(ev.issues.length, "mistake")})`}
          </button>
        ) : null}
        <Link
          href={`/calls/${encodeURIComponent(call.call_uuid)}`}
          className="inline-flex items-center gap-1 text-[12px] font-medium text-primary hover:underline [&_svg]:size-3"
        >
          Open call <ExternalLink aria-hidden />
        </Link>
      </div>
      {open ? (
        <div className="mt-3 space-y-3">
          {ev.issues.map((i, n) => (
            <div key={n} className="rounded-lg bg-canvas p-2.5 text-[13px]">
              <p className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">{categoryLabel(i.category)}</span>
                <span className="text-[12px] text-muted">
                  {i.turn} · {i.at} · {SEVERITY_LABEL[i.severity] ?? ""}
                </span>
              </p>
              {i.quote ? (
                <p className="text-mixed mt-1 flex gap-1.5">
                  <Quote className="mt-1 size-3 shrink-0 text-muted" aria-hidden />“{i.quote}”
                </p>
              ) : null}
              {i.facts.length ? (
                <ul className="mt-1 space-y-0.5 text-[12px] text-subtle-fg">
                  {i.facts.map((fid) => {
                    const f = call.facts.find((x) => x.id === fid);
                    return f ? <li key={fid}>Measured: {f.text}</li> : null;
                  })}
                </ul>
              ) : null}
              {i.earlier && i.earlier_text ? (
                <p className="text-mixed mt-1 text-[12px] text-subtle-fg">
                  {i.category === "ignored_question" ? "The customer had asked" : "Already asked or answered"} at {i.earlier}: “
                  {i.earlier_text}”
                </p>
              ) : null}
              {i.why ? <p className="text-mixed mt-1 text-subtle-fg">{i.why}</p> : null}
              {i.customer_reaction ? <p className="text-mixed mt-1 text-[12px] text-muted">Customer: {i.customer_reaction}</p> : null}
            </div>
          ))}
          {ev.strengths.length ? (
            <div className="text-[13px]">
              <p className="font-semibold">What worked</p>
              <ul className="text-mixed mt-1 space-y-1">
                {ev.strengths.map((st, n) => (
                  <li key={n}>
                    “{st.quote}” <span className="text-muted">({st.at})</span> {st.why ? <span className="text-subtle-fg">· {st.why}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {ev.proof?.removed?.length ? (
            <p className="text-mixed text-[12px] text-muted">Removed by the proof check: {ev.proof.removed.join("; ")}</p>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}
