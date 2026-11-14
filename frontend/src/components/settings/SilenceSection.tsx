"use client";

import { ChevronRight, Hourglass, MessageCircleQuestion, MicOff, PhoneOff } from "lucide-react";
import type { ReactNode } from "react";
import { CircleIcon } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import type { SectionProps } from "./model";
import { NumberField, SettingsSection } from "./SettingsSection";

function Step({
  tone,
  icon,
  title,
  text,
}: {
  tone: "subtle" | "primary" | "brand";
  icon: ReactNode;
  title: string;
  text?: string;
}) {
  return (
    <li className="flex min-w-0 flex-1 basis-[150px] items-start gap-2.5 rounded-lg border border-line bg-white p-2.5">
      <CircleIcon tone={tone} size="sm">
        {icon}
      </CircleIcon>
      <div className="min-w-0">
        <p className="text-[13px] leading-tight font-semibold">{title}</p>
        {text ? <p className="text-mixed mt-0.5 line-clamp-2 text-[12px] font-normal text-muted">{text}</p> : null}
      </div>
    </li>
  );
}

function Arrow() {
  return (
    <li aria-hidden className="hidden shrink-0 items-center self-center md:flex">
      <ChevronRight className="size-5" />
    </li>
  );
}

function secs(v: number, fallback: number) {
  return Number.isFinite(v) ? v : fallback;
}

/** Timeline: N s silence -> prompt -> M s -> goodbye + hang up (bound to live form values). */
function SilenceTimeline({ form }: Pick<SectionProps, "form">) {
  const n = secs(form.silence_timeout_sec, 10);
  const m = secs(form.silence_final_wait_sec, 3);
  return (
    <div className="rounded-xl border border-line bg-canvas p-3">
      <p className="text-[13px] font-medium text-muted mb-2.5">What happens when the line goes quiet</p>
      <ol className="flex flex-wrap items-stretch gap-2" aria-label="Silence timeline">
        <Step tone="subtle" icon={<MicOff />} title={`${n}s silence`} text="Customer says nothing" />
        <Arrow />
        <Step tone="subtle" icon={<MessageCircleQuestion />} title="Agent checks in" text={`“${form.silence_prompt_text}”`} />
        <Arrow />
        <Step tone="subtle" icon={<Hourglass />} title={`${m}s more`} text="Still no reply" />
        <Arrow />
        <Step tone="subtle" icon={<PhoneOff />} title="Goodbye + hang up" text={`“${form.silence_goodbye_text}”`} />
      </ol>
      <p className="mt-2.5 text-[12px] font-medium text-muted">
        Worst case, a silent call ends about {n + m}s after the customer stops talking (plus the time the agent takes to speak).
      </p>
    </div>
  );
}

export function SilenceSection({ form, set, errors }: SectionProps) {
  return (
    <SettingsSection
      id="silence"
      title="Silence & hang-up"
      subtitle="Stops dead-air calls from running up minutes."
      icon={<Hourglass />}
      tone="subtle"
    >
      <SilenceTimeline form={form} />
      <div className="grid gap-4 sm:grid-cols-[200px_1fr]">
        <NumberField
          label="Silence timeout"
          unit="sec"
          min={3}
          max={60}
          value={form.silence_timeout_sec}
          onChange={(v) => set("silence_timeout_sec", v)}
          error={errors.silence_timeout_sec}
          hint="Default 10"
        />
        <TextField
          id="silence_prompt_text"
          label="Check-in prompt"
          value={form.silence_prompt_text}
          onChange={(e) => set("silence_prompt_text", e.target.value)}
          error={errors.silence_prompt_text}
          inputClassName="text-mixed"
          placeholder="Hello? Kya aap call par hain?"
        />
        <NumberField
          label="Final wait"
          unit="sec"
          min={1}
          max={30}
          value={form.silence_final_wait_sec}
          onChange={(v) => set("silence_final_wait_sec", v)}
          error={errors.silence_final_wait_sec}
          hint="Default 3"
        />
        <TextField
          id="silence_goodbye_text"
          label="Goodbye line"
          value={form.silence_goodbye_text}
          onChange={(e) => set("silence_goodbye_text", e.target.value)}
          error={errors.silence_goodbye_text}
          inputClassName="text-mixed"
          placeholder="Lagta hai line clear nahi hai, main baad mein call karti hoon. Dhanyavaad!"
        />
      </div>
    </SettingsSection>
  );
}
