"use client";

import { RefreshCw, SquareKanban } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { useCallNow } from "@/components/calls/useCallNow";
import { Button } from "@/components/ui/Button";
import { SearchInput } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { useDebounced } from "@/hooks/useDebounced";
import { cn } from "@/lib/cn";
import { formatNumber, STAGE_HINT } from "@/lib/format";
import type { Lead, Stage } from "@/lib/types";
import { BoardCard, DRAG_MIME } from "./BoardCard";
import { useBoard, type ViewColumn } from "./useBoard";

/** Header treatment per column: neutral for new / no answer, then the tier ramp. */
const HEADER: Record<Stage, { wrap: string; title: string; count: string }> = {
  new: { wrap: "bg-white", title: "text-fg", count: "bg-subtle text-subtle-fg" },
  no_answer: { wrap: "bg-white", title: "text-fg", count: "bg-subtle text-subtle-fg" },
  cold: { wrap: "bg-subtle", title: "text-fg", count: "bg-white text-subtle-fg" },
  warm: { wrap: "bg-primary-tint", title: "text-primary-hover", count: "bg-white text-primary-hover" },
  hot: { wrap: "bg-white border-t-[3px] border-t-brand", title: "text-primary", count: "bg-brand text-white" },
};

function Column({
  col,
  dragging,
  onDragging,
  onDropLead,
  onMove,
  onCall,
  callingId,
  onLoadMore,
}: {
  col: ViewColumn;
  dragging: { id: number; from: Stage } | null;
  onDragging: (d: { id: number; from: Stage } | null) => void;
  onDropLead: (leadId: number, from: Stage, to: Stage) => void;
  onMove: (lead: Lead, to: Stage) => void;
  onCall: (lead: Lead) => void;
  callingId: number | null;
  onLoadMore: () => void;
}) {
  const [over, setOver] = useState(false);
  const h = HEADER[col.key];
  const isTarget = dragging !== null && dragging.from !== col.key;

  function onDragOver(e: DragEvent<HTMLElement>) {
    // Accept any of our card drags (checked on the event itself, so it doesn't depend on React state timing).
    if (!e.dataTransfer.types.includes(DRAG_MIME)) return;
    if (dragging && dragging.from === col.key) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (!over) setOver(true);
  }

  function onDragLeave(e: DragEvent<HTMLElement>) {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(false);
  }

  function onDrop(e: DragEvent<HTMLElement>) {
    e.preventDefault();
    setOver(false);
    let payload: { id: number; from: Stage } | null = null;
    try {
      payload = JSON.parse(e.dataTransfer.getData(DRAG_MIME));
    } catch {
      payload = dragging;
    }
    if (payload && payload.from !== col.key) onDropLead(payload.id, payload.from, col.key);
  }

  return (
    <section
      aria-labelledby={`col-${col.key}`}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className={cn(
        "flex max-h-[calc(100dvh-220px)] min-h-[320px] w-[85vw] max-w-[300px] shrink-0 snap-start flex-col overflow-hidden rounded-xl border bg-white transition-colors sm:w-[288px]",
        over ? "border-primary bg-primary-tint ring-2 ring-primary-tint-strong" : isTarget ? "border-dashed border-primary-tint-strong" : "border-line",
      )}
    >
      <header className={cn("border-b border-line px-3 py-2.5", h.wrap)}>
        <div className="flex items-center justify-between gap-2">
          <h2 id={`col-${col.key}`} className={cn("font-heading text-sm font-semibold", h.title)}>
            {col.title}
          </h2>
          <span className={cn("tabular rounded-full px-2 py-0.5 text-xs font-medium", h.count)} aria-label={`${col.count} leads`}>
            {formatNumber(col.count)}
          </span>
        </div>
        <p className="mt-0.5 truncate text-xs text-muted">{STAGE_HINT[col.key]}</p>
      </header>

      <div className="scroll-thin min-h-0 flex-1 space-y-2 overflow-y-auto p-2.5">
        {col.leads.length === 0 ? (
          <p className="rounded-lg border border-dashed border-line px-3 py-6 text-center text-xs text-muted">
            {over ? "Drop here" : "No leads"}
          </p>
        ) : (
          col.leads.map((lead) => (
            <BoardCard
              key={lead.id}
              lead={lead}
              stage={col.key}
              calling={callingId === lead.id}
              onCall={() => onCall(lead)}
              onMove={(to) => onMove(lead, to)}
              onDragStateChange={onDragging}
            />
          ))
        )}
        {col.canLoadMore ? (
          <Button size="sm" variant="ghost" block loading={col.loadingMore} onClick={onLoadMore}>
            Load more ({formatNumber(col.count - col.leads.length)})
          </Button>
        ) : null}
      </div>
    </section>
  );
}

export function BoardView() {
  const [q, setQ] = useState("");
  const search = useDebounced(q.trim(), 300);
  const { columns, data, error, isLoading, isValidating, refresh, loadMore, moveLead } = useBoard(search);
  const { callNow, callingId } = useCallNow();
  const [dragging, setDragging] = useState<{ id: number; from: Stage } | null>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);

  /** While dragging a card near the left/right edge, scroll the columns so off-screen columns can be reached. */
  function onScrollerDragOver(e: DragEvent<HTMLDivElement>) {
    const el = scrollerRef.current;
    if (!el || !e.dataTransfer.types.includes(DRAG_MIME)) return;
    const r = el.getBoundingClientRect();
    const edge = 72;
    if (e.clientX > r.right - edge) el.scrollBy({ left: 18 });
    else if (e.clientX < r.left + edge) el.scrollBy({ left: -18 });
  }

  const leadById = (id: number) => columns.flatMap((c) => c.leads).find((l) => l.id === id);
  const total = columns.reduce((s, c) => s + c.count, 0);

  return (
    <div className="min-w-0">
      <PageHeader
        title="Lead board"
        description="Every lead by stage. Drag a card to another column (or use its menu) to move it; the next scored call can move it again."
        actions={
          <Button size="sm" icon={<RefreshCw className={isValidating ? "spin" : undefined} />} onClick={() => void refresh()}>
            Refresh
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <SearchInput value={q} onChange={setQ} placeholder="Search name, phone, company, city" label="Search leads" className="w-full sm:w-80" />
        {data ? <p className="text-[13px] text-muted">{formatNumber(total)} leads</p> : null}
      </div>

      {error && !data ? (
        <ErrorState error={error} onRetry={() => void refresh()} title="Couldn't load the board" />
      ) : isLoading && !data ? (
        <div className="flex gap-4 overflow-hidden" role="status" aria-label="Loading board">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-[420px] w-[288px] shrink-0 rounded-xl" />
          ))}
        </div>
      ) : total === 0 && !search ? (
        <EmptyState icon={<SquareKanban />} title="No leads yet" body="Import leads on the Leads page and they'll show up here, sorted by stage." />
      ) : (
        <>
          {error ? <ErrorState className="mb-3" compact error={error} onRetry={() => void refresh()} title="Showing the last loaded board" /> : null}
          {/* Only this container scrolls horizontally; the page never does. */}
          <div
            ref={scrollerRef}
            onDragOver={onScrollerDragOver}
            className={cn("-mx-4 flex gap-3 overflow-x-auto px-4 pb-3 sm:mx-0 sm:px-0", dragging ? "snap-none" : "snap-x snap-mandatory")}
            role="region"
            aria-label="Lead board columns"
          >
            {columns.map((col) => (
              <Column
                key={col.key}
                col={col}
                dragging={dragging}
                onDragging={setDragging}
                onDropLead={(id) => {
                  const lead = leadById(id);
                  if (lead) void moveLead(lead, col.key);
                  setDragging(null);
                }}
                onMove={(lead, to) => void moveLead(lead, to)}
                onCall={(lead) => void callNow({ id: lead.id, name: lead.name || lead.phone })}
                callingId={callingId}
                onLoadMore={() => void loadMore(col.key)}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
