"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  Building2,
  Headphones,
  ListTree,
  Mail,
  MapPin,
  Pencil,
  Phone,
  PhoneCall,
  Sparkles,
  Table2,
  Trash2,
  UserRound,
} from "lucide-react";
import { useEffect, useId, useState, type ReactNode } from "react";
import useSWR, { useSWRConfig } from "swr";
import { CallsTable } from "@/components/calls/CallsTable";
import { useCallNow } from "@/components/calls/useCallNow";
import { useTestAgent } from "@/components/providers/TestAgentProvider";
import { CustomerTimeline } from "@/components/timeline/CustomerTimeline";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { InterestBadge } from "@/components/ui/InterestBadge";
import { ConfirmDialog } from "@/components/ui/Modal";
import { RatingPill } from "@/components/ui/RatingPill";
import { Segmented } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { LeadStatusChip } from "@/components/ui/StatusChip";
import { useToast } from "@/components/ui/Toast";
import { useBoundedInterval } from "@/hooks/useBoundedInterval";
import { useEntrance } from "@/hooks/useEntrance";
import { api, ApiError, errorMessage, keys } from "@/lib/api";
import { formatDateTime, formatNumber, formatPhone, isCallActive, leadName, telHref } from "@/lib/format";
import type { LeadWithCalls } from "@/lib/types";
import { LeadFormModal } from "./LeadFormModal";

function Detail({ label, children, icon }: { label: string; children: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex items-start gap-3 py-2.5">
      <span className="mt-0.5 shrink-0 [&_svg]:size-4" aria-hidden>
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <dt className="text-xs font-semibold text-muted">{label}</dt>
        <dd className="mt-0.5 break-words text-[15px] font-normal">{children}</dd>
      </div>
    </div>
  );
}

export function LeadDetailView({ id }: { id: number }) {
  const router = useRouter();
  const toast = useToast();
  const { mutate: globalMutate } = useSWRConfig();
  const testAgent = useTestAgent();
  const { callNow, callingId, outboundOff, outboundHint } = useCallNow();
  const [historyView, setHistoryView] = useState<"timeline" | "table">("timeline");
  const callHintId = useId();
  const validId = Number.isInteger(id) && id > 0;
  const [pollEvery, setPollEvery] = useState(0);
  const { data, error, isLoading, mutate } = useSWR<LeadWithCalls>(validId ? keys.lead(id) : null, {
    refreshInterval: pollEvery,
  });
  // Poll while a call is running or being analysed - bounded, so a stuck call doesn't poll forever.
  const busyCalls = !!data?.calls?.some((c) => c.analysis_status === "pending" || isCallActive(c.status));
  const poll = useBoundedInterval(busyCalls, 4000, 15 * 60_000);
  useEffect(() => setPollEvery(poll.interval), [poll.interval]);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);
  const rootRef = useEntrance<HTMLDivElement>(!!data);

  async function remove() {
    if (!data) return;
    setBusy(true);
    try {
      await api.leads.remove(data.id);
      toast({ tone: "success", title: "Lead deleted", description: leadName(data) });
      void globalMutate((key) => typeof key === "string" && (key.startsWith("/api/leads") || key.startsWith("/api/dashboard")));
      router.push("/leads");
    } catch (err) {
      toast({ tone: "error", title: "Couldn't delete lead", description: errorMessage(err) });
      setBusy(false);
    }
  }

  const back = (
    <Link href="/leads" className="mb-4 inline-flex items-center gap-1.5 text-[12px] font-semibold hover:underline">
      <ArrowLeft className="size-4" aria-hidden /> All leads
    </Link>
  );

  if (!data || (error instanceof ApiError && error.status === 404)) {
    if (!validId || (error instanceof ApiError && error.status === 404)) {
      return (
        <div>
          {back}
          <EmptyState icon={<UserRound />} title="Lead not found" body="It may have been deleted." action={<ButtonLink href="/leads">Back to leads</ButtonLink>} />
        </div>
      );
    }
    return (
      <div>
        {back}
        {error ? (
          <ErrorState error={error} onRetry={() => void mutate()} title="Couldn't load this lead" />
        ) : isLoading ? (
          <div className="space-y-5" role="status" aria-label="Loading lead">
            <Skeleton className="h-12 w-72" />
            <div className="grid gap-5 lg:grid-cols-3">
              <Skeleton className="h-80 rounded-xl" />
              <Skeleton className="h-80 rounded-xl lg:col-span-2" />
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  const lead = data;
  const calls = data.calls ?? [];

  return (
    <div ref={rootRef}>
      {back}
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4" data-anim>
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <InterestBadge interest={lead.interest} size="md" />
            <RatingPill rating={lead.rating} />
            <LeadStatusChip status={lead.status} />
          </div>
          <h1 className="font-heading font-semibold tracking-tight break-words text-[22px] sm:text-2xl">{leadName(lead)}</h1>
          <p className="mt-2 text-[15px] font-normal text-muted">
            {[lead.company, lead.city, formatPhone(lead.phone)].filter(Boolean).join(" · ")}
          </p>
        </div>
        <div className="flex flex-wrap gap-2.5">
          <Button
            variant="primary"
            icon={<PhoneCall />}
            loading={callingId === lead.id}
            disabled={lead.status === "dnc" || outboundOff}
            aria-describedby={outboundHint ? callHintId : undefined}
            title={outboundHint ?? undefined}
            onClick={() => void callNow({ id: lead.id, name: leadName(lead) })}
          >
            Call now
          </Button>
          <Button icon={<Headphones />} onClick={() => testAgent.open({ lead })}>
            Test in browser
          </Button>
          <Button icon={<Pencil />} onClick={() => setEditing(true)}>
            Edit
          </Button>
          <Button variant="danger" icon={<Trash2 />} onClick={() => setDeleting(true)}>
            Delete
          </Button>
          {outboundHint ? (
            <p id={callHintId} className="w-full text-[13px] text-muted">
              {outboundHint}{" "}
              <Link href="/settings#calling" className="font-medium text-primary hover:underline">
                Open Settings
              </Link>
            </p>
          ) : null}
        </div>
      </header>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card data-anim>
          <CardHeader title="Details" />
          <dl className="-mt-1">
            <Detail label="Phone" icon={<Phone />}>
              <a href={telHref(lead.phone)} className="tabular hover:underline">
                {formatPhone(lead.phone)}
              </a>
            </Detail>
            <Detail label="Company" icon={<Building2 />}>
              {lead.company ?? "—"}
            </Detail>
            <Detail label="City" icon={<MapPin />}>
              {lead.city ?? "—"}
            </Detail>
            <Detail label="Email" icon={<Mail />}>
              {lead.email ? (
                <a href={`mailto:${lead.email}`} className="hover:underline">
                  {lead.email}
                </a>
              ) : (
                "—"
              )}
            </Detail>
            <Detail label="Source" icon={<Sparkles />}>
              {lead.source ?? "—"}
            </Detail>
            <Detail label="Calls" icon={<PhoneCall />}>
              {formatNumber(lead.attempts)} attempts · last contact {formatDateTime(lead.last_called_at, "never")}
            </Detail>
          </dl>
          {lead.notes ? (
            <div className="mt-3 rounded-lg border border-line bg-canvas p-3">
              <p className="field-label">Notes</p>
              <p className="text-mixed text-sm font-normal whitespace-pre-wrap">{lead.notes}</p>
            </div>
          ) : null}
          <p className="mt-4 text-xs font-normal text-muted">
            Added {formatDateTime(lead.created_at)} · updated {formatDateTime(lead.updated_at)}
          </p>
        </Card>

        <div className="flex min-w-0 flex-col gap-5 lg:col-span-2">
          {lead.last_summary ? (
            <section className="rounded-xl border border-line bg-canvas p-5" data-anim>
              <p className="text-[13px] font-medium text-muted">Latest call summary</p>
              <p className="text-mixed mt-2 text-[16px] leading-relaxed font-normal">{lead.last_summary}</p>
              {calls[0] ? (
                <ButtonLink href={`/calls/${calls[0].uuid}`} size="sm" className="mt-3">
                  Open full summary
                </ButtonLink>
              ) : null}
            </section>
          ) : null}

          <section className="overflow-hidden rounded-xl border border-line bg-white" data-anim aria-labelledby={`${callHintId}-history`}>
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3 sm:px-5">
              <div className="min-w-0">
                <h2 id={`${callHintId}-history`} className="font-heading font-semibold tracking-tight text-base">
                  {historyView === "timeline" ? "Customer timeline" : "Call history"}
                </h2>
                {historyView === "table" ? (
                  <p className="text-[13px] font-medium text-muted">{formatNumber(calls.length)} calls</p>
                ) : null}
              </div>
              <Segmented<"timeline" | "table">
                label="History view"
                size="sm"
                value={historyView}
                onChange={setHistoryView}
                options={[
                  { value: "timeline", label: "Timeline", icon: <ListTree aria-hidden /> },
                  { value: "table", label: "Table", icon: <Table2 aria-hidden /> },
                ]}
              />
            </div>
            {historyView === "timeline" ? (
              <div className="p-4 sm:p-5">
                <CustomerTimeline leadId={lead.id} limit={30} />
              </div>
            ) : calls.length ? (
              <CallsTable calls={calls} showLead={false} />
            ) : (
              <EmptyState
                icon={<PhoneCall />}
                tone="subtle"
                title="Not called yet"
                body="Call now, add this lead to a campaign, or try the conversation in your browser first."
              />
            )}
          </section>
        </div>
      </div>

      <LeadFormModal open={editing} lead={lead} onClose={() => setEditing(false)} onSaved={() => void mutate()} />
      <ConfirmDialog
        open={deleting}
        onClose={() => setDeleting(false)}
        onConfirm={() => void remove()}
        title="Delete lead?"
        body={
          <>
            <strong>{leadName(lead)}</strong> and their call history will be removed. This can&apos;t be undone.
          </>
        }
        confirmLabel="Delete"
        danger
        busy={busy}
      />
    </div>
  );
}
