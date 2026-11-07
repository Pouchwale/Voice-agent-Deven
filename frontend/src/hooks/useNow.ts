"use client";

import { useEffect, useState } from "react";

/** Current epoch ms, re-rendering every `interval` ms (for live timers / relative times). */
export function useNow(interval = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), interval);
    return () => clearInterval(t);
  }, [interval]);
  return now;
}
