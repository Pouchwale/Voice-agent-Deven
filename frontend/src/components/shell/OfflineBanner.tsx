"use client";

import { RefreshCw, WifiOff } from "lucide-react";
import useSWR from "swr";
import { keys } from "@/lib/api";
import { API_BASE } from "@/lib/config";

/** Shows when GET /api/health fails. Polls every 10s so it clears itself when the backend returns. */
export function OfflineBanner() {
  const { error, isValidating, mutate } = useSWR<{ status: string }>(keys.health, {
    refreshInterval: 10_000,
    shouldRetryOnError: false,
    revalidateOnFocus: true,
    dedupingInterval: 2000,
  });

  if (!error) return null;

  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 bg-navy px-4 py-2 text-white sm:px-6">
      <WifiOff className="size-4 shrink-0" aria-hidden />
      <p className="min-w-0 flex-1 text-[13px]">
        <span className="font-semibold">Backend offline.</span> Can&apos;t reach <code className="font-mono text-xs">{API_BASE}</code>. Data
        may be out of date; retrying automatically.
      </p>
      <button
        type="button"
        onClick={() => void mutate()}
        className="inline-flex h-7 items-center gap-1.5 rounded-md border border-white/40 px-2 text-[13px] font-medium hover:bg-white/10"
        disabled={isValidating}
      >
        <RefreshCw className={isValidating ? "spin size-3.5" : "size-3.5"} aria-hidden />
        Retry now
      </button>
    </div>
  );
}
