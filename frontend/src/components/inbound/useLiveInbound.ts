"use client";

import { useCallback, useRef, useSyncExternalStore } from "react";
import { useLiveStore } from "@/components/providers/LiveProvider";
import type { LiveCall } from "@/lib/live-store";
import { isInboundPhone } from "./caller";

const NONE: LiveCall[] = [];

/**
 * Inbound phone calls in progress, from the live feed. Unlike useLive(), this only re-renders when that list or a
 * call's status / agent state changes - not on every transcript chunk - so a page with a big table stays cheap.
 */
export function useLiveInbound(): LiveCall[] {
  const store = useLiveStore();
  const cache = useRef<{ sig: string; list: LiveCall[] }>({ sig: "", list: NONE });
  const getSnapshot = useCallback(() => {
    const list = store.getSnapshot().active.filter((lc) => isInboundPhone(lc.call));
    const sig = list
      .map((lc) =>
        [lc.call.uuid, lc.call.status, lc.state, lc.call.answered_at, lc.call.lead_id, lc.call.lead_name, lc.call.caller_type].join("~"),
      )
      .join("|");
    // Same calls in the same states: hand back the previous array so React sees no change.
    if (sig !== cache.current.sig) cache.current = { sig, list };
    return cache.current.list;
  }, [store]);
  return useSyncExternalStore(store.subscribe, getSnapshot, () => NONE);
}

/** Has the live feed delivered its first snapshot? */
export function useLiveSynced(): boolean {
  const store = useLiveStore();
  const getSnapshot = useCallback(() => store.getSnapshot().synced, [store]);
  return useSyncExternalStore(store.subscribe, getSnapshot, () => false);
}

/** Is this lead on a live phone call right now (so a call back would be refused)? */
export function useLeadOnLiveCall(leadId: number | null): boolean {
  const store = useLiveStore();
  const getSnapshot = useCallback(
    () => !!leadId && store.getSnapshot().active.some((lc) => lc.call.lead_id === leadId && lc.call.channel === "phone"),
    [store, leadId],
  );
  return useSyncExternalStore(store.subscribe, getSnapshot, () => false);
}
