"use client";

import { X } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { anime, DUR, EASE_OUT, prefersReducedMotion } from "@/lib/anim";
import { cn } from "@/lib/cn";
import { IconButton } from "./Button";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

let openCount = 0;

/** Shared dialog behaviour: portal, Esc to close, focus trap, focus restore, scroll lock. */
function useDialog(open: boolean, onClose: () => void, panelRef: React.RefObject<HTMLElement | null>) {
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    openCount += 1;
    document.body.style.overflow = "hidden";

    const panel = panelRef.current;
    const first = panel?.querySelector<HTMLElement>("[data-autofocus]") ?? panel?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel)?.focus();

    function onKey(e: KeyboardEvent) {
      if (!panelRef.current) return;
      // Only the top-most dialog reacts.
      const dialogs = document.querySelectorAll("[data-dialog-panel]");
      if (dialogs[dialogs.length - 1] !== panelRef.current) return;
      if (e.key === "Escape") {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key === "Tab") {
        const items = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
          (el) => el.offsetParent !== null || el === document.activeElement,
        );
        if (!items.length) {
          e.preventDefault();
          return;
        }
        const firstEl = items[0];
        const lastEl = items[items.length - 1];
        const active = document.activeElement;
        // Focus on the panel itself, or anywhere outside it (e.g. after a click on the backdrop): pull it back in.
        if (!active || active === panelRef.current || !panelRef.current.contains(active)) {
          e.preventDefault();
          (e.shiftKey ? lastEl : firstEl).focus();
        } else if (e.shiftKey && active === firstEl) {
          e.preventDefault();
          lastEl.focus();
        } else if (!e.shiftKey && document.activeElement === lastEl) {
          e.preventDefault();
          firstEl.focus();
        }
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      openCount -= 1;
      if (openCount <= 0) {
        openCount = 0;
        document.body.style.overflow = "";
      }
      previouslyFocused?.focus?.();
    };
  }, [open, panelRef]);
}

function useMounted() {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted;
}

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
}) {
  const mounted = useMounted();
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descId = useId();
  useDialog(open && mounted, onClose, panelRef);

  useLayoutEffect(() => {
    if (!open || !panelRef.current || prefersReducedMotion()) return;
    anime({
      targets: panelRef.current,
      opacity: [0, 1],
      translateY: [6, 0],
      duration: DUR.base,
      easing: EASE_OUT,
    });
  }, [open, mounted]);

  if (!mounted || !open) return null;
  const width = size === "sm" ? "max-w-md" : size === "lg" ? "max-w-3xl" : size === "xl" ? "max-w-5xl" : "max-w-xl";

  return createPortal(
    <div className="fixed inset-0 z-[80] flex items-end justify-center p-0 sm:items-center sm:p-6">
      <div className="absolute inset-0 bg-fg/40" onClick={onClose} aria-hidden />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descId : undefined}
        tabIndex={-1}
        data-dialog-panel
        className={cn(
          "relative flex max-h-[92dvh] w-full flex-col rounded-t-xl border border-line bg-white shadow-lg outline-none sm:rounded-xl",
          width,
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4 sm:px-6">
          <div className="min-w-0">
            <h2 id={titleId} className="font-heading text-base font-semibold">
              {title}
            </h2>
            {description ? (
              <p id={descId} className="mt-1.5 text-sm font-normal text-muted">
                {description}
              </p>
            ) : null}
          </div>
          <IconButton label="Close" icon={<X />} size="sm" onClick={onClose} />
        </div>
        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-6">{children}</div>
        {footer ? (
          <div className="flex flex-wrap items-center justify-end gap-3 border-t border-line px-5 py-4 sm:px-6">{footer}</div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}

/** Right-side slide-over panel. */
export function Drawer({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  width = "max-w-[520px]",
  labelledBy,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  width?: string;
  labelledBy?: string;
}) {
  const mounted = useMounted();
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useDialog(open && mounted, onClose, panelRef);

  useLayoutEffect(() => {
    if (!open || !panelRef.current || prefersReducedMotion()) return;
    anime({ targets: panelRef.current, translateX: [24, 0], opacity: [0, 1], duration: DUR.slow, easing: EASE_OUT });
  }, [open, mounted]);

  if (!mounted || !open) return null;

  return createPortal(
    <div className="fixed inset-0 z-[70] flex justify-end">
      <div className="absolute inset-0 bg-fg/40" onClick={onClose} aria-hidden />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy ?? (title ? titleId : undefined)}
        tabIndex={-1}
        data-dialog-panel
        className={cn("relative flex h-full w-full flex-col border-l border-line bg-white shadow-lg outline-none", width)}
      >
        {title ? (
          <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
            <div className="min-w-0">
              <h2 id={titleId} className="font-heading text-base font-semibold">
                {title}
              </h2>
              {description ? <p className="mt-0.5 text-[13px] text-muted">{description}</p> : null}
            </div>
            <IconButton label="Close panel" icon={<X />} size="sm" onClick={onClose} />
          </div>
        ) : null}
        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">{children}</div>
        {footer ? <div className="border-t border-line px-5 py-4">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}

/** Small confirm dialog. */
export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  body,
  confirmLabel = "Confirm",
  danger = false,
  busy = false,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: ReactNode;
  body?: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  busy?: boolean;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      size="sm"
      footer={
        <>
          <button type="button" className="btn border-line bg-white text-fg hover:bg-canvas" onClick={onClose} data-autofocus>
            Cancel
          </button>
          <button
            type="button"
            className={cn(
              "btn text-white",
              danger ? "border-fg bg-white !text-fg hover:bg-subtle" : "bg-primary hover:bg-primary-hover",
            )}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </>
      }
    >
      {body ? <div className="text-sm leading-relaxed">{body}</div> : null}
    </Modal>
  );
}
