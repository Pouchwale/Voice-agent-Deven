/**
 * The name of the person changing the agent's settings.
 *
 * Settings changes directly affect how the voice agent behaves on calls, so they (and only they) are recorded with a
 * name: the confirm popup on the Settings page (components/ui/TypeConfirmDialog.tsx) asks for it, and it is sent with
 * the change that popup confirms (save, reset to defaults, undo). The dashboard has no user accounts, so the name is
 * remembered on this computer to pre-fill that popup next time.
 *
 * No other page asks for a name and no other request carries one. It is a label, not a login: nothing checks that
 * the name is true.
 */

export const ACTOR_STORAGE_KEY = "va-actor-name";
export const ACTOR_MAX = 60;
export const ACTOR_MIN = 2;

/** Used when the browser blocks localStorage (private windows): the name then lasts until the tab is closed. */
let memory = "";

/** Control characters and the two Unicode line separators: never part of a name. */
function isControl(code: number): boolean {
  return code < 0x20 || (code >= 0x7f && code <= 0x9f) || code === 0x2028 || code === 0x2029;
}

/** Single spaces, no control characters, at most 60 characters (cut on whole characters, never mid-emoji). */
export function cleanActorName(raw: string): string {
  let tidy = "";
  for (const ch of raw) tidy += isControl(ch.codePointAt(0) ?? 0) ? " " : ch;
  tidy = tidy.split(" ").filter(Boolean).join(" ");
  return Array.from(tidy).slice(0, ACTOR_MAX).join("").trim();
}

/** The name remembered on this computer ("" when none). */
export function getActorName(): string {
  if (typeof window === "undefined") return "";
  try {
    const saved = window.localStorage.getItem(ACTOR_STORAGE_KEY);
    if (saved !== null) return cleanActorName(saved);
  } catch {
    /* storage blocked */
  }
  return memory;
}

/** Remembers the name on this computer. Returns the name as stored. */
export function setActorName(name: string): string {
  const clean = cleanActorName(name);
  memory = clean;
  try {
    if (clean) window.localStorage.setItem(ACTOR_STORAGE_KEY, clean);
    else window.localStorage.removeItem(ACTOR_STORAGE_KEY);
  } catch {
    /* storage blocked: kept in memory for this tab */
  }
  return clean;
}

/** The header value for a name (headers are ASCII-only; names may be in Hindi). "" when it can't be encoded. */
export function actorHeader(name: string): string {
  try {
    return encodeURIComponent(name);
  } catch {
    return "";
  }
}
