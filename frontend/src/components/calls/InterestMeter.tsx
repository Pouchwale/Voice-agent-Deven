import { cn } from "@/lib/cn";
import { INTEREST_LABEL, INTEREST_ORDER } from "@/lib/format";
import { INTEREST_STYLE } from "@/lib/theme";
import type { Interest } from "@/lib/types";

const HINT: Record<Exclude<Interest, "unknown">, string> = {
  cold: "Not interested now",
  warm: "Open, needs follow-up",
  hot: "Strong interest, call back soon",
};

/** Simple 3-step indicator: cold → warm → hot; the active step is filled in its tier colour. "unknown" = not yet scored. */
export function InterestMeter({ interest }: { interest: Interest }) {
  return (
    <div>
      <ol className="grid grid-cols-3 overflow-hidden rounded-lg border border-line" aria-label={`Interest: ${INTEREST_LABEL[interest]}`}>
        {INTEREST_ORDER.map((tier, i) => {
          const active = tier === interest;
          return (
            <li
              key={tier}
              aria-current={active ? "step" : undefined}
              className={cn("min-w-0 px-3 py-2.5", i > 0 && "border-l border-line", active ? INTEREST_STYLE[tier].chip : "bg-white text-muted")}
            >
              <p className={cn("truncate text-[13px]", active && "font-semibold")}>{INTEREST_LABEL[tier]}</p>
              <p className={cn("hidden truncate text-xs sm:block", active ? "opacity-90" : "text-muted")}>
                {HINT[tier as Exclude<Interest, "unknown">]}
              </p>
            </li>
          );
        })}
      </ol>
      {interest === "unknown" ? <p className="mt-2 text-[13px] text-muted">Not scored yet: not enough signal to place this lead.</p> : null}
    </div>
  );
}
