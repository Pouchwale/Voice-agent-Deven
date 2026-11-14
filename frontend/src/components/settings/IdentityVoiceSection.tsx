"use client";

import { AudioLines, Play, Square, UserRound } from "lucide-react";
import { useMemo } from "react";
import useSWR from "swr";
import { Button } from "@/components/ui/Button";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { Segmented, SelectField } from "@/components/ui/Select";
import { Slider } from "@/components/ui/Switch";
import { keys } from "@/lib/api";
import type { AgentSettings, Voice } from "@/lib/types";
import { fillPlaceholders, LANGUAGES, type SectionProps } from "./model";
import { SettingsSection } from "./SettingsSection";
import { usePreviewVoice, voiceOf } from "./VoicePreview";

/** Backend default voice; only used as a preference when the current voice is missing or doesn't fit. */
const DEFAULT_VOICE = "suhani";

const GENDERS: { value: AgentSettings["agent_gender"]; label: string }[] = [
  { value: "female", label: "Female" },
  { value: "male", label: "Male" },
];

export function IdentityVoiceSection({ form, set, errors }: SectionProps) {
  const { data: voices, error: voicesError } = useSWR<Voice[]>(keys.voices, { revalidateOnFocus: false });
  const preview = usePreviewVoice();

  const voiceOptions = useMemo(() => {
    const list = (voices ?? []).filter((v) => v.gender === form.agent_gender);
    const opts = list.map((v) => ({ value: v.id, label: v.name || v.id }));
    if (form.voice && !opts.some((o) => o.value === form.voice)) {
      opts.unshift({ value: form.voice, label: `${form.voice} (current)` });
    }
    if (!opts.length) opts.push({ value: "", label: voices ? "No voices for this gender" : "Loading voices…" });
    return opts;
  }, [voices, form.agent_gender, form.voice]);

  const isV4 = voices?.find((v) => v.id === form.voice)?.model === "bulbul:v4-flash";

  const languageOptions = useMemo(() => {
    if (LANGUAGES.some((l) => l.value === form.language_code)) return LANGUAGES;
    return [{ value: form.language_code, label: `${form.language_code} (current)` }, ...LANGUAGES];
  }, [form.language_code]);

  function onGender(g: AgentSettings["agent_gender"]) {
    set("agent_gender", g);
    const current = voices?.find((v) => v.id === form.voice);
    if (voices && (!current || current.gender !== g)) {
      // Prefer the backend default voice ("suhani") when it fits the gender, else the first voice of that gender.
      const preferred = voices.find((v) => v.id === DEFAULT_VOICE && v.gender === g) ?? voices.find((v) => v.gender === g);
      if (preferred) set("voice", preferred.id);
    }
  }

  const sample = fillPlaceholders(
    form.opening_line.trim() || "Namaste {lead_name_ji}, main {company_name} se bol rahi hoon.",
    form,
  );

  return (
    <SettingsSection
      id="identity"
      title="Identity & voice"
      subtitle="How the agent introduces itself and how it sounds."
      icon={<UserRound />}
      tone="subtle"
      actions={
        <Button
          variant={preview.state === "playing" ? "primary" : "secondary"}
          size="sm"
          icon={preview.state === "playing" ? <Square /> : <Play />}
          loading={preview.state === "loading"}
          onClick={() => (preview.state === "idle" ? void preview.play("identity", sample, voiceOf(form)) : preview.stop())}
          disabled={!form.voice && preview.state === "idle"}
        >
          {preview.state === "playing" ? "Stop preview" : preview.state === "loading" ? "Generating…" : "Preview voice"}
        </Button>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Agent name (optional)"
          hint="Leave empty to not use a personal name on calls"
          value={form.agent_name}
          onChange={(e) => set("agent_name", e.target.value)}
          error={errors.agent_name}
          autoComplete="off"
        />
        <TextField
          label="Company name"
          value={form.company_name}
          onChange={(e) => set("company_name", e.target.value)}
          error={errors.company_name}
          autoComplete="off"
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-[auto_1fr_1fr] sm:items-start">
        <div>
          <span className="field-label">Gender</span>
          <Segmented label="Agent gender" options={GENDERS} value={form.agent_gender} onChange={onGender} />
        </div>
        <SelectField
          label="Voice"
          options={voiceOptions}
          value={form.voice}
          onChange={(v) => v && set("voice", v)}
          error={errors.voice}
          hint={
            voicesError
              ? "Couldn't load the voice list - keeping the current voice."
              : isV4
                ? "Newer Bulbul v4 voice: its speaking style is built in."
                : "“(v4)” voices are Sarvam's newer ones. Listen with Preview voice."
          }
        />
        <SelectField
          label="Language"
          options={languageOptions}
          value={form.language_code}
          onChange={(v) => set("language_code", v)}
          hint="Also the language the agent listens for: keep hi-IN for Hindi and Hinglish callers."
        />
      </div>

      <div className="grid gap-5 sm:grid-cols-2">
        <Slider
          label="Speaking pace"
          value={form.tts_pace}
          min={0.5}
          max={2}
          step={0.05}
          onChange={(v) => set("tts_pace", v)}
          format={(v) => `${v.toFixed(2)}x`}
          hint={errors.tts_pace ?? "1.00x is natural. Slightly faster sounds more confident on the phone."}
        />
        <Slider
          label="Voice expressiveness"
          value={form.tts_temperature}
          min={0}
          max={1}
          step={0.05}
          onChange={(v) => set("tts_temperature", v)}
          format={(v) => v.toFixed(2)}
          hint={
            errors.tts_temperature ??
            (isV4
              ? "Not used by v4 voices (their style is built in). Applies when you pick a v3 voice."
              : "0.70–0.80 sounds warm and conversational. Lower is steadier but flatter.")
          }
        />
      </div>

      <TextAreaField
        label="Pronunciation fixes"
        hint="One per line: word = how to say it. Brand names written in Hindi letters sound natural. Only the voice uses this; transcripts keep the normal spelling."
        error={errors.pronunciations}
        value={form.pronunciations.join("\n")}
        onChange={(e) => set("pronunciations", e.target.value.split("\n"))}
        rows={4}
        placeholder={"PouchWale = पाउचवाले\nGP3 = जी पी थ्री"}
        spellCheck={false}
      />

      <p className="flex items-start gap-2 rounded-lg border border-line bg-canvas px-3 py-2 text-[13px] font-normal">
        <AudioLines className="mt-0.5 size-4 shrink-0" aria-hidden />
        <span className="text-mixed">
          <span className="font-semibold">Preview says: </span>
          {sample}
        </span>
      </p>
    </SettingsSection>
  );
}
