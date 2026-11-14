"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ChartColumn,
  LayoutDashboard,
  Megaphone,
  PhoneCall,
  PhoneIncoming,
  Radio,
  Settings,
  SquareKanban,
  Users,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect } from "react";
import { isInboundPhone } from "@/components/inbound/caller";
import { useLive } from "@/components/providers/LiveProvider";
import { SignOutButton } from "./SignOutButton";
import { cn } from "@/lib/cn";

const NAV: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/analytics", label: "Analytics", icon: ChartColumn },
  { href: "/leads", label: "Leads", icon: Users },
  { href: "/board", label: "Lead board", icon: SquareKanban },
  { href: "/campaigns", label: "Campaigns", icon: Megaphone },
  { href: "/calls", label: "Calls", icon: PhoneCall },
  { href: "/inbound", label: "Inbound", icon: PhoneIncoming },
  { href: "/live", label: "Live", icon: Radio },
  { href: "/settings", label: "Settings", icon: Settings },
];

function isActive(pathname: string, href: string) {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pathname = usePathname();
  const live = useLive();
  const liveCount = live.active.length;
  // Customers on the line with the agent right now (browser rehearsals don't count).
  const inboundCount = live.active.filter((lc) => isInboundPhone(lc.call)).length;
  const badges: Record<string, { count: number; label: string }> = {
    "/live": { count: liveCount, label: "live calls" },
    "/inbound": { count: inboundCount, label: "live inbound calls" },
  };

  // Esc closes the mobile menu.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <>
      <div className={cn("fixed inset-0 z-40 bg-fg/30 lg:hidden", open ? "block" : "hidden")} onClick={onClose} aria-hidden />
      <aside
        id="app-sidebar"
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-[var(--sidebar-w)] flex-col border-r border-line bg-white transition-[translate,visibility] duration-200 ease-out lg:visible lg:translate-x-0",
          // `invisible` when closed on small screens: off-screen links must not stay in the Tab order.
          open ? "translate-x-0" : "invisible -translate-x-full",
        )}
        aria-label="Main navigation"
      >
        <div className="flex items-start gap-2 border-b border-line px-4 py-4">
          <div className="min-w-0 flex-1">
            {/* Brand logo: white wordmark (transparent PNG-style webp, 1136x480) on its logo-blue tile.
                Shown at 120px wide with its natural aspect ratio - never stretched, recoloured, cropped or rounded. */}
            <div className="inline-block bg-brand px-3 py-2.5">
              <Image
                src="/pouchwale_logo.webp"
                alt="PouchWale"
                width={1136}
                height={480}
                priority
                style={{ width: 120, height: "auto", display: "block" }}
              />
            </div>
            <p className="mt-2 font-heading text-sm font-semibold text-fg">Voice Agent</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1.5 text-muted hover:bg-subtle lg:hidden [&_svg]:size-4"
            aria-label="Close menu"
          >
            <X aria-hidden />
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto p-3">
          <ul className="space-y-0.5">
            {NAV.map(({ href, label, icon: Icon }) => {
              const active = isActive(pathname, href);
              const badge = badges[href];
              return (
                <li key={href}>
                  <Link
                    href={href}
                    onClick={onClose}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "relative flex h-9 items-center gap-3 rounded-lg px-3 text-sm transition-colors",
                      active ? "bg-primary-tint font-medium text-primary" : "text-subtle-fg hover:bg-canvas hover:text-fg",
                    )}
                  >
                    {active ? <span className="absolute inset-y-2 left-0 w-[3px] rounded-full bg-brand" aria-hidden /> : null}
                    <Icon className="size-4" aria-hidden />
                    <span className="flex-1">{label}</span>
                    {badge && badge.count > 0 ? (
                      <span className="tabular inline-flex min-w-5 items-center justify-center rounded-full bg-brand px-1.5 text-xs font-medium text-white">
                        {badge.count}
                        <span className="sr-only"> {badge.label}</span>
                      </span>
                    ) : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <SignOutButton />
      </aside>
    </>
  );
}
