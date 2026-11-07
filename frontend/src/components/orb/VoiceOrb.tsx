"use client";

import dynamic from "next/dynamic";
import { cn } from "@/lib/cn";
import { ORB_COLOR, type VoiceOrbProps } from "./types";

/** Lazy three.js orb (client only). Shows a flat circle while three.js loads. */
const VoiceOrbCanvas = dynamic(() => import("./VoiceOrbCanvas"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full w-full items-center justify-center" aria-hidden>
      <div className="aspect-square h-[58%] rounded-full" style={{ background: ORB_COLOR.idle }} />
    </div>
  ),
});

export function VoiceOrb(props: VoiceOrbProps) {
  return <VoiceOrbCanvas {...props} className={cn("h-full w-full", props.className)} />;
}

export type { OrbState, VoiceOrbProps } from "./types";
