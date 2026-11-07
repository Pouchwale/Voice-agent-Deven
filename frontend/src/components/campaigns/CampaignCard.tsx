"use client";

import { ChevronDown, Clock, Layers, Pause, Play, RotateCcw, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import { Chip } from "@/components/ui/Badge";
import { Button, IconButton } from "@/components/ui/Button";
import { InterestBadge } from "@/components/ui/InterestBadge";
import { ConfirmDialog } from "@/components/ui/Modal";
import { CampaignStatusChip } from "@/components/ui/StatusChip";
import { useToast } from "@/components/ui/Toast";
import { api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatNumber, formatShortDateTime } from "@/lib/format";
import type { Campaign, Interest } from "@/lib/types";
import { CampaignLeads } from "./CampaignLeads";
import { CampaignProgress } from "./CampaignProgress";

const INTEREST_SHOWN: Interest[] = ["cold", "warm", "hot", "unknown"];

export function CampaignCard({
  campaign,
  onChanged,
  onDeleted,
}: {
  campaign: Campaign;
  onChanged: (c: Campaign) => void;
  onDeleted: (id: number) => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState<"start" | "pause" | "delete" | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const leadsId = useId();
  const c = campaign;
  const running = c.status === "running";

  async function start() {
    setBusy("start");
    try {
      const next = await api.campaigns.start(c.id);
      onChanged(next);
      toast({
        tone: "success",
        title: c.status === "paused" ? `Resumed: ${c.name}` : `Started: ${c.name}`,
        description: "The agent is dialing the queue.",
        action: { label: "Watch live", href: "/live" },
      });
    } catch (err) {
      toast({ tone: "error", title: "Couldn't start campaign", description: errorMessage(err) });
    } finally {
      setBusy(null);
    }
  }

  async function pause() {
    setBusy("pause");
    try {
      const next = await api.campaigns.pause(c.id);
      onChanged(next);
      toast({ tone: "info", title: `Paused: ${c.name}`, description: "Calls already in progress will finish normally." });
    } catch (err) {
      toast({ tone: "error", title: "Couldn't pause campaign", description: errorMessage(err) });
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    setBusy("delete");
    try {
      await api.campaigns.remove(c.id);
      setConfirmDelete(false);
      onDeleted(c.id);
      toast({ tone: "success", title: `Deleted: ${c.name}` });
    } catch (err) {
      toast({ tone: "error", title: "Couldn't delete campaign", description: errorMessage(err) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <article
      data-anim
      className="rounded-xl border border-line bg-white"
      aria-labelledby={`${leadsId}-title`}
    >
      <div className="p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2.5">
              <h2 id={`${leadsId}-title`} className="font-heading text-base font-semibold break-words">
                {c.name}
              </h2>
              <CampaignStatusChip status={c.status} />
              {c.active_calls > 0 ? (
                <Chip size="sm" color="primary" icon={<span className="live-dot inline-block size-2 rounded-full border border-line bg-primary" aria-hidden />}>
                  {c.active_calls} on call
                </Chip>
              ) : null}
            </div>
            <p className="mt-1.5 text-[13px] font-normal text-muted">
              Created {formatShortDateTime(c.created_at)}
              {c.started_at ? ` · Started ${formatShortDateTime(c.started_at)}` : ""}
              {c.completed_at ? ` · Completed ${formatShortDateTime(c.completed_at)}` : ""}
            </p>
          </div>

          <div className="flex items-center gap-2">
            {running ? (
              <Button variant="secondary" size="sm" icon={<Pause />} loading={busy === "pause"} disabled={busy !== null} onClick={() => void pause()}>
                Pause
              </Button>
            ) : c.status !== "completed" ? (
              <Button variant="primary" size="sm" icon={<Play />} loading={busy === "start"} disabled={busy !== null} onClick={() => void start()}>
                {c.status === "paused" ? "Resume" : "Start"}
              </Button>
            ) : null}
            <IconButton
              label={`Delete campaign ${c.name}`}
              icon={<Trash2 />}
              variant="danger"
              size="sm"
              disabled={busy !== null}
              onClick={() => setConfirmDelete(true)}
            />
          </div>
        </div>

        <ul className="mt-3 flex flex-wrap gap-2" aria-label="Campaign settings">
          <li>
            <Chip size="xs" icon={<Layers aria-hidden />}>
              {c.concurrency} parallel {c.concurrency === 1 ? "line" : "lines"}
            </Chip>
          </li>
          <li>
            <Chip size="xs" icon={<RotateCcw aria-hidden />}>
              Max {c.max_attempts} {c.max_attempts === 1 ? "attempt" : "attempts"} · retry {c.retry_delay_min} min
            </Chip>
          </li>
          <li>
            <Chip size="xs" color={c.respect_call_window ? "neutral" : "white"} icon={<Clock aria-hidden />}>
              {c.respect_call_window ? "Calling window on" : "Calls any time"}
            </Chip>
          </li>
        </ul>

        <div className="mt-4">
          <CampaignProgress counts={c.counts} name={c.name} />
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2" aria-label="Interest so far">
          {INTEREST_SHOWN.map((i) => {
            const n = c.interest_counts?.[i] ?? 0;
            return (
              <span key={i} className={cn("inline-flex items-center gap-1.5", n === 0 && "opacity-60")}>
                <InterestBadge interest={i} size="xs" />
                <span className="tabular text-sm font-semibold">{formatNumber(n)}</span>
              </span>
            );
          })}
        </div>
      </div>

      <div className="border-t border-line">
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          aria-expanded={expanded}
          aria-controls={leadsId}
          className={cn(
            "flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-[12px] font-semibold transition-colors hover:bg-canvas sm:px-5",
            expanded ? "rounded-none" : "rounded-b-xl",
          )}
        >
          <span>{expanded ? "Hide leads" : `View leads (${formatNumber(c.counts.total)})`}</span>
          <ChevronDown className={cn("size-4 transition-transform duration-200", expanded && "rotate-180")} aria-hidden />
        </button>
        {expanded ? (
          <div id={leadsId} className="border-t border-line">
            <CampaignLeads id={c.id} running={running} total={c.counts.total} />
          </div>
        ) : null}
      </div>

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => void remove()}
        title="Delete campaign?"
        body={
          <>
            <p>
              <strong>{c.name}</strong> and its call queue will be removed. Leads and past call recordings are kept.
            </p>
            {running ? <p className="mt-2">It&apos;s running right now - calls in progress will finish, no new ones start.</p> : null}
          </>
        }
        confirmLabel="Delete campaign"
        danger
        busy={busy === "delete"}
      />
    </article>
  );
}
