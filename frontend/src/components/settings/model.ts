import type { AgentSettings } from "@/lib/types";

export type SetField = <K extends keyof AgentSettings>(key: K, value: AgentSettings[K]) => void;
export type FieldErrors = Partial<Record<keyof AgentSettings, string>>;

export interface SectionProps {
  form: AgentSettings;
  set: SetField;
  errors: FieldErrors;
}

export const PLACEHOLDERS = ["{lead_name_ji}", "{lead_name}", "{agent_name}", "{company_name}"] as const;

/** Sample values used for previews of the opening line. */
export function fillPlaceholders(text: string, form: Pick<AgentSettings, "agent_name" | "company_name">): string {
  return text
    .replace(/\{lead_name_ji\}/g, "Rahul ji")
    .replace(/\{lead_name\}/g, "Rahul")
    .replace(/\{agent_name\}/g, form.agent_name.trim())
    .replace(/\{company_name\}/g, form.company_name.trim() || "our company")
    .replace(/\s{2,}/g, " ");
}

/** Common Sarvam language codes. The current value is always kept even if it's not listed here. */
export const LANGUAGES: { value: string; label: string }[] = [
  { value: "hi-IN", label: "Hindi / Hinglish (hi-IN)" },
  { value: "en-IN", label: "English, India (en-IN)" },
  { value: "gu-IN", label: "Gujarati (gu-IN)" },
  { value: "mr-IN", label: "Marathi (mr-IN)" },
  { value: "bn-IN", label: "Bengali (bn-IN)" },
  { value: "ta-IN", label: "Tamil (ta-IN)" },
  { value: "te-IN", label: "Telugu (te-IN)" },
  { value: "kn-IN", label: "Kannada (kn-IN)" },
  { value: "ml-IN", label: "Malayalam (ml-IN)" },
  { value: "pa-IN", label: "Punjabi (pa-IN)" },
  { value: "od-IN", label: "Odia (od-IN)" },
];

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

function checkRange(v: number, min: number, max: number, unit: string): string | undefined {
  if (!Number.isFinite(v)) return "Required.";
  if (v < min || v > max) return `Use ${min}–${max} ${unit}.`;
  return undefined;
}

export function validate(f: AgentSettings): FieldErrors {
  const e: FieldErrors = {};
  if (!f.company_name.trim()) e.company_name = "Company name is required.";
  if (!f.voice) e.voice = "Pick a voice.";
  if (!f.opening_line.trim()) e.opening_line = "The opening line can't be empty.";
  const pace = checkRange(f.tts_pace, 0.5, 2, "x");
  if (pace) e.tts_pace = pace;
  const temp = checkRange(f.tts_temperature, 0, 1, "");
  if (temp) e.tts_temperature = temp;
  const silence = checkRange(f.silence_timeout_sec, 3, 60, "seconds");
  if (silence) e.silence_timeout_sec = silence;
  const finalWait = checkRange(f.silence_final_wait_sec, 1, 30, "seconds");
  if (finalWait) e.silence_final_wait_sec = finalWait;
  if (!f.silence_prompt_text.trim()) e.silence_prompt_text = "The agent needs something to say here.";
  if (!f.silence_goodbye_text.trim()) e.silence_goodbye_text = "The agent needs something to say here.";
  const delay = checkRange(f.filler_delay_ms, 0, 5000, "ms");
  if (delay) e.filler_delay_ms = delay;
  const maxCall = checkRange(f.max_call_sec, 60, 3600, "seconds");
  if (maxCall) e.max_call_sec = maxCall;
  if (!HHMM.test(f.call_window_start)) e.call_window_start = "Use HH:MM (24h).";
  if (!HHMM.test(f.call_window_end)) e.call_window_end = "Use HH:MM (24h).";
  if (!e.call_window_start && !e.call_window_end && f.call_window_start >= f.call_window_end) {
    e.call_window_end = "End time must be after the start time.";
  }
  return e;
}

/** Changed fields only (for a partial PUT). */
export function diffSettings(next: AgentSettings, base: AgentSettings): Partial<AgentSettings> {
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(next) as (keyof AgentSettings)[]) {
    if (JSON.stringify(next[k]) !== JSON.stringify(base[k])) out[k] = next[k];
  }
  return out as Partial<AgentSettings>;
}

/** Drop blank list entries before saving. */
export function normalize(f: AgentSettings): AgentSettings {
  return {
    ...f,
    qualification_questions: f.qualification_questions.map((q) => q.trim()).filter(Boolean),
    filler_words: f.filler_words.map((w) => w.trim()).filter(Boolean),
  };
}

/** Defensive: make server data safe for the form (null strings/lists, "HH:MM:SS" times). */
export function coerce(d: AgentSettings): AgentSettings {
  const s = (v: unknown) => (typeof v === "string" ? v : v === null || v === undefined ? "" : String(v));
  const list = (v: unknown) => (Array.isArray(v) ? v.map((x) => s(x)) : []);
  return {
    ...d,
    agent_name: s(d.agent_name),
    company_name: s(d.company_name),
    voice: s(d.voice),
    language_code: s(d.language_code),
    opening_line: s(d.opening_line),
    call_purpose: s(d.call_purpose),
    knowledge_base: s(d.knowledge_base),
    objection_handling: s(d.objection_handling),
    interest_rules: s(d.interest_rules),
    closing_line: s(d.closing_line),
    silence_prompt_text: s(d.silence_prompt_text),
    silence_goodbye_text: s(d.silence_goodbye_text),
    call_window_start: s(d.call_window_start).slice(0, 5),
    call_window_end: s(d.call_window_end).slice(0, 5),
    qualification_questions: list(d.qualification_questions),
    filler_words: list(d.filler_words),
  };
}
