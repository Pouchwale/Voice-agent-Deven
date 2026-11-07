"use client";

import Link from "next/link";
import { Check, ChevronDown, CircleCheck, CircleMinus, Info, Minus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { keys } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { SystemInfo } from "@/lib/types";

const PROVIDERS: { key: keyof SystemInfo["providers"]; label: string; role: string }[] = [
  { key: "openai", label: "OpenAI", role: "Language model" },
  { key: "sarvam", label: "Sarvam", role: "Speech (STT / TTS)" },
  { key: "vobiz", label: "Vobiz", role: "Phone calls" },
];

/** OK = blue check; missing = grey dash (the text label says which). */
function StatusMark({ ok }: { ok: boolean }) {
  return ok ? <Check className="size-3.5 text-primary" aria-hidden /> : <Minus className="size-3.5 text-faint" aria-hidden />;
}

export function ProviderStatus() {
  const { data, error, isLoading } = useSWR<SystemInfo>(keys.system, { refreshInterval: 60_000 });
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (isLoading && !data) {
    return <span className="skeleton inline-block h-8 w-40" aria-label="Loading provider status" />;
  }

  if (error && !data) {
    return (
      <span className="hidden items-center gap-1.5 px-2 text-[13px] text-muted sm:inline-flex">
        <Minus className="size-3.5 text-faint" aria-hidden />
        Status unavailable
      </span>
    );
  }
  if (!data) return null;

  const missing = PROVIDERS.filter((p) => !data.providers?.[p.key]);
  const warn = missing.length > 0 || !data.public_url_ok;

  return (
    <div className="relative" ref={wrapRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="dialog"
        className="inline-flex h-8 items-center gap-3 rounded-md px-2 text-[13px] whitespace-nowrap text-subtle-fg hover:bg-subtle"
      >
        <span className="hidden items-center gap-3 md:inline-flex">
          {PROVIDERS.map((p) => (
            <span key={p.key} className="inline-flex items-center gap-1.5">
              <StatusMark ok={!!data.providers?.[p.key]} />
              <span className={data.providers?.[p.key] ? undefined : "text-muted"}>{p.label}</span>
            </span>
          ))}
        </span>
        <span className="inline-flex items-center gap-1.5 md:hidden">
          <StatusMark ok={!warn} />
          <span className="hidden sm:inline">{warn ? "Setup needed" : "All set"}</span>
          <span className="sr-only sm:hidden">{warn ? "Setup needed" : "All set"}</span>
        </span>
        {!data.public_url_ok ? <Info className="size-3.5 text-fg" aria-label="Public URL not reachable" /> : null}
        <span className="sr-only">
          {PROVIDERS.map((p) => `${p.label} ${data.providers?.[p.key] ? "configured" : "not configured"}`).join(", ")}
        </span>
        <ChevronDown className="size-3.5 text-muted" aria-hidden />
      </button>

      {open ? (
        <div role="dialog" aria-label="Provider status" className="absolute top-[calc(100%+8px)] right-0 z-50 w-[320px] rounded-xl border border-line bg-white p-4 shadow-lg">
          <p className="font-heading text-sm font-semibold">Providers</p>
          <ul className="mt-3 divide-y divide-line">
            {PROVIDERS.map((p) => {
              const ok = !!data.providers?.[p.key];
              return (
                <li key={p.key} className="flex items-center gap-3 py-2">
                  {ok ? <CircleCheck className="size-4 text-primary" aria-hidden /> : <CircleMinus className="size-4 text-faint" aria-hidden />}
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{p.label}</p>
                    <p className="text-xs text-muted">{p.role}</p>
                  </div>
                  <span className={cn("text-xs", ok ? "text-primary" : "text-muted")}>{ok ? "Configured" : "Not configured"}</span>
                </li>
              );
            })}
          </ul>
          {!data.public_url_ok ? (
            <div className="mt-3 rounded-lg border border-navy p-3 text-[13px] text-fg">
              <p className="flex items-center gap-1.5 font-medium">
                <Info className="size-4" aria-hidden /> Public URL not reachable
              </p>
              <p className="mt-1 break-all">
                Backend public URL is <code className="font-mono text-xs">{data.public_base_url || "not set"}</code>. Vobiz can&apos;t
                reach localhost, so set a public URL (for example an ngrok tunnel) for phone calls. Browser tests still work.
              </p>
            </div>
          ) : null}
          <Link href="/settings#ai-brain" onClick={() => setOpen(false)} className="mt-3 inline-flex text-[13px] font-medium text-primary hover:underline">
            Test providers in Settings
          </Link>
        </div>
      ) : null}
    </div>
  );
}
