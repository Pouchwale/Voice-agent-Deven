"use client";

import Link from "next/link";
import { useState } from "react";
import { cn } from "@/lib/cn";
import { formatNumber, formatPercent, INTEREST_LABEL } from "@/lib/format";
import { INTEREST_STYLE } from "@/lib/theme";
import type { Interest } from "@/lib/types";

/** Horizontal bars with the value at the tip (all values labelled; ranked). */
export function HBars({
  data,
  color,
  emptyText = "No data yet.",
}: {
  data: { label: string; value: number }[];
  color: string;
  emptyText?: string;
}) {
  const max = Math.max(1, ...data.map((d) => d.value));
  if (data.length === 0) return <p className="py-6 text-center text-sm text-muted">{emptyText}</p>;
  return (
    <ul className="space-y-2.5">
      {data.map((d, i) => (
        <li key={`${i}-${d.label}`} className="grid grid-cols-[minmax(96px,150px)_1fr] items-center gap-3">
          <span className="truncate text-[13px] text-muted" title={d.label}>
            {d.label}
          </span>
          <span className="flex min-w-0 items-center gap-2">
            <span
              className="block h-2.5 rounded-r-sm"
              style={{ width: `${Math.max(1.5, (d.value / max) * 85)}%`, background: color }}
              aria-hidden
            />
            <span className="tabular shrink-0 text-[13px] font-medium">{formatNumber(d.value)}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

const ORDER: Interest[] = ["cold", "warm", "hot", "unknown"];

/** Segmented part-to-whole bar in the tier colours + rows with counts and shares. */
export function InterestBreakdown({ counts, linkRows = true }: { counts: Record<Interest, number>; linkRows?: boolean }) {
  const total = ORDER.reduce((s, k) => s + (counts[k] ?? 0), 0);
  const [hover, setHover] = useState<Interest | null>(null);

  return (
    <div>
      <div
        className="flex h-3 w-full gap-0.5 overflow-hidden rounded-full"
        role="img"
        aria-label={
          total === 0
            ? "No leads yet"
            : ORDER.map((k) => `${INTEREST_LABEL[k]} ${counts[k] ?? 0} (${formatPercent((counts[k] ?? 0) / total)})`).join(", ")
        }
      >
        {total === 0 ? (
          <span className="flex-1 rounded-full bg-subtle" />
        ) : (
          ORDER.filter((k) => (counts[k] ?? 0) > 0).map((k) => {
            const share = (counts[k] ?? 0) / total;
            return (
              <span
                key={k}
                className={cn("h-full transition-opacity", hover && hover !== k && "opacity-50", (k === "unknown" || k === "cold") && "border border-line")}
                style={{ width: `${share * 100}%`, background: INTEREST_STYLE[k].fill }}
                title={`${INTEREST_LABEL[k]}: ${counts[k]} (${formatPercent(share)})`}
                onPointerEnter={() => setHover(k)}
                onPointerLeave={() => setHover(null)}
              />
            );
          })
        )}
      </div>

      <ul className="mt-4 space-y-0.5">
        {ORDER.map((k) => {
          const c = counts[k] ?? 0;
          const row = (
            <>
              <span
                className={cn("size-2.5 shrink-0 rounded-full", (k === "unknown" || k === "cold") && "border border-line")}
                style={{ background: INTEREST_STYLE[k].fill }}
                aria-hidden
              />
              <span className="flex-1 text-sm">{INTEREST_LABEL[k]}</span>
              <span className="tabular text-sm font-medium">{formatNumber(c)}</span>
              <span className="tabular w-12 text-right text-[13px] text-muted">{total ? formatPercent(c / total) : "—"}</span>
            </>
          );
          return (
            <li key={k} onPointerEnter={() => setHover(k)} onPointerLeave={() => setHover(null)}>
              {linkRows ? (
                <Link href={`/leads?interest=${k}`} className={cn("flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-canvas", hover === k && "bg-canvas")}>
                  {row}
                </Link>
              ) : (
                <div className="flex items-center gap-3 px-2 py-1.5">{row}</div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
