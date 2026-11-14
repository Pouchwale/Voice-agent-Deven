"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, EllipsisVertical, Phone } from "lucide-react";
import { useEffect, useRef, useState, type DragEvent, type KeyboardEvent } from "react";
import { Dot } from "@/components/ui/Badge";
import { useNow } from "@/hooks/useNow";
import { cn } from "@/lib/cn";
import { formatPhone, formatRelative, STAGE_LABEL, STAGES } from "@/lib/format";
import type { Lead, Stage } from "@/lib/types";

export const DRAG_MIME = "application/x-voice-agent-lead";

/** Card actions: "Call now" and "Move to …" (the keyboard alternative to dragging). */
function CardMenu({
  lead,
  stage,
  onCall,
  onMove,
  calling,
  callBlockedReason,
}: {
  lead: Lead;
  stage: Stage;
  onCall: () => void;
  onMove: (to: Stage) => void;
  calling: boolean;
  callBlockedReason: string | null;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    menuRef.current?.querySelector<HTMLButtonElement>("[role=menuitem]")?.focus();
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  function close(focusButton = true) {
    setOpen(false);
    if (focusButton) btnRef.current?.focus();
  }

  function onMenuKey(e: KeyboardEvent<HTMLDivElement>) {
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]:not(:disabled)") ?? []);
    const i = items.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      items[(i + 1) % items.length]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      items[(i - 1 + items.length) % items.length]?.focus();
    } else if (e.key === "Tab") {
      close(false);
    }
  }

  const itemCls = "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[13px] hover:bg-canvas focus:bg-canvas focus:outline-none disabled:opacity-50";

  return (
    <div className="relative" ref={wrapRef} onClick={(e) => e.stopPropagation()}>
      <button
        ref={btnRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Actions for ${lead.name || lead.phone}`}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex size-7 items-center justify-center rounded-md text-muted hover:bg-subtle hover:text-fg [&_svg]:size-4"
      >
        <EllipsisVertical aria-hidden />
      </button>
      {open ? (
        <div
          ref={menuRef}
          role="menu"
          aria-label={`Actions for ${lead.name || lead.phone}`}
          onKeyDown={onMenuKey}
          className="absolute top-8 right-0 z-30 w-48 rounded-lg border border-line bg-white p-1 shadow-lg"
        >
          <button
            type="button"
            role="menuitem"
            className={itemCls}
            disabled={calling || lead.status === "calling" || lead.status === "dnc" || !!callBlockedReason}
            aria-describedby={callBlockedReason && lead.status !== "dnc" ? `call-blocked-${lead.id}` : undefined}
            onClick={() => {
              close(false);
              onCall();
            }}
          >
            <Phone className="size-3.5 text-primary" aria-hidden />
            {lead.status === "dnc" ? "Do not call" : "Call now"}
          </button>
          {callBlockedReason && lead.status !== "dnc" ? (
            <p id={`call-blocked-${lead.id}`} className="px-2.5 pb-1 text-xs text-muted" role="presentation">
              {callBlockedReason}
            </p>
          ) : null}
          <p className="mt-1 border-t border-line px-2.5 pt-1.5 pb-1 text-xs text-muted" role="presentation">
            Move to…
          </p>
          {STAGES.filter((s) => s !== stage).map((s) => (
            <button
              key={s}
              type="button"
              role="menuitem"
              className={itemCls}
              onClick={() => {
                close();
                onMove(s);
              }}
            >
              <ArrowRight className="size-3.5 text-muted" aria-hidden />
              {STAGE_LABEL[s]}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function BoardCard({
  lead,
  stage,
  onCall,
  onMove,
  calling,
  callBlockedReason = null,
  onDragStateChange,
}: {
  lead: Lead;
  stage: Stage;
  onCall: () => void;
  onMove: (to: Stage) => void;
  calling: boolean;
  /** Why "Call now" is unavailable (e.g. outbound calling switched off), else null. */
  callBlockedReason?: string | null;
  onDragStateChange: (dragging: { id: number; from: Stage } | null) => void;
}) {
  const router = useRouter();
  const now = useNow(60_000);
  const [dragging, setDragging] = useState(false);
  const title = lead.name?.trim() || formatPhone(lead.phone);
  const place = [lead.company, lead.city].filter(Boolean).join(" · ");

  function onDragStart(e: DragEvent<HTMLDivElement>) {
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData(DRAG_MIME, JSON.stringify({ id: lead.id, from: stage }));
    e.dataTransfer.setData("text/plain", String(lead.id));
    setDragging(true);
    onDragStateChange({ id: lead.id, from: stage });
  }

  function onDragEnd() {
    setDragging(false);
    onDragStateChange(null);
  }

  return (
    <div
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onClick={() => router.push(`/leads/${lead.id}`)}
      className={cn(
        "group cursor-grab rounded-lg border border-line bg-white p-3 transition-colors hover:border-primary-tint-strong active:cursor-grabbing",
        dragging && "opacity-50",
      )}
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <Link
            href={`/leads/${lead.id}`}
            onClick={(e) => e.stopPropagation()}
            className="block truncate text-sm font-medium hover:text-primary hover:underline"
            draggable={false}
          >
            {title}
          </Link>
          {lead.name?.trim() ? <p className="tabular truncate text-xs text-muted">{formatPhone(lead.phone)}</p> : null}
        </div>
        {lead.rating !== null && lead.rating !== undefined ? (
          <span className="tabular shrink-0 rounded-md bg-subtle px-1.5 py-0.5 text-xs font-medium text-subtle-fg" title="Rating">
            {Math.round(lead.rating)}/10
          </span>
        ) : null}
        <CardMenu lead={lead} stage={stage} onCall={onCall} onMove={onMove} calling={calling} callBlockedReason={callBlockedReason} />
      </div>
      {place ? <p className="mt-1 truncate text-xs text-muted">{place}</p> : null}
      {lead.last_summary ? <p className="text-mixed mt-1.5 truncate text-[13px] text-subtle-fg" title={lead.last_summary}>{lead.last_summary}</p> : null}
      <div className="mt-2 flex items-center justify-between gap-2 text-xs text-muted">
        <span>{lead.last_called_at ? `Called ${formatRelative(lead.last_called_at, now)}` : "Not called yet"}</span>
        {lead.status === "calling" || calling ? (
          <span className="inline-flex items-center gap-1 font-medium text-primary">
            <Dot className="bg-primary" live />
            Calling now
          </span>
        ) : null}
      </div>
    </div>
  );
}
