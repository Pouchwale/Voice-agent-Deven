"use client";

import { useCallback, useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { useToast } from "@/components/ui/Toast";
import { api, errorMessage, keys } from "@/lib/api";
import type { AgentSettings } from "@/lib/types";

/** Shown wherever a call button is disabled because outbound calling is switched off. */
export const OUTBOUND_OFF_HINT = "Outbound calling is off in Settings → Calling.";

/**
 * Starts a real phone call for a lead (POST /api/calls) and reports the result as a toast.
 * `outboundOff` lets every "Call now" / "Call back" button show the same disabled state and hint.
 */
export function useCallNow() {
  const toast = useToast();
  const { mutate } = useSWRConfig();
  const [callingId, setCallingId] = useState<number | null>(null);
  const { data: settings } = useSWR<AgentSettings>(keys.settings, { revalidateOnFocus: false });
  const outboundOff = settings?.outbound_enabled === false;

  const callNow = useCallback(
    async (lead: { id: number; name: string }) => {
      setCallingId(lead.id);
      try {
        await api.calls.start(lead.id);
        toast({
          tone: "success",
          title: `Calling ${lead.name}…`,
          description: "The agent is dialing now. Watch the call live.",
          action: { label: "Open live monitor", href: "/live" },
        });
        void mutate(
          (key) =>
            typeof key === "string" &&
            (key.startsWith("/api/leads") || key.startsWith("/api/calls") || key.startsWith("/api/inbound/summary")),
        );
      } catch (err) {
        toast({ tone: "error", title: `Couldn't call ${lead.name}`, description: errorMessage(err) });
      } finally {
        setCallingId(null);
      }
    },
    [toast, mutate],
  );

  return { callNow, callingId, outboundOff, outboundHint: outboundOff ? OUTBOUND_OFF_HINT : null };
}
