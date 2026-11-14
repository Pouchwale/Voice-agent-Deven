"use client";

import Link from "next/link";
import { ArrowRight, PhoneCall, RefreshCw, TriangleAlert } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { Chip, Dot } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { TextAreaField } from "@/components/ui/Input";
import { Segmented } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { Switch } from "@/components/ui/Switch";
import { useNow } from "@/hooks/useNow";
import { errorMessage, keys } from "@/lib/api";
import { formatDuration, formatPhone, formatRelative } from "@/lib/format";
import type { InboundOffAction, InboundSetup } from "@/lib/types";
import { COMPANY_PLACEHOLDERS, fillTemplate, PLACEHOLDERS, type SectionProps } from "./model";
import { NumberField, SettingsSection } from "./SettingsSection";
import { SpokenLineField, type SpokenSample } from "./SpokenLineField";
import { usePreviewVoice, voiceOf } from "./VoicePreview";

/** Local readiness only (check=false): the Settings page never triggers Vobiz API calls. */
const SETUP_KEY = `${keys.inboundSetup}?check=false`;
/** Greetings longer than this (as heard) get talked over: the caller expects us to speak briefly, then listen. */
const GREETING_SOFT_MAX = 120;
/** The call-back greeting also says why we are calling, so it may run a little longer. */
const CALLBACK_SOFT_MAX = 160;
const SAMPLE_NAME = "Rahul";

const OFF_ACTIONS: { value: InboundOffAction; label: string }[] = [
  { value: "message", label: "Polite message, then hang up" },
  { value: "reject", label: "Reject with a busy tone" },
];

type Tone = "on" | "off" | "warn";
type Status = { label: string; tone: Tone; text: string; checkable?: boolean };
const DOT: Record<Tone, string> = { on: "bg-primary", off: "bg-faint", warn: "bg-fg" };

/** Where inbound stands, from the switch (unsaved value) and the backend's readiness checks. */
function inboundStatus(setup: InboundSetup | undefined, enabled: boolean, number: string): Status {
  if (!enabled) {
    return {
      label: "Off",
      tone: "off",
      text: "Callers get the “can't answer” response below. Their calls are still logged as missed, so the team can call back.",
    };
  }
  if (!setup) return { label: "Status unknown", tone: "off", text: "Couldn't read the inbound setup from the backend." };
  if (!setup.vobiz_ready) {
    return { label: "Not set up", tone: "warn", text: "The Vobiz keys or our number are missing in the backend .env file." };
  }
  if (!setup.public_url_ok) {
    return { label: "Not reachable", tone: "warn", text: "PUBLIC_BASE_URL isn't an https address, so Vobiz can't reach the agent." };
  }
  if (setup.app_urls_match === false) {
    return { label: "Reconnect needed", tone: "warn", text: "The tunnel URL changed since Vobiz was connected: click Connect again on the Inbound page." };
  }
  if (setup.number_linked === false) {
    return { label: "Not connected", tone: "warn", text: `Calls to ${number} don't reach the agent yet: connect the number on the Inbound page.` };
  }
  if (setup.number_linked === true) return { label: "Connected", tone: "on", text: `Calls to ${number} reach the agent.` };
  // The quick status doesn't ask Vobiz, so whether our number points at the agent is unknown until checked.
  return { label: "Not checked", tone: "off", text: "Whether Vobiz sends calls to the agent hasn't been checked yet.", checkable: true };
}

function InboundStatus({
  setup,
  error,
  enabled,
  number,
}: {
  setup: InboundSetup | undefined;
  error: unknown;
  enabled: boolean;
  number: string;
}) {
  const now = useNow(60_000);
  // One live Vobiz check, only when the owner asks for it (the Settings page never polls Vobiz).
  const [wantCheck, setWantCheck] = useState(false);
  const checked = useSWR<InboundSetup>(wantCheck ? keys.inboundSetup : null, {
    revalidateOnFocus: false,
    revalidateOnReconnect: false,
  });
  const view = checked.data ?? setup;

  if (enabled && !view && !error) return <Skeleton className="mt-3 h-6 w-64 rounded-full" />;
  const status = inboundStatus(view, enabled, number);
  const checking = wantCheck && checked.isValidating;

  return (
    <div className="mt-3 space-y-2 text-[13px]">
      <p className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <Chip size="xs" color={status.tone === "on" ? "primary" : "white"} icon={<Dot className={DOT[status.tone]} />}>
          {status.label}
        </Chip>
        <span className="text-muted">{status.text}</span>
        {status.checkable ? (
          <Button
            size="sm"
            variant="ghost"
            icon={<RefreshCw />}
            loading={checking}
            onClick={() => (wantCheck ? void checked.mutate() : setWantCheck(true))}
          >
            {checking ? "Checking Vobiz…" : "Check connection"}
          </Button>
        ) : null}
      </p>
      {wantCheck && checked.error && !checking ? (
        <p className="text-muted" role="status">
          Couldn&apos;t check Vobiz: {errorMessage(checked.error)}
        </p>
      ) : null}
      {view && enabled && !view.ai_ready ? (
        <p className="flex items-start gap-2 rounded-lg border border-line bg-subtle px-3 py-2 font-medium" role="status">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
          The OpenAI or Sarvam key is missing, so callers get the “can&apos;t answer” response until both are set in the
          backend .env file.
        </p>
      ) : null}
      {view && view.rejected_requests > 0 ? (
        <p className="flex items-start gap-2 rounded-lg border border-line bg-subtle px-3 py-2 font-medium" role="status">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
          Vobiz called an old inbound address {view.rejected_requests === 1 ? "once" : `${view.rejected_requests} times`}{" "}
          since the backend started: open the Inbound page and click Connect again.
        </p>
      ) : null}
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted">
        {view ? (
          <span>{view.last_inbound_at ? `Last inbound call ${formatRelative(view.last_inbound_at, now)}` : "No inbound calls yet"}</span>
        ) : null}
        <Link
          href="/inbound"
          className="inline-flex items-center gap-1 font-semibold text-primary underline-offset-4 hover:underline [&_svg]:size-3.5"
        >
          Inbound calls &amp; setup
          <ArrowRight aria-hidden />
        </Link>
      </p>
    </div>
  );
}

function SubHeading({ title, text }: { title: string; text: string }) {
  return (
    <div>
      <h3 className="text-sm font-semibold">{title}</h3>
      <p className="mt-0.5 text-[13px] text-muted">{text}</p>
    </div>
  );
}

export function CallingSection({ form, set, errors }: SectionProps) {
  const { data: setup, error: setupError } = useSWR<InboundSetup>(SETUP_KEY);
  const preview = usePreviewVoice();
  const voice = voiceOf(form);
  const number = setup?.number ? formatPhone(setup.number) : "our Vobiz number";

  /** Preview wiring for one line: the Play button speaks it in the current (unsaved) voice. */
  const playerFor = (id: string) => ({
    state: preview.stateFor(id),
    play: (text: string) => void preview.play(id, text, voice),
    stop: preview.stop,
  });
  const asUnknownCaller = (text: string) => fillTemplate(text, form, null);
  const asNamedLead = (text: string) => fillTemplate(text, form, SAMPLE_NAME);
  /** New callers are usually unknown numbers; a lead we already have by name hears it with their name. */
  const greetingSamples = (text: string): SpokenSample[] =>
    /\{lead_name(_ji)?\}/.test(text)
      ? [{ text: asUnknownCaller(text) }, { label: `To a lead we know as ${SAMPLE_NAME}`, text: asNamedLead(text) }]
      : [{ text: asUnknownCaller(text) }];

  return (
    <SettingsSection
      id="calling"
      title="Calling"
      subtitle="Switch outbound and inbound calling on or off, and choose what callers hear."
      icon={<PhoneCall />}
      tone="subtle"
    >
      <Switch
        label="Outbound calling"
        description="“Call now” buttons and campaigns. Off: no new calls go out, and running campaigns wait and carry on by themselves once you turn it back on. Browser tests still work."
        checked={form.outbound_enabled}
        onChange={(v) => set("outbound_enabled", v)}
      />

      <div className="border-t border-line pt-5">
        <Switch
          label="Inbound calling"
          description={`The agent answers calls to ${number}. It can only answer while this computer, the backend and the tunnel are running.`}
          checked={form.inbound_enabled}
          onChange={(v) => set("inbound_enabled", v)}
        />
        <InboundStatus setup={setup} error={setupError} enabled={form.inbound_enabled} number={number} />
      </div>

      <div className="space-y-4 rounded-xl border border-line bg-canvas p-3 sm:p-4">
        <SubHeading
          title="When the agent can't answer"
          text="Inbound calling is off, every line is busy, or the AI keys are missing. Each of these calls is logged as missed so the team can call back."
        />
        <div>
          <Segmented
            label="When the agent can't answer"
            options={OFF_ACTIONS}
            value={form.inbound_off_action}
            onChange={(v) => set("inbound_off_action", v)}
          />
          <p className="mt-1.5 text-[13px] font-normal text-muted">
            The message answers the call, so it is billed. A busy tone isn&apos;t answered, so it isn&apos;t billed.
          </p>
        </div>
        <SpokenLineField
          id="inbound_unavailable_message"
          label="Can't-answer message"
          hint={
            form.inbound_off_action === "reject"
              ? "Not played while “Reject with a busy tone” is selected."
              : "Played to the caller, then the call ends. Only the company and agent names can be filled in here."
          }
          value={form.inbound_unavailable_message}
          onChange={(v) => set("inbound_unavailable_message", v)}
          error={errors.inbound_unavailable_message}
          placeholders={COMPANY_PLACEHOLDERS}
          samples={[{ text: asUnknownCaller(form.inbound_unavailable_message) }]}
          preview={playerFor("unavailable")}
        />
      </div>

      <div className="space-y-5 border-t border-line pt-5">
        <SubHeading
          title="Greetings"
          text="The first thing the agent says. {lead_name_ji} becomes “Rahul जी” when we know the caller's name, else just “जी”."
        />
        <SpokenLineField
          id="inbound_greeting"
          label="Greeting for new callers"
          hint="Everyone who hasn't had a real conversation with us before: unknown numbers, and leads we never actually spoke with."
          value={form.inbound_greeting}
          onChange={(v) => set("inbound_greeting", v)}
          error={errors.inbound_greeting}
          placeholders={PLACEHOLDERS}
          samples={greetingSamples(form.inbound_greeting)}
          softMax={GREETING_SOFT_MAX}
          preview={playerFor("greeting")}
        />
        <SpokenLineField
          id="inbound_greeting_known"
          label="Greeting for returning callers"
          hint="Only for callers who already had a real conversation with us, so it never claims a talk that didn't happen."
          value={form.inbound_greeting_known}
          onChange={(v) => set("inbound_greeting_known", v)}
          error={errors.inbound_greeting_known}
          placeholders={PLACEHOLDERS}
          samples={[{ text: asNamedLead(form.inbound_greeting_known) }]}
          softMax={GREETING_SOFT_MAX}
          preview={playerFor("greeting_known")}
        />
        <SpokenLineField
          id="callback_greeting"
          label="Call-back greeting"
          hint="Said instead of the opening line when we call someone whose call to us was missed in the last 7 days and nobody has spoken with them since (Call back, Call now or a campaign)."
          value={form.callback_greeting}
          onChange={(v) => set("callback_greeting", v)}
          error={errors.callback_greeting}
          placeholders={PLACEHOLDERS}
          samples={[{ text: asNamedLead(form.callback_greeting) }]}
          softMax={CALLBACK_SOFT_MAX}
          preview={playerFor("callback")}
        />
      </div>

      <div className="space-y-5 border-t border-line pt-5">
        <SubHeading
          title="Inbound conversation"
          text="What the agent should do when a customer calls us. Outbound calls use the call script instead."
        />
        <TextAreaField
          id="inbound_purpose"
          label="Inbound call purpose"
          hint="The call flow for callers: find out why they called, help, qualify, and agree the next step."
          value={form.inbound_purpose}
          onChange={(e) => set("inbound_purpose", e.target.value)}
          error={errors.inbound_purpose}
          rows={9}
          inputClassName="min-h-[200px]"
          aside={<span className="tabular">{form.inbound_purpose.length.toLocaleString("en-IN")} chars</span>}
        />
        <div className="grid gap-4 sm:grid-cols-[200px_minmax(0,1fr)] sm:items-start">
          <NumberField
            label="Max inbound call length"
            unit="sec"
            min={60}
            max={3600}
            step={30}
            value={form.inbound_max_call_sec}
            onChange={(v) => set("inbound_max_call_sec", v)}
            error={errors.inbound_max_call_sec}
            hint={
              Number.isFinite(form.inbound_max_call_sec)
                ? `Wrapped up at ${formatDuration(form.inbound_max_call_sec)} (m:ss). Callers with a real enquiry talk longer than on cold calls.`
                : "Set a hard limit for inbound calls."
            }
          />
          <SpokenLineField
            id="inbound_closing_line"
            label="Inbound closing line"
            hint="Said when an inbound call reaches the max length, just before the agent hangs up."
            value={form.inbound_closing_line}
            onChange={(v) => set("inbound_closing_line", v)}
            error={errors.inbound_closing_line}
            samples={[{ text: asNamedLead(form.inbound_closing_line) }]}
            preview={playerFor("closing")}
          />
        </div>
      </div>
    </SettingsSection>
  );
}
