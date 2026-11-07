"use client";

import type { ReactNode } from "react";
import { SWRConfig } from "swr";
import { ApiError, fetcher } from "@/lib/api";
import { ToastProvider } from "@/components/ui/Toast";
import { AuthProvider } from "./AuthProvider";
import { LiveProvider } from "./LiveProvider";
import { TestAgentProvider } from "./TestAgentProvider";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <SWRConfig
      value={{
        fetcher,
        dedupingInterval: 4000,
        focusThrottleInterval: 10_000,
        revalidateOnFocus: true,
        errorRetryCount: 4,
        // Don't hammer the backend on client errors (404 etc.); network errors retry with backoff.
        onErrorRetry: (err, _key, config, revalidate, { retryCount }) => {
          if (err instanceof ApiError && err.status >= 400 && err.status < 500) return;
          if (retryCount >= (config.errorRetryCount ?? 4)) return;
          setTimeout(() => revalidate({ retryCount }), Math.min(15_000, 1500 * 2 ** retryCount));
        },
      }}
    >
      <ToastProvider>
        <AuthProvider>
          <LiveProvider>
            <TestAgentProvider>{children}</TestAgentProvider>
          </LiveProvider>
        </AuthProvider>
      </ToastProvider>
    </SWRConfig>
  );
}
