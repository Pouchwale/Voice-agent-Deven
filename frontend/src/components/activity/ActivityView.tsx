"use client";

import { Download, FunnelX, History, UserRound } from "lucide-react";
import { useState } from "react";
import { ValueChange } from "@/components/review/ValueChange";
import { Chip } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { SearchInput } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { useToast } from "@/components/ui/Toast";
import { useDebounced } from "@/hooks/useDebounced";
import { useEntrance } from "@/hooks/useEntrance";
import { useLogPages } from "@/hooks/useLogPages";
import { api, errorMessage, keys } from "@/lib/api";
import { formatSettingValue } from "@/lib/diff";
import { formatNumber, formatShortDateTime } from "@/lib/format";
import type { ActivityArea, ActivityEntry } from "@/lib/types";

const PAGE = 50;

const AREA_LABEL: Record<ActivityArea, string> = {
  settings: "Settings",
  leads: "Leads",
  campaigns: "Campaigns",
  calls: "Calls",
  inbound: "Inbound",
  review: "Call review",
  other: "Other",
};

const AREA_OPTIONS: { value: ActivityArea | ""; label: string }[] = [
  { value: "", label: "All changes" },
  ...(Object.keys(AREA_LABEL) as ActivityArea[]).map((value) => ({ value, label: AREA_LABEL[value] })),
];

export function ActivityView() {
  const [area, setArea] = useState<ActivityArea | "">("");
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim(), 300);
  const filter = { area: area || undefined, q: q || undefined };
  const firstKey = keys.activity(filter);
  const filtered = !!(area || q);
  const rootRef = useEntrance<HTMLDivElement>();

  return (
    <div ref={rootRef}>
      <PageHeader
        title="Activity log"
        description="Every change made in this dashboard: when it was made and what changed. Changes to Settings also show who made them. Entries are kept forever and can't be edited or deleted."
        actions={
          <ButtonLink href={api.activity.exportUrl()} variant="secondary" icon={<Download />} download>
            Download CSV
          </ButtonLink>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <SearchInput
          value={search}
          onChange={setSearch}
          placeholder="Search a person or a change"
          label="Search the activity log"
          className="w-full sm:w-72"
        />
        <Select<ActivityArea | "">
          aria-label="Part of the dashboard"
          value={area}
          onChange={setArea}
          options={AREA_OPTIONS}
          className="w-full sm:w-44"
        />
      </div>

      <section className="overflow-hidden rounded-xl border border-line bg-white" data-anim>
        <ActivityList
          key={firstKey}
          firstKey={firstKey}
          filter={filter}
          filtered={filtered}
          onClear={() => {
            setArea("");
            setSearch("");
          }}
        />
      </section>
    </div>
  );
}

function ActivityList({
  firstKey,
  filter,
  filtered,
  onClear,
}: {
  firstKey: string;
  filter: { area?: ActivityArea; q?: string };
  filtered: boolean;
  onClear: () => void;
}) {
  const toast = useToast();
  const log = useLogPages<ActivityEntry>(firstKey, (beforeId) => api.activity.list({ ...filter, before_id: beforeId, limit: PAGE }));

  if (log.error && !log.items.length) {
    return <ErrorState error={log.error} onRetry={() => void log.mutate()} title="Couldn't load the activity log" />;
  }
  if (!log.loaded) {
    return (
      <div className="space-y-2 p-4" role="status" aria-label="Loading the activity log">
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-14 rounded-lg" />
        ))}
      </div>
    );
  }
  if (!log.items.length) {
    return filtered ? (
      <EmptyState
        icon={<FunnelX />}
        title="No changes match"
        body="Try a different search or show all changes."
        action={
          <Button icon={<FunnelX />} onClick={onClear}>
            Clear filters
          </Button>
        }
      />
    ) : (
      <EmptyState
        icon={<History />}
        title="No changes yet"
        body="From now on every change made in this dashboard appears here: settings, leads, campaigns, calls and more."
      />
    );
  }

  const remaining = Math.max(0, log.total - log.items.length);
  async function showOlder() {
    try {
      await log.loadOlder();
    } catch (err) {
      toast({ tone: "error", title: "Couldn't load older changes", description: errorMessage(err) });
    }
  }

  return (
    <>
      <p className="tabular border-b border-line px-4 py-2.5 text-[13px] text-muted" aria-live="polite">
        {formatNumber(log.total)} {log.total === 1 ? "change" : "changes"}
        {filtered ? " match" : " recorded"}
      </p>
      <ul className="divide-y divide-line">
        {log.items.map((entry) => (
          <ActivityRow key={entry.id} entry={entry} />
        ))}
      </ul>
      {log.hasMore ? (
        <div className="flex justify-center border-t border-line p-3">
          <Button variant="secondary" size="sm" onClick={() => void showOlder()} loading={log.loadingOlder}>
            Show older changes{remaining ? ` (${formatNumber(remaining)})` : ""}
          </Button>
        </div>
      ) : null}
    </>
  );
}

function plainValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return String(value);
  return formatSettingValue(value, 600);
}

function ActivityRow({ entry }: { entry: ActivityEntry }) {
  const [open, setOpen] = useState(false);
  const details = entry.details ?? {};
  const changes = details.changes ?? [];
  const facts = details.data ? Object.entries(details.data) : [];
  const names = details.items ?? [];
  const hasDetails = changes.length + facts.length + names.length > 0;

  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <Chip size="xs">{AREA_LABEL[entry.area] ?? entry.area}</Chip>
        <span className="tabular text-[12px] text-muted">{formatShortDateTime(entry.created_at)}</span>
        {/* Only Settings changes are made under a name (their confirm popup asks for it). */}
        {entry.actor ? (
          <span className="inline-flex min-w-0 items-center gap-1 text-[12px] font-semibold text-fg">
            <UserRound className="size-3 shrink-0" aria-hidden />
            <span className="sr-only">By </span>
            <span className="text-mixed min-w-0 break-words">{entry.actor}</span>
          </span>
        ) : null}
      </div>
      <p className="text-mixed mt-1 text-sm font-medium break-words">{entry.summary}</p>
      {hasDetails ? (
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="mt-1 text-[12px] font-medium text-primary hover:underline"
        >
          {open ? "Hide details" : "Show details"}
        </button>
      ) : null}
      {open ? (
        <div className="mt-3 space-y-3">
          {/* Outlined, not tinted: the old value is grey text, which needs the white background to stay readable. */}
          {changes.map((c) => (
            <div key={c.key} className="rounded-lg border border-line p-2.5">
              <p className="text-[12px] font-semibold text-subtle-fg">{c.label}</p>
              <ValueChange before={c.old} after={c.new} className="mt-1" />
            </div>
          ))}
          {facts.length ? (
            <dl className="grid gap-x-4 gap-y-1.5 rounded-lg border border-line p-2.5 text-[13px] sm:grid-cols-[max-content_minmax(0,1fr)]">
              {facts.map(([label, value]) => (
                <div key={label} className="contents">
                  <dt className="text-[12px] font-semibold text-subtle-fg sm:pt-px">{label}</dt>
                  <dd className="text-mixed min-w-0 break-words whitespace-pre-wrap">{plainValue(value)}</dd>
                </div>
              ))}
            </dl>
          ) : null}
          {names.length ? (
            <ul className="text-mixed space-y-0.5 rounded-lg border border-line p-2.5 text-[13px]">
              {names.map((name, i) => (
                <li key={i} className="break-words">
                  {name}
                </li>
              ))}
              {details.more ? <li className="text-muted">and {formatNumber(details.more)} more</li> : null}
            </ul>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}
