"use client";

import Link from "next/link";
import { ArrowRight, PhoneOff } from "lucide-react";
import type { ReactNode } from "react";
import useSWR from "swr";
import { keys } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { AgentSettings } from "@/lib/types";

/** True while outbound calling is switched off in Settings (still loading counts as on: never block on a guess). */
export function useOutboundOff(): boolean {
  const { data } = useSWR<AgentSettings>(keys.settings);
  return data?.outbound_enabled === false;
}

/** Neutral notice (this theme has no warning colours) explaining why calling is unavailable, with the way back on. */
export function OutboundOffNotice({
  children,
  link = true,
  className,
}: {
  children: ReactNode;
  /** Off inside forms, where leaving the page would lose what was typed. */
  link?: boolean;
  className?: string;
}) {
  return (
    <div
      role="status"
      className={cn("flex flex-wrap items-start gap-x-3 gap-y-1.5 rounded-xl border border-line bg-subtle px-4 py-3 text-[13px]", className)}
    >
      <PhoneOff className="mt-0.5 size-4 shrink-0" aria-hidden />
      <p className="min-w-0 flex-1 basis-60 font-medium">{children}</p>
      {link ? (
        <Link
          href="/settings#calling"
          className="inline-flex items-center gap-1 font-semibold whitespace-nowrap text-primary underline-offset-4 hover:underline [&_svg]:size-3.5"
        >
          Turn it on in Settings
          <ArrowRight aria-hidden />
        </Link>
      ) : null}
    </div>
  );
}
