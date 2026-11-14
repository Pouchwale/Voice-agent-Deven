"use client";

import Link from "next/link";
import { ArrowRight, PhoneIncoming } from "lucide-react";
import { CallTimer, StateChip } from "@/components/live/LiveCallCard";
import { Dot } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { formatNumber, formatPhone } from "@/lib/format";
import type { LiveCall } from "@/lib/live-store";
import { callerTitle, customerNumber } from "./caller";
import { CallerChip } from "./chips";

/** Inbound calls in progress right now (from the live feed). Renders nothing when there are none. */
export function LiveInbound({ calls }: { calls: LiveCall[] }) {
  if (calls.length === 0) return null;
  return (
    <section
      aria-labelledby="inbound-live-title"
      className="rounded-xl border border-primary-tint-strong bg-primary-tint p-4 sm:px-5"
      data-anim
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h2 id="inbound-live-title" className="flex items-center gap-2 font-heading text-base font-semibold">
          <Dot className="bg-primary" live />
          Live now
          <span className="tabular text-sm font-medium text-muted">
            {formatNumber(calls.length)} {calls.length === 1 ? "caller" : "callers"}
          </span>
        </h2>
        <ButtonLink href="/live" size="sm" iconRight={<ArrowRight />}>
          Listen on the Live page
        </ButtonLink>
      </div>
      <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {calls.map((lc) => {
          const { call } = lc;
          // The caller chip says "Hidden number", so the name line doesn't repeat it.
          const name = callerTitle(call, "Unknown caller", "Unknown caller");
          const raw = customerNumber(call);
          const number = raw ? formatPhone(raw) : null;
          return (
            <li key={call.uuid} className="flex min-w-0 items-start gap-3 rounded-lg border border-primary-tint-strong bg-white p-3">
              <span className="inline-flex size-9 shrink-0 items-center justify-center rounded-full bg-primary-tint text-primary [&_svg]:size-4">
                <PhoneIncoming aria-hidden />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  {call.lead_id ? (
                    <Link href={`/leads/${call.lead_id}`} className="truncate text-sm font-semibold hover:underline">
                      {name}
                    </Link>
                  ) : (
                    <p className="truncate text-sm font-semibold">{name}</p>
                  )}
                  <CallTimer call={call} compact />
                </div>
                {number && number !== name ? <p className="tabular truncate text-xs text-muted">{number}</p> : null}
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <CallerChip type={call.caller_type} />
                  <StateChip state={lc.state} call={call} size="xs" />
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
