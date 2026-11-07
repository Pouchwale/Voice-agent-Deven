"use client";

import { useEffect, useRef } from "react";
import { useLiveStore } from "@/components/providers/LiveProvider";
import { prefersReducedMotion } from "@/lib/anim";
import { cn } from "@/lib/cn";

const SHAPE = [0.55, 0.85, 1, 0.8, 0.6];
const STALE_MS = 1000;
const MIN_PCT = 14;

/**
 * Animated level meter for one side of a live call.
 * Reads `store.getLevels(uuid)` inside requestAnimationFrame and writes bar heights straight to the DOM,
 * so ~8 level events/sec per call never cause React re-renders.
 */
export function LevelBars({
  uuid,
  track,
  label,
  barClass,
}: {
  uuid: string;
  track: "user" | "agent";
  label: string;
  barClass: string;
}) {
  const store = useLiveStore();
  const barsRef = useRef<Array<HTMLSpanElement | null>>([]);

  useEffect(() => {
    const reduced = prefersReducedMotion();
    let raf = 0;
    let current = 0;
    let lastPaint = 0;

    const frame = (t: number) => {
      raf = requestAnimationFrame(frame);
      // Reduced motion: no wobble/smoothing, repaint at most ~4x/sec.
      if (reduced && t - lastPaint < 250) return;
      lastPaint = t;

      const lv = store.getLevels(uuid);
      const fresh = lv && performance.now() - lv.at < STALE_MS;
      const target = fresh ? Math.max(0, Math.min(1, lv[track])) : 0;
      current = reduced ? target : current + (target - current) * 0.25;

      barsRef.current.forEach((bar, i) => {
        if (!bar) return;
        const wobble = reduced ? 1 : 0.82 + 0.18 * Math.sin(t / 110 + i * 1.7);
        const pct = MIN_PCT + (100 - MIN_PCT) * Math.min(1, current * 1.6 * SHAPE[i] * wobble);
        bar.style.height = `${pct.toFixed(1)}%`;
      });
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [store, uuid, track]);

  return (
    <div className="flex items-center gap-2">
      <div className="flex h-7 items-end gap-[3px]" aria-hidden>
        {SHAPE.map((_, i) => (
          <span
            key={i}
            ref={(el) => {
              barsRef.current[i] = el;
            }}
            className={cn("block w-2 rounded-full", barClass)}
            style={{ height: `${MIN_PCT}%` }}
          />
        ))}
      </div>
      <span className="text-xs font-semibold">{label}</span>
    </div>
  );
}
