"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { formatNumber } from "@/lib/format";
import { IconButton } from "./Button";

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
  busy,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (p: number) => void;
  busy?: boolean;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <nav className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-3" aria-label="Pagination">
      <p className="tabular text-[13px] text-muted" aria-live="polite">
        {total === 0 ? "No results" : `Showing ${formatNumber(from)}–${formatNumber(to)} of ${formatNumber(total)}`}
        {busy ? <span className="ml-2 text-muted">· updating…</span> : null}
      </p>
      <div className="flex items-center gap-2">
        <IconButton label="Previous page" icon={<ChevronLeft />} size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)} />
        <span className="tabular min-w-[84px] text-center text-[13px] text-muted">
          Page {page} / {pages}
        </span>
        <IconButton label="Next page" icon={<ChevronRight />} size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)} />
      </div>
    </nav>
  );
}
