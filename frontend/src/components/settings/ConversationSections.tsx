"use client";

import { Clock, Sparkles, X } from "lucide-react";
import { useId, useState } from "react";
import { Switch } from "@/components/ui/Switch";
import { cn } from "@/lib/cn";
import { formatDuration } from "@/lib/format";
import type { SectionProps } from "./model";
import { NumberField, SettingsSection, TimeField } from "./SettingsSection";

/** Removable word chips + an input that adds on Enter (commas stay: "जी, समझ गई" is one filler). */
function WordChips({
  label,
  words,
  onChange,
  disabled,
}: {
  label: string;
  words: string[];
  onChange: (w: string[]) => void;
  disabled?: boolean;
}) {
  const [draft, setDraft] = useState("");
  const id = useId();

  function commit(raw: string) {
    const parts = raw
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean)
      .filter((s, i, arr) => arr.indexOf(s) === i && !words.includes(s));
    if (parts.length) onChange([...words, ...parts]);
    setDraft("");
  }

  return (
    <div className={cn(disabled && "opacity-60")}>
      <label htmlFor={id} className="field-label">
        {label}
      </label>
      <div className="field flex min-h-[48px] flex-wrap items-center gap-1.5 !py-1.5 focus-within:border-primary focus-within:ring-3 focus-within:ring-primary-tint-strong">
        {words.map((w, i) => (
          <span
            key={`${w}-${i}`}
            className="text-mixed inline-flex items-center gap-1 rounded-full border border-line bg-canvas py-0.5 pr-1 pl-2.5 text-[13px] font-medium"
          >
            {w}
            <button
              type="button"
              onClick={() => onChange(words.filter((_, k) => k !== i))}
              aria-label={`Remove ${w}`}
              disabled={disabled}
              className="rounded-full p-0.5 hover:bg-canvas [&_svg]:size-3.5"
            >
              <X aria-hidden className="" />
            </button>
          </span>
        ))}
        <input
          id={id}
          value={draft}
          disabled={disabled}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit(draft);
            } else if (e.key === "Backspace" && !draft && words.length) {
              onChange(words.slice(0, -1));
            }
          }}
          onBlur={() => draft.trim() && commit(draft)}
          placeholder={words.length ? "Add…" : "e.g. हम्म, जी, अच्छा"}
          className="text-mixed min-w-[120px] flex-1 bg-transparent py-1 text-[15px] font-normal outline-none placeholder:font-medium placeholder:text-muted"
        />
      </div>
      <p className="mt-1.5 text-[13px] font-normal text-muted">Press Enter to add (commas are fine inside a filler). Backspace removes the last one.</p>
    </div>
  );
}

export function ConversationSection({ form, set, errors }: SectionProps) {
  return (
    <SettingsSection
      id="feel"
      title="Conversation feel"
      subtitle="Small touches that make the agent sound natural on a phone line."
      icon={<Sparkles />}
      tone="subtle"
    >
      <Switch
        label="Filler words"
        description="Natural Hindi sounds (“हम्म”, “जी, समझ गई”) when a reply takes a moment, so the line never feels dead. Thinking sounds follow questions, acknowledgements follow statements, and the same one never plays twice in a row. Keep them neutral: they play before the reply is known."
        checked={form.fillers_enabled}
        onChange={(v) => set("fillers_enabled", v)}
      />
      <div className="grid gap-4 sm:grid-cols-[1fr_200px]">
        <WordChips
          label="Filler words"
          words={form.filler_words}
          onChange={(w) => set("filler_words", w)}
          disabled={!form.fillers_enabled}
        />
        <NumberField
          label="Filler delay"
          unit="ms"
          min={200}
          max={3000}
          step={50}
          value={form.filler_delay_ms}
          onChange={(v) => set("filler_delay_ms", v)}
          error={errors.filler_delay_ms}
          hint="Wait this long before a filler. 800 ms feels natural; shorter can delay a reply that was nearly ready."
        />
      </div>
      <div className="border-t border-line pt-5">
        <Switch
          label="Barge-in"
          description="Let the customer interrupt. The agent stops talking as soon as they speak."
          checked={form.barge_in_enabled}
          onChange={(v) => set("barge_in_enabled", v)}
        />
      </div>
      <div className="grid gap-4 border-t border-line pt-5 sm:grid-cols-[200px_1fr] sm:items-end">
        <NumberField
          label="Max outbound call length"
          unit="sec"
          min={30}
          max={1800}
          step={30}
          value={form.max_call_sec}
          onChange={(v) => set("max_call_sec", v)}
          error={errors.max_call_sec}
        />
        <p className="pb-3 text-[13px] font-normal text-muted">
          {Number.isFinite(form.max_call_sec)
            ? `Outbound calls are wrapped up politely at ${formatDuration(form.max_call_sec)} (m:ss). Inbound calls have their own limit under Calling.`
            : "Set a hard limit for call length."}
        </p>
      </div>
    </SettingsSection>
  );
}

export function CampaignWindowSection({ form, set, errors }: SectionProps) {
  return (
    <SettingsSection
      id="window"
      title="Campaign window"
      subtitle="Campaigns only dial inside this window (IST). Manual calls and browser tests ignore it."
      icon={<Clock />}
      tone="subtle"
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <TimeField
          label="Start (IST)"
          value={form.call_window_start}
          onChange={(v) => set("call_window_start", v)}
          error={errors.call_window_start}
        />
        <TimeField
          label="End (IST)"
          value={form.call_window_end}
          onChange={(v) => set("call_window_end", v)}
          error={errors.call_window_end}
        />
      </div>
    </SettingsSection>
  );
}
