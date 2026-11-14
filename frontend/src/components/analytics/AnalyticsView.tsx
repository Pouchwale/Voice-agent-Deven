"use client";

import {
  ChartColumn,
  Clock,
  Download,
  Flame,
  Hourglass,
  MessageSquare,
  PhoneCall,
  PhoneIncoming,
  PhoneOutgoing,
  Snowflake,
  Star,
  Sun,
  Zap,
} from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { SplitBar, FunnelBars, StackedHBars } from "@/components/charts/Bars";
import { HBars, InterestBreakdown } from "@/components/charts/Breakdowns";
import { ChartCard, ChartTable } from "@/components/charts/ChartCard";
import { ColumnChart } from "@/components/charts/ColumnChart";
import { LineChart } from "@/components/charts/LineChart";
import { MultiColumnChart } from "@/components/charts/MultiColumnChart";
import { KpiTile } from "@/components/dashboard/KpiTile";
import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { Segmented, Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { keys } from "@/lib/api";
import { endReasonLabel, formatDayLabel, formatDuration, formatNumber, formatPercent, INTEREST_LABEL, ymdIST } from "@/lib/format";
import { THEME } from "@/lib/theme";
import type { Analytics, Campaign, Interest } from "@/lib/types";
import { ExportCard } from "./ExportCard";
import { PRESETS, useAnalyticsFilters, type RangePreset } from "./range";

const TARGET_LATENCY_S = 1.5;
const seconds = (ms: number | null | undefined) => (ms === null || ms === undefined ? null : ms / 1000);
const fmtSec = (n: number) => `${n.toFixed(1)} s`;
const pct = (num: number, den: number) => (den > 0 ? formatPercent(num / den) : "—");

/** Shown inside outbound-only charts when the range has inbound calls but nothing was dialed. */
function NoOutbound() {
  return <p className="py-6 text-center text-sm text-muted">No outbound calls in this range: this chart covers calls we dialed.</p>;
}

function AnalyticsSkeleton() {
  return (
    <div className="space-y-5" role="status" aria-label="Loading analytics">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
        {Array.from({ length: 10 }).map((_, i) => (
          <Skeleton key={i} className="h-[104px] rounded-xl" />
        ))}
      </div>
      <div className="grid gap-5 lg:grid-cols-12">
        <Skeleton className="h-[300px] rounded-xl lg:col-span-5" />
        <Skeleton className="h-[300px] rounded-xl lg:col-span-7" />
      </div>
    </div>
  );
}

function Sections({ data }: { data: Analytics }) {
  const [overTime, setOverTime] = useState<"volume" | "interest">("volume");
  const t = data.totals;
  const days = data.by_day.map((d) => formatDayLabel(d.date));
  const hours = data.by_hour.map((h) => `${String(h.hour).padStart(2, "0")}:00`);
  const histogram = [...data.rating_histogram].sort((a, b) => a.rating - b.rating);
  const endReasons = [...data.end_reasons].sort((a, b) => b.count - a.count).map((r) => ({ label: endReasonLabel(r.reason), value: r.count }));
  const objections = [...data.objections].sort((a, b) => b.count - a.count).map((o) => ({ label: o.label, value: o.count }));
  const latency = data.latency_by_day.map((d) => seconds(d.avg_ms));
  const avgS = seconds(t.avg_latency_ms);
  const p90S = seconds(t.p90_latency_ms);
  // Every ratio below divides counts of the SAME scope (outbound by outbound, all phone calls by all phone
  // calls), so none can pass 100%. Cold also counts pick-up-and-cut calls, hence "of scored calls".
  const scored = t.hot + t.warm + t.cold;
  const rated = data.rating_histogram.reduce((s, r) => s + r.count, 0);
  const hasOutbound = t.dialed > 0;

  return (
    <>
      {/* 1. KPIs: dialed / connected = outbound, inbound on its own tile, the rest = every phone call */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
        <KpiTile label="Dialed" value={t.dialed} icon={<PhoneOutgoing />} caption="Outbound calls we placed" />
        <KpiTile label="Connected" value={t.connected} icon={<PhoneCall />} caption={`${pct(t.connected, t.dialed)} connect rate`} />
        <KpiTile
          label="Inbound calls"
          value={t.inbound}
          icon={<PhoneIncoming />}
          caption={`${formatNumber(t.inbound_missed)} missed`}
          href="/inbound"
        />
        <KpiTile label="Conversations" value={t.conversations} icon={<MessageSquare />} caption="Customer spoke, in or out" />
        <KpiTile
          label="Talk time"
          value={t.talk_minutes}
          format={(n) => formatNumber(Math.round(n))}
          suffix="min"
          icon={<Hourglass />}
          caption={t.avg_duration_sec !== null && t.avg_duration_sec !== undefined ? `Avg call ${formatDuration(t.avg_duration_sec)}` : "No answered calls"}
        />
        <KpiTile label="Hot" value={t.hot} icon={<Flame />} highlight="brand" caption={`${formatNumber(t.qualified)} qualified`} />
        <KpiTile label="Warm" value={t.warm} icon={<Sun />} caption={`${pct(t.warm, scored)} of scored calls`} />
        <KpiTile label="Cold" value={t.cold} icon={<Snowflake />} caption={`${pct(t.cold, scored)} of scored calls`} />
        <KpiTile
          label="Avg rating"
          value={t.avg_rating}
          format={(n) => n.toFixed(1)}
          suffix="/10"
          icon={<Star />}
          caption={rated ? `Over ${formatNumber(rated)} rated ${rated === 1 ? "call" : "calls"}` : "No rated calls"}
        />
        <KpiTile
          label="Avg response time"
          value={avgS}
          format={(n) => n.toFixed(1)}
          suffix="s"
          icon={<Zap />}
          caption={p90S !== null ? `p90 ${fmtSec(p90S)} · target ${fmtSec(TARGET_LATENCY_S)}` : `Target ${fmtSec(TARGET_LATENCY_S)}`}
        />
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-12">
        {/* 2. Funnel */}
        <ChartCard
          className="lg:col-span-5"
          title="Funnel"
          subtitle="Outbound: share of dialed calls reaching each step"
          table={<ChartTable head={["Step", "Calls", "% of dialed"]} rows={data.funnel.map((f) => [f.label, f.count, pct(f.count, data.funnel[0]?.count ?? 0)])} />}
        >
          {hasOutbound ? <FunnelBars steps={data.funnel.map((f) => ({ label: f.label, count: f.count }))} /> : <NoOutbound />}
        </ChartCard>

        {/* 3. Calls over time */}
        <ChartCard
          className="lg:col-span-7"
          title="Calls over time"
          subtitle="Per day, IST. Dialed and connected are outbound; inbound counts calls to our number."
          actions={
            <Segmented
              label="Chart view"
              size="sm"
              value={overTime}
              onChange={setOverTime}
              options={[
                { value: "volume", label: "Volume" },
                { value: "interest", label: "Interest" },
              ]}
            />
          }
          legend={
            overTime === "volume"
              ? [
                  { label: "Dialed", color: THEME.faint, kind: "line" },
                  { label: "Connected", color: THEME.brand, kind: "line" },
                  { label: "Inbound", color: THEME.navy, kind: "line" },
                ]
              : [
                  { label: "Cold", color: THEME.faint, kind: "rect" },
                  { label: "Warm", color: THEME.primaryTintStrong, kind: "rect", outlined: true },
                  { label: "Hot", color: THEME.brand, kind: "rect" },
                ]
          }
          table={
            <ChartTable
              head={["Day", "Dialed", "Connected", "Inbound", "Hot", "Warm", "Cold", "Avg rating"]}
              rows={data.by_day.map((d) => [
                formatDayLabel(d.date),
                d.dialed,
                d.connected,
                d.inbound ?? 0,
                d.hot,
                d.warm,
                d.cold,
                d.avg_rating === null ? "—" : d.avg_rating.toFixed(1),
              ])}
            />
          }
        >
          {overTime === "volume" ? (
            <LineChart
              ariaLabel="Dialed, connected and inbound calls per day"
              labels={days}
              series={[
                { key: "dialed", label: "Dialed", color: THEME.faint, values: data.by_day.map((d) => d.dialed) },
                { key: "connected", label: "Connected", color: THEME.brand, values: data.by_day.map((d) => d.connected) },
                { key: "inbound", label: "Inbound", color: THEME.navy, values: data.by_day.map((d) => d.inbound ?? 0) },
              ]}
            />
          ) : (
            <MultiColumnChart
              mode="stack"
              ariaLabel="Hot, warm and cold calls per day"
              labels={days}
              series={[
                { key: "cold", label: "Cold", color: THEME.faint, values: data.by_day.map((d) => d.cold) },
                { key: "warm", label: "Warm", color: THEME.primaryTintStrong, values: data.by_day.map((d) => d.warm), outlined: true },
                { key: "hot", label: "Hot", color: THEME.brand, values: data.by_day.map((d) => d.hot) },
              ]}
            />
          )}
        </ChartCard>

        {/* 4. Best time to call */}
        <ChartCard
          className="lg:col-span-7"
          title="Best time to call"
          subtitle="Outbound calls dialed per hour (IST), with the share that connected"
          legend={
            hasOutbound
              ? [
                  { label: "Dialed", color: THEME.primaryTintStrong, kind: "rect", outlined: true },
                  { label: "Connected", color: THEME.brand, kind: "rect" },
                ]
              : undefined
          }
          table={<ChartTable head={["Hour (IST)", "Dialed", "Connected", "Connect rate"]} rows={data.by_hour.map((h, i) => [hours[i], h.dialed, h.connected, pct(h.connected, h.dialed)])} />}
        >
          {hasOutbound ? (
            <MultiColumnChart
              mode="overlay"
              ariaLabel="Dialed and connected calls by hour of day"
              labels={hours}
              series={[
                { key: "dialed", label: "Dialed", color: THEME.primaryTintStrong, values: data.by_hour.map((h) => h.dialed), outlined: true },
                { key: "connected", label: "Connected", color: THEME.brand, values: data.by_hour.map((h) => h.connected) },
              ]}
              extraTooltip={(i) => ({ label: "connect rate", value: pct(data.by_hour[i].connected, data.by_hour[i].dialed) })}
            />
          ) : (
            <NoOutbound />
          )}
        </ChartCard>

        {/* 5. Interest split */}
        <ChartCard
          className="lg:col-span-5"
          title="Interest split"
          subtitle="Phone calls in this range by interest, inbound and outbound"
          table={
            <ChartTable
              head={["Interest", "Calls"]}
              rows={(["cold", "warm", "hot", "unknown"] as Interest[]).map((k) => [INTEREST_LABEL[k], data.interest[k] ?? 0])}
            />
          }
        >
          <InterestBreakdown counts={data.interest} linkRows={false} />
        </ChartCard>

        <ChartCard
          className="lg:col-span-6"
          title="Rating distribution"
          subtitle="1 = not interested, 10 = ready to buy"
          table={<ChartTable head={["Rating", "Calls"]} rows={histogram.map((r) => [`${r.rating} / 10`, r.count])} />}
        >
          <ColumnChart ariaLabel="Calls per rating" data={histogram.map((r) => ({ label: String(r.rating), value: r.count }))} categoryPrefix="Rating " valueLabel="calls" />
        </ChartCard>

        <ChartCard
          className="lg:col-span-6"
          title="Call duration"
          subtitle="Answered calls by length, inbound and outbound"
          table={<ChartTable head={["Duration", "Calls"]} rows={data.duration_buckets.map((b) => [b.label, b.count])} />}
        >
          <ColumnChart ariaLabel="Calls per duration bucket" data={data.duration_buckets.map((b) => ({ label: b.label, value: b.count }))} valueLabel="calls" />
        </ChartCard>

        {/* 6. End reasons + sentiment */}
        <ChartCard
          className="lg:col-span-6"
          title="Why calls ended"
          table={<ChartTable head={["End reason", "Calls"]} rows={endReasons.map((r) => [r.label, r.value])} />}
        >
          <HBars data={endReasons} color={THEME.faint} emptyText="No finished calls in this range." />
        </ChartCard>

        <ChartCard
          className="lg:col-span-6"
          title="Customer sentiment"
          table={
            <ChartTable
              head={["Sentiment", "Calls"]}
              rows={[
                ["Positive", data.sentiment.positive],
                ["Neutral", data.sentiment.neutral],
                ["Negative", data.sentiment.negative],
              ]}
            />
          }
        >
          <SplitBar
            parts={[
              { key: "positive", label: "Positive", color: THEME.brand, value: data.sentiment.positive },
              { key: "neutral", label: "Neutral", color: THEME.faint, value: data.sentiment.neutral },
              { key: "negative", label: "Negative", color: THEME.navy, value: data.sentiment.negative },
            ]}
          />
        </ChartCard>

        {/* 7. Objections */}
        <ChartCard
          className="lg:col-span-6"
          title="Top objections"
          subtitle="What customers pushed back on"
          table={<ChartTable head={["Objection", "Calls"]} rows={objections.map((o) => [o.label, o.value])} />}
        >
          <HBars data={objections} color={THEME.brand} emptyText="No objections recorded in this range." />
        </ChartCard>

        {/* 8. Qualification coverage */}
        <ChartCard
          className="lg:col-span-6"
          title="Qualification coverage"
          subtitle="How often each question got an answer"
          table={
            <ChartTable
              head={["Question", "Answered", "Partial", "Not asked"]}
              rows={data.qualification.map((q) => [q.question, q.answered, q.partial, q.not_asked])}
            />
          }
        >
          {data.qualification.length ? (
            <StackedHBars
              parts={[
                { key: "answered", label: "Answered", color: THEME.brand },
                { key: "partial", label: "Partial", color: THEME.primaryTintStrong, outlined: true },
                { key: "not_asked", label: "Not asked", color: THEME.subtle, outlined: true },
              ]}
              rows={data.qualification.map((q) => ({ label: q.question, values: { answered: q.answered, partial: q.partial, not_asked: q.not_asked } }))}
            />
          ) : (
            <p className="py-6 text-center text-sm text-muted">No qualification data in this range.</p>
          )}
        </ChartCard>

        {/* 9. Campaign comparison */}
        <section className="min-w-0 overflow-hidden rounded-xl border border-line bg-white lg:col-span-12">
          <div className="px-4 pt-4 sm:px-5 sm:pt-5">
            <h2 className="font-heading text-base font-semibold">Campaign comparison</h2>
            <p className="mt-0.5 text-[13px] text-muted">Outbound calls per campaign in this range</p>
          </div>
          {data.campaigns.length ? (
            <div className="relative mt-3 overflow-x-auto">
              <table className="data-table min-w-[640px]">
                <thead>
                  <tr>
                    <th>Campaign</th>
                    <th className="!text-right">Dialed</th>
                    <th className="!text-right">Connected</th>
                    <th className="!text-right">Hot</th>
                    <th className="!text-right">Warm</th>
                    <th className="!text-right">Cold</th>
                    <th className="!text-right">Avg rating</th>
                  </tr>
                </thead>
                <tbody>
                  {data.campaigns.map((c) => (
                    <tr key={c.id}>
                      <td className="font-medium">{c.name}</td>
                      <td className="tabular text-right">{formatNumber(c.dialed)}</td>
                      <td className="tabular text-right">{pct(c.connected, c.dialed)}</td>
                      <td className="tabular text-right font-medium text-primary">{formatNumber(c.hot)}</td>
                      <td className="tabular text-right">{formatNumber(c.warm)}</td>
                      <td className="tabular text-right">{formatNumber(c.cold)}</td>
                      <td className="tabular text-right">{c.avg_rating === null ? "—" : `${c.avg_rating.toFixed(1)}/10`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="px-5 py-8 text-center text-sm text-muted">No campaign calls in this range.</p>
          )}
        </section>

        {/* 10. Response time trend */}
        <ChartCard
          className="lg:col-span-12"
          title="Response time"
          subtitle="Average time from the customer finishing to the agent starting to speak, per day"
          legend={[{ label: "Avg response time", color: THEME.brand, kind: "line" }]}
          table={<ChartTable head={["Day", "Avg response"]} rows={data.latency_by_day.map((d) => [formatDayLabel(d.date), d.avg_ms === null ? "—" : fmtSec(d.avg_ms / 1000)])} />}
        >
          <LineChart
            ariaLabel="Average response time per day"
            labels={data.latency_by_day.map((d) => formatDayLabel(d.date))}
            series={[{ key: "latency", label: "Avg response", color: THEME.brand, values: latency }]}
            formatValue={fmtSec}
            target={{ value: TARGET_LATENCY_S, label: `Target ${fmtSec(TARGET_LATENCY_S)}` }}
          />
        </ChartCard>
      </div>
    </>
  );
}

export function AnalyticsView() {
  const { filters, apiParams, update, setPreset } = useAnalyticsFilters();
  const { data, error, isLoading, isValidating, mutate } = useSWR<Analytics>(keys.analytics(apiParams), { keepPreviousData: true });
  const { data: campaigns } = useSWR<Campaign[]>(keys.campaigns);
  const today = ymdIST();
  const campaignName = campaigns?.find((c) => c.id === filters.campaignId)?.name;

  function goToExport() {
    const el = document.getElementById("export");
    el?.scrollIntoView({ behavior: "smooth", block: "start" });
    el?.focus({ preventScroll: true });
  }

  return (
    <div className="min-w-0">
      <PageHeader
        title="Analytics"
        description="How the agent's calls are performing, inbound and outbound: connect rate, interest, objections and response time."
        actions={
          <Button icon={<Download />} onClick={goToExport}>
            Export
          </Button>
        }
      />

      {/* Filters: one row above everything they scope */}
      <div className="mb-5 flex flex-wrap items-end gap-3">
        <Segmented<RangePreset> label="Date range" value={filters.preset} onChange={setPreset} options={PRESETS} />
        {filters.preset === "custom" ? (
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-[13px] text-muted">
              <span className="mb-1 block">From</span>
              <input
                type="date"
                className="field h-9 !py-0"
                value={filters.from}
                max={filters.to}
                onChange={(e) => e.target.value && update({ range: "custom", from: e.target.value, to: filters.to })}
              />
            </label>
            <label className="text-[13px] text-muted">
              <span className="mb-1 block">To</span>
              <input
                type="date"
                className="field h-9 !py-0"
                value={filters.to}
                min={filters.from}
                max={today}
                onChange={(e) => e.target.value && update({ range: "custom", from: filters.from, to: e.target.value })}
              />
            </label>
          </div>
        ) : null}
        <Select<string>
          aria-label="Campaign"
          className="w-56"
          value={filters.campaignId === "" ? "" : String(filters.campaignId)}
          onChange={(v) => update({ campaign: v || null })}
          options={[{ value: "", label: "All campaigns" }, ...(campaigns ?? []).map((c) => ({ value: String(c.id), label: c.name }))]}
        />
        <label className="inline-flex h-9 cursor-pointer items-center gap-2 text-[13px] text-subtle-fg">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={filters.includeTests}
            onChange={(e) => update({ tests: e.target.checked ? "1" : null })}
          />
          Include test calls
        </label>
        {isValidating && data ? <span className="h-9 content-center text-[13px] text-muted">Updating…</span> : null}
      </div>

      {error && !data ? (
        <ErrorState error={error} onRetry={() => void mutate()} title="Couldn't load analytics" />
      ) : !data ? (
        isLoading ? <AnalyticsSkeleton /> : null
      ) : (
        <div className={isValidating ? "opacity-70 transition-opacity" : "transition-opacity"}>
          {error ? <ErrorState className="mb-5" compact error={error} onRetry={() => void mutate()} title="Showing the last loaded numbers" /> : null}
          {/* Empty only when nothing happened at all: an inbound-only range (outbound switched off) still has data. */}
          {data.totals.dialed + (data.totals.inbound ?? 0) === 0 ? (
            <div className="rounded-xl border border-line bg-white">
              <EmptyState
                icon={<ChartColumn />}
                title="No calls in this range"
                body="Try a longer date range, another campaign, or include test calls."
                action={
                  <Button icon={<Clock />} onClick={() => setPreset("30d")}>
                    Show last 30 days
                  </Button>
                }
              />
            </div>
          ) : (
            <Sections data={data} />
          )}
        </div>
      )}

      <div className="mt-5">
        <ExportCard params={apiParams} campaignName={campaignName} />
      </div>
    </div>
  );
}
