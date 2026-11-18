"use client";

import { ArrowRight } from "lucide-react";
import { formatSettingValue, isLongText, lineDiff } from "@/lib/diff";
import { cn } from "@/lib/cn";

const asText = (v: unknown): string =>
  Array.isArray(v) ? v.map((x) => String(x)).join("\n") : typeof v === "string" ? v : v === null || v === undefined ? "" : formatSettingValue(v, 10_000);

/** What a change does to one setting: removed / added lines for texts and lists, "old → new" for short values. */
export function ValueChange({ before, after, className }: { before: unknown; after: unknown; className?: string }) {
  if (Array.isArray(before) || Array.isArray(after) || isLongText(before, after)) {
    const { removed, added } = lineDiff(asText(before), asText(after));
    if (!removed.length && !added.length) {
      return <p className={cn("text-[13px] text-muted", className)}>Only spacing changed.</p>;
    }
    return (
      <ul className={cn("text-mixed space-y-1 text-[13px] leading-relaxed", className)}>
        {removed.map((line, i) => (
          <li key={`r${i}`} className="flex gap-2 text-muted">
            <span aria-hidden className="w-3 shrink-0 select-none text-center">−</span>
            <span className="sr-only">Removed: </span>
            <span className="min-w-0 break-words line-through decoration-muted/60">{line}</span>
          </li>
        ))}
        {added.map((line, i) => (
          <li key={`a${i}`} className="flex gap-2 rounded-md bg-primary-tint px-1 text-fg">
            <span aria-hidden className="w-3 shrink-0 select-none text-center font-semibold text-primary">+</span>
            <span className="sr-only">Added: </span>
            <span className="min-w-0 break-words">{line}</span>
          </li>
        ))}
      </ul>
    );
  }
  return (
    <p className={cn("text-mixed flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]", className)}>
      <span className="break-words text-muted line-through decoration-muted/60">{formatSettingValue(before)}</span>
      <ArrowRight className="size-3.5 shrink-0 text-muted" aria-label="changed to" />
      <span className="break-words font-medium">{formatSettingValue(after)}</span>
    </p>
  );
}
