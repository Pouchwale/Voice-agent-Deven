"use client";

import { useLayoutEffect, useRef, useState } from "react";

/** Tracks an element's content-box width with a ResizeObserver. */
export function useMeasure<T extends HTMLElement = HTMLDivElement>(initial = 0) {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(initial);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      setWidth(Math.round(w));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}
