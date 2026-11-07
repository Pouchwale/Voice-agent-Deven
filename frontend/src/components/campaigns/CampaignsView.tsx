"use client";

import { Megaphone, Plus, Users } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { useCampaignUpdates } from "@/components/providers/LiveProvider";
import { Button, ButtonLink } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { useEntrance } from "@/hooks/useEntrance";
import { keys } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import type { Campaign } from "@/lib/types";
import { CampaignCard } from "./CampaignCard";
import { CreateCampaignModal } from "./CreateCampaignModal";

function upsert(list: Campaign[] | undefined, c: Campaign): Campaign[] {
  const current = list ?? [];
  const idx = current.findIndex((x) => x.id === c.id);
  if (idx === -1) return [c, ...current];
  const next = current.slice();
  next[idx] = c;
  return next;
}

function CampaignSkeleton() {
  return (
    <div className="rounded-xl border border-line bg-white p-5" aria-hidden>
      <div className="flex items-center gap-3">
        <Skeleton className="h-7 w-56" />
        <Skeleton className="h-6 w-20 rounded-full" />
      </div>
      <Skeleton className="mt-3 h-4 w-72" />
      <Skeleton className="mt-5 h-5 w-full rounded-full" />
      <Skeleton className="mt-3 h-4 w-80" />
    </div>
  );
}

export function CampaignsView() {
  const [creating, setCreating] = useState(false);
  const { data, error, isLoading, mutate } = useSWR<Campaign[]>(keys.campaigns, {
    refreshInterval: (latest) => (latest?.some((c) => c.status === "running") ? 5000 : 30_000),
  });

  // Campaigns deleted here: a late `campaign.update` from the feed must not bring the card back.
  const deleted = useRef(new Set<number>());

  // Live progress from /ws/live `campaign.update` events.
  const onCampaignUpdate = useCallback(
    (c: Campaign) => {
      if (typeof c.id !== "number" || deleted.current.has(c.id)) return;
      void mutate((list) => upsert(list, c), { revalidate: false });
    },
    [mutate],
  );
  useCampaignUpdates(onCampaignUpdate);

  const onChanged = useCallback((c: Campaign) => void mutate((list) => upsert(list, c), { revalidate: false }), [mutate]);
  const onDeleted = useCallback(
    (id: number) => {
      deleted.current.add(id);
      void mutate((list) => (list ?? []).filter((x) => x.id !== id), { revalidate: true });
    },
    [mutate],
  );

  const campaigns = useMemo(
    () => (data ? [...data].sort((a, b) => b.created_at.localeCompare(a.created_at)) : []),
    [data],
  );
  const running = campaigns.filter((c) => c.status === "running").length;
  const activeCalls = campaigns.reduce((n, c) => n + (c.active_calls || 0), 0);

  const headerRef = useEntrance<HTMLDivElement>(true);
  const listRef = useEntrance<HTMLDivElement>(!!data);

  return (
    <div ref={headerRef}>
      <PageHeader
        title="Campaigns"
        description={
          data && campaigns.length > 0
            ? `${formatNumber(campaigns.length)} campaigns · ${running} running · ${activeCalls} calls on air right now.`
            : "Bulk-dial a list of leads. The agent calls, qualifies and scores every lead so your sales team only follows up on the hot ones."
        }
        actions={
          <Button variant="primary" icon={<Plus />} onClick={() => setCreating(true)}>
            New campaign
          </Button>
        }
      />

      {error && !data ? (
        <ErrorState error={error} onRetry={() => void mutate()} title="Couldn't load campaigns" />
      ) : isLoading && !data ? (
        <div className="space-y-5" role="status" aria-label="Loading campaigns">
          <CampaignSkeleton />
          <CampaignSkeleton />
          <CampaignSkeleton />
        </div>
      ) : campaigns.length === 0 ? (
        <div className="rounded-xl border border-line bg-white" data-anim>
          <EmptyState
            icon={<Megaphone />}
            tone="subtle"
            title="No campaigns yet"
            body="A campaign dials a list of leads automatically. The agent calls, qualifies and scores each one, and hot leads go to your sales team."
            action={
              <>
                <Button variant="primary" icon={<Plus />} onClick={() => setCreating(true)}>
                  New campaign
                </Button>
                <ButtonLink href="/leads" variant="secondary" icon={<Users />}>
                  Pick leads first
                </ButtonLink>
              </>
            }
          />
        </div>
      ) : (
        <div ref={listRef} className="space-y-5">
          {error ? <ErrorState error={error} onRetry={() => void mutate()} title="Showing the last loaded data" compact /> : null}
          {campaigns.map((c) => (
            <CampaignCard key={c.id} campaign={c} onChanged={onChanged} onDeleted={onDeleted} />
          ))}
        </div>
      )}

      <CreateCampaignModal open={creating} onClose={() => setCreating(false)} />
    </div>
  );
}
