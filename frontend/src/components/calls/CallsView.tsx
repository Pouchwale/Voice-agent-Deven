"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { FunnelX, Headphones, PhoneCall, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import useSWR from "swr";
import { useTestAgent } from "@/components/providers/TestAgentProvider";
import { Button, ButtonLink } from "@/components/ui/Button";
import { INTEREST_ICON } from "@/components/ui/InterestBadge";
import { SearchInput } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Pagination } from "@/components/ui/Pagination";
import { Segmented, Select } from "@/components/ui/Select";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { useDebounced } from "@/hooks/useDebounced";
import { useEntrance } from "@/hooks/useEntrance";
import { keys } from "@/lib/api";
import { INTEREST_ACTIVE } from "@/lib/theme";
import { CALLS_PAGE_SIZE } from "@/lib/config";
import { CALL_STATUSES, CALL_STATUS_LABEL, formatNumber, INTEREST_ALL, INTEREST_LABEL } from "@/lib/format";
import type { CallListItem, CallStatus, Channel, Interest, Page } from "@/lib/types";
import { CallsTable } from "./CallsTable";

export function CallsView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const testAgent = useTestAgent();

  const status = (CALL_STATUSES as string[]).includes(params.get("status") ?? "") ? (params.get("status") as CallStatus) : "";
  const interest = (INTEREST_ALL as string[]).includes(params.get("interest") ?? "") ? (params.get("interest") as Interest) : "";
  const channel = params.get("channel") === "phone" || params.get("channel") === "browser" ? (params.get("channel") as Channel) : "";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const urlSearch = params.get("search") ?? "";

  const [q, setQ] = useState(urlSearch);
  const debouncedQ = useDebounced(q.trim(), 300);

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

  useEffect(() => {
    if (debouncedQ !== urlSearch) setParams({ search: debouncedQ || null });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to the debounced input
  }, [debouncedQ]);

  const { data, error, isLoading, isValidating, mutate } = useSWR<Page<CallListItem>>(
    keys.calls({ search: urlSearch, status, interest, channel, page, page_size: CALLS_PAGE_SIZE }),
    {
      keepPreviousData: true,
      // keep the log fresh while calls are running / being analysed
      refreshInterval: (d) =>
        d?.items?.some((c) => c.analysis_status === "pending" || ["initiated", "ringing", "in_progress"].includes(c.status)) ? 5000 : 30_000,
    },
  );
  const rootRef = useEntrance<HTMLDivElement>(!!data);
  const hasFilters = !!(urlSearch || status || interest || channel);

  // A stale ?page= past the end jumps to the last real page instead of showing "No calls yet".
  const lastPage = data ? Math.max(1, Math.ceil(data.total / (data.page_size || CALLS_PAGE_SIZE))) : 1;
  const pastEnd = !!data && !isValidating && data.items.length === 0 && data.total > 0 && page > lastPage;
  useEffect(() => {
    if (pastEnd) setParams({ page: lastPage > 1 ? lastPage : null }, false);
  }, [pastEnd, lastPage, setParams]);

  const clear = () => {
    setQ("");
    setParams({ search: null, status: null, interest: null, channel: null });
  };

  return (
    <div ref={rootRef}>
      <PageHeader
        title="Calls"
        description={data ? `${formatNumber(data.total)} ${hasFilters ? "matching calls" : "calls"}, newest first. Click a call for the full summary.` : "Every call the agent made, newest first."}
        actions={
          <>
            <Button size="sm" icon={<RefreshCw className={isValidating ? "spin" : undefined} />} onClick={() => void mutate()}>
              Refresh
            </Button>
            <Button variant="primary" icon={<Headphones />} onClick={() => testAgent.open()}>
              Test agent
            </Button>
          </>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-3" data-anim>
        <SearchInput value={q} onChange={setQ} placeholder="Search lead name, phone, company" label="Search calls" className="w-full sm:w-72" />
        <Select<CallStatus | "">
          aria-label="Filter by call status"
          pill
          className="w-44"
          value={status}
          onChange={(v) => setParams({ status: v || null })}
          options={[{ value: "", label: "All statuses" }, ...CALL_STATUSES.map((s) => ({ value: s, label: CALL_STATUS_LABEL[s] }))]}
        />
        <Select<Channel | "">
          aria-label="Filter by channel"
          pill
          className="w-40"
          value={channel}
          onChange={(v) => setParams({ channel: v || null })}
          options={[
            { value: "", label: "All channels" },
            { value: "phone", label: "Phone" },
            { value: "browser", label: "Browser tests" },
          ]}
        />
        <Segmented<Interest | "">
          label="Filter by interest"
          size="sm"
          value={interest}
          onChange={(v) => setParams({ interest: v || null })}
          options={[
            { value: "", label: "All" },
            ...(["cold", "warm", "hot", "unknown"] as Interest[]).map((i) => {
              const Icon = INTEREST_ICON[i];
              return { value: i, label: INTEREST_LABEL[i], icon: <Icon aria-hidden />, activeClass: INTEREST_ACTIVE[i] };
            }),
          ]}
        />
        {hasFilters ? (
          <Button size="sm" variant="ghost" icon={<FunnelX />} onClick={clear}>
            Clear filters
          </Button>
        ) : null}
      </div>

      <section className="overflow-hidden rounded-xl border border-line bg-white" data-anim>
        {error && !data ? (
          <div className="p-4">
            <ErrorState error={error} onRetry={() => void mutate()} title="Couldn't load calls" />
          </div>
        ) : !data || (data.items.length === 0 && data.total > 0) ? (
          <SkeletonRows rows={8} cols={7} />
        ) : data.items.length === 0 ? (
          hasFilters ? (
            <EmptyState icon={<FunnelX />} title="No calls match" body="Try different filters." action={<Button icon={<FunnelX />} onClick={clear}>Clear filters</Button>} />
          ) : (
            <EmptyState
              icon={<PhoneCall />}
              tone="subtle"
              title="No calls yet"
              body="Call a lead from the Leads page, start a campaign, or test the agent from your browser."
              action={
                <>
                  <Button variant="primary" icon={<Headphones />} onClick={() => testAgent.open()}>
                    Test agent
                  </Button>
                  <ButtonLink href="/leads">Go to leads</ButtonLink>
                </>
              }
            />
          )
        ) : (
          <>
            {error ? (
              <div className="border-b border-line p-3">
                <ErrorState compact error={error} onRetry={() => void mutate()} title="Showing the last loaded page" />
              </div>
            ) : null}
            <CallsTable calls={data.items} dim={isValidating && !isLoading && data.page !== page} />
            <Pagination
              page={data.page || page}
              pageSize={data.page_size || CALLS_PAGE_SIZE}
              total={data.total}
              onPage={(p) => setParams({ page: p > 1 ? p : null }, false)}
              busy={isValidating && !isLoading}
            />
          </>
        )}
      </section>
    </div>
  );
}
