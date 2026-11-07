"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Headphones, Menu } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useLive } from "@/components/providers/LiveProvider";
import { useTestAgent } from "@/components/providers/TestAgentProvider";
import { Dot } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { isLoginPath } from "@/lib/auth";
import { OfflineBanner } from "./OfflineBanner";
import { ProviderStatus } from "./ProviderStatus";
import { Sidebar } from "./Sidebar";

function LiveIndicator() {
  const live = useLive();
  const n = live.active.length;
  if (n === 0) return null;
  return (
    <Link
      href="/live"
      className="tabular hidden h-8 items-center gap-1.5 rounded-md px-2.5 text-[13px] font-medium text-primary hover:bg-primary-tint sm:inline-flex"
    >
      <Dot className="bg-primary" live />
      {n} live {n === 1 ? "call" : "calls"}
    </Link>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const [navOpen, setNavOpen] = useState(false);
  const pathname = usePathname();
  const testAgent = useTestAgent();

  // close the mobile drawer on navigation
  useEffect(() => setNavOpen(false), [pathname]);

  // Sign-in screen: no app chrome (and no API-backed widgets that would 401 before login).
  if (isLoginPath(pathname ?? "")) return <main id="main">{children}</main>;

  return (
    <div className="min-h-dvh">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-[100] focus:rounded-md focus:bg-white focus:px-3 focus:py-2 focus:shadow-lg"
      >
        Skip to content
      </a>
      <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />

      <div className="lg:pl-[var(--sidebar-w)]">
        <header className="sticky top-0 z-30 border-b border-line bg-white">
          <div className="flex h-14 items-center gap-3 px-4 sm:px-6">
            <button
              type="button"
              className="rounded-md p-2 text-subtle-fg hover:bg-subtle lg:hidden [&_svg]:size-5"
              onClick={() => setNavOpen(true)}
              aria-label="Open menu"
              aria-controls="app-sidebar"
              aria-expanded={navOpen}
            >
              <Menu aria-hidden />
            </button>
            <p className="font-heading text-[15px] font-semibold whitespace-nowrap lg:hidden">Voice Agent</p>
            <div className="ml-auto flex items-center gap-2">
              <LiveIndicator />
              <ProviderStatus />
              <Button variant="primary" icon={<Headphones />} onClick={() => testAgent.open()}>
                <span className="hidden sm:inline">Test agent</span>
                <span className="sm:hidden">Test</span>
              </Button>
            </div>
          </div>
          <OfflineBanner />
        </header>

        <main id="main" className="mx-auto w-full max-w-[1400px] px-4 py-6 sm:px-6 lg:px-8">
          {children}
        </main>
      </div>
    </div>
  );
}
