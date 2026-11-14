"use client";

import Link from "next/link";
import {
  AudioLines,
  Brain,
  Ear,
  Headphones,
  Laptop,
  LoaderCircle,
  Phone,
  PhoneOff,
  Volume2,
  VolumeX,
} from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";
import { callerTitle, customerNumber, epochMs } from "@/components/inbound/caller";
import { CallerChip, DirectionChip } from "@/components/inbound/chips";
import { Chip, type ChipColor } from "@/components/ui/Badge";
import { Button, IconButton } from "@/components/ui/Button";
import { CallStatusChip } from "@/components/ui/StatusChip";
import { useToast } from "@/components/ui/Toast";
import { useNow } from "@/hooks/useNow";
import { api, errorMessage } from "@/lib/api";
import { formatDuration, formatPhone, initials } from "@/lib/format";
import type { LiveCall } from "@/lib/live-store";
import type { AgentState, CallListItem } from "@/lib/types";
import { LevelBars } from "./LevelBars";
import { LiveTranscript } from "./LiveTranscript";
import type { ListenStatus } from "./useListenIn";

const STATE: Record<AgentState, { label: string; color: ChipColor; icon: ReactNode }> = {
  connecting: { label: "Connecting", color: "white", icon: <LoaderCircle className="spin" aria-hidden /> },
  listening: { label: "Listening", color: "neutral", icon: <Ear aria-hidden /> },
  thinking: { label: "Thinking", color: "primary", icon: <Brain aria-hidden /> },
  speaking: { label: "Speaking", color: "primary-solid", icon: <AudioLines aria-hidden /> },
  ended: { label: "Ended", color: "white", icon: <PhoneOff aria-hidden /> },
};

/** What the agent is doing right now (listening / thinking / speaking…). */
export function StateChip({ state, call, size = "sm" }: { state: AgentState | null; call: CallListItem; size?: "xs" | "sm" }) {
  // Before the agent reports a state, derive one from the call status.
  const s: AgentState = state ?? (call.status === "in_progress" ? "listening" : "connecting");
  const cfg = STATE[s];
  return (
    <span aria-live="polite">
      <Chip size={size} color={cfg.color} icon={cfg.icon}>
        {state === null && call.status !== "in_progress" ? "Connecting" : cfg.label}
      </Chip>
    </span>
  );
}

/** Running call length (from pick-up, else from the start). `compact` = one inline line for lists. */
export function CallTimer({ call, compact = false }: { call: CallListItem; compact?: boolean }) {
  const now = useNow(1000);
  const startMs = epochMs(call.answered_at ?? call.started_at ?? call.created_at);
  const secs = Number.isFinite(startMs) ? Math.max(0, (now - startMs) / 1000) : null;
  // An inbound call has been answered by Vobiz before our audio starts: that gap is "picking up", not "dialing".
  const caption = call.answered_at ? "On call" : call.direction === "inbound" ? "Picking up" : "Since dial";
  if (compact) {
    return (
      <span className="tabular inline-flex items-baseline gap-1.5 text-sm font-semibold">
        <span aria-label="Call duration">{formatDuration(secs, "0:00")}</span>
        <span className="text-xs font-medium text-muted">{caption}</span>
      </span>
    );
  }
  return (
    <div className="text-right">
      <p className="tabular text-2xl leading-none font-semibold" aria-label="Call duration">
        {formatDuration(secs, "0:00")}
      </p>
      <p className="mt-1 text-xs font-semibold text-muted">{caption}</p>
    </div>
  );
}

export function LiveCallCard({
  live,
  listening,
  listenStatus,
  muted,
  onListen,
  onStopListen,
  onToggleMute,
}: {
  live: LiveCall;
  listening: boolean;
  listenStatus: ListenStatus;
  muted: boolean;
  onListen: () => void;
  onStopListen: () => void;
  onToggleMute: () => void;
}) {
  const { call, state, transcript } = live;
  const toast = useToast();
  const [hangingUp, setHangingUp] = useState(false);
  const inbound = call.direction === "inbound";
  // Inbound cards carry a caller chip ("Hidden number"), so the name line doesn't repeat it.
  const name = callerTitle(call, inbound ? "Unknown caller" : "Unknown lead", "Unknown caller");
  const rawNumber = call.channel === "phone" ? customerNumber(call) : null;
  const number = rawNumber ? formatPhone(rawNumber) : null;

  async function hangup() {
    setHangingUp(true);
    try {
      await api.calls.hangup(call.uuid);
      toast({ tone: "success", title: "Hanging up", description: `Ending the call with ${name}.` });
    } catch (err) {
      toast({ tone: "error", title: "Couldn't hang up", description: errorMessage(err) });
      setHangingUp(false);
    }
  }

  return (
    <article data-anim className="flex flex-col gap-3.5 rounded-xl border border-line bg-white p-4" aria-label={`Live call with ${name}`}>
      <header className="flex items-start gap-3">
        <span
          className="inline-flex size-11 shrink-0 items-center justify-center rounded-full border border-line bg-canvas text-sm font-semibold"
          aria-hidden
        >
          {initials(call.lead_name)}
        </span>
        <div className="min-w-0 flex-1">
          {call.lead_id ? (
            <Link
              href={`/leads/${call.lead_id}`}
              className="block truncate text-base font-semibold underline-offset-4 hover:underline"
            >
              {name}
            </Link>
          ) : (
            <p className="truncate text-base font-semibold">{name}</p>
          )}
          <p className="truncate text-[13px] font-normal text-muted">
            {[call.lead_company, number && number !== name ? number : null].filter(Boolean).join(" · ") || "—"}
          </p>
        </div>
        <CallTimer call={call} />
      </header>

      <div className="flex flex-wrap items-center gap-1.5">
        <Chip size="xs" color="white" icon={call.channel === "browser" ? <Laptop aria-hidden /> : <Phone aria-hidden />}>
          {call.channel === "browser" ? "Browser" : "Phone"}
        </Chip>
        {inbound ? <DirectionChip direction="inbound" /> : null}
        {inbound ? <CallerChip type={call.caller_type} /> : null}
        <CallStatusChip status={call.status} direction={call.direction} />
        <StateChip state={state} call={call} />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-canvas px-3 py-2">
        <LevelBars uuid={call.uuid} track="user" label="Customer" barClass="bg-primary" />
        <LevelBars uuid={call.uuid} track="agent" label="Agent" barClass="bg-brand" />
      </div>

      <LiveTranscript bubbles={transcript} />

      <footer className="flex flex-wrap items-center gap-2">
        {listening ? (
          <>
            <Button
              size="sm"
              variant="primary"
              icon={listenStatus === "connecting" ? <LoaderCircle className="spin" /> : <Headphones />}
              onClick={onStopListen}
              aria-pressed
            >
              {listenStatus === "connecting" ? "Connecting…" : "Listening"}
            </Button>
            <IconButton
              size="sm"
              label={muted ? "Unmute listen-in" : "Mute listen-in"}
              icon={muted ? <VolumeX /> : <Volume2 />}
              onClick={onToggleMute}
              aria-pressed={muted}
            />
          </>
        ) : (
          <Button size="sm" variant="secondary" icon={<Headphones />} onClick={onListen} aria-pressed={false}>
            Listen in
          </Button>
        )}
        <Button
          size="sm"
          variant="danger"
          className="ml-auto"
          icon={<PhoneOff />}
          loading={hangingUp}
          onClick={hangup}
        >
          Hang up
        </Button>
      </footer>
    </article>
  );
}
