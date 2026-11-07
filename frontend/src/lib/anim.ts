"use client";

import anime from "animejs";

/** Motion tokens (ms): subtle only, everything <= 200ms with ease-out. */
export const DUR = { fast: 120, base: 160, slow: 200 } as const;
export const EASE_OUT = "easeOutCubic";

export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Quick fade-in of a container (no per-item stagger). */
export function fadeIn(el: Element | null): void {
  if (!el || prefersReducedMotion()) return;
  anime.remove(el);
  anime({
    targets: el,
    opacity: [0, 1],
    duration: DUR.slow,
    easing: EASE_OUT,
    complete: () => {
      (el as HTMLElement).style.opacity = "";
    },
  });
}

/** Number count-up into an element's textContent. Returns a cancel fn. */
export function countUp(el: HTMLElement, from: number, to: number, format: (n: number) => string, duration = DUR.slow): () => void {
  if (prefersReducedMotion() || from === to) {
    el.textContent = format(to);
    return () => {};
  }
  const state = { v: from };
  const inst = anime({
    targets: state,
    v: to,
    duration,
    easing: EASE_OUT,
    update: () => {
      el.textContent = format(state.v);
    },
    complete: () => {
      el.textContent = format(to);
    },
  });
  return () => inst.pause();
}

/** Animate an element's width (e.g. progress bar) to a percentage. */
export function animateWidth(el: HTMLElement, toPct: number, duration: number = DUR.slow): void {
  const target = `${Math.max(0, Math.min(100, toPct))}%`;
  if (prefersReducedMotion()) {
    el.style.width = target;
    return;
  }
  anime.remove(el);
  anime({ targets: el, width: target, duration, easing: EASE_OUT });
}

export { anime };
