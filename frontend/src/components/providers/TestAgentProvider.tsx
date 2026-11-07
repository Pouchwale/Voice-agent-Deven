"use client";

import dynamic from "next/dynamic";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import type { Lead } from "@/lib/types";

// Heavy (Web Audio + three.js) - only loaded the first time the panel opens.
const TestAgentPanel = dynamic(() => import("@/components/test-agent/TestAgentPanel"), { ssr: false });

interface TestAgentApi {
  open: (opts?: { lead?: Pick<Lead, "id" | "name" | "company"> | null }) => void;
  close: () => void;
  isOpen: boolean;
}

const TestAgentContext = createContext<TestAgentApi | null>(null);

export function TestAgentProvider({ children }: { children: ReactNode }) {
  const [isOpen, setOpen] = useState(false);
  const [everOpened, setEverOpened] = useState(false);
  const [lead, setLead] = useState<Pick<Lead, "id" | "name" | "company"> | null>(null);
  const [session, setSession] = useState(0);

  const open = useCallback<TestAgentApi["open"]>((opts) => {
    setLead(opts?.lead ?? null);
    setSession((s) => s + 1);
    setEverOpened(true);
    setOpen(true);
  }, []);
  const close = useCallback(() => setOpen(false), []);

  const value = useMemo(() => ({ open, close, isOpen }), [open, close, isOpen]);

  return (
    <TestAgentContext.Provider value={value}>
      {children}
      {everOpened && isOpen ? <TestAgentPanel key={session} onClose={close} initialLead={lead} /> : null}
    </TestAgentContext.Provider>
  );
}

export function useTestAgent(): TestAgentApi {
  const ctx = useContext(TestAgentContext);
  if (!ctx) throw new Error("useTestAgent must be used inside <TestAgentProvider>");
  return ctx;
}
