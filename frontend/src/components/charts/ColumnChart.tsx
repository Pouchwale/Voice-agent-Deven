"use client";

import { useState, type KeyboardEvent } from "react";
import { useMeasure } from "@/hooks/useMeasure";
import { THEME } from "@/lib/theme";
import { formatNumber } from "@/lib/format";
import { ChartTooltip, niceTicks } from "./ChartCard";

const M = { top: 18, right: 8, bottom: 26, left: 34 };

/** Column with a 4px rounded data end and a square baseline. */
function columnPath(x: number, y: number, w: number, h: number, r = 4): string {
  if (h <= 0) return "";
  const rr = Math.min(r, h, w / 2);
  return `M${x},${y + h} L${x},${y + rr} Q${x},${y} ${x + rr},${y} L${x + w - rr},${y} Q${x + w},${y} ${x + w},${y + rr} L${x + w},${y + h} Z`;
}

/** Single-series column chart (one color). Labels the tallest column; tooltip for the rest. */
export function ColumnChart({
  data,
  color = THEME.brand,
  height = 210,
  ariaLabel,
  valueLabel = "Calls",
  categoryPrefix = "",
}: {
  data: { label: string; value: number }[];
  color?: string;
  height?: number;
  ariaLabel: string;
  valueLabel?: string;
  categoryPrefix?: string;
}) {
  const [ref, width] = useMeasure<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const n = data.length;
  const innerW = Math.max(10, width - M.left - M.right);
  const innerH = height - M.top - M.bottom;
  const max = Math.max(1, ...data.map((d) => d.value));
  const ticks = niceTicks(max, 4);
  const top = ticks[ticks.length - 1];
  const slot = innerW / Math.max(1, n);
  const barW = Math.min(24, slot * 0.62);
  const y = (v: number) => M.top + innerH - (v / top) * innerH;
  const maxIdx = data.reduce((best, d, i) => (d.value > (data[best]?.value ?? -1) ? i : best), 0);

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
              <text x={M.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="tabular fill-muted text-xs font-medium">
                {formatNumber(t)}
              </text>
            </g>
          ))}
          {data.map((d, i) => {
            const cx = M.left + slot * i + slot / 2;
            const h = (d.value / top) * innerH;
            const active = hover === i;
            return (
              <g key={d.label}>
                {active ? <rect x={cx - slot / 2 + 1} y={M.top} width={slot - 2} height={innerH} fill={THEME.canvas} /> : null}
                <path d={columnPath(cx - barW / 2, y(d.value), barW, h)} fill={active ? THEME.primary : color} />
                {i === maxIdx && d.value > 0 ? (
                  <text x={cx} y={y(d.value) - 6} textAnchor="middle" className="tabular fill-fg text-xs font-semibold">
                    {formatNumber(d.value)}
                  </text>
                ) : null}
                <text x={cx} y={height - 8} textAnchor="middle" className="tabular fill-muted text-xs font-medium">
                  {d.label}
                </text>
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
      {hover !== null && width > 0 && data[hover] ? (
        <ChartTooltip
          x={M.left + slot * hover + slot / 2}
          y={Math.max(0, y(data[hover].value) - 60)}
          containerWidth={width}
          title={`${categoryPrefix}${data[hover].label}`}
          rows={[{ label: valueLabel, value: formatNumber(data[hover].value), color, kind: "rect" }]}
        />
      ) : null}
    </div>
  );
}
