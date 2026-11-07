"use client";

import { useCallback, useState } from "react";
import { useSWRConfig } from "swr";
import { useToast } from "@/components/ui/Toast";
import { api, errorMessage } from "@/lib/api";

/** Starts a real phone call for a lead (POST /api/calls) and reports the result as a toast. */
export function useCallNow() {
  const toast = useToast();
  const { mutate } = useSWRConfig();
  const [callingId, setCallingId] = useState<number | null>(null);

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
        void mutate((key) => typeof key === "string" && (key.startsWith("/api/leads") || key.startsWith("/api/calls")));
      } catch (err) {
        toast({ tone: "error", title: `Couldn't call ${lead.name}`, description: errorMessage(err) });
      } finally {
        setCallingId(null);
      }
    },
    [toast, mutate],
  );

  return { callNow, callingId };
}
