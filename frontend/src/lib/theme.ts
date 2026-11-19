import type { Interest } from "./types";

/**
 * A palette colour for places that set colours directly (SVG chart fill/stroke, inline styles): the CSS
 * variable `--theme-<name>` with the original colour as fallback. The original design never sets these
 * variables, so it renders exactly the hex values below; the PouchWale design (app/pouchwale.css) sets some.
 * Not for places that need a raw hex (canvas, three.js - the voice orb has its own ORB_COLOR).
 */
const themed = (name: string, hex: string) => `var(--theme-${name}, ${hex})`;

/** Palette for SVG charts. Mirrors globals.css. */
export const THEME = {
  white: themed("white", "#FFFFFF"),
  canvas: themed("canvas", "#F5F8FB"),
  line: themed("line", "#E2E8F0"),
  subtle: themed("subtle", "#F1F5F9"),
  subtleFg: themed("subtle-fg", "#475569"),
  muted: themed("muted", "#64748B"),
  faint: themed("faint", "#94A3B8"), // neutral chart series (total calls, end reasons)
  fg: themed("fg", "#0F172A"),
  brand: themed("brand", "#0095D5"), // exact logo blue
  primary: themed("primary", "#0077B3"),
  primaryHover: themed("primary-hover", "#00649A"),
  primaryTint: themed("primary-tint", "#E6F4FB"),
  primaryTintStrong: themed("primary-tint-strong", "#CCE9F7"),
  navy: themed("navy", "#0B3B5C"),
} as const;

/**
 * Interest tiers as an ordered blue ramp (always shown with a text label):
 * unknown = white + border (not yet scored), cold = slate tint, warm = light blue, hot = brand blue.
 */
export const INTEREST_STYLE: Record<Interest, { fill: string; text: string; chip: string }> = {
  unknown: { fill: THEME.white, text: THEME.muted, chip: "bg-white text-muted border-line" },
  cold: { fill: THEME.subtle, text: THEME.subtleFg, chip: "bg-subtle text-subtle-fg border-line" },
  warm: { fill: THEME.primaryTint, text: THEME.primaryHover, chip: "bg-primary-tint text-primary-hover border-primary-tint-strong" },
  hot: { fill: THEME.brand, text: THEME.white, chip: "bg-brand text-white font-semibold border-brand" },
};

/** Active-state classes for interest filters (segmented controls). */
export const INTEREST_ACTIVE: Record<Interest, string> = {
  unknown: "bg-white text-fg border-line",
  cold: "bg-subtle text-subtle-fg border-line",
  warm: "bg-primary-tint text-primary-hover border-primary-tint-strong",
  hot: "bg-brand text-white border-brand",
};
