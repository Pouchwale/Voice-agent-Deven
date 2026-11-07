"use client";

import Link from "next/link";
import { CircleAlert, CircleCheck, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { anime, DUR, EASE_OUT, prefersReducedMotion } from "@/lib/anim";
import { cn } from "@/lib/cn";

export type ToastTone = "success" | "error" | "info";

export interface ToastInput {
  title: string;
  description?: string;
  tone?: ToastTone;
  action?: { label: string; href: string };
  duration?: number;
}

interface ToastItem extends ToastInput {
  id: number;
}

const ToastContext = createContext<((t: ToastInput) => void) | null>(null);

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}

const TONE: Record<ToastTone, { cls: string; icon: ReactNode }> = {
  success: { cls: "text-primary", icon: <CircleCheck aria-hidden /> },
  error: { cls: "text-fg", icon: <CircleAlert aria-hidden /> },
  info: { cls: "text-primary", icon: <Info aria-hidden /> },
};

function ToastCard({ item, onDismiss }: { item: ToastItem; onDismiss: (id: number) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const tone = TONE[item.tone ?? "info"];

  useLayoutEffect(() => {
    if (!ref.current || prefersReducedMotion()) return;
    anime({ targets: ref.current, translateY: [8, 0], opacity: [0, 1], duration: DUR.base, easing: EASE_OUT });
  }, []);

  return (
    <div
      ref={ref}
      role={item.tone === "error" ? "alert" : "status"}
      className="pointer-events-auto flex w-full items-start gap-3 rounded-xl border border-line bg-white p-3.5 shadow-lg"
    >
      <span className={cn("mt-0.5 shrink-0 [&_svg]:size-[18px]", tone.cls)}>{tone.icon}</span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold leading-snug">{item.title}</p>
        {item.description ? <p className="mt-0.5 break-words text-[13px] font-normal leading-snug">{item.description}</p> : null}
        {item.action ? (
          <Link
            href={item.action.href}
            onClick={() => onDismiss(item.id)}
            className="mt-1.5 inline-flex text-[13px] font-medium text-primary hover:underline"
          >
            {item.action.label}
          </Link>
        ) : null}
      </div>
      <button
        type="button"
        onClick={() => onDismiss(item.id)}
        className="-m-1 rounded-md p-1 text-muted hover:bg-subtle [&_svg]:size-4"
        aria-label="Dismiss notification"
      >
        <X aria-hidden />
      </button>
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => {
    setItems((list) => list.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer);
    timers.current.delete(id);
  }, []);

  const push = useCallback(
    (t: ToastInput) => {
      const id = nextId.current++;
      setItems((list) => [...list.slice(-3), { ...t, id }]);
      const duration = t.duration ?? (t.tone === "error" ? 8000 : t.action ? 7000 : 4500);
      timers.current.set(
        id,
        setTimeout(() => dismiss(id), duration),
      );
    },
    [dismiss],
  );

  const value = useMemo(() => push, [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        aria-relevant="additions"
        className="pointer-events-none fixed inset-x-3 bottom-3 z-[90] flex flex-col items-end gap-2 sm:inset-x-auto sm:right-5 sm:bottom-5 sm:w-[380px]"
      >
        {items.map((item) => (
          <ToastCard key={item.id} item={item} onDismiss={dismiss} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}
