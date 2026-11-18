"use client";

import { ClipboardCheck, Play, Scale, Settings2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { Chip, Dot } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { useToast } from "@/components/ui/Toast";
import { useEntrance } from "@/hooks/useEntrance";
import { api, errorMessage, keys } from "@/lib/api";
import { formatShortDateTime, formatTime, pluralize } from "@/lib/format";
import type { ReviewMethod, ReviewRunSummary, ReviewStatus, ReviewSuggestion } from "@/lib/types";
import { categoryLabel, SUGGESTION_STATUS_LABEL } from "./labels";
import { RunDrawer } from "./RunDrawer";
import { SuggestionCard, useRefreshReview } from "./SuggestionCard";

const LIMITS = ["5", "10", "15"] as const;

export function ReviewView() {
  const toast = useToast();
  const refresh = useRefreshReview();
  const status = useSWR<ReviewStatus>(keys.reviewStatus);
  const pending = useSWR<ReviewSuggestion[]>(keys.reviewSuggestions("pending"));
  const runs = useSWR<ReviewRunSummary[]>(keys.reviewRuns);
  const [limit, setLimit] = useState<(typeof LIMITS)[number]>("10");
  const [starting, setStarting] = useState(false);
  const [openRun, setOpenRun] = useState<number | null>(null);
  const st = status.data;
  const rootRef = useEntrance<HTMLDivElement>(!!st || !!status.error);

  async function runNow() {
    setStarting(true);
    try {
      const run = await api.review.start(Number(limit));
      toast({ tone: "success", title: "Review started", description: `Reviewing ${pluralize(run.call_count, "call")}. This takes a minute or two.` });
      await refresh();
    } catch (err) {
      toast({ tone: "error", title: "Couldn't start a review", description: errorMessage(err) });
    } finally {
      setStarting(false);
    }
  }

  const running = st?.running ?? null;
  return (
    <div ref={rootRef} className="space-y-6">
      <PageHeader
        title="Call review"
        description="A second AI reviews finished calls, finds where and why customers were lost (with quotes from the calls as proof) and suggests changes to the script. Nothing changes until you approve it."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Select<(typeof LIMITS)[number]>
              aria-label="How many recent calls to review"
              value={limit}
              onChange={setLimit}
              options={LIMITS.map((n) => ({ value: n, label: `Last ${n} calls` }))}
            />
            <Button
              variant="primary"
              icon={<Play />}
              onClick={() => void runNow()}
              loading={starting}
              disabled={!!running || st?.openai_ready === false}
            >
              Run review now
            </Button>
          </div>
        }
      />

      {status.error && !st ? (
        <ErrorState error={status.error} onRetry={() => void status.mutate()} title="Couldn't load the review agent" />
      ) : !st ? (
        <Skeleton className="h-24 rounded-xl" />
      ) : (
        <StatusCard st={st} onOpenRun={setOpenRun} />
      )}

      <section aria-labelledby="pending-heading" className="space-y-3">
        <h2 id="pending-heading" className="font-heading text-base font-semibold">
          Waiting for your decision{pending.data?.length ? ` (${pending.data.length})` : ""}
        </h2>
        {pending.error && !pending.data ? (
          <ErrorState error={pending.error} onRetry={() => void pending.mutate()} title="Couldn't load the suggestions" compact />
        ) : !pending.data ? (
          <Skeleton className="h-48 rounded-xl" />
        ) : pending.data.length ? (
          pending.data.map((s) => <SuggestionCard key={s.id} s={s} onOpenRun={setOpenRun} />)
        ) : (
          <EmptyState
            icon={<ClipboardCheck />}
            title="Nothing waiting"
            body={
              st && st.enabled
                ? `New suggestions appear here after a review. The next automatic review runs after ${pluralize(Math.max(0, st.every - st.new_calls), "more call")}.`
                : "New suggestions appear here after a review. Automatic reviews are off: use “Run review now”."
            }
          />
        )}
      </section>

      <RunsCard runs={runs.data} error={runs.error} onRetry={() => void runs.mutate()} onOpen={setOpenRun} />
      <DecisionsCard onOpenRun={setOpenRun} />
      <MethodCard />

      <RunDrawer runId={openRun} onClose={() => setOpenRun(null)} />
    </div>
  );
}

function StatusCard({ st, onOpenRun }: { st: ReviewStatus; onOpenRun: (id: number) => void }) {
  const running = st.running;
  return (
    <Card className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto]">
      <div className="min-w-0 space-y-1.5 text-sm">
        {!st.openai_ready ? (
          <p className="font-semibold">OPENAI_API_KEY is not set in backend/.env: the review agent can&apos;t run.</p>
        ) : running ? (
          <p className="flex items-center gap-2 font-semibold" role="status">
            <Dot live className="bg-brand" />
            Reviewing {pluralize(running.call_count, "call")}: {running.stage ?? "starting"}…
            <button type="button" onClick={() => onOpenRun(running.id)} className="font-medium text-primary hover:underline">
              Open
            </button>
          </p>
        ) : st.enabled ? (
          <p className="font-semibold">
            Automatic reviews are on: every {pluralize(st.every, "call")} ({st.new_calls} of {st.every} waiting).
          </p>
        ) : (
          <p className="font-semibold">Automatic reviews are off. “Run review now” still works.</p>
        )}
        {st.auto_paused_until && !running ? (
          <p className="text-[13px]">
            The last review failed, so automatic reviews wait until {formatTime(st.auto_paused_until)}. Check the error below, or run one now.
          </p>
        ) : null}
        <p className="text-[13px] text-muted">
          Model: <span className="font-medium text-fg">{st.model}</span> · proof needed: same problem in {pluralize(st.min_evidence, "call")} ·{" "}
          {st.include_inbound ? "outbound and inbound calls" : "outbound calls only"}
        </p>
        {st.last_run ? (
          <p className="text-[13px] text-muted">
            Last review: {formatShortDateTime(st.last_run.finished_at ?? st.last_run.created_at)} ·{" "}
            {st.last_run.status === "failed" ? (
              <span className="font-medium text-fg">failed: {st.last_run.error}</span>
            ) : (
              <>
                {pluralize(st.last_run.call_count, "call")}, {pluralize(st.last_run.patterns, "proven problem")},{" "}
                {pluralize(st.last_run.suggestions, "suggestion")}
              </>
            )}{" "}
            <button type="button" onClick={() => onOpenRun(st.last_run!.id)} className="font-medium text-primary hover:underline">
              Open
            </button>
          </p>
        ) : null}
      </div>
      <Link
        href="/settings#review-agent"
        className="inline-flex h-9 items-center gap-2 self-start rounded-lg border border-line bg-white px-3 text-[13px] font-medium hover:bg-canvas [&_svg]:size-4"
      >
        <Settings2 aria-hidden /> Review settings
      </Link>
    </Card>
  );
}

function RunsCard({
  runs,
  error,
  onRetry,
  onOpen,
}: {
  runs: ReviewRunSummary[] | undefined;
  error: unknown;
  onRetry: () => void;
  onOpen: (id: number) => void;
}) {
  return (
    <Card>
      <CardHeader title="Reviews" subtitle="Every review with its findings. Open one to see each call's verdict and the proof." />
      {error && !runs ? (
        <ErrorState error={error} onRetry={onRetry} title="Couldn't load the reviews" compact />
      ) : !runs ? (
        <Skeleton className="h-24 rounded-lg" />
      ) : !runs.length ? (
        <p className="text-sm text-muted">No reviews yet.</p>
      ) : (
        <ul className="divide-y divide-line">
          {runs.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => onOpen(r.id)}
                className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 py-3 text-left hover:bg-canvas sm:px-2"
              >
                <span className="tabular w-12 shrink-0 text-[13px] font-semibold">#{r.id}</span>
                <span className="min-w-0 flex-1 text-[13px]">
                  <span className="text-mixed line-clamp-2">
                    {r.status === "failed" ? `Failed: ${r.error ?? ""}` : r.status === "running" ? `${r.stage ?? "Running"}…` : r.summary || "Done"}
                  </span>
                </span>
                <span className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
                  <Chip size="xs" color={r.status === "failed" ? "dark" : r.status === "running" ? "brand" : "white"}>
                    {r.status === "done" ? "Done" : r.status === "running" ? "Running" : "Failed"}
                  </Chip>
                  {r.trigger === "auto" ? "Automatic" : "By hand"} · {pluralize(r.call_count, "call")} ·{" "}
                  {r.status === "done" ? `${pluralize(r.suggestions, "suggestion")} · ` : ""}
                  {formatShortDateTime(r.created_at)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function DecisionsCard({ onOpenRun }: { onOpenRun: (id: number) => void }) {
  const { data } = useSWR<ReviewSuggestion[]>(keys.reviewSuggestions());
  const decided = (data ?? []).filter((s) => s.status !== "pending").slice(0, 12);
  if (!decided.length) return null;
  return (
    <Card>
      <CardHeader
        title="Decided suggestions"
        subtitle={
          <>
            Approved changes can be undone under{" "}
            <Link href="/settings#history" className="font-medium text-primary hover:underline">
              Settings → Change history
            </Link>
            .
          </>
        }
      />
      <ul className="divide-y divide-line">
        {decided.map((s) => (
          <li key={s.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 text-[13px]">
            <Chip size="xs" color={s.status === "approved" ? "primary" : "white"}>
              {SUGGESTION_STATUS_LABEL[s.status]}
            </Chip>
            <span className="text-mixed min-w-0 flex-1 font-medium">{s.title}</span>
            <span className="text-[12px] text-muted">
              {s.field_label} · {formatShortDateTime(s.decided_at)} ·{" "}
              <button type="button" onClick={() => onOpenRun(s.run_id)} className="font-medium text-primary hover:underline">
                review #{s.run_id}
              </button>
            </span>
            {s.decision_note ? <p className="text-mixed w-full text-[12px] text-muted">Note: {s.decision_note}</p> : null}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function MethodCard() {
  const { data } = useSWR<ReviewMethod>(keys.reviewMethod, { revalidateOnFocus: false });
  if (!data) return null;
  return (
    <Card>
      <CardHeader
        icon={<Scale className="mt-0.5 size-5 text-primary" aria-hidden />}
        title="How the review agent evaluates calls"
        subtitle={
          <>
            The rules are editable in{" "}
            <Link href="/settings#review-agent" className="font-medium text-primary hover:underline">
              Settings → Review agent
            </Link>
            .
          </>
        }
      />
      <ol className="list-decimal space-y-2 pl-5 text-[13px]">
        {data.steps.map((step, i) => (
          <li key={i} className="text-mixed">
            {step}
          </li>
        ))}
      </ol>
      <details className="mt-4 rounded-lg border border-line p-3 text-[13px]">
        <summary className="cursor-pointer font-medium">How calls are judged (the rules it follows)</summary>
        <pre className="text-mixed mt-2 font-sans text-[13px] whitespace-pre-wrap">{data.rubric}</pre>
      </details>
      <details className="mt-2 rounded-lg border border-line p-3 text-[13px]">
        <summary className="cursor-pointer font-medium">Mistakes it looks for ({data.categories.length})</summary>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {data.categories.map((c) => (
            <Chip key={c} size="xs">
              {categoryLabel(c)}
            </Chip>
          ))}
        </div>
      </details>
      <details className="mt-2 rounded-lg border border-line p-3 text-[13px]">
        <summary className="cursor-pointer font-medium">Settings it may suggest changes to ({data.fields.length})</summary>
        <ul className="mt-2 space-y-1.5">
          {data.fields.map((f) => (
            <li key={f.key} className="text-mixed">
              <span className="font-medium">{f.label}</span> <span className="text-muted">({f.how.join(", ")})</span>: {f.help}
            </li>
          ))}
        </ul>
        <p className="mt-2 text-muted">It can never change the voice, calling switches, timings or anything else.</p>
      </details>
    </Card>
  );
}
