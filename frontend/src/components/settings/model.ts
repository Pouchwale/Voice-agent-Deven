import type { AgentSettings, InboundOffAction, LanguageMix, SpeakingTone } from "@/lib/types";

export type SetField = <K extends keyof AgentSettings>(key: K, value: AgentSettings[K]) => void;
export type FieldErrors = Partial<Record<keyof AgentSettings, string>>;

export interface SectionProps {
  form: AgentSettings;
  set: SetField;
  errors: FieldErrors;
}

export const PLACEHOLDERS = ["{lead_name_ji}", "{lead_name}", "{agent_name}", "{company_name}"] as const;
/** For lines played before we know who is calling (cached per settings, so no lead placeholders). */
export const COMPANY_PLACEHOLDERS = ["{company_name}", "{agent_name}"] as const;

export const SPEAKING_TONES: readonly SpeakingTone[] = ["warm", "professional", "energetic", "calm"];
export const LANGUAGE_MIXES: readonly LanguageMix[] = ["hinglish", "hindi", "english"];
export const INBOUND_OFF_ACTIONS: readonly InboundOffAction[] = ["message", "reject"];

/** Lines spoken word for word on the phone. Same rules as the backend: required, at most 600 characters (~40 s). */
export const SPOKEN_LINES = [
  "inbound_greeting",
  "inbound_greeting_known",
  "callback_greeting",
  "inbound_unavailable_message",
  "inbound_closing_line",
] as const satisfies readonly (keyof AgentSettings)[];
export const SPOKEN_LINE_MAX = 600;

/** Sample values used for previews of the opening line. */
export function fillPlaceholders(text: string, form: Pick<AgentSettings, "agent_name" | "company_name">): string {
  return text
    .replace(/\{lead_name_ji\}/g, "Rahul ji")
    .replace(/\{lead_name\}/g, "Rahul")
    .replace(/\{agent_name\}/g, form.agent_name.trim())
    .replace(/\{company_name\}/g, form.company_name.trim() || "our company")
    .replace(/\s{2,}/g, " ");
}

/**
 * What the phone line actually says, mirroring the backend's fill_template: {lead_name_ji} = "Rahul जी" for a
 * lead with a name, else just "जी" (unknown caller); {lead_name} = the name, else "जी".
 */
export function fillTemplate(
  text: string,
  form: Pick<AgentSettings, "agent_name" | "company_name">,
  leadName: string | null,
): string {
  const name = leadName?.trim() ?? "";
  return text
    .replace(/\{lead_name_ji\}/g, name ? `${name.split(/\s+/)[0]} जी` : "जी")
    .replace(/\{lead_name\}/g, name || "जी")
    .replace(/\{agent_name\}/g, form.agent_name.trim())
    .replace(/\{company_name\}/g, form.company_name.trim() || "our company")
    .replace(/\{lead_(company|city)\}/g, "")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
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
  // ranges mirror backend/app/routers/settings.py _NUMERIC_LIMITS (otherwise Save fails with a 422)
  const temp = checkRange(f.tts_temperature, 0.01, 1, "");
  if (temp) e.tts_temperature = temp;
  const silence = checkRange(f.silence_timeout_sec, 3, 60, "seconds");
  if (silence) e.silence_timeout_sec = silence;
  const finalWait = checkRange(f.silence_final_wait_sec, 1, 30, "seconds");
  if (finalWait) e.silence_final_wait_sec = finalWait;
  if (!f.silence_prompt_text.trim()) e.silence_prompt_text = "The agent needs something to say here.";
  if (!f.silence_goodbye_text.trim()) e.silence_goodbye_text = "The agent needs something to say here.";
  const delay = checkRange(f.filler_delay_ms, 200, 3000, "ms");
  if (delay) e.filler_delay_ms = delay;
  const maxCall = checkRange(f.max_call_sec, 30, 1800, "seconds");
  if (maxCall) e.max_call_sec = maxCall;
  const inboundMax = checkRange(f.inbound_max_call_sec, 60, 3600, "seconds");
  if (inboundMax) e.inbound_max_call_sec = inboundMax;
  for (const key of SPOKEN_LINES) {
    // Checked as saved (normalize trims), so stray spaces never count.
    const line = f[key].trim();
    if (!line) e[key] = "Callers would hear silence here - this can't be empty.";
    else if (line.length > SPOKEN_LINE_MAX) {
      e[key] = `Keep it under ${SPOKEN_LINE_MAX} characters: it's spoken on the phone (now ${line.length}).`;
    }
  }
  const badRule = f.pronunciations
    .map((l) => l.trim())
    .find((l) => l && !(l.includes("=") && l.split("=").slice(0, 2).every((p) => p.trim())));
  if (badRule) e.pronunciations = `“${badRule}” must look like: word = how to say it`;
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

/** Drop blank list entries and the stray spaces / newlines around spoken lines before saving. */
export function normalize(f: AgentSettings): AgentSettings {
  const out: AgentSettings = {
    ...f,
    qualification_questions: f.qualification_questions.map((q) => q.trim()).filter(Boolean),
    filler_words: f.filler_words.map((w) => w.trim()).filter(Boolean),
    pronunciations: f.pronunciations.map((l) => l.trim()).filter(Boolean),
  };
  for (const key of SPOKEN_LINES) out[key] = f[key].trim();
  return out;
}

const TRUE_STRINGS = ["true", "1", "yes", "on"];
const FALSE_STRINGS = ["false", "0", "no", "off"];

/** Defensive: make server data safe for the form (null strings/lists, "HH:MM:SS" times, unknown choices). */
export function coerce(d: AgentSettings): AgentSettings {
  const s = (v: unknown) => (typeof v === "string" ? v : v === null || v === undefined ? "" : String(v));
  const list = (v: unknown) => (Array.isArray(v) ? v.map((x) => s(x)) : []);
  // Switches default ON (the backend default) when missing; "false" / 0 from a hand-edited row still reads as off.
  const bool = (v: unknown, fallback: boolean) => {
    if (typeof v === "boolean") return v;
    const key = s(v).trim().toLowerCase();
    return TRUE_STRINGS.includes(key) ? true : FALSE_STRINGS.includes(key) ? false : fallback;
  };
  const num = (v: unknown, fallback: number) => {
    const n = typeof v === "number" ? v : typeof v === "string" && v.trim() ? Number(v) : Number.NaN;
    return Number.isFinite(n) ? n : fallback;
  };
  const oneOf = <T extends string>(v: unknown, allowed: readonly T[], fallback: T): T =>
    (allowed as readonly unknown[]).includes(v) ? (v as T) : fallback;
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
    pronunciations: list(d.pronunciations),
    outbound_enabled: bool(d.outbound_enabled, true),
    inbound_enabled: bool(d.inbound_enabled, true),
    inbound_greeting: s(d.inbound_greeting),
    inbound_greeting_known: s(d.inbound_greeting_known),
    inbound_purpose: s(d.inbound_purpose),
    inbound_closing_line: s(d.inbound_closing_line),
    inbound_max_call_sec: num(d.inbound_max_call_sec, 600),
    callback_greeting: s(d.callback_greeting),
    inbound_off_action: oneOf(d.inbound_off_action, INBOUND_OFF_ACTIONS, "message"),
    inbound_unavailable_message: s(d.inbound_unavailable_message),
    speaking_tone: oneOf(d.speaking_tone, SPEAKING_TONES, "warm"),
    language_mix: oneOf(d.language_mix, LANGUAGE_MIXES, "hinglish"),
  };
}
