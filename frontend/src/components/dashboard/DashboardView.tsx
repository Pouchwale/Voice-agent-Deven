"use client";

import { Clock, Flame, Headphones, PhoneCall, PhoneIncoming, PhoneOutgoing, Radio, RefreshCw, Star, Upload, Users } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { ChartCard, ChartTable } from "@/components/charts/ChartCard";
import { ColumnChart } from "@/components/charts/ColumnChart";
import { HBars, InterestBreakdown } from "@/components/charts/Breakdowns";
import { LineChart } from "@/components/charts/LineChart";
import { useLive } from "@/components/providers/LiveProvider";
import { useTestAgent } from "@/components/providers/TestAgentProvider";
import { Dot } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { CircleIcon } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { Segmented } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { useEntrance } from "@/hooks/useEntrance";
import { keys } from "@/lib/api";
import { endReasonLabel, formatDayLabel, formatNumber, formatPercent, INTEREST_LABEL } from "@/lib/format";
import { THEME } from "@/lib/theme";
import type { DashboardStats, Interest } from "@/lib/types";
import { KpiTile } from "./KpiTile";
import { RecentCallsList, TopLeadsList } from "./Lists";

const RANGES = [
  { value: "7", label: "7 days" },
  { value: "14", label: "14 days" },
  { value: "30", label: "30 days" },
] as const;

// 7 tiles: the first spans two columns below 2xl, so every breakpoint has full rows (2 / 4 / 7 columns).
const KPI_GRID = "grid grid-cols-2 gap-4 md:grid-cols-4 2xl:grid-cols-7";
const KPI_WIDE = "col-span-2 2xl:col-span-1";

function DashboardSkeleton() {
  return (
    <div className="space-y-5" aria-label="Loading dashboard" role="status">
      <div className={KPI_GRID}>
        {Array.from({ length: 7 }).map((_, i) => (
          <Skeleton key={i} className={i === 0 ? `h-[112px] rounded-xl ${KPI_WIDE}` : "h-[112px] rounded-xl"} />
        ))}
      </div>
      <div className="grid gap-5 lg:grid-cols-12">
        <Skeleton className="h-[300px] rounded-xl lg:col-span-8" />
        <Skeleton className="h-[300px] rounded-xl lg:col-span-4" />
      </div>
    </div>
  );
}

export function DashboardView() {
  const [days, setDays] = useState<"7" | "14" | "30">("14");
  const { data, error, isLoading, isValidating, mutate } = useSWR<DashboardStats>(keys.stats(Number(days)), {
    keepPreviousData: true,
    refreshInterval: 30_000,
  });
  const live = useLive();
  const testAgent = useTestAgent();
  const rootRef = useEntrance<HTMLDivElement>(!!data);

  const header = (
    <PageHeader
      title="Dashboard"
      description="Who the agent called, who picked up, and which leads are ready for your sales team."
      actions={
        <>
          <Segmented label="Date range" value={days} onChange={setDays} options={RANGES.map((r) => ({ ...r }))} />
          <Button size="sm" icon={<RefreshCw className={isValidating ? "spin" : undefined} />} onClick={() => void mutate()}>
            Refresh
          </Button>
        </>
      }
    />
  );

  if (!data) {
    return (
      <div>
        {header}
        {error ? <ErrorState error={error} onRetry={() => void mutate()} title="Couldn't load the dashboard" /> : null}
        {isLoading || !error ? <DashboardSkeleton /> : null}
      </div>
    );
  }

  const t = data.totals;
  const hot = data.interest.hot ?? 0;
  const liveCount = live.synced ? live.active.length : t.active_calls;
  const busy = isValidating && !isLoading;
  const dayLabels = data.calls_by_day.map((d) => formatDayLabel(d.date));
  const endReasons = [...data.end_reasons]
    .sort((a, b) => b.count - a.count)
    .map((r) => ({ label: endReasonLabel(r.reason), value: r.count }));
  const histogram = [...data.rating_histogram].sort((a, b) => a.rating - b.rating);
  const ratedCalls = histogram.reduce((s, r) => s + r.count, 0);

  return (
    <div ref={rootRef}>
      {header}

      {error ? (
        <ErrorState className="mb-5" compact error={error} onRetry={() => void mutate()} title="Showing the last loaded numbers" />
      ) : null}

      {t.leads === 0 ? (
        <div className="mb-5 flex flex-wrap items-center gap-4 rounded-xl border border-primary-tint-strong bg-primary-tint p-4">
          <CircleIcon tone="primary">
            <Upload />
          </CircleIcon>
          <div className="min-w-0 flex-1">
            <p className="font-heading text-base font-semibold">Import your first leads</p>
            <p className="mt-0.5 text-sm text-muted">Upload a CSV of names and phone numbers and the agent will start qualifying them.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <ButtonLink href="/leads?import=1" variant="primary" icon={<Upload />}>
              Import CSV
            </ButtonLink>
            <Button icon={<Headphones />} onClick={() => testAgent.open()}>
              Try the agent
            </Button>
          </div>
        </div>
      ) : null}

      {/* KPIs */}
      <div className={KPI_GRID}>
        <KpiTile
          className={KPI_WIDE}
          label="Live calls"
          value={liveCount}
          icon={<Radio />}
          href="/live"
          highlight={liveCount > 0 ? "primary" : undefined}
          indicator={liveCount > 0 ? <Dot className="bg-primary" live /> : undefined}
          caption={liveCount > 0 ? "Open live monitor" : "None right now"}
        />
        <KpiTile label="Leads" value={t.leads} icon={<Users />} href="/leads" caption={`${formatNumber(t.qualified)} qualified`} />
        <KpiTile
          label="Calls today"
          value={t.calls_today}
          icon={<PhoneOutgoing />}
          href="/calls"
          caption={`${formatNumber(t.calls)} dialed in the last ${days} days`}
        />
        <KpiTile
          label="Inbound today"
          value={t.inbound_today ?? null}
          icon={<PhoneIncoming />}
          href="/inbound"
          caption={
            typeof t.inbound_missed_today === "number"
              ? `${formatNumber(t.inbound_missed_today)} missed`
              : "Customers who called us"
          }
        />
        <KpiTile
          label="Connect rate"
          value={Math.round(t.connect_rate * 100)}
          suffix="%"
          icon={<PhoneCall />}
          caption={`${formatNumber(t.connected)} of ${formatNumber(t.calls)} picked up`}
        />
        <KpiTile
          label="Avg rating"
          value={t.avg_rating}
          format={(n) => n.toFixed(1)}
          suffix="/10"
          icon={<Star />}
          caption={t.avg_rating === null ? "No rated calls yet" : `${formatNumber(ratedCalls)} rated calls`}
        />
        <KpiTile
          label="Hot leads"
          value={hot}
          icon={<Flame />}
          href="/leads?interest=hot"
          highlight="brand"
          caption={`${formatNumber(data.interest.warm)} warm · ${formatNumber(data.interest.cold)} cold`}
        />
      </div>

      {/* Charts */}
      <div className="mt-5 grid gap-5 lg:grid-cols-12">
        <ChartCard
          className="lg:col-span-8"
          title="Calls per day"
          subtitle={`Calls the agent made, last ${days} days, IST`}
          busy={busy}
          legend={[
            { label: "Dialed", color: THEME.faint, kind: "line" },
            { label: "Connected", color: THEME.brand, kind: "line" },
            { label: "Hot", color: THEME.navy, kind: "line" },
          ]}
          table={
            <ChartTable
              head={["Day", "Dialed", "Connected", "Hot"]}
              rows={data.calls_by_day.map((d) => [formatDayLabel(d.date), d.total, d.connected, d.hot])}
            />
          }
        >
          {data.calls_by_day.length ? (
            <LineChart
              ariaLabel="Calls per day: dialed, connected and hot"
              labels={dayLabels}
              series={[
                { key: "total", label: "Dialed", color: THEME.faint, values: data.calls_by_day.map((d) => d.total) },
                { key: "connected", label: "Connected", color: THEME.brand, values: data.calls_by_day.map((d) => d.connected) },
                { key: "hot", label: "Hot", color: THEME.navy, values: data.calls_by_day.map((d) => d.hot) },
              ]}
            />
          ) : (
            <p className="py-10 text-center text-sm text-muted">No calls in this range.</p>
          )}
        </ChartCard>

        <ChartCard
          className="lg:col-span-4"
          title="Lead interest"
          subtitle={`${formatNumber(t.leads)} leads · click a tier to filter`}
          busy={busy}
          table={
            <ChartTable
              head={["Interest", "Leads", "Share"]}
              rows={(["cold", "warm", "hot", "unknown"] as Interest[]).map((k) => [
                INTEREST_LABEL[k],
                data.interest[k] ?? 0,
                t.leads ? formatPercent((data.interest[k] ?? 0) / t.leads) : "—",
              ])}
            />
          }
        >
          <InterestBreakdown counts={data.interest} />
        </ChartCard>

        <ChartCard
          className="lg:col-span-6"
          title="Rating distribution"
          subtitle="Connected calls by rating (1 = not interested, 10 = ready to buy)"
          busy={busy}
          table={<ChartTable head={["Rating", "Calls"]} rows={histogram.map((r) => [`${r.rating} / 10`, r.count])} />}
        >
          {ratedCalls ? (
            <ColumnChart
              ariaLabel="Number of calls for each rating from 1 to 10"
              data={histogram.map((r) => ({ label: String(r.rating), value: r.count }))}
              categoryPrefix="Rating "
              valueLabel="calls"
            />
          ) : (
            <p className="py-10 text-center text-sm text-muted">No rated calls yet.</p>
          )}
        </ChartCard>

        <ChartCard
          className="lg:col-span-6"
          title="Why calls ended"
          subtitle={`Last ${days} days`}
          busy={busy}
          table={<ChartTable head={["End reason", "Calls"]} rows={endReasons.map((r) => [r.label, r.value])} />}
        >
          <HBars data={endReasons} color={THEME.faint} emptyText="No finished calls in this range." />
        </ChartCard>
      </div>

      {/* Lists */}
      <div className="mt-5 grid gap-5 lg:grid-cols-12">
        <section className="min-w-0 rounded-xl border border-line bg-white p-4 sm:p-5 lg:col-span-7">
          <div className="mb-2 flex items-center justify-between gap-3">
            <h2 className="font-heading text-base font-semibold">Recent calls</h2>
            <ButtonLink href="/calls" size="sm" variant="ghost">
              View all
            </ButtonLink>
          </div>
          {data.recent_calls.length ? (
            <RecentCallsList calls={data.recent_calls} />
          ) : (
            <EmptyState
              icon={<Clock />}
              title="No calls yet"
              body="Start a campaign or test the agent from your browser."
              action={
                <Button variant="primary" icon={<Headphones />} onClick={() => testAgent.open()}>
                  Test agent
                </Button>
              }
            />
          )}
        </section>

        <section className="min-w-0 rounded-xl border border-line bg-white p-4 sm:p-5 lg:col-span-5">
          <div className="mb-2 flex items-center justify-between gap-3">
            <div>
              <h2 className="font-heading text-base font-semibold">Top leads for sales</h2>
              <p className="mt-0.5 text-[13px] text-muted">Hot leads, best rating first</p>
            </div>
            <ButtonLink href="/leads?interest=hot&sort=-rating" size="sm" variant="ghost">
              View all
            </ButtonLink>
          </div>
          {data.top_leads.length ? (
            <TopLeadsList leads={data.top_leads} />
          ) : (
            <EmptyState icon={<Flame />} tone="subtle" title="No hot leads yet" body="Interested buyers show up here for your sales team." />
          )}
        </section>
      </div>
    </div>
  );
}
