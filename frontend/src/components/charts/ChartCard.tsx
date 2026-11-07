"use client";

import { ChartColumn, Table2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface LegendItem {
  label: string;
  color: string;
  kind?: "line" | "rect";
  outlined?: boolean;
}

export function Legend({ items }: { items: LegendItem[] }) {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5" aria-label="Legend">
      {items.map((it) => (
        <li key={it.label} className="inline-flex items-center gap-1.5 text-xs text-muted">
          {it.kind === "line" ? (
            <span className="inline-block h-[3px] w-4 rounded-full" style={{ background: it.color }} aria-hidden />
          ) : (
            <span
              className={cn("inline-block size-3 rounded-sm", it.outlined && "border border-line")}
              style={{ background: it.color }}
              aria-hidden
            />
          )}
          {it.label}
        </li>
      ))}
    </ul>
  );
}

/** Chart container: title, legend, chart/table toggle. Holds the previous render dimmed while refetching. */
export function ChartCard({
  title,
  subtitle,
  legend,
  table,
  children,
  busy,
  className,
  actions,
}: {
  title: string;
  subtitle?: ReactNode;
  legend?: LegendItem[];
  table?: ReactNode;
  children: ReactNode;
  busy?: boolean;
  className?: string;
  actions?: ReactNode;
}) {
  const [showTable, setShowTable] = useState(false);
  return (
    <section className={cn("flex min-w-0 flex-col rounded-xl border border-line bg-white p-4 sm:p-5", className)}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-heading text-base font-semibold">{title}</h2>
          {subtitle ? <p className="mt-0.5 text-[13px] text-muted">{subtitle}</p> : null}
        </div>
        <div className="flex items-center gap-2">
          {actions}
          {table ? (
            <button
              type="button"
              onClick={() => setShowTable((s) => !s)}
              aria-pressed={showTable}
              className="inline-flex h-7 items-center gap-1.5 rounded-md border border-line bg-white px-2.5 text-xs font-medium text-muted hover:bg-canvas hover:text-fg [&_svg]:size-3.5"
            >
              {showTable ? <ChartColumn aria-hidden /> : <Table2 aria-hidden />}
              {showTable ? "Chart" : "Table"}
            </button>
          ) : null}
        </div>
      </div>
      {legend && !showTable ? (
        <div className="mb-3">
          <Legend items={legend} />
        </div>
      ) : null}
      <div className={cn("min-w-0 flex-1 transition-opacity duration-200", busy && "opacity-60")}>
        {showTable && table ? <div className="relative overflow-x-auto">{table}</div> : children}
      </div>
    </section>
  );
}

/** Minimal table used as the accessible twin of each chart. */
export function ChartTable({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  return (
    <table className="data-table">
      <thead>
        <tr>
          {head.map((h, i) => (
            <th key={h} className={i > 0 ? "!text-right" : undefined}>
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, ri) => (
          <tr key={ri}>
            {r.map((c, ci) => (
              <td key={ci} className={cn("tabular", ci > 0 && "text-right")}>
                {c}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Floating tooltip box positioned inside a relative container. Values lead, labels follow. */
export function ChartTooltip({
  x,
  y,
  containerWidth,
  title,
  rows,
}: {
  x: number;
  y: number;
  containerWidth: number;
  title: string;
  rows: { label: string; value: string; color: string; kind?: "line" | "rect"; outlined?: boolean }[];
}) {
  const w = 168;
  const left = Math.max(0, Math.min(containerWidth - w, x + 12 + w > containerWidth ? x - w - 12 : x + 12));
  return (
    <div
      className="pointer-events-none absolute z-10 rounded-lg border border-line bg-white px-3 py-2 shadow-lg"
      style={{ left, top: Math.max(0, y), width: w }}
      role="presentation"
    >
      <p className="mb-1 text-xs font-semibold text-muted">{title}</p>
      {rows.map((r) => (
        <p key={r.label} className="flex items-center gap-2 text-[13px]">
          {r.kind === "rect" ? (
            <span
              className={cn("inline-block size-2.5 rounded-sm", r.outlined && "border border-line")}
              style={{ background: r.color }}
              aria-hidden
            />
          ) : (
            <span className="inline-block h-[3px] w-3 rounded-full" style={{ background: r.color }} aria-hidden />
          )}
          <span className="tabular font-medium">{r.value}</span>
          <span className="truncate font-normal text-muted">{r.label}</span>
        </p>
      ))}
    </div>
  );
}

/** "Nice" axis ticks from 0 to >= max. */
export function niceTicks(max: number, count = 4): number[] {
  if (!Number.isFinite(max) || max <= 0) return [0, 1];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  const s = Math.max(1, step);
  const ticks: number[] = [];
  for (let v = 0; v <= max + s * 0.001; v += s) ticks.push(Math.round(v * 100) / 100);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + s);
  return ticks;
}
