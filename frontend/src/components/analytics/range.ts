"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";
import { ymdIST } from "@/lib/format";
import type { AnalyticsParams } from "@/lib/types";

export type RangePreset = "today" | "7d" | "30d" | "month" | "custom";

export const PRESETS: { value: RangePreset; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "month", label: "This month" },
  { value: "custom", label: "Custom" },
];

const YMD = /^\d{4}-\d{2}-\d{2}$/;

/** Shift an IST calendar date (YYYY-MM-DD) by whole days. */
export function shiftYmd(ymd: string, days: number): string {
  const [y, m, d] = ymd.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + days));
  return dt.toISOString().slice(0, 10);
}

/** A real calendar date in YYYY-MM-DD form ("2026-02-30" or "2026-13-01" from a hand-edited URL are not). */
function isYmd(s: string): boolean {
  if (!YMD.test(s)) return false;
  const y = Number(s.slice(0, 4));
  return y >= 2000 && y <= 2100 && shiftYmd(s, 0) === s;
}

export function presetRange(preset: Exclude<RangePreset, "custom">, today = ymdIST()): { from: string; to: string } {
  switch (preset) {
    case "today":
      return { from: today, to: today };
    case "7d":
      return { from: shiftYmd(today, -6), to: today };
    case "month":
      return { from: `${today.slice(0, 8)}01`, to: today };
    case "30d":
    default:
      return { from: shiftYmd(today, -29), to: today };
  }
}

export interface AnalyticsFilters extends Required<Pick<AnalyticsParams, "from" | "to">> {
  preset: RangePreset;
  campaignId: number | "";
  includeTests: boolean;
}

/** Range, campaign and "include tests" live in the URL (?range=7d&from=&to=&campaign=&tests=1). Default: last 30 days. */
export function useAnalyticsFilters() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const filters: AnalyticsFilters = useMemo(() => {
    const raw = params.get("range") as RangePreset | null;
    const preset: RangePreset = raw && ["today", "7d", "30d", "month", "custom"].includes(raw) ? raw : "30d";
    let from: string;
    let to: string;
    if (preset === "custom") {
      const today = ymdIST();
      const f = params.get("from") ?? "";
      const t = params.get("to") ?? "";
      from = isYmd(f) ? f : shiftYmd(today, -29);
      to = isYmd(t) ? t : today;
      if (from > to) [from, to] = [to, from];
    } else {
      ({ from, to } = presetRange(preset));
    }
    const c = Number(params.get("campaign"));
    return { preset, from, to, campaignId: Number.isFinite(c) && c > 0 ? c : "", includeTests: params.get("tests") === "1" };
  }, [params]);

  const update = useCallback(
    (patch: Partial<Record<"range" | "from" | "to" | "campaign" | "tests", string | null>>) => {
      const next = new URLSearchParams(params.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === "") next.delete(k);
        else next.set(k, v);
      }
      const s = next.toString();
      router.replace(s ? `${pathname}?${s}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  const setPreset = useCallback(
    (p: RangePreset) => {
      if (p === "custom") update({ range: "custom", from: filters.from, to: filters.to });
      else update({ range: p === "30d" ? null : p, from: null, to: null });
    },
    [filters.from, filters.to, update],
  );

  const apiParams: AnalyticsParams = useMemo(
    () => ({ from: filters.from, to: filters.to, campaign_id: filters.campaignId, include_tests: filters.includeTests }),
    [filters],
  );

  return { filters, apiParams, update, setPreset };
}
