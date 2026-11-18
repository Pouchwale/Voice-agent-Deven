"use client";

import { ClipboardCheck, Play, Quote } from "lucide-react";
import { Chip } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { formatShortDateTime } from "@/lib/format";
import type { CallReview } from "@/lib/types";
import { CAUSE_LABEL, categoryLabel, OUTCOME_LABEL, SEVERITY_LABEL } from "./labels";

/** "m:ss" (a turn's time in the call) -> milliseconds. */
function toMs(at: string | null | undefined): number | null {
  const m = /^(\d+):(\d{2})$/.exec(at ?? "");
  return m ? (Number(m[1]) * 60 + Number(m[2])) * 1000 : null;
}

/** A moment in the call: plays the recording from there when the page has one. */
function At({ at, onSeek }: { at: string | null; onSeek?: (ms: number) => void }) {
  const ms = toMs(at);
  if (!at) return null;
  return onSeek && ms !== null ? (
    <button
      type="button"
      onClick={() => onSeek(ms)}
      className="tabular inline-flex items-center gap-1 rounded-full border border-line bg-white px-2 text-[12px] font-medium hover:bg-canvas [&_svg]:size-3"
      aria-label={`Play the recording from ${at}`}
    >
      <Play aria-hidden /> {at}
    </button>
  ) : (
    <span className="tabular text-[12px] text-muted">{at}</span>
  );
}

/** The review agent's verdict on one call, on the call's own page. Times jump the recording there. */
export function CallReviewCard({ review, onSeek }: { review: CallReview; onSeek?: (ms: number) => void }) {
  const ev = review.evaluation;
  return (
    <Card className="mt-5">
      <CardHeader
        icon={<ClipboardCheck className="mt-0.5 size-5 text-primary" aria-hidden />}
        title="Call review"
        subtitle={`What the review agent found, with proof from this call (review #${review.run_id} · ${formatShortDateTime(review.reviewed_at)}).`}
        actions={
          <ButtonLink href="/review" size="sm" variant="ghost">
            Open Call review
          </ButtonLink>
        }
      />
      <div className="flex flex-wrap items-center gap-2">
        <Chip size="sm" color={ev.outcome === "converted" || ev.outcome === "interested" ? "primary" : "neutral"}>
          {OUTCOME_LABEL[ev.outcome] ?? ev.outcome}
        </Chip>
        {ev.intent_lost && ev.loss_cause ? (
          <Chip size="sm" color={ev.loss_cause === "agent" ? "dark" : "white"}>
            {CAUSE_LABEL[ev.loss_cause]}
          </Chip>
        ) : null}
        {ev.intent_lost && ev.lost_at ? (
          <span className="inline-flex items-center gap-1.5 text-[13px] text-subtle-fg">
            Customer lost at {ev.lost_at} <At at={ev.lost_at_time} onSeek={onSeek} />
          </span>
        ) : null}
      </div>
      {ev.verdict ? <p className="text-mixed mt-3 text-sm">{ev.verdict}</p> : null}
      {ev.proof?.removed?.length ? (
        <p className="text-mixed mt-1 text-[12px] text-muted">
          The proof check removed what the call doesn&apos;t back up: {ev.proof.removed.join("; ")}.
        </p>
      ) : null}
      {ev.loss_cause === "customer_side" && ev.customer_side_reason ? (
        <p className="text-mixed mt-1 text-[13px] text-muted">Customer&apos;s reason: {ev.customer_side_reason}</p>
      ) : null}

      {ev.issues.length ? (
        <ul className="mt-4 space-y-2">
          {ev.issues.map((i, n) => (
            <li key={n} className="rounded-lg bg-canvas p-3 text-[13px]">
              <p className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">{categoryLabel(i.category)}</span>
                <At at={i.at} onSeek={onSeek} />
                <span className="text-[12px] text-muted">{SEVERITY_LABEL[i.severity] ?? ""}</span>
              </p>
              {i.quote ? (
                <p className="text-mixed mt-1 flex gap-1.5">
                  <Quote className="mt-1 size-3 shrink-0 text-muted" aria-hidden />“{i.quote}”
                </p>
              ) : null}
              {i.facts.map((fid) => {
                const f = review.facts.find((x) => x.id === fid);
                return f ? (
                  <p key={fid} className="mt-1 text-[12px] text-subtle-fg">
                    Measured: {f.text}
                  </p>
                ) : null;
              })}
              {i.earlier && i.earlier_text ? (
                <p className="text-mixed mt-1 text-[12px] text-subtle-fg">
                  {i.category === "ignored_question" ? "The customer had asked" : "Already asked or answered"} at {i.earlier}: “
                  {i.earlier_text}”
                </p>
              ) : null}
              {i.why ? <p className="text-mixed mt-1 text-subtle-fg">{i.why}</p> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-4 text-[13px] text-muted">No proven mistakes by the agent on this call.</p>
      )}

      {ev.strengths.length ? (
        <div className="mt-4 text-[13px]">
          <p className="font-semibold">What worked</p>
          <ul className="text-mixed mt-1 space-y-1">
            {ev.strengths.map((s, n) => (
              <li key={n} className="flex flex-wrap items-center gap-1.5">
                “{s.quote}” <At at={s.at} onSeek={onSeek} /> {s.why ? <span className="text-subtle-fg">{s.why}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Card>
  );
}
