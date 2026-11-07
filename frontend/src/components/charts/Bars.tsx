"use client";

import { cn } from "@/lib/cn";
import { formatNumber, formatPercent } from "@/lib/format";
import { THEME } from "@/lib/theme";
import { Legend, type LegendItem } from "./ChartCard";

/** Funnel: one horizontal bar per stage, width relative to the first stage, with count and % of the first. */
export function FunnelBars({ steps }: { steps: { label: string; count: number }[] }) {
  const first = steps[0]?.count ?? 0;
  return (
    <ol className="space-y-3">
      {steps.map((s, i) => {
        const pct = first > 0 ? s.count / first : 0;
        return (
          <li key={s.label}>
            <div className="mb-1 flex items-baseline justify-between gap-3 text-[13px]">
              <span className="text-fg">
                <span className="tabular mr-2 text-muted">{i + 1}.</span>
                {s.label}
              </span>
              <span className="tabular shrink-0">
                <span className="font-medium">{formatNumber(s.count)}</span>
                <span className="ml-2 text-muted">{first > 0 ? formatPercent(pct) : "—"}</span>
              </span>
            </div>
            <div className="h-2.5 rounded-full bg-subtle" aria-hidden>
              <div
                className="h-full rounded-full"
                style={{ width: `${Math.max(pct > 0 ? 1.5 : 0, pct * 100)}%`, background: i === 0 ? THEME.faint : THEME.brand }}
              />
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export interface StackPart {
  key: string;
  label: string;
  color: string;
  outlined?: boolean;
}

/** One stacked horizontal bar per row (e.g. qualification coverage per question). */
export function StackedHBars({
  rows,
  parts,
}: {
  rows: { label: string; values: Record<string, number> }[];
  parts: StackPart[];
}) {
  return (
    <div>
      <div className="mb-3">
        <Legend items={parts.map<LegendItem>((p) => ({ label: p.label, color: p.color, kind: "rect", outlined: p.outlined }))} />
      </div>
      <ul className="space-y-3">
        {rows.map((r, ri) => {
          const total = parts.reduce((s, p) => s + (r.values[p.key] ?? 0), 0);
          return (
            // index key: two qualification questions can have the same text
            <li key={`${ri}-${r.label}`}>
              <p className="text-mixed mb-1 text-[13px] text-fg">{r.label}</p>
              <div
                className="flex h-3 w-full gap-0.5 overflow-hidden rounded-full bg-subtle"
                role="img"
                aria-label={`${r.label}: ${parts.map((p) => `${p.label} ${r.values[p.key] ?? 0}`).join(", ")}`}
              >
                {total > 0
                  ? parts.map((p) => {
                      const v = r.values[p.key] ?? 0;
                      if (!v) return null;
                      return (
                        <span
                          key={p.key}
                          className={cn("h-full", p.outlined && "border border-line")}
                          style={{ width: `${(v / total) * 100}%`, background: p.color }}
                          title={`${p.label}: ${v}`}
                        />
                      );
                    })
                  : null}
              </div>
              <p className="tabular mt-1 text-xs text-muted">
                {parts.map((p) => `${p.label} ${formatNumber(r.values[p.key] ?? 0)}`).join(" · ")}
              </p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Part-to-whole bar + legend rows (e.g. sentiment). */
export function SplitBar({ parts }: { parts: (StackPart & { value: number })[] }) {
  const total = parts.reduce((s, p) => s + p.value, 0);
  return (
    <div>
      <div
        className="flex h-3 w-full gap-0.5 overflow-hidden rounded-full bg-subtle"
        role="img"
        aria-label={parts.map((p) => `${p.label} ${p.value}`).join(", ")}
      >
        {total > 0
          ? parts.map((p) =>
              p.value ? (
                <span
                  key={p.key}
                  className={cn("h-full", p.outlined && "border border-line")}
                  style={{ width: `${(p.value / total) * 100}%`, background: p.color }}
                />
              ) : null,
            )
          : null}
      </div>
      <ul className="mt-3 space-y-0.5">
        {parts.map((p) => (
          <li key={p.key} className="flex items-center gap-3 px-1 py-1">
            <span className={cn("size-2.5 shrink-0 rounded-full", p.outlined && "border border-line")} style={{ background: p.color }} aria-hidden />
            <span className="flex-1 text-sm">{p.label}</span>
            <span className="tabular text-sm font-medium">{formatNumber(p.value)}</span>
            <span className="tabular w-12 text-right text-[13px] text-muted">{total ? formatPercent(p.value / total) : "—"}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
