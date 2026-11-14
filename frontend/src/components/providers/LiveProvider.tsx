"use client";

import { createContext, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useSWRConfig } from "swr";
import { keys } from "@/lib/api";
import { isLoginPath } from "@/lib/auth";
import { LiveStore, type LiveSnapshot } from "@/lib/live-store";
import type { CallListItem, Campaign } from "@/lib/types";

const LiveContext = createContext<LiveStore | null>(null);

export function LiveProvider({ children }: { children: ReactNode }) {
  const [store] = useState(() => new LiveStore());
  const { mutate } = useSWRConfig();
  // The login page has no session yet: don't open the live socket there (it would only be refused).
  const onLogin = isLoginPath(usePathname() ?? "");

  useEffect(() => {
    if (onLogin) return;
    store.connect();
    return () => store.disconnect();
  }, [store, onLogin]);

  // Keep cached call details / lists fresh when the live feed reports changes. (Only mounted SWR keys refetch.)
  useEffect(() => {
    let summaryTimer: ReturnType<typeof setTimeout> | null = null;
    const off = store.onCallUpdate((call) => {
      void mutate(keys.call(call.uuid));
      if (call.lead_id) {
        void mutate(keys.lead(call.lead_id));
        void mutate(keys.leadTimeline(call.lead_id));
      }
      // Inbound KPIs (any range): a phone call in either direction can move them (missed / answered / called back).
      // Coalesced, so a busy campaign doesn't refetch the summary on every ring. Never the Vobiz setup key.
      if (call.channel === "phone" && !summaryTimer) {
        summaryTimer = setTimeout(() => {
          summaryTimer = null;
          void mutate((key) => typeof key === "string" && key.startsWith("/api/inbound/summary"));
        }, 600);
      }
    });
    return () => {
      off();
      if (summaryTimer) clearTimeout(summaryTimer);
    };
  }, [store, mutate]);

  return <LiveContext.Provider value={store}>{children}</LiveContext.Provider>;
}

export function useLiveStore(): LiveStore {
  const store = useContext(LiveContext);
  if (!store) throw new Error("useLiveStore must be used inside <LiveProvider>");
  return store;
}

/** Live calls snapshot (re-renders on feed changes, coalesced per frame). */
export function useLive(): LiveSnapshot {
  const store = useLiveStore();
  return useSyncExternalStore(store.subscribe, store.getSnapshot, store.getServerSnapshot);
}

/** Subscribe to `campaign.update` events. */
export function useCampaignUpdates(fn: (c: Campaign) => void) {
  const store = useLiveStore();
  useEffect(() => store.onCampaign(fn), [store, fn]);
}

/** Subscribe to `call.update` events (a call was placed, rang, connected or ended). */
export function useCallUpdates(fn: (c: CallListItem) => void) {
  const store = useLiveStore();
  useEffect(() => store.onCallUpdate(fn), [store, fn]);
}
