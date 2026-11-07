"use client";

import { useLayoutEffect, useRef } from "react";
import { fadeIn } from "@/lib/anim";

/** A short fade-in of the page container once `ready` turns true (once per mount; no staggers). */
export function useEntrance<T extends HTMLElement = HTMLDivElement>(ready = true) {
  const ref = useRef<T>(null);
  const done = useRef(false);
  useLayoutEffect(() => {
    if (!ready || done.current) return;
    done.current = true;
    fadeIn(ref.current);
  }, [ready]);
  return ref;
}
