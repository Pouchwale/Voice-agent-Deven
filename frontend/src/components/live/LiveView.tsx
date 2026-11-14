"use client";

import { Headphones, LoaderCircle, Megaphone, Radio, RefreshCw, Users, WifiOff } from "lucide-react";
import { useEffect, useState } from "react";
import { useLive, useLiveStore } from "@/components/providers/LiveProvider";
import { useTestAgent } from "@/components/providers/TestAgentProvider";
import { Chip } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/States";
import { useToast } from "@/components/ui/Toast";
import { useEntrance } from "@/hooks/useEntrance";
import { LiveCallCard } from "./LiveCallCard";
import { RecentlyEnded } from "./RecentlyEnded";
import { useListenIn } from "./useListenIn";

function FeedBanner({ status, synced, onReconnect }: { status: string; synced: boolean; onReconnect: () => void }) {
  if (status === "open") return null;
  const interrupted = synced;
  return (
    <div
      role="status"
      data-anim
      className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-line bg-subtle p-3 sm:px-4"
    >
      {interrupted ? (
        <WifiOff className="size-5 shrink-0" aria-hidden />
      ) : (
        <LoaderCircle className="spin size-5 shrink-0" aria-hidden />
      )}
      <p className="min-w-0 flex-1 text-sm font-medium">
        {interrupted ? (
          <>
            <span className="font-semibold">Live feed interrupted.</span> Reconnecting automatically - cards may be out of
            date.
          </>
        ) : (
          <>
            <span className="font-semibold">Connecting to the live feed…</span> Retrying with backoff if the backend is
            unreachable.
          </>
        )}
      </p>
      <Button size="sm" variant="secondary" icon={<RefreshCw />} onClick={onReconnect}>
        Reconnect now
      </Button>
    </div>
  );
}

function CardSkeleton() {
  return (
    <div className="flex flex-col gap-3.5 rounded-xl border border-line bg-white p-4" aria-hidden>
      <div className="flex items-center gap-3">
        <Skeleton className="size-11 rounded-full" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-3 w-1/2" />
        </div>
        <Skeleton className="h-6 w-14" />
      </div>
      <Skeleton className="h-6 w-3/4" />
      <Skeleton className="h-11 w-full" />
      <Skeleton className="h-56 w-full" />
    </div>
  );
}

export function LiveView() {
  const live = useLive();
  const store = useLiveStore();
  const testAgent = useTestAgent();
  const toast = useToast();
  const listen = useListenIn();
  const [waited, setWaited] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setWaited(true), 4000);
    return () => clearTimeout(t);
  }, []);

  const loading = !live.synced && !waited && live.active.length === 0;
  const ref = useEntrance<HTMLDivElement>(!loading);

  // Stop listening when that call is no longer active.
  const { uuid: listenUuid, stop: stopListen } = listen;
  useEffect(() => {
    if (listenUuid && !live.active.some((c) => c.call.uuid === listenUuid)) stopListen();
  }, [listenUuid, live.active, stopListen]);

  const listenError = listen.error;
  useEffect(() => {
    if (listenError) toast({ tone: "error", title: "Listen-in failed", description: listenError });
  }, [listenError, toast]);

  const count = live.active.length;

  return (
    <div ref={ref}>
      <PageHeader
        title="Live calls"
        description="Calls in progress right now: state, audio levels and the transcript as it happens. Listen in or hang up from here."
        actions={
          <>
            <Chip color={live.status === "open" ? "primary" : "neutral"} icon={<Radio aria-hidden />}>
              {live.status === "open" ? "Feed connected" : live.synced ? "Reconnecting" : "Connecting"}
            </Chip>
            <Chip color={count > 0 ? "primary" : "white"}>
              <span className="tabular">
                {count} live {count === 1 ? "call" : "calls"}
              </span>
            </Chip>
          </>
        }
      />

      <FeedBanner status={live.status} synced={live.synced} onReconnect={() => store.reconnectNow()} />

      {listen.uuid ? (
        <p className="sr-only" role="status">
          Listening in to a live call.
        </p>
      ) : null}

      {loading ? (
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2 2xl:grid-cols-3" role="status" aria-label="Loading live calls">
          <CardSkeleton />
          <CardSkeleton />
        </div>
      ) : count === 0 ? (
        <div data-anim className="rounded-xl border border-line bg-white">
          <EmptyState
            icon={<Radio />}
            tone="primary"
            title="No live calls right now"
            body="Start a campaign, call a lead, or wait for a customer to call your number: every call shows up here instantly. Want to hear the agent first? Run a browser test (no phone cost)."
            action={
              <>
                <Button variant="primary" icon={<Headphones />} onClick={() => testAgent.open()}>
                  Test agent
                </Button>
                <ButtonLink href="/leads" variant="secondary" icon={<Users />}>
                  Leads
                </ButtonLink>
                <ButtonLink href="/campaigns" variant="secondary" icon={<Megaphone />}>
                  Campaigns
                </ButtonLink>
              </>
            }
          />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2 2xl:grid-cols-3">
          {live.active.map((lc) => {
            const isListening = listen.uuid === lc.call.uuid;
            return (
              <LiveCallCard
                key={lc.call.uuid}
                live={lc}
                listening={isListening}
                listenStatus={listen.status}
                muted={listen.muted}
                onListen={() => listen.start(lc.call.uuid)}
                onStopListen={listen.stop}
                onToggleMute={listen.toggleMute}
              />
            );
          })}
        </div>
      )}

      <RecentlyEnded calls={live.recent} />
    </div>
  );
}
