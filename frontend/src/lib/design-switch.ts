"use client";

import { flushSync } from "react-dom";
import { prefersReducedMotion } from "./anim";
import { DESIGN_ATTRIBUTE, DESIGN_STORAGE_KEY, type Design } from "./design";

/*
 * Switching between the original and the PouchWale design (see lib/design.ts).
 *
 * The switch plays a "melt": the design on screen melts away from its top edge down like a thick layer, with
 * rounded molten drips, a soft shadow beneath its edge, sparks, ash and paper flakes and falling molten drops,
 * and uncovers the other design, which is already in place underneath. It runs on
 * the View Transitions API: the design being left is a still snapshot lying on top, so nothing re-lays-out or
 * fades mid-animation, and every moving part is a keyframed animation in the browser's own animation engine
 * (no per-frame JavaScript). Without View Transitions, with prefers-reduced-motion, or in a hidden tab, the
 * design changes instantly.
 */

const listeners = new Set<() => void>();
let storageBound = false;

/** The design on screen now (the boot script in <head> set it before the first paint). */
export function currentDesign(): Design {
  return document.documentElement.getAttribute(DESIGN_ATTRIBUTE) === "pouchwale" ? "pouchwale" : "original";
}

function savedDesign(): Design {
  try {
    return localStorage.getItem(DESIGN_STORAGE_KEY) === "pouchwale" ? "pouchwale" : "original";
  } catch {
    return currentDesign();
  }
}

function save(design: Design): void {
  try {
    localStorage.setItem(DESIGN_STORAGE_KEY, design);
  } catch {
    // storage blocked (private mode, policy): the switch still works until the page reloads
  }
}

function show(design: Design): void {
  const root = document.documentElement;
  if (design === "pouchwale") root.setAttribute(DESIGN_ATTRIBUTE, "pouchwale");
  else root.removeAttribute(DESIGN_ATTRIBUTE);
  for (const fn of listeners) fn();
}

/**
 * Puts the design on screen in one step: the switch re-renders synchronously and CSS transitions are off while
 * the new styles are computed, so no button or card fades between the two designs.
 */
function apply(design: Design): void {
  const root = document.documentElement;
  root.classList.add("design-instant");
  flushSync(() => show(design));
  void document.body.offsetWidth; // compute the new styles now, while transitions are off
  root.classList.remove("design-instant");
}

/** For useSyncExternalStore. A switch made in another tab of this browser is followed here too. */
export function subscribeDesign(onChange: () => void): () => void {
  listeners.add(onChange);
  if (!storageBound) {
    storageBound = true;
    window.addEventListener("storage", (e) => {
      if (e.key !== null && e.key !== DESIGN_STORAGE_KEY) return;
      const next = savedDesign();
      if (next !== currentDesign()) apply(next);
    });
  }
  return () => {
    listeners.delete(onChange);
  };
}

/**
 * Loads the PouchWale fonts (Mada, Mulish; not preloaded, the original design never uses them), so the
 * PouchWale design is drawn in its own fonts from the first frame of the melt. Call it early (e.g. when the
 * pointer reaches the switch); it never waits more than 400 ms.
 */
export function warmUpDesignFonts(): Promise<unknown> {
  if (typeof document === "undefined" || !document.fonts) return Promise.resolve();
  const css = getComputedStyle(document.documentElement);
  const loads = ["--font-mada", "--font-mulish"]
    .map((v) => css.getPropertyValue(v).trim())
    .filter(Boolean)
    .map((family) => document.fonts.load(`700 16px ${family}`).catch(() => []));
  return Promise.race([Promise.all(loads), new Promise((done) => setTimeout(done, 400))]);
}

let target: Design | null = null; // the design a switch in progress is heading to
let request = 0;

/** Shows `next` with the melt and remembers it in this browser. The last request always wins. */
export async function switchDesign(next: Design): Promise<void> {
  if (next === (target ?? currentDesign())) return;
  const id = ++request;
  target = next;
  save(next);
  if (next === "pouchwale") await warmUpDesignFonts();
  if (id !== request) return; // a newer switch took over while the fonts loaded
  await melt(next);
  if (id === request) target = null;
}

// ---------------------------------------------------------------- melt transition

type ViewTransitionLike = { ready: Promise<void>; finished: Promise<void>; skipTransition(): void };
type StartViewTransition = (update: () => void) => ViewTransitionLike;
type Point = [number, number];
type Drip = { x: number; half: number; len: number; at: number };
type Particle = { el: Element; frames: Keyframe[]; delay: number; duration: number };

const MELT_MS = 1400;
const KEYFRAMES = 32; // per clip path; the browser interpolates between them
const SVG_NS = "http://www.w3.org/2000/svg";

/** The design melting away sheds flakes in its own colours, and molten drops in a hot tint of them. */
const MELTING: Record<Design, { flakes: string[]; drop: string }> = {
  original: { flakes: ["#ffffff", "#f5f8fb", "#e2e8f0"], drop: "#ffe1bf" },
  pouchwale: { flakes: ["#0b2a45", "#eef5fb", "#ffffff"], drop: "#1d4a70" },
};

let running: ViewTransitionLike | null = null;

async function melt(next: Design): Promise<void> {
  const start = (document as Document & { startViewTransition?: StartViewTransition }).startViewTransition;
  running?.skipTransition(); // a second switch finishes the running melt at once
  document.querySelectorAll(".design-melt-fx, .design-melt-under").forEach((el) => el.remove()); // a melt cut short
  if (!start || prefersReducedMotion() || document.visibilityState !== "visible") {
    apply(next);
    return;
  }

  const w = window.innerWidth;
  const h = window.innerHeight;
  const edge = meltEdge(w, h);
  const { edgeFrames, lineFrames } = meltFrames(edge, w, h);
  const { under, strokes } = meltShadow(w, h);
  const { fx, particles } = meltEffects(edge, w, h, MELTING[next === "pouchwale" ? "original" : "pouchwale"]);
  const root = document.documentElement;
  // Every effect animation is created now (its keyframes parsed before the snapshot) and paused on its first frame;
  // the edge animation can only be created once its snapshot exists. All of them start in the same task, on the
  // same clock, so the shadow and the flakes stay exactly on the melting edge.
  const timing: KeyframeAnimationOptions = { duration: MELT_MS, easing: "linear", fill: "both" };
  const lineAnims = strokes.map((path) => path.animate(lineFrames, timing));
  const particleAnims = particles.map((p) => p.el.animate(p.frames, { duration: p.duration, delay: p.delay, easing: "linear", fill: "both" }));
  const anims = [...lineAnims, ...particleAnims];
  for (const a of anims) a.pause();
  root.classList.add("design-melting");
  let vt: ViewTransitionLike;
  try {
    vt = start.call(document, () => {
      apply(next);
      // the shadow is part of the new design's picture (under the melting snapshot); the flakes are their own
      // transition group, drawn above both designs
      document.body.append(under, fx);
    });
  } catch {
    for (const a of anims) a.cancel();
    root.classList.remove("design-melting");
    apply(next);
    return;
  }
  running = vt;
  let played = false;
  try {
    await vt.ready;
    anims.push(root.animate(edgeFrames, { ...timing, pseudoElement: "::view-transition-old(root)" }));
    for (const a of anims) a.play();
    played = true;
    await vt.finished;
  } catch {
    // skipped (a second switch, the tab was hidden): the new design has been applied all the same
  } finally {
    under.remove(); // by now the shadow has left the screen with the edge
    if (running === vt) {
      running = null;
      root.classList.remove("design-melting");
    }
  }
  if (!played) for (const a of anims) a.cancel();
  // the last flakes and drops settle over the new design (the layer never takes clicks), then it goes
  await Promise.allSettled(particleAnims.map((a) => a.finished));
  fx.remove();
}

/**
 * The melting edge: y (px) of the boundary at x and progress t (0..1). A slow wavy front moves down the screen
 * while rounded drips start at different moments and run ahead of it; at t = 1 everything is below the screen.
 */
function meltEdge(w: number, h: number): { at: (x: number, t: number) => number; drips: Drip[] } {
  const amp = Math.max(6, h * 0.012);
  const dripMax = h * 0.14;
  const waves = [1, -1].map((dir) => ({ len: w * (0.5 + Math.random() * 0.4), phase: Math.random() * 6.283, speed: dir * (0.5 + Math.random() * 0.5) }));
  const count = Math.max(5, Math.round(w / 105));
  const drips: Drip[] = Array.from({ length: count }, (_, i) => ({
    x: ((i + 0.15 + Math.random() * 0.7) / count) * w,
    half: 7 + Math.random() * 9,
    len: dripMax * (0.3 + Math.random() * 0.7),
    at: Math.random() * 0.3,
  }));
  const from = amp * 1.2; // starts at the very top: the edge is visible from the first frame
  const to = h + dripMax + amp * 4; // ends with every drip below the screen
  const at = (x: number, t: number): number => {
    let y = from + (to - from) * easeInOutSine(t);
    for (const wave of waves) y += amp * Math.sin((6.283 * x) / wave.len + wave.phase + wave.speed * t * 3);
    for (const d of drips) {
      const u = (x - d.x) / d.half;
      y += d.len * smoothstep(d.at, d.at + 0.5, t) * Math.exp(-(u ** 4));
    }
    return y;
  };
  return { at, drips };
}

/**
 * Keyframes for the edge: the snapshot of the design being left keeps only what is below it (clip-path), and the
 * shadow follows it (path d). Every keyframe has the same commands, so the browser interpolates them.
 */
function meltFrames(
  edge: { at: (x: number, t: number) => number; drips: Drip[] },
  w: number,
  h: number,
): { edgeFrames: Keyframe[]; lineFrames: Keyframe[] } {
  // x samples: a coarse grid plus a dense cluster on every drip, so the rounded drip tips are drawn exactly
  const xs = new Set<number>([w]);
  for (let x = 0; x < w; x += 40) xs.add(x);
  for (const d of edge.drips) {
    for (const u of [-1.6, -1.1, -0.7, -0.35, 0, 0.35, 0.7, 1.1, 1.6]) {
      const x = Math.round(d.x + u * d.half);
      if (x > 0 && x < w) xs.add(x);
    }
  }
  const X = [...xs].sort((a, b) => a - b).filter((x, i, all) => i === 0 || x - all[i - 1] >= 2);
  const edgeFrames: Keyframe[] = [];
  const lineFrames: Keyframe[] = [];
  for (let f = 0; f <= KEYFRAMES; f++) {
    const t = f / KEYFRAMES;
    const pts: Point[] = X.map((x) => [x, edge.at(x, t)]);
    const line = `M0 ${r(pts[0][1])}${curve(pts)}`;
    edgeFrames.push({ offset: t, clipPath: `path("${line} L${w} ${r(h + 600)} L0 ${r(h + 600)} Z")` });
    lineFrames.push({ offset: t, d: `path("${line}")` });
  }
  return { edgeFrames, lineFrames };
}

/**
 * The shadow the melting design casts on the design underneath: soft dark strokes centred on the melting edge.
 * They are drawn on the new design (part of its picture, not of the effects group), so the melting snapshot on
 * top hides their lower half and only the shadow beneath its edge shows - it reads as a thick layer melting
 * off. Stacked translucent strokes, not a blur filter: a blurred full-screen layer costs a long first frame.
 */
function meltShadow(w: number, h: number): { under: HTMLDivElement; strokes: SVGPathElement[] } {
  const under = document.createElement("div");
  under.className = "design-melt-under";
  under.setAttribute("aria-hidden", "true");
  const svg = svgEl("svg", { class: "design-melt-shadow", viewBox: `0 0 ${w} ${h}` });
  const strokes = [
    [52, 0.045],
    [34, 0.06],
    [20, 0.08],
    [9, 0.13],
  ].map(([width, opacity]) => svgEl("path", { stroke: "#101828", "stroke-width": width, "stroke-opacity": opacity }));
  svg.append(...strokes);
  under.append(svg);
  return { under, strokes };
}

/**
 * The effects layer above both designs: what breaks off the melting edge. Sparks fly up, ash and paper flakes in
 * the melting design's colours flutter away, and molten drops fall from the drip tips. Everything is drawn in
 * one SVG (no extra compositing layers) and moved by keyframed animations on the same clock as the edge.
 */
function meltEffects(
  edge: { at: (x: number, t: number) => number; drips: Drip[] },
  w: number,
  h: number,
  melting: { flakes: string[]; drop: string },
): { fx: HTMLDivElement; particles: Particle[] } {
  const fx = document.createElement("div");
  fx.className = "design-melt-fx";
  fx.setAttribute("aria-hidden", "true");
  const svg = svgEl("svg", { class: "design-melt-rim", viewBox: `0 0 ${w} ${h}` });
  fx.append(svg);

  const particles: Particle[] = [];
  const add = (el: SVGElement, t: number, life: number, frames: Keyframe[]) => {
    el.setAttribute("class", "design-melt-p");
    svg.append(el);
    particles.push({ el, delay: t * MELT_MS, duration: life, frames });
  };
  // a point on the visible part of the edge at progress t, or null when that part is off screen
  const onEdge = (t: number, x = rand(0, w)): Point | null => {
    const y = edge.at(x, t);
    return y > 0 && y < h ? [x, y] : null;
  };

  // sparks: glowing specks (a warm halo around a bright core) that fly up from the melting edge and die out.
  // Nothing breaks off in the first tenth of the melt, while the browser is still busy drawing the new design.
  for (let i = Math.round(w / 36); i > 0; i--) {
    const t = rand(0.1, 0.9);
    const at = onEdge(t);
    if (!at) continue;
    const spark = svgEl("g", {});
    spark.append(
      svgEl("circle", { r: r(rand(4.5, 8)), fill: "#ff7a2e", "fill-opacity": "0.4" }),
      svgEl("circle", { r: r(rand(1.6, 2.8)), fill: pick(["#fff3c4", "#ffe6a8", "#ffd27a"]) }),
    );
    const life = rand(420, 820);
    add(spark, t, life, flight({ at, v: [rand(-110, 110), rand(-300, -110)], gravity: 160, wobble: rand(3, 10), spin: 0, flip: 0, life, fade: [0.1, 0.55], scale: [1, 0.3], stretch: 1 }));
  }
  // flakes: ash and burnt paper in the melting design's colours, lifted by the heat, then fluttering down
  for (let i = Math.round(w / 50); i > 0; i--) {
    const t = rand(0.1, 0.85);
    const at = onEdge(t);
    if (!at) continue;
    const ash = Math.random() < 0.45;
    const shape = svgEl("polygon", {
      points: flakePoints(rand(3.5, 8)),
      fill: ash ? pick(["#2b1d14", "#4a2f20", "#6b4a33"]) : pick(melting.flakes),
      stroke: ash ? "none" : "#e2662a",
      "stroke-width": "0.9",
      "stroke-opacity": "0.85",
    });
    const life = rand(850, 1350);
    add(shape, t, life, flight({ at, v: [rand(-70, 70), rand(-140, -40)], gravity: rand(380, 560), wobble: rand(8, 22), spin: rand(-540, 540), flip: rand(240, 720) * (Math.random() < 0.5 ? -1 : 1), life, fade: [0.08, 0.7], scale: [1, 0.85], stretch: 1 }));
  }
  // molten drops: one breaks off the tip of each drip once it has run, and falls
  for (const d of edge.drips) {
    const t = Math.min(0.88, d.at + 0.32 + Math.random() * 0.25);
    const at = onEdge(t, d.x);
    if (!at || at[1] > h - 24) continue;
    const half = rand(3, 4.5);
    const drop = svgEl("path", {
      d: `M0 ${r(-half * 1.5)} C${r(half * 0.6)} ${r(-half * 0.6)} ${r(half)} 0 ${r(half)} ${r(half * 0.45)} A${r(half)} ${r(half)} 0 0 1 ${r(-half)} ${r(half * 0.45)} C${r(-half)} 0 ${r(-half * 0.6)} ${r(-half * 0.6)} 0 ${r(-half * 1.5)} Z`,
      fill: melting.drop,
      stroke: "#ff8a3d",
      "stroke-width": "1",
      "stroke-opacity": "0.85",
    });
    const life = rand(480, 720);
    add(drop, t, life, flight({ at, v: [rand(-8, 8), rand(30, 80)], gravity: rand(2100, 2700), wobble: 0, spin: 0, flip: 0, life, fade: [0.05, 0.85], scale: [0.8, 1], stretch: 1.7 }));
  }
  return { fx, particles };
}

/** Ballistic flight from a point on the edge: keyframes of position, spin, flutter, scale and fade. */
function flight(p: {
  at: Point;
  v: [number, number]; // px/s
  gravity: number; // px/s², positive is down
  wobble: number; // px of sideways sway
  spin: number; // degrees over the flight
  flip: number; // degrees of flutter over the flight (drawn as a vertical squash)
  life: number; // ms
  fade: [number, number]; // fully visible between these progress points
  scale: [number, number];
  stretch: number; // final vertical stretch (falling drops)
}): Keyframe[] {
  return [0, 0.12, 0.3, 0.55, 0.8, 1].map((k) => {
    const s = (k * p.life) / 1000;
    const x = p.at[0] + p.v[0] * s + p.wobble * Math.sin(k * 9.42);
    const y = p.at[1] + p.v[1] * s + 0.5 * p.gravity * s * s;
    const opacity = k < p.fade[0] ? k / p.fade[0] : k > p.fade[1] ? (1 - k) / (1 - p.fade[1]) : 1;
    const scale = p.scale[0] + (p.scale[1] - p.scale[0]) * k;
    const squash = Math.cos((p.flip * k * Math.PI) / 180);
    const sy = scale * (1 + (p.stretch - 1) * k) * (Math.abs(squash) < 0.15 ? Math.sign(squash || 1) * 0.15 : squash);
    return {
      offset: k,
      opacity: Math.round(opacity * 100) / 100,
      transform: `translate(${r(x)}px, ${r(y)}px) rotate(${r(p.spin * k)}deg) scale(${Math.round(scale * 100) / 100}, ${Math.round(sy * 100) / 100})`,
    };
  });
}

/** An irregular flake outline around (0, 0). */
function flakePoints(radius: number): string {
  const n = 6 + Math.floor(Math.random() * 3);
  return Array.from({ length: n }, (_, i) => {
    const a = (i / n) * 6.283 + rand(-0.25, 0.25);
    const rad = radius * rand(0.55, 1);
    return `${r(rad * Math.cos(a))},${r(rad * Math.sin(a))}`;
  }).join(" ");
}

function svgEl<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number>): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

function rand(a: number, b: number): number {
  return a + Math.random() * (b - a);
}

function pick<T>(items: T[]): T {
  return items[Math.floor(Math.random() * items.length)];
}

/** Smooth curve through the points (Catmull-Rom as cubic Béziers), starting from the current point p[0]. */
function curve(p: Point[]): string {
  let d = "";
  for (let i = 0; i < p.length - 1; i++) {
    const p0 = p[Math.max(0, i - 1)];
    const p1 = p[i];
    const p2 = p[i + 1];
    const p3 = p[Math.min(p.length - 1, i + 2)];
    d += ` C${r(p1[0] + (p2[0] - p0[0]) / 6)} ${r(p1[1] + (p2[1] - p0[1]) / 6)} ${r(p2[0] - (p3[0] - p1[0]) / 6)} ${r(
      p2[1] - (p3[1] - p1[1]) / 6,
    )} ${r(p2[0])} ${r(p2[1])}`;
  }
  return d;
}

function smoothstep(a: number, b: number, x: number): number {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
}

function easeInOutSine(t: number): number {
  return -(Math.cos(Math.PI * t) - 1) / 2;
}

function r(v: number): number {
  return Math.round(v * 10) / 10;
}
