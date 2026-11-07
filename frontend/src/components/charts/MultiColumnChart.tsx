"use client";

import { useState, type KeyboardEvent } from "react";
import { useMeasure } from "@/hooks/useMeasure";
import { formatNumber } from "@/lib/format";
import { THEME } from "@/lib/theme";
import { ChartTooltip, niceTicks } from "./ChartCard";

export interface ColumnSeries {
  key: string;
  label: string;
  color: string;
  values: number[];
  /** Light fills get a hairline outline so they stay visible on white. */
  outlined?: boolean;
}

const M = { top: 12, right: 8, bottom: 26, left: 36 };

/**
 * Columns per category, either stacked (series add up) or overlaid (each series drawn from the baseline,
 * first series behind - e.g. dialed behind connected). Hover/focus shows every series for that category.
 */
export function MultiColumnChart({
  labels,
  series,
  mode,
  height = 220,
  ariaLabel,
  extraTooltip,
}: {
  labels: string[];
  series: ColumnSeries[];
  mode: "stack" | "overlay";
  height?: number;
  ariaLabel: string;
  /** Extra tooltip row for a category, e.g. a connect rate. */
  extraTooltip?: (i: number) => { label: string; value: string } | null;
}) {
  const [ref, width] = useMeasure<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const n = labels.length;
  const innerW = Math.max(10, width - M.left - M.right);
  const innerH = height - M.top - M.bottom;
  const totals = labels.map((_, i) =>
    mode === "stack" ? series.reduce((s, se) => s + (se.values[i] ?? 0), 0) : Math.max(0, ...series.map((se) => se.values[i] ?? 0)),
  );
  const ticks = niceTicks(Math.max(1, ...totals), 4);
  const top = ticks[ticks.length - 1];
  const slot = innerW / Math.max(1, n);
  const barW = Math.max(3, Math.min(22, slot * 0.66));
  const y = (v: number) => M.top + innerH - (v / top) * innerH;
  const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(innerW / 34))));

  function onKey(e: KeyboardEvent<SVGRectElement>) {
    if (e.key === "ArrowRight") setHover((h) => Math.min(n - 1, (h ?? -1) + 1));
    else if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? n) - 1));
    else return;
    e.preventDefault();
  }

  return (
    <div ref={ref} className="relative w-full" style={{ height }}>
      {width > 0 ? (
        <svg width={width} height={height} role="img" aria-label={ariaLabel} className="block overflow-visible">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={M.left} x2={width - M.right} y1={y(t)} y2={y(t)} stroke={THEME.line} strokeWidth={1} />
              <text x={M.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="tabular fill-muted text-xs">
                {formatNumber(t)}
              </text>
            </g>
          ))}
          {labels.map((label, i) => {
            const cx = M.left + slot * i + slot / 2;
            let acc = 0;
            return (
              <g key={label + i}>
                {hover === i ? <rect x={cx - slot / 2} y={M.top} width={slot} height={innerH} fill={THEME.canvas} /> : null}
                {series.map((s) => {
                  const v = s.values[i] ?? 0;
                  if (v <= 0) return null;
                  const base = mode === "stack" ? acc : 0;
                  if (mode === "stack") acc += v;
                  const y1 = y(base + v);
                  const h = y(base) - y1;
                  const w = mode === "overlay" && s !== series[0] ? barW * 0.6 : barW;
                  return (
                    <rect
                      key={s.key}
                      x={cx - w / 2}
                      y={y1}
                      width={w}
                      height={Math.max(0, h - (mode === "stack" ? 1 : 0))}
                      rx={mode === "overlay" ? 2 : 0}
                      fill={s.color}
                      stroke={s.outlined ? THEME.line : undefined}
                      strokeWidth={s.outlined ? 1 : undefined}
                    />
                  );
                })}
                {i % every === 0 ? (
                  <text x={cx} y={height - 8} textAnchor="middle" className="tabular fill-muted text-xs">
                    {label}
                  </text>
                ) : null}
                <rect
                  x={cx - slot / 2}
                  y={M.top}
                  width={slot}
                  height={innerH}
                  fill="transparent"
                  onPointerEnter={() => setHover(i)}
                  onPointerLeave={() => setHover(null)}
                />
              </g>
            );
          })}
          <rect
            x={M.left}
            y={height - M.bottom}
            width={innerW}
            height={M.bottom}
            fill="transparent"
            tabIndex={0}
            aria-label={`${ariaLabel}. Use arrow keys to read values.`}
            onFocus={() => setHover((h) => h ?? 0)}
            onBlur={() => setHover(null)}
            onKeyDown={onKey}
          />
        </svg>
      ) : null}
      {hover !== null && width > 0 ? (
        <ChartTooltip
          x={M.left + slot * hover + slot / 2}
          y={M.top}
          containerWidth={width}
          title={labels[hover]}
          rows={[
            ...[...series].reverse().map((s) => ({
              label: s.label,
              value: formatNumber(s.values[hover] ?? 0),
              color: s.color,
              kind: "rect" as const,
              outlined: s.outlined,
            })),
            ...(extraTooltip?.(hover) ? [{ ...extraTooltip(hover)!, color: "transparent" }] : []),
          ]}
        />
      ) : null}
    </div>
  );
}
