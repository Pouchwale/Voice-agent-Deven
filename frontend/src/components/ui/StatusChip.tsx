import { CALL_STATUS_LABEL, LEAD_STATUS_LABEL } from "@/lib/format";
import type { CallStatus, CampaignStatus, LeadStatus } from "@/lib/types";
import { Chip, Dot, type ChipColor } from "./Badge";

// Status is carried by the label; dots are blue (active/done) or neutral (everything else).
const LEAD: Record<LeadStatus, { color: ChipColor; dot: string; live?: boolean }> = {
  new: { color: "white", dot: "bg-primary" },
  queued: { color: "white", dot: "bg-faint" },
  calling: { color: "primary", dot: "bg-primary", live: true },
  called: { color: "white", dot: "bg-brand" },
  failed: { color: "white", dot: "bg-fg" },
  dnc: { color: "neutral", dot: "bg-subtle-fg" },
};

export function LeadStatusChip({ status }: { status: LeadStatus }) {
  const s = LEAD[status] ?? LEAD.new;
  return (
    <Chip size="xs" color={s.color} icon={<Dot className={s.dot} live={s.live} />}>
      {LEAD_STATUS_LABEL[status] ?? status}
    </Chip>
  );
}

const CALL: Record<CallStatus, { color: ChipColor; dot: string; live?: boolean }> = {
  queued: { color: "white", dot: "bg-faint" },
  initiated: { color: "primary", dot: "bg-primary", live: true },
  ringing: { color: "primary", dot: "bg-primary", live: true },
  in_progress: { color: "primary", dot: "bg-primary", live: true },
  completed: { color: "white", dot: "bg-brand" },
  no_answer: { color: "white", dot: "bg-faint" },
  busy: { color: "white", dot: "bg-faint" },
  failed: { color: "white", dot: "bg-fg" },
  canceled: { color: "white", dot: "bg-faint" },
};

export function CallStatusChip({ status }: { status: CallStatus }) {
  const s = CALL[status] ?? CALL.queued;
  return (
    <Chip size="xs" color={s.color} icon={<Dot className={s.dot} live={s.live} />}>
      {CALL_STATUS_LABEL[status] ?? status}
    </Chip>
  );
}

const CAMPAIGN: Record<CampaignStatus, { color: ChipColor; dot: string; label: string; live?: boolean }> = {
  draft: { color: "white", dot: "bg-faint", label: "Draft" },
  running: { color: "primary", dot: "bg-primary", label: "Running", live: true },
  paused: { color: "neutral", dot: "bg-subtle-fg", label: "Paused" },
  completed: { color: "white", dot: "bg-brand", label: "Completed" },
};

export function CampaignStatusChip({ status }: { status: CampaignStatus }) {
  const c = CAMPAIGN[status] ?? CAMPAIGN.draft;
  return (
    <Chip size="sm" color={c.color} icon={<Dot className={c.dot} live={c.live} />}>
      {c.label}
    </Chip>
  );
}
