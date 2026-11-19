"use client";

import { useRef, useSyncExternalStore, type KeyboardEvent } from "react";
import { cn } from "@/lib/cn";
import type { Design } from "@/lib/design";
import { currentDesign, subscribeDesign, switchDesign, warmUpDesignFonts } from "@/lib/design-switch";

const OPTIONS: { value: Design; label: string }[] = [
  { value: "original", label: "Original" },
  { value: "pouchwale", label: "PouchWale" },
];

const serverDesign = (): Design => "original";

/**
 * Top-bar switch between the original design and the PouchWale design (remembered in this browser).
 * From the `lg` breakpoint (sidebar visible): "Original | PouchWale", same look as <Segmented>. Below it the
 * top bar has no room for two options next to its own items, so it is one round switch ("PouchWale design",
 * on/off) with the PouchWale swatch.
 */
export function DesignToggle() {
  const design = useSyncExternalStore(subscribeDesign, currentDesign, serverDesign);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const pouchwale = design === "pouchwale";

  // Radio-group keys: arrows move the choice (and switch), Home/End jump to the first/last option.
  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const i = OPTIONS.findIndex((o) => o.value === design);
    let next: number;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % OPTIONS.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (i - 1 + OPTIONS.length) % OPTIONS.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = OPTIONS.length - 1;
    else return;
    e.preventDefault();
    buttons.current[next]?.focus();
    void switchDesign(OPTIONS[next].value);
  }

  return (
    <>
      <button
        type="button"
        role="switch"
        aria-checked={pouchwale}
        aria-label="PouchWale design"
        title={pouchwale ? "PouchWale design is on: tap for the original design" : "Switch to the PouchWale design"}
        onPointerDown={() => void warmUpDesignFonts()}
        onClick={() => void switchDesign(pouchwale ? "original" : "pouchwale")}
        className={cn(
          "design-toggle-switch inline-flex size-8 shrink-0 items-center justify-center rounded-lg border transition-colors lg:hidden",
          pouchwale ? "border-primary-tint bg-primary-tint" : "border-line bg-white hover:bg-canvas",
        )}
      >
        <span className="design-swatch design-swatch-pouchwale" aria-hidden />
      </button>

      <div
        role="radiogroup"
        aria-label="Dashboard design"
        onKeyDown={onKeyDown}
        // load the PouchWale fonts before the click, so the melt shows the PouchWale design in its own fonts
        onPointerEnter={() => void warmUpDesignFonts()}
        onFocus={() => void warmUpDesignFonts()}
        className="design-toggle hidden shrink-0 items-center gap-0.5 rounded-lg border border-line bg-white p-0.5 lg:inline-flex"
      >
        {OPTIONS.map((o, i) => {
          const active = o.value === design;
          return (
            <button
              key={o.value}
              ref={(el) => {
                buttons.current[i] = el;
              }}
              type="button"
              role="radio"
              aria-checked={active}
              tabIndex={active ? 0 : -1}
              title={`${o.label} design`}
              onClick={() => void switchDesign(o.value)}
              className={cn(
                "inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-[13px] font-medium whitespace-nowrap transition-colors",
                active ? "border-primary-tint bg-primary-tint text-primary" : "border-transparent text-muted hover:bg-canvas hover:text-fg",
              )}
            >
              <span className={cn("design-swatch", `design-swatch-${o.value}`)} aria-hidden />
              {o.label}
            </button>
          );
        })}
      </div>
    </>
  );
}
