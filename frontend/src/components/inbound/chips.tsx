import { CircleCheck, Clock, EyeOff, Globe, PhoneIncoming, PhoneOutgoing, UserCheck, UserPlus, UserRound } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Chip, Dot, type ChipColor } from "@/components/ui/Badge";
import { CALLER_TYPE_LABEL, CALL_REASON_LABEL, FOLLOW_UP_LABEL } from "@/lib/format";
import type { CallDirection, CallerType, CallReason, FollowUp } from "@/lib/types";

type Size = "xs" | "sm";

// Returning callers are the warm ones (they spoke with us before), so they get the blue chip.
const CALLER: Record<CallerType, { color: ChipColor; icon: LucideIcon }> = {
  returning: { color: "primary", icon: UserCheck },
  known: { color: "white", icon: UserRound },
  new: { color: "white", icon: UserPlus },
  international: { color: "neutral", icon: Globe },
  anonymous: { color: "neutral", icon: EyeOff },
};

/** "Returning caller" / "New caller" / "Existing lead" / "International" / "Hidden number". */
export function CallerChip({ type, size = "xs" }: { type: CallerType | null | undefined; size?: Size }) {
  const cfg = type ? CALLER[type] : undefined;
  if (!type || !cfg) return null;
  const Icon = cfg.icon;
  return (
    <Chip size={size} color={cfg.color} icon={<Icon aria-hidden />}>
      {CALLER_TYPE_LABEL[type]}
    </Chip>
  );
}

export function DirectionChip({ direction, size = "xs" }: { direction: CallDirection; size?: Size }) {
  const inbound = direction === "inbound";
  return (
    <Chip size={size} color="white" icon={inbound ? <PhoneIncoming aria-hidden /> : <PhoneOutgoing aria-hidden />}>
      {inbound ? "Inbound" : "Outbound"}
    </Chip>
  );
}

/** Why an inbound caller called (from the post-call analysis). */
export function ReasonChip({ reason, size = "xs" }: { reason: CallReason | null | undefined; size?: Size }) {
  if (!reason) return null;
  return (
    <Chip size={size} color="white">
      {CALL_REASON_LABEL[reason] ?? reason}
    </Chip>
  );
}

const FOLLOW_UP: Record<FollowUp, { color: ChipColor; icon: ReactNode }> = {
  pending: { color: "primary", icon: <Dot className="bg-primary" /> },
  tried: { color: "white", icon: <Clock aria-hidden /> },
  done: { color: "white", icon: <CircleCheck aria-hidden /> },
};

/** Missed inbound calls: "To call back" / "Tried, no answer yet" / "Spoke since". */
export function FollowUpChip({ value, size = "xs" }: { value: FollowUp | null | undefined; size?: Size }) {
  const cfg = value ? FOLLOW_UP[value] : undefined;
  if (!value || !cfg) return null;
  return (
    <Chip size={size} color={cfg.color} icon={cfg.icon}>
      {FOLLOW_UP_LABEL[value]}
    </Chip>
  );
}
