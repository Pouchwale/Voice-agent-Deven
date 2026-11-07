import type { Interest } from "./types";

/** Raw palette values for places that need hex (SVG charts, three.js). Mirrors globals.css. */
export const THEME = {
  white: "#FFFFFF",
  canvas: "#F5F8FB",
  line: "#E2E8F0",
  subtle: "#F1F5F9",
  subtleFg: "#475569",
  muted: "#64748B",
  faint: "#94A3B8", // neutral chart series (total calls, end reasons)
  fg: "#0F172A",
  brand: "#0095D5", // exact logo blue
  primary: "#0077B3",
  primaryHover: "#00649A",
  primaryTint: "#E6F4FB",
  primaryTintStrong: "#CCE9F7",
  navy: "#0B3B5C",
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
