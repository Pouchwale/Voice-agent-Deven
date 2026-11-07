"use client";

import { useEffect, useRef } from "react";
import { countUp } from "@/lib/anim";

/** Animated number (anime.js). Animates from the previously shown value to the new one. */
export function CountUp({
  value,
  format = (n) => Math.round(n).toLocaleString("en-IN"),
  className,
}: {
  value: number;
  format?: (n: number) => string;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const prev = useRef(0);
  const fmt = useRef(format);
  fmt.current = format;

  useEffect(() => {
    if (!ref.current) return;
    const cancel = countUp(ref.current, prev.current, value, (n) => fmt.current(n));
    prev.current = value;
    return cancel;
  }, [value]);

  return (
    <span ref={ref} className={className}>
      {format(0)}
    </span>
  );
}
