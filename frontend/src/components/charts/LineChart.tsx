"use client";

import { useState, type KeyboardEvent } from "react";
import { useMeasure } from "@/hooks/useMeasure";
import { formatNumber } from "@/lib/format";
import { THEME } from "@/lib/theme";
import { ChartTooltip, niceTicks } from "./ChartCard";

export interface LineSeries {
  key: string;
  label: string;
  color: string;
  /** `null` = no data for that point (the line breaks there). */
  values: (number | null)[];
}

const M = { top: 10, right: 14, bottom: 28, left: 38 };

/** Multi-series line chart with crosshair + tooltip (pointer and keyboard), optional target line. */
export function LineChart({
  labels,
  series,
  height = 230,
  ariaLabel,
  formatValue = (n) => formatNumber(n),
  target,
}: {
  labels: string[];
  series: LineSeries[];
  height?: number;
  ariaLabel: string;
  formatValue?: (n: number) => string;
  /** Horizontal reference line, e.g. a response-time goal. */
  target?: { value: number; label: string };
}) {
  const [ref, width] = useMeasure<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const n = labels.length;
  const innerW = Math.max(10, width - M.left - M.right);
  const innerH = height - M.top - M.bottom;
  const finite = series.flatMap((s) => s.values.filter((v): v is number => v !== null && Number.isFinite(v)));
  const max = Math.max(target?.value ?? 0, ...finite, 0) || 1;
  const ticks = niceTicks(max, 4);
  const top = ticks[ticks.length - 1];
  const x = (i: number) => M.left + (n <= 1 ? innerW / 2 : (i * innerW) / (n - 1));
  const y = (v: number) => M.top + innerH - (v / top) * innerH;
  const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(innerW / 58))));

  function pick(clientX: number, rect: DOMRect) {
    const px = clientX - rect.left - M.left;
    const i = n <= 1 ? 0 : Math.round((px / innerW) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, i)));
  }

  function onKey(e: KeyboardEvent<SVGRectElement>) {
    if (e.key === "ArrowRight") setHover((h) => Math.min(n - 1, (h ?? -1) + 1));
    else if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? n) - 1));
    else if (e.key === "Home") setHover(0);
    else if (e.key === "End") setHover(n - 1);
    else return;
    e.preventDefault();
  }

  /** Path that breaks at null values. */
  function pathFor(values: (number | null)[]) {
    let d = "";
    let pen = false;
    values.forEach((v, i) => {
      if (v === null || !Number.isFinite(v)) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)} `;
      pen = true;
    });
    return d.trim();
  }

  const markerIndex = hover ?? n - 1;

  return (
    <div ref={ref} className="relative w-full" style={{ height }}>
      {width > 0 ? (
        <svg width={width} height={height} role="img" aria-label={ariaLabel} className="block overflow-visible">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={M.left} x2={width - M.right} y1={y(t)} y2={y(t)} stroke={THEME.line} strokeWidth={1} />
              <text x={M.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="tabular fill-muted text-xs">
                {formatValue(t)}
              </text>
            </g>
          ))}
          {labels.map((l, i) =>
            (i % every === 0 && n - 1 - i >= every) || i === n - 1 ? (
              <text key={l + i} x={x(i)} y={height - 8} textAnchor="middle" className="fill-muted text-xs">
                {l}
              </text>
            ) : null,
          )}
          {target ? (
            <g>
              <line x1={M.left} x2={width - M.right} y1={y(target.value)} y2={y(target.value)} stroke={THEME.navy} strokeWidth={1} strokeDasharray="4 4" />
              <text x={width - M.right} y={y(target.value) - 5} textAnchor="end" className="fill-fg text-xs">
                {target.label}
              </text>
            </g>
          ) : null}
          {hover !== null ? <line x1={x(hover)} x2={x(hover)} y1={M.top} y2={M.top + innerH} stroke={THEME.muted} strokeWidth={1} /> : null}
          {series.map((s) => (
            <path key={s.key} d={pathFor(s.values)} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          ))}
          {n > 0
            ? series.map((s) => {
                const v = s.values[markerIndex];
                if (v === null || v === undefined || !Number.isFinite(v)) return null;
                return <circle key={s.key} cx={x(markerIndex)} cy={y(v)} r={4} fill={s.color} stroke={THEME.white} strokeWidth={2} />;
              })
            : null}
          <rect
            x={M.left}
            y={M.top}
            width={innerW}
            height={innerH}
            fill="transparent"
            tabIndex={0}
            aria-label={`${ariaLabel}. Use arrow keys to read values.`}
            onPointerMove={(e) => pick(e.clientX, e.currentTarget.ownerSVGElement!.getBoundingClientRect())}
            onPointerLeave={() => setHover(null)}
            onFocus={() => setHover((h) => h ?? n - 1)}
            onBlur={() => setHover(null)}
            onKeyDown={onKey}
          />
        </svg>
      ) : null}
      {hover !== null && width > 0 ? (
        <ChartTooltip
          x={x(hover)}
          y={M.top}
          containerWidth={width}
          title={labels[hover]}
          rows={series.map((s) => {
            const v = s.values[hover];
            return { label: s.label, value: v === null || v === undefined ? "—" : formatValue(v), color: s.color };
          })}
        />
      ) : null}
      {hover !== null ? (
        <p className="sr-only" aria-live="polite">
          {labels[hover]}:{" "}
          {series.map((s) => `${s.label} ${s.values[hover] === null ? "no data" : formatValue(s.values[hover] ?? 0)}`).join(", ")}
        </p>
      ) : null}
    </div>
  );
}
