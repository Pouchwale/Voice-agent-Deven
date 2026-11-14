"use client";

import { useEffect, useRef } from "react";
import { animateWidth } from "@/lib/anim";
import { formatNumber } from "@/lib/format";
import type { CampaignCounts } from "@/lib/types";

/** Thick pill progress bar (processed = done + failed + skipped over total) with a text breakdown. */
export function CampaignProgress({ counts, name }: { counts: CampaignCounts; name: string }) {
  const fillRef = useRef<HTMLDivElement>(null);
  const processed = counts.done + counts.failed + counts.skipped;
  const pct = counts.total > 0 ? (processed / counts.total) * 100 : 0;

  useEffect(() => {
    if (fillRef.current) animateWidth(fillRef.current, pct);
  }, [pct]);

  const parts: { label: string; value: number }[] = [
    { label: "in queue (incl. retries)", value: counts.pending + counts.dialing },
    { label: "gave up", value: counts.failed },
    { label: "skipped", value: counts.skipped },
  ];

  return (
    <div>
      <div className="flex items-center gap-3">
        <div
          role="progressbar"
          aria-label={`${name} progress`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(pct)}
          aria-valuetext={`${formatNumber(processed)} of ${formatNumber(counts.total)} leads processed`}
          className="relative h-5 flex-1 overflow-hidden rounded-full border border-line bg-primary-tint"
        >
          <div
            ref={fillRef}
            className="absolute inset-y-0 left-0 rounded-full bg-brand"
            style={{ width: "0%" }}
          />
        </div>
        <span className="tabular w-12 text-right text-sm font-semibold">{Math.round(pct)}%</span>
      </div>
      <p className="tabular mt-2 text-[13px] font-medium">
        <span className="font-semibold">
          {formatNumber(processed)} / {formatNumber(counts.total)} numbers finished
        </span>
        <span className={counts.done === 0 ? "text-muted" : undefined}>
          {" · "}
          {formatNumber(counts.done)} reached
        </span>
        {parts.map((p) => (
          <span key={p.label} className={p.value === 0 ? "text-muted" : undefined}>
            {" · "}
            {formatNumber(p.value)} {p.label}
          </span>
        ))}
      </p>
    </div>
  );
}
