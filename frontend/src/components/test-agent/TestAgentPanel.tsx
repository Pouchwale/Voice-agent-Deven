"use client";

import { ArrowRight, Headphones, Mic, MicOff, PhoneOff, RotateCcw, Search, UserRound, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { ChatBubble } from "@/components/calls/ChatBubble";
import { VoiceOrb, type OrbState } from "@/components/orb/VoiceOrb";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Chip, type ChipColor } from "@/components/ui/Badge";
import { TextField } from "@/components/ui/Input";
import { Drawer } from "@/components/ui/Modal";
import { useDebounced } from "@/hooks/useDebounced";
import { useNow } from "@/hooks/useNow";
import { keys } from "@/lib/api";
import { endReasonLabel, formatDuration, formatPhone, leadName } from "@/lib/format";
import type { Lead, Page } from "@/lib/types";
import { useBrowserCall, type CallPhase } from "./useBrowserCall";

type PickedLead = Pick<Lead, "id" | "name" | "company">;

const STATE_LABEL: Record<OrbState, string> = {
  idle: "Idle",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
};

const STATE_CHIP: Record<OrbState, ChipColor> = {
  idle: "white",
  listening: "neutral",
  thinking: "primary",
  speaking: "primary-solid",
};

function LeadPicker({ value, onChange }: { value: PickedLead | null; onChange: (l: PickedLead | null) => void }) {
  const [q, setQ] = useState("");
  const search = useDebounced(q.trim(), 250);
  const { data, isLoading, error } = useSWR<Page<Lead>>(search ? keys.leads({ search, page_size: 6 }) : null, {
    keepPreviousData: true,
  });

  if (value) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-line bg-canvas px-3 py-2.5">
        <UserRound className="size-4 shrink-0 text-muted" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{value.name?.trim() || "Unnamed lead"}</p>
          {value.company ? <p className="truncate text-[13px] text-muted">{value.company}</p> : null}
        </div>
        <button
          type="button"
          onClick={() => onChange(null)}
          className="rounded-md p-1 text-muted hover:bg-subtle [&_svg]:size-4"
          aria-label="Clear selected lead"
        >
          <X aria-hidden />
        </button>
      </div>
    );
  }

  return (
    <div>
      <label htmlFor="ta-lead-search" className="field-label">
        Pick a lead (optional)
      </label>
      <div className="relative">
        <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" aria-hidden />
        <input
          id="ta-lead-search"
          type="search"
          className="field pl-9"
          placeholder="Search leads by name, phone or company"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          autoComplete="off"
        />
      </div>
      {search ? (
        <ul className="mt-2 max-h-56 overflow-y-auto rounded-lg border border-line" aria-label="Matching leads">
          {isLoading && !data ? <li className="px-3 py-2 text-sm font-normal text-muted">Searching…</li> : null}
          {error ? <li className="px-3 py-2 text-sm font-normal">Couldn&apos;t search leads.</li> : null}
          {data && data.items.length === 0 ? <li className="px-3 py-2 text-sm font-normal text-muted">No matching leads.</li> : null}
          {data?.items.map((l) => (
            <li key={l.id} className="border-b border-line last:border-b-0">
              <button
                type="button"
                className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-canvas"
                onClick={() => {
                  onChange({ id: l.id, name: leadName(l), company: l.company });
                  setQ("");
                }}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{leadName(l)}</span>
                  <span className="block truncate text-xs font-normal text-muted">
                    {[l.company, formatPhone(l.phone)].filter(Boolean).join(" · ")}
                  </span>
                </span>
                <ArrowRight className="size-4 shrink-0 text-muted" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function phaseText(phase: CallPhase): string {
  switch (phase) {
    case "mic":
      return "Waiting for microphone permission…";
    case "connecting":
      return "Connecting to the agent…";
    case "ending":
      return "Hanging up…";
    default:
      return "";
  }
}

export default function TestAgentPanel({ onClose, initialLead }: { onClose: () => void; initialLead: PickedLead | null }) {
  const call = useBrowserCall();
  const [lead, setLead] = useState<PickedLead | null>(initialLead);
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const now = useNow(1000);
  const scrollRef = useRef<HTMLDivElement>(null);
  const { phase } = call;

  // keep the transcript pinned to the newest turn
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [call.bubbles]);

  const inCall = phase === "mic" || phase === "connecting" || phase === "live" || phase === "ending";
  const showSetup = phase === "idle" || phase === "error";

  const orbState: OrbState =
    phase !== "live"
      ? "idle"
      : call.agentState === "speaking" || call.isAgentAudible()
        ? "speaking"
        : call.agentState === "thinking"
          ? "thinking"
          : call.agentState === "listening"
            ? "listening"
            : "idle";

  const elapsed = call.startedAt ? (now - call.startedAt) / 1000 : 0;

  return (
    <Drawer
      open
      onClose={onClose}
      title="Test agent"
      description="Talk to the agent from your browser. No phone cost."
      width="max-w-[560px]"
      footer={
        <p className="text-xs text-muted">
          Browser tests are saved as calls (channel: browser). Closing this panel hangs up.
        </p>
      }
    >
      <div className="flex flex-col gap-5 p-5">
        {/* Orb stage */}
        <div className="relative overflow-hidden rounded-xl border border-line bg-canvas">
          <div className="h-36">
            <VoiceOrb state={orbState} getLevel={call.getLevel} label={`Agent is ${STATE_LABEL[orbState].toLowerCase()}`} />
          </div>
          <div className="absolute top-3 left-3 flex items-center gap-2">
            <Chip color={STATE_CHIP[orbState]} size="sm">
              {phase === "live" ? STATE_LABEL[orbState] : phase === "ended" ? "Ended" : inCall ? "Connecting" : "Ready"}
            </Chip>
          </div>
          {phase === "live" || phase === "ending" ? (
            <span className="tabular absolute top-3 right-3 text-[13px] font-medium text-muted">
              {formatDuration(elapsed)}
            </span>
          ) : null}
          {phaseText(phase) ? (
            <p className="absolute inset-x-0 bottom-2 text-center text-[13px] text-muted" role="status">
              {phaseText(phase)}
            </p>
          ) : null}
        </div>

        {call.error ? (
          <div role="alert" className="rounded-lg border border-line bg-subtle p-3 text-sm text-fg">
            {call.error}
          </div>
        ) : null}

        {showSetup ? (
          <div className="flex flex-col gap-4">
            <LeadPicker value={lead} onChange={setLead} />
            {!lead ? (
              <div className="grid gap-3 sm:grid-cols-2">
                <TextField label="Or a test name" placeholder="e.g. Rahul Sharma" value={name} onChange={(e) => setName(e.target.value)} />
                <TextField label="Company" placeholder="e.g. Sharma Snacks" value={company} onChange={(e) => setCompany(e.target.value)} />
              </div>
            ) : null}
            <div className="flex items-start gap-3 rounded-lg border border-primary-tint-strong bg-primary-tint p-3">
              <Headphones className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
              <p className="text-[13px]">
                <span className="font-medium">Use headphones.</span> On laptop speakers the agent can hear itself and keep
                interrupting. Speak naturally in Hindi, English or Hinglish.
              </p>
            </div>
            <Button
              variant="primary"
              size="lg"
              block
              icon={<Mic />}
              onClick={() => void call.start(lead ? { lead_id: lead.id } : { name, company })}
            >
              {phase === "error" ? "Try again" : "Start test call"}
            </Button>
          </div>
        ) : null}

        {inCall || phase === "ended" ? (
          <div className="flex flex-col gap-4">
            <div className="flex items-center justify-between gap-3">
              <p className="font-heading text-sm font-semibold">Transcript</p>
              {lead || name ? (
                <p className="truncate text-[13px] text-muted">
                  Talking as {lead ? lead.name?.trim() || "Unnamed lead" : name}
                  {(lead?.company ?? company) ? ` · ${lead?.company ?? company}` : ""}
                </p>
              ) : null}
            </div>
            <div
              ref={scrollRef}
              className="scroll-thin h-72 space-y-2.5 overflow-y-auto rounded-lg border border-line bg-canvas p-3"
              role="log"
              aria-live="polite"
              aria-label="Live transcript"
            >
              {call.bubbles.length === 0 ? (
                <p className="py-8 text-center text-[13px] text-muted">
                  {phase === "live" ? "The agent will start speaking in a moment…" : "Nothing said yet."}
                </p>
              ) : (
                call.bubbles.map((b) => (
                  <ChatBubble
                    key={b.key}
                    role={b.role}
                    text={b.text}
                    partial={!b.final}
                    interrupted={b.interrupted}
                    offsetMs={b.offset_ms}
                    compact
                  />
                ))
              )}
            </div>

            {inCall ? (
              <div className="flex items-center gap-3">
                <Button
                  variant={call.muted ? "primary" : "secondary"}
                  size="lg"
                  icon={call.muted ? <MicOff /> : <Mic />}
                  onClick={call.toggleMute}
                  disabled={phase !== "live"}
                  aria-pressed={call.muted}
                >
                  {call.muted ? "Unmute" : "Mute"}
                </Button>
                <Button
                  variant="navy"
                  size="lg"
                  block
                  className="flex-1"
                  icon={<PhoneOff />}
                  onClick={call.hangup}
                  loading={phase === "ending"}
                >
                  Hang up
                </Button>
              </div>
            ) : null}

            {phase === "ended" ? (
              <div className="rounded-lg border border-line bg-canvas p-4">
                <p className="text-sm font-medium">
                  Call ended
                  {call.endReason
                    ? ` · ${call.endReason === "connection_lost" ? "Connection lost" : endReasonLabel(call.endReason)}`
                    : ""}
                </p>
                <p className="mt-1 text-[13px] text-muted">
                  The summary, rating and interest level arrive a few seconds after the call ends.
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {call.uuid ? (
                    <ButtonLink href={`/calls/${call.uuid}`} variant="primary" icon={<ArrowRight />} onClick={onClose}>
                      View call summary
                    </ButtonLink>
                  ) : null}
                  <Button variant="secondary" icon={<RotateCcw />} onClick={call.reset}>
                    Start another
                  </Button>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </Drawer>
  );
}
