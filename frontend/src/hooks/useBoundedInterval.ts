"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * A polling interval for SWR that gives up after `maxMs` of continuous activity, so a backend that never
 * leaves "pending" (crashed analysis, a call stuck "in_progress") isn't polled forever.
 * `interval` is 0 while inactive or once expired; `restart()` starts a fresh window (e.g. "Check again").
 */
export function useBoundedInterval(active: boolean, every: number, maxMs: number) {
  const [expired, setExpired] = useState(false);
  const [epoch, setEpoch] = useState(0);

  useEffect(() => {
    setExpired(false);
    if (!active) return;
    const t = setTimeout(() => setExpired(true), maxMs);
    return () => clearTimeout(t);
  }, [active, maxMs, epoch]);

  const restart = useCallback(() => setEpoch((e) => e + 1), []);

  return { interval: active && !expired ? every : 0, expired: active && expired, restart };
}
