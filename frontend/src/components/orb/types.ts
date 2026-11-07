export type OrbState = "idle" | "listening" | "thinking" | "speaking";

export interface VoiceOrbProps {
  state: OrbState;
  /** 0..1 audio level (for slow-changing values). */
  level?: number;
  /** Polled every frame - use for high-frequency audio levels without React re-renders. */
  getLevel?: () => number;
  className?: string;
  label?: string;
}

/** Single colour per state: brand blue speaking, light blue thinking, grey listening, border grey idle. */
export const ORB_COLOR: Record<OrbState, string> = {
  idle: "#E2E8F0",
  listening: "#94A3B8",
  thinking: "#CCE9F7",
  speaking: "#0095D5",
};
