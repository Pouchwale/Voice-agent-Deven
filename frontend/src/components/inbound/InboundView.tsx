"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  CircleCheck,
  Clock,
  FunnelX,
  LoaderCircle,
  PhoneCall,
  PhoneForwarded,
  PhoneIncoming,
  PhoneMissed,
  RefreshCw,
  Settings2,
  UserCheck,
  UserPlus,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import useSWR, { useSWRConfig } from "swr";
import { presetRange } from "@/components/analytics/range";
import { KpiTile } from "@/components/dashboard/KpiTile";
import { useCallUpdates } from "@/components/providers/LiveProvider";
import { Chip, Dot } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { SearchInput } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Pagination } from "@/components/ui/Pagination";
import { Segmented, Select } from "@/components/ui/Select";
import { Skeleton, SkeletonRows } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { useToast } from "@/components/ui/Toast";
import { useDebounced } from "@/hooks/useDebounced";
import { useEntrance } from "@/hooks/useEntrance";
import { useNow } from "@/hooks/useNow";
import { prefersReducedMotion } from "@/lib/anim";
import { keys } from "@/lib/api";
import { cn } from "@/lib/cn";
import { CALLS_PAGE_SIZE } from "@/lib/config";
import {
  callOutcome,
  formatDayLabel,
  formatDuration,
  formatNumber,
  formatPercent,
  formatPhone,
  isCallActive,
  pluralize,
  ymdIST,
} from "@/lib/format";
import type { AgentSettings, CallerType, CallListItem, CallListParams, InboundSummary, Page } from "@/lib/types";
import { callerTitle, epochMs, missedReason } from "./caller";
import { InboundCallDrawer } from "./InboundCallDrawer";
import { InboundCallsTable } from "./InboundCallsTable";
import { InboundSetupCard } from "./InboundSetupCard";
import { LiveInbound } from "./LiveInbound";
import { useInboundSetup } from "./useInboundSetup";
import { useLiveInbound, useLiveSynced } from "./useLiveInbound";

type RangeKey = "today" | "7d" | "30d";
type Tab = "callback" | "all" | "answered" | "missed";
type InboundState = "checking" | "on" | "off" | "disconnected";

const RANGES: { value: RangeKey; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
];
const RANGE_TITLE: Record<RangeKey, string> = { today: "Today", "7d": "Last 7 days", "30d": "Last 30 days" };
const RANGE_PHRASE: Record<RangeKey, string> = { today: "today", "7d": "in the last 7 days", "30d": "in the last 30 days" };

const TABS: { id: Tab; label: string }[] = [
  { id: "callback", label: "To call back" },
  { id: "all", label: "All" },
  { id: "answered", label: "Answered" },
  { id: "missed", label: "Missed" },
];
const TAB_PARAMS: Record<Tab, Pick<CallListParams, "outcome" | "follow_up">> = {
  callback: { follow_up: "pending" },
  all: {},
  answered: { outcome: "answered" },
  missed: { outcome: "missed" },
};

const CALLER_FILTERS: { value: CallerType | ""; label: string }[] = [
  { value: "", label: "All callers" },
  { value: "returning", label: "Returning callers" },
  { value: "new", label: "New callers" },
  { value: "known", label: "Existing leads" },
  { value: "anonymous", label: "Hidden numbers" },
  { value: "international", label: "International" },
];

// Inbound events refresh the page at once; outbound ones only move the follow-up column, so they can be batched.
const INBOUND_REFRESH_MS = 600;
const OUTBOUND_REFRESH_MS = 5000;
// A missed call first seen already ended (inbound off, lines busy) still gets a toast if it is this fresh.
const TOAST_FRESH_MS = 3 * 60_000;

function InboundStatusChip({ state }: { state: InboundState }) {
  if (state === "on") {
    return (
      <Chip color="primary" icon={<Dot className="bg-primary" live />}>
        Answering calls
      </Chip>
    );
  }
  if (state === "off") {
    return (
      <Chip color="neutral" icon={<Dot className="bg-subtle-fg" />}>
        Inbound off
      </Chip>
    );
  }
  if (state === "disconnected") {
    return (
      <Chip color="white" icon={<Dot className="bg-fg" />}>
        Not connected
      </Chip>
    );
  }
  return (
    <Chip color="white" icon={<LoaderCircle className="spin" aria-hidden />}>
      Checking…
    </Chip>
  );
}

/** "To call back · All · Answered · Missed" (WAI-ARIA tabs: arrow keys, Home/End). */
function CallTabs({ tab, onSelect, toCallBack }: { tab: Tab | null; onSelect: (t: Tab) => void; toCallBack: number | null }) {
  const refs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({});
  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    const i = Math.max(0, TABS.findIndex((t) => t.id === tab));
    let n: number;
    if (e.key === "ArrowRight") n = (i + 1) % TABS.length;
    else if (e.key === "ArrowLeft") n = (i - 1 + TABS.length) % TABS.length;
    else if (e.key === "Home") n = 0;
    else if (e.key === "End") n = TABS.length - 1;
    else return;
    e.preventDefault();
    const next = TABS[n].id;
    onSelect(next);
    refs.current[next]?.focus();
  }
  return (
    <div
      role="tablist"
      aria-label="Which inbound calls to show"
      onKeyDown={onKey}
      className="scroll-thin flex overflow-x-auto border-b border-line px-2 sm:px-3"
    >
      {TABS.map((t) => {
        const active = t.id === tab;
        return (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[t.id] = el;
            }}
            type="button"
            role="tab"
            id={`inbound-tab-${t.id}`}
            aria-selected={active}
            aria-controls="inbound-tabpanel"
            tabIndex={active || (!tab && t.id === "all") ? 0 : -1}
            onClick={() => onSelect(t.id)}
            className={cn(
              "relative inline-flex h-11 shrink-0 items-center gap-1.5 px-3 text-sm font-medium transition-colors",
              active ? "text-primary" : "text-muted hover:text-fg",
            )}
          >
            {t.label}
            {t.id === "callback" && toCallBack !== null ? (
              <span
                className={cn(
                  "tabular inline-flex min-w-5 items-center justify-center rounded-full px-1.5 text-xs font-medium",
                  toCallBack > 0 ? "bg-brand text-white" : "bg-subtle text-subtle-fg",
                )}
              >
                {formatNumber(toCallBack)}
              </span>
            ) : null}
            {active ? <span className="absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-primary" aria-hidden /> : null}
          </button>
        );
      })}
    </div>
  );
}

function KpiSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-4 2xl:grid-cols-7" role="status" aria-label="Loading inbound numbers">
      {Array.from({ length: 7 }).map((_, i) => (
        <Skeleton key={i} className={cn("h-[112px] rounded-xl", i === 0 && "col-span-2 2xl:col-span-1")} />
      ))}
    </div>
  );
}

export function InboundView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const toast = useToast();
  // Narrow live-feed views: this page must not re-render (with its table) on every transcript chunk.
  const liveInbound = useLiveInbound();
  const liveSynced = useLiveSynced();
  const now = useNow(60_000);
  const today = ymdIST(new Date(now));

  // ---- URL state (shareable; the toast links straight to a call) ----
  const rawRange = params.get("range");
  const range: RangeKey = rawRange === "7d" || rawRange === "30d" ? rawRange : "today";
  const rawTab = params.get("tab");
  const urlTab = TABS.find((t) => t.id === rawTab)?.id ?? null;
  const rawCaller = params.get("caller") ?? "";
  const callerType = (CALLER_FILTERS.find((c) => c.value && c.value === rawCaller)?.value ?? "") as CallerType | "";
  const page = Math.max(1, Math.floor(Number(params.get("page"))) || 1);
  const urlSearch = params.get("search") ?? "";
  const openUuid = params.get("call") || null;
  const { from, to } = presetRange(range, today);

  const setParams = useCallback(
    (patch: Record<string, string | number | null>, resetPage = true) => {
      const next = new URLSearchParams(params.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === "") next.delete(k);
        else next.set(k, String(v));
      }
      if (resetPage && !("page" in patch)) next.delete("page");
      const s = next.toString();
      router.replace(s ? `${pathname}?${s}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  const [q, setQ] = useState(urlSearch);
  const debouncedQ = useDebounced(q.trim(), 300);
  useEffect(() => {
    if (debouncedQ !== urlSearch) setParams({ search: debouncedQ || null });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to the debounced input
  }, [debouncedQ]);

  // ---- setup status (header chip + setup card) ----
  const setup = useInboundSetup();
  const { data: settings } = useSWR<AgentSettings>(keys.settings, { revalidateOnFocus: false });
  const enabled = setup.data?.enabled ?? settings?.inbound_enabled;
  const inboundState: InboundState =
    enabled === false
      ? "off"
      : !setup.data
        ? setup.error
          ? "disconnected"
          : "checking"
        : setup.fullPending
          ? "checking"
          : setup.data.ready
            ? "on"
            : "disconnected";

  // ---- KPIs for the selected range ----
  const summary = useSWR<InboundSummary>(keys.inboundSummary({ from, to }), { keepPreviousData: true, refreshInterval: 60_000 });
  const sum = summary.data;
  const sumFresh = !!sum && sum.from === from && sum.to === to;
  const totals = sum?.totals;
  // On "Today", point out older missed callers still waiting (they'd otherwise be invisible until the range changes).
  const week = presetRange("7d", today);
  const weekSummary = useSWR<InboundSummary>(range === "today" ? keys.inboundSummary(week) : null, { refreshInterval: 120_000 });
  const weekPending = weekSummary.data?.totals?.to_call_back;
  const olderPending =
    range === "today" && sumFresh && totals && typeof weekPending === "number"
      ? Math.max(0, weekPending - (totals.to_call_back ?? 0))
      : 0;

  // ---- table tab: "To call back" by default when somebody is waiting (decided once, so it never jumps) ----
  const [autoTab, setAutoTab] = useState<Tab | null>(null);
  useEffect(() => {
    if (autoTab) return;
    if (sum) setAutoTab((sum.totals?.to_call_back ?? 0) > 0 ? "callback" : "all");
    else if (summary.error) setAutoTab("all");
  }, [autoTab, sum, summary.error]);
  const tab: Tab | null = urlTab ?? autoTab;

  const callsKey = tab
    ? keys.calls({
        direction: "inbound",
        channel: "phone",
        from,
        to,
        caller_type: callerType,
        search: urlSearch,
        page,
        page_size: CALLS_PAGE_SIZE,
        ...TAB_PARAMS[tab],
      })
    : null;
  const calls = useSWR<Page<CallListItem>>(callsKey, {
    keepPreviousData: true,
    // keep the log fresh while calls are running / being analysed (the live feed covers the rest)
    refreshInterval: (d) => (d?.items?.some((c) => isCallActive(c.status) || c.analysis_status === "pending") ? 5000 : 30_000),
  });
  // Rows from the previous tab / filter stay visible (dimmed) until the new ones arrive.
  const [settledKey, setSettledKey] = useState<string | null>(null);
  useEffect(() => {
    if (calls.data && !calls.isValidating && !calls.error) setSettledKey(callsKey);
  }, [calls.data, calls.isValidating, calls.error, callsKey]);
  const laggy = !!calls.data && calls.isValidating && settledKey !== callsKey;

  // A stale ?page= past the end jumps to the last real page instead of showing an empty list.
  const lastPage = calls.data ? Math.max(1, Math.ceil(calls.data.total / (calls.data.page_size || CALLS_PAGE_SIZE))) : 1;
  const pastEnd = !!calls.data && !calls.isValidating && calls.data.items.length === 0 && calls.data.total > 0 && page > lastPage;
  useEffect(() => {
    if (pastEnd) setParams({ page: lastPage > 1 ? lastPage : null }, false);
  }, [pastEnd, lastPage, setParams]);

  // ---- live feed: coalesced refresh of the list + KPIs (never the Vobiz setup check) and a toast per missed call ----
  const { mutate: globalMutate } = useSWRConfig();
  const refreshTimer = useRef<{ id: ReturnType<typeof setTimeout>; due: number } | null>(null);
  const scheduleRefresh = useCallback(
    (ms: number) => {
      const due = Date.now() + ms;
      if (refreshTimer.current) {
        if (refreshTimer.current.due <= due) return;
        clearTimeout(refreshTimer.current.id);
      }
      refreshTimer.current = {
        due,
        id: setTimeout(() => {
          refreshTimer.current = null;
          void globalMutate(
            (key) => typeof key === "string" && (key.startsWith("/api/calls") || key.startsWith("/api/inbound/summary")),
          );
        }, ms),
      };
    },
    [globalMutate],
  );
  useEffect(
    () => () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current.id);
    },
    [],
  );

  const seenActive = useRef(new Set<string>());
  const toasted = useRef(new Set<string>());
  const paramsRef = useRef(params.toString());
  useEffect(() => {
    paramsRef.current = params.toString();
  }, [params]);
  useEffect(() => {
    for (const lc of liveInbound) seenActive.current.add(lc.call.uuid);
  }, [liveInbound]);

  const onCallUpdate = useCallback(
    (call: CallListItem) => {
      if (call.channel !== "phone") return; // browser tests never change these numbers
      scheduleRefresh(call.direction === "inbound" ? INBOUND_REFRESH_MS : OUTBOUND_REFRESH_MS);
      if (call.direction !== "inbound") return;
      if (isCallActive(call.status)) {
        seenActive.current.add(call.uuid);
        return;
      }
      if (callOutcome(call) !== "missed" || toasted.current.has(call.uuid)) return;
      // Only calls missed just now: a late update for an old call (re-link, re-analysis) must not raise a toast.
      const endedMs = epochMs(call.ended_at ?? call.created_at);
      const fresh = seenActive.current.has(call.uuid) || (Number.isFinite(endedMs) && Date.now() - endedMs < TOAST_FRESH_MS);
      if (!fresh) return;
      toasted.current.add(call.uuid);
      const next = new URLSearchParams(paramsRef.current);
      next.set("call", call.uuid);
      const canCallBack = call.caller_type !== "anonymous" && !!call.lead_id;
      toast({
        tone: "info",
        title: `Missed call from ${callerTitle(call, "an unknown caller", "a hidden number")}`,
        description: missedReason(call) ?? undefined,
        action: { label: canCallBack ? "Call back" : "View call", href: `${pathname}?${next.toString()}` },
        duration: 12_000,
      });
    },
    [scheduleRefresh, toast, pathname],
  );
  useCallUpdates(onCallUpdate);

  // ---- actions ----
  const tableRef = useRef<HTMLElement>(null);
  const showTab = useCallback(
    (t: Tab) => {
      setParams({ tab: t });
      tableRef.current?.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
    },
    [setParams],
  );
  const openCall = useCallback((c: CallListItem) => setParams({ call: c.uuid }, false), [setParams]);
  const closeDrawer = useCallback(() => setParams({ call: null }, false), [setParams]);
  const hasFilters = !!(urlSearch || callerType);
  const clearFilters = () => {
    setQ("");
    setParams({ search: null, caller: null });
  };
  const refreshNow = () => {
    void summary.mutate();
    void calls.mutate();
    void weekSummary.mutate();
  };

  const rootRef = useEntrance<HTMLDivElement>(true);
  const rows = calls.data?.items ?? [];
  const fallback = openUuid ? rows.find((c) => c.uuid === openUuid) : undefined;
  const liveCount = liveSynced ? liveInbound.length : (sum?.live ?? liveInbound.length);
  const ourNumber = setup.data?.number ? formatPhone(setup.data.number) : "your number";

  return (
    <div ref={rootRef}>
      <PageHeader
        title="Inbound calls"
        description="Customers who call your Vobiz number. The agent answers, remembers returning callers and logs every call here."
        actions={
          <>
            <InboundStatusChip state={inboundState} />
            <ButtonLink href="/settings#calling" size="sm" icon={<Settings2 />}>
              Inbound settings
            </ButtonLink>
          </>
        }
      />

      <div className="flex flex-col gap-5">
        <InboundSetupCard setup={setup} />
        <LiveInbound calls={liveInbound} />

        <section aria-labelledby="inbound-kpis-title" data-anim>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <h2 id="inbound-kpis-title" className="font-heading text-base font-semibold">
                {RANGE_TITLE[range]}
              </h2>
              <p className="text-[13px] text-muted">
                {from === to ? formatDayLabel(from) : `${formatDayLabel(from)} – ${formatDayLabel(to)}`} · IST
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Segmented<RangeKey>
                label="Date range"
                value={range}
                onChange={(v) => setParams({ range: v === "today" ? null : v })}
                options={RANGES}
              />
              <Button
                size="sm"
                variant="ghost"
                icon={<RefreshCw className={summary.isValidating || calls.isValidating ? "spin" : undefined} />}
                onClick={refreshNow}
              >
                Refresh
              </Button>
            </div>
          </div>

          {summary.error && !sum ? (
            <ErrorState error={summary.error} onRetry={() => void summary.mutate()} title="Couldn't load the inbound numbers" />
          ) : !totals ? (
            <KpiSkeleton />
          ) : (
            <div
              className={cn(
                "grid grid-cols-2 gap-4 transition-opacity md:grid-cols-4 2xl:grid-cols-7",
                !sumFresh && summary.isValidating && "opacity-60",
              )}
            >
              <KpiTile
                className="col-span-2 2xl:col-span-1"
                label="Inbound calls"
                value={totals.calls}
                icon={<PhoneIncoming />}
                indicator={liveCount > 0 ? <Dot className="bg-primary" live /> : undefined}
                highlight={liveCount > 0 ? "primary" : undefined}
                caption={
                  liveCount > 0
                    ? `${formatNumber(liveCount)} live now`
                    : totals.anonymous > 0
                      ? `${pluralize(totals.anonymous, "call")} from hidden numbers`
                      : "Calls to your number"
                }
              />
              <KpiTile
                label="Answered"
                value={totals.answered}
                icon={<PhoneCall />}
                onClick={() => showTab("answered")}
                caption={totals.calls > 0 ? `${formatPercent(totals.answered / totals.calls)} of calls` : "Talked with the agent"}
              />
              <KpiTile
                label="Missed"
                value={totals.missed}
                icon={<PhoneMissed />}
                onClick={() => showTab("missed")}
                caption={totals.calls > 0 ? `${formatPercent(totals.missed / totals.calls)} of calls` : "Off, busy or hung up early"}
              />
              <KpiTile
                label="To call back"
                value={totals.to_call_back}
                icon={<PhoneForwarded />}
                highlight={totals.to_call_back > 0 ? "brand" : undefined}
                onClick={() => showTab("callback")}
                caption={totals.to_call_back > 0 ? "Nobody has called them back yet" : "All caught up"}
              />
              <KpiTile label="New callers" value={totals.new_callers} icon={<UserPlus />} caption="First call created their lead" />
              <KpiTile label="Returning callers" value={totals.returning_callers} icon={<UserCheck />} caption="Spoke with us before" />
              <KpiTile
                label="Avg duration"
                value={totals.avg_duration_sec}
                format={(n) => formatDuration(n)}
                icon={<Clock />}
                caption="Answered calls"
              />
            </div>
          )}
          {summary.error && sum ? (
            <ErrorState
              className="mt-3"
              compact
              error={summary.error}
              onRetry={() => void summary.mutate()}
              title="Showing the last loaded numbers"
            />
          ) : null}
        </section>

        {olderPending > 0 ? (
          <div
            role="status"
            className="flex flex-wrap items-center gap-3 rounded-xl border border-primary-tint-strong bg-primary-tint px-4 py-3"
          >
            <PhoneForwarded className="size-4 shrink-0 text-primary" aria-hidden />
            <p className="min-w-0 flex-1 text-sm">
              <span className="font-medium">
                {pluralize(olderPending, "more missed caller")} from the last 7 days{" "}
                {olderPending === 1 ? "is" : "are"} still waiting for a call back.
              </span>
            </p>
            <Button size="sm" onClick={() => setParams({ range: "7d", tab: "callback" })}>
              Show them
            </Button>
          </div>
        ) : null}

        <section
          ref={tableRef}
          aria-labelledby="inbound-calls-title"
          className="scroll-mt-20 overflow-hidden rounded-xl border border-line bg-white"
          data-anim
        >
          <h2 id="inbound-calls-title" className="sr-only">
            Inbound calls
          </h2>
          <CallTabs tab={tab} onSelect={(t) => setParams({ tab: t })} toCallBack={sumFresh && totals ? totals.to_call_back : null} />
          <div className="flex flex-wrap items-center gap-3 border-b border-line p-3 sm:px-4">
            <SearchInput
              value={q}
              onChange={setQ}
              placeholder="Search name, number or company"
              label="Search inbound calls"
              className="w-full sm:w-72"
            />
            <Select<CallerType | "">
              aria-label="Filter by caller"
              pill
              className="w-full sm:w-48"
              value={callerType}
              onChange={(v) => setParams({ caller: v || null })}
              options={CALLER_FILTERS}
            />
            {hasFilters ? (
              <Button size="sm" variant="ghost" icon={<FunnelX />} onClick={clearFilters}>
                Clear filters
              </Button>
            ) : null}
          </div>

          <div id="inbound-tabpanel" role="tabpanel" aria-labelledby={tab ? `inbound-tab-${tab}` : undefined}>
            {calls.error && !calls.data ? (
              <div className="p-4">
                <ErrorState error={calls.error} onRetry={() => void calls.mutate()} title="Couldn't load inbound calls" />
              </div>
            ) : !tab || !calls.data || (calls.data.items.length === 0 && calls.data.total > 0) ? (
              <SkeletonRows rows={6} cols={7} />
            ) : calls.data.items.length === 0 ? (
              <TabEmpty tab={tab} range={range} hasFilters={hasFilters} onClear={clearFilters} ourNumber={ourNumber} />
            ) : (
              <>
                {calls.error ? (
                  <div className="border-b border-line p-3">
                    <ErrorState compact error={calls.error} onRetry={() => void calls.mutate()} title="Showing the last loaded calls" />
                  </div>
                ) : null}
                <InboundCallsTable calls={calls.data.items} selectedUuid={openUuid} onOpen={openCall} dim={laggy} />
                <Pagination
                  page={calls.data.page || page}
                  pageSize={calls.data.page_size || CALLS_PAGE_SIZE}
                  total={calls.data.total}
                  onPage={(p) => setParams({ page: p > 1 ? p : null }, false)}
                  busy={calls.isValidating && !calls.isLoading}
                />
              </>
            )}
          </div>
        </section>
      </div>

      <InboundCallDrawer uuid={openUuid} fallback={fallback} onClose={closeDrawer} />
    </div>
  );
}

function TabEmpty({
  tab,
  range,
  hasFilters,
  onClear,
  ourNumber,
}: {
  tab: Tab;
  range: RangeKey;
  hasFilters: boolean;
  onClear: () => void;
  ourNumber: string;
}) {
  if (hasFilters) {
    return (
      <EmptyState
        icon={<FunnelX />}
        title="No calls match"
        body="Try a different search or caller filter."
        action={
          <Button icon={<FunnelX />} onClick={onClear}>
            Clear filters
          </Button>
        }
      />
    );
  }
  const phrase = RANGE_PHRASE[range];
  const copy: Record<Tab, { icon: ReactNode; title: string; body: string; tone?: "primary" }> = {
    callback: {
      icon: <CircleCheck />,
      tone: "primary",
      title: "Nobody to call back",
      body: `Every missed caller ${phrase} has been called back or spoken to since.`,
    },
    all: {
      icon: <PhoneIncoming />,
      title: `No inbound calls ${phrase}`,
      body: `When customers call ${ourNumber}, the agent answers and the call shows up here within seconds.`,
    },
    answered: { icon: <PhoneCall />, title: "No answered calls", body: `No caller talked with the agent ${phrase}.` },
    missed: { icon: <PhoneMissed />, title: "No missed calls", body: `The agent answered every call ${phrase}.` },
  };
  const c = copy[tab];
  return <EmptyState icon={c.icon} tone={c.tone} title={c.title} body={c.body} />;
}
