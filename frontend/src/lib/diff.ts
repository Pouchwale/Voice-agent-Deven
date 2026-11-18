/**
 * Line-level difference between two texts, for showing what a settings change does: the lines it removes and the
 * lines it adds (in order). Lines present in both are left out. Good enough for scripts and rule lists, where a
 * change is usually a few added or edited lines.
 */
export function lineDiff(before: string, after: string): { removed: string[]; added: string[] } {
  const split = (s: string) => s.split(/\r?\n/).map((l) => l.trimEnd()).filter((l) => l.trim());
  const a = split(before);
  const b = split(after);
  const inA = new Map<string, number>();
  for (const l of a) inA.set(l, (inA.get(l) ?? 0) + 1);
  const inB = new Map<string, number>();
  for (const l of b) inB.set(l, (inB.get(l) ?? 0) + 1);
  // a line counts as unchanged as many times as it appears on both sides
  const removed: string[] = [];
  const seenA = new Map<string, number>();
  for (const l of a) {
    const n = (seenA.get(l) ?? 0) + 1;
    seenA.set(l, n);
    if (n > (inB.get(l) ?? 0)) removed.push(l);
  }
  const added: string[] = [];
  const seenB = new Map<string, number>();
  for (const l of b) {
    const n = (seenB.get(l) ?? 0) + 1;
    seenB.set(l, n);
    if (n > (inA.get(l) ?? 0)) added.push(l);
  }
  return { removed, added };
}

/** A settings value as short readable text (history rows, suggestion previews). */
export function formatSettingValue(v: unknown, max = 160): string {
  if (v === null || v === undefined || v === "") return "(empty)";
  if (typeof v === "boolean") return v ? "On" : "Off";
  if (Array.isArray(v)) return clip(v.map((x) => String(x)).join(" · "), max) || "(empty)";
  if (typeof v === "object") return clip(JSON.stringify(v), max);
  return clip(String(v), max);
}

/** Long multi-line texts (scripts, knowledge base, rules) get a line diff instead of before / after strings. */
export function isLongText(...values: unknown[]): boolean {
  return values.some((v) => typeof v === "string" && (v.length > 160 || v.includes("\n")));
}

function clip(s: string, max: number): string {
  const one = s.replace(/\s+/g, " ").trim();
  return one.length > max ? `${one.slice(0, max - 1)}…` : one;
}
