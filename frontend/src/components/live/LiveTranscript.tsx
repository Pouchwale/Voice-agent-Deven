"use client";

import { useLayoutEffect, useRef } from "react";
import { ChatBubble } from "@/components/calls/ChatBubble";
import type { LiveBubble } from "@/lib/live-store";

const MAX_VISIBLE = 30;

/** Streaming transcript. Auto-scrolls only while the reader is already near the bottom. */
export function LiveTranscript({ bubbles }: { bubbles: LiveBubble[] }) {
  const boxRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const visible = bubbles.length > MAX_VISIBLE ? bubbles.slice(-MAX_VISIBLE) : bubbles;

  useLayoutEffect(() => {
    const el = boxRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [bubbles]);

  return (
    <div
      ref={boxRef}
      role="log"
      aria-live="off"
      aria-label="Live transcript"
      tabIndex={0}
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
      }}
      className="scroll-thin h-56 space-y-2 overflow-y-auto rounded-lg border border-line bg-white p-2.5"
    >
      {visible.length === 0 ? (
        <p className="flex h-full items-center justify-center text-center text-[13px] font-medium text-muted">
          Waiting for the first words…
        </p>
      ) : (
        visible.map((b) => (
          <ChatBubble
            key={b.key}
            role={b.role}
            text={b.text}
            offsetMs={b.offset_ms}
            interrupted={b.interrupted}
            partial={!b.final}
            compact
          />
        ))
      )}
    </div>
  );
}
