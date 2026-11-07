"use client";

import type { LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "@/lib/cn";

export interface NavItem {
  id: string;
  label: string;
  icon: LucideIcon;
}

/** Anchor list: horizontal scroller on small screens, sticky vertical list on wide screens. */
export function SectionNav({ items }: { items: NavItem[] }) {
  const [active, setActive] = useState(items[0]?.id);

  useEffect(() => {
    const els = items.map((i) => document.getElementById(i.id)).filter((el): el is HTMLElement => !!el);
    if (!els.length) return;
    const io = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActive(visible[0].target.id);
      },
      { rootMargin: "-20% 0px -65% 0px" },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [items]);

  return (
    <nav aria-label="Settings sections" className="lg:sticky lg:top-24">
      <ul className="scroll-thin -mx-1 flex gap-1.5 overflow-x-auto px-1 pb-2 lg:mx-0 lg:flex-col lg:overflow-visible lg:px-0 lg:pb-0">
        {items.map(({ id, label, icon: Icon }) => {
          const on = active === id;
          return (
            <li key={id} className="shrink-0">
              <a
                href={`#${id}`}
                onClick={() => setActive(id)}
                aria-current={on ? "location" : undefined}
                className={cn(
                  "flex items-center gap-2 rounded-full border px-3 py-1.5 text-[12px] font-semibold whitespace-nowrap transition-colors",
                  on ? "border-line bg-canvas shadow-sm" : "border-transparent hover:border-line hover:bg-canvas",
                )}
              >
                <Icon className="size-4" aria-hidden />
                {label}
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
