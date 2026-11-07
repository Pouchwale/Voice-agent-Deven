import { CircleDashed, Flame, Snowflake, Sun } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { INTEREST_LABEL } from "@/lib/format";
import { INTEREST_STYLE } from "@/lib/theme";
import type { Interest } from "@/lib/types";
import { Chip } from "./Badge";

export const INTEREST_ICON: Record<Interest, LucideIcon> = {
  unknown: CircleDashed,
  cold: Snowflake,
  warm: Sun,
  hot: Flame,
};

export function InterestBadge({
  interest,
  size = "sm",
  className,
}: {
  interest: Interest | null | undefined;
  size?: "xs" | "sm" | "md";
  className?: string;
}) {
  const i: Interest = interest ?? "unknown";
  return (
    <Chip custom={INTEREST_STYLE[i].chip} size={size} className={className}>
      {INTEREST_LABEL[i]}
    </Chip>
  );
}
