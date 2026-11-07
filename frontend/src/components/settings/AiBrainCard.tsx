"use client";

import { Brain, CircleCheck, CircleX, PlugZap, TriangleAlert } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import useSWR from "swr";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/States";
import { useToast } from "@/components/ui/Toast";
import { api, errorMessage, keys } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatLatency, titleCase } from "@/lib/format";
import type { ProviderTest, SystemInfo } from "@/lib/types";
import { SettingsSection } from "./SettingsSection";

const PROVIDER_LABEL: Record<string, string> = {
  openai: "OpenAI GPT-4o-mini",
  sarvam_tts: "Sarvam voice (REST)",
  sarvam_tts_stream: "Sarvam live voice (streaming)",
  sarvam_stt: "Sarvam speech recognition (realtime)",
  sarvam: "Sarvam",
  vobiz: "Vobiz telephony",
};

function isProviderTest(v: unknown): v is ProviderTest {
  return !!v && typeof v === "object" && "ok" in v;
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5 rounded-lg border border-line bg-white px-3 py-2">
      <dt className="text-xs font-semibold text-muted">{label}</dt>
      <dd className="truncate font-mono text-[14px] font-medium" title={typeof value === "string" ? value : undefined}>
        {value}
      </dd>
    </div>
  );
}

export function AiBrainCard() {
  const toast = useToast();
  const { data, error, isLoading, mutate } = useSWR<SystemInfo>(keys.system);
  const [testing, setTesting] = useState(false);
  const [results, setResults] = useState<[string, ProviderTest][] | null>(null);

  // Arriving via /settings#ai-brain: scroll once the card has content.
  useEffect(() => {
    if (data && typeof window !== "undefined" && window.location.hash === "#ai-brain") {
      document.getElementById("ai-brain")?.scrollIntoView({ block: "start" });
    }
  }, [data]);

  async function runTest() {
    setTesting(true);
    try {
      const res = await api.settings.testProviders();
      const rows = Object.entries(res as Record<string, unknown>).filter((e): e is [string, ProviderTest] =>
        isProviderTest(e[1]),
      );
      setResults(rows);
      const failed = rows.filter(([, r]) => !r.ok).length;
      toast(
        failed
          ? { tone: "error", title: `${failed} provider${failed === 1 ? "" : "s"} failed`, description: "See details in the AI Brain card." }
          : { tone: "success", title: "All providers responded" },
      );
    } catch (err) {
      toast({ tone: "error", title: "Provider test failed", description: errorMessage(err) });
    } finally {
      setTesting(false);
    }
  }

  return (
    <SettingsSection
      id="ai-brain"
      title="AI brain"
      subtitle="Read-only. These come from the backend .env file."
      icon={<Brain />}
      tone="subtle"
      actions={
        <Button size="sm" variant="primary" icon={<PlugZap />} loading={testing} onClick={() => void runTest()}>
          {testing ? "Testing… (up to ~10 s)" : "Test providers"}
        </Button>
      }
    >
      {error && !data ? (
        <ErrorState error={error} onRetry={() => void mutate()} title="Couldn't load system info" compact />
      ) : isLoading || !data ? (
        <div className="grid gap-2.5 sm:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-[58px] rounded-lg" />
          ))}
        </div>
      ) : (
        <>
          <dl className="grid gap-2.5 sm:grid-cols-3">
            <Row label="LLM model" value={data.llm_model} />
            <Row label="Max tokens" value={data.llm_max_tokens} />
            <Row label="Temperature" value={data.llm_temperature} />
            <Row label="STT model" value={data.stt_model} />
            <Row label="STT mode" value={data.stt_mode} />
            <Row label="TTS model" value={data.tts_model} />
          </dl>
          <div
            className={cn(
              "flex items-start gap-2.5 rounded-lg border border-line px-3 py-2.5 text-[13px] font-normal",
              data.public_url_ok ? "bg-primary-tint" : "bg-subtle",
            )}
          >
            {data.public_url_ok ? (
              <CircleCheck className="mt-0.5 size-4 shrink-0" aria-hidden />
            ) : (
              <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
            )}
            <p className="min-w-0 break-all">
              <span className="font-semibold">Public URL: </span>
              <code className="font-mono">{data.public_base_url || "not set"}</code>
              {data.public_url_ok
                ? " - Vobiz can reach the backend."
                : " - still localhost, so Vobiz can't reach it. Phone calls need a public URL (e.g. an ngrok tunnel); browser tests still work."}
            </p>
          </div>
        </>
      )}

      {results ? (
        <div>
          <p className="field-label">Provider test results</p>
          {results.length === 0 ? (
            <p className="text-sm font-normal text-muted">The backend returned no provider results.</p>
          ) : (
            <ul className="space-y-2">
              {results.map(([key, r]) => (
                <li
                  key={key}
                  className={cn(
                    "flex items-start gap-3 rounded-lg border border-line px-3 py-2.5",
                    r.ok ? "bg-white" : "bg-subtle",
                  )}
                >
                  {r.ok ? (
                    <CircleCheck className="mt-0.5 size-5 shrink-0" aria-hidden />
                  ) : (
                    <CircleX className="mt-0.5 size-5 shrink-0" aria-hidden />
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold">{PROVIDER_LABEL[key] ?? titleCase(key)}</p>
                    {r.detail ? <p className="mt-0.5 text-[13px] font-normal break-words text-muted">{r.detail}</p> : null}
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1">
                    <span
                      className={cn(
                        "rounded-full border border-line px-2 text-xs font-semibold ",
                        r.ok ? "bg-primary-tint" : "bg-brand",
                      )}
                    >
                      {r.ok ? "OK" : "Failed"}
                    </span>
                    {typeof r.latency_ms === "number" ? (
                      <span className="tabular text-[12px] font-medium text-muted">{formatLatency(r.latency_ms)}</span>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </SettingsSection>
  );
}
