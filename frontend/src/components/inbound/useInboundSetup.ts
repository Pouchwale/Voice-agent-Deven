"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import useSWR from "swr";
import { api, keys } from "@/lib/api";
import type { InboundSetup } from "@/lib/types";

const QUICK_KEY = `${keys.inboundSetup}?check=false`;

/** The locally known switches; when they change, the Vobiz-side facts and problems need a fresh check. */
function localSignature(s: InboundSetup): string {
  return [s.enabled, s.public_url_ok, s.vobiz_ready, s.ai_ready, s.rejected_requests > 0].join("|");
}

function newest(a: string | null, b: string | null): string | null {
  if (!a) return b;
  if (!b) return a;
  return a > b ? a : b; // same ISO UTC format on both sides, so string order = time order
}

/**
 * Inbound setup status for the Inbound page.
 * - The full check (asks Vobiz about the application and the number; up to 8 s per query and shared rate limits)
 *   runs on mount, after Connect / Disconnect, on "Check again", and once when a local switch changes - never on
 *   window focus or a timer, and the live feed never touches this key.
 * - A cheap `?check=false` read every 30 s keeps the local facts fresh ("Last inbound call", switches, AI keys).
 */
export function useInboundSetup() {
  const full = useSWR<InboundSetup>(keys.inboundSetup, {
    revalidateOnFocus: false,
    revalidateOnReconnect: false,
    dedupingInterval: 15_000,
  });
  const quick = useSWR<InboundSetup>(QUICK_KEY, { refreshInterval: 30_000 });
  const { mutate: mutateFull, isValidating: fullValidating } = full;
  const { mutate: mutateQuick } = quick;

  const data = useMemo<InboundSetup | undefined>(() => {
    const f = full.data;
    const q = quick.data;
    if (!f) return q;
    if (!q) return f;
    const merged: InboundSetup = {
      ...f,
      enabled: q.enabled,
      number: q.number,
      answer_url: q.answer_url,
      hangup_url: q.hangup_url,
      public_url_ok: q.public_url_ok,
      vobiz_ready: q.vobiz_ready,
      ai_ready: q.ai_ready,
      rejected_requests: q.rejected_requests,
      last_rejected_at: q.last_rejected_at,
      last_inbound_at: newest(f.last_inbound_at, q.last_inbound_at),
      problems: [...new Set([...(f.problems ?? []), ...(q.problems ?? [])])],
    };
    merged.ready =
      merged.enabled &&
      merged.public_url_ok &&
      merged.vobiz_ready &&
      merged.ai_ready &&
      merged.app_urls_match === true &&
      merged.number_linked === true;
    return merged;
  }, [full.data, quick.data]);

  // A switch flipped since the last full check (e.g. inbound turned off in Settings in another tab): check once.
  const fullSig = full.data ? localSignature(full.data) : null;
  const quickSig = quick.data ? localSignature(quick.data) : null;
  const lastForced = useRef<string | null>(null);
  useEffect(() => {
    if (!fullSig || !quickSig || fullSig === quickSig || fullValidating || lastForced.current === quickSig) return;
    lastForced.current = quickSig;
    void mutateFull();
  }, [fullSig, quickSig, fullValidating, mutateFull]);

  const recheck = useCallback(() => {
    void mutateFull();
    void mutateQuick();
  }, [mutateFull, mutateQuick]);

  const apply = useCallback(
    async (fresh: InboundSetup) => {
      await mutateFull(fresh, { revalidate: false });
      void mutateQuick();
      return fresh;
    },
    [mutateFull, mutateQuick],
  );

  /** Throws ApiError 409 when the number is routed elsewhere and `force` is false (the caller asks to confirm). */
  const connect = useCallback(async (force: boolean) => apply(await api.inbound.connect(force)), [apply]);
  const disconnect = useCallback(async () => apply(await api.inbound.disconnect()), [apply]);

  return {
    data,
    /** Neither request has succeeded yet. */
    loading: !data && !full.error && !quick.error,
    error: data ? undefined : (full.error ?? quick.error),
    /** A full Vobiz check is in flight. */
    checking: fullValidating,
    /** The full check hasn't completed yet (status unknown rather than "not connected"). */
    fullPending: !full.data && !full.error,
    recheck,
    connect,
    disconnect,
  };
}

export type InboundSetupState = ReturnType<typeof useInboundSetup>;
