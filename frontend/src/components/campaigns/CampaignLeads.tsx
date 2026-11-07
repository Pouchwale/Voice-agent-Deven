"use client";

import Link from "next/link";
import { ExternalLink, Users } from "lucide-react";
import useSWR from "swr";
import { Chip } from "@/components/ui/Badge";
import { InterestBadge } from "@/components/ui/InterestBadge";
import { RatingPill } from "@/components/ui/RatingPill";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { keys } from "@/lib/api";
import { formatNumber, formatPhone, leadName, titleCase } from "@/lib/format";
import type { CampaignDetail } from "@/lib/types";

/** Lazily-loaded table of a campaign's leads (backend returns the first 500). */
export function CampaignLeads({ id, running, total }: { id: number; running: boolean; total: number }) {
  const { data, error, isLoading, mutate } = useSWR<CampaignDetail>(keys.campaign(id), {
    refreshInterval: running ? 10_000 : 0,
    keepPreviousData: true,
  });

  if (error && !data) {
    return <ErrorState error={error} onRetry={() => void mutate()} title="Couldn't load campaign leads" compact className="m-4" />;
  }
  if (isLoading && !data) return <SkeletonRows rows={5} cols={6} />;
  if (!data) return null;

  if (data.items.length === 0) {
    return (
      <EmptyState
        icon={<Users />}
        tone="primary"
        title="No leads in this campaign"
        body="Nothing matched when it was created. Create a new campaign from the Leads page."
        className="py-8"
      />
    );
  }

  return (
    <div>
      <div className="scroll-thin max-h-[420px] overflow-auto">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Lead</th>
              <th scope="col">Phone</th>
              <th scope="col">Status</th>
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
                  <Chip size="xs" color={item.status === "dialing" ? "primary" : "white"}>
                    {titleCase(item.status)}
                  </Chip>
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
                      className="inline-flex items-center gap-1 text-[12px] font-semibold underline-offset-4 hover:underline hover:decoration-primary hover:decoration-2"
                    >
                      Summary <ExternalLink className="size-3.5" aria-hidden />
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
      {total > data.items.length ? (
        <p className="border-t border-line px-4 py-2.5 text-[13px] font-normal text-muted">
          Showing the first {formatNumber(data.items.length)} of {formatNumber(total)} leads.
        </p>
      ) : null}
    </div>
  );
}
