"use client";

import { useLayoutEffect, useRef } from "react";
import { anime, DUR, EASE_OUT, prefersReducedMotion } from "@/lib/anim";
import { THEME } from "@/lib/theme";

const SIZE = 160;
const STROKE = 12;
const R = (SIZE - STROKE) / 2;
const C = 2 * Math.PI * R;

function fmt(n: number, decimals: boolean) {
  return decimals ? n.toFixed(1) : String(Math.round(n));
}

/** Clean blue ring with "N/10" in the middle. */
export function RatingGauge({ rating }: { rating: number | null | undefined; interest?: unknown }) {
  const arcRef = useRef<SVGCircleElement>(null);
  const numRef = useRef<HTMLSpanElement>(null);
  const prev = useRef(0);
  const value = rating === null || rating === undefined || !Number.isFinite(rating) ? null : Math.max(0, Math.min(10, rating));
  const decimals = value !== null && !Number.isInteger(value);

  useLayoutEffect(() => {
    const arc = arcRef.current;
    const num = numRef.current;
    if (!arc || !num || value === null) return;
    const from = prev.current;
    prev.current = value;
    const apply = (v: number) => {
      arc.style.strokeDashoffset = String(C * (1 - v / 10));
      num.textContent = fmt(v, decimals);
    };
    if (prefersReducedMotion()) {
      apply(value);
      return;
    }
    const st = { v: from };
    apply(from);
    const inst = anime({
      targets: st,
      v: value,
      duration: DUR.slow,
      easing: EASE_OUT,
      update: () => apply(st.v),
      complete: () => apply(value),
    });
    return () => inst.pause();
  }, [value, decimals]);

  return (
    <div
      className="relative mx-auto size-40"
      role="img"
      aria-label={value === null ? "Not rated yet" : `Rating ${fmt(value, decimals)} out of 10`}
    >
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="block size-full -rotate-90" aria-hidden>
        <circle cx={SIZE / 2} cy={SIZE / 2} r={R} fill="none" stroke={THEME.primaryTint} strokeWidth={STROKE} />
        {value !== null ? (
          <circle
            ref={arcRef}
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={R}
            fill="none"
            stroke={THEME.brand}
            strokeWidth={STROKE}
            strokeLinecap="round"
            strokeDasharray={`${C} ${C}`}
            style={{ strokeDashoffset: C }}
          />
        ) : null}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <p className="flex items-baseline font-heading leading-none">
          <span ref={numRef} className="text-[40px] font-semibold tracking-tight">
            {value === null ? "—" : fmt(0, decimals)}
          </span>
          <span className="ml-0.5 text-base font-medium text-muted">/10</span>
        </p>
        <p className="mt-1 text-xs text-muted">{value === null ? "Not rated yet" : "Lead rating"}</p>
      </div>
    </div>
  );
}
