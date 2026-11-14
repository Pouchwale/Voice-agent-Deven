"use client";

import { Check, Languages, SlidersHorizontal } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { Button } from "@/components/ui/Button";
import { Segmented } from "@/components/ui/Select";
import { keys } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { AgentSettings, LanguageMix, SpeakingTone, Voice } from "@/lib/types";
import type { SectionProps } from "./model";
import { SettingsSection } from "./SettingsSection";
import { PlayButton, usePreviewVoice, voiceOf } from "./VoicePreview";

const V4_MODEL = "bulbul:v4-flash";
/** v4 persona made for code-mixed Hindi + English speech. */
const HINGLISH_VOICE = "ishita_enhi_customer";

interface ToneOption {
  value: SpeakingTone;
  title: string;
  blurb: string;
  /** One reply in this tone, Hindi in Devanagari + English words (what the voice pronounces best). */
  example: string;
  pace: number;
  temperature: number;
  /** v4 persona whose built-in style fits (v4 voices ignore expressiveness). */
  v4Voice: string;
}

const TONES: ToneOption[] = [
  {
    value: "warm",
    title: "Warm & friendly",
    blurb: "Smiles in her voice, patient, like a helpful colleague.",
    example: "अच्छा जी, समझ गई! हम आपके brand के लिए बिल्कुल सही pouch बना देंगे। कितनी quantity चाहिए?",
    pace: 1.0,
    temperature: 0.7,
    v4Voice: "ritu_hi_customer_warm",
  },
  {
    value: "professional",
    title: "Professional",
    blurb: "Polite, crisp and precise. Fewer exclamations, still friendly.",
    example: "जी, ज़रूर। हम custom printed pouches बनाते हैं, कम quantity में भी। आपको किस size का pouch चाहिए?",
    pace: 1.0,
    temperature: 0.55,
    v4Voice: "aparna_hi_customer",
  },
  {
    value: "energetic",
    title: "Energetic",
    blurb: "Upbeat and lively, with positive energy. Never pushy or loud.",
    example: "अरे वाह, बढ़िया! आपके snacks के लिए हमारे stand-up pouches एकदम perfect रहेंगे। Sample भेज दूँ?",
    pace: 1.08,
    temperature: 0.8,
    v4Voice: "suhani_hi_social",
  },
  {
    value: "calm",
    title: "Calm & patient",
    blurb: "Soft, unhurried and reassuring. Good with confused or older callers.",
    example: "जी, कोई जल्दी नहीं है, आराम से बताइए। आप किस product के लिए packaging ढूँढ रहे हैं?",
    pace: 0.92,
    temperature: 0.6,
    v4Voice: "aarti_hi_customer",
  },
];

const LANGUAGE_OPTIONS: { value: LanguageMix; label: string }[] = [
  { value: "hinglish", label: "Hinglish" },
  { value: "hindi", label: "Mostly Hindi" },
  { value: "english", label: "Mostly English" },
];

const LANGUAGE_HINT: Record<LanguageMix, string> = {
  hinglish: "Everyday Hindi with the English words people always use (order, sample, pouch, WhatsApp).",
  hindi: "Simple Hindi. English only for words people always say in English (WhatsApp, order, sample, pouch).",
  english: "Simple Indian English, unless the customer clearly prefers Hindi.",
};

/** Lines the agent says word for word: the language style can't change them, only editing can. */
const FIXED_LINES: { key: keyof AgentSettings; label: string; section: string; field?: string }[] = [
  { key: "opening_line", label: "Opening line", section: "script", field: "opening_line" },
  { key: "closing_line", label: "Closing line", section: "script", field: "closing_line" },
  { key: "inbound_greeting", label: "Greeting for new callers", section: "calling", field: "inbound_greeting" },
  { key: "inbound_greeting_known", label: "Greeting for returning callers", section: "calling", field: "inbound_greeting_known" },
  { key: "callback_greeting", label: "Call-back greeting", section: "calling", field: "callback_greeting" },
  { key: "inbound_closing_line", label: "Inbound closing line", section: "calling", field: "inbound_closing_line" },
  { key: "inbound_unavailable_message", label: "Can't-answer message", section: "calling", field: "inbound_unavailable_message" },
  { key: "filler_words", label: "Filler words", section: "feel" },
  { key: "silence_prompt_text", label: "Silence check-in", section: "silence", field: "silence_prompt_text" },
  { key: "silence_goodbye_text", label: "Silence goodbye", section: "silence", field: "silence_goodbye_text" },
];

const DEVANAGARI = /[ऀ-ॿ]/;
const two = (n: number) => (Number.isFinite(n) ? n.toFixed(2) : "—");

/** Scroll to a field (or its section) and put the cursor in it. */
function jumpTo(fieldId: string | undefined, sectionId: string) {
  const field = fieldId ? document.getElementById(fieldId) : null;
  const target = field ?? document.getElementById(sectionId);
  if (!target) return;
  target.scrollIntoView({ block: field ? "center" : "start" });
  field?.focus({ preventScroll: true });
}

function HindiLines({ form }: { form: AgentSettings }) {
  const lines = FIXED_LINES.filter(({ key }) => {
    if (key === "filler_words" && !form.fillers_enabled) return false;
    if (key === "inbound_unavailable_message" && form.inbound_off_action === "reject") return false;
    const v = form[key];
    return DEVANAGARI.test(Array.isArray(v) ? v.join(" ") : String(v ?? ""));
  });
  if (!lines.length) {
    return <p className="mt-3 text-[13px] text-muted">All the lines the agent says word for word are in English already.</p>;
  }
  return (
    <div className="mt-3 rounded-lg border border-line bg-canvas px-3 py-2.5 text-[13px]">
      <p className="font-medium">These lines are said word for word, and they are still in Hindi:</p>
      <ul className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
        {lines.map((l) => (
          <li key={l.key}>
            <a
              href={`#${l.section}`}
              onClick={(e) => {
                e.preventDefault();
                jumpTo(l.field, l.section);
              }}
              className="font-semibold text-primary underline-offset-4 hover:underline"
            >
              {l.label}
            </a>
          </li>
        ))}
      </ul>
      <p className="mt-1.5 text-muted">
        English-speaking callers hear these in Hindi. Rewrite them in English if most of your customers speak English.
      </p>
    </div>
  );
}

interface Applied {
  tone: SpeakingTone;
  pace: number;
  temperature: number;
  was: { tone: SpeakingTone; pace: number; temperature: number };
}

export function ToneSection({ form, set }: SectionProps) {
  const { data: voices } = useSWR<Voice[]>(keys.voices, { revalidateOnFocus: false });
  const preview = usePreviewVoice();
  const [applied, setApplied] = useState<Applied | null>(null);

  const selected = TONES.find((t) => t.value === form.speaking_tone) ?? TONES[0];
  const voice = voices?.find((v) => v.id === form.voice);
  const isV4 = voice?.model === V4_MODEL;
  // A persona only fits when it exists in this backend's voice list and matches the agent's gender.
  const persona = (id: string) => voices?.find((v) => v.id === id && v.gender === form.agent_gender && v.id !== form.voice);
  const toneVoice = isV4 ? persona(selected.v4Voice) : undefined;
  const hinglishVoice = persona(HINGLISH_VOICE);

  // The "was … · Undo" note only describes the form while nothing has been changed by hand since.
  const showApplied =
    !!applied &&
    applied.tone === form.speaking_tone &&
    applied.pace === form.tts_pace &&
    applied.temperature === form.tts_temperature;
  const matchesPreset = form.tts_pace === selected.pace && form.tts_temperature === selected.temperature;

  function choose(t: ToneOption) {
    const was = { tone: form.speaking_tone, pace: form.tts_pace, temperature: form.tts_temperature };
    set("speaking_tone", t.value);
    set("tts_pace", t.pace);
    set("tts_temperature", t.temperature);
    const changed = was.tone !== t.value || was.pace !== t.pace || was.temperature !== t.temperature;
    setApplied(changed ? { tone: t.value, pace: t.pace, temperature: t.temperature, was } : null);
  }

  function undo() {
    if (!applied) return;
    set("speaking_tone", applied.was.tone);
    set("tts_pace", applied.was.pace);
    set("tts_temperature", applied.was.temperature);
    setApplied(null);
  }

  return (
    <SettingsSection
      id="tone"
      title="Tone & accent"
      subtitle="The agent's personality: how it words its replies and how the voice sounds."
      icon={<SlidersHorizontal />}
      tone="subtle"
      actions={
        <PlayButton
          state={preview.stateFor("tone")}
          onPlay={() => void preview.play("tone", selected.example, voiceOf(form))}
          onStop={preview.stop}
          disabled={!form.voice}
          label="Preview tone"
          what={`${selected.title} example with the current voice`}
        />
      }
    >
      <div>
        <div role="radiogroup" aria-label="Speaking tone" className="grid gap-3 sm:grid-cols-2">
          {TONES.map((t) => {
            const on = t.value === form.speaking_tone;
            return (
              <button
                key={t.value}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => choose(t)}
                className={cn(
                  "flex h-full flex-col items-start gap-1.5 rounded-xl border p-3.5 text-left transition-colors",
                  on ? "border-primary bg-primary-tint" : "border-line bg-white hover:border-primary-tint-strong hover:bg-canvas",
                )}
              >
                <span className="flex w-full items-center justify-between gap-2">
                  <span className="text-sm font-semibold">{t.title}</span>
                  {on ? <Check className="size-4 shrink-0 text-primary" aria-hidden /> : null}
                </span>
                <span className="text-[13px] font-normal text-muted">{t.blurb}</span>
                <span className="text-mixed text-[13px] font-normal text-fg">“{t.example}”</span>
                <span className="tabular mt-auto pt-1 text-[12px] font-medium text-muted">
                  {isV4 ? `Pace ${two(t.pace)}` : `Pace ${two(t.pace)} · expressiveness ${two(t.temperature)}`}
                </span>
              </button>
            );
          })}
        </div>

        <div className="mt-3 text-[13px]">
          {/* Only the "preset applied" note is announced: "Voice now" follows every slider move in Identity & voice. */}
          <div aria-live="polite">
            {showApplied && applied ? (
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-primary-tint-strong bg-primary-tint px-3 py-2">
                <span className="tabular font-medium">
                  {isV4
                    ? `Pace ${two(applied.pace)}`
                    : `Pace ${two(applied.pace)} · expressiveness ${two(applied.temperature)}`}
                </span>
                <span className="tabular text-muted">
                  {isV4 ? `(was ${two(applied.was.pace)})` : `(was ${two(applied.was.pace)} / ${two(applied.was.temperature)})`}
                </span>
                <span aria-hidden className="text-muted">
                  ·
                </span>
                <button type="button" onClick={undo} className="font-semibold text-primary underline-offset-4 hover:underline">
                  Undo
                </button>
              </p>
            ) : null}
          </div>
          {showApplied ? null : (
            <p className="tabular text-muted">
              Voice now: pace {two(form.tts_pace)}
              {isV4 ? "" : ` · expressiveness ${two(form.tts_temperature)}`}
              {matchesPreset || (isV4 && form.tts_pace === selected.pace)
                ? ` (the ${selected.title} preset)`
                : " (adjusted by hand: pick the tone again for its preset)"}
            </p>
          )}
          {isV4 ? (
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-line bg-canvas px-3 py-2">
              <p className="min-w-0 flex-1 basis-64">
                {voice?.name ?? "This voice"} is a v4 voice with its speaking style built in, so a tone changes only the
                pace (and how the agent words its replies), not the expressiveness.
                {toneVoice ? ` For ${selected.title.toLowerCase()}, ${toneVoice.name} fits best.` : ""}
              </p>
              {toneVoice ? (
                <Button size="sm" onClick={() => set("voice", toneVoice.id)}>
                  Use {toneVoice.name}
                </Button>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>

      <div className="border-t border-line pt-5">
        <span className="field-label">Language style</span>
        <Segmented
          label="Language style"
          options={LANGUAGE_OPTIONS}
          value={form.language_mix}
          onChange={(v) => set("language_mix", v)}
        />
        <p className="mt-1.5 text-[13px] font-normal text-muted">
          {LANGUAGE_HINT[form.language_mix]} The customer&apos;s own language still wins: whatever they speak, the agent
          answers in it.
        </p>
        {form.language_mix === "english" ? <HindiLines form={form} /> : null}
      </div>

      <div className="flex items-start gap-2.5 rounded-lg border border-line bg-canvas px-3 py-2.5 text-[13px]">
        <Languages className="mt-0.5 size-4 shrink-0" aria-hidden />
        <div className="min-w-0 space-y-2">
          <p className="font-semibold">Accent comes from the voice</p>
          <p className="font-normal text-muted">
            Hindi (hi-IN) voices speak Hindi, Hinglish and English words with a natural Indian accent, so there is no
            separate accent switch. For a lot of English mixed into Hindi, the v4 voice “Ishita (v4) · Hinglish” sounds
            the most natural. Pick and preview voices under{" "}
            <a
              href="#identity"
              onClick={(e) => {
                e.preventDefault();
                jumpTo(undefined, "identity");
              }}
              className="font-semibold text-primary underline-offset-4 hover:underline"
            >
              Identity &amp; voice
            </a>
            .
          </p>
          {hinglishVoice ? (
            <Button size="sm" onClick={() => set("voice", hinglishVoice.id)}>
              Use {hinglishVoice.name}
            </Button>
          ) : null}
        </div>
      </div>
    </SettingsSection>
  );
}
