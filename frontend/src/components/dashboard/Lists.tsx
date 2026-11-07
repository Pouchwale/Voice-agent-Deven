"use client";

import Link from "next/link";
import { ChevronRight, Globe, Phone } from "lucide-react";
import { InterestBadge } from "@/components/ui/InterestBadge";
import { RatingPill } from "@/components/ui/RatingPill";
import { useNow } from "@/hooks/useNow";
import { callTitle, formatDuration, formatPhone, formatRelative, leadName } from "@/lib/format";
import type { CallListItem, Lead } from "@/lib/types";

export function RecentCallsList({ calls }: { calls: CallListItem[] }) {
  const now = useNow(30_000);
  return (
    <ul className="-mx-2">
      {calls.map((c) => (
        <li key={c.uuid}>
          <Link href={`/calls/${c.uuid}`} className="flex items-center gap-3 rounded-lg px-2 py-2.5 hover:bg-canvas">
            <span
              className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg bg-subtle text-subtle-fg [&_svg]:size-4"
              title={c.channel === "browser" ? "Browser test" : "Phone call"}
            >
              {c.channel === "browser" ? <Globe aria-hidden /> : <Phone aria-hidden />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">{callTitle(c, "Unknown caller")}</span>
              <span className="block truncate text-xs font-normal text-muted">
                {[c.lead_company, formatDuration(c.duration_sec), formatRelative(c.started_at ?? c.created_at, now)]
                  .filter((x) => x && x !== "—")
                  .join(" · ")}
              </span>
            </span>
            <span className="hidden sm:inline-flex">
              <InterestBadge interest={c.interest} size="xs" />
            </span>
            <RatingPill rating={c.rating} />
            <ChevronRight className="size-4 shrink-0 text-muted" aria-hidden />
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function TopLeadsList({ leads }: { leads: Lead[] }) {
  const now = useNow(60_000);
  return (
    <ol className="-mx-2">
      {leads.map((l, i) => (
        <li key={l.id}>
          <Link href={`/leads/${l.id}`} className="flex items-start gap-3 rounded-lg px-2 py-2.5 hover:bg-canvas">
            <span className="tabular mt-0.5 inline-flex size-6 shrink-0 items-center justify-center rounded-md bg-subtle text-xs font-medium text-subtle-fg">
              {i + 1}
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="truncate text-sm font-medium">{leadName(l)}</span>
                <InterestBadge interest={l.interest} size="xs" />
                <RatingPill rating={l.rating} />
              </span>
              <span className="mt-0.5 block truncate text-xs font-normal text-muted">
                {[l.company, l.city, formatPhone(l.phone), l.last_called_at ? `called ${formatRelative(l.last_called_at, now)}` : null]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
              {l.last_summary ? <span className="text-mixed mt-1 line-clamp-2 block text-[13px] text-muted">{l.last_summary}</span> : null}
            </span>
            <ChevronRight className="mt-1 size-4 shrink-0 text-muted" aria-hidden />
          </Link>
        </li>
      ))}
    </ol>
  );
}
