import { cn } from "@/lib/cn";

/** "7/10" with a small bar. Null rating renders a dash. */
export function RatingPill({ rating, className }: { rating: number | null | undefined; className?: string }) {
  if (rating === null || rating === undefined) {
    return (
      <span className={cn("inline-flex items-center text-sm text-muted", className)} aria-label="Not rated">
        —
      </span>
    );
  }
  const r = Math.max(0, Math.min(10, Math.round(rating)));
  return (
    <span
      className={cn("tabular inline-flex items-center gap-2 text-[13px] font-medium", className)}
      aria-label={`Rating ${r} out of 10`}
      title={`Rating ${r} / 10`}
    >
      <span className="relative block h-1.5 w-10 overflow-hidden rounded-full bg-subtle" aria-hidden>
        <span className="absolute inset-y-0 left-0 rounded-full bg-primary" style={{ width: `${r * 10}%` }} />
      </span>
      <span>
        {r}
        <span className="text-muted">/10</span>
      </span>
    </span>
  );
}
