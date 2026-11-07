"use client";

import { createContext, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useSWRConfig } from "swr";
import { keys } from "@/lib/api";
import { isLoginPath } from "@/lib/auth";
import { LiveStore, type LiveSnapshot } from "@/lib/live-store";
import type { Campaign } from "@/lib/types";

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

  // Keep cached call details / lists fresh when the live feed reports changes.
  useEffect(
    () =>
      store.onCallUpdate((call) => {
        void mutate(keys.call(call.uuid));
        if (call.lead_id) void mutate(keys.lead(call.lead_id));
      }),
    [store, mutate],
  );

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
