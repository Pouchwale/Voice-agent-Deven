"use client";

import { cn } from "@/lib/cn";
import { formatOffset } from "@/lib/format";

export interface ChatBubbleProps {
  role: "agent" | "user" | "system";
  text: string;
  offsetMs?: number | null;
  interrupted?: boolean;
  /** Streaming (not yet final) text. */
  partial?: boolean;
  /** Highlight (e.g. currently playing in the recording). */
  active?: boolean;
  /** Makes the bubble a button (e.g. seek the recording). */
  onClick?: () => void;
  agentName?: string;
  compact?: boolean;
}

/** One transcript turn. Agent on the left, customer on the right, system notes centred. */
export function ChatBubble({ role, text, offsetMs, interrupted, partial, active, onClick, agentName = "Agent", compact }: ChatBubbleProps) {
  if (role === "system") {
    return (
      <div className="flex justify-center py-0.5">
        <p className="text-mixed max-w-[85%] text-center text-xs text-muted">
          {offsetMs !== undefined && offsetMs !== null ? <span className="tabular mr-1.5">{formatOffset(offsetMs)}</span> : null}
          {text}
        </p>
      </div>
    );
  }

  const isAgent = role === "agent";
  const body = (
    <>
      <span className="mb-0.5 flex items-center gap-2 text-xs text-muted">
        <span className="font-medium text-fg">{isAgent ? agentName : "Customer"}</span>
        {offsetMs !== undefined && offsetMs !== null ? <span className="tabular">{formatOffset(offsetMs)}</span> : null}
        {interrupted ? <span className="rounded bg-subtle px-1.5 text-fg">Interrupted</span> : null}
        {partial ? <span>typing…</span> : null}
      </span>
      <span className={cn("text-mixed block text-left whitespace-pre-wrap", compact ? "text-[13px]" : "text-sm", partial && "text-muted")}>
        {text}
        {interrupted ? <span aria-hidden> …</span> : null}
      </span>
    </>
  );

  const cls = cn(
    "block max-w-[86%] rounded-xl border px-3 py-2 transition-colors",
    isAgent ? "rounded-bl-sm border-line bg-white" : "rounded-br-sm border-primary-tint-strong bg-primary-tint",
    partial && "border-dashed",
    active && "ring-2 ring-primary",
    onClick && "cursor-pointer hover:border-primary",
  );

  return (
    <div className={cn("flex", isAgent ? "justify-start" : "justify-end")}>
      {onClick ? (
        <button type="button" onClick={onClick} className={cls} title={`Play from ${formatOffset(offsetMs ?? 0)}`}>
          <span className="sr-only">Play recording from {formatOffset(offsetMs ?? 0)}. </span>
          {body}
        </button>
      ) : (
        <div className={cls}>{body}</div>
      )}
    </div>
  );
}
