"use client";

import Link from "next/link";
import { ExternalLink, Users } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { Chip, Dot, type ChipColor } from "@/components/ui/Badge";
import { InterestBadge } from "@/components/ui/InterestBadge";
import { Pagination } from "@/components/ui/Pagination";
import { RatingPill } from "@/components/ui/RatingPill";
import { Segmented } from "@/components/ui/Select";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { keys } from "@/lib/api";
import { CAMPAIGN_RESULT_LABEL, formatDuration, formatNumber, formatPhone, formatTime, leadName } from "@/lib/format";
import type { CampaignDetail, CampaignItem, CampaignOutcomes, CampaignResult, CampaignResultFilter } from "@/lib/types";

const PAGE_SIZE = 50;

const RESULT_STYLE: Record<CampaignResult, { color: ChipColor; dot: string; live?: boolean }> = {
  waiting: { color: "white", dot: "bg-faint" },
  ringing: { color: "primary", dot: "bg-primary", live: true },
  talking: { color: "primary-solid", dot: "bg-white", live: true },
  picked_up: { color: "white", dot: "bg-brand" },
  no_answer: { color: "white", dot: "bg-faint" },
  busy: { color: "white", dot: "bg-faint" },
  failed: { color: "white", dot: "bg-fg" },
  skipped: { color: "neutral", dot: "bg-subtle-fg" },
};

function ResultCell({ item }: { item: CampaignItem }) {
  const s = RESULT_STYLE[item.result] ?? RESULT_STYLE.waiting;
  const call = item.last_call;
  let detail: string | null = null;
  if (item.result === "picked_up" && call?.duration_sec != null) detail = `Talked ${formatDuration(call.duration_sec)}`;
  else if (item.result === "waiting") detail = item.attempts > 0 ? "Dialing…" : "Not dialed yet";
  else if (item.result === "skipped") detail = "Do-not-call or lead removed";
  else if (["no_answer", "busy", "failed"].includes(item.result)) {
    if (item.status === "pending") detail = item.next_attempt_at ? `Retry at ${formatTime(item.next_attempt_at)}` : "Retry queued";
    else if (item.status === "failed") detail = `Gave up after ${item.attempts} ${item.attempts === 1 ? "attempt" : "attempts"}`;
  }
  return (
    <div className="min-w-[150px]">
      <Chip size="xs" color={s.color} icon={<Dot className={s.dot} live={s.live} />}>
        {CAMPAIGN_RESULT_LABEL[item.result] ?? item.result}
      </Chip>
      {detail ? <span className="mt-0.5 block text-[12px] font-normal text-muted">{detail}</span> : null}
    </div>
  );
}

/** Lazily-loaded, filterable table of a campaign's numbers with each one's latest call result. */
export function CampaignLeads({
  id,
  running,
  total,
  outcomes,
  filter,
  onFilter,
}: {
  id: number;
  running: boolean;
  total: number;
  outcomes?: CampaignOutcomes;
  filter: CampaignResultFilter | "";
  onFilter: (f: CampaignResultFilter | "") => void;
}) {
  // The page belongs to one filter: picking another filter starts again at page 1.
  const [paging, setPaging] = useState({ filter, page: 1 });
  const page = paging.filter === filter ? paging.page : 1;
  const setPage = (p: number) => setPaging({ filter, page: p });
  const { data, error, isLoading, mutate } = useSWR<CampaignDetail>(
    keys.campaignItems(id, { result: filter, page, page_size: PAGE_SIZE }),
    { refreshInterval: running ? 10_000 : 0, keepPreviousData: true },
  );

  const o = outcomes;
  const count = (n: number | undefined) => (o ? ` (${formatNumber(n ?? 0)})` : "");
  const options: { value: CampaignResultFilter | ""; label: string }[] = [
    { value: "", label: `All${o ? ` (${formatNumber(total)})` : ""}` },
    { value: "waiting", label: `Waiting${count(o?.waiting)}` },
    { value: "ongoing", label: `On call${count(o ? o.ringing + o.talking : undefined)}` },
    { value: "picked_up", label: `Picked up${count(o?.picked_up)}` },
    { value: "no_answer", label: `No answer${count(o?.no_answer)}` },
    { value: "busy", label: `Busy${count(o?.busy)}` },
    { value: "failed", label: `Failed${count(o?.failed)}` },
    ...(o?.skipped ? [{ value: "skipped" as const, label: `Skipped${count(o.skipped)}` }] : []),
  ];

  let body: React.ReactNode;
  if (error && !data) {
    body = <ErrorState error={error} onRetry={() => void mutate()} title="Couldn't load campaign numbers" compact className="m-4" />;
  } else if (isLoading && !data) {
    body = <SkeletonRows rows={5} cols={6} />;
  } else if (!data) {
    body = null;
  } else if (data.items.length === 0) {
    body = (
      <EmptyState
        icon={<Users />}
        tone="primary"
        title={filter ? `No numbers are ${CAMPAIGN_RESULT_LABEL[filter as CampaignResult]?.toLowerCase() ?? "on a call"} right now` : "No numbers in this campaign"}
        body={filter ? "The list updates live while the campaign runs." : "Nothing matched when it was created."}
        className="py-8"
      />
    );
  } else {
    body = (
      <>
        <div className="scroll-thin max-h-[460px] overflow-auto">
          <table className="data-table">
            <thead className="sticky top-0 z-[1] bg-white">
              <tr>
                <th scope="col">Lead</th>
                <th scope="col">Phone</th>
                <th scope="col">Result</th>
                <th scope="col" className="text-right">
                  Attempts
                </th>
                <th scope="col">Interest</th>
                <th scope="col">Rating</th>
                <th scope="col">Last call</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((item) => (
                <tr key={item.lead.id}>
                  <td className="max-w-[240px]">
                    <Link
                      href={`/leads/${item.lead.id}`}
                      className="font-semibold underline-offset-4 hover:underline hover:decoration-primary hover:decoration-2"
                    >
                      {leadName(item.lead)}
                    </Link>
                    {item.lead.company ? (
                      <span className="block truncate text-[12px] font-normal text-muted">{item.lead.company}</span>
                    ) : null}
                  </td>
                  <td className="tabular whitespace-nowrap font-normal">{formatPhone(item.lead.phone)}</td>
                  <td>
                    <ResultCell item={item} />
                  </td>
                  <td className="tabular text-right font-medium">{item.attempts}</td>
                  <td>
                    <InterestBadge interest={item.lead.interest} size="xs" />
                  </td>
                  <td>
                    <RatingPill rating={item.lead.rating} />
                  </td>
                  <td>
                    {item.last_call_uuid ? (
                      <Link
                        href={`/calls/${item.last_call_uuid}`}
                        className="inline-flex items-center gap-1 text-[12px] font-semibold whitespace-nowrap underline-offset-4 hover:underline hover:decoration-primary hover:decoration-2"
                      >
                        {item.result === "picked_up" ? "Summary" : "Details"} <ExternalLink className="size-3.5" aria-hidden />
                      </Link>
                    ) : (
                      <span className="text-muted">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data.items_total > PAGE_SIZE ? (
          <Pagination page={page} pageSize={PAGE_SIZE} total={data.items_total} onPage={setPage} busy={isLoading} />
        ) : null}
      </>
    );
  }

  return (
    <div>
      <div className="scroll-thin overflow-x-auto border-b border-line px-3 py-2 sm:px-4">
        <Segmented<CampaignResultFilter | "">
          label="Show numbers"
          size="sm"
          options={options}
          value={filter}
          onChange={onFilter}
          className="flex-nowrap"
        />
      </div>
      {body}
    </div>
  );
}
