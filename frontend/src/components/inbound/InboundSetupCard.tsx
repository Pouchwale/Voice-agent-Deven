"use client";

import {
  Check,
  ChevronDown,
  Circle,
  CircleAlert,
  CircleCheck,
  CircleX,
  Copy,
  Info,
  PlugZap,
  RefreshCw,
  Smartphone,
  TriangleAlert,
  Unplug,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/Button";
import { CircleIcon } from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/Modal";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/States";
import { useToast } from "@/components/ui/Toast";
import { useNow } from "@/hooks/useNow";
import { ApiError, errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber, formatPhone, formatRelative } from "@/lib/format";
import type { InboundSetup } from "@/lib/types";
import type { InboundSetupState } from "./useInboundSetup";

const APP_NAME = "GP3-Voice-Agent-Inbound";

type DialogState = null | { kind: "connect" } | { kind: "conflict"; detail: string } | { kind: "disconnect" };

/** Where calls to our number go right now, in plain words. */
function routingText(s: InboundSetup, num: string, pending: boolean): string {
  if (!s.vobiz_ready) return "Add the Vobiz keys and number to the backend to see where calls go.";
  if (!s.checked) return pending ? `Checking with Vobiz where calls to ${num} go…` : `Couldn't check with Vobiz where calls to ${num} go right now.`;
  if (s.number_linked) return `Calls to ${num} go to the AI agent.`;
  if (s.number_on_trunk) return `Calls to ${num} currently go to a SIP trunk.`;
  if (s.number_app_id) return `Calls to ${num} currently go to the Vobiz application “${s.number_app_name || s.number_app_id}”.`;
  return `${num} isn't attached to any Vobiz application yet, so nobody answers its calls.`;
}

function CheckItem({ ok, label, detail }: { ok: boolean | null; label: string; detail?: ReactNode }) {
  return (
    <li className="flex items-start gap-2.5 py-1.5">
      <span className={cn("mt-0.5 shrink-0 [&_svg]:size-4", ok === true ? "text-primary" : ok === false ? "text-fg" : "text-faint")}>
        {ok === true ? <CircleCheck aria-hidden /> : ok === false ? <CircleX aria-hidden /> : <Circle aria-hidden />}
      </span>
      <span className="min-w-0 text-sm">
        {label}
        <span className="sr-only">: {ok === true ? "done" : ok === false ? "not yet" : "not checked"}</span>
        {detail ? <span className="block text-[13px] text-muted">{detail}</span> : null}
      </span>
    </li>
  );
}

function CopyField({ label, value }: { label: string; value: string }) {
  const id = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard blocked (http page, permissions): select the text so Ctrl+C works.
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }

  return (
    <div className="min-w-0">
      <label htmlFor={id} className="field-label">
        {label}
      </label>
      <div className="flex gap-2">
        <input
          ref={inputRef}
          id={id}
          readOnly
          value={value}
          onFocus={(e) => e.currentTarget.select()}
          className="field min-w-0 flex-1 font-mono text-xs"
        />
        <Button icon={copied ? <Check /> : <Copy />} onClick={() => void copy()} className="shrink-0" aria-label={`${copied ? "Copied" : "Copy"} ${label}`}>
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}

/** Vobiz connection status + one-click Connect / Disconnect. Collapses to one line once everything is ready. */
export function InboundSetupCard({ setup }: { setup: InboundSetupState }) {
  const toast = useToast();
  const now = useNow(30_000);
  const [openPref, setOpenPref] = useState<boolean | null>(null);
  const [dialog, setDialog] = useState<DialogState>(null);
  const [busy, setBusy] = useState(false);
  const bodyId = useId();
  const s = setup.data;

  if (!s) {
    if (setup.error) {
      return <ErrorState error={setup.error} onRetry={setup.recheck} title="Couldn't load the inbound setup" />;
    }
    return <Skeleton className="h-[76px] rounded-xl" />;
  }

  const num = s.number ? formatPhone(s.number) : "your Vobiz number";
  const pending = setup.fullPending;
  // Until Vobiz has answered, "not linked" only means "not known yet": no Connect button, no expanded checklist.
  const open = openPref ?? (!pending && !s.ready);
  const needsConnect = !pending && s.vobiz_ready && (s.number_linked !== true || s.app_urls_match !== true);
  const routing = routingText(s, num, pending);
  const connectBlock = !s.vobiz_ready
    ? "Add VOBIZ_AUTH_ID, VOBIZ_AUTH_TOKEN and VOBIZ_FROM_NUMBER to backend/.env, then restart the backend."
    : !s.public_url_ok
      ? "Set PUBLIC_BASE_URL in backend/.env to your https tunnel address, then restart the backend."
      : null;
  // rejected_requests counts since the backend started, so it stays > 0 after a fix: it only means callers are
  // being dropped while our application still has outdated URLs.
  const staleUrlDrops = s.rejected_requests > 0 && s.app_urls_match !== true;
  const turnedAway = s.enabled && s.number_linked !== false && (!s.ai_ready || !s.public_url_ok || staleUrlDrops);
  const lastInbound = s.last_inbound_at
    ? `${formatRelative(s.last_inbound_at, now)} (${formatDateTime(s.last_inbound_at)} IST)`
    : "none yet";
  const problems = Array.isArray(s.problems) ? s.problems.filter(Boolean) : [];

  const headline = !s.enabled
    ? "Inbound calling is switched off in Settings. Callers hear the unavailable message (or a busy tone) and show up as missed calls."
    : s.ready
      ? `Calls to ${num} are answered by the AI agent. Last inbound call: ${lastInbound}.`
      : pending
        ? "Checking the Vobiz connection…"
        : (problems[0] ?? "Finish the steps below to start answering calls.");

  async function connect(force: boolean) {
    setBusy(true);
    try {
      const fresh = await setup.connect(force);
      setDialog(null);
      toast({
        tone: "success",
        title: "Inbound connected",
        description: fresh.ready
          ? `Calls to ${num} now reach the AI agent. Call it from your mobile to test.`
          : "Vobiz was updated. Check the remaining items in the setup card.",
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && !force) {
        setDialog({ kind: "conflict", detail: err.detail });
      } else {
        setDialog(null);
        toast({ tone: "error", title: "Couldn't connect inbound", description: errorMessage(err) });
      }
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      await setup.disconnect();
      setDialog(null);
      toast({
        tone: "success",
        title: "Inbound disconnected",
        description: s?.previous_app_id
          ? `Calls to ${num} go back to the application they used before.`
          : `Calls to ${num} no longer reach the AI agent.`,
      });
    } catch (err) {
      setDialog(null);
      toast({ tone: "error", title: "Couldn't disconnect", description: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby={`${bodyId}-title`} className="rounded-xl border border-line bg-white" data-anim>
      <div className="flex flex-wrap items-start gap-3 p-4 sm:px-5">
        <CircleIcon tone={s.ready ? "primary" : "subtle"} size="sm">
          <PlugZap />
        </CircleIcon>
        <div className="min-w-0 flex-1 basis-60">
          <h2 id={`${bodyId}-title`} className="font-heading text-base font-semibold">
            Vobiz connection
          </h2>
          <p className="text-mixed mt-0.5 text-[13px] text-muted">{headline}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {needsConnect ? (
            <Button
              variant="primary"
              size="sm"
              icon={<PlugZap />}
              disabled={!!connectBlock || busy}
              title={connectBlock ?? undefined}
              onClick={() => setDialog({ kind: "connect" })}
            >
              {s.number_linked && s.app_urls_match === false ? "Connect again" : "Connect inbound on Vobiz"}
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="ghost"
            icon={<RefreshCw className={setup.checking ? "spin" : undefined} />}
            onClick={setup.recheck}
            disabled={setup.checking}
          >
            Check again
          </Button>
          <Button
            size="sm"
            variant="ghost"
            iconRight={<ChevronDown className={cn("transition-transform", open && "rotate-180")} />}
            aria-expanded={open}
            aria-controls={bodyId}
            onClick={() => setOpenPref(!open)}
          >
            {open ? "Hide details" : "Show details"}
          </Button>
        </div>
      </div>

      {turnedAway ? (
        <div role="alert" className="mx-4 mb-4 flex items-start gap-2.5 rounded-lg border border-line bg-subtle p-3 sm:mx-5">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-fg" aria-hidden />
          <div className="min-w-0 text-sm">
            <p className="font-semibold">Callers are being turned away right now.</p>
            <p className="text-[13px] text-subtle-fg">
              {!s.ai_ready
                ? "The AI keys (OpenAI / Sarvam) are missing, so every caller hears the unavailable message and is logged as missed."
                : !s.public_url_ok
                  ? "PUBLIC_BASE_URL isn't an https address, so the agent's audio can't connect. Callers hear the unavailable message."
                  : `Vobiz called an old inbound address ${formatNumber(s.rejected_requests)} ${s.rejected_requests === 1 ? "time" : "times"}${
                      s.last_rejected_at ? ` (last ${formatRelative(s.last_rejected_at, now)})` : ""
                    } and those callers were dropped. Connect again to give Vobiz the current address.`}
            </p>
          </div>
        </div>
      ) : null}

      {/* Always rendered (hidden when collapsed) so aria-controls always points at a real element. */}
      <div id={bodyId} hidden={!open} className="grid gap-5 border-t border-line p-4 sm:px-5 lg:grid-cols-2">
        <div className="min-w-0">
          <p className="field-label">Number</p>
          <p className="tabular text-[15px] font-semibold">{s.number ? formatPhone(s.number) : "Not configured"}</p>
          <p className="mt-1 text-[13px] text-muted">{routing}</p>
          {s.checked && (s.number_status || s.number_voice_enabled === false || s.concurrency_max !== null) ? (
            <p className="mt-1 text-[13px] text-muted">
              {[
                s.number_status ? `Number status: ${s.number_status}` : null,
                s.number_voice_enabled === false ? "voice calls disabled on this number" : null,
                s.concurrency_max !== null ? `account limit ${formatNumber(s.concurrency_max)} calls at once` : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          ) : null}

          <p className="field-label mt-4">Ready to answer?</p>
          <ul aria-label="Inbound readiness">
            <CheckItem
              ok={s.enabled}
              label="Inbound calling is switched on"
              detail={
                s.enabled ? undefined : (
                  <Link href="/settings#calling" className="font-medium text-primary hover:underline">
                    Turn it on in Settings → Calling
                  </Link>
                )
              }
            />
            <CheckItem ok={s.vobiz_ready} label="Vobiz keys and number are set" />
            <CheckItem ok={s.ai_ready} label="AI keys are set (OpenAI + Sarvam)" />
            <CheckItem ok={s.public_url_ok} label="Public https address (tunnel) is set" />
            <CheckItem ok={s.app_urls_match} label={`Vobiz application “${s.app_name || APP_NAME}” uses this dashboard's URLs`} />
            <CheckItem ok={s.number_linked} label={`${num} is attached to the AI agent`} />
          </ul>
          {connectBlock ? <p className="mt-2 text-[13px] text-muted">{connectBlock}</p> : null}
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          {problems.length ? (
            <div>
              <p className="field-label">To fix</p>
              <ul className="space-y-1.5">
                {problems.map((p) => (
                  <li key={p} className="text-mixed flex items-start gap-2 text-sm">
                    <CircleAlert className="mt-0.5 size-4 shrink-0 text-fg" aria-hidden />
                    {p}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {s.number_linked ? (
            <div className="flex items-start gap-3 rounded-lg border border-primary-tint-strong bg-primary-tint p-3">
              <Smartphone className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
              <div className="min-w-0 text-sm">
                <p className="font-medium">Test it: call {num} from your mobile.</p>
                <p className="text-[13px] text-subtle-fg">The call shows up on this page within a few seconds.</p>
                <p className="mt-1 text-[13px]">
                  <span className="font-medium">Last inbound call:</span> {lastInbound}
                </p>
              </div>
            </div>
          ) : null}

          <details className="group rounded-lg border border-line">
            <summary className="cursor-pointer list-none px-3 py-2.5 text-sm font-medium [&::-webkit-details-marker]:hidden">
              <span className="inline-flex items-center gap-1.5">
                <ChevronDown className="size-4 transition-transform group-open:rotate-180" aria-hidden />
                Webhook URLs for manual setup
              </span>
            </summary>
            <div className="space-y-3 border-t border-line p-3">
              <p className="text-[13px] text-muted">
                These URLs contain a secret. Paste them only into Vobiz: anyone who has them can send fake calls to the agent.
                If one leaks, change INBOUND_TOKEN_SALT in backend/.env and connect again.
              </p>
              <CopyField label="Answer URL" value={s.answer_url} />
              <CopyField label="Hangup URL" value={s.hangup_url} />
            </div>
          </details>

          <p className="flex items-start gap-2 text-[13px] text-muted">
            <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
            The agent answers only while this computer, the backend and the tunnel are running. Calls that ring while they
            are off are listed later as “Missed (agent offline)” so you can call back.
          </p>

          {s.number_linked ? (
            <div>
              <Button size="sm" variant="danger" icon={<Unplug />} disabled={busy} onClick={() => setDialog({ kind: "disconnect" })}>
                Disconnect
              </Button>
            </div>
          ) : null}
        </div>
      </div>

      <ConfirmDialog
        open={dialog?.kind === "connect"}
        onClose={() => !busy && setDialog(null)}
        onConfirm={() => void connect(false)}
        title="Connect inbound calling?"
        confirmLabel="Connect"
        busy={busy}
        body={
          <div className="space-y-2.5">
            <p>
              This creates (or updates) the Vobiz application <strong>{s.app_name || APP_NAME}</strong> with this
              dashboard&apos;s answer and hang-up URLs, and attaches <strong>{num}</strong> to it.
            </p>
            <p>{routing}</p>
            <p>
              After connecting, the AI agent answers <strong>every</strong> call to this number
              {s.enabled ? "." : " once inbound calling is switched on in Settings."} You can undo it with Disconnect.
            </p>
          </div>
        }
      />
      <ConfirmDialog
        open={dialog?.kind === "conflict"}
        onClose={() => !busy && setDialog(null)}
        onConfirm={() => void connect(true)}
        title="This number is in use elsewhere"
        confirmLabel="Connect anyway"
        busy={busy}
        body={
          <div className="space-y-2.5">
            <p className="text-mixed">{dialog?.kind === "conflict" ? dialog.detail : null}</p>
            <p>
              Connecting anyway moves every call to the AI agent. Disconnect later restores the previous routing when Vobiz
              reports it.
            </p>
          </div>
        }
      />
      <ConfirmDialog
        open={dialog?.kind === "disconnect"}
        onClose={() => !busy && setDialog(null)}
        onConfirm={() => void disconnect()}
        title="Disconnect inbound calling?"
        confirmLabel="Disconnect"
        danger
        busy={busy}
        body={
          s.previous_app_id ? (
            <p>
              Calls to <strong>{num}</strong> go back to the Vobiz application they used before ({s.previous_app_id}). The AI
              agent stops answering them.
            </p>
          ) : (
            <p>
              <strong>{num}</strong> is detached from the AI agent&apos;s application. Calls to it won&apos;t be answered by the
              agent until you connect again.
            </p>
          )
        }
      />
      <span className="sr-only" aria-live="polite">
        {setup.checking ? "Checking the Vobiz connection…" : ""}
      </span>
    </section>
  );
}
