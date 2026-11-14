"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Earth,
  EyeOff,
  Globe,
  LoaderCircle,
  Phone,
  PhoneIncoming,
  PhoneOutgoing,
  UserCheck,
  UserPlus,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import { Chip, type ChipColor } from "@/components/ui/Badge";
import { InterestBadge } from "@/components/ui/InterestBadge";
import { RatingPill } from "@/components/ui/RatingPill";
import { CallStatusChip } from "@/components/ui/StatusChip";
import { cn } from "@/lib/cn";
import {
  CALLER_TYPE_LABEL,
  callTitle,
  DIRECTION_LABEL,
  endReasonLabel,
  FOLLOW_UP_LABEL,
  formatDuration,
  formatPhone,
  formatShortDateTime,
} from "@/lib/format";
import type { CallDirection, CallerType, CallListItem } from "@/lib/types";

export function ChannelIcon({ channel }: { channel: CallListItem["channel"] }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[13px] text-subtle-fg"
      title={channel === "browser" ? "Browser test call" : "Phone call"}
    >
      <span className="inline-flex text-muted [&_svg]:size-4">
        {channel === "browser" ? <Globe aria-hidden /> : <Phone aria-hidden />}
      </span>
      <span className="sr-only sm:not-sr-only">{channel === "browser" ? "Browser" : "Phone"}</span>
    </span>
  );
}

/** Inbound (the customer called us) or outbound (we called them). Rows from an older backend count as outbound. */
export function DirectionIcon({ direction }: { direction: CallDirection | null | undefined }) {
  const inbound = direction === "inbound";
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[13px] text-subtle-fg"
      title={inbound ? "Inbound: the customer called us" : "Outbound: we called the customer"}
    >
      <span className={cn("inline-flex [&_svg]:size-4", inbound ? "text-primary" : "text-muted")}>
        {inbound ? <PhoneIncoming aria-hidden /> : <PhoneOutgoing aria-hidden />}
      </span>
      <span className="sr-only sm:not-sr-only">{DIRECTION_LABEL[inbound ? "inbound" : "outbound"]}</span>
    </span>
  );
}

const CALLER_STYLE: Record<CallerType, { color: ChipColor; icon: LucideIcon }> = {
  returning: { color: "primary", icon: UserCheck },
  known: { color: "white", icon: UserRound },
  new: { color: "white", icon: UserPlus },
  international: { color: "neutral", icon: Earth },
  anonymous: { color: "neutral", icon: EyeOff },
};

/** Who an inbound caller is to us: returning, existing lead, new, international or hidden number. */
export function CallerTypeChip({ type }: { type: CallerType }) {
  const style = CALLER_STYLE[type] ?? CALLER_STYLE.known;
  const Icon = style.icon;
  return (
    <Chip size="xs" color={style.color} icon={<Icon aria-hidden />}>
      {CALLER_TYPE_LABEL[type] ?? type}
    </Chip>
  );
}

/** Call log table. Row click opens the call summary. */
export function CallsTable({ calls, showLead = true, dim = false }: { calls: CallListItem[]; showLead?: boolean; dim?: boolean }) {
  const router = useRouter();
  return (
    <div className={cn("relative overflow-x-auto transition-opacity", dim && "opacity-70")}>
      <table className={cn("data-table", showLead ? "min-w-[1040px]" : "min-w-[880px]")}>
        <thead>
          <tr>
            <th>Time (IST)</th>
            {showLead ? <th>Lead / caller</th> : null}
            <th>Direction</th>
            <th>Channel</th>
            <th>Status</th>
            <th>Interest</th>
            <th>Rating</th>
            <th className="text-right">Duration</th>
            <th>End reason</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((c) => {
            const inbound = c.direction === "inbound";
            return (
              <tr key={c.uuid} className="row-link" onClick={() => router.push(`/calls/${c.uuid}`)}>
                <td className="tabular whitespace-nowrap font-normal">
                  <Link href={`/calls/${c.uuid}`} className="hover:underline" onClick={(e) => e.stopPropagation()}>
                    {formatShortDateTime(c.started_at ?? c.created_at)}
                  </Link>
                </td>
                {showLead ? (
                  <td className="max-w-[240px]">
                    <span className="block truncate font-medium">{callTitle(c, inbound ? "Unknown caller" : "Unknown lead")}</span>
                    <span className="block truncate text-xs font-normal text-muted">
                      {[c.lead_company, formatPhone(c.lead_phone, "")].filter(Boolean).join(" · ")}
                    </span>
                  </td>
                ) : null}
                <td>
                  <div className="flex flex-col items-start gap-1">
                    <DirectionIcon direction={c.direction} />
                    {inbound && c.caller_type ? <CallerTypeChip type={c.caller_type} /> : null}
                  </div>
                </td>
                <td>
                  <ChannelIcon channel={c.channel} />
                </td>
                <td>
                  <CallStatusChip status={c.status} direction={c.direction} />
                  {c.follow_up && FOLLOW_UP_LABEL[c.follow_up] ? (
                    <span className="mt-1 block text-xs font-normal whitespace-nowrap text-muted">{FOLLOW_UP_LABEL[c.follow_up]}</span>
                  ) : null}
                </td>
                <td>
                  {c.analysis_status === "pending" ? (
                    <span className="inline-flex items-center gap-1 text-xs font-medium text-muted">
                      <LoaderCircle className="spin size-3.5" aria-hidden /> Analysing
                    </span>
                  ) : (
                    <InterestBadge interest={c.interest} size="xs" />
                  )}
                </td>
                <td>
                  <RatingPill rating={c.rating} />
                </td>
                <td className="tabular text-right font-medium">{formatDuration(c.duration_sec)}</td>
                <td className="whitespace-nowrap font-normal">{endReasonLabel(c.end_reason)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
